"""
명령 계층: 소유자 입력(출처·검증·가설·캘린더 등)을 '명령'으로 표현해 같은 검증 코드로 처리한다.

- 로컬 '직접 모드'(SL_MODE=direct, 기본): 화면에서 바로 DB에 반영
- 로컬 '편집기 모드'(SL_MODE=editor): 화면 입력을 inbox/*.json 제출 파일로 저장 → GitHub에 올리면
  GitHub Actions가 같은 핸들러로 검증·반영(app/ci.py). 공개 원장은 GitHub 실행기만 쓴다.

보유종목은 명령으로 만들 수 없다(공개 원장에 들어가면 안 되므로 로컬 직접 모드 전용).
출처 번호를 아직 모를 때는 "ref:이름"으로 가리킬 수 있다(source.add의 ref).
"""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ROOT
from app.models import (
    CalendarException, CalendarYear, Experiment, Hypothesis, Instrument, Source,
)
from app.services.audit import audit
from app.timeutil import KST, utcnow


class CommandError(Exception):
    pass


def inbox_dir() -> Path:
    return Path(os.environ.get("SL_INBOX_DIR") or (ROOT / "inbox"))


MAX_INBOX_BYTES = 2 * 1024 * 1024


# ---------------------------------------------------------------- 공통 변환

def _date(v, field: str) -> date:  # noqa: ANN001
    try:
        return date.fromisoformat(str(v))
    except (TypeError, ValueError):
        raise CommandError(f"{field}: YYYY-MM-DD 형식이어야 합니다.") from None


def _kst_dt(v, field: str) -> datetime:  # noqa: ANN001
    """'2026-11-24T10:00'(KST) → UTC naive. 시간대가 적혀 있으면 그대로 변환."""
    try:
        dt = datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        raise CommandError(f"{field}: 'YYYY-MM-DDTHH:MM' 형식이어야 합니다(KST).") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _int(v, field: str, allow_none: bool = False) -> int | None:  # noqa: ANN001
    if v in (None, "") and allow_none:
        return None
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        raise CommandError(f"{field}: 정수여야 합니다.") from None


def _list(v) -> list:  # noqa: ANN001
    if v in (None, ""):
        return []
    if isinstance(v, list):
        return v
    return [x.strip() for x in str(v).replace("\n", ",").split(",") if x.strip()]


def resolve_source_ids(db: Session, values) -> list[int]:  # noqa: ANN001
    out = []
    for v in _list(values):
        s = str(v).strip()
        if s.startswith("ref:"):
            src = db.scalar(select(Source).where(Source.ref_key == s[4:]))
            if src is None:
                raise CommandError(f"출처 참조 '{s}'를 찾지 못했습니다(먼저 source.add로 등록).")
            out.append(src.id)
        elif s.isdigit():
            if db.get(Source, int(s)) is None:
                raise CommandError(f"출처 #{s}가 없습니다.")
            out.append(int(s))
        else:
            raise CommandError(f"출처는 번호 또는 'ref:이름'으로 적어야 합니다: {s!r}")
    return out


def _hypothesis(db: Session, ref) -> Hypothesis:  # noqa: ANN001
    h = db.scalar(select(Hypothesis).where(Hypothesis.uid == str(ref))) if not str(ref).isdigit() \
        else db.get(Hypothesis, int(ref))
    if h is None:
        raise CommandError(f"가설 {ref}이(가) 없습니다.")
    return h


# ---------------------------------------------------------------- 핸들러

def h_schedule(db, p, actor):  # noqa: ANN001, ANN201
    from app.services.settings_store import put_setting
    vals = {}
    for k in ("pre_brief_time", "close_brief_time", "settlement_time"):
        v = str(p.get(k) or "").strip()
        if v:
            try:
                datetime.strptime(v, "%H:%M")
            except ValueError:
                raise CommandError(f"{k} 형식은 HH:MM 입니다.") from None
        vals[k] = v or None
    put_setting(db, "schedule", vals)
    audit(db, actor, "settings.schedule", None, vals)
    return "예약 시각을 저장했습니다(저장 = 예약 등록이며 실제 성공은 작업 기록에서 확인).", "/admin"


