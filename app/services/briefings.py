"""
브리핑 발행.

상태: scheduled(발행 예정) → draft(초안) → published(정식 발행) / withheld(필수 수치 미검증으로 보류)
- 모든 판(version)을 보존한다. 화면은 (거래일, 종류)별 최신 published를 보여준다.
- archive = 현재 거래일 이전의 published 판들. 삭제하지 않는다.
- 개장 전 작업은 당일 close를 scheduled로 만든다. 단, 당일 close가 이미 어떤 판이라도 있으면 건드리지 않는다.
- 마감 후 작업은 KRX 공식 지수·거래량·거래대금이 검증되지 않으면 정식 발행하지 않는다(withheld 판만 추가).
- 사실(facts)은 반드시 출처(source_id)·등급·확인시각을 가진다. 해석은 사실과 별도 필드다.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Briefing, Instrument, Source
from app.providers.ai import GeminiProvider
from app.providers.base import PStatus
from app.providers.dart import DartProvider
from app.providers.macro import FRED_SERIES, FredProvider, FxProvider
from app.services.audit import audit
from app.services.calendar import day_info, previous_trading_day
from app.services.market_data import latest_official, log_fetch
from app.services.sources import add_source
from app.timeutil import utcnow

KIND_LABEL = {"pre": "개장 전 브리핑", "close": "장 마감 후 브리핑"}
STATUS_LABEL = {"scheduled": "발행 예정", "draft": "초안", "published": "발행됨", "withheld": "발행 보류"}


class BriefingError(Exception):
    pass


def empty_content() -> dict:
    return {"summary": "", "regime": None, "facts": [], "interpretations": [], "counter_scenarios": [],
            "next_checks": [], "data_status": [], "provenance": None}


def _next_version(db: Session, d: date, kind: str) -> int:
    v = db.scalar(select(func.max(Briefing.version)).where(Briefing.trading_date == d, Briefing.kind == kind))
    return (v or 0) + 1


def latest_published(db: Session, kind: str, d: date | None = None) -> Briefing | None:
    q = select(Briefing).where(Briefing.kind == kind, Briefing.status == "published")
    if d is not None:
        q = q.where(Briefing.trading_date == d)
    return db.scalar(q.order_by(Briefing.trading_date.desc(), Briefing.version.desc()).limit(1))


def latest_any(db: Session, kind: str, d: date) -> Briefing | None:
    return db.scalar(select(Briefing).where(Briefing.kind == kind, Briefing.trading_date == d)
                     .order_by(Briefing.version.desc()).limit(1))


def archive(db: Session, before: date, limit: int = 200) -> list[Briefing]:
    """현재 거래일 이전의 발행본(각 거래일·종류별 최신판)."""
    rows = db.scalars(select(Briefing).where(Briefing.status == "published", Briefing.trading_date < before)
                      .order_by(Briefing.trading_date.desc(), Briefing.kind, Briefing.version.desc())).all()
    seen, out = set(), []
    for b in rows:
        key = (b.trading_date, b.kind)
        if key not in seen:
            seen.add(key)
            out.append(b)
        if len(out) >= limit:
            break
    return out


def validate_content(db: Session, content: dict) -> list[str]:
    errors = []
    facts = content.get("facts") or []
    if not facts:
        errors.append("검증된 사실이 한 건도 없습니다.")
    for i, f in enumerate(facts, 1):
        sid = f.get("source_id")
        src = db.get(Source, sid) if sid else None
        if src is None:
            errors.append(f"사실 {i}: 출처가 연결되지 않았습니다.")
            continue
        if src.grade == "D":
            errors.append(f"사실 {i}: D등급 출처는 사실로 발행할 수 없습니다.")
        if not f.get("text"):
            errors.append(f"사실 {i}: 내용이 비어 있습니다.")
    for i, it in enumerate(content.get("interpretations") or [], 1):
        if not it.get("text"):
            errors.append(f"해석 {i}: 내용이 비어 있습니다.")
    return errors


def ensure_scheduled_close(db: Session, d: date, actor: str) -> Briefing | None:
    """당일 close가 하나도 없을 때만 scheduled 판을 만든다(지연된 개장 전 작업이 발행본을 덮지 않게)."""
    if latest_any(db, "close", d) is not None:
        return None
    b = Briefing(trading_date=d, kind="close", version=_next_version(db, d, "close"), status="scheduled",
                 content={**empty_content(), "summary": "오늘 장 마감 후 발행 예정"}, created_by=actor)
    db.add(b)
    db.flush()
    return b


def save_version(db: Session, d: date, kind: str, status: str, content: dict, actor: str) -> Briefing:
    if kind not in KIND_LABEL or status not in STATUS_LABEL:
        raise BriefingError("알 수 없는 종류·상태")
    b = Briefing(trading_date=d, kind=kind, version=_next_version(db, d, kind), status=status,
                 content=content, created_by=actor, published_at=utcnow() if status == "published" else None)
    db.add(b)
    db.flush()
    audit(db, actor, f"briefing.{status}", f"{kind}:{d.isoformat()}:v{b.version}")
    return b


def publish_draft(db: Session, briefing_id: int, actor: str) -> Briefing:
    """초안을 검증해 새 published 판으로 발행(초안 행은 그대로 보존)."""
    draft = db.get(Briefing, briefing_id)
    if draft is None or draft.status not in ("draft", "withheld"):
        raise BriefingError("발행할 수 있는 초안이 아닙니다.")
    errors = validate_content(db, draft.content)
    if draft.kind == "close":
        required_missing = [s for s in draft.content.get("data_status", []) if s.get("required") and s.get("status") != "ok"]
        if required_missing:
            errors.append("마감 필수 수치가 검증되지 않았습니다: " + ", ".join(s["item"] for s in required_missing))
    if errors:
        raise BriefingError(" / ".join(errors))
    return save_version(db, draft.trading_date, draft.kind, "published", dict(draft.content), actor)


# ---------------------------------------------------------------- 자동 작업

def _fact_from_official(db: Session, code: str, label: str, d: date) -> tuple[dict | None, dict]:
    row = latest_official(db, code, d)
    if row is None:
        return None, {"item": f"{label} ({d.isoformat()})", "status": "missing", "detail": "KRX 공식 일별값 없음"}
    src = add_source(db, url=row.source_url or "https://data.krx.co.kr", title=f"KRX 공식 일별 {label}",
                     provider="KRX", grade="A", origin_key=f"krx:{code}:{d.isoformat()}:{row.raw_sha256[:12]}",
                     checked_at=row.fetched_at, raw_sha256=row.raw_sha256)
    if row.scale == 100:
        value = f"{row.close // 100:,}.{row.close % 100:02d}"
    else:
        value = f"{row.close:,}원"
    fact = {"text": f"{label} 종가(KRX 정규장, {d.isoformat()})", "value": value, "source_id": src.id,
            "grade": "A", "checked_at": row.fetched_at.isoformat(), "official_price_id": row.id}
    extra = []
    if row.volume is not None:
        extra.append(f"거래량 {row.volume:,}")
    if row.trading_value is not None:
        extra.append(f"거래대금 {row.trading_value:,}원")
    if extra:
        fact["detail"] = " · ".join(extra)
    return fact, {"item": f"{label} ({d.isoformat()})", "status": "ok", "detail": "KRX 공식 일별(A)"}


def _overseas_facts(db: Session, d: date) -> tuple[list[dict], list[dict]]:
    facts, status = [], []
    fred = FredProvider()
    for sid, label in FRED_SERIES.items():
        res = fred.latest(sid, d)
        log_fetch(db, "fred", f"latest:{sid}", res)
        if not res.ok:
            status.append({"item": label, "status": res.status.value, "detail": res.detail})
            continue
        src = add_source(db, url=f"https://fred.stlouisfed.org/series/{sid}", title=label, provider="FRED", grade="B",
                         origin_key=f"fred:{sid}:{res.data['observation_date']}", checked_at=utcnow())
        facts.append({"text": f"{label} — 관측일 {res.data['observation_date']}", "value": res.data["value"],
                      "source_id": src.id, "grade": "B", "checked_at": utcnow().isoformat()})
        status.append({"item": label, "status": "ok", "detail": f"관측일 {res.data['observation_date']}"})
    fx = FxProvider().seoul_close(d)
    status.append({"item": "원·달러 환율(서울 외환시장 종가)", "status": fx.status.value, "detail": fx.detail})
    return facts, status


def _disclosure_facts(db: Session, codes: list[str], start: date, end: date) -> tuple[list[dict], list[dict]]:
    facts, status = [], []
    dart = DartProvider()
    if not dart.api_key:
        return [], [{"item": "DART 공시", "status": PStatus.NOT_CONFIGURED.value, "detail": "DART_API_KEY 미설정"}]
    for code in codes:
        inst = db.get(Instrument, code)
        if inst is None or inst.is_etf or not inst.corp_code:
            status.append({"item": f"공시 {code}", "status": "skipped",
                           "detail": "ETF이거나 DART 고유번호 매핑 없음 — 다른 기업 공시를 연결하지 않음"})
            continue
        res = dart.disclosures(inst.corp_code, start, end)
        log_fetch(db, "dart", f"list:{code}", res, record_count=len(res.data or []) if res.ok else 0)
        if not res.ok:
            status.append({"item": f"공시 {inst.name}", "status": res.status.value, "detail": res.detail})
            continue
        for it in res.data[:5]:
            src = add_source(db, url=it["url"], title=it["report_nm"], provider="DART", grade="A",
                             origin_key=f"dart:{it['rcept_no']}", checked_at=utcnow())
            facts.append({"text": f"{inst.name} 공시: {it['report_nm']} (접수 {it['rcept_dt']})", "value": None,
                          "source_id": src.id, "grade": "A", "checked_at": utcnow().isoformat()})
        status.append({"item": f"공시 {inst.name}", "status": "ok", "detail": f"{len(res.data)}건"})
    return facts, status


def _ai_interpret(facts: list[dict], kind: str) -> tuple[dict | None, dict]:
    ai = GeminiProvider()
    if not ai.configured:
        return None, {"item": "AI 해석", "status": PStatus.NOT_CONFIGURED.value,
                      "detail": "AI 미연결 — 해석은 관리자가 직접 작성"}
    schema = {"type": "object", "properties": {
        "summary": {"type": "string"}, "regime": {"type": "string"},
        "interpretations": {"type": "array", "items": {"type": "string"}},
        "counter_scenarios": {"type": "array", "items": {"type": "string"}},
        "next_checks": {"type": "array", "items": {"type": "string"}}},
        "required": ["summary", "interpretations", "counter_scenarios", "next_checks"]}
    material = {"facts": [{"text": f["text"], "value": f.get("value")} for f in facts]}
    res = ai.generate_json(f"{KIND_LABEL[kind]}의 해석을 사실과 구분해 작성하라. 수치는 자료에 있는 것만 인용.",
                           material, schema)
    if not res.ok:
        return None, {"item": "AI 해석", "status": res.status.value, "detail": res.detail}
    data = res.data
    try:
        out = {"summary": str(data["summary"])[:600], "regime": str(data.get("regime") or "")[:200] or None,
               "interpretations": [{"text": str(t)[:800], "by": "ai"} for t in data["interpretations"]][:6],
               "counter_scenarios": [str(t)[:500] for t in data["counter_scenarios"]][:5],
               "next_checks": [str(t)[:300] for t in data["next_checks"]][:6]}
    except (KeyError, TypeError):
        return None, {"item": "AI 해석", "status": PStatus.INVALID.value, "detail": "스키마 불일치"}
    return out, {"item": "AI 해석", "status": "ok", "detail": "AI 제안(사실 아님)"}


def watch_codes(db: Session) -> list[str]:
    from app.models import Hypothesis, ResearchVersion
    codes = {h.code for h in db.scalars(select(Hypothesis).where(Hypothesis.status.in_(["활성", "중기추적"])))}
    codes |= {r.code for r in db.scalars(select(ResearchVersion).where(ResearchVersion.is_current_candidate))}
    return sorted(codes)


def run_pre_brief(db: Session, d: date, actor: str = "job:pre_brief") -> dict:
    info = day_info(db, d)
    if info.status == "unknown":
        return {"result": "skipped", "reason": info.reason}
    if not info.is_trading:
        return {"result": "skipped", "reason": f"휴장: {info.reason}"}
    facts, status = [], []
    prev = previous_trading_day(db, d)
    if prev:
        for code, label in (("IDX:KOSPI", "코스피"), ("IDX:KOSDAQ", "코스닥")):
            f, s = _fact_from_official(db, code, label, prev)
            status.append(s)
            if f:
                facts.append(f)
    else:
        status.append({"item": "직전 거래일", "status": "missing", "detail": "캘린더에서 직전 거래일 확인 불가"})
    ov_f, ov_s = _overseas_facts(db, d)
    facts += ov_f
    status += ov_s
    dc_f, dc_s = _disclosure_facts(db, watch_codes(db), d - timedelta(days=3), d)
    facts += dc_f
    status += dc_s
    ai_out, ai_s = _ai_interpret(facts, "pre") if facts else (None, {"item": "AI 해석", "status": "skipped",
                                                                         "detail": "해석할 사실 없음"})
    status.append(ai_s)
    content = {**empty_content(), "facts": facts, "data_status": status, "provenance": "job",
               "checked_at": utcnow().isoformat()}
    if ai_out:
        content.update(ai_out)
    else:
        content["summary"] = "확인된 사실만 정리했습니다. 해석은 아직 작성되지 않았습니다." if facts else ""
    if facts:
        b = save_version(db, d, "pre", "published", content, actor)
        result = "published"
    else:
        b = save_version(db, d, "pre", "withheld", content, actor)
        result = "withheld"
    ensure_scheduled_close(db, d, actor)
    return {"result": result, "briefing_id": b.id, "facts": len(facts)}


def run_close_brief(db: Session, d: date, actor: str = "job:close_brief") -> dict:
    info = day_info(db, d)
    if not info.is_trading:
        return {"result": "skipped", "reason": info.reason or "거래일 아님"}
    facts, status = [], []
    for code, label in (("IDX:KOSPI", "코스피"), ("IDX:KOSDAQ", "코스닥")):
        f, s = _fact_from_official(db, code, label, d)
        s["required"] = True
        if f and ("detail" not in f):
            s["status"], s["detail"] = "partial", "종가는 있으나 거래량·거래대금 없음"
        status.append(s)
        if f:
            facts.append(f)
    status.append({"item": "외국인·기관·개인 수급(시장 전체)", "status": PStatus.NOT_CONFIGURED.value,
                   "detail": "요건을 만족하는 수급 공급자 미연결", "required": False})
    dc_f, dc_s = _disclosure_facts(db, watch_codes(db), d, d)
    facts += dc_f
    status += dc_s
    required_ok = all(s.get("status") == "ok" for s in status if s.get("required"))
    content = {**empty_content(), "facts": facts, "data_status": status, "provenance": "job",
               "checked_at": utcnow().isoformat()}
    if required_ok:
        ai_out, ai_s = _ai_interpret(facts, "close")
        status.append(ai_s)
        if ai_out:
            content.update(ai_out)
        else:
            content["summary"] = "KRX 공식 마감 수치를 확인했습니다. 해석은 아직 작성되지 않았습니다."
        b = save_version(db, d, "close", "published", content, actor)
        return {"result": "published", "briefing_id": b.id}
    content["summary"] = "필수 마감 수치가 검증되지 않아 정식 발행을 보류했습니다."
    b = save_version(db, d, "close", "withheld", content, actor)
    return {"result": "withheld", "briefing_id": b.id}
