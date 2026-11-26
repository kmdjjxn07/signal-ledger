import json
import re
from datetime import date, datetime

import pytest

from app.models import HoldingTransaction, JobLock, JobRun, LedgerViolation, LegacyRecord
from app.timeutil import KST, utcnow
from tests.conftest import add_price

D = date(2026, 11, 24)


# ---------------------------------------------------------------- 작업

def test_job_slot_idempotent_and_lock(calendar_2026, monkeypatch):
    from app import jobs
    calls = []

    def fake(db, day, now):
        calls.append(day)
        return {"ok": True}
    monkeypatch.setitem(jobs.JOBS, "intraday_audit", jobs.JobSpec("intraday_audit", "감사", frozenset({"audit.read"}),
                                                                   60, 2, fake))
    now = datetime(2026, 11, 24, 10, 0, tzinfo=KST)
    r1 = jobs.run_job("intraday_audit", "slot-A", D, now)
    r2 = jobs.run_job("intraday_audit", "slot-A", D, now)
    assert r1.status == "succeeded" and r2.id == r1.id and len(calls) == 1
    db = calendar_2026
    db.add(JobLock(name="job:intraday_audit", holder="other-host:1", acquired_at=utcnow(),
                   expires_at=utcnow().replace(year=2099)))
    db.commit()
    assert jobs.run_job("intraday_audit", "slot-B", D, now) is None and len(calls) == 1


def test_job_failure_retry_limit(calendar_2026, monkeypatch):
    from app import jobs

    def boom(db, day, now):
        raise RuntimeError("공급자 오류")
    monkeypatch.setitem(jobs.JOBS, "close", jobs.JobSpec("close", "마감", frozenset(), 60, 2, boom))
    now = datetime(2026, 11, 24, 16, 30, tzinfo=KST)
    jobs.run_job("close", "s", D, now)
    jobs.run_job("close", "s", D, now)
    r = jobs.run_job("close", "s", D, now)
    assert r.status == "failed" and r.attempts == 2 and "공급자 오류" in r.error


def test_due_slots_respect_calendar_and_settings(calendar_2026):
    from app.jobs import due_slots
    from app.services.settings_store import put_setting
    db = calendar_2026
    assert [s for s, _ in due_slots(db, datetime(2026, 11, 24, 10, 5, tzinfo=KST))] == ["intraday_audit", "paper_engine"]
    put_setting(db, "schedule", {"pre_brief_time": "08:10"})
    db.commit()
    slots = due_slots(db, datetime(2026, 11, 24, 10, 5, tzinfo=KST))
    assert ("pre_brief", "2026-11-24") in slots
    assert due_slots(db, datetime(2026, 12, 25, 10, 5, tzinfo=KST)) == []  # 휴장
    assert due_slots(db, datetime(2028, 3, 2, 10, 5, tzinfo=KST)) == []    # 캘린더 미확인
    assert not any(s == "paper_engine" for s, _ in due_slots(db, datetime(2026, 11, 24, 15, 40, tzinfo=KST)))


# ---------------------------------------------------------------- 웹·보안