def h_calendar_year(db, p, actor):  # noqa: ANN001, ANN201
    year, url = _int(p.get("year"), "year"), str(p.get("source_url") or "").strip()
    if not url.startswith("https://"):
        raise CommandError("KRX 공식 공지 URL(https)이 필요합니다.")
    days = [_date(x, "holidays") for x in _list(p.get("holidays"))]
    if any(d.year != year for d in days):
        raise CommandError("다른 연도의 날짜가 섞여 있습니다.")
    row = db.get(CalendarYear, year) or CalendarYear(year=year)
    row.confirmed, row.source_url, row.confirmed_at = True, url, utcnow()
    row.regular_open = str(p.get("open") or "09:00")
    row.regular_close = str(p.get("close") or "15:30")
    db.merge(row)
    existing = {e.day for e in db.scalars(select(CalendarException).where(CalendarException.kind == "holiday"))}
    for d in days:
        if d not in existing:
            db.add(CalendarException(day=d, kind="holiday", source_url=url, note=str(p.get("note") or "공식 휴장일")))
    audit(db, actor, "calendar.confirm_year", str(year), {"holidays": len(days), "source": url})
    return f"{year}년 캘린더를 확인됨으로 등록했습니다(휴장일 {len(days)}일).", "/admin"


def h_calendar_exception(db, p, actor):  # noqa: ANN001, ANN201
    d, kind, url = _date(p.get("day"), "day"), str(p.get("kind")), str(p.get("source_url") or "").strip()
    if kind not in ("holiday", "closure", "special_hours") or not url.startswith("https://"):
        raise CommandError("종류(holiday/closure/special_hours)와 공지 URL(https)이 필요합니다.")
    db.add(CalendarException(day=d, kind=kind, open_time=str(p.get("open_time") or "") or None,
                             close_time=str(p.get("close_time") or "") or None, source_url=url,
                             note=str(p.get("note") or "")[:200]))
    audit(db, actor, "calendar.exception", d.isoformat(), {"kind": kind})
    return "캘린더 예외를 추가했습니다.", "/admin"


def h_naver(db, p, actor):  # noqa: ANN001, ANN201
    from app.services.settings_store import put_setting
    vals = {"confirmed_by_owner": bool(p.get("confirmed"))}
    for key in ("allowed_market_status", "allowed_session_types", "allowed_exchange_codes"):
        items = [str(x) for x in _list(p.get(key))]
        if items:
            vals[key] = items
    put_setting(db, "naver_validation", vals)
    audit(db, actor, "settings.naver_validation", None, vals)
    return "네이버 원본 검증 기준을 저장했습니다.", "/admin"


def h_instrument(db, p, actor):  # noqa: ANN001, ANN201
    inst = db.get(Instrument, str(p.get("code") or "").strip())
    if inst is None:
        raise CommandError("종목 마스터에 없는 코드입니다(KRX 동기화 후 다시 제출).")
    inst.sector = str(p.get("sector") or "").strip() or None
    inst.managed_issue = bool(p.get("managed_issue"))
    inst.is_etf = bool(p.get("is_etf"))
    if inst.is_etf:
        inst.corp_code = None
    audit(db, actor, "instrument.update", inst.code, {"sector": inst.sector, "managed": inst.managed_issue,
                                                      "etf": inst.is_etf})
    return f"{inst.name} 정보를 저장했습니다.", "/admin"


def h_source(db, p, actor):  # noqa: ANN001, ANN201
    from app.services.sources import add_source
    published = str(p.get("published_at") or "").strip()
    if not str(p.get("title") or "").strip() or not str(p.get("provider") or "").strip():
        raise CommandError("출처 제목과 제공처가 필요합니다.")
    try:
        src = add_source(db, url=str(p.get("url") or "").strip(), title=str(p.get("title") or "").strip(),
                         provider=str(p.get("provider") or "").strip(), grade=str(p.get("grade") or ""),
                         origin_key=str(p.get("origin_key") or "").strip() or None,
                         checked_at=_kst_dt(p.get("checked_at"), "checked_at"),
                         published_at=datetime.fromisoformat(published) if published else None,
                         is_company_claim=bool(p.get("is_company_claim")), note=str(p.get("note") or "")[:500] or None,
                         ref_key=str(p.get("ref")).strip() if p.get("ref") else None)
    except ValueError as e:
        raise CommandError(f"출처 등록 실패: {e}") from None
    audit(db, actor, "source.add", str(src.id), {"grade": src.grade, "ref": src.ref_key})
    return f"출처 #{src.id} 등록" + (f" (ref:{src.ref_key})" if src.ref_key else ""), "/admin"


