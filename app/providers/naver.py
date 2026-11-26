"""
네이버 증권 공개 시세 원본 (B등급 참고 시세 / 모의체결 근거 후보).

이 주소가 항상 동작하거나 필드를 계속 준다고 가정하지 않는다. 검증 기준(필드명·허용값)은
settings 'naver_validation'에 두고, 소유자가 `python -m app.cli probe-naver 005930`으로 실제
응답을 확인한 뒤 confirmed_by_owner=true로 바꾸기 전까지는 어떤 관측도 체결 근거로 쓰지 않는다.
overMarketPriceInfo·integratedPriceInfo(장외·통합 가격)는 절대 가격으로 읽지 않는다.
이용조건 확인 전에는 NAVER_QUOTE_ENABLED=false가 기본이다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.config import get_settings
from app.money import parse_krw_int
from app.providers.base import HttpClient, ProviderResult, PStatus
from app.timeutil import parse_iso_aware

URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
HOST = ("polling.finance.naver.com",)


@dataclass
class ParsedQuote:
    code: str
    price: int | None
    source_time: datetime | None
    market_status: str | None
    session_type: str | None
    exchange_code: str | None
    eligible: bool
    reason: str | None


def validate_quote(requested_code: str, payload: dict, rules: dict) -> ParsedQuote:
    datas = payload.get("datas") if isinstance(payload, dict) else None
    if not isinstance(datas, list) or not datas or not isinstance(datas[0], dict):
        return ParsedQuote(requested_code, None, None, None, None, None, False, "datas 항목 없음")
    d = datas[0]
    reasons: list[str] = []

    code = next((str(d[f]) for f in rules["code_fields"] if f in d and d[f] is not None), None)
    if code != requested_code:
        reasons.append(f"종목코드 불일치(응답 {code})")

    price = None
    raw_price = d.get(rules["price_field"])
    if raw_price is None:
        reasons.append("가격 필드 없음")
    else:
        try:
            price = parse_krw_int(raw_price)
            if price <= 0:
                reasons.append("가격이 0 이하")
        except ValueError:
            reasons.append("가격 형식 오류")

    source_time = None
    raw_time = d.get(rules["time_field"])
    if raw_time is None:
        reasons.append("기준시각 필드 없음")
    else:
        source_time = parse_iso_aware(str(raw_time))
        if source_time is None:
            reasons.append("기준시각에 시간대가 없거나 형식 오류(추측하지 않음)")

    status = d.get(rules["market_status_field"])
    if status not in rules["allowed_market_status"]:
        reasons.append(f"장 상태 부적격({status})")

    session = d.get(rules["session_type_field"])
    if isinstance(session, dict):
        session = session.get("code") or session.get("name")
    if session is None:
        reasons.append("세션 구분 필드 없음")
    elif session not in rules["allowed_session_types"]:
        reasons.append(f"정규장 세션 아님({session})")

    exch = d.get(rules["exchange_type_field"])
    exch_code = exch.get("code") if isinstance(exch, dict) else exch
    if exch_code not in rules["allowed_exchange_codes"]:
        reasons.append(f"거래소 구분 부적격({exch_code})")

    if not rules.get("confirmed_by_owner"):
        reasons.append("검증 기준이 소유자에 의해 확정되지 않음(probe 후 확정 필요)")

    return ParsedQuote(requested_code, price, source_time, status, session, exch_code,
                       eligible=not reasons, reason="; ".join(reasons) or None)


class NaverQuoteProvider:
    name = "naver"

    def __init__(self, http: HttpClient | None = None, enabled: bool | None = None):
        self.http = http or HttpClient()
        self.enabled = get_settings().naver_quote_enabled if enabled is None else enabled

    def fetch(self, code: str) -> ProviderResult:
        if not self.enabled:
            return ProviderResult(PStatus.NOT_CONFIGURED,
                                  detail="NAVER_QUOTE_ENABLED=false (이용조건 확인 후 소유자가 켜야 함)")
        if len(code) != 6 or not code.isalnum():
            return ProviderResult(PStatus.INVALID, detail="6자리 종목코드가 아님")
        res = self.http.get(URL.format(code=code), allowed_hosts=HOST)
        if not res.ok:
            return res
        try:
            payload = json.loads(res.raw_text or "")
        except ValueError:
            return ProviderResult(PStatus.INVALID, detail="JSON이 아닌 응답", url=res.url)
        return ProviderResult(PStatus.OK, data=payload, url=res.url, raw_text=res.raw_text)
