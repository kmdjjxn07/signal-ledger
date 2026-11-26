"""
기업 심층검증 (9항목 gate).

항목이 '확인됨'이 되려면: 결론이 작성되어 있고, 작성자가 확인 표시를 했고,
독립 A~C 근거가 2개 이상이어야 한다(origin_key 중복 제거, 기업 자료는 최대 1개로 셈).
통과 조건: 9항목 모두 조사 + 8항목 이상 확인 + 핵심 3항목(수익구조·비용 지속성·전환비용) 확인
+ 유동성 확인 + 관리종목·거래정지 아님 + 구조적 적신호 없음.
통과하면 현재 후보(최대 1개)로 지정되고, 이전 후보는 이력으로 남는다.
ETF는 기업용 9항목을 적용하지 않는다(별도 검증 경로 미구현 → 차단).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Instrument, ResearchVersion
from app.services.audit import audit
from app.services.sources import independent_ac_count, load_sources
from app.timeutil import utcnow

ITEMS: list[tuple[str, str]] = [
    ("revenue_model", "수익구조와 전문용어"),
    ("competitors", "경쟁기업 2곳과 차이"),
    ("edge", "그 기업만의 장점과 현재 의미"),
    ("cost_structure", "비용구조"),
    ("cost_durability", "비용구조·현금흐름 지속성"),
    ("customers", "B2B/B2C와 고객 구매 이유"),
    ("market_position", "시장지위·점유율 전략·실효성"),
    ("switching_cost", "고객 전환비용과 경쟁사 대비 순위 추론"),
    ("news", "최근 긍정·부정 뉴스"),
]
ITEM_LABEL = dict(ITEMS)
CORE_ITEMS = ("revenue_model", "cost_durability", "switching_cost")


class ResearchError(Exception):
    pass


def evaluate_items(db: Session, items: dict) -> dict:
    per_item, investigated, verified = {}, 0, 0
    for key, label in ITEMS:
        it = items.get(key) or {}
        conclusion = (it.get("conclusion") or "").strip()
        srcs = load_sources(db, [int(s) for s in it.get("source_ids", [])])
        indep = independent_ac_count(srcs)
        ok = bool(conclusion) and bool(it.get("verified")) and indep >= 2
        reason = None
        if not conclusion:
            reason = "조사 내용 없음"
        elif not it.get("verified"):
            reason = "작성자 확인 표시 없음"
        elif indep < 2:
            reason = f"독립 A~C 근거 {indep}개(2개 필요)"
        investigated += bool(conclusion)
        verified += ok
        per_item[key] = {"label": label, "conclusion": conclusion, "verified": bool(it.get("verified")),
                         "source_ids": [s.id for s in srcs], "independent_sources": indep, "fully_verified": ok,
                         "reason": reason}
    return {"items": per_item, "investigated": investigated, "fully_verified": verified}


def submit_research(db: Session, code: str, payload: dict, actor: str) -> ResearchVersion:
    inst = db.get(Instrument, code)
    if inst is None:
        raise ResearchError("종목 마스터에 없는 코드입니다. KRX 공식 데이터를 먼저 동기화하세요.")
    if inst.is_etf or code.startswith("IDX:"):
        raise ResearchError("ETF·지수에는 기업용 9항목 검증을 적용하지 않습니다(별도 검증 경로 미구현).")
    ev = evaluate_items(db, payload.get("items") or {})
    red_flags = [str(f).strip() for f in payload.get("red_flags", []) if str(f).strip()]
    liquidity_ok = payload.get("liquidity_ok")
    failed: list[str] = []
    if ev["investigated"] < 9:
        failed.append(f"9항목 중 {ev['investigated']}개만 조사됨")
    if ev["fully_verified"] < 8:
        failed.append(f"확인된 항목 {ev['fully_verified']}개(8개 이상 필요)")
    core_ok = all(ev["items"][k]["fully_verified"] for k in CORE_ITEMS)
    if not core_ok:
        failed.append("핵심 항목 미통과: " + ", ".join(ITEM_LABEL[k] for k in CORE_ITEMS
                                                      if not ev["items"][k]["fully_verified"]))
    if liquidity_ok is not True:
        failed.append("유동성 확인 안 됨")
    if inst.managed_issue:
        failed.append("관리종목·거래정지 등 위험 표시")
    if red_flags:
        failed.append("구조적 적신호: " + "; ".join(red_flags))
    passed = not failed
    next_checks = [str(x).strip() for x in payload.get("next_checks", []) if str(x).strip()]
    if not passed and not next_checks:
        raise ResearchError("미통과 검증에는 다음에 확인할 구체적인 자료(보고서·주석·계약·고객 근거)를 적어야 합니다.")
    rv = ResearchVersion(code=code, checked_at=utcnow(), items=ev["items"], red_flags=red_flags,
                         liquidity_ok=liquidity_ok, status="통과" if passed else "관찰 보류", core_passed=core_ok,
                         fully_verified_items=ev["fully_verified"], failed_reasons=failed, next_checks=next_checks,
                         is_current_candidate=False, created_by=actor)
    db.add(rv)
    db.flush()
    if passed:
        for other in db.scalars(select(ResearchVersion).where(ResearchVersion.is_current_candidate)):
            other.is_current_candidate = False
        rv.is_current_candidate = True
        db.flush()
    audit(db, actor, "research.submit", code, {"status": rv.status, "version_id": rv.id})
    return rv


def current_candidate(db: Session) -> ResearchVersion | None:
    return db.scalar(select(ResearchVersion).where(ResearchVersion.is_current_candidate).limit(1))


def latest_for_code(db: Session, code: str) -> ResearchVersion | None:
    return db.scalar(select(ResearchVersion).where(ResearchVersion.code == code)
                     .order_by(ResearchVersion.id.desc()).limit(1))


def history_for_code(db: Session, code: str) -> list[ResearchVersion]:
    return list(db.scalars(select(ResearchVersion).where(ResearchVersion.code == code)
                           .order_by(ResearchVersion.id.desc())))
