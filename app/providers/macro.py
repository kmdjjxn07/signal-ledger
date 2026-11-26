"""
해외 지수·금리 등 거시 자료.

FRED(미국 세인트루이스 연준 데이터) 어댑터를 우선 구현했다. FRED는 원 발표기관 자료를
재배포하는 데이터 제공처이므로 B등급으로 기록한다. 시리즈의 정확한 의미를 라벨에 남겨
서로 다른 지표가 섞이지 않게 한다.

원·달러 환율은 '서울 외환시장 종가', '은행 고시환율', 'NDF'가 서로 다른 값이다. 이를 구분해
제공하는 공급자(예: 한국은행 ECOS)를 아직 연결하지 않았으므로 환율은 '연결 필요'로 둔다.
FRED의 DEXKOUS(뉴욕 정오 매입률)를 서울 환율처럼 표시하지 않는다.
"""
from __future__ import annotations

import json
from datetime import date

from app.config import get_settings
from app.providers.base import HttpClient, ProviderResult, PStatus

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
HOST = ("api.stlouisfed.org",)

FRED_SERIES = {
    "DGS10": "미국 10년 국채 수익률(%, 일별, FRED DGS10)",
    "DGS2": "미국 2년 국채 수익률(%, 일별, FRED DGS2)",
    "SP500": "S&P 500 지수(FRED SP500, 종가)",
    "NASDAQCOM": "나스닥 종합지수(FRED NASDAQCOM, 종가)",
    "DCOILWTICO": "WTI 원유 현물가격(달러/배럴, FRED DCOILWTICO)",
}


class FredProvider:
    name = "fred"
    grade = "B"

    def __init__(self, http: HttpClient | None = None, api_key: str | None = None):
        self.http = http or HttpClient()
        self.api_key = api_key if api_key is not None else get_settings().fred_api_key

    def latest(self, series_id: str, as_of: date) -> ProviderResult:
        if not self.api_key:
            return ProviderResult(PStatus.NOT_CONFIGURED, detail="FRED_API_KEY 미설정")
        if series_id not in FRED_SERIES:
            return ProviderResult(PStatus.INVALID, detail="등록되지 않은 시리즈")
        res = self.http.get(FRED_URL, allowed_hosts=HOST, params={
            "series_id": series_id, "api_key": self.api_key, "file_type": "json",
            "observation_end": as_of.isoformat(), "sort_order": "desc", "limit": 5})
        if not res.ok:
            return res
        try:
            obs = json.loads(res.raw_text or "").get("observations", [])
        except (ValueError, AttributeError):
            return ProviderResult(PStatus.INVALID, detail="JSON 형식 오류", url=res.url)
        # FRED는 값이 없는 날을 "." 로 준다 — 그런 날은 건너뛰되 날짜를 바꿔치기하지 않고 실제 날짜를 함께 반환
        for o in obs:
            if o.get("value") not in (None, ".", ""):
                return ProviderResult(PStatus.OK, url=res.url, raw_text=res.raw_text, data={
                    "series_id": series_id, "label": FRED_SERIES[series_id], "value": o["value"],
                    "observation_date": o.get("date")})
        return ProviderResult(PStatus.EMPTY, detail="최근 관측값 없음", url=res.url)


class FxProvider:
    name = "fx-unconfigured"

    def seoul_close(self, d: date) -> ProviderResult:
        return ProviderResult(PStatus.NOT_CONFIGURED,
                              detail="서울 외환시장 종가 공급자(예: 한국은행 ECOS) 미연결 — 다른 환율로 대체하지 않음")
