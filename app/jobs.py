"""
예약 작업 정의와 실행 규칙.

각 작업: 실행 잠금(job_locks, 만료시간 있음) · 멱등성 키(job_name + slot_key 유일) · 중복 실행 방지 ·
재시도(실패 시 같은 슬롯에서 최대 N회) · 시간 제한(외부 호출 타임아웃 + 초과 기록) · 실패 로그 ·
실제 시작·완료 시각 · 작업별 쓰기 권한(scope).

'예약이 등록됨'과 '실제 성공'은 job_runs.status로 구분된다.
"""
from __future__ import annotations

import os
import socket
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models import Experiment, JobLock, JobRun
from app.services.calendar import day_info, is_in_regular_session
from app.services.settings_store import get_setting
from app.timeutil import KST, now_kst, to_kst, utcnow

HOLDER = f"{socket.gethostname()}:{os.getpid()}"


@dataclass(frozen=True)
class JobSpec:
    name: str
    label: str
    scopes: frozenset[str]
    timeout_sec: int
    max_attempts: int
    run: Callable[[Session, date, datetime], dict]


# ---------------------------------------------------------------- 잠금

def acquire_lock(db: Session, name: str, ttl_sec: int, holder: str = HOLDER) -> bool:
    now = utcnow()
    lock = db.get(JobLock, name)
    if lock is None:
        db.add(JobLock(name=name, holder=holder, acquired_at=now, expires_at=now + timedelta(seconds=ttl_sec)))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return False
        return True
    if lock.expires_at < now or lock.holder == holder:
        lock.holder, lock.acquired_at, lock.expires_at = holder, now, now + timedelta(seconds=ttl_sec)
        db.commit()
        return True
    return False


def release_lock(db: Session, name: str, holder: str = HOLDER) -> None:
    lock = db.get(JobLock, name)
    if lock is not None and lock.holder == holder:
        db.delete(lock)
        db.commit()


# ---------------------------------------------------------------- 작업 본문

def _job_pre_brief(db: Session, day: date, now: datetime) -> dict:
    from app.services.briefings import run_pre_brief
    return run_pre_brief(db, day)


def _job_close(db: Session, day: date, now: datetime) -> dict:
    from app.services.briefings import run_close_brief
    from app.services.evaluation import evaluate_due
    from app.services.market_data import sync_official_daily
    sync = sync_official_daily(db, day)
    db.commit()
    brief = run_close_brief(db, day)
    db.commit()
    ev = evaluate_due(db, day)
    return {"official_sync": sync, "brief": brief, "evaluation": ev}


def _job_intraday_audit(db: Session, day: date, now: datetime) -> dict:
    """정시 감사: 원장 무결성 확인과 상태 기록만 한다. 판단·주문·체결을 만들지 않는다."""
    from app.paper.ledger import verify_chain
    results = {}
    for exp in db.scalars(select(Experiment)):
        ok, msg = verify_chain(db, exp.id)
        results[str(exp.id)] = {"chain_ok": ok, "detail": msg, "status": exp.status}
    return {"experiments": results}


def _job_paper_engine(db: Session, day: date, now: datetime) -> dict:
    from app.paper.engine import Engine
    out = {}
    engine = Engine(db)
    for exp in db.scalars(select(Experiment).where(Experiment.status.in_(["첫판단대기", "운용중"]),
                                                   Experiment.execution_owner == "engine")):
        lock_name = f"experiment:{exp.id}"
        if not acquire_lock(db, lock_name, ttl_sec=600):
            out[str(exp.id)] = {"result": "skipped", "reason": "다른 작업이 이 실험을 운용 중"}
            continue
        try:
            out[str(exp.id)] = engine.run_cycle(exp)
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            out[str(exp.id)] = {"result": "error", "error": type(e).__name__}
        finally:
            release_lock(db, lock_name)
    return out


def _job_settlement(db: Session, day: date, now: datetime) -> dict:
    from app.paper.lifecycle import end_and_settle
    out = {}
    for exp in db.scalars(select(Experiment).where(Experiment.status.in_(["운용중", "정산대기"]))):
        lock_name = f"experiment:{exp.id}"
        if not acquire_lock(db, lock_name, ttl_sec=600):
            out[str(exp.id)] = {"result": "skipped", "reason": "잠금 중"}
            continue
        try:
            out[str(exp.id)] = end_and_settle(db, exp, day)
            db.commit()
        finally:
            release_lock(db, lock_name)
    return out


JOBS: dict[str, JobSpec] = {
    "pre_brief": JobSpec("pre_brief", "개장 전 브리핑", frozenset({"briefing.write"}), 600, 3, _job_pre_brief),
    "close": JobSpec("close", "장 마감 후 브리핑·공식가격·가설평가",
                     frozenset({"briefing.write", "prices.write", "hypothesis.evaluate"}), 1200, 3, _job_close),
    "intraday_audit": JobSpec("intraday_audit", "장중 감사·동기화", frozenset({"audit.read"}), 300, 2,
                              _job_intraday_audit),
    "paper_engine": JobSpec("paper_engine", "모의판단 엔진", frozenset({"paper.trade"}), 540, 1, _job_paper_engine),
    "settlement": JobSpec("settlement", "마감 후 모의운용 정산", frozenset({"paper.settle"}), 600, 3, _job_settlement),
}


