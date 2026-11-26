"""모의운용: 합성 데이터와 가짜 AI·가짜 시세로 전체 흐름을 검증한다(운영 원장에 넣지 않음)."""
from datetime import date, datetime, timedelta

import pytest

from app.models import Experiment, InvestorFlow, LedgerViolation, PaperEvent
from app.paper import risk
from app.paper.engine import Engine
from app.paper.execution import execute_immediate
from app.paper.ledger import PaperViolation, append_event, replay, verify_chain
from app.paper.lifecycle import LifecycleError, discard, end_and_settle, register, verified_summary
from app.providers.base import ProviderResult, PStatus
from app.services.hypotheses import issue
from app.services.research import submit_research
from app.services.settings_store import get_setting, put_setting
from app.timeutil import KST
from tests.conftest import add_instrument, add_price, add_source, naver_payload, research_items

TODAY = date(2026, 11, 24)
CFG = {"initial_capital": 1_000_000, "duration_trading_days": 3, "max_positions": 3, "max_position_bp": 3500,
       "cost_bps": 15}


class FakeClock:
    def __init__(self, start: datetime):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


class FakeAI:
    configured = True
    model = "fake-model"

    def __init__(self, data):
        self.data = data

    def generate_json(self, task, materials, schema):
        assert "holdings" not in str(materials)  # 개인 보유종목은 전달하지 않음
        return ProviderResult(PStatus.OK, data=self.data)


class FakeQuote:
    """offsets: 결정 이후 호출마다 원자료 시각(초). 음수면 결정 이전 가격."""
    enabled = True

    def __init__(self, clock, offsets, price="70,000", lag=0.5):
        self.clock, self.offsets, self.price, self.lag, self.calls = clock, list(offsets), price, lag, 0

    def fetch(self, code):
        off = self.offsets[min(self.calls, len(self.offsets) - 1)]
        self.calls += 1
        src = (self.clock.now() + timedelta(seconds=off)).replace(tzinfo=None)
        from datetime import timezone
        t = src.replace(tzinfo=timezone.utc).astimezone(KST).isoformat()
        import json
        payload = naver_payload(code=code, price=self.price, t=t)
        self.clock.t += timedelta(seconds=self.lag)
        return ProviderResult(PStatus.OK, data=payload, raw_text=json.dumps(payload), url="https://polling.test/")


def ai_buy(code="005930", inval=68000, weight=3000):
    return {"compared": [{"code": code, "scores": {k: 2 for k in ("catalyst", "improvement", "flow", "relative_strength",
                                                                  "macro_fit")},
                          "action": "buy", "catalyst": "수주 공시", "not_priced_reason": "공시 직후",
                          "counter_scenario": "계약 취소", "invalidation_condition": "계약 해지 공시 또는 68,000원 이탈",
                          "invalidation_price": inval, "next_check": "다음 주 실적", "event_risk": "실적 발표 전",
                          "reward_scenario_pct": 6.0, "target_weight_bp": weight}],
            "position_reviews": []}


@pytest.fixture()
def world(calendar_2026):
    db = calendar_2026
    add_instrument(db, "005930", "합성전자", sector="반도체")
    rv = submit_research(db, "005930", {"items": research_items(db), "liquidity_ok": True}, "t")
    add_price(db, "005930", date(2026, 11, 23), 70000, value=50_000_000_000)
    src = add_source(db, "A")
    issue(db, {"code": "005930", "research_version_id": rv.id, "catalyst": "수주", "not_priced_reason": "직후",
               "counter_scenario": "취소", "invalidation": "해지", "next_check": "실적", "direction": "up",
               "confidence": "보통", "scores": {"catalyst": 2, "improvement": 2, "flow": 2, "relative_strength": 1,
                                                "macro_fit": 1}, "source_ids": [src.id], "flow_source_ids": [src.id]},
          "t", today=TODAY)
    db.add(InvestorFlow(code="005930", trading_date=TODAY, checked_at=datetime(2026, 11, 24, 0, 50), scope="KRX_REGULAR",
                        investor_type="foreign", net_value_krw=1_000_000_000, unit="KRW", provider="synthetic",
                        grade="B", raw_sha256="0" * 64))
    put_setting(db, "naver_validation", {"confirmed_by_owner": True})
    db.commit()
    return db


def clock_at_10am():
    return FakeClock(datetime(2026, 11, 24, 1, 0, 0))  # UTC = 10:00 KST


