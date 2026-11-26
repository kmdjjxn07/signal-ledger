"""
웹 앱 진입점.

    uvicorn app.main:app --port 8000

모든 화면과 API는 소유자 로그인 후에만 볼 수 있다. 자동 API 문서(/docs)는 끈다(guide/API.md 참조).
"""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.db import session_factory
from app.security import COOKIE_NAME, load_session
from app.web import admin, pages
from app.web.deps import templates

PUBLIC_PREFIXES = ("/login", "/static", "/health", "/favicon.ico")
# 공개 사이트 생성용 앱이 그릴 수 있는 경로(그 밖은 404). 보유종목·관리·로그인·실시간 시세는 제외.
STATIC_ALLOWED = ("/", "/briefing/", "/archive", "/research", "/paper", "/search", "/stock/", "/big-picture",
                  "/glossary", "/status", "/api/export", "/static/")


class PublicViewer:
    """공개 사이트를 그릴 때 쓰는 가상 열람자. DB 계정이 아니며 쓰기 권한이 없다."""
    id = 0
    username = "public"


app = FastAPI(title="시그널 원장", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="static")
app.include_router(pages.router)
app.include_router(admin.router)


def create_static_app() -> FastAPI:
    """공개 사이트(docs/) 생성 전용 앱. 읽기 화면만 있고, 쓰기 경로(admin)는 아예 등록하지 않는다.
    이 앱은 서버로 띄우지 않고 app/site.py가 내부에서만 호출해 HTML을 만든다."""
    sapp = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    sapp.include_router(pages.router)

    @sapp.middleware("http")
    async def static_mw(request: Request, call_next):  # noqa: ANN001, ANN201
        path = request.url.path
        if request.method != "GET" or not (path == "/" or any(path.startswith(p) for p in STATIC_ALLOWED[1:])):
            return JSONResponse({"error": "공개 사이트에 없는 경로"}, status_code=404)
        request.state.user = PublicViewer()
        request.state.session = None
        request.state.static_site = True
        return await call_next(request)

    @sapp.exception_handler(StarletteHTTPException)
    async def static_error(request: Request, exc: StarletteHTTPException):  # noqa: ANN201
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return sapp

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):  # noqa: ANN001, ANN201
    request.state.user = None
    request.state.session = None
    path = request.url.path
    if not path.startswith(PUBLIC_PREFIXES):
        db = session_factory()()
        try:
            loaded = load_session(db, request.cookies.get(COOKIE_NAME))
        finally:
            db.close()
        if loaded is None:
            if path.startswith("/api/"):
                resp = JSONResponse({"error": "로그인이 필요합니다."}, status_code=401)
            else:
                resp = RedirectResponse("/login", status_code=303)
            for k, v in SECURITY_HEADERS.items():
                resp.headers[k] = v
            return resp
        request.state.user, request.state.session = loaded
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if path.startswith("/static"):
        response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):  # noqa: ANN201
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    title = {403: "권한 없음", 404: "찾을 수 없음"}.get(exc.status_code, "오류")
    return templates.TemplateResponse(request, "error.html", {"user": request.state.user, "csrf": "",
                                                              "title": title, "message": exc.detail,
                                                              "code": exc.status_code}, status_code=exc.status_code)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "env": get_settings().env}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():  # noqa: ANN201
    from fastapi.responses import Response
    return Response(status_code=204)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):  # noqa: ANN201
    # 이미 발급한 로그인 토큰이 있으면 재사용한다(다른 탭·자동 요청이 토큰을 바꿔 로그인이 실패하지 않게).
    existing = request.cookies.get("sl_login_csrf", "")
    token = existing if (16 <= len(existing) <= 64 and existing.replace("-", "").replace("_", "").isalnum()) \
        else secrets.token_urlsafe(16)
    resp = templates.TemplateResponse(request, "login.html", {"user": None, "csrf": token, "error": None})
    resp.set_cookie("sl_login_csrf", token, httponly=True, samesite="strict", secure=get_settings().cookie_secure)
    return resp


@app.post("/login")
async def login(request: Request):  # noqa: ANN201
    from app.db import session_scope
    from app.security import (
        authenticate, clear_failures, is_rate_limited, record_failure, start_session,
    )
    from app.services.audit import audit
    form = await request.form()
    client = request.client.host if request.client else "unknown"
    cookie_token = request.cookies.get("sl_login_csrf")
    if not cookie_token or not secrets.compare_digest(cookie_token, str(form.get("csrf_token", ""))):
        return templates.TemplateResponse(request, "login.html", {"user": None, "csrf": "", "error":
                                          "보안 토큰이 맞지 않습니다. 새로고침 후 다시 시도하세요."}, status_code=403)
    if is_rate_limited(client):
        return templates.TemplateResponse(request, "login.html", {"user": None, "csrf": cookie_token, "error":
                                          "로그인 실패가 많아 15분간 잠겼습니다."}, status_code=429)
    with session_scope() as db:
        user = authenticate(db, str(form.get("username", "")), str(form.get("password", "")))
        if user is None:
            record_failure(client)
            audit(db, "anonymous", "login.failed", str(form.get("username", ""))[:50])
            return templates.TemplateResponse(request, "login.html", {"user": None, "csrf": cookie_token,
                                              "error": "아이디 또는 비밀번호가 맞지 않습니다."}, status_code=401)
        clear_failures(client)
        token, _ = start_session(db, user)
        audit(db, f"owner:{user.username}", "login.success")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", secure=get_settings().cookie_secure,
                    max_age=get_settings().session_hours * 3600)
    resp.delete_cookie("sl_login_csrf")
    return resp
