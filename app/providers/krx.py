"""
KRX 정보데이터시스템 Open API — 공식 일별 가격(A등급).

⚠ 이 코드는 작성 환경에서 KRX에 접속할 수 없어 실제 응답으로 검증하지 못했다.
엔드포인트 경로·필드명은 KRX Open API 문서 기준으로 작성했지만, 운영 전에
`python -m app.cli probe-krx --date YYYY-MM-DD`로 실제 응답을 확인해야 한다.
필드가 예상과 다르면 추측하지 않고 invalid_response로 기록한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.config import get_settings
from app.money import parse_krw_int
from app.providers.base import HttpClient, ProviderResult, PStatus

BASE = "https://data-dbg.krx.co.kr/svc/apis"
HOST = ("data-dbg.krx.co.kr",)

STOCK_ENDPOINTS = {"KOSPI": "/sto/stk_bydd_trd", "KOSDAQ": "/sto/ksq_bydd_trd"}
INDEX_ENDPOINTS = {"KOSPI": "/idx/kospi_dd_trd", "KOSDAQ": "/idx/kosdaq_dd_trd"}
# 지수 시리즈 가운데 대표 지수 이름 (응답의 IDX_NM과 정확히 일치해야 저장)
INDEX_NAMES = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}


@dataclass
class DailyRow:
    code: str
    name: str
    market: str
    trading_date: date
    close: int
    open: int | None
    high: int | None
    low: int | None
    volume: int | None
    trading_value: int | None
    market_cap: int | None
    scale: int  # 주식 1, 지수 100(소수 둘째 자리를 정수로)
    raw: dict


def _short_code(isu_cd: str) -> str | None:
    isu_cd = (isu_cd or "").strip()
    if len(isu_cd) == 6 and isu_cd.isalnum():
        return isu_cd
    if len(isu_cd) == 12 and isu_cd.startswith("KR"):
        return isu_cd[3:9]
    return None


def _opt_int(row: dict, key: str) -> int | None:
    v = row.get(key)
    if v in (None, "", "-"):
        return None
    return parse_krw_int(v)


def _index_value(text: str) -> int:
    """'2,650.32' → 265032 (×100). 소수 셋째 자리 이상이 오면 거부."""
    s = str(text).replace(",", "").strip()
    whole, _, frac = s.partition(".")
    if not whole.isdigit() or (frac and (not frac.isdigit() or len(frac) > 2)):
        raise ValueError(f"지수 값 형식 오류: {text!r}")
    return int(whole) * 100 + (int(frac.ljust(2, "0")) if frac else 0)


class KrxDailyProvider:
    name = "krx"

    def __init__(self, http: HttpClient | None = None, api_key: str | None = None):
        self.http = http or HttpClient()
        self.api_key = api_key if api_key is not None else get_settings().krx_api_key

    def _call(self, path: str, d: date) -> ProviderResult:
        if not self.api_key:
            return ProviderResult(PStatus.NOT_CONFIGURED, detail="KRX_API_KEY 미설정")
        res = self.http.get(BASE + path, params={"basDd": d.strftime("%Y%m%d")},
                            headers={"AUTH_KEY": self.api_key}, allowed_hosts=HOST)
        if not res.ok:
            return res
        try:
            import json
            payload = json.loads(res.raw_text or "")
        except ValueError:
            return ProviderResult(PStatus.INVALID, detail="JSON이 아닌 응답", url=res.url, raw_text=res.raw_text)
        if not isinstance(payload, dict) or "OutBlock_1" not in payload:
            # 인증 오류가 200 + 메시지로 오는 경우를 대비해 원문 일부만 기록(키는 응답에 없음)
            msg = str(payload)[:200]
            status = PStatus.AUTH_FAILED if "auth" in msg.lower() or "인증" in msg else PStatus.INVALID
            return ProviderResult(status, detail=f"예상 형식 아님: {msg}", url=res.url, raw_text=res.raw_text)
        rows = payload.get("OutBlock_1") or []
        if not rows:
            return ProviderResult(PStatus.EMPTY, data=[], url=res.url, raw_text=res.raw_text)
        return ProviderResult(PStatus.OK, data=rows, url=res.url, raw_text=res.raw_text)

    def stock_daily(self, market: str, d: date) -> ProviderResult:
        res = self._call(STOCK_ENDPOINTS[market], d)
        if not res.ok:
            return res
        parsed, bad = [], 0
        for row in res.data:
            try:
                if row.get("BAS_DD") != d.strftime("%Y%m%d"):
                    bad += 1
                    continue
                code = _short_code(row.get("ISU_CD", ""))
                if code is None:
                    bad += 1
                    continue
                parsed.append(DailyRow(
                    code=code, name=row.get("ISU_NM", "").strip(), market=market, trading_date=d,
                    close=parse_krw_int(row["TDD_CLSPRC"]), open=_opt_int(row, "TDD_OPNPRC"),
                    high=_opt_int(row, "TDD_HGPRC"), low=_opt_int(row, "TDD_LWPRC"),
                    volume=_opt_int(row, "ACC_TRDVOL"), trading_value=_opt_int(row, "ACC_TRDVAL"),
                    market_cap=_opt_int(row, "MKTCAP"), scale=1, raw=row))
            except (KeyError, ValueError):
                bad += 1
        if not parsed:
            return ProviderResult(PStatus.INVALID, detail=f"해석 가능한 행 없음(오류 {bad}건)", url=res.url,
                                  raw_text=res.raw_text)
        return ProviderResult(PStatus.OK, data=parsed, url=res.url, raw_text=res.raw_text,
                              detail=f"해석 불가 행 {bad}건 제외" if bad else "")

    def index_daily(self, market: str, d: date) -> ProviderResult:
        res = self._call(INDEX_ENDPOINTS[market], d)
        if not res.ok:
            return res
        target = INDEX_NAMES[market]
        for row in res.data:
            if row.get("IDX_NM", "").strip() == target and row.get("BAS_DD") == d.strftime("%Y%m%d"):
                try:
                    return ProviderResult(PStatus.OK, url=res.url, raw_text=res.raw_text, data=DailyRow(
                        code=f"IDX:{market}", name=target, market="INDEX", trading_date=d,
                        close=_index_value(row["CLSPRC_IDX"]),
                        open=_index_value(row["OPNPRC_IDX"]) if row.get("OPNPRC_IDX") else None,
                        high=_index_value(row["HGPRC_IDX"]) if row.get("HGPRC_IDX") else None,
                        low=_index_value(row["LWPRC_IDX"]) if row.get("LWPRC_IDX") else None,
                        volume=_opt_int(row, "ACC_TRDVOL"), trading_value=_opt_int(row, "ACC_TRDVAL"),
                        market_cap=_opt_int(row, "MKTCAP"), scale=100, raw=row))
                except (KeyError, ValueError) as e:
                    return ProviderResult(PStatus.INVALID, detail=f"지수 행 해석 실패: {e}", url=res.url)
        return ProviderResult(PStatus.INVALID, detail=f"'{target}' 지수 행을 찾지 못함", url=res.url,
                              raw_text=res.raw_text)