def test_register_validates_and_locks_on_first_decision(world):
    db = world
    with pytest.raises(LifecycleError):
        register(db, "bad", {**CFG, "cost_bps": -1}, "owner:t")
    exp = register(db, "실험", CFG, "owner:t")
    db.commit()
    assert exp.status == "첫판단대기" and exp.start_date is None
    clock = clock_at_10am()
    out = Engine(db, ai=FakeAI(ai_buy()), quote=FakeQuote(clock, [5]), clock=clock, poll_interval=5).run_cycle(exp)
    db.commit()
    assert out["result"] == "decided"
    assert exp.status == "운용중" and exp.start_date == TODAY and exp.end_date == date(2026, 11, 26)
    exp.cost_bps = 1
    with pytest.raises(LedgerViolation):
        db.flush()


def test_missing_providers_is_ops_error_not_decision(world):
    db = world
    exp = register(db, "실험", CFG, "owner:t")
    clock = clock_at_10am()

    class NoAI(FakeAI):
        configured = False
    out = Engine(db, ai=NoAI({}), quote=FakeQuote(clock, [5]), clock=clock).run_cycle(exp)
    assert out["result"] == "ops_error"
    assert db.query(PaperEvent).filter_by(type="decision").count() == 0
    assert exp.config_locked_at is None and exp.status == "첫판단대기"
    Engine(db, ai=NoAI({}), quote=FakeQuote(clock, [5]), clock=clock).run_cycle(exp)
    assert db.query(PaperEvent).filter_by(type="ops_error").count() == 1  # 같은 날 같은 사유는 1회


def test_full_immediate_fill_and_live_verification(world):
    from app.jobs import run_job
    from app.services.system_status import live_execution_verified
    db = world
    exp = register(db, "실험", CFG, "owner:t")
    db.commit()
    clock = clock_at_10am()
    out = Engine(db, ai=FakeAI(ai_buy()), quote=FakeQuote(clock, [3]), clock=clock).run_cycle(exp)
    db.commit()
    assert out["orders"][0]["result"] == "filled"
    fill = db.query(PaperEvent).filter_by(type="fill").one()
    order = db.query(PaperEvent).filter_by(type="order").one()
    p = fill.payload
    assert p["filledAt"] == p["observedAt"] and p["sourceGrade"] == "B" and p["priceType"] == "immediate"
    assert p["executionEvidenceVersion"] == "source-krx-observation-v1" and p["fill_price"] == 70000
    assert order.payload["createdAt"] == order.payload["executionAt"] == order.payload["decisionAt"]
    assert datetime.fromisoformat(p["observedAt"]) > datetime.fromisoformat(order.payload["decisionAt"])
    st = replay(db, exp)
    assert st.positions["005930"].quantity == 4 and st.cash == 1_000_000 - 280_000 - 420
    assert verify_chain(db, exp.id)[0]
    assert live_execution_verified(db)[0] is False  # 감사 전에는 false
    now = datetime(2026, 11, 24, 11, 0, tzinfo=KST)
    run = run_job("intraday_audit", "2026-11-24T11:00", now.date(), now)
    assert run.status == "succeeded"
    db.expire_all()
    assert live_execution_verified(db)[0] is True
    # 중복 체결 차단
    res = execute_immediate(db, exp, order.payload, FakeQuote(clock, [3]).fetch, {}, get_setting(db, "paper_defaults"),
                            actor="engine", clock=clock)
    assert res["result"] == "blocked"


def test_pre_decision_price_rejected_then_first_eligible_used(world):
    db = world
    exp = register(db, "실험", CFG, "owner:t")
    clock = clock_at_10am()
    quote = FakeQuote(clock, [-30, 10], price="70,000")
    Engine(db, ai=FakeAI(ai_buy()), quote=quote, clock=clock, poll_interval=5).run_cycle(exp)
    fill = db.query(PaperEvent).filter_by(type="fill").one()
    assert quote.calls == 2
    from app.models import Observation
    first = db.query(Observation).order_by(Observation.id).first()
    assert first.eligible  # 원본 필드는 적격이지만
    assert fill.payload["observation_id"] != first.id  # 결정 이전 시각이라 쓰지 않음


def test_window_expiry_no_carry_over(world):
    db = world
    exp = register(db, "실험", CFG, "owner:t")
    clock = clock_at_10am()
    # 매 조회의 원자료 시각이 결정 시각 이전(또는 같음)에 머무는 상황 → 창 안에 적격 관측 없음
    quote = FakeQuote(clock, [-5, -60, -100, -200], lag=20)
    out = Engine(db, ai=FakeAI(ai_buy()), quote=quote, clock=clock, poll_interval=30).run_cycle(exp)
    assert quote.calls == 3
    assert out["orders"][0]["result"] == "expired"
    exp_ev = db.query(PaperEvent).filter_by(type="expiry").one()
    assert "120초" in exp_ev.payload["reason"]
    assert replay(db, exp).open_orders == {}  # 이월 없음


