"""
관리자 화면용 상태 집계.

'배포 성공', 'AI 연결', '자료 수집 성공', '판단 성공', '검증된 모의체결 성공'을 따로 계산한다.
liveExecutionVerified는 (1) 적격 원본 관측에 연결된 fill이 있고 (2) 그 실험의 해시 체인이 검증되며
(3) 그 fill 이후 성공한 정시 감사 작업이 있을 때만 true다.
주문이 없다는 사실만으로 장애로 판단하지 않는다.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Experiment, FetchLog, JobRun, Observation, PaperEvent
from app.paper.ledger import verify_chain
from app.providers.ai import GeminiProvider
from app.timeutil import fmt_kst


def live_execution_verified(db: Session) -> tuple[bool, str]:
    fills = db.scalars(select(PaperEvent).where(PaperEvent.type == "fill").order_by(PaperEvent.id)).all()
    if not fills:
        return False, "검증된 모의체결 없음"
    for f in fills:
        obs = db.get(Observation, f.payload.get("observation_id"))
        if obs is None or not obs.eligible:
            continue
        ok, _ = verify_chain(db, f.experiment_id)
        if not ok:
            continue
        audit_after = db.scalar(select(JobRun).where(JobRun.job_name == "intraday_audit", JobRun.status == "succeeded",
                                                     JobRun.finished_at > f.at).limit(1))
        if audit_after is None:
            return False, "체결은 있으나 이후 감사 통과 기록 없음"
        return True, f"실험 {f.experiment_id} fill seq {f.seq}: 적격 관측·체인·감사 통과"
    return False, "적격 관측에 연결된 체결 없음"


def overview(db: Session) -> dict:
    s = get_settings()
    ai = GeminiProvider()
    last_ok_fetch = db.scalar(select(func.max(FetchLog.requested_at)).where(FetchLog.status == "ok"))
    last_decision = db.scalar(select(PaperEvent).where(PaperEvent.type == "decision").order_by(PaperEvent.id.desc())
                              .limit(1))
    active = db.scalar(select(func.count()).select_from(Experiment).where(
        Experiment.status.in_(["첫판단대기", "운용중"]))) or 0
    lev, lev_msg = live_execution_verified(db)
    last_ops = db.scalar(select(PaperEvent).where(PaperEvent.type == "ops_error").order_by(PaperEvent.id.desc())
                         .limit(1))
    if active == 0:
        engine_state = ("정상 대기", "진행 중인 실험이 없습니다.")
    elif last_ops is not None and (last_decision is None or last_ops.id > last_decision.id):
        engine_state = ("운영 오류", last_ops.payload.get("reason", ""))
    else:
        engine_state = ("운용 중", "진행 중인 실험이 있습니다.")
    return {
        "deploy": {"ok": True, "detail": f"환경 {s.env}, DB {s.database_url.split(':', 1)[0]}"},
        "ai": {"ok": ai.configured, "detail": "설정됨(호출 성공 여부는 아래 작업 기록 참조)" if ai.configured
               else "연결 필요(GEMINI_API_KEY·GEMINI_MODEL)"},
        "collection": {"ok": last_ok_fetch is not None, "detail": f"마지막 성공 {fmt_kst(last_ok_fetch)}" if last_ok_fetch
                       else "성공한 자료 수집 기록 없음"},
        "judgement": {"ok": last_decision is not None,
                      "detail": f"마지막 판단 {fmt_kst(last_decision.at)}" if last_decision else "판단 기록 없음"},
        "verified_fill": {"ok": lev, "detail": lev_msg},
        "liveExecutionVerified": lev,
        "engine_state": engine_state,
    }