def h_research(db, p, actor):  # noqa: ANN001, ANN201
    from app.services.research import ITEMS, ResearchError, submit_research
    code = str(p.get("code") or "").strip()
    raw_items = p.get("items") or {}
    items = {}
    for k, _ in ITEMS:
        it = raw_items.get(k) or {}
        items[k] = {"conclusion": str(it.get("conclusion") or ""), "verified": bool(it.get("verified")),
                    "source_ids": resolve_source_ids(db, it.get("source_ids"))}
    try:
        rv = submit_research(db, code, {"items": items, "liquidity_ok": bool(p.get("liquidity_ok")),
                                        "red_flags": [str(x) for x in _list(p.get("red_flags"))],
                                        "next_checks": [str(x) for x in _list(p.get("next_checks"))]}, actor)
    except ResearchError as e:
        raise CommandError(str(e)) from None
    return f"심층검증 판 #{rv.id} 저장: {rv.status}" + (
        f" — {' / '.join(rv.failed_reasons)}" if rv.failed_reasons else ""), f"/research/{code}"


def h_hypothesis(db, p, actor):  # noqa: ANN001, ANN201
    from app.services.hypotheses import AXES, IssueRejected, issue
    from app.services.research import latest_for_code
    code = str(p.get("code") or "").strip()
    rvid = p.get("research_version_id")
    if rvid in (None, "", "latest"):
        latest = latest_for_code(db, code)
        rvid = latest.id if latest else 0
    payload = {k: str(p.get(k) or "") for k in ("code", "catalyst", "not_priced_reason", "counter_scenario",
                                                 "invalidation", "next_check", "direction", "confidence")}
    scores = p.get("scores") or {}
    payload.update({"research_version_id": _int(rvid, "research_version_id"),
                    "scores": {a: _int(scores.get(a), f"scores.{a}") if scores.get(a) not in (None, "") else -1
                               for a in AXES},
                    "source_ids": resolve_source_ids(db, p.get("source_ids")),
                    "flow_source_ids": resolve_source_ids(db, p.get("flow_source_ids")),
                    "macro_headwind": bool(p.get("macro_headwind"))})
    try:
        h = issue(db, payload, actor)
    except IssueRejected as e:
        raise CommandError("발행 거부: " + " / ".join(e.reasons)) from None
    return f"가설 {h.uid} 발행({h.quality_tier}, {h.quality_score}점)", "/research"


def h_hyp_invalidate(db, p, actor):  # noqa: ANN001, ANN201
    from app.services.hypotheses import invalidate
    h = _hypothesis(db, p.get("hypothesis"))
    try:
        invalidate(db, h, str(p.get("reason") or ""), actor)
    except ValueError as e:
        raise CommandError(str(e)) from None
    return f"{h.uid} 무효화 기록", "/research"


def h_hyp_next_check(db, p, actor):  # noqa: ANN001, ANN201
    from app.services.hypotheses import update_next_check
    h = _hypothesis(db, p.get("hypothesis"))
    value = str(p.get("next_check") or "").strip()
    if not value:
        raise CommandError("다음 확인 일정이 비어 있습니다.")
    update_next_check(db, h, value, actor)
    return f"{h.uid} 다음 확인 일정 변경(감사로그 기록)", "/research"


