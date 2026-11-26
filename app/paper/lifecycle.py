"""
실험 생애주기: 등록 → 첫판단대기 → 운용중 → 정산대기 → 검증완료 / 폐기

- 첫 실제 판단 순간 config를 잠그고, 그 판단의 거래일을 시작일로 고정한다(등록 시각 아님).
- 기간 종료와 성과 검증 완료를 구분한다. 공식 가격이 없다고 기간을 연장하지 않는다.
- 종료 시 신규 판단·주문·체결을 막고, 남은 주문은 만료 사건으로 닫는다. 강제 매도 없음.
- 정산 = 기간 종료일 KRX 공식 종가로 보유분을 평가 + 현금·수량·비용 재검증 + 해시 체인 검증.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Experiment
from app.money import pct_change, trade_cost
from app.paper.ledger import append_event, events, replay, verify_chain
from app.services.audit import audit
from app.services.calendar import CalendarUnknown, add_trading_days
from app.services.market_data import latest_official
from app.timeutil import utcnow

LIMITS = {
    "initial_capital": (100_000, 10_000_000_000),
    "duration_trading_days": (1, 250),
    "max_positions": (1, 20),
    "max_position_bp": (100, 10000),
    "cost_bps": (0, 500),
}


class LifecycleError(Exception):
    pass


def register(db: Session, name: str, config: dict, actor: str) -> Experiment:
    for k, (lo, hi) in LIMITS.items():
        v = config.get(k)
        if not isinstance(v, int) or not (lo <= v <= hi):
            raise LifecycleError(f"{k}는 {lo}~{hi} 사이 정수여야 합니다.")
    exp = Experiment(name=name.strip() or "이름 없는 실험", status="첫판단대기", execution_owner="engine",
                     **{k: config[k] for k in LIMITS})
    db.add(exp)
    db.flush()
    append_event(db, exp, "lifecycle", {"status": "첫판단대기", "config": {k: config[k] for k in LIMITS},
                                        "note": "등록. 시작일은 첫 실제 판단 거래일로 고정됨"}, utcnow(), actor=actor)
    audit(db, actor, "experiment.register", str(exp.id), {k: config[k] for k in LIMITS})
    return exp


def lock_on_first_decision(db: Session, exp: Experiment, decision_day: date, actor: str) -> None:
    if exp.config_locked_at is not None:
        return
    try:
        end = decision_day if exp.duration_trading_days == 1 else add_trading_days(
            db, decision_day, exp.duration_trading_days - 1)
    except CalendarUnknown as e:
        raise LifecycleError(f"운용 종료일을 고정할 수 없습니다(캘린더 미확인): {e}") from None
    exp.start_date, exp.end_date = decision_day, end
    exp.config_locked_at = utcnow()
    exp.status = "운용중"
    db.flush()
    append_event(db, exp, "lifecycle", {"status": "운용중", "start_date": decision_day.isoformat(),
                                        "end_date": end.isoformat(), "config_locked": True}, utcnow(), actor=actor)


def discard(db: Session, exp: Experiment, reason: str, actor: str) -> None:
    if exp.status in ("검증완료", "폐기"):
        raise LifecycleError("이미 종료된 실험입니다.")
    if not reason.strip():
        raise LifecycleError("폐기 사유가 필요합니다.")
    _close_open_orders(db, exp, f"실험 폐기: {reason}", actor)
    exp.status = "폐기"
    db.flush()
    append_event(db, exp, "lifecycle", {"status": "폐기", "reason": reason}, utcnow(), actor=actor)
    audit(db, actor, "experiment.discard", str(exp.id), {"reason": reason})


def _close_open_orders(db: Session, exp: Experiment, reason: str, actor: str) -> int:
    st = replay(db, exp)
    for oid in list(st.open_orders):
        append_event(db, exp, "expiry", {"order_id": oid, "reason": reason}, utcnow(), actor=actor,
                     idempotency_key=f"end:{oid}")
    return len(st.open_orders)


def end_and_settle(db: Session, exp: Experiment, today: date, actor: str = "settlement") -> dict:
    """마감 후 정산 작업에서 호출. 기간이 끝난 실험만 처리한다."""
    if exp.status not in ("운용중", "정산대기") or exp.end_date is None or today < exp.end_date:
        return {"result": "not_due"}
    if exp.status == "운용중":
        expired = _close_open_orders(db, exp, "운용기간 종료 — 남은 주문 만료(이월 없음)", actor)
        exp.status = "정산대기"
        db.flush()
        append_event(db, exp, "lifecycle", {"status": "정산대기", "expired_orders": expired}, utcnow(), actor=actor)
    return try_verify(db, exp, actor)


def try_verify(db: Session, exp: Experiment, actor: str) -> dict:
    problems: list[str] = []
    ok, chain_msg = verify_chain(db, exp.id)
    if not ok:
        problems.append(f"원장 해시 체인 오류: {chain_msg}")
    st = replay(db, exp)
    if st.cash < 0:
        problems.append("현금이 음수입니다.")
    for p in st.positions.values():
        if p.quantity < 0:
            problems.append(f"{p.code} 수량이 음수입니다.")
    for f in events(db, exp.id, "fill"):
        pl = f.payload
        if pl["cost"] != trade_cost(pl["quantity"] * pl["fill_price"], exp.cost_bps):
            problems.append(f"fill seq {f.seq}: 비용 재계산 불일치")
    prices, missing = {}, []
    for p in st.held:
        row = latest_official(db, p.code, exp.end_date)
        if row is None:
            missing.append(p.code)
        else:
            prices[p.code] = row.close
    if missing:
        problems.append(f"종료일({exp.end_date}) KRX 공식 종가 미확인: {', '.join(missing)} — 기간 연장 없이 정산 대기")
    if problems:
        return {"result": "pending", "problems": problems}
    nav = st.nav(prices)
    ret = pct_change(nav, exp.initial_capital)
    append_event(db, exp, "settlement", {
        "end_date": exp.end_date.isoformat(), "official_nav": nav, "cash": st.cash, "total_cost": st.total_cost,
        "return_after_cost_pct": str(ret), "valuation_prices": prices,
        "basis": "종료일 KRX 정규장 공식 종가, 거래비용 차감 후, 세금·배당 미반영", "chain": chain_msg}, utcnow(), actor=actor)
    exp.status = "검증완료"
    db.flush()
    append_event(db, exp, "lifecycle", {"status": "검증완료"}, utcnow(), actor=actor)
    return {"result": "verified", "nav": nav, "return_pct": str(ret)}


def verified_summary(db: Session) -> dict:
    rows, excluded = [], []
    for exp in db.scalars(select(Experiment).order_by(Experiment.id)):
        if exp.status == "검증완료":
            st = [e for e in events(db, exp.id, "settlement")]
            if st:
                rows.append({"id": exp.id, "name": exp.name, "return_pct": Decimal(st[-1].payload["return_after_cost_pct"])})
                continue
        reason = {"폐기": "폐기된 실험", "정산대기": "정산 검증 대기", "운용중": "운용 중(미완료)",
                  "첫판단대기": "첫 판단 전", "등록": "등록만 됨"}.get(exp.status, exp.status)
        if exp.is_imported_readonly:
            reason = "가져온 과거 실험(검증 인증 소급 안 함)"
        excluded.append({"id": exp.id, "name": exp.name, "reason": reason})
    avg = (sum(r["return_pct"] for r in rows) / len(rows)).quantize(Decimal("0.01")) if rows else None
    return {"verified": rows, "excluded": excluded, "average_return_pct": avg,
            "label": "산출 대기" if avg is None else f"{avg:+}%"}
