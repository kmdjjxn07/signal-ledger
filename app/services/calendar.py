"""
KRX 거래소 캘린더.

평일이라고 거래일로 간주하지 않는다. 해당 연도의 휴장일 목록을 소유자가 공식 공지(KRX)로
확인해 '확인됨'으로 등록한 경우에만, 그 연도의 평일 중 휴장·임시휴장이 아닌 날을 거래일로 본다.
확인되지 않은 연도는 status="unknown"을 돌려주고, 거래·판정·브리핑 작업은 진행하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CalendarException, CalendarYear
from app.timeutil import KST


@dataclass(frozen=True)
class DayInfo:
    day: date
    status: str  # trading / closed / unknown
    open_time: time | None = None
    close_time: time | None = None
    reason: str = ""

    @property
    def is_trading(self) -> bool:
        return self.status == "trading"


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def day_info(db: Session, d: date) -> DayInfo:
    year = db.get(CalendarYear, d.year)
    if year is None or not year.confirmed:
        return DayInfo(d, "unknown", reason=f"{d.year}년 휴장일 목록이 공식 자료로 확인되지 않음")
    exceptions = db.scalars(select(CalendarException).where(CalendarException.day == d)).all()
    for ex in exceptions:
        if ex.kind in ("holiday", "closure"):
            label = "휴장" if ex.kind == "holiday" else "임시휴장"
            return DayInfo(d, "closed", reason=f"{label}: {ex.note or ''}".strip())
    if d.weekday() >= 5:
        return DayInfo(d, "closed", reason="주말")
    open_t, close_t = _parse_hhmm(year.regular_open), _parse_hhmm(year.regular_close)
    for ex in exceptions:
        if ex.kind == "special_hours" and ex.open_time and ex.close_time:
            open_t, close_t = _parse_hhmm(ex.open_time), _parse_hhmm(ex.close_time)
            return DayInfo(d, "trading", open_t, close_t, reason=f"특별 거래시간: {ex.note or ''}".strip())
    return DayInfo(d, "trading", open_t, close_t)


def session_bounds_kst(info: DayInfo) -> tuple[datetime, datetime] | None:
    if not info.is_trading or info.open_time is None or info.close_time is None:
        return None
    return (datetime.combine(info.day, info.open_time, tzinfo=KST),
            datetime.combine(info.day, info.close_time, tzinfo=KST))


def is_in_regular_session(db: Session, moment_kst: datetime) -> bool:
    info = day_info(db, moment_kst.date())
    bounds = session_bounds_kst(info)
    if bounds is None:
        return False
    return bounds[0] <= moment_kst < bounds[1]


class CalendarUnknown(Exception):
    pass


def add_trading_days(db: Session, start: date, n: int) -> date:
    """start 이후 n번째 거래일. 중간에 미확인 날짜가 있으면 CalendarUnknown."""
    if n <= 0:
        raise ValueError("n은 1 이상")
    d, count = start, 0
    for _ in range(400):
        d += timedelta(days=1)
        info = day_info(db, d)
        if info.status == "unknown":
            raise CalendarUnknown(info.reason)
        if info.is_trading:
            count += 1
            if count == n:
                return d
    raise CalendarUnknown("거래일 계산 범위를 넘었습니다")


def previous_trading_day(db: Session, d: date) -> date | None:
    cur = d
    for _ in range(30):
        cur -= timedelta(days=1)
        info = day_info(db, cur)
        if info.status == "unknown":
            return None
        if info.is_trading:
            return cur
    return None


def trading_days_between(db: Session, start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        info = day_info(db, d)
        if info.status == "unknown":
            raise CalendarUnknown(info.reason)
        if info.is_trading:
            out.append(d)
        d += timedelta(days=1)
    return out
