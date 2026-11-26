"""
공개 정적 사이트(GitHub Pages용 _site/ 폴더) 생성. GitHub Actions가 이 폴더를 Pages로 배포한다(저장소에는 커밋하지 않음).

- 읽기 화면만 HTML 파일로 만든다. 로그인·관리·입력 폼·보유종목·실시간 시세는 싣지 않는다.
- 링크는 모두 상대경로(…/index.html)로 바꿔서, 어느 주소(https://아이디.github.io/저장소/)에서도,
  내 컴퓨터에서 파일을 직접 열어도 동작한다.
- 내용이 바뀐 파일만 다시 쓴다(Git 변경분 최소화). 생성 후 금지 요소가 남아 있으면 실패로 처리한다.
"""
from __future__ import annotations

import json
import posixpath
import re
import shutil
from pathlib import Path

from sqlalchemy import select

STATIC_SRC = Path(__file__).parent / "web" / "static"

FORM_RE = re.compile(r"<form\b.*?</form>", re.S | re.I)
ADMIN_LINK_RE = re.compile(r'<a\b[^>]*href="/(?:admin|holdings|logout|login)[^"]*"[^>]*>.*?</a>', re.S | re.I)
HREF_RE = re.compile(r'(href|src)="(/[^"#?]*)(#[^"]*)?"')
FORBIDDEN = ("<form", 'href="/admin', "/holdings", "csrf_token", "data-quote-code", "로그아웃")


def _out_file(path: str) -> str:
    """'/research/005930' → 'research/005930/index.html', '/export.json' → 'export.json'."""
    p = path.strip("/")
    if not p:
        return "index.html"
    if p.startswith("static/") or p.endswith((".json", ".css", ".js", ".html")):
        return p
    return f"{p}/index.html"


def _rewrite_links(html: str, page_file: str) -> str:
    page_dir = posixpath.dirname(page_file) or "."

    def repl(m: re.Match) -> str:
        attr, path, frag = m.group(1), m.group(2), m.group(3) or ""
        target = _out_file(path)
        rel = posixpath.relpath(target, page_dir)
        return f'{attr}="{rel}{frag}"'
    return HREF_RE.sub(repl, html)


def _clean(html: str) -> str:
    html = FORM_RE.sub("", html)
    html = ADMIN_LINK_RE.sub("", html)
    return html


def _write_if_changed(path: Path, data: bytes, changed: list[str], root: Path) -> None:
    if path.exists() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    changed.append(path.relative_to(root).as_posix())


def page_paths(db) -> list[str]:  # noqa: ANN001
    from app.models import Briefing, Experiment
    from app.web.pages import recorded_codes
    paths = ["/", "/archive", "/research", "/paper", "/search", "/big-picture", "/glossary", "/status"]
    paths += [f"/briefing/{i}" for (i,) in db.execute(select(Briefing.id).order_by(Briefing.id))]
    paths += [f"/paper/{i}" for (i,) in db.execute(select(Experiment.id).order_by(Experiment.id))]
    for code in recorded_codes(db):
        paths += [f"/research/{code}", f"/stock/{code}"]
    return paths


def build_site(out_dir: Path) -> dict:
    from fastapi.testclient import TestClient

    from app.db import session_scope
    from app.main import create_static_app

    with session_scope() as db:
        paths = page_paths(db)
    client = TestClient(create_static_app())
    expected: set[str] = set()
    changed: list[str] = []
    problems: list[str] = []
    for path in paths:
        r = client.get(path)
        if r.status_code != 200:
            problems.append(f"{path}: HTTP {r.status_code}")
            continue
        rel = _out_file(path)
        html = _rewrite_links(_clean(r.text), rel)
        for bad in FORBIDDEN:
            if bad in html:
                problems.append(f"{path}: 공개 금지 요소 '{bad}' 발견")
        expected.add(rel)
        _write_if_changed(out_dir / rel, html.encode("utf-8"), changed, out_dir)

    # 호환 JSON (갱신 시각처럼 매번 바뀌는 값은 빼서 불필요한 커밋을 줄인다)
    r = client.get("/api/export")
    if r.status_code == 200:
        data = r.json()
        data.pop("updatedAt", None)
        body = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")
        _write_if_changed(out_dir / "export.json", body, changed, out_dir)
        expected.add("export.json")
        if "holdings" in body.decode("utf-8").lower():
            problems.append("export.json에 holdings 키가 있습니다")
    else:
        problems.append(f"/api/export: HTTP {r.status_code}")

    for name in ("app.css", "app.js"):
        _write_if_changed(out_dir / "static" / name, (STATIC_SRC / name).read_bytes(), changed, out_dir)
        expected.add(f"static/{name}")

    not_found = _not_found_page()
    _write_if_changed(out_dir / "404.html", not_found.encode("utf-8"), changed, out_dir)
    _write_if_changed(out_dir / ".nojekyll", b"", changed, out_dir)
    expected |= {"404.html", ".nojekyll"}

    removed = []
    if out_dir.exists():
        for p in sorted(out_dir.rglob("*")):
            if p.is_file() and p.relative_to(out_dir).as_posix() not in expected:
                p.unlink()
                removed.append(p.relative_to(out_dir).as_posix())
        for d in sorted((d for d in out_dir.rglob("*") if d.is_dir()), key=lambda x: len(x.parts), reverse=True):
            if not any(d.iterdir()):
                shutil.rmtree(d)
    if problems:
        raise RuntimeError("공개 사이트 생성 검사 실패: " + "; ".join(problems[:10]))
    return {"pages": len(expected), "changed": len(changed), "removed": len(removed)}


def _not_found_page() -> str:
    # GitHub Pages는 404.html을 어느 깊이에서나 보여 주므로 링크는 절대 경로 없이 첫 화면 안내만 둔다.
    return ("<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>찾을 수 없음 · 시그널 원장</title>"
            "<style>body{font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;color:#222;"
            "background:#fafaf7}</style></head><body><h1>페이지를 찾을 수 없습니다</h1>"
            "<p>공개 사이트에는 기록이 있는 종목과 공개 기록만 실려 있습니다. 주소 끝의 경로를 지우고 첫 화면으로 이동하세요.</p>"
            "</body></html>")