def h_briefing_draft(db, p, actor):  # noqa: ANN001, ANN201
    from app.services.briefings import BriefingError, empty_content, publish_draft, save_version
    d, kind = _date(p.get("trading_date"), "trading_date"), str(p.get("kind"))
    if kind not in ("pre", "close"):
        raise CommandError("kind는 pre 또는 close 입니다.")
    facts = []
    for f in p.get("facts") or []:
        ids = resolve_source_ids(db, [f.get("source")]) if f.get("source") not in (None, "") else []
        src = db.get(Source, ids[0]) if ids else None
        facts.append({"text": str(f.get("text") or "").strip(), "value": f.get("value") or None,
                      "source_id": src.id if src else None, "grade": src.grade if src else None,
                      "checked_at": src.checked_at.isoformat() if src else None})
    content = {**empty_content(), "summary": str(p.get("summary") or "").strip(),
               "regime": str(p.get("regime") or "") or None, "facts": facts,
               "interpretations": [{"text": str(x), "by": "owner"} for x in _list_lines(p.get("interpretations"))],
               "counter_scenarios": [str(x) for x in _list_lines(p.get("counter_scenarios"))],
               "next_checks": [str(x) for x in _list_lines(p.get("next_checks"))],
               "provenance": "owner", "checked_at": utcnow().isoformat()}
    if kind == "close":
        ok = bool(p.get("close_verified"))
        content["data_status"] = [{"item": "KRX 공식 마감 수치", "required": True, "status": "ok" if ok else "missing",
                                   "detail": "소유자가 KRX 공식 출처로 확인했다고 표시" if ok else "확인 표시 없음"}]
    b = save_version(db, d, kind, "draft", content, actor)
    if p.get("publish"):
        try:
            b = publish_draft(db, b.id, actor)
        except BriefingError as e:
            raise CommandError(f"발행 불가: {e}") from None
        return "브리핑을 발행했습니다.", f"/briefing/{b.id}"
    return "초안을 저장했습니다. 검토 후 발행하세요.", f"/briefing/{b.id}"


def _list_lines(v) -> list:  # noqa: ANN001
    if isinstance(v, list):
        return [x for x in v if str(x).strip()]
    return [x.strip() for x in str(v or "").splitlines() if x.strip()]


def h_briefing_publish(db, p, actor):  # noqa: ANN001, ANN201
    from app.services.briefings import BriefingError, publish_draft
    try:
        b = publish_draft(db, _int(p.get("id"), "id"), actor)
    except BriefingError as e:
        raise CommandError(f"발행 불가: {e}") from None
    return "발행했습니다.", f"/briefing/{b.id}"


def h_macro(db, p, actor):  # noqa: ANN001, ANN201
    from app.services.macro_outlook import add_revision
    try:
        add_revision(db, str(p.get("quarter_id") or "").strip().upper(),
                     data_as_of=_date(p.get("data_as_of"), "data_as_of"), reason=str(p.get("reason") or ""),
                     content={k: str(p.get(k) or "").strip() for k in ("facts", "interpretation", "korea", "us",
                                                                        "global", "transmission", "risks",
                                                                        "next_checks")},
                     source_ids=resolve_source_ids(db, p.get("source_ids")), actor=actor)
    except ValueError as e:
        raise CommandError(str(e)) from None
    return "분기 전망 판을 추가했습니다(이전 판 보존).", "/big-picture"


def h_experiment_register(db, p, actor):  # noqa: ANN001, ANN201
    from app.paper.lifecycle import LifecycleError, register
    name = str(p.get("name") or "").strip()
    if not 1 <= len(name) <= 100:
        raise CommandError("실험 이름(1~100자)이 필요합니다.")
    p = {**p, "name": name}
    cfg = {k: _int(p.get(k), k) for k in ("initial_capital", "duration_trading_days", "max_positions",
                                          "max_position_bp", "cost_bps")}
    try:
        exp = register(db, str(p.get("name") or ""), cfg, actor)
    except LifecycleError as e:
        raise CommandError(str(e)) from None
    return "실험을 등록했습니다. 첫 실제 판단 거래일이 시작일이 됩니다.", f"/paper/{exp.id}"


def h_experiment_discard(db, p, actor):  # noqa: ANN001, ANN201
    from app.paper.lifecycle import LifecycleError, discard
    exp = db.get(Experiment, _int(p.get("id"), "id"))
    if exp is None:
        raise CommandError("실험이 없습니다.")
    try:
        discard(db, exp, str(p.get("reason") or ""), actor)
    except LifecycleError as e:
        raise CommandError(str(e)) from None
    return "실험을 폐기했습니다(원장 보존).", f"/paper/{exp.id}"


def h_import_briefs(db, p, actor):  # noqa: ANN001, ANN201
    from app.services.portability import ImportError_, import_briefs_json
    content = p.get("content")
    raw = (json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else content).encode("utf-8")
    try:
        run = import_briefs_json(db, raw, str(p.get("filename") or "briefs.json"), dry_run=not p.get("apply"),
                                 actor=actor)
    except ImportError_ as e:
        raise CommandError(f"가져오기 실패: {e}") from None
    return f"{'[미리보기] ' if run.dry_run else ''}briefs.json: {json.dumps(run.report, ensure_ascii=False)[:400]}", \
        "/admin"


