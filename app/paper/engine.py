"""
모의판단 엔진 (실험별 단일 executionOwner = "engine").

역할 분리
1) 수집기: 후보별 공개 자료(공식가격·거래대금·당일 수급·촉매 출처)를 모은다.
2) 판단기: AI가 후보(서로 다른 업종, 최대 3개)를 비교해 구조화된 제안을 낸다.
3) 위험 검증기: 서버 코드(app/paper/risk.py)가 최종 판정한다.
4) 체결기: 결정 이후 첫 적격 원본 관측으로만 체결(app/paper/execution.py).

데이터 공급자 미연결·접근 실패는 투자판단이 아니라 운영 오류(ops_error)로 기록하며,
이 경우 판단 사건을 만들지 않으므로 실험의 시작일·config도 잠기지 않는다.
정시 감사 작업은 이 모듈을 호출하지 않는다(판단·주문·체결 생성 권한 없음).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Experiment, Hypothesis, Instrument, InvestorFlow, ResearchVersion
from app.paper import risk
from app.paper.execution import POLICY, Clock, execute_immediate
from app.paper.ledger import PaperViolation, append_event, replay
from app.paper.lifecycle import LifecycleError, lock_on_first_decision
from app.providers.ai import GeminiProvider
from app.providers.naver import NaverQuoteProvider
from app.services.calendar import is_in_regular_session
from app.services.market_data import latest_official_on_or_before
from app.services.settings_store import get_setting
from app.services.sources import independent_ac_count, load_sources
from app.timeutil import to_kst

ACTOR = "engine"
AXES = ("catalyst", "improvement", "flow", "relative_strength", "macro_fit")

AI_SCHEMA = {
    "type": "object",
    "properties": {
        "compared": {"type": "array", "items": {"type": "object", "properties": {
            "code": {"type": "string"},
            "scores": {"type": "object", "properties": {a: {"type": "integer"} for a in AXES}},
            "action": {"type": "string", "enum": ["buy", "pass"]},
            "catalyst": {"type": "string"}, "not_priced_reason": {"type": "string"},
            "counter_scenario": {"type": "string"}, "invalidation_condition": {"type": "string"},
            "invalidation_price": {"type": "integer"}, "next_check": {"type": "string"},
            "event_risk": {"type": "string"}, "reward_scenario_pct": {"type": "number"},
            "target_weight_bp": {"type": "integer"}}}},
        "position_reviews": {"type": "array", "items": {"type": "object", "properties": {
            "code": {"type": "string"}, "thesis_status": {"type": "string", "enum": ["유지", "강화", "약화", "무효화"]},
            "catalyst_progress": {"type": "string"}, "remaining_risks": {"type": "string"},
            "sell": {"type": "boolean"}}}},
        "cash_only_reason": {"type": "string"},
    },
    "required": ["compared", "position_reviews"],
}


@dataclass
class Candidate:
    code: str
    name: str
    sector: str | None
    official_close: int | None
    official_date: date | None
    trading_value: int | None
    flow_today: bool
    catalyst_ok: bool
    managed: bool
    thesis_id: str | None
    unmet: list[str] = field(default_factory=list)


def gather_candidates(db: Session, today: date, cfg: dict) -> list[Candidate]:
    codes: dict[str, str | None] = {}
    for h in db.scalars(select(Hypothesis).where(Hypothesis.status.in_(["활성", "중기추적"]))):
        codes.setdefault(h.code, h.uid)
    for rv in db.scalars(select(ResearchVersion).where(ResearchVersion.is_current_candidate)):
        codes.setdefault(rv.code, None)
    out = []
    for code, thesis_id in codes.items():
        inst = db.get(Instrument, code)
        if inst is None:
            continue
        price = latest_official_on_or_before(db, code, today - timedelta(days=1))
        flow = db.scalar(select(InvestorFlow).where(InvestorFlow.code == code, InvestorFlow.trading_date == today,
                                                    InvestorFlow.scope == "KRX_REGULAR").limit(1)) is not None
        catalyst_ok = False
        if thesis_id:
            h = db.scalar(select(Hypothesis).where(Hypothesis.uid == thesis_id))
            srcs = [s for s in load_sources(db, h.source_ids) if s.checked_at.date() >= today - timedelta(days=14)]
            catalyst_ok = independent_ac_count(srcs) >= 1
        c = Candidate(code, inst.name, inst.sector, price.close if price else None, price.trading_date if price else None,
                      price.trading_value if price else None, flow, catalyst_ok, inst.managed_issue, thesis_id)
        if c.official_close is None:
            c.unmet.append("신뢰 가능한 KRX 공식 가격 없음")
        if not c.trading_value or c.trading_value < cfg["min_trading_value_krw"]:
            c.unmet.append(f"거래대금(유동성) 기준 미달 또는 미확인(기준 {cfg['min_trading_value_krw']:,}원)")
        if not flow:
            c.unmet.append("당일 종목별 수급(KRX 정규장 범위) 미확인")
        if not catalyst_ok:
            c.unmet.append("최근 14일 내 신규 A~C 촉매 근거 없음")
        if not c.sector:
            c.unmet.append("업종 미확인(업종 분산 확인 불가)")
        if c.managed:
            c.unmet.append("관리종목·거래정지 위험")
        if inst.is_etf:
            c.unmet.append("ETF 판단 경로 미구현")
        out.append(c)
    return out


class Engine:
    def __init__(self, db: Session, ai: GeminiProvider | None = None, quote=None, clock: Clock | None = None,  # noqa: ANN001
                 poll_interval: float = 5.0):
        self.db = db
        self.ai = ai or GeminiProvider()
        self.quote = quote or NaverQuoteProvider()
        self.clock = clock or Clock()
        self.poll_interval = poll_interval

    def _ops_error(self, exp: Experiment, day: date, reason: str, detail: dict | None = None) -> dict:
        key = f"ops:{exp.id}:{day.isoformat()}:{reason[:60]}"
        try:
            append_event(self.db, exp, "ops_error", {"reason": reason, **(detail or {})}, self.clock.now(),
                         actor=ACTOR, idempotency_key=key)
        except PaperViolation:
            pass  # 같은 날 같은 사유는 한 번만 기록
        return {"result": "ops_error", "reason": reason}

    def run_cycle(self, exp: Experiment) -> dict:
        db = self.db
        now = self.clock.now()
        now_kst = to_kst(now)
        today = now_kst.date()
        if exp.execution_owner != ACTOR:
            return {"result": "skipped", "reason": "이 실험의 실행 권한이 엔진에 없음"}
        if exp.status not in ("첫판단대기", "운용중"):
            return {"result": "skipped", "reason": f"상태 {exp.status}"}
        if not is_in_regular_session(db, now_kst):
            return {"result": "skipped", "reason": "KRX 정규장 시간이 아님(또는 캘린더 미확인)"}
        if exp.end_date and today > exp.end_date:
            return {"result": "skipped", "reason": "운용기간 종료 — 정산 작업 대상"}

        cfg = {**get_setting(db, "paper_defaults"), "min_trading_value_krw": 1_000_000_000, "max_sector_bp": 5000}
        naver_rules = get_setting(db, "naver_validation")
        missing = []
        if not self.ai.configured:
            missing.append("AI 판단기 미연결(GEMINI_API_KEY·GEMINI_MODEL)")
        if not getattr(self.quote, "enabled", True):
            missing.append("장중 원본 시세 미연결(NAVER_QUOTE_ENABLED)")
        if not naver_rules.get("confirmed_by_owner"):
            missing.append("네이버 원본 검증 기준 미확정(probe 후 소유자 확정 필요)")
        if missing:
            return self._ops_error(exp, today, "필수 공급자 미연결: " + "; ".join(missing))

        st = replay(db, exp)
        candidates = gather_candidates(db, today, cfg)
        eligible = [c for c in candidates if not c.unmet]
        # 서로 다른 업종에서 최대 3개
        seen_sectors, compared = set(), []
        for c in eligible:
            if c.sector not in seen_sectors:
                compared.append(c)
                seen_sectors.add(c.sector)
            if len(compared) == 3:
                break
        if not compared and not st.held:
            return self._ops_error(exp, today, "필수 데이터를 모두 갖춘 후보가 없음",
                                   {"exclusions": [{"code": c.code, "unmet": c.unmet} for c in candidates]})

        prices = {c.code: c.official_close for c in candidates if c.official_close}
        for p in st.held:
            row = latest_official_on_or_before(db, p.code, today - timedelta(days=1))
            if row:
                prices[p.code] = row.close
        material = {
            "trading_date": today.isoformat(),
            "candidates": [{"code": c.code, "name": c.name, "sector": c.sector, "last_official_close": c.official_close,
                            "official_date": str(c.official_date), "trading_value_krw": c.trading_value,
                            "thesis_id": c.thesis_id} for c in compared],
            "positions": [{"code": p.code, "thesis_id": p.thesis_id, "invalidation_price": p.invalidation_price}
                          for p in st.held],
        }
        res = self.ai.generate_json(
            "후보를 촉매·실제 개선·수급·상대강도·거시 적합성 각 0~2점으로 비교하고, 매수 제안 시 사실 기반 무효화 "
            "조건과 가격, 이벤트 위험, 비용 후 보상 시나리오를 제시하라. 보유 포지션은 가설 상태를 점검하라. "
            "막연한 시장 불확실성만으로 현금 100%를 택하지 말고, 그렇게 할 경우 후보별 미달 요건을 적어라.",
            material, AI_SCHEMA)
        if not res.ok:
            return self._ops_error(exp, today, f"AI 판단기 호출 실패: {res.status.value}", {"detail": res.detail})
        return self._apply(exp, today, now, res.data, compared, candidates, st, prices, cfg)

    def _apply(self, exp, today, now, data, compared, candidates, st, prices, cfg) -> dict:  # noqa: ANN001, PLR0913
        db = self.db
        by_code = {c.code: c for c in compared}
        decision_id = f"D-{exp.id}-{uuid.uuid4().hex[:10]}"
        buys, rejected = [], []
        for prop in (data.get("compared") or [])[:3]:
            code = str(prop.get("code", ""))
            c = by_code.get(code)
            if c is None:
                rejected.append({"code": code, "reason": "비교 대상 후보가 아님"})
                continue
            scores = prop.get("scores") or {}
            if any(not isinstance(scores.get(a), int) or scores.get(a) not in (0, 1, 2) for a in AXES):
                rejected.append({"code": code, "reason": "점수 형식 오류"})
                continue
            total = sum(scores[a] for a in AXES)
            if prop.get("action") != "buy":
                continue
            reasons = []
            if total < 6:
                reasons.append(f"점수 {total}점(<6)")
            for f, label in (("catalyst", "촉매"), ("invalidation_condition", "무효화 조건"), ("event_risk", "이벤트 위험"),
                             ("next_check", "다음 확인"), ("counter_scenario", "반대 시나리오")):
                if not str(prop.get(f, "")).strip():
                    reasons.append(f"{label} 누락")
            inval = prop.get("invalidation_price")
            if not isinstance(inval, int) or inval <= 0 or inval >= c.official_close:
                reasons.append("무효화 가격이 올바르지 않음")
            if not isinstance(prop.get("reward_scenario_pct"), (int, float)):
                reasons.append("비용 후 보상 시나리오 누락")
            exploration = total < 7
            if reasons:
                rejected.append({"code": code, "reason": "; ".join(reasons)})
                continue
            target = prop.get("target_weight_bp") or 0
            if exploration:
                target = min(max(target, cfg["exploration_min_bp"]), cfg["exploration_max_bp"])
            target = min(target, exp.max_position_bp)
            qty = risk.size_buy(exp, st, c.official_close, inval, target, prices, cfg)
            if qty <= 0:
                rejected.append({"code": code, "reason": "비중·손실설계(NAV 1%)·현금 조건을 만족하는 정수 수량 없음"})
                continue
            plan_check = risk.check_buy(exp, st, risk.BuyPlan(code, qty, c.official_close, inval, c.sector, exploration),
                                        prices, cfg)
            if plan_check:
                rejected.append({"code": code, "reason": "; ".join(plan_check)})
                continue
            buys.append({"code": code, "quantity": qty, "invalidation_price": inval, "sector": c.sector,
                         "exploration": exploration, "thesis_id": c.thesis_id, "score": total, "scores": scores,
                         "catalyst": prop["catalyst"], "not_priced_reason": prop.get("not_priced_reason", ""),
                         "counter_scenario": prop["counter_scenario"],
                         "invalidation_condition": prop["invalidation_condition"], "event_risk": prop["event_risk"],
                         "next_check": prop["next_check"], "reward_scenario_pct": prop["reward_scenario_pct"]})
            if len(buys) + len(st.held) >= exp.max_positions:
                break
        reviews = []
        sells = []
        held_codes = {p.code: p for p in st.held}
        for r in data.get("position_reviews") or []:
            if r.get("code") in held_codes:
                reviews.append({k: r.get(k) for k in ("code", "thesis_status", "catalyst_progress", "remaining_risks")})
                if r.get("sell") and r.get("thesis_status") == "무효화":
                    sells.append({"code": r["code"], "quantity": held_codes[r["code"]].quantity,
                                  "thesis_id": held_codes[r["code"]].thesis_id})
        cash_reason = str(data.get("cash_only_reason", "")).strip()
        if compared and not buys and not st.held and not cash_reason:
            cash_reason = "AI가 현금 유지 사유를 제시하지 않음 — 후보별 미달 요건은 rejected 참조"

        decision_at = self.clock.now()
        try:
            lock_on_first_decision(db, exp, today, ACTOR)
        except LifecycleError as e:
            return self._ops_error(exp, today, str(e))
        append_event(db, exp, "decision", {
            "decision_id": decision_id, "decisionAt": decision_at.isoformat(), "trading_date": today.isoformat(),
            "compared": [c.code for c in compared], "buys": buys, "sells": sells, "rejected": rejected,
            "exclusions": [{"code": c.code, "unmet": c.unmet} for c in candidates if c.unmet],
            "position_reviews": reviews, "cash_only_reason": cash_reason if not buys else None,
            "ai_model": self.ai.model, **POLICY}, decision_at, actor=ACTOR, idempotency_key=f"decision:{decision_id}")
        if reviews:
            append_event(db, exp, "position_review", {"decision_id": decision_id, "reviews": reviews}, decision_at,
                         actor=ACTOR)
        results = []
        for side, items in (("sell", sells), ("buy", buys)):
            for it in items:
                order_id = f"O-{uuid.uuid4().hex[:12]}"
                order = {"order_id": order_id, "decision_id": decision_id, "thesis_id": it.get("thesis_id"),
                         "code": it["code"], "side": side, "quantity": it["quantity"],
                         "invalidation_price": it.get("invalidation_price"), "sector": it.get("sector"),
                         "exploration": it.get("exploration", False), "decisionAt": decision_at.isoformat(),
                         "createdAt": decision_at.isoformat(), "executionAt": decision_at.isoformat(), **POLICY}
                append_event(db, exp, "order", order, decision_at, actor=ACTOR, idempotency_key=f"order:{order_id}")
                r = execute_immediate(db, exp, order, self.quote.fetch, prices, cfg, actor=ACTOR, clock=self.clock,
                                      poll_interval=self.poll_interval)
                results.append({"order_id": order_id, "code": it["code"], "side": side, "result": r["result"]})
        return {"result": "decided", "decision_id": decision_id, "orders": results, "rejected": rejected}