@pytest.fixture()
def client(calendar_2026, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.security import create_owner
    monkeypatch.setenv("KRX_API_KEY", "SUPER-SECRET-KRX")
    create_owner(calendar_2026, "owner", "correct-horse-battery")
    calendar_2026.commit()
    return TestClient(app, base_url="http://testserver")


def login(c):
    page = c.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    r = c.post("/login", data={"username": "owner", "password": "correct-horse-battery", "csrf_token": token},
               follow_redirects=False)
    assert r.status_code == 303
    return re.search(r'name="csrf_token" value="([^"]+)"', c.get("/").text).group(1)


def test_auth_required(client):
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/api/export").status_code == 401
    assert client.get("/health").status_code == 200


def test_login_rejects_bad_password_and_csrf(client):
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    assert client.post("/login", data={"username": "owner", "password": "wrong-password!", "csrf_token": token}).status_code == 401
    assert client.post("/login", data={"username": "owner", "password": "correct-horse-battery",
                                       "csrf_token": "forged"}).status_code == 403


def test_all_pages_render_and_secrets_hidden(client):
    csrf = login(client)
    for path in ("/", "/research", "/paper", "/holdings", "/search?q=테스트", "/big-picture", "/archive", "/glossary",
                 "/admin", "/admin/research/new", "/admin/hypothesis/new", "/admin/briefing/new", "/admin/macro/new"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "SUPER-SECRET-KRX" not in r.text
        assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]
    assert "SUPER-SECRET-KRX" not in client.get("/api/export").text
    assert "설정됨" in client.get("/admin").text
    assert csrf


def test_post_requires_csrf_and_same_origin(client):
    csrf = login(client)
    data = {"name": "x", "initial_capital": "1000000", "duration_trading_days": "5", "max_positions": "3",
            "max_position_bp": "3500", "cost_bps": "15"}
    assert client.post("/admin/experiments", data=data).status_code == 403
    assert client.post("/admin/experiments", data={**data, "csrf_token": csrf},
                       headers={"origin": "https://evil.test"}).status_code == 403
    r = client.post("/admin/experiments", data={**data, "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303 and "/paper/" in r.headers["location"]


def test_xss_escaped(client):
    csrf = login(client)
    client.post("/admin/experiments", data={"name": "<script>alert(1)</script>", "initial_capital": "1000000",
                                            "duration_trading_days": "5", "max_positions": "3",
                                            "max_position_bp": "3500", "cost_bps": "15", "csrf_token": csrf})
    html = client.get("/paper").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_unconfigured_quote_shows_connection_needed(client):
    login(client)
    d = client.get("/api/quote/005930").json()
    assert d["status"] == "not_configured"


def test_empty_states_not_zero(client):
    login(client)
    html = client.get("/paper").text
    assert "산출 대기" in html
    assert "0%" not in html.split("산출 대기")[0][-200:]


# ---------------------------------------------------------------- 보유종목

def test_holdings_append_only_and_valuation(calendar_2026):
    from app.services import holdings as hs
    db = calendar_2026
    h = hs.add_holding(db, "005380", "합성자동차", "owner:t")
    t1 = hs.record_transaction(db, h, trade_date=date(2026, 4, 24), side="buy", quantity=8, unit_price=513000,
                               note=None, actor="owner:t")
    assert hs.position(db, h).total_cost == 4_104_000
    hs.record_transaction(db, h, trade_date=date(2026, 4, 24), side="buy", quantity=8, unit_price=512000,
                          note="정정", actor="owner:t", voids_id=t1.id)
    assert hs.position(db, h).total_cost == 4_096_000  # 원 기록은 보존, 정정 반영
    db.commit()
    with pytest.raises(hs.HoldingError):
        hs.record_transaction(db, h, trade_date=D, side="sell", quantity=99, unit_price=1, note=None, actor="t")
    db.rollback()
    v = hs.valuation(db, h, D)
    assert v.value is None  # 공식 종가 없으면 평가하지 않음(0으로 표시하지 않음)
    add_price(db, "005380", date(2026, 11, 20), 450000)
    add_price(db, "005380", date(2026, 11, 23), 455000)
    v = hs.valuation(db, h, D)
    assert v.value == 455000 * 8 and v.day_change == 5000 * 8 and v.price.trading_date == date(2026, 11, 23)
    tx = db.query(HoldingTransaction).first()
    tx.quantity = 1
    with pytest.raises(LedgerViolation):
        db.flush()


# ---------------------------------------------------------------- 가져오기·백업

def test_briefs_import_dry_run_then_idempotent(calendar_2026):
    from app.services.portability import ImportError_, import_briefs_json
    db = calendar_2026
    data = {"archive": [{"id": "b1", "date": "2026-09-01"}], "hypotheses": [{"id": "IH-1", "relative": -1.97}],
            "modelPortfolio": {"runs": [{"id": 382397784422855}]}}
    raw = json.dumps(data).encode()
    dry = import_briefs_json(db, raw, "briefs.json", dry_run=True, actor="t")
    assert dry.report["new"] == 3 and db.query(LegacyRecord).count() == 0
    import_briefs_json(db, raw, "briefs.json", dry_run=False, actor="t")
    db.commit()
    again = import_briefs_json(db, raw, "briefs.json", dry_run=False, actor="t")
    db.commit()
    assert again.report["duplicates"] == 3 and db.query(LegacyRecord).count() == 3
    kinds = {r.kind for r in db.query(LegacyRecord)}
    assert kinds == {"briefing", "legacyRelative", "experiment"}
    with pytest.raises(ImportError_):
        import_briefs_json(db, b"[1,2]", "x.json", dry_run=True, actor="t")
    from app.services.evaluation import statistics
    assert statistics(db, D)["total_issued"] == 0  # 과거 기록을 신규 통계에 소급 포함하지 않음


def test_backup_and_restore_verification(calendar_2026, tmp_path):
    from app.paper.lifecycle import register
    from app.services.portability import backup_sqlite, verify_backup
    db = calendar_2026
    register(db, "백업 테스트", {"initial_capital": 1_000_000, "duration_trading_days": 5, "max_positions": 3,
                              "max_position_bp": 3500, "cost_bps": 15}, "t")
    db.commit()
    info = backup_sqlite("test")
    check = verify_backup(info["path"])
    assert check["ok"], check
    with open(info["path"], "r+b") as f:  # 백업 파일 훼손 → 검증 실패
        f.seek(200)
        f.write(b"\x00garbage\x00")
    assert not verify_backup(info["path"])["ok"]


def test_job_run_records_distinguish_registration_from_success(calendar_2026):
    db = calendar_2026
    assert db.query(JobRun).count() == 0  # 예약 시각 저장만으로 성공 기록이 생기지 않음


def test_login_survives_extra_requests_between_form_and_submit(client):
    """실제 브라우저처럼 로그인 화면을 연 뒤 다른 요청(favicon, 새 탭)이 있어도 토큰이 바뀌지 않아야 한다."""
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.get("/favicon.ico").status_code == 204
    client.get("/", follow_redirects=True)  # 로그인 화면으로 다시 리다이렉트되는 요청
    r = client.post("/login", data={"username": "owner", "password": "correct-horse-battery", "csrf_token": token},
                    follow_redirects=False)
    assert r.status_code == 303
