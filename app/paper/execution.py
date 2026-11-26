"""
즉시 모의체결 (policyVersion active-paper-v2 / executionPolicy immediate-paper-v1).

절차: 주문이 먼저 저장된 뒤, 해당 종목 원본 시세를 새로 조회해 '결정 이후 첫 적격 KRX 정규장 관측'을
얻는다. 관측의 원자료 시각과 수신 시각이 모두 결정 후 120초 이내여야 한다. 그 가격으로 위험 검증을
다시 통과하면 fill 사건 하나를 추가한다(현금·포지션은 fill 재생으로 계산되므로 단일 insert가 원자적).
창 안에 적격 관측이 없으면 expiry 사건을 남기고 다음 시점으로 이월하지 않는다.
"""
from __future__ import annotations

import time as _time
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Experiment, Observation
from app.money import trade_cost
from app.paper import risk
from app.paper.ledger import PaperViolation, append_event, replay
from app.providers.base import ProviderResult
from app.services.market_data import record_observation
from app.timeutil import utcnow

POLICY = {"policyVersion": "active-paper-v2", "executionPolicy": "immediate-paper-v1", "executionType": "immediate"}


class Clock:
    """테스트에서 시간을 제어하기 위한 시계."""

    def now(self) -> datetime:
        return utcnow()

    def sleep(self, seconds: float) -> None:
        _time.sleep(seconds)


def execute_immediate(db: Session, exp: Experiment, order: dict, fetch: Callable[[str], ProviderResult],
                      prices_for_nav: dict[str, int], cfg: dict, *, actor: str, clock: Clock | None = None,
                      poll_interval: float = 5.0) -> dict:
    clock = clock or Clock()
    if order["order_id"] not in replay(db, exp).open_orders:
        return {"result": "blocked", "reason": "이미 체결·만료·취소된 주문입니다(중복 체결 금지)."}
    decision_at = datetime.fromisoformat(order["decisionAt"])
    window = timedelta(seconds=cfg.get("fill_window_sec", 120))
    deadline = decision_at + window
    attempts: list[dict] = []
    chosen: Observation | None = None

    while True:
        requested_at = clock.now()
        if requested_at > deadline:
            break
        res = fetch(order["code"])
        received_at = clock.now()
        obs = record_observation(db, order["code"], res, requested_at, received_at)
        reason = _ineligible(obs, decision_at, deadline)
        attempts.append({"observation_id": obs.id, "eligible": reason is None, "reason": reason})
        if reason is None:
            chosen = obs  # 결정 이후 '첫' 적격 관측만 사용(유리한 가격 골라 쓰기 금지)
            break
        if clock.now() + timedelta(seconds=poll_interval) > deadline:
            break
        clock.sleep(poll_interval)

    if chosen is None:
        return {"result": "expired", "event": append_event(db, exp, "expiry", {
            "order_id": order["order_id"], "reason": "120초 창 안에 적격 KRX 정규장 관측 없음",
            "attempts": attempts}, clock.now(), actor=actor, idempotency_key=f"end:{order['order_id']}")}

    st = replay(db, exp)
    if order["side"] == "buy":
        violations = risk.check_buy(exp, st, risk.BuyPlan(
            code=order["code"], quantity=order["quantity"], price=chosen.price,
            invalidation_price=order["invalidation_price"], sector=order.get("sector"),
            exploration=order.get("exploration", False)), prices_for_nav, cfg)
    else:
        violations = risk.check_sell(st, order["code"], order["quantity"])
    if violations:
        return {"result": "cancelled", "event": append_event(db, exp, "cancel", {
            "order_id": order["order_id"], "reason": "체결가 기준 위험 재검증 실패: " + "; ".join(violations),
            "observation_id": chosen.id}, clock.now(), actor=actor, idempotency_key=f"end:{order['order_id']}")}

    observed_at = chosen.source_time
    fill = {
        "order_id": order["order_id"], "code": order["code"], "side": order["side"],
        "quantity": order["quantity"], "fill_price": chosen.price,
        "cost": trade_cost(order["quantity"] * chosen.price, exp.cost_bps),
        "observation_id": chosen.id, "observedAt": observed_at.isoformat(), "filledAt": observed_at.isoformat(),
        "sourceGrade": "B", "executionEvidenceVersion": "source-krx-observation-v1", "priceType": "immediate",
        "raw_sha256": chosen.raw_sha256, **POLICY,
    }
    try:
        ev = append_event(db, exp, "fill", fill, observed_at, actor=actor, idempotency_key=f"fill:{order['order_id']}")
    except PaperViolation as e:
        return {"result": "blocked", "reason": str(e)}
    return {"result": "filled", "event": ev}


def _ineligible(obs: Observation, decision_at: datetime, deadline: datetime) -> str | None:
    if not obs.eligible:
        return obs.ineligible_reason or "부적격 관측"
    if obs.source_time is None:
        return "원자료 시각 없음"
    if obs.source_time <= decision_at:
        return "결정 이전(또는 같은 시각) 가격 — 소급 사용 금지"
    if obs.source_time > deadline or obs.received_at > deadline:
        return "120초 체결 창 경과"
    if obs.price is None or obs.price <= 0:
        return "가격 없음"
    return None
