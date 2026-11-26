"""
가설 평가 (absolute-price-v1).

주 평가 = 고정 판정일 KRX 정규장 종가 / 고정 기준가격 − 1 (비용·배당 전 가격변동).
- up: 상승이면 적중(hit), 하락이면 실패(miss)
- down: 하락이면 적중, 상승이면 실패
- 보합(flat): 평균에는 포함, 적중률 분모에서는 제외
- 판정일 공식 종가가 없으면 pending(보류) — 다음 날 가격으로 대체하지 않는다
- 사건은 append-only. 정정은 supersedes_id로 새 사건 추가.
표본(적중+실패) 20건 미만이면 '추적 중'으로 표시한다.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Hypothesis, HypothesisEvaluation, LegacyRecord
from app.money import pct_change
from app.services.market_data import latest_official

MIN_SAMPLE = 20


def current_event(db: Session, hid: int, horizon: str) -> HypothesisEvaluation | None:
    return db.scalar(select(HypothesisEvaluation).where(HypothesisEvaluation.hypothesis_id == hid,
                                                        HypothesisEvaluation.horizon == horizon)
                     .order_by(HypothesisEvaluation.id.desc()).limit(1))


def judge(direction: str, change: Decimal) -> str:
    if change == 0:
        return "flat"
    up = change > 0
    return "hit" if (up and direction == "up") or (not up and direction == "down") else "miss"


def evaluate_due(db: Session, as_of: date) -> dict:
    """판정일이 as_of 이하인데 확정 결과가 없는 가설을 평가한다."""
    done = {"hit": 0, "miss": 0, "flat": 0, "pending": 0}
    hyps = db.scalars(select(Hypothesis).where(Hypothesis.status.in_(["활성", "중기추적"]))).all()
    for h in hyps:
        for horizon, jdate in (("short", h.short_date), ("medium", h.medium_date)):
            if jdate > as_of:
                continue
            cur = current_event(db, h.id, horizon)
            if cur is not None and cur.outcome in ("hit", "miss", "flat"):
                continue
            price = latest_official(db, h.code, jdate)
            if price is None:
                if cur is None:  # 보류 사건은 한 번만 남긴다
                    db.add(HypothesisEvaluation(hypothesis_id=h.id, horizon=horizon, judgement_date=jdate,
                                                outcome="pending", reason="판정일 KRX 공식 종가 미확인"))
                done["pending"] += 1
                continue
            change = pct_change(price.close, h.base_price)
            outcome = judge(h.direction, change)
            db.add(HypothesisEvaluation(hypothesis_id=h.id, horizon=horizon, judgement_date=jdate,
                                        price_id=price.id, close=price.close, change_pct=str(change),
                                        outcome=outcome, reason="고정 판정일 KRX 정규장 종가 기준(비용·배당 전)",
                                        supersedes_id=cur.id if cur else None))
            done[outcome] += 1
        db.flush()
        _advance_status(db, h)
    return done


def _advance_status(db: Session, h: Hypothesis) -> None:
    s = current_event(db, h.id, "short")
    m = current_event(db, h.id, "medium")
    final = ("hit", "miss", "flat")
    if m is not None and m.outcome in final:
        h.status = "종료"
    elif s is not None and s.outcome in final and h.status == "활성":
        h.status = "중기추적"  # 활성 슬롯을 차지하지 않음


def correct_evaluation(db: Session, event_id: int, price_id: int, reason: str) -> HypothesisEvaluation:
    """공식 가격 정정 반영: 원 사건 보존 + 정정 사건 추가."""
    from app.models import OfficialPrice
    old = db.get(HypothesisEvaluation, event_id)
    price = db.get(OfficialPrice, price_id)
    h = db.get(Hypothesis, old.hypothesis_id) if old else None
    if old is None or price is None or h is None:
        raise ValueError("정정 대상이 없습니다.")
    if price.code != h.code or price.trading_date != old.judgement_date:
        raise ValueError("판정일·종목이 일치하는 공식 가격만 정정에 쓸 수 있습니다.")
    change = pct_change(price.close, h.base_price)
    ev = HypothesisEvaluation(hypothesis_id=h.id, horizon=old.horizon, judgement_date=old.judgement_date,
                              price_id=price.id, close=price.close, change_pct=str(change),
                              outcome=judge(h.direction, change), reason=f"정정: {reason}", supersedes_id=old.id)
    db.add(ev)
    db.flush()
    return ev


def _bucket(rows: list[tuple[Hypothesis, HypothesisEvaluation | None]], as_of: date, horizon: str) -> dict:
    hit = miss = flat = pending = not_due = 0
    changes: list[Decimal] = []
    adverse: list[Decimal] = []
    pending_reasons: list[str] = []
    for h, ev in rows:
        jdate = h.short_date if horizon == "short" else h.medium_date
        if jdate > as_of:
            not_due += 1
            continue
        if ev is None or ev.outcome == "pending":
            pending += 1
            pending_reasons.append(f"{h.uid}: {ev.reason if ev else '평가 미실행'}")
            continue
        c = Decimal(ev.change_pct)
        changes.append(c)
        signed = c if h.direction == "up" else -c
        adverse.append(min(signed, Decimal(0)))
        hit += ev.outcome == "hit"
        miss += ev.outcome == "miss"
        flat += ev.outcome == "flat"
    n = hit + miss
    rate = (Decimal(hit) * 100 / n).quantize(Decimal("0.1")) if n else None
    return {"hit": hit, "miss": miss, "flat": flat, "pending": pending, "not_due": not_due, "sample": n,
            "hit_rate": rate, "tracking": n < MIN_SAMPLE,
            "avg_change": (sum(changes) / len(changes)).quantize(Decimal("0.01")) if changes else None,
            "avg_adverse": (sum(adverse) / len(adverse)).quantize(Decimal("0.01")) if adverse else None,
            "worst_adverse": min(adverse) if adverse else None, "pending_reasons": pending_reasons}


def statistics(db: Session, as_of: date) -> dict:
    hyps = list(db.scalars(select(Hypothesis).where(Hypothesis.evaluation_policy == "absolute-price-v1")))
    result = {"total_issued": len(hyps), "legacy_count": db.query(LegacyRecord).filter(
        LegacyRecord.kind == "legacyRelative").count()}
    expl = sum(h.quality_tier == "Exploration" for h in hyps)
    result["exploration_share"] = {"count": expl, "total": len(hyps),
                                   "basis": "발행된 absolute-price-v1 카드 수 기준"}
    for horizon in ("short", "medium"):
        rows = [(h, current_event(db, h.id, horizon)) for h in hyps]
        result[horizon] = {
            "all": _bucket(rows, as_of, horizon),
            "Core": _bucket([r for r in rows if r[0].quality_tier == "Core"], as_of, horizon),
            "Exploration": _bucket([r for r in rows if r[0].quality_tier == "Exploration"], as_of, horizon),
        }
    return result
