"""관리자·운영 화면과 쓰기 작업. 모든 POST는 require_csrf를 통과해야 한다."""
from __future__ import annotations

import json
from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.commands import (
    CommandError, apply_command, inbox_dir, parse_inbox_file, pending_files, preview_command, queue_command,
)
from app.config import get_settings
from app.db import get_db
from app.jobs import JOBS, next_run_times, run_job
from app.models import (
    AuditLog, CalendarException, CalendarYear, Experiment, FetchLog, Holding, Hypothesis, ImportRun,
    Instrument, JobLock, JobRun, PaperEvent, ResearchVersion, Source,
)
from app.paper.ledger import verify_chain
from app.services import holdings as holdings_svc
from app.services.audit import audit
from app.services.calendar import day_info
from app.services.hypotheses import AXES, AXIS_LABEL
from app.services.market_data import provider_status, sync_dart_corp_codes, sync_official_daily
from app.services.portability import ImportError_, backup_sqlite, import_briefs_json, import_holdings_json, verify_backup
from app.services.research import ITEMS
from app.services.settings_store import get_setting
from app.services.system_status import overview
from app.timeutil import KST, today_kst
from app.web.deps import actor, page, require_csrf

router = APIRouter(prefix="/admin")
W = [Depends(require_csrf)]


def back(path: str, msg: str, ok: bool = True) -> RedirectResponse:
    sep = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{sep}ok={int(ok)}&msg={quote(msg[:500])}", status_code=303)


def _int(v, default=None):  # noqa: ANN001, ANN202
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


@router.get("", response_class=HTMLResponse)
def admin_home(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    today = today_kst()
    years = db.scalars(select(CalendarYear).order_by(CalendarYear.year.desc())).all()
    exceptions = db.scalars(select(CalendarException).order_by(CalendarException.day.desc()).limit(40)).all()
    runs = db.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(40)).all()
    last_success = {n: db.scalar(select(func.max(JobRun.finished_at)).where(JobRun.job_name == n,
                                                                          JobRun.status == "succeeded")) for n in JOBS}
    exps = db.scalars(select(Experiment).order_by(Experiment.id.desc())).all()
    integrity = {e.id: verify_chain(db, e.id) for e in exps}
    ops = db.scalars(select(PaperEvent).where(PaperEvent.type.in_(["ops_error", "expiry", "cancel"]))
                     .order_by(PaperEvent.id.desc()).limit(20)).all()
    decisions = db.scalars(select(PaperEvent).where(PaperEvent.type == "decision").order_by(PaperEvent.id.desc())
                           .limit(5)).all()
    s = get_settings()
    keys = {"KRX_API_KEY": bool(s.krx_api_key), "DART_API_KEY": bool(s.dart_api_key),
            "GEMINI_API_KEY": bool(s.gemini_api_key), "GEMINI_MODEL": bool(s.gemini_model),
            "FRED_API_KEY": bool(s.fred_api_key), "NAVER_QUOTE_ENABLED": s.naver_quote_enabled}
    backups = sorted(s.backup_dir.glob("*.db"), reverse=True)[:10] if s.backup_dir.exists() else []
    return page(request, "admin.html", ov=overview(db), today=today, today_info=day_info(db, today), years=years,
                exceptions=exceptions, runs=runs, last_success=last_success, jobs=JOBS, next_runs=next_run_times(db),
                locks=db.scalars(select(JobLock)).all(), providers=provider_status(db), keys=keys, exps=exps,
                integrity=integrity, ops=ops, decisions=decisions, schedule=get_setting(db, "schedule"),
                naver=get_setting(db, "naver_validation"), paper_defaults=get_setting(db, "paper_defaults"),
                imports=db.scalars(select(ImportRun).order_by(ImportRun.id.desc()).limit(10)).all(), backups=backups,
                audits=db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(30)).all(),
                sources=db.scalars(select(Source).order_by(Source.id.desc()).limit(30)).all(),
                fetches=db.scalars(select(FetchLog).order_by(FetchLog.id.desc()).limit(30)).all(),
                msg=request.query_params.get("msg"), ok=request.query_params.get("ok") == "1")


