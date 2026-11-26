from datetime import date

import pytest

from app.models import Hypothesis, HypothesisEvaluation, LedgerViolation
from app.services import evaluation
from app.services.hypotheses import IssueRejected, classify, issue
from app.services.research import ResearchError, submit_research
from tests.conftest import add_instrument, add_price, add_source, research_items

D = date(2026, 11, 24)


def _passed_research(db, code="005930"):
    add_instrument(db, code, f"합성{code}", sector=f"업종{code}")
    return submit_research(db, code, {"items": research_items(db), "liquidity_ok": True, "red_flags": [],
                                      "next_checks": []}, "owner:t")


def _payload(rv, code="005930", scores=None, **kw):
    src = add_source(kw.pop("db"), "A")
    return {"code": code, "research_version_id": rv.id, "catalyst": "수주 공시", "not_priced_reason": "아직 반영 전",
            "counter_scenario": "수주 취소", "invalidation": "계약 해지 공시", "next_check": "다음 주 실적",
            "direction": "up", "confidence": "보통", "scores": scores or {"catalyst": 2, "improvement": 2, "flow": 1,
                                                                        "relative_strength": 1, "macro_fit": 1},
            "source_ids": [src.id], "flow_source_ids": [src.id], **kw}


def test_research_gate_independent_sources(db):
    add_instrument(db, "005930")
    items = research_items(db)
    # 같은 origin_key 두 개는 독립 근거 1개로 센다
    dup = add_source(db, "A", origin="same-press")
    dup2 = add_source(db, "C", origin="same-press")
    items["revenue_model"]["source_ids"] = [dup.id, dup2.id]
    rv = submit_research(db, "005930", {"items": items, "liquidity_ok": True, "next_checks": ["사업보고서 주석 확인"]},
                         "owner:t")
    assert rv.status == "관찰 보류" and not rv.core_passed
    assert "핵심 항목 미통과" in " ".join(rv.failed_reasons)


def test_company_claims_count_once(db):
    add_instrument(db, "005930")
    items = research_items(db)
    c1, c2 = add_source(db, "A", company=True), add_source(db, "A", company=True)
    items["switching_cost"]["source_ids"] = [c1.id, c2.id]
    rv = submit_research(db, "005930", {"items": items, "liquidity_ok": True, "next_checks": ["고객 계약 확인"]}, "t")
    assert rv.status == "관찰 보류"


def test_research_requires_next_checks_when_failing_and_blocks_etf(db):
    add_instrument(db, "005930")
    with pytest.raises(ResearchError):
        submit_research(db, "005930", {"items": {}, "liquidity_ok": True}, "t")
    add_instrument(db, "091160", "KODEX", etf=True)
    with pytest.raises(ResearchError):
        submit_research(db, "091160", {"items": research_items(db), "liquidity_ok": True}, "t")


def test_passed_research_becomes_single_candidate(db):
    a = _passed_research(db, "005930")
    b = _passed_research(db, "000660")
    db.refresh(a)
    assert a.status == "통과" and b.is_current_candidate and not a.is_current_candidate


def test_classify_scores():
    assert classify({"catalyst": 2, "improvement": 2, "flow": 2, "relative_strength": 1, "macro_fit": 0})[0] == "Core"
    assert classify({"catalyst": 2, "improvement": 2, "flow": 1, "relative_strength": 1, "macro_fit": 0})[0] == "Exploration"
    assert classify({"catalyst": 1, "improvement": 1, "flow": 1, "relative_strength": 1, "macro_fit": 1})[0] is None
    with pytest.raises(IssueRejected):
        classify({"catalyst": 3})


def test_issue_and_immutability(calendar_2026):
    db = calendar_2026
    rv = _passed_research(db)
    add_price(db, "005930", date(2026, 11, 23), 70000)
    h = issue(db, _payload(rv, db=db), "owner:t", today=D)
    db.commit()
    assert h.quality_tier == "Core" and h.base_price == 70000
    assert h.short_date == date(2026, 11, 26) and h.medium_date == date(2026, 12, 21)
    h.base_price = 1
    with pytest.raises(LedgerViolation):
        db.flush()
    db.rollback()
    h = db.get(Hypothesis, h.id)
    h.short_date = date(2027, 1, 1)
    with pytest.raises(LedgerViolation):
        db.flush()
    db.rollback()
    h = db.get(Hypothesis, h.id)
    h.next_check = "변경 가능"  # 다음 확인 일정은 감사로그와 함께 바꿀 수 있는 필드
    db.flush()


def test_issue_rejections(calendar_2026):
    db = calendar_2026
    rv = _passed_research(db)
    # 가격 없음
    with pytest.raises(IssueRejected) as e:
        issue(db, _payload(rv, db=db), "t", today=D)
    assert any("기준가격" in r for r in e.value.reasons)
    add_price(db, "005930", date(2026, 11, 23), 70000)
    # D등급 단독
    d_src = add_source(db, "D")
    p = _payload(rv, db=db)
    p["source_ids"] = [d_src.id]
    with pytest.raises(IssueRejected) as e:
        issue(db, p, "t", today=D)
    assert any("D등급" in r for r in e.value.reasons)
    # Exploration은 확신도 낮음
    p = _payload(rv, db=db, scores={"catalyst": 2, "improvement": 2, "flow": 1, "relative_strength": 1, "macro_fit": 0})
    with pytest.raises(IssueRejected):
        issue(db, p, "t", today=D)
    # 수급 근거 없음
    p = _payload(rv, db=db)
    p["flow_source_ids"] = []
    with pytest.raises(IssueRejected) as e:
        issue(db, p, "t", today=D)
    assert any("수급" in r for r in e.value.reasons)
    # 거시 역풍
    with pytest.raises(IssueRejected):
        issue(db, _payload(rv, db=db, macro_headwind=True), "t", today=D)