def test_revalidation_at_fill_price_cancels(world):
    db = world
    exp = register(db, "실험", CFG, "owner:t")
    clock = clock_at_10am()
    out = Engine(db, ai=FakeAI(ai_buy()), quote=FakeQuote(clock, [3], price="70,500"), clock=clock).run_cycle(exp)
    assert out["orders"][0]["result"] == "cancelled"
    assert "NAV" in db.query(PaperEvent).filter_by(type="cancel").one().payload["reason"]


def test_single_execution_owner_and_terminal_block(world):
    db = world
    exp = register(db, "실험", CFG, "owner:t")
    with pytest.raises(PaperViolation):
        append_event(db, exp, "decision", {"x": 1}, datetime(2026, 11, 24, 1, 0), actor="audit")
    discard(db, exp, "테스트 폐기", "owner:t")
    with pytest.raises(PaperViolation):
        append_event(db, exp, "order", {"order_id": "o"}, datetime(2026, 11, 24, 1, 0), actor="engine")
    exp.status = "운용중"
    with pytest.raises(LedgerViolation):
        db.flush()


def test_paper_events_append_only(world):
    db = world
    exp = register(db, "실험", CFG, "owner:t")
    ev = db.query(PaperEvent).filter_by(experiment_id=exp.id).first()
    ev.payload = {"changed": True}
    with pytest.raises(LedgerViolation):
        db.flush()


def test_chain_detects_tampering(world):
    db = world
    exp = register(db, "실험", CFG, "owner:t")
    db.commit()
    db.execute(PaperEvent.__table__.update().values(payload={"tampered": True}))  # ORM 우회 변조 시뮬레이션
    db.commit()
    ok, msg = verify_chain(db, exp.id)
    assert not ok and "변조" in msg


def test_risk_rules():
    exp = Experiment(initial_capital=1_000_000, duration_trading_days=5, max_positions=1, max_position_bp=3500, cost_bps=15)
    from app.paper.ledger import Position, State
    cfg = {"max_loss_bp_of_nav": 100, "exploration_min_bp": 1000, "exploration_max_bp": 1500, "max_sector_bp": 5000}
    st = State(cash=1_000_000)
    ok = risk.BuyPlan("A", 4, 70000, 68000, "반도체", False)
    assert risk.check_buy(exp, st, ok, {}, cfg) == []
    assert any("최대 비중" in v for v in risk.check_buy(exp, st, risk.BuyPlan("A", 6, 70000, 69000, None, False), {}, cfg))
    assert any("NAV 1.0%" in v for v in risk.check_buy(exp, st, risk.BuyPlan("A", 4, 70000, 60000, None, False), {}, cfg))
    assert risk.check_buy(exp, st, risk.BuyPlan("A", 0, 70000, 68000, None, False), {}, cfg)
    poor = State(cash=100_000)
    assert any("현금 부족" in v for v in risk.check_buy(exp, poor, ok, {}, cfg))
    held = State(cash=500_000, positions={"B": Position("B", 1, 100_000)})
    assert any("최대 보유" in v for v in risk.check_buy(exp, held, ok, {"B": 100_000}, cfg))
    assert risk.check_sell(st, "A", 1)  # 공매도 금지
    qty = risk.size_buy(exp, st, 70000, 68000, 3000, {}, cfg)
    assert qty == 4 and risk.check_buy(exp, st, risk.BuyPlan("A", qty, 70000, 68000, None, False), {}, cfg) == []


def test_settlement_verified_pending_and_average(world):
    db = world
    exp = register(db, "실험", CFG, "owner:t")
    clock = clock_at_10am()
    Engine(db, ai=FakeAI(ai_buy()), quote=FakeQuote(clock, [3]), clock=clock).run_cycle(exp)
    other = register(db, "다른 실험", {**CFG, "initial_capital": 2_000_000}, "owner:t")
    assert replay(db, other).cash == 2_000_000  # 실험별 자본 분리
    db.commit()
    assert verified_summary(db)["label"] == "산출 대기"
    assert end_and_settle(db, exp, date(2026, 11, 25))["result"] == "not_due"
    out = end_and_settle(db, exp, date(2026, 11, 26))
    assert out["result"] == "pending" and "기간 연장 없이" in out["problems"][0]
    assert exp.status == "정산대기" and exp.end_date == date(2026, 11, 26)
    with pytest.raises(PaperViolation):  # 종료 후 판단·주문 금지
        append_event(db, exp, "order", {"order_id": "late"}, clock.now(), actor="engine")
    add_price(db, "005930", date(2026, 11, 26), 72000)
    out = end_and_settle(db, exp, date(2026, 11, 26))
    assert out["result"] == "verified"
    expected_nav = 1_000_000 - 280_420 + 4 * 72000
    assert out["nav"] == expected_nav
    summary = verified_summary(db)
    assert summary["average_return_pct"] is not None and len(summary["excluded"]) == 1
