"""
공개 모드(GitHub Actions + 공개 저장소) 검증.
- 공개 상태 파일에 로그인 정보·보유종목·원본 응답이 들어가지 않는가
- 상태 파일 왕복(저장→복원)이 기록과 해시 체인을 보존하는가, 같은 DB면 같은 파일이 나오는가
- 제출 파일(inbox)이 파일 단위로 전부 반영/전부 거부되는가, ref: 출처 참조, 허용되지 않은 명령 거부
- 편집기 모드: 화면 입력이 DB를 바꾸지 않고 제출 파일로만 저장되는가
- 공개 사이트에 폼·관리 화면·보유종목·비밀값이 없는가
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from tests.conftest import add_instrument, add_price, add_source

D = date(2026, 11, 24)
KEY = "x" * 43 + "="  # 시험용 Fernet 열쇠(32바이트 base64)


def _db_file() -> Path:
    from app.config import get_settings
    return Path(get_settings().database_url.split("///", 1)[1])


def _seed_private_and_public(db):
    from app.security import create_owner
    from app.services import holdings as holdings_svc
    from app.services.sources import store_raw
    add_instrument(db, "005930", "관찰전자")
    add_instrument(db, "000660", "비관찰반도체")
    add_instrument(db, "IDX:KOSPI", "코스피", market="INDEX")
    add_price(db, "005930", date(2026, 11, 23), 70_000)
    add_price(db, "000660", date(2026, 11, 23), 150_000)
    add_price(db, "IDX:KOSPI", date(2026, 11, 23), 250_000, scale=100)
    create_owner(db, "owner", "correct-horse-battery")
    h = holdings_svc.add_holding(db, "000660", None, "owner:t")
    holdings_svc.record_transaction(db, h, trade_date=date(2026, 11, 20), side="buy", quantity=3, unit_price=140_000,
                                    note=None, actor="owner:t")
    store_raw(db, "krx", "https://example.test/raw", '{"secret_body": "원본 응답"}')
    from app.services.research import submit_research
    from tests.conftest import research_items
    submit_research(db, "005930", {"items": research_items(db), "liquidity_ok": True, "red_flags": [],
                                   "next_checks": []}, "owner:t")
    db.commit()


def test_state_dump_excludes_private_rows_and_roundtrips(calendar_2026, tmp_path):
    from app.state import dump, restore, write_state
    db = calendar_2026
    _seed_private_and_public(db)
    files = dump(_db_file())
    text = "\n".join(files.values())
    for table in ("users", "user_sessions", "holdings", "holding_transactions", "raw_documents", "job_locks"):
        assert f'INSERT INTO "{table}"' not in text, table
        assert f"CREATE TABLE {table}" in files["ledger.sql"], table  # 스키마는 남김
    assert "correct-horse" not in text and "scrypt" not in text and "원본 응답" not in text
    prices = files.get("tables/official_prices/2026-11.sql", "")
    assert "'005930'" in prices and "'IDX:KOSPI'" in prices
    assert "'000660'" not in prices  # 기록이 없는 종목(=내 보유종목) 가격은 공개 상태에 남기지 않음

    state = tmp_path / "state"
    first = write_state(_db_file(), state, key=KEY)
    assert "ledger.sql" in first["changed"]
    assert write_state(_db_file(), state, key=KEY)["changed"] == []  # 같은 DB → 같은 파일(결정적)

    restored = tmp_path / "restored.db"
    restore(state, restored, KEY)
    con = sqlite3.connect(restored)
    try:
        assert con.execute("SELECT count(*) FROM users").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM holdings").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM research_versions").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM sources").fetchone()[0] == \
            db.execute(__import__("sqlalchemy").text("SELECT count(*) FROM sources")).scalar()
    finally:
        con.close()


def test_scrub_removes_secret_values(calendar_2026, tmp_path):
    from app.services.audit import audit
    from app.state import write_state
    db = calendar_2026
    audit(db, "job:x", "provider.error", None, {"message": "failed with key ABCDEF123456 in url"})
    db.commit()
    write_state(_db_file(), tmp_path / "s", ["ABCDEF123456"])
    body = "\n".join(p.read_text(encoding="utf-8") for p in (tmp_path / "s").rglob("*.sql"))
    assert "ABCDEF123456" not in body and "[비밀값 가림]" in body


def test_paper_chain_survives_state_roundtrip(calendar_2026, tmp_path):
    from app import db as dbmod
    from app.paper.ledger import append_event, verify_chain
    from app.paper.lifecycle import register
    from app.state import restore, write_state
    db = calendar_2026
    exp = register(db, "왕복 실험", {"initial_capital": 1_000_000, "duration_trading_days": 5, "max_positions": 3,
                                    "max_position_bp": 3500, "cost_bps": 15}, "owner:t")
    db.commit()
    append_event(db, exp, "ops_error", {"message": "합성 운영 오류"}, datetime(2026, 11, 24, 1, 0), actor="server:engine",
                 idempotency_key="ops:test-1")
    db.commit()
    assert verify_chain(db, exp.id)[0]
    write_state(_db_file(), tmp_path / "state")
    restored = tmp_path / "r.db"
    restore(tmp_path / "state", restored)
    dbmod.configure(f"sqlite:///{restored.as_posix()}")
    s = dbmod.session_factory()()
    try:
        ok, msg = verify_chain(s, exp.id)
        assert ok, msg
    finally:
        s.close()


# ---------------------------------------------------------------- 제출 파일

def _write_inbox(inbox: Path, name: str, commands: list[dict]) -> None:
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_text(json.dumps({"commands": commands}, ensure_ascii=False), encoding="utf-8")


def test_inbox_atomic_per_file_with_ref_sources(calendar_2026, tmp_path, monkeypatch):
    from app.ci import process_inbox
    from app.models import MacroRevision, Source
    inbox = tmp_path / "inbox"
    monkeypatch.setenv("SL_INBOX_DIR", str(inbox))
    db = calendar_2026
    src = {"url": "https://example.test/cpi", "title": "물가 발표", "provider": "통계청", "grade": "A",
           "checked_at": "2026-11-24T09:00"}
    macro = {"quarter_id": "2026Q4", "data_as_of": "2026-11-24", "reason": "최초", "facts": "f",
             "interpretation": "i", "risks": "r", "next_checks": "n"}
    _write_inbox(inbox, "20261124-01-ok.json", [
        {"kind": "source.add", "payload": {**src, "ref": "cpi-nov"}},
        {"kind": "macro.revision", "payload": {**macro, "source_ids": "ref:cpi-nov"}}])
    _write_inbox(inbox, "20261124-02-bad.json", [
        {"kind": "source.add", "payload": {**src, "url": "https://example.test/x", "ref": "should-not-exist"}},
        {"kind": "calendar.exception", "payload": {"day": "2026-12-24", "kind": "closure",
                                                   "source_url": "http://not-https.test"}}])
    _write_inbox(inbox, "20261124-03-holding.json", [{"kind": "holding.add", "payload": {"code": "005930"}}])
    (inbox / "20261124-04-broken.json").write_text("{not json", encoding="utf-8")

    results = process_inbox(inbox)
    assert [r["ok"] for r in results] == [True, False, False, False]
    assert "공지 URL" in results[1]["error"]
    assert "허용되지 않은" in results[2]["error"]
    db.expire_all()
    from sqlalchemy import select
    assert db.scalar(select(Source).where(Source.ref_key == "cpi-nov")) is not None
    assert db.scalar(select(Source).where(Source.ref_key == "should-not-exist")) is None  # 파일 전체 거부
    assert db.scalar(select(MacroRevision)) is not None
    assert list(inbox.glob("*.json")) == []  # 모두 처리 폴더로 이동
    assert len(list((inbox / "processed").glob("*.result.json"))) == 4

    # 같은 ref 이름 재사용은 거부
    _write_inbox(inbox, "20261124-05-dup.json", [{"kind": "source.add", "payload": {**src, "ref": "cpi-nov"}}])
    assert process_inbox(inbox)[0]["ok"] is False


def test_unknown_ref_rejected(calendar_2026):
    from app.commands import CommandError, resolve_source_ids
    with pytest.raises(CommandError):
        resolve_source_ids(calendar_2026, "ref:none")
    with pytest.raises(CommandError):
        resolve_source_ids(calendar_2026, "abc")
    s = add_source(calendar_2026)
    assert resolve_source_ids(calendar_2026, f"{s.id}") == [s.id]


# ---------------------------------------------------------------- 편집기 모드

@pytest.fixture()
def editor_client(calendar_2026, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.security import create_owner
    monkeypatch.setenv("SL_MODE", "editor")
    monkeypatch.setenv("SL_INBOX_DIR", str(tmp_path / "inbox"))
    create_owner(calendar_2026, "owner", "correct-horse-battery")
    calendar_2026.commit()
    return TestClient(app, base_url="http://testserver")


def test_editor_mode_queues_instead_of_writing(editor_client, calendar_2026, tmp_path):
    from sqlalchemy import func, select

    from app.models import Experiment, Source
    from tests.test_jobs_web_portability import login
    csrf = login(editor_client)
    r = editor_client.post("/admin/sources", data={"csrf_token": csrf, "url": "https://example.test/a", "title": "t",
                                                   "provider": "p", "grade": "A", "checked_at": "2026-11-24T09:00",
                                                   "ref": "a1"}, follow_redirects=False)
    assert r.status_code == 303 and "/admin/inbox" in r.headers["location"]
    # 대기 중인 제출 파일의 ref를 다음 입력의 미리 검사에서 쓸 수 있다
    r = editor_client.post("/admin/macro", data={"csrf_token": csrf, "quarter_id": "2026Q4", "data_as_of": "2026-11-24",
                                                 "reason": "최초", "facts": "f", "interpretation": "i", "risks": "r",
                                                 "next_checks": "n", "source_ids": "ref:a1"}, follow_redirects=False)
    assert "/admin/inbox" in r.headers["location"], r.headers["location"]
    # 미리 검사 실패 → 제출 파일을 만들지 않음
    r = editor_client.post("/admin/experiments", data={"csrf_token": csrf, "name": "", "initial_capital": "x"},
                           follow_redirects=False)
    assert "ok=0" in r.headers["location"]
    calendar_2026.expire_all()
    assert calendar_2026.scalar(select(func.count()).select_from(Source)) == 0
    assert calendar_2026.scalar(select(func.count()).select_from(Experiment)) == 0
    files = sorted((tmp_path / "inbox").glob("*.json"))
    assert len(files) == 2
    assert json.loads(files[0].read_text(encoding="utf-8"))["commands"][0]["kind"] == "source.add"
    page = editor_client.get("/admin/inbox")
    assert page.status_code == 200 and "source.add" in page.text and "편집기 모드" in page.text
    # 공개 원장을 바꾸는 작업은 편집기 모드에서 막힌다
    r = editor_client.post("/admin/jobs/run", data={"csrf_token": csrf, "job": "close"}, follow_redirects=False)
    assert "ok=0" in r.headers["location"]


def test_direct_mode_still_applies(calendar_2026, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.main import app
    from app.models import Source
    from app.security import create_owner
    from tests.test_jobs_web_portability import login
    monkeypatch.delenv("SL_MODE", raising=False)
    create_owner(calendar_2026, "owner", "correct-horse-battery")
    calendar_2026.commit()
    c = TestClient(app, base_url="http://testserver")
    csrf = login(c)
    r = c.post("/admin/sources", data={"csrf_token": csrf, "url": "https://example.test/a", "title": "t", "provider": "p",
                                       "grade": "B", "checked_at": "2026-11-24T09:00", "ref": "d1"},
               follow_redirects=False)
    assert "ok=1" in r.headers["location"]
    calendar_2026.expire_all()
    assert calendar_2026.scalar(select(Source).where(Source.ref_key == "d1")).grade == "B"


# ---------------------------------------------------------------- 공개 사이트

def test_static_site_has_no_private_or_write_elements(calendar_2026, tmp_path, monkeypatch):
    from app.site import build_site
    monkeypatch.setenv("KRX_API_KEY", "SUPER-SECRET-KRX")
    _seed_private_and_public(calendar_2026)
    out = tmp_path / "site"
    rep = build_site(out)
    assert rep["pages"] >= 10
    assert (out / "research" / "005930" / "index.html").exists()
    assert (out / "stock" / "005930" / "index.html").exists()
    assert not (out / "stock" / "000660").exists()  # 보유종목만 있는 종목은 화면을 만들지 않음
    assert not (out / "holdings").exists() and not (out / "admin").exists()
    for f in out.rglob("*.html"):
        html = f.read_text(encoding="utf-8")
        for bad in ("<form", "/admin", "/holdings", "csrf_token", "SUPER-SECRET-KRX", "비관찰반도체", "로그아웃"):
            assert bad not in html, (f, bad)
        assert re.search(r'href="/(?!/)', html) is None, f  # 절대경로 링크 없음(어느 주소에서도 동작)
        if f.name == "index.html":
            assert "투자 권유가 아니며" in html  # 모든 화면 면책 문구
    export = json.loads((out / "export.json").read_text(encoding="utf-8"))
    assert "updatedAt" not in export and "holdings" not in json.dumps(export).lower()
    # 두 번째 생성은 바뀐 파일이 없어야 한다(불필요한 배포·커밋 방지)
    assert build_site(out)["changed"] == 0


def test_static_app_refuses_private_paths(calendar_2026):
    from fastapi.testclient import TestClient

    from app.main import create_static_app
    c = TestClient(create_static_app())
    for path in ("/holdings", "/admin", "/admin/inbox", "/api/quote/005930", "/login", "/logout"):
        assert c.get(path).status_code == 404, path
    assert c.post("/admin/sources").status_code == 404
    assert c.get("/status").status_code == 200


# ---------------------------------------------------------------- 공급자 자료 암호화(KRX 약관: 제3자 제공 금지)

def test_provider_tables_encrypted_in_public_state(calendar_2026, tmp_path):
    from app.state import StateKeyError, restore, write_state
    db = calendar_2026
    _seed_private_and_public(db)
    state = tmp_path / "state"
    with pytest.raises(StateKeyError):
        write_state(_db_file(), state)  # 열쇠 없이 공급자 자료를 평문으로 쓰지 않음
    assert not state.exists() or not any(state.rglob("*"))
    rep = write_state(_db_file(), state, key=KEY)
    assert rep["encrypted"] >= 2
    enc = sorted(p.relative_to(state).as_posix() for p in state.rglob("*.enc"))
    assert "tables/instruments/all.sql.enc" in enc and "tables/official_prices/2026-11.sql.enc" in enc
    public_text = "\n".join(p.read_text(encoding="utf-8") for p in state.rglob("*") if p.is_file())
    for leaked in ("관찰전자", "70000", "INSERT INTO \"official_prices\"", "INSERT INTO \"instruments\""):
        assert leaked not in public_text, leaked
    assert not list(state.rglob("official_prices/*.sql"))  # 평문 파일 없음
    with pytest.raises(StateKeyError):
        restore(state, tmp_path / "nokey.db")
    with pytest.raises(StateKeyError):
        restore(state, tmp_path / "wrong.db", "y" * 43 + "=")
    restore(state, tmp_path / "ok.db", KEY)
    con = sqlite3.connect(tmp_path / "ok.db")
    try:
        assert con.execute("SELECT close FROM official_prices WHERE code='005930'").fetchone()[0] == 70_000
        assert con.execute("SELECT name FROM instruments WHERE code='005930'").fetchone()[0] == "관찰전자"
    finally:
        con.close()
    assert write_state(_db_file(), state, key=KEY)["changed"] == []  # 암호문도 내용이 같으면 다시 쓰지 않음


def test_ci_refuses_krx_without_state_key(monkeypatch, capsys):
    from app.ci import main
    monkeypatch.setenv("KRX_API_KEY", "some-krx-key")
    monkeypatch.delenv("SL_STATE_KEY", raising=False)
    assert main(["run", "--skip-jobs"]) == 1
    assert "SL_STATE_KEY" in capsys.readouterr().out


def test_gen_key_is_usable():
    from app.state import decrypt_text, encrypt_text, generate_key
    k = generate_key()
    assert decrypt_text(encrypt_text("안녕", k), k) == "안녕"