HANDLERS = {
    "schedule.set": h_schedule,
    "calendar.year": h_calendar_year,
    "calendar.exception": h_calendar_exception,
    "naver.validation": h_naver,
    "instrument.update": h_instrument,
    "source.add": h_source,
    "research.submit": h_research,
    "hypothesis.issue": h_hypothesis,
    "hypothesis.invalidate": h_hyp_invalidate,
    "hypothesis.next_check": h_hyp_next_check,
    "briefing.draft": h_briefing_draft,
    "briefing.publish": h_briefing_publish,
    "macro.revision": h_macro,
    "experiment.register": h_experiment_register,
    "experiment.discard": h_experiment_discard,
    "import.briefs": h_import_briefs,
}


def apply_command(db: Session, kind: str, payload: dict, actor: str) -> tuple[str, str]:
    handler = HANDLERS.get(kind)
    if handler is None:
        raise CommandError(f"알 수 없거나 허용되지 않은 명령: {kind}")
    if not isinstance(payload, dict):
        raise CommandError("payload는 객체여야 합니다.")
    return handler(db, payload, actor)


def queue_command(kind: str, payload: dict, inbox: Path | None = None) -> Path:
    """편집기 모드: 제출 파일을 inbox/에 저장한다(DB는 건드리지 않음)."""
    if kind not in HANDLERS:
        raise CommandError(f"제출할 수 없는 명령: {kind}")
    inbox = inbox or inbox_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    # 처리 순서 = 파일 이름 순서. 앞서 만든 제출 파일보다 항상 뒤에 오도록 이름을 정한다(ref: 참조 순서 보장).
    stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S-%f")
    existing = [p.name[:22] for p in pending_files(inbox) if re.match(r"\d{8}-\d{6}-\d{6}", p.name)]
    if existing and max(existing) >= stamp:
        last = datetime.strptime(max(existing), "%Y%m%d-%H%M%S-%f")
        stamp = (last + timedelta(microseconds=1)).strftime("%Y%m%d-%H%M%S-%f")
    path = inbox / f"{stamp}-{secrets.token_hex(2)}-{kind.replace('.', '-')}.json"
    path.write_text(json.dumps({"commands": [{"kind": kind, "payload": payload}],
                                "submitted_at": datetime.now(KST).isoformat(timespec="seconds")},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def parse_inbox_file(raw: bytes) -> list[dict]:
    if len(raw) > MAX_INBOX_BYTES:
        raise CommandError("제출 파일이 너무 큽니다(2MB 이하).")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise CommandError(f"JSON 형식 오류: {e}") from None
    if isinstance(data, dict) and "commands" in data:
        cmds = data["commands"]
    elif isinstance(data, dict) and "kind" in data:
        cmds = [data]
    else:
        raise CommandError('{"commands": [{"kind": ..., "payload": {...}}]} 형식이어야 합니다.')
    if not isinstance(cmds, list) or not cmds:
        raise CommandError("commands가 비어 있습니다.")
    for c in cmds:
        if not isinstance(c, dict) or c.get("kind") not in HANDLERS:
            raise CommandError(f"허용되지 않은 명령: {c.get('kind') if isinstance(c, dict) else c!r}")
    return cmds


def pending_files(inbox: Path | None = None) -> list[Path]:
    inbox = inbox or inbox_dir()
    return sorted(inbox.glob("*.json")) if inbox.exists() else []


def preview_command(db: Session, kind: str, payload: dict, actor: str, inbox: Path | None = None) -> str:
    """편집기 모드의 미리 검사: 아직 처리되지 않은 제출 파일을 먼저 가상 적용한 뒤 새 명령을 검사한다.
    호출한 쪽이 반드시 db.rollback()으로 되돌린다(아무것도 저장하지 않음)."""
    from app.db import begin_real_transaction
    begin_real_transaction(db)
    for path in pending_files(inbox):
        try:
            cmds = parse_inbox_file(path.read_bytes())
        except CommandError:
            continue
        sp = db.begin_nested()
        try:
            for c in cmds:
                apply_command(db, c["kind"], c.get("payload") or {}, actor)
            sp.commit()
        except Exception:  # noqa: BLE001 - 이전 제출 파일의 실패는 여기서 판단하지 않는다(실행기가 판단)
            sp.rollback()
    msg, _ = apply_command(db, kind, payload, actor)
    return msg