# ---------------------------------------------------------------- 실행

def run_job(name: str, slot_key: str, day: date, now: datetime) -> JobRun | None:
    spec = JOBS[name]
    with session_scope() as db:
        run = db.scalar(select(JobRun).where(JobRun.job_name == name, JobRun.slot_key == slot_key))
        if run is not None:
            if run.status in ("succeeded", "skipped"):
                return run  # 멱등성: 같은 슬롯은 다시 실행하지 않음
            if run.status == "running" and run.started_at and run.started_at > utcnow() - timedelta(
                    seconds=spec.timeout_sec * 2):
                return run
            if run.attempts >= spec.max_attempts:
                return run
        else:
            run = JobRun(job_name=name, slot_key=slot_key, status="pending", attempts=0)
            db.add(run)
            try:
                db.flush()
            except IntegrityError:
                return None
        run_id = run.id

    with session_scope() as db:
        if not acquire_lock(db, f"job:{name}", ttl_sec=spec.timeout_sec * 2):
            return None
    try:
        with session_scope() as db:
            run = db.get(JobRun, run_id)
            run.status, run.attempts, run.started_at, run.error = "running", run.attempts + 1, utcnow(), None
        start = utcnow()
        try:
            with session_scope() as db:
                summary = spec.run(db, day, now)
            elapsed = (utcnow() - start).total_seconds()
            with session_scope() as db:
                run = db.get(JobRun, run_id)
                skipped = isinstance(summary, dict) and summary.get("result") == "skipped"
                run.status = "skipped" if skipped else "succeeded"
                run.finished_at = utcnow()
                run.summary = {**(summary if isinstance(summary, dict) else {"value": str(summary)}),
                               "elapsed_sec": round(elapsed, 1),
                               "timeout_exceeded": elapsed > spec.timeout_sec}
        except Exception as e:  # noqa: BLE001
            with session_scope() as db:
                run = db.get(JobRun, run_id)
                run.status, run.finished_at = "failed", utcnow()
                run.error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"[:4000]
        with session_scope() as db:
            return db.get(JobRun, run_id)
    finally:
        with session_scope() as db:
            release_lock(db, f"job:{name}")


def _at(day: date, hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return datetime.combine(day, time(int(h), int(m)), tzinfo=KST)


def due_slots(db: Session, moment: datetime | None = None) -> list[tuple[str, str]]:
    """지금 실행해야 할 (작업, 슬롯) 목록. 놓친 일일 작업은 같은 날 안에서만 따라잡는다."""
    now = to_kst(moment) if moment else now_kst()
    day = now.date()
    sched = get_setting(db, "schedule")
    info = day_info(db, day)
    out: list[tuple[str, str]] = []
    if info.status == "unknown":
        return out
    if info.is_trading:
        if sched.get("pre_brief_time") and now >= _at(day, sched["pre_brief_time"]):
            out.append(("pre_brief", day.isoformat()))
        if sched.get("close_brief_time") and now >= _at(day, sched["close_brief_time"]):
            out.append(("close", day.isoformat()))
        if sched.get("settlement_time") and now >= _at(day, sched["settlement_time"]):
            out.append(("settlement", day.isoformat()))
        for h in sched.get("intraday_audit_hours", []):
            slot = datetime.combine(day, time(int(h), 0), tzinfo=KST)
            if slot <= now < slot + timedelta(minutes=50):
                out.append(("intraday_audit", slot.strftime("%Y-%m-%dT%H:%M")))
        if is_in_regular_session(db, now):
            step = int(sched.get("engine_interval_min", 10))
            minute = (now.minute // step) * step
            out.append(("paper_engine", now.replace(minute=minute, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")))
    return out


def next_run_times(db: Session, moment: datetime | None = None) -> dict[str, str]:
    now = to_kst(moment) if moment else now_kst()
    sched = get_setting(db, "schedule")
    out = {}
    for name, key in (("pre_brief", "pre_brief_time"), ("close", "close_brief_time"), ("settlement", "settlement_time")):
        if not sched.get(key):
            out[name] = "시각 미설정(관리자 설정 필요)"
            continue
        out[name] = _next_trading_at(db, now, sched[key])
    return out


def _next_trading_at(db: Session, now: datetime, hhmm: str) -> str:
    d = now.date()
    for _ in range(15):
        info = day_info(db, d)
        if info.status == "unknown":
            return f"{d.isoformat()} 이후 캘린더 미확인"
        if info.is_trading and _at(d, hhmm) > now:
            return _at(d, hhmm).strftime("%Y-%m-%d %H:%M KST")
        d += timedelta(days=1)
    return "15일 안에 거래일 없음"
