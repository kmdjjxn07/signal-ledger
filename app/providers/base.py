"""
공급자 공통 계층.

모든 공급자는 예외로 작업 전체를 멈추지 않고 ProviderResult를 돌려준다.
상태를 세분화해서 '빈 응답', '미발행', '인증 실패', '접근 거부', '휴장'을 섞지 않는다.
리다이렉트는 따라가지 않는다(비밀키가 다른 도메인으로 전달되는 것을 막기 위함). HTTPS만 허용.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import get_settings


class PStatus(str, Enum):
    OK = "ok"
    EMPTY = "empty"                  # 정상 응답이지만 레코드 없음(원인 미상)
    NOT_PUBLISHED = "not_published"  # 거래일인데 아직 발행 전으로 판단
    CLOSED = "closed"                # 캘린더상 휴장
    AUTH_FAILED = "auth_failed"      # 키 오류·만료
    FORBIDDEN = "forbidden"          # 403 등 접근 거부 (우회하지 않음)
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    INVALID = "invalid_response"     # 형식이 예상과 다름
    NOT_CONFIGURED = "not_configured"  # 키·설정 없음 → 화면에는 '연결 필요'


STATUS_LABEL_KO = {
    PStatus.OK: "정상", PStatus.EMPTY: "빈 응답", PStatus.NOT_PUBLISHED: "미발행",
    PStatus.CLOSED: "휴장", PStatus.AUTH_FAILED: "인증 실패", PStatus.FORBIDDEN: "접근 거부",
    PStatus.RATE_LIMITED: "호출 제한", PStatus.NETWORK_ERROR: "네트워크 오류",
    PStatus.INVALID: "응답 형식 오류", PStatus.NOT_CONFIGURED: "연결 필요",
}


@dataclass
class ProviderResult:
    status: PStatus
    data: Any = None
    http_status: int | None = None
    detail: str = ""
    url: str | None = None
    raw_text: str | None = None
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == PStatus.OK


class HttpClient:
    """테스트에서는 httpx.MockTransport를 주입해 실제 네트워크 없이 검증한다."""

    def __init__(self, transport: httpx.BaseTransport | None = None, timeout: float | None = None):
        self._transport = transport
        self._timeout = timeout or get_settings().http_timeout_sec

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None,
            allowed_hosts: tuple[str, ...] = ()) -> ProviderResult:
        return self._request("GET", url, params=params, headers=headers, allowed_hosts=allowed_hosts)

    def post_json(self, url: str, *, json_body: dict, headers: dict | None = None,
                  allowed_hosts: tuple[str, ...] = ()) -> ProviderResult:
        return self._request("POST", url, json_body=json_body, headers=headers, allowed_hosts=allowed_hosts)

    def _request(self, method: str, url: str, *, params=None, headers=None, json_body=None,  # noqa: ANN001
                 allowed_hosts: tuple[str, ...] = ()) -> ProviderResult:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return ProviderResult(PStatus.INVALID, detail="HTTPS가 아닌 주소는 호출하지 않습니다.", url=url)
        if allowed_hosts and parsed.hostname not in allowed_hosts:
            return ProviderResult(PStatus.INVALID, detail=f"허용되지 않은 호스트: {parsed.hostname}", url=url)
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout, follow_redirects=False) as c:
                resp = c.request(method, url, params=params, headers=headers, json=json_body)
        except httpx.TimeoutException:
            return ProviderResult(PStatus.NETWORK_ERROR, detail="시간 초과", url=url)
        except httpx.HTTPError as e:
            return ProviderResult(PStatus.NETWORK_ERROR, detail=type(e).__name__, url=url)

        public_url = _strip_secret_params(str(resp.request.url))
        if 300 <= resp.status_code < 400:
            return ProviderResult(PStatus.INVALID, http_status=resp.status_code,
                                  detail="리다이렉트 응답 — 따라가지 않음(비밀키 보호)", url=public_url)
        if resp.status_code == 401:
            return ProviderResult(PStatus.AUTH_FAILED, http_status=401, detail="인증 실패", url=public_url)
        if resp.status_code == 403:
            return ProviderResult(PStatus.FORBIDDEN, http_status=403, detail="접근 거부(우회하지 않음)", url=public_url)
        if resp.status_code == 429:
            return ProviderResult(PStatus.RATE_LIMITED, http_status=429, detail="호출 제한", url=public_url)
        if resp.status_code >= 400:
            return ProviderResult(PStatus.INVALID, http_status=resp.status_code,
                                  detail=f"HTTP {resp.status_code}", url=public_url)
        return ProviderResult(PStatus.OK, http_status=resp.status_code, url=public_url, raw_text=resp.text,
                              meta={"content_type": resp.headers.get("content-type", ""), "body_bytes": resp.content})


_SECRET_PARAMS = {"crtfc_key", "auth_key", "api_key", "key", "apikey", "serviceKey"}


def _strip_secret_params(url: str) -> str:
    """로그·DB에 남기는 URL에서 비밀 파라미터를 지운다."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    kept = [p for p in parsed.query.split("&") if p.split("=", 1)[0] not in _SECRET_PARAMS]
    return parsed._replace(query="&".join(kept)).geturl()
