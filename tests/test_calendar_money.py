from datetime import date, datetime

import pytest

from app.money import parse_krw_int, pct_change, trade_cost
from app.services.calendar import CalendarUnknown, add_trading_days, day_info, is_in_regular_session
from app.timeutil import KST


def test_trade_cost_rounds_up_integer():
    assert trade_cost(1_000_000, 15) == 1500
    assert trade_cost(1, 15) == 1  # 올림
    with pytest.raises(ValueError):
        trade_cost(-1, 15)


def test_pct_change_decimal():
    assert str(pct_change(110, 100)) == "10.00"
    assert str(pct_change(99, 100)) == "-1.00"


def test_parse_krw_rejects_decimal():
    assert parse_krw_int("55,000") == 55000
    with pytest.raises(ValueError):
        parse_krw_int("55,000.5")


def test_unknown_year_is_not_trading(db):
    info = day_info(db, date(2026, 11, 24))
    assert info.status == "unknown"  # 평일이라고 거래일로 보지 않음


def test_weekday_holiday_weekend(calendar_2026):
    db = calendar_2026
    assert day_info(db, date(2026, 11, 24)).is_trading
    assert day_info(db, date(2026, 11, 28)).status == "closed"  # 토요일
    assert day_info(db, date(2026, 12, 25)).status == "closed"


def test_special_hours_and_closure(calendar_2026):
    from app.models import CalendarException
    db = calendar_2026
    db.add(CalendarException(day=date(2026, 11, 19), kind="special_hours", open_time="10:00", close_time="16:30",
                             source_url="https://example.test/suneung"))
    db.add(CalendarException(day=date(2026, 11, 20), kind="closure", source_url="https://example.test/closure"))
    db.commit()
    info = day_info(db, date(2026, 11, 19))
    assert info.is_trading and info.open_time.hour == 10
    assert not is_in_regular_session(db, datetime(2026, 11, 19, 9, 30, tzinfo=KST))
    assert is_in_regular_session(db, datetime(2026, 11, 19, 16, 0, tzinfo=KST))
    assert day_info(db, date(2026, 11, 20)).status == "closed"


def test_add_trading_days_skips_holidays_and_stops_at_unknown(calendar_2026):
    db = calendar_2026
    assert add_trading_days(db, date(2026, 12, 23), 2) == date(2026, 12, 28)  # 24, (25 휴장), 28
    from app.models import CalendarYear
    db.get(CalendarYear, 2027).confirmed = False
    db.commit()
    with pytest.raises(CalendarUnknown):
        add_trading_days(db, date(2026, 12, 23), 10)


def test_alembic_ini_is_ascii_for_korean_windows():
    # alembic은 설정 파일을 시스템 기본 인코딩(한국어 Windows = cp949)으로 읽는다. 한글이 있으면 init-db가 실패한다.
    from app.config import ROOT
    (ROOT / "alembic.ini").read_bytes().decode("ascii")