# ---------------------------------------------------------------- 명령 제출(직접 반영 / 편집기 모드)

def _lines(v) -> list[str]:  # noqa: ANN001
    return [x.strip() for x in str(v or "").splitlines() if x.strip()]


def _on(f, key: str) -> bool:  # noqa: ANN001
    return f.get(key) == "on"


def _editor_block() -> RedirectResponse | None:
    if get_settings().is_editor:
        return back("/admin", "편집기 모드에서는 공개 원장을 바꾸는 작업(작업 실행·가격 동기화 등)은 GitHub 실행기만 합니다.",
                    False)
    return None


async def submit(request: Request, db: Session, kind: str, payload: dict, fail_path: str) -> RedirectResponse:
    """직접 모드: 같은 명령 핸들러로 바로 반영. 편집기 모드: 미리 검사 후 inbox/ 제출 파일로 저장."""
    who = actor(request)
    if get_settings().is_editor:
        try:
            msg = preview_command(db, kind, payload, who)
        except CommandError as e:
            db.rollback()
            return back(fail_path, f"미리 검사 실패(제출하지 않음): {e}", False)
        db.rollback()
        path = queue_command(kind, payload)
        return back("/admin/inbox", f"제출 파일 inbox/{path.name} 을(를) 만들었습니다. GitHub Desktop에서 커밋·푸시하면 "
                                    f"다음 실행 때 반영됩니다. (미리 검사: {msg})")
    try:
        msg, dest = apply_command(db, kind, payload, who)
    except CommandError as e:
        db.rollback()
        return back(fail_path, str(e), False)
    db.commit()
    return back(dest, msg)


@router.get("/inbox", response_class=HTMLResponse)
def inbox_page(request: Request):  # noqa: ANN201
    pending = []
    for p in pending_files():
        try:
            kinds = [c["kind"] for c in parse_inbox_file(p.read_bytes())]
        except CommandError as e:
            kinds = [f"형식 오류: {e}"]
        pending.append({"name": p.name, "kinds": kinds})
    processed = []
    pdir = inbox_dir() / "processed"
    if pdir.exists():
        for p in sorted(pdir.glob("*.result.json"), reverse=True)[:50]:
            try:
                processed.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return page(request, "admin_inbox.html", pending=pending, processed=processed, editor=get_settings().is_editor,
                msg=request.query_params.get("msg"), ok=request.query_params.get("ok") == "1")


# ---------------------------------------------------------------- 설정·캘린더

