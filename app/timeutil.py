"""
시간 처리. DB에는 UTC(타임존 정보 없는 naive datetime)로 저장하고, 화면에는 KST로 보여준다.
Windows에는 시스템 시간대 DB가 없으므로 tzdata 패키지가 반드시 필요하다(requirements.txt 포함).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def utcnow() -> datetime:
    """DB 저장용 UTC naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now_kst() -> datetime:
    return datetime.now(KST)


def to_kst(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def kst_to_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def fmt_kst(dt: datetime | None, with_seconds: bool = False) -> str:
    if dt is None:
        return "—"
    k = to_kst(dt)
    return k.strftime("%Y-%m-%d %H:%M:%S KST" if with_seconds else "%Y-%m-%d %H:%M KST")


def today_kst() -> date:
    return now_kst().date()


def parse_iso_aware(value: str) -> datetime | None:
    """타임존이 명시된 ISO 문자열만 받아들인다. 타임존이 없으면 추측하지 않고 None."""
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return None
    return dt
