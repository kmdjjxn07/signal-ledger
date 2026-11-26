"""
당일 종목별 투자자 수급.

요건: 종목코드·거래일·확인시각·KRX 정규장 범위·투자자 구분·단위·원출처·등급·원본 해시.
현재 이 요건을 만족한다고 확인된 공급자가 없다. 전일 누계, 범위 불명 추정치, 화면 스크래핑
값으로 대체하지 않는다. 따라서 기본 구현은 '연결 필요'를 반환하고, 모의판단의 필수 요건
'당일 종목별 수급'이 충족되지 않아 신규 주문은 차단된다.

새 공급자를 붙일 때는 FlowProvider를 상속해 fetch()가 아래 형식의 레코드만 내도록 한다:
  {"code", "trading_date", "checked_at"(aware), "scope": "KRX_REGULAR",
   "investor_type", "net_value_krw"(int), "unit": "KRW", "is_provisional": bool}
"""
from __future__ import annotations

from datetime import date

from app.providers.base import ProviderResult, PStatus

REQUIRED_FIELDS = {"code", "trading_date", "checked_at", "scope", "investor_type", "net_value_krw", "unit",
                   "is_provisional"}


class FlowProvider:
    name = "flows-unconfigured"
    grade = "B"

    def fetch(self, code: str, d: date) -> ProviderResult:
        return ProviderResult(PStatus.NOT_CONFIGURED,
                              detail="요건(당일·정규장 범위·확인시각·단위)을 만족하는 수급 공급자가 아직 없음")


def validate_flow_record(rec: dict, code: str, d: date) -> str | None:
    """적격이면 None, 아니면 사유."""
    missing = REQUIRED_FIELDS - set(rec)
    if missing:
        return f"필수 필드 누락: {sorted(missing)}"
    if rec["code"] != code:
        return "종목코드 불일치"
    if rec["trading_date"] != d:
        return "거래일 불일치(전일 자료 대체 금지)"
    if rec["scope"] != "KRX_REGULAR":
        return "KRX 정규장 범위가 아님"
    if rec["unit"] != "KRW" or not isinstance(rec["net_value_krw"], int):
        return "단위·정수 금액 아님"
    if getattr(rec["checked_at"], "tzinfo", None) is None:
        return "확인시각에 시간대 없음"
    return None
