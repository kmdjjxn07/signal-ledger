from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.money import fmt_krw, fmt_pct
from app.providers.base import STATUS_LABEL_KO, PStatus
from app.security import check_csrf
from app.timeutil import fmt_kst, to_kst

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _fmt_index(v: int | None) -> str:
    if v is None:
        return "확인 안 됨"
    return f"{v // 100:,}.{v % 100:02d}"


def _price(row) -> str:  # noqa: ANN001
    if row is None:
        return "확인 안 됨"
    return _fmt_index(row.close) if row.scale == 100 else fmt_krw(row.close)


def _status_ko(s: str | None) -> str:
    if s is None:
        return "—"
    try:
        return STATUS_LABEL_KO[PStatus(s)]
    except ValueError:
        return {"ok": "정상", "missing": "누락", "partial": "일부 확인", "skipped": "건너뜀",
                "calendar_unknown": "캘린더 미확인", "published": "발행됨", "scheduled": "발행 예정", "draft": "초안",
                "withheld": "발행 보류", "succeeded": "성공", "failed": "실패", "running": "실행 중", "pending": "대기",
                "hit": "적중", "miss": "실패", "flat": "보합", "ops_error": "운영 오류"}.get(s, s)


def _date(d: date | datetime | None) -> str:
    if d is None:
        return "—"
    if isinstance(d, datetime):
        return fmt_kst(d)
    return d.isoformat()


def _signed(v: Decimal | str | None) -> str:
    if v is None:
        return "확인 안 됨"
    return fmt_pct(Decimal(str(v)))


templates.env.filters.update({"kst": fmt_kst, "krw": fmt_krw, "pct": _signed, "idx": _fmt_index, "price": _price,
                              "status_ko": _status_ko, "d": _date, "to_kst": to_kst})


def page(request: Request, name: str, **ctx):  # noqa: ANN201
    sess = getattr(request.state, "session", None)
    return templates.TemplateResponse(request, name, {"user": getattr(request.state, "user", None),
                                                      "csrf": sess.csrf_token if sess else "",
                                                      "static_site": bool(getattr(request.state, "static_site", False)),
                                                      **ctx})


def _editor_mode() -> bool:
    from app.config import get_settings
    return get_settings().is_editor


templates.env.globals["editor_mode"] = _editor_mode


async def require_csrf(request: Request) -> None:
    """모든 쓰기 요청: 같은 출처(Origin/Referer) + 세션 CSRF 토큰 확인."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin:
        o = urlparse(origin)
        if o.netloc and o.netloc != request.url.netloc:
            raise HTTPException(403, "다른 출처에서 온 쓰기 요청은 거부합니다.")
    form = await request.form()
    sess = getattr(request.state, "session", None)
    if sess is None or not check_csrf(sess, form.get("csrf_token")):
        raise HTTPException(403, "보안 토큰(CSRF)이 맞지 않습니다. 페이지를 새로고침한 뒤 다시 시도하세요.")


def actor(request: Request) -> str:
    return f"owner:{request.state.user.username}"
