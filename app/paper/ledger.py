"""
모의운용 원장: 실험별 append-only 사건 + 해시 체인.

해시 체인은 '저장된 뒤 바뀌었는지'를 탐지하는 도구일 뿐, 기록 내용이 사실임을 보증하지 않는다.
현금·포지션은 별도 테이블에 덮어쓰지 않고 fill 사건을 처음부터 재생(replay)해서 계산한다.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Experiment, PaperEvent
from app.services.sources import canonical_json

GENESIS = "0" * 64
TRADING_TYPES = {"decision", "order", "fill"}
TERMINAL_STATUSES = {"정산대기", "검증완료", "폐기"}
ALL_TYPES = {"lifecycle", "decision", "order", "fill", "cancel", "expiry", "nav", "settlement", "ops_error",
             "position_review"}


class PaperViolation(Exception):
    pass


def _hash(prev: str, exp_id: int, seq: int, type_: str, at: datetime, payload: dict) -> str:
    body = canonical_json({"prev": prev, "experiment": exp_id, "seq": seq, "type": type_,
                           "at": at.isoformat(), "payload": payload})
    return hashlib.sha256(body.encode()).hexdigest()


def append_event(db: Session, exp: Experiment, type_: str, payload: dict, at: datetime, *, actor: str,
                 idempotency_key: str | None = None) -> PaperEvent:
    if type_ not in ALL_TYPES:
        raise PaperViolation(f"알 수 없는 사건 유형: {type_}")
    if exp.is_imported_readonly:
        raise PaperViolation("가져온 과거 실험은 읽기 전용입니다.")
    if type_ in TRADING_TYPES:
        if exp.status in TERMINAL_STATUSES:
            raise PaperViolation(f"'{exp.status}' 상태의 실험에는 판단·주문·체결을 추가할 수 없습니다.")
        if actor != exp.execution_owner:
            raise PaperViolation(f"이 실험의 실행 권한은 '{exp.execution_owner}'에게만 있습니다(요청: {actor}).")
    if idempotency_key:
        dup = db.scalar(select(PaperEvent).where(PaperEvent.idempotency_key == idempotency_key))
        if dup is not None:
            raise PaperViolation(f"중복 사건(멱등성 키 {idempotency_key})")
    last = db.scalar(select(PaperEvent).where(PaperEvent.experiment_id == exp.id)
                     .order_by(PaperEvent.seq.desc()).limit(1))
    seq = (last.seq + 1) if last else 1
    prev = last.hash if last else GENESIS
    ev = PaperEvent(experiment_id=exp.id, seq=seq, type=type_, at=at, payload=payload,
                    idempotency_key=idempotency_key, prev_hash=prev, hash=_hash(prev, exp.id, seq, type_, at, payload))
    db.add(ev)
    db.flush()
    return ev


def events(db: Session, exp_id: int, type_: str | None = None) -> list[PaperEvent]:
    q = select(PaperEvent).where(PaperEvent.experiment_id == exp_id)
    if type_:
        q = q.where(PaperEvent.type == type_)
    return list(db.scalars(q.order_by(PaperEvent.seq)))


def verify_chain(db: Session, exp_id: int) -> tuple[bool, str]:
    prev = GENESIS
    expected_seq = 1
    for ev in events(db, exp_id):
        if ev.seq != expected_seq:
            return False, f"순번 불연속: {ev.seq} (예상 {expected_seq})"
        if ev.prev_hash != prev:
            return False, f"seq {ev.seq}: 이전 해시 불일치"
        if ev.hash != _hash(prev, exp_id, ev.seq, ev.type, ev.at, ev.payload):
            return False, f"seq {ev.seq}: 내용 해시 불일치(변조 의심)"
        prev, expected_seq = ev.hash, expected_seq + 1
    return True, f"{expected_seq - 1}건 검증"


@dataclass
class Position:
    code: str
    quantity: int = 0
    cost_basis: int = 0  # 매수금액 합(비용 제외)
    sector: str | None = None
    exploration: bool = False
    invalidation_price: int | None = None
    thesis_id: str | None = None


@dataclass
class State:
    cash: int
    positions: dict[str, Position] = field(default_factory=dict)
    open_orders: dict[str, dict] = field(default_factory=dict)
    total_cost: int = 0
    fills: int = 0
    decisions: int = 0

    def nav(self, prices: dict[str, int]) -> int | None:
        total = self.cash
        for p in self.positions.values():
            if p.quantity == 0:
                continue
            if p.code not in prices:
                return None
            total += p.quantity * prices[p.code]
        return total

    @property
    def held(self) -> list[Position]:
        return [p for p in self.positions.values() if p.quantity > 0]


def replay(db: Session, exp: Experiment) -> State:
    st = State(cash=exp.initial_capital)
    for ev in events(db, exp.id):
        p = ev.payload
        if ev.type == "decision":
            st.decisions += 1
        elif ev.type == "order":
            st.open_orders[p["order_id"]] = p
        elif ev.type in ("expiry", "cancel"):
            st.open_orders.pop(p["order_id"], None)
        elif ev.type == "fill":
            order = st.open_orders.pop(p["order_id"], None) or {}
            pos = st.positions.setdefault(p["code"], Position(code=p["code"]))
            notional = p["quantity"] * p["fill_price"]
            if p["side"] == "buy":
                st.cash -= notional + p["cost"]
                pos.quantity += p["quantity"]
                pos.cost_basis += notional
                pos.sector = order.get("sector", pos.sector)
                pos.exploration = order.get("exploration", pos.exploration)
                pos.invalidation_price = order.get("invalidation_price", pos.invalidation_price)
                pos.thesis_id = order.get("thesis_id", pos.thesis_id)
            else:
                avg = pos.cost_basis // pos.quantity if pos.quantity else 0
                st.cash += notional - p["cost"]
                pos.quantity -= p["quantity"]
                pos.cost_basis -= avg * p["quantity"]
                if pos.quantity == 0:
                    pos.cost_basis = 0
            st.total_cost += p["cost"]
            st.fills += 1
    return st


def event_count(db: Session, exp_id: int) -> int:
    return db.scalar(select(func.count()).select_from(PaperEvent).where(PaperEvent.experiment_id == exp_id)) or 0
