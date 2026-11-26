"""
내보내기·가져오기·백업·복구.

내보내기: DB(정본)에서 호환 JSON(briefs.json 형식의 최상위 키)을 생성한다.
가져오기: 파일이 제공될 때만. 형식·크기 검증 → 원본 백업 → dry-run → 중복(해시) 확인 →
          매핑·충돌 보고 → 원자적 적용 → import 감사 기록. 과거 기록은 legacy_records에 읽기 전용으로
          저장하며, 품질군·연구 통과·체결 인증을 소급 부여하지 않는다.
백업: SQLite는 온라인 백업 API로 파일 복사 + SHA-256 매니페스트. 복구 검증은 사본을 열어
      무결성 검사와 원장 해시 체인 검증까지 수행한다. PostgreSQL은 pg_dump 절차(guide/BACKUP.md).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import (
    Briefing, Experiment, Hypothesis, HypothesisEvaluation, ImportRun, LegacyRecord, MacroRevision, ResearchVersion,
)
from app.services.audit import audit
from app.services.sources import canonical_json, sha256_text
from app.timeutil import today_kst, utcnow

MAX_IMPORT_BYTES = 20 * 1024 * 1024
COMPAT_KEYS = ("pre", "close", "archive", "deepResearch", "hypotheses", "hypothesisEvaluationPolicy", "performance",
               "performanceHistory", "macroOutlook", "modelPortfolio", "updatedAt", "currentTradingDate",
               "systemRecovery")


# ---------------------------------------------------------------- 내보내기

def _brief(b: Briefing | None) -> dict | None:
    if b is None:
        return None
    return {"id": b.id, "tradingDate": b.trading_date.isoformat(), "kind": b.kind, "version": b.version,
            "status": b.status, "publishedAt": b.published_at.isoformat() if b.published_at else None, **b.content}


def export_compat(db: Session) -> dict:
    from app.paper.ledger import events
    from app.paper.lifecycle import verified_summary
    from app.services.briefings import archive, latest_any, latest_published
    from app.services.evaluation import current_event, statistics
    from app.services.research import current_candidate

    today = today_kst()
    pre = latest_published(db, "pre", today) or latest_any(db, "pre", today)
    close = latest_published(db, "close", today) or latest_any(db, "close", today)
    cand = current_candidate(db)
    research_hist = [{"id": r.id, "code": r.code, "checkedAt": r.checked_at.isoformat(), "status": r.status,
                      "review": {"corePassed": r.core_passed, "fullyVerifiedItems": r.fully_verified_items},
                      "failedReasons": r.failed_reasons, "nextChecks": r.next_checks}
                     for r in db.scalars(select(ResearchVersion).order_by(ResearchVersion.id.desc()))]
    hyps = []
    for h in db.scalars(select(Hypothesis).order_by(Hypothesis.id)):
        evs = {hz: current_event(db, h.id, hz) for hz in ("short", "medium")}
        hyps.append({"id": h.uid, "code": h.code, "name": h.name, "issuedAt": h.issued_at.isoformat(),
                     "evaluationPolicy": h.evaluation_policy, "absoluteDirection": h.direction,
                     "qualityTier": h.quality_tier, "qualityScore": h.quality_score,
                     "scoreBreakdown": h.score_breakdown, "confidence": h.confidence,
                     "researchRef": {"code": h.research_code, "checkedAt": h.research_checked_at.isoformat()},
                     "basePrice": h.base_price, "basePriceId": h.base_price_id, "baseDate": h.base_date.isoformat(),
                     "shortDate": h.short_date.isoformat(), "mediumDate": h.medium_date.isoformat(),
                     "catalyst": h.catalyst, "notPricedReason": h.not_priced_reason,
                     "counterScenario": h.counter_scenario, "invalidation": h.invalidation, "nextCheck": h.next_check,
                     "sourceIds": h.source_ids, "status": h.status,
                     "evaluations": {hz: ({"outcome": e.outcome, "changePct": e.change_pct, "close": e.close,
                                           "judgementDate": e.judgement_date.isoformat()} if e else None)
                                     for hz, e in evs.items()}})
    stats = statistics(db, today)
    macro = []
    from app.services.macro_outlook import list_outlooks
    for item in list_outlooks(db):
        macro.append({"quarter": item["outlook"].quarter_id,
                      "revisions": [{"no": r.revision_no, "createdAt": r.created_at.isoformat(),
                                     "dataAsOf": r.data_as_of.isoformat(), "reason": r.reason, **r.content,
                                     "sourceIds": r.source_ids} for r in item["revisions"]]})
    runs = []
    for exp in db.scalars(select(Experiment).order_by(Experiment.id)):
        runs.append({"id": exp.id, "name": exp.name, "status": exp.status, "executionOwner": exp.execution_owner,
                     "config": {"initialCapital": exp.initial_capital, "durationTradingDays": exp.duration_trading_days,
                                "maxPositions": exp.max_positions, "maxPositionPct": exp.max_position_bp / 100,
                                "costBps": exp.cost_bps, "benchmark": exp.benchmark},
                     "startDate": exp.start_date.isoformat() if exp.start_date else None,
                     "endDate": exp.end_date.isoformat() if exp.end_date else None,
                     "events": [{"seq": e.seq, "type": e.type, "at": e.at.isoformat(), "payload": e.payload,
                                 "hash": e.hash} for e in events(db, exp.id)]})
    return {
        "updatedAt": utcnow().isoformat() + "Z",
        "currentTradingDate": today.isoformat(),
        "pre": _brief(pre), "close": _brief(close),
        "archive": [_brief(b) for b in archive(db, today)],
        "deepResearch": {"candidate": ({"code": cand.code, "checkedAt": cand.checked_at.isoformat(),
                                        "details": cand.items, "sources": sorted({s for it in cand.items.values()
                                                                                  for s in it["source_ids"]})}
                                       if cand else None),
                         "status": "통과" if cand else "관찰 보류", "history": research_hist},
        "hypotheses": hyps,
        "hypothesisEvaluationPolicy": {"id": "absolute-price-v1", "basis": "고정 판정일 KRX 정규장 종가 / 고정 기준가격 − 1",
                                       "note": "비용·배당 전 가격변동, 실제 체결수익과 다름", "minSample": 20},
        "performance": json.loads(json.dumps(stats, default=str)),
        "performanceHistory": [],
        "macroOutlook": macro,
        "modelPortfolio": {"runs": runs, "verified": json.loads(json.dumps(verified_summary(db), default=str))},
        "systemRecovery": {"note": "백업·복구 상태는 관리자 화면 참조"},
    }


# ---------------------------------------------------------------- 백업·복구

def _sqlite_path() -> Path | None:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        return None
    return Path(url.split("///", 1)[1])


def backup_sqlite(label: str = "manual") -> dict:
    src = _sqlite_path()
    if src is None:
        raise RuntimeError("SQLite가 아닌 DB입니다. PostgreSQL은 guide/BACKUP.md의 pg_dump 절차를 사용하세요.")
    out_dir = get_settings().backup_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = out_dir / f"signal-ledger-{stamp}-{label}.db"
    with sqlite3.connect(src) as s, sqlite3.connect(dest) as d:
        s.backup(d)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    manifest = {"file": dest.name, "sha256": digest, "created_at": stamp, "label": label}
    dest.with_suffix(".json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**manifest, "path": str(dest)}


def verify_backup(path: str | Path) -> dict:
    """백업 파일 사본을 열어 무결성·해시 체인·행 수를 확인한다(원본 DB는 건드리지 않음)."""
    path = Path(path)
    manifest_path = path.with_suffix(".json")
    result = {"file": path.name, "checks": []}
    if manifest_path.exists():
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        same = hashlib.sha256(path.read_bytes()).hexdigest() == m["sha256"]
        result["checks"].append(("매니페스트 해시 일치", same))
    tmp = path.with_name(path.stem + ".verify-copy.db")
    shutil.copyfile(path, tmp)
    try:
        with sqlite3.connect(tmp) as c:
            ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        result["checks"].append(("SQLite 무결성 검사", ok))
        eng = create_engine(f"sqlite:///{tmp.as_posix()}")
        with sessionmaker(bind=eng)() as s:
            from app.paper.ledger import verify_chain
            exps = s.scalars(select(Experiment)).all()
            chain_ok = all(verify_chain(s, e.id)[0] for e in exps)
            result["checks"].append((f"모의운용 해시 체인({len(exps)}개 실험)", chain_ok))
            result["counts"] = {"briefings": s.query(Briefing).count(), "hypotheses": s.query(Hypothesis).count(),
                                "evaluations": s.query(HypothesisEvaluation).count(),
                                "macro_revisions": s.query(MacroRevision).count()}
        eng.dispose()
    finally:
        tmp.unlink(missing_ok=True)
    result["ok"] = all(ok for _, ok in result["checks"])
    return result


def restore_sqlite(path: str | Path) -> dict:
    """복구는 웹서버·worker를 모두 멈춘 상태에서 CLI로만 실행한다. 현재 DB는 먼저 백업해 둔다."""
    check = verify_backup(path)
    if not check["ok"]:
        raise RuntimeError(f"백업 검증 실패로 복구하지 않음: {check['checks']}")
    target = _sqlite_path()
    safety = backup_sqlite("before-restore")
    shutil.copyfile(path, target)
    for ext in ("-wal", "-shm"):
        Path(str(target) + ext).unlink(missing_ok=True)
    return {"restored_from": str(path), "previous_saved_as": safety["path"], "verification": check}


# ---------------------------------------------------------------- 가져오기

class ImportError_(Exception):
    pass


def _legacy_items(data: dict) -> list[tuple[str, str | None, dict]]:
    items: list[tuple[str, str | None, dict]] = []
    for key in ("pre", "close"):
        if isinstance(data.get(key), dict):
            items.append(("briefing", f"{key}:{data[key].get('date') or data[key].get('tradingDate')}", data[key]))
    for b in data.get("archive") or []:
        if isinstance(b, dict):
            items.append(("briefing", str(b.get("id") or b.get("date") or ""), b))
    for h in data.get("hypotheses") or []:
        if isinstance(h, dict):
            kind = "legacyRelative" if h.get("evaluationPolicy") != "absolute-price-v1" else "legacyHypothesis"
            items.append((kind, str(h.get("id") or ""), h))
    dr = data.get("deepResearch")
    if isinstance(dr, dict):
        items.append(("research", str(dr.get("checkedAt") or ""), dr))
        for old in dr.get("history") or []:
            if isinstance(old, dict):
                items.append(("research", str(old.get("checkedAt") or ""), old))
    mo = data.get("macroOutlook")
    if mo:
        items.append(("macro", None, mo if isinstance(mo, dict) else {"value": mo}))
    mp = data.get("modelPortfolio")
    if isinstance(mp, dict):
        for run in (mp.get("runs") or []) + (mp.get("archivedRuns") or []):
            if isinstance(run, dict):
                items.append(("experiment", str(run.get("id") or ""), run))
    return items


def import_briefs_json(db: Session, raw: bytes, filename: str, *, dry_run: bool, actor: str) -> ImportRun:
    if len(raw) > MAX_IMPORT_BYTES:
        raise ImportError_(f"파일이 너무 큽니다(최대 {MAX_IMPORT_BYTES // 1024 // 1024}MB).")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ImportError_(f"JSON 형식 오류: {e}") from None
    if not isinstance(data, dict):
        raise ImportError_("최상위가 객체(JSON object)가 아닙니다.")
    unknown = sorted(set(data) - set(COMPAT_KEYS))
    items = _legacy_items(data)
    plan, dups = [], 0
    for kind, oid, payload in items:
        h = sha256_text(canonical_json(payload))
        exists = db.scalar(select(LegacyRecord).where(LegacyRecord.content_sha256 == h)) is not None
        dups += exists
        plan.append({"kind": kind, "original_id": oid, "sha256": h, "duplicate": exists})
    report = {"total_items": len(items), "duplicates": dups, "new": len(items) - dups, "unknown_top_level_keys": unknown,
              "by_kind": {k: sum(1 for p in plan if p["kind"] == k) for k in {p["kind"] for p in plan}},
              "policy": "모든 항목은 legacy_records에 읽기 전용으로 저장. 신규 통계·연구 통과·체결 인증에 소급 반영하지 않음."}
    file_hash = hashlib.sha256(raw).hexdigest()
    backup_path = None
    if not dry_run:
        if _sqlite_path() is not None:
            backup_path = backup_sqlite("before-import")["path"]
    run = ImportRun(kind="briefs.json", filename=filename[:300], file_sha256=file_hash, dry_run=dry_run,
                    status="dry-run" if dry_run else "applied", report=report, backup_path=backup_path)
    db.add(run)
    db.flush()
    if not dry_run:
        seen = set()
        for (kind, oid, payload), p in zip(items, plan):
            if p["duplicate"] or p["sha256"] in seen:
                continue
            seen.add(p["sha256"])
            db.add(LegacyRecord(kind=kind, original_id=oid, content_sha256=p["sha256"], payload=payload,
                                import_id=run.id))
        db.flush()
    audit(db, actor, "import.briefs_json", filename, {"dry_run": dry_run, "file_sha256": file_hash, **report})
    return run


def import_holdings_json(db: Session, raw: bytes, filename: str, *, dry_run: bool, actor: str) -> dict:
    """보유종목 형식: [{"code","name","purchase_history":[{"date","quantity","unit_price"}]}]"""
    from app.models import Holding
    from app.services.holdings import add_holding, record_transaction
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ImportError_("보유종목 파일은 배열이어야 합니다.")
    report = {"holdings": 0, "transactions": 0, "skipped_existing": []}
    for item in data:
        code = str(item.get("code", "")).strip()
        if db.scalar(select(Holding).where(Holding.code == code)):
            report["skipped_existing"].append(code)
            continue
        report["holdings"] += 1
        report["transactions"] += len(item.get("purchase_history") or [])
        if dry_run:
            continue
        h = add_holding(db, code, item.get("name"), actor)
        for tx in item.get("purchase_history") or []:
            record_transaction(db, h, trade_date=date.fromisoformat(tx["date"]), side="buy",
                               quantity=int(tx["quantity"]), unit_price=int(tx["unit_price"]),
                               note=f"가져오기: {filename}", actor=actor)
    return report
