"""
금액·수량·비율 계산. 부동소수점을 쓰지 않는다.
- 금액: 원 단위 정수
- 가격: 원 단위 정수 (KRX 주식 가격은 원 단위 정수)
- 비율: bp(1/10000) 정수
- 화면용 백분율: Decimal 문자열
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def ceil_div(a: int, b: int) -> int:
    if b <= 0:
        raise ValueError("나눗수는 양수여야 합니다")
    return -((-a) // b)


def trade_cost(notional_krw: int, cost_bps: int) -> int:
    """거래비용 = 거래금액 × bp / 10000, 원 단위 올림(보수적)."""
    if notional_krw < 0 or cost_bps < 0:
        raise ValueError("음수 금액·비용은 허용하지 않습니다")
    return ceil_div(notional_krw * cost_bps, 10000)


def pct_change(new: int, base: int, places: int = 2) -> Decimal:
    """(new/base - 1) × 100 을 Decimal로 계산."""
    if base <= 0:
        raise ValueError("기준값은 양수여야 합니다")
    value = (Decimal(new) / Decimal(base) - Decimal(1)) * Decimal(100)
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def ratio_pct(part: int, whole: int, places: int = 1) -> Decimal | None:
    if whole == 0:
        return None
    return (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def fmt_krw(v: int | None) -> str:
    if v is None:
        return "확인 안 됨"
    return f"{v:,}원"


def fmt_pct(v: Decimal | None, signed: bool = True) -> str:
    if v is None:
        return "확인 안 됨"
    return f"{v:+}%" if signed else f"{v}%"


def parse_krw_int(text: str) -> int:
    """'55,000' 같은 문자열을 정수로. 소수점이 있으면 거부(추측해서 반올림하지 않음)."""
    cleaned = str(text).replace(",", "").strip()
    if not cleaned or not cleaned.lstrip("-").isdigit():
        raise ValueError(f"정수 가격이 아닙니다: {text!r}")
    return int(cleaned)
