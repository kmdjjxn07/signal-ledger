"""
테스트 공통 준비. 모든 테스트는 임시 SQLite 파일에 '합성 데이터'를 넣어 검증하며 운영 DB를 건드리지 않는다.
네트워크는 httpx.MockTransport로 대체한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# 실제 키가 환경에 있어도 테스트에서는 쓰지 않는다
for k in ("KRX_API_KEY", "DART_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL", "FRED_API_KEY", "NAVER_QUOTE_ENABLED"):
    os.environ[k] = ""


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SL_DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("SL_DATA_DIR", str(tmp_path / "data"))
    from app import db as dbmod
    dbmod.configure(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    dbmod.create_all()
    s = dbmod.session_factory()()
    yield s
    s.close()
    dbmod.engine().dispose()


@pytest.fixture()
def calendar_2026(db):
    """합성 캘린더: 2026년 확인됨 + 일부 휴장일. 실제 KRX 일정이 아님(테스트 전용)."""
    from app.models import CalendarException, CalendarYear
    db.add(CalendarYear(year=2026, confirmed=True, source_url="https://example.test/krx-2026", regular_open="09:00",
                        regular_close="15:30"))
    db.add(CalendarYear(year=2027, confirmed=True, source_url="https://example.test/krx-2027"))
    for d in ("2026-12-25", "2026-12-31", "2027-01-01"):
        db.add(CalendarException(day=date.fromisoformat(d), kind="holiday", source_url="https://example.test/h"))
    db.commit()
    return db


def add_instrument(db, code="005930", name="테스트전자", sector="반도체", market="KOSPI", etf=False):
    from app.models import Instrument
    db.add(Instrument(code=code, name=name, market=market, sector=sector, is_etf=etf, source="test"))
    db.flush()


def add_price(db, code, d, close, volume=1_000_000, value=50_000_000_000, scale=1):
    from app.models import OfficialPrice
    raw = hashlib.sha256(f"{code}{d}{close}{volume}".encode()).hexdigest()
    p = OfficialPrice(code=code, trading_date=d, close=close, volume=volume, trading_value=value, scale=scale,
                      provider="test", raw_sha256=raw)
    db.add(p)
    db.flush()
    return p


def add_source(db, grade="A", origin=None, company=False, checked=None):
    from app.services.sources import add_source as _add
    n = (origin or f"o-{os.urandom(4).hex()}")
    return _add(db, url=f"https://example.test/{n}", title=f"합성 출처 {n}", provider="test", grade=grade,
                origin_key=n, checked_at=checked or datetime(2026, 11, 20, 1, 0), is_company_claim=company)


def naver_payload(code="005930", price="70,000", t="2026-11-24T10:00:30+09:00", status="OPEN", session="REGULAR",
                  exch="KS", **extra):
    d = {"itemCode": code, "closePrice": price, "localTradedAt": t, "marketStatus": status,
         "marketSessionType": session, "stockExchangeType": {"code": exch, "name": "코스피"},
         "overMarketPriceInfo": {"overPrice": "99,999"}, **extra}
    return {"datas": [d]}


@pytest.fixture()
def mock_http():
    import httpx

    from app.providers.base import HttpClient

    def make(handler):
        return HttpClient(transport=httpx.MockTransport(handler))
    return make


def json_response(obj, status=200):
    import httpx
    return httpx.Response(status, content=json.dumps(obj, ensure_ascii=False).encode(),
                          headers={"content-type": "application/json"})


def research_items(db, verified=True, sources_per_item=2):
    from app.services.research import ITEMS
    items = {}
    for key, _ in ITEMS:
        srcs = [add_source(db, "A").id for _ in range(sources_per_item)]
        items[key] = {"conclusion": f"{key} 결론", "verified": verified, "source_ids": srcs}
    return items
