"""
위험 검증기(서버 코드). AI 제안과 무관하게 이 검증을 통과해야만 주문·체결된다.

검증 항목: 정수 수량, 현금(비용 포함), 최대 보유 종목 수, 종목당 최대 비중, 섹터 집중,
탐색 비중(NAV 10~15%, 동시에 최대 1개), 무효화 손실+왕복 비용 ≤ NAV 1%, 차입·공매도 금지.
NAV는 장중 참고가가 아니라 '검증 시점에 쓰는 가격'으로 계산하며, 이 값은 설계 점검용이지
공식 NAV가 아니다(공식 NAV는 정산 시 KRX 종가로 계산).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import Experiment
from app.money import trade_cost
from app.paper.ledger import State


@dataclass
class BuyPlan:
    code: str
    quantity: int
    price: int
    invalidation_price: int
    sector: str | None
    exploration: bool


def check_buy(exp: Experiment, st: State, plan: BuyPlan, prices: dict[str, int], cfg: dict) -> list[str]:
    v: list[str] = []
    if not isinstance(plan.quantity, int) or plan.quantity <= 0:
        v.append("수량은 1 이상의 정수여야 합니다.")
        return v
    if plan.price <= 0:
        v.append("가격이 올바르지 않습니다.")
        return v
    if not (0 < plan.invalidation_price < plan.price):
        v.append("매수 무효화 가격은 0보다 크고 매수가보다 낮아야 합니다.")
        return v
    marks = {**prices, plan.code: plan.price}
    nav = st.nav(marks)
    if nav is None or nav <= 0:
        v.append("보유 종목 가격이 없어 NAV를 계산할 수 없습니다.")
        return v
    notional = plan.quantity * plan.price
    buy_cost = trade_cost(notional, exp.cost_bps)
    if notional + buy_cost > st.cash:
        v.append(f"현금 부족(필요 {notional + buy_cost:,}원, 보유 {st.cash:,}원). 차입 금지.")
    held_codes = {p.code for p in st.held}
    if plan.code not in held_codes and len(held_codes) >= exp.max_positions:
        v.append(f"최대 보유 종목 수({exp.max_positions}) 초과")
    existing = st.positions.get(plan.code)
    after_value = (existing.quantity if existing else 0) * plan.price + notional
    if after_value * 10000 > nav * exp.max_position_bp:
        v.append(f"종목당 최대 비중 {exp.max_position_bp / 100:.1f}% 초과")
    if plan.sector:
        same = sum(p.quantity * marks.get(p.code, 0) for p in st.held if p.sector == plan.sector and p.code != plan.code)
        if (same + after_value) * 10000 > nav * cfg.get("max_sector_bp", 5000):
            v.append("섹터 집중 한도 초과")
    if plan.exploration:
        others = [p for p in st.held if p.exploration and p.code != plan.code]
        if others:
            v.append("탐색 포지션은 동시에 1개까지입니다.")
        lo, hi = cfg["exploration_min_bp"], cfg["exploration_max_bp"]
        if not (nav * lo <= after_value * 10000 <= nav * hi):
            v.append(f"탐색 비중은 NAV {lo / 100:.0f}~{hi / 100:.0f}% 범위여야 합니다.")
    sell_cost_at_stop = trade_cost(plan.quantity * plan.invalidation_price, exp.cost_bps)
    loss = (plan.price - plan.invalidation_price) * plan.quantity + buy_cost + sell_cost_at_stop
    if loss * 10000 > nav * cfg["max_loss_bp_of_nav"]:
        v.append(f"무효화 손실+비용 {loss:,}원이 NAV {cfg['max_loss_bp_of_nav'] / 100:.1f}%를 넘습니다.")
    return v


def size_buy(exp: Experiment, st: State, ref_price: int, invalidation_price: int, target_bp: int,
             prices: dict[str, int], cfg: dict) -> int:
    """목표 비중과 손실설계(NAV 1%)를 동시에 만족하는 최대 정수 수량(보수적으로 비용 포함)."""
    nav = st.nav({**prices})
    if nav is None or ref_price <= invalidation_price or ref_price <= 0:
        return 0
    by_weight = (nav * target_bp) // (10000 * ref_price)
    per_share_risk = ref_price - invalidation_price
    budget = nav * cfg["max_loss_bp_of_nav"] // 10000
    qty = min(by_weight, budget // per_share_risk)
    while qty > 0:
        loss = (per_share_risk * qty + trade_cost(qty * ref_price, exp.cost_bps)
                + trade_cost(qty * invalidation_price, exp.cost_bps))
        cash_need = qty * ref_price + trade_cost(qty * ref_price, exp.cost_bps)
        if loss * 10000 <= nav * cfg["max_loss_bp_of_nav"] and cash_need <= st.cash:
            return int(qty)
        qty -= 1
    return 0


def check_sell(st: State, code: str, quantity: int) -> list[str]:
    pos = st.positions.get(code)
    if not isinstance(quantity, int) or quantity <= 0:
        return ["수량은 1 이상의 정수여야 합니다."]
    if pos is None or pos.quantity < quantity:
        return ["보유 수량을 넘는 매도(공매도)는 금지입니다."]
    return []
