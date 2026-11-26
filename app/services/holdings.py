"""
내 보유종목 (실험 자산과 완전히 분리).

- 거래 기록은 append-only. 수정은 새 행(voids_id로 이전 행 지정)을 추가하고, 삭제는 수량 0 무효화 행으로 한다.
- 공식 평가는 KRX 정규장 공식 종가만 사용하고, 평가일·수집시각을 함께 보여준다.
- 브리핑·엔진 작업은 이 모듈의 쓰기 함수를 호출하지 않는다. 외부 AI로 보내지 않는다.
- 평균단가는 이동평균법(매도 시 평균단가 유지)으로 계산하고 원 미만은 버린다(표시용).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Holding, HoldingTransaction, Instrument, OfficialPrice
from app.money import pct_change
from app.services.audit import audit
from app.services.market_data import latest_official_on_or_before


class HoldingError(Exception):
    pass


def add_holding(db: Session, code: str, name: str | None, actor: str) -> Holding:
    code = code.strip()
    if len(code) != 6 or not code.isalnum():
        raise HoldingError("종목코드는 6자리여야 합니다.")
    if db.scalar(select(Holding).where(Holding.code == code)):
        raise HoldingError("이미 등록된 종목입니다.")
    inst = db.get(Instrument, code)
    h = Holding(code=code, name=(name or (inst.name if inst else code)).strip())
    db.add(h)
    db.flush()
    audit(db, actor, "holding.add", code)
    return h


def record_transaction(db: Session, holding: Holding, *, trade_date: date, side: str, quantity: int,
                       unit_price: int, note: str | None, actor: str, voids_id: int | None = None) -> HoldingTransaction:
    if side not in ("buy", "sell"):
        raise HoldingError("매수(buy) 또는 매도(sell)만 가능합니다.")
    if not isinstance(quantity, int) or quantity < 0 or not isinstance(unit_price, int) or unit_price < 0:
        raise HoldingError("수량·단가는 0 이상의 정수여야 합니다.")
    if voids_id is None and (quantity == 0 or unit_price == 0):
        raise HoldingError("새 거래의 수량·단가는 1 이상이어야 합니다.")
    if voids_id is not None:
        old = db.get(HoldingTransaction, voids_id)
        if old is None or old.holding_id != holding.id:
            raise HoldingError("정정 대상 거래가 이 종목에 없습니다.")
        if voids_id in _voided_ids(db, holding.id):
            raise HoldingError("이미 정정된 거래입니다.")
    tx = HoldingTransaction(holding_id=holding.id, trade_date=trade_date, side=side, quantity=quantity,
                            unit_price=unit_price, note=note, voids_id=voids_id)
    db.add(tx)
    db.flush()
    if side == "sell" and position(db, holding).quantity < 0:
        raise HoldingError("보유 수량보다 많이 매도할 수 없습니다.")
    audit(db, actor, "holding.tx" if voids_id is None else "holding.tx_correct", holding.code,
          {"tx_id": tx.id, "voids_id": voids_id})
    return tx


def _voided_ids(db: Session, holding_id: int) -> set[int]:
    return {v for v in db.scalars(select(HoldingTransaction.voids_id).where(
        HoldingTransaction.holding_id == holding_id, HoldingTransaction.voids_id.is_not(None)))}


def effective_transactions(db: Session, holding: Holding) -> list[HoldingTransaction]:
    voided = _voided_ids(db, holding.id)
    rows = db.scalars(select(HoldingTransaction).where(HoldingTransaction.holding_id == holding.id)
                      .order_by(HoldingTransaction.trade_date, HoldingTransaction.id)).all()
    return [t for t in rows if t.id not in voided and t.quantity > 0]


@dataclass
class Position:
    quantity: int
    total_cost: int
    avg_price: int | None


def position(db: Session, holding: Holding) -> Position:
    qty, cost = 0, 0
    for t in effective_transactions(db, holding):
        if t.side == "buy":
            qty += t.quantity
            cost += t.quantity * t.unit_price
        else:
            avg_cost_removed = (cost * t.quantity) // qty if qty else 0
            qty -= t.quantity
            cost -= avg_cost_removed
    return Position(qty, cost, (cost // qty) if qty > 0 else None)


@dataclass
class Valuation:
    price: OfficialPrice | None
    value: int | None
    pnl: int | None
    pnl_pct: Decimal | None
    prev_price: OfficialPrice | None
    day_change: int | None
    day_change_pct: Decimal | None


def valuation(db: Session, holding: Holding, as_of: date) -> Valuation:
    pos = position(db, holding)
    price = latest_official_on_or_before(db, holding.code, as_of)
    if price is None or pos.quantity <= 0:
        return Valuation(price, None, None, None, None, None, None)
    value = price.close * pos.quantity
    pnl = value - pos.total_cost
    pnl_pct = pct_change(value, pos.total_cost) if pos.total_cost > 0 else None
    from datetime import timedelta
    prev = latest_official_on_or_before(db, holding.code, price.trading_date - timedelta(days=1))
    day_change = (price.close - prev.close) * pos.quantity if prev else None
    day_pct = pct_change(price.close, prev.close) if prev else None
    return Valuation(price, value, pnl, pnl_pct, prev, day_change, day_pct)