def test_issue_requires_latest_research_and_limits(calendar_2026):
    db = calendar_2026
    rv = _passed_research(db)
    add_price(db, "005930", date(2026, 11, 23), 70000)
    submit_research(db, "005930", {"items": {}, "liquidity_ok": False, "next_checks": ["재조사"]}, "t")
    with pytest.raises(IssueRejected) as e:
        issue(db, _payload(rv, db=db), "t", today=D)
    assert any("더 최근" in r for r in e.value.reasons)


def test_active_and_exploration_limits(calendar_2026):
    db = calendar_2026
    codes = ["000001", "000002", "000003", "000004"]
    rvs = {}
    for c in codes:
        rvs[c] = _passed_research(db, c)
        add_price(db, c, date(2026, 11, 23), 10000)
    expl = {"catalyst": 2, "improvement": 2, "flow": 1, "relative_strength": 1, "macro_fit": 0}
    issue(db, _payload(rvs["000001"], code="000001", db=db, scores=expl, confidence="낮음"), "t", today=D)
    with pytest.raises(IssueRejected) as e:
        issue(db, _payload(rvs["000002"], code="000002", db=db, scores=expl, confidence="낮음"), "t", today=D)
    assert any("Exploration" in r for r in e.value.reasons)
    issue(db, _payload(rvs["000002"], code="000002", db=db), "t", today=D)
    issue(db, _payload(rvs["000003"], code="000003", db=db), "t", today=D)
    with pytest.raises(IssueRejected) as e:
        issue(db, _payload(rvs["000004"], code="000004", db=db), "t", today=D)
    assert any("활성 카드" in r for r in e.value.reasons)


def test_evaluation_hit_flat_pending_and_status(calendar_2026):
    db = calendar_2026
    rv = _passed_research(db)
    add_price(db, "005930", date(2026, 11, 23), 70000)
    h = issue(db, _payload(rv, db=db), "t", today=D)
    # 단기 판정일 공식 종가 없음 → 보류, 다음날 가격으로 대체 안 함
    add_price(db, "005930", date(2026, 11, 27), 99999)
    evaluation.evaluate_due(db, date(2026, 11, 27))
    ev = evaluation.current_event(db, h.id, "short")
    assert ev.outcome == "pending"
    assert h.status == "활성"
    add_price(db, "005930", date(2026, 11, 26), 70000)  # 판정일 가격 도착(보합)
    evaluation.evaluate_due(db, date(2026, 11, 27))
    ev = evaluation.current_event(db, h.id, "short")
    assert ev.outcome == "flat" and ev.supersedes_id is not None
    assert db.get(Hypothesis, h.id).status == "중기추적"  # 활성 슬롯 해제
    stats = evaluation.statistics(db, date(2026, 11, 27))
    s = stats["short"]["all"]
    assert s["flat"] == 1 and s["sample"] == 0 and s["hit_rate"] is None and s["tracking"]


def test_statistics_denominators(calendar_2026):
    db = calendar_2026
    rvs = {}
    for i, c in enumerate(["000011", "000012", "000013"]):
        rvs[c] = _passed_research(db, c)
        add_price(db, c, date(2026, 11, 23), 10000)
        p = _payload(rvs[c], code=c, db=db)
        p["direction"] = "down" if i == 2 else "up"
        issue(db, p, "t", today=D)
    add_price(db, "000011", date(2026, 11, 26), 11000)   # up 적중
    add_price(db, "000012", date(2026, 11, 26), 9000)    # up 실패
    add_price(db, "000013", date(2026, 11, 26), 9500)    # down 적중
    evaluation.evaluate_due(db, date(2026, 11, 26))
    s = evaluation.statistics(db, date(2026, 11, 26))["short"]["all"]
    assert (s["hit"], s["miss"], s["sample"]) == (2, 1, 3)
    assert str(s["hit_rate"]) == "66.7" and s["tracking"]
    assert str(s["worst_adverse"]) == "-10.00"
    assert db.query(HypothesisEvaluation).count() == 3


def test_evaluation_events_append_only(calendar_2026):
    db = calendar_2026
    rv = _passed_research(db)
    add_price(db, "005930", date(2026, 11, 23), 70000)
    h = issue(db, _payload(rv, db=db), "t", today=D)
    add_price(db, "005930", date(2026, 11, 26), 71000)
    evaluation.evaluate_due(db, date(2026, 11, 26))
    ev = evaluation.current_event(db, h.id, "short")
    ev.outcome = "miss"
    with pytest.raises(LedgerViolation):
        db.flush()