@router.post("/schedule", dependencies=W)
async def save_schedule(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    return await submit(request, db, "schedule.set",
                        {k: str(f.get(k, "")).strip() for k in ("pre_brief_time", "close_brief_time",
                                                                 "settlement_time")}, "/admin")


@router.post("/calendar/year", dependencies=W)
async def calendar_year(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    return await submit(request, db, "calendar.year",
                        {"year": str(f.get("year", "")).strip(), "source_url": str(f.get("source_url", "")).strip(),
                         "holidays": [x.strip() for x in str(f.get("holidays", "")).replace("\n", ",").split(",")
                                      if x.strip()],
                         "open": str(f.get("open") or "09:00"), "close": str(f.get("close") or "15:30")}, "/admin")


@router.post("/calendar/exception", dependencies=W)
async def calendar_exception(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    return await submit(request, db, "calendar.exception",
                        {k: str(f.get(k) or "").strip() for k in ("day", "kind", "open_time", "close_time",
                                                                  "source_url", "note")}, "/admin")


@router.post("/naver/confirm", dependencies=W)
async def naver_confirm(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    payload = {"confirmed": _on(f, "confirmed")}
    for key in ("allowed_market_status", "allowed_session_types", "allowed_exchange_codes"):
        payload[key] = [x.strip() for x in str(f.get(key, "")).split(",") if x.strip()]
    return await submit(request, db, "naver.validation", payload, "/admin")


@router.post("/jobs/run", dependencies=W)
async def run_now(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    if (blocked := _editor_block()) is not None:
        return blocked
    f = await request.form()
    name = str(f.get("job"))
    if name not in JOBS:
        return back("/admin", "알 수 없는 작업", False)
    now = datetime.now(KST)
    audit(db, actor(request), "job.manual_run", name)
    db.commit()
    run = run_job(name, f"manual:{now.strftime('%Y-%m-%dT%H:%M:%S')}", now.date(), now)
    if run is None:
        return back("/admin", "다른 실행이 잠금을 갖고 있어 시작하지 않았습니다.", False)
    return back("/admin", f"{JOBS[name].label}: {run.status}" + (f" — {run.error.splitlines()[0]}" if run.error else ""),
                run.status in ("succeeded", "skipped"))


# ---------------------------------------------------------------- 데이터

@router.post("/official/sync", dependencies=W)
async def official_sync(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    if (blocked := _editor_block()) is not None:
        return blocked
    f = await request.form()
    try:
        d = date.fromisoformat(str(f.get("day")))
    except ValueError:
        return back("/admin", "날짜 형식 오류", False)
    summary = sync_official_daily(db, d)
    audit(db, actor(request), "official.sync", d.isoformat(), {"results": str(summary["results"])[:500]})
    db.commit()
    return back("/admin", f"공식 가격 동기화 {d}: {json.dumps(summary['results'], ensure_ascii=False)[:300]}")


@router.post("/dart/corp-codes", dependencies=W)
async def dart_corp_codes(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    if (blocked := _editor_block()) is not None:
        return blocked
    n, err = sync_dart_corp_codes(db, actor(request))
    db.commit()
    if err:
        return back("/admin", f"DART 매핑 실패: {err}", False)
    return back("/admin", f"DART 고유번호 {n}건을 연결했습니다.")


@router.post("/instrument", dependencies=W)
async def instrument_update(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    return await submit(request, db, "instrument.update",
                        {"code": str(f.get("code", "")).strip(), "sector": str(f.get("sector", "")).strip(),
                         "managed_issue": _on(f, "managed_issue"), "is_etf": _on(f, "is_etf")}, "/admin")


@router.post("/sources", dependencies=W)
async def source_add(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    payload = {k: str(f.get(k) or "").strip() for k in ("url", "title", "provider", "grade", "origin_key",
                                                        "checked_at", "published_at", "note", "ref")}
    payload["is_company_claim"] = _on(f, "is_company_claim")
    return await submit(request, db, "source.add", payload, "/admin")


# ---------------------------------------------------------------- 리서치·가설

@router.get("/research/new", response_class=HTMLResponse)
def research_form(request: Request, code: str = "", db: Session = Depends(get_db)):  # noqa: ANN201
    return page(request, "admin_research.html", code=code, items=ITEMS, inst=db.get(Instrument, code) if code else None,
                msg=request.query_params.get("msg"), ok=request.query_params.get("ok") == "1")


@router.post("/research", dependencies=W)
async def research_submit(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    code = str(f.get("code", "")).strip()
    payload = {"code": code,
               "items": {k: {"conclusion": str(f.get(f"{k}_conclusion", "")), "verified": _on(f, f"{k}_verified"),
                             "source_ids": str(f.get(f"{k}_sources", ""))} for k, _ in ITEMS},
               "liquidity_ok": _on(f, "liquidity_ok"), "red_flags": _lines(f.get("red_flags")),
               "next_checks": _lines(f.get("next_checks"))}
    return await submit(request, db, "research.submit", payload, f"/admin/research/new?code={code}")


@router.get("/hypothesis/new", response_class=HTMLResponse)
def hypothesis_form(request: Request, code: str = "", db: Session = Depends(get_db)):  # noqa: ANN201
    rv = db.scalar(select(ResearchVersion).where(ResearchVersion.code == code).order_by(ResearchVersion.id.desc())
                   .limit(1)) if code else None
    return page(request, "admin_hypothesis.html", code=code, rv=rv, axes=AXES, axis_label=AXIS_LABEL,
                msg=request.query_params.get("msg"), ok=request.query_params.get("ok") == "1")


@router.post("/hypothesis", dependencies=W)
async def hypothesis_issue(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    code = str(f.get("code", "")).strip()
    payload = {k: str(f.get(k, "")) for k in ("code", "catalyst", "not_priced_reason", "counter_scenario",
                                              "invalidation", "next_check", "direction", "confidence")}
    payload.update({"research_version_id": str(f.get("research_version_id") or "latest"),
                    "scores": {a: str(f.get(f"score_{a}", "")) for a in AXES},
                    "source_ids": str(f.get("source_ids", "")), "flow_source_ids": str(f.get("flow_source_ids", "")),
                    "macro_headwind": _on(f, "macro_headwind")})
    return await submit(request, db, "hypothesis.issue", payload, f"/admin/hypothesis/new?code={code}")


@router.post("/hypothesis/{hid}/invalidate", dependencies=W)
async def hypothesis_invalidate(hid: int, request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    h = db.get(Hypothesis, hid)
    if h is None:
        raise HTTPException(404, "가설이 없습니다.")
    return await submit(request, db, "hypothesis.invalidate", {"hypothesis": h.uid, "reason": str(f.get("reason", ""))},
                        "/research")


@router.post("/hypothesis/{hid}/next-check", dependencies=W)
async def hypothesis_next_check(hid: int, request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    h = db.get(Hypothesis, hid)
    if h is None:
        raise HTTPException(404, "가설이 없습니다.")
    return await submit(request, db, "hypothesis.next_check",
                        {"hypothesis": h.uid, "next_check": str(f.get("next_check", "")).strip()}, "/research")


# ---------------------------------------------------------------- 브리핑·거시

@router.get("/briefing/new", response_class=HTMLResponse)
def briefing_form(request: Request):  # noqa: ANN201
    return page(request, "admin_briefing.html", today=today_kst(), msg=request.query_params.get("msg"),
                ok=request.query_params.get("ok") == "1")


@router.post("/briefing", dependencies=W)
async def briefing_save(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    facts = []
    for line in str(f.get("facts", "")).splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3 and parts[0]:
            facts.append({"text": parts[0], "value": parts[1], "source": parts[2]})
    payload = {"trading_date": str(f.get("trading_date", "")), "kind": str(f.get("kind", "")),
               "summary": str(f.get("summary", "")).strip(), "regime": str(f.get("regime", "")), "facts": facts,
               "interpretations": _lines(f.get("interpretations")), "counter_scenarios": _lines(f.get("counter")),
               "next_checks": _lines(f.get("next_checks")), "close_verified": _on(f, "close_verified"),
               "publish": _on(f, "publish_now")}
    return await submit(request, db, "briefing.draft", payload, "/admin/briefing/new")


@router.post("/briefing/{bid}/publish", dependencies=W)
async def briefing_publish(bid: int, request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    return await submit(request, db, "briefing.publish", {"id": bid}, f"/briefing/{bid}")


@router.get("/macro/new", response_class=HTMLResponse)
def macro_form(request: Request):  # noqa: ANN201
    return page(request, "admin_macro.html", today=today_kst(), msg=request.query_params.get("msg"),
                ok=request.query_params.get("ok") == "1")


@router.post("/macro", dependencies=W)
async def macro_save(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    payload = {k: str(f.get(k, "")).strip() for k in ("quarter_id", "data_as_of", "reason", "facts", "interpretation",
                                                      "korea", "us", "global", "transmission", "risks", "next_checks",
                                                      "source_ids")}
    return await submit(request, db, "macro.revision", payload, "/admin/macro/new")


# ---------------------------------------------------------------- 모의운용

@router.post("/experiments", dependencies=W)
async def experiment_register(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    payload = {k: str(f.get(k, "")) for k in ("name", "initial_capital", "duration_trading_days", "max_positions",
                                              "max_position_bp", "cost_bps")}
    return await submit(request, db, "experiment.register", payload, "/paper")


@router.post("/experiments/{eid}/discard", dependencies=W)
async def experiment_discard(eid: int, request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    if db.get(Experiment, eid) is None:
        raise HTTPException(404, "실험이 없습니다.")
    return await submit(request, db, "experiment.discard", {"id": eid, "reason": str(f.get("reason", ""))},
                        f"/paper/{eid}")


# ---------------------------------------------------------------- 보유종목

@router.post("/holdings", dependencies=W)
async def holding_add(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    try:
        h = holdings_svc.add_holding(db, str(f.get("code", "")), str(f.get("name", "")) or None, actor(request))
        holdings_svc.record_transaction(db, h, trade_date=date.fromisoformat(str(f.get("trade_date"))), side="buy",
                                        quantity=_int(f.get("quantity"), -1), unit_price=_int(f.get("unit_price"), -1),
                                        note=str(f.get("note", "")) or None, actor=actor(request))
    except (holdings_svc.HoldingError, ValueError) as e:
        db.rollback()
        return back("/holdings", str(e), False)
    db.commit()
    return back("/holdings", "보유종목을 추가했습니다.")


@router.post("/holdings/{hid}/tx", dependencies=W)
async def holding_tx(hid: int, request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    h = db.get(Holding, hid)
    if h is None:
        raise HTTPException(404, "보유종목이 없습니다.")
    try:
        holdings_svc.record_transaction(db, h, trade_date=date.fromisoformat(str(f.get("trade_date"))),
                                        side=str(f.get("side")), quantity=_int(f.get("quantity"), -1),
                                        unit_price=_int(f.get("unit_price"), -1), note=str(f.get("note", "")) or None,
                                        actor=actor(request), voids_id=_int(f.get("voids_id")))
    except (holdings_svc.HoldingError, ValueError) as e:
        db.rollback()
        return back("/holdings", str(e), False)
    db.commit()
    return back("/holdings", "거래 기록을 추가했습니다(이전 기록 보존).")


# ---------------------------------------------------------------- 가져오기·백업

@router.post("/import", dependencies=W)
async def import_file(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    f = await request.form()
    upload = f.get("file")
    kind = str(f.get("kind"))
    dry = f.get("apply") != "on"
    if upload is None or not hasattr(upload, "read"):
        return back("/admin", "파일을 선택하세요.", False)
    raw = await upload.read()
    if kind == "briefs" and get_settings().is_editor:
        return await submit(request, db, "import.briefs", {"content": raw.decode("utf-8-sig", "replace"),
                                                           "filename": upload.filename or "briefs.json",
                                                           "apply": not dry}, "/admin")
    try:
        if kind == "briefs":
            run = import_briefs_json(db, raw, upload.filename or "briefs.json", dry_run=dry, actor=actor(request))
            msg = f"{'[미리보기] ' if dry else ''}briefs.json: {json.dumps(run.report, ensure_ascii=False)[:400]}"
        else:
            rep = import_holdings_json(db, raw, upload.filename or "holdings.json", dry_run=dry, actor=actor(request))
            msg = f"{'[미리보기] ' if dry else ''}보유종목: {json.dumps(rep, ensure_ascii=False)}"
    except (ImportError_, ValueError, KeyError, holdings_svc.HoldingError) as e:
        db.rollback()
        return back("/admin", f"가져오기 실패: {e}", False)
    if dry and kind != "briefs":
        db.rollback()  # 보유종목 미리보기는 아무것도 저장하지 않음
    else:
        db.commit()  # briefs 미리보기는 import_runs에 dry-run 보고서만 남김
    return back("/admin", msg)


@router.post("/backup", dependencies=W)
async def backup_create(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    try:
        info = backup_sqlite("manual")
        check = verify_backup(info["path"])
    except RuntimeError as e:
        return back("/admin", str(e), False)
    audit(db, actor(request), "backup.create", info["file"], {"verified": check["ok"]})
    db.commit()
    return back("/admin", f"백업 {info['file']} 생성, 복구 검증 {'통과' if check['ok'] else '실패'}", check["ok"])
