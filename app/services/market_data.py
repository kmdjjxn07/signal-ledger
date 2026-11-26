"""
공식 가격·관측·공시 매핑 저장.

- 실패 응답은 기존 성공 레코드를 절대 덮어쓰지 않는다(아무것도 쓰지 않고 fetch_logs에만 기록).
- 같은 내용(해시 동일)은 중복 저장하지 않는다.
- 내용이 바뀐 정정값은 새 행으로 저장하고 supersedes_id로 이전 행과 연결한다.
- 거래일인데 빈 응답이면 '미발행', 캘린더상 휴장이면 '휴장', 캘린더 미확인이면 호출하지 않는다.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import FetchLog, Instrument, Observation, OfficialPrice
from app.providers.base import ProviderResult, PStatus
from app.providers.krx import DailyRow, KrxDailyProvider
from app.providers.naver import validate_quote
from app.services.calendar import day_info
from app.services.settings_store import get_setting
from app.services.sources import canonical_json, sha256_text, store_raw
from app.timeutil import kst_to_utc_naive, utcnow


def log_fetch(db: Session, provider: str, operation: str, res: ProviderResult, record_count: int = 0,
              raw_sha: str | None = None) -> FetchLog:
    row = FetchLog(provider=provider, operation=operation, status=res.status.value, http_status=res.http_status,
                   detail=(res.detail or "")[:1000], raw_sha256=raw_sha, record_count=record_count)
    db.add(row)
    db.flush()
    return row


def latest_official(db: Session, code: str, d: date) -> OfficialPrice | None:
    """(종목, 거래일)의 최신판(정정 반영) 공식 가격."""
    return db.scalar(select(OfficialPrice).where(OfficialPrice.code == code, OfficialPrice.trading_date == d)
                     .order_by(OfficialPrice.id.desc()).limit(1))


def latest_official_on_or_before(db: Session, code: str, d: date) -> OfficialPrice | None:
    last_date = db.scalar(select(func.max(OfficialPrice.trading_date))
                          .where(OfficialPrice.code == code, OfficialPrice.trading_date <= d))
    return latest_official(db, code, last_date) if last_date else None


def save_official_row(db: Session, row: DailyRow, provider: str, url: str | None) -> tuple[str, OfficialPrice]:
    """반환: ('created' | 'duplicate' | 'corrected', 행)"""
    content_hash = sha256_text(canonical_json(row.raw))
    existing_same = db.scalar(select(OfficialPrice).where(
        OfficialPrice.code == row.code, OfficialPrice.trading_date == row.trading_date,
        OfficialPrice.raw_sha256 == content_hash))
    if existing_same is not None:
        return "duplicate", existing_same
    prev = latest_official(db, row.code, row.trading_date)
    rec = OfficialPrice(code=row.code, trading_date=row.trading_date, close=row.close, open=row.open, high=row.high,
                        low=row.low, volume=row.volume, trading_value=row.trading_value, market_cap=row.market_cap,
                        scale=row.scale, provider=provider, source_url=url, raw_sha256=content_hash,
                        supersedes_id=prev.id if prev else None)
    db.add(rec)
    db.flush()
    return ("corrected" if prev else "created"), rec


def _upsert_instrument(db: Session, row: DailyRow) -> None:
    inst = db.get(Instrument, row.code)
    if inst is None:
        db.add(Instrument(code=row.code, name=row.name, market=row.market, source="krx",
                          is_etf=False if row.market == "INDEX" else _looks_like_etf(row.name)))
    elif inst.name != row.name:
        inst.name, inst.updated_at = row.name, utcnow()


_ETF_PREFIXES = ("KODEX", "TIGER", "KBSTAR", "RISE", "ACE", "SOL", "HANARO", "KOSEF", "ARIRANG", "PLUS", "KIWOOM")


def _looks_like_etf(name: str) -> bool:
    """KRX 주식 일별 API에는 ETF가 포함되지 않는 것으로 알려져 있으나, 혹시 섞이면 공시 오연결을 막기 위해 표시."""
    return name.upper().startswith(_ETF_PREFIXES)


def sync_official_daily(db: Session, d: date, provider: KrxDailyProvider | None = None) -> dict:
    provider = provider or KrxDailyProvider()
    info = day_info(db, d)
    summary = {"date": d.isoformat(), "calendar": info.status, "results": {}}
    if info.status == "unknown":
        log_fetch(db, "krx", "official_daily", ProviderResult(PStatus.INVALID, detail=f"캘린더 미확인: {info.reason}"))
        summary["results"]["all"] = "calendar_unknown"
        return summary
    if info.status == "closed":
        log_fetch(db, "krx", "official_daily", ProviderResult(PStatus.CLOSED, detail=info.reason))
        summary["results"]["all"] = PStatus.CLOSED.value
        return summary

    for market in ("KOSPI", "KOSDAQ"):
        for kind, fn in (("stock", provider.stock_daily), ("index", provider.index_daily)):
            op = f"{kind}_daily:{market}"
            res = fn(market, d)
            if res.status == PStatus.EMPTY:
                res = ProviderResult(PStatus.NOT_PUBLISHED, detail="거래일이지만 아직 발행되지 않음", url=res.url)
            if not res.ok:
                log_fetch(db, "krx", op, res)
                summary["results"][op] = res.status.value
                continue
            raw_sha = store_raw(db, "krx", res.url, res.raw_text or "")
            rows = res.data if isinstance(res.data, list) else [res.data]
            counts = {"created": 0, "duplicate": 0, "corrected": 0}
            for r in rows:
                _upsert_instrument(db, r)
                outcome, _ = save_official_row(db, r, "krx", res.url)
                counts[outcome] += 1
            log_fetch(db, "krx", op, res, record_count=len(rows), raw_sha=raw_sha)
            summary["results"][op] = {"status": "ok", **counts}
    return summary


def record_observation(db: Session, code: str, res: ProviderResult, requested_at: datetime,
                       received_at: datetime) -> Observation:
    """네이버 등 원본 관측 저장. 적격성은 원본 필드로만 판정한다."""
    if not res.ok:
        obs = Observation(code=code, provider="naver", requested_at=requested_at, received_at=received_at,
                          eligible=False, ineligible_reason=f"{res.status.value}: {res.detail}")
        db.add(obs)
        db.flush()
        return obs
    raw_sha = store_raw(db, "naver", res.url, res.raw_text or "")
    q = validate_quote(code, res.data, get_setting(db, "naver_validation"))
    obs = Observation(
        code=code, provider="naver", requested_at=requested_at, received_at=received_at,
        source_time=kst_to_utc_naive(q.source_time) if q.source_time else None, price=q.price,
        market_status=q.market_status, session_type=str(q.session_type) if q.session_type else None,
        exchange_type=q.exchange_code, eligible=q.eligible, ineligible_reason=q.reason, raw_sha256=raw_sha)
    db.add(obs)
    db.flush()
    return obs


def provider_status(db: Session) -> list[dict]:
    """관리자 화면: 공급자·작업별 최근 결과와 마지막 성공 시각."""
    out = []
    ops = db.execute(select(FetchLog.provider, FetchLog.operation).distinct()).all()
    for provider, op in ops:
        last = db.scalar(select(FetchLog).where(FetchLog.provider == provider, FetchLog.operation == op)
                         .order_by(FetchLog.id.desc()).limit(1))
        last_ok = db.scalar(select(FetchLog).where(FetchLog.provider == provider, FetchLog.operation == op,
                                                   FetchLog.status == "ok").order_by(FetchLog.id.desc()).limit(1))
        out.append({"provider": provider, "operation": op, "last": last, "last_ok": last_ok})
    return out


def sync_dart_corp_codes(db: Session, actor: str) -> tuple[int, str | None]:
    """DART 기업 고유번호를 종목 마스터(ETF 제외)에 연결한다. (연결 건수, 실패 사유)"""
    from app.models import Instrument as _Instrument
    from app.providers.dart import DartProvider
    from app.services.audit import audit
    res = DartProvider().corp_codes()
    log_fetch(db, "dart", "corp_codes", res, record_count=len(res.data or {}) if res.ok else 0)
    if not res.ok:
        return 0, f"{res.status.value} {res.detail or ''}".strip()
    n = 0
    for inst in db.scalars(select(_Instrument).where(_Instrument.is_etf == False)):  # noqa: E712
        m = res.data.get(inst.code)
        if m and inst.corp_code != m["corp_code"]:
            inst.corp_code, n = m["corp_code"], n + 1
    audit(db, actor, "dart.corp_codes", None, {"updated": n})
    return n, None
