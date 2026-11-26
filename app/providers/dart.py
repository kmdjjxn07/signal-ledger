"""
DART Open API — 공시(A등급)와 기업 고유번호 매핑.

⚠ 작성 환경에서 opendart.fss.or.kr 접속이 불가해 실제 응답으로 검증하지 못했다.
상태코드 해석은 DART Open API 문서 기준(000 정상, 013 조회 데이터 없음, 010/011 키 오류, 020 호출 제한).
ETF는 기업이 아니므로 corpCode 목록에 없고, 다른 기업 공시를 ETF에 연결하지 않는다.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from xml.etree import ElementTree

from app.config import get_settings
from app.providers.base import HttpClient, ProviderResult, PStatus

BASE = "https://opendart.fss.or.kr/api"
HOST = ("opendart.fss.or.kr",)

_STATUS_MAP = {
    "000": PStatus.OK, "013": PStatus.EMPTY, "010": PStatus.AUTH_FAILED, "011": PStatus.AUTH_FAILED,
    "012": PStatus.FORBIDDEN, "020": PStatus.RATE_LIMITED, "800": PStatus.NETWORK_ERROR,
}


class DartProvider:
    name = "dart"

    def __init__(self, http: HttpClient | None = None, api_key: str | None = None):
        self.http = http or HttpClient()
        self.api_key = api_key if api_key is not None else get_settings().dart_api_key

    def corp_codes(self) -> ProviderResult:
        """상장사 종목코드(6자리) → DART 고유번호(8자리) 매핑."""
        if not self.api_key:
            return ProviderResult(PStatus.NOT_CONFIGURED, detail="DART_API_KEY 미설정")
        res = self.http.get(f"{BASE}/corpCode.xml", params={"crtfc_key": self.api_key}, allowed_hosts=HOST)
        if not res.ok:
            return res
        body: bytes = res.meta.get("body_bytes") or b""
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as zf:
                xml_bytes = zf.read(zf.namelist()[0])
        except (zipfile.BadZipFile, IndexError):
            # 오류 시 DART는 zip 대신 JSON/XML 메시지를 준다
            text = body[:300].decode("utf-8", "replace")
            status = PStatus.AUTH_FAILED if "010" in text or "011" in text else PStatus.INVALID
            return ProviderResult(status, detail=f"zip이 아닌 응답: {text}", url=res.url)
        try:
            root = ElementTree.fromstring(xml_bytes)
        except ElementTree.ParseError as e:
            return ProviderResult(PStatus.INVALID, detail=f"XML 해석 실패: {e}", url=res.url)
        mapping: dict[str, dict] = {}
        for item in root.iter("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_code = (item.findtext("corp_code") or "").strip()
            if len(stock_code) == 6 and len(corp_code) == 8:
                mapping[stock_code] = {"corp_code": corp_code, "corp_name": (item.findtext("corp_name") or "").strip()}
        if not mapping:
            return ProviderResult(PStatus.EMPTY, detail="상장사 매핑 0건", url=res.url)
        return ProviderResult(PStatus.OK, data=mapping, url=res.url)

    def disclosures(self, corp_code: str, start: date, end: date, page: int = 1) -> ProviderResult:
        if not self.api_key:
            return ProviderResult(PStatus.NOT_CONFIGURED, detail="DART_API_KEY 미설정")
        if len(corp_code) != 8 or not corp_code.isdigit():
            return ProviderResult(PStatus.INVALID, detail="DART 고유번호는 8자리 숫자여야 합니다")
        res = self.http.get(f"{BASE}/list.json", allowed_hosts=HOST, params={
            "crtfc_key": self.api_key, "corp_code": corp_code, "bgn_de": start.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"), "page_no": page, "page_count": 100})
        if not res.ok:
            return res
        try:
            payload = json.loads(res.raw_text or "")
        except ValueError:
            return ProviderResult(PStatus.INVALID, detail="JSON이 아닌 응답", url=res.url)
        code = str(payload.get("status", ""))
        status = _STATUS_MAP.get(code, PStatus.INVALID)
        if status != PStatus.OK:
            return ProviderResult(status, detail=f"DART status {code}: {payload.get('message', '')}", url=res.url,
                                  raw_text=res.raw_text)
        items = []
        for it in payload.get("list", []):
            if it.get("corp_code") != corp_code:
                continue  # 다른 기업 공시가 섞이면 버린다
            items.append({
                "rcept_no": it.get("rcept_no"), "report_nm": (it.get("report_nm") or "").strip(),
                "rcept_dt": it.get("rcept_dt"), "flr_nm": it.get("flr_nm"),
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it.get('rcept_no')}",
            })
        return ProviderResult(PStatus.OK, data=items, url=res.url, raw_text=res.raw_text)
