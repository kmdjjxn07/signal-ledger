"""
GitHub Actions(무료, 공개 저장소)에서 서버 없이 시그널 원장을 돌리는 실행기.

    python -m app.ci run                 상태 복원 → 제출 파일 반영 → 예약 작업 → 상태 저장 → 공개 사이트 생성
    python -m app.ci build-site          상태 파일로 공개 사이트(_site/)만 다시 만들기 (내 컴퓨터에서 미리보기)
    python -m app.ci local-refresh       (내 컴퓨터) GitHub에서 받은 상태로 내 DB를 새로 만들기. 계정·보유종목은 보존
    python -m app.ci init-from-local     (내 컴퓨터, 처음 한 번) 지금까지 입력한 기록으로 공개 상태 파일 만들기

한 번의 실행은 '전부 저장되거나 전부 저장되지 않는다': 중간에 예기치 못한 오류로 멈추면 상태 파일을 쓰지 않으므로
GitHub에 아무것도 커밋되지 않고, 제출 파일은 다음 실행 때 다시 처리된다.
비밀값(API 키)은 GitHub Secrets → 환경변수로만 받으며 출력하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from app.config import ROOT
from app.timeutil import KST

STATE_DIR = Path(os.environ.get("SL_STATE_DIR") or (ROOT / "state"))
SITE_DIR = Path(os.environ.get("SL_SITE_DIR") or (ROOT / "_site"))
CI_ACTOR = "owner:inbox"


def _ci_db_path() -> Path:
    return Path(os.environ.get("SL_CI_DB") or (ROOT / "data" / "ci-ledger.db"))


def _sqlite_url(p: Path) -> str:
    return f"sqlite:///{p.resolve().as_posix()}"


def migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def use_db(path: Path) -> str:
    from app import db as dbmod
    url = _sqlite_url(path)
    os.environ["SL_DATABASE_URL"] = url
    dbmod.configure(url)
    return url


def prepare_db(path: Path, state_dir: Path = STATE_DIR) -> dict:
    from app.state import has_state, restore
    loaded = restore(state_dir, path) if has_state(state_dir) else 0
    if not loaded:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
    url = use_db(path)
    migrate(url)
    from app import db as dbmod
    dbmod.configure(url)  # 마이그레이션 뒤 새 연결
    return {"restored_files": loaded}


def _secret_values() -> list[str]:
    from app.config import get_settings
    s = get_settings()
    return [v for v in (s.krx_api_key, s.dart_api_key, s.gemini_api_key, s.fred_api_key) if v]


# ---------------------------------------------------------------- 제출 파일

def process_inbox(inbox: Path | None = None) -> list[dict]:
    from app.commands import CommandError, apply_command, inbox_dir, parse_inbox_file, pending_files
    from app.db import session_factory
    inbox = inbox or inbox_dir()
    done_dir = inbox / "processed"
    results = []
    for path in pending_files(inbox):
        result = {"file": path.name, "processed_at": datetime.now(KST).isoformat(timespec="seconds"),
                  "ok": False, "messages": [], "error": None}
        db = session_factory()()
        try:
            cmds = parse_inbox_file(path.read_bytes())
            for i, c in enumerate(cmds, 1):
                msg, _ = apply_command(db, c["kind"], c.get("payload") or {}, CI_ACTOR)
                result["messages"].append(f"{i}. {c['kind']}: {msg}")
            db.commit()
            result["ok"] = True
        except CommandError as e:
            db.rollback()
            result["error"] = f"거부(아무것도 반영하지 않음): {e}"
        except Exception as e:  # noqa: BLE001 - 규칙 위반(LedgerViolation 등)도 파일 단위로 거부
            db.rollback()
            result["error"] = f"거부(아무것도 반영하지 않음): {type(e).__name__}: {e}"
        finally:
            db.close()
        done_dir.mkdir(parents=True, exist_ok=True)
        target = done_dir / path.name
        if target.exists():
            target = done_dir / f"{path.stem}-{datetime.now(KST).strftime('%H%M%S')}.json"
        shutil.move(str(path), str(target))
        (done_dir / f"{target.stem}.result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                                            encoding="utf-8", newline="\n")
        results.append(result)
    return results


# ---------------------------------------------------------------- 데이터 보조 작업

def ensure_watch_prices(now: datetime) -> dict:
    """관찰 종목(가설·심층검증)의 직전 거래일 공식 가격이 없으면 그날 KRX 공식 일별 자료를 다시 받는다.
    (공개 상태에는 관찰 종목 가격만 남기므로, 새로 관찰을 시작한 종목은 이렇게 채운다)"""
    from app.config import get_settings
    from app.db import session_scope
    from app.services.briefings import watch_codes
    from app.services.calendar import CalendarUnknown, previous_trading_day
    from app.services.market_data import latest_official, sync_official_daily
    if not get_settings().krx_api_key:
        return {"result": "skipped", "reason": "KRX_API_KEY 없음"}
    with session_scope() as db:
        try:
            prev = previous_trading_day(db, now.date())
        except CalendarUnknown as e:
            return {"result": "skipped", "reason": f"캘린더 미확인: {e}"}
        if prev is None:
            return {"result": "skipped", "reason": "직전 거래일 미확인"}
        missing = [c for c in watch_codes(db) if latest_official(db, c, prev) is None]
        if not missing:
            return {"result": "ok", "missing": 0}
        summary = sync_official_daily(db, prev)
        return {"result": "synced", "date": prev.isoformat(), "missing": len(missing), "results": summary["results"]}


def weekly_dart_sync(now: datetime) -> dict:
    from sqlalchemy import func, select

    from app.config import get_settings
    from app.db import session_scope
    from app.models import FetchLog
    from app.services.market_data import sync_dart_corp_codes
    from app.timeutil import utcnow
    if not get_settings().dart_api_key:
        return {"result": "skipped", "reason": "DART_API_KEY 없음"}
    with session_scope() as db:
        last = db.scalar(select(func.max(FetchLog.requested_at)).where(FetchLog.provider == "dart",
                                                                       FetchLog.operation == "corp_codes"))
        if last and last > utcnow() - timedelta(days=7):
            return {"result": "skipped", "reason": "최근 7일 안에 시도함"}
        n, err = sync_dart_corp_codes(db, "job:dart_corp_codes")
        return {"result": "failed" if err else "ok", "updated": n, "error": err}


def tick(now: datetime) -> list[str]:
    from app.db import session_scope
    from app.jobs import due_slots, run_job
    with session_scope() as db:
        slots = due_slots(db, now)
    ran = []
    for name, slot in slots:
        run = run_job(name, slot, now.date(), now)
        if run is not None:
            ran.append(f"{name}@{slot}={run.status}")
    return ran


# ---------------------------------------------------------------- 명령

def cmd_run(args: argparse.Namespace) -> int:
    now = datetime.fromisoformat(args.now).astimezone(KST) if args.now else datetime.now(KST)
    db_path = _ci_db_path()
    report: dict = {"now_kst": now.isoformat(timespec="minutes"), **prepare_db(db_path)}
    report["inbox"] = [{"file": r["file"], "ok": r["ok"]} for r in process_inbox()]
    if not args.skip_jobs:
        for name, fn in (("watch_prices", ensure_watch_prices), ("dart_corp_codes", weekly_dart_sync)):
            try:
                report[name] = fn(now)
            except Exception as e:  # noqa: BLE001 - 보조 작업 실패는 기록만 하고 계속
                report[name] = {"result": "failed", "error": f"{type(e).__name__}"}
        report["jobs"] = tick(now)
    from app.state import write_state
    report["state"] = write_state(db_path, STATE_DIR, _secret_values())
    from app.site import build_site
    report["site"] = build_site(SITE_DIR)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_build_site(args: argparse.Namespace) -> int:  # noqa: ARG001
    prepare_db(_ci_db_path())
    from app.site import build_site
    print(json.dumps(build_site(SITE_DIR), ensure_ascii=False, indent=2))
    return 0


def cmd_local_refresh(args: argparse.Namespace) -> int:
    """GitHub에서 Pull 받은 state/로 내 컴퓨터 DB를 새로 만든다. 로그인 계정·보유종목은 기존 DB에서 옮겨 온다."""
    from app.config import get_settings
    from app.state import LOCAL_ONLY_TABLES, copy_rows, has_state
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        print("local-refresh는 SQLite DB에서만 동작합니다.")
        return 2
    if not has_state(STATE_DIR):
        print("state/ledger.sql이 없습니다. GitHub Desktop에서 Pull(가져오기)을 먼저 하세요.")
        return 2
    local = Path(url.split("///", 1)[1])
    tmp = local.with_name(local.stem + ".refresh.db")
    prepare_db(tmp)
    kept = {}
    if local.exists():
        backup_dir = get_settings().backup_dir
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
        shutil.copy2(local, backup_dir / f"before-refresh-{stamp}.db")
        kept = copy_rows(local, tmp, LOCAL_ONLY_TABLES)
    from app import db as dbmod
    dbmod.configure(_sqlite_url(tmp))
    dbmod.engine().dispose()
    for suffix in ("", "-wal", "-shm"):
        side = Path(str(local) + suffix)
        if side.exists():
            side.unlink()
    shutil.move(str(tmp), str(local))
    use_db(local)
    msg = {"result": "ok", "db": local.name, "kept_local_rows": kept}
    if args.prices:
        msg["holding_prices"] = refresh_holding_prices()
    print(json.dumps(msg, ensure_ascii=False, indent=2))
    print("완료: 공개 원장의 최신 상태를 내 DB에 반영했습니다. 웹 화면을 새로고침하세요.")
    return 0


def cmd_init_from_local(args: argparse.Namespace) -> int:  # noqa: ARG001
    """(처음 한 번) 내 컴퓨터 DB에 이미 입력한 기록을 공개 상태 파일의 출발점으로 만든다.
    로그인 계정·보유종목·원본 응답은 넣지 않는다. 이미 state/가 있으면 덮어쓰지 않는다."""
    from app.config import get_settings
    from app.state import has_state, write_state
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        print("SQLite DB에서만 동작합니다.")
        return 2
    if has_state(STATE_DIR):
        print("state/ledger.sql이 이미 있습니다. 공개 원장을 덮어쓰지 않습니다.")
        return 2
    local = Path(url.split("///", 1)[1])
    if not local.exists():
        print("내 DB가 없습니다.")
        return 2
    migrate(url)
    rep = write_state(local, STATE_DIR, _secret_values())
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    print("완료: state/ 폴더가 생겼습니다. 보유종목·계정은 들어가지 않았습니다. GitHub Desktop에서 커밋·푸시하세요.")
    return 0


def refresh_holding_prices() -> dict:
    """보유종목 평가용 KRX 공식 가격(최근 2거래일)을 내 컴퓨터 DB에만 받는다. 공개 저장소에는 올라가지 않는다."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.db import session_scope
    from app.models import Holding
    from app.services.calendar import CalendarUnknown, previous_trading_day
    from app.services.market_data import sync_official_daily
    from app.timeutil import today_kst
    if not get_settings().krx_api_key:
        return {"result": "skipped", "reason": "내 컴퓨터 .env에 KRX_API_KEY 없음"}
    out = {}
    with session_scope() as db:
        if not db.scalars(select(Holding)).first():
            return {"result": "skipped", "reason": "보유종목 없음"}
        d = today_kst()
        try:
            days = []
            p = previous_trading_day(db, d)
            while p is not None and len(days) < 2:
                days.append(p)
                p = previous_trading_day(db, p)
        except CalendarUnknown as e:
            return {"result": "skipped", "reason": f"캘린더 미확인: {e}"}
        for day in days:
            out[day.isoformat()] = sync_official_daily(db, day)["results"]
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.ci")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--now", help="시험용: 이 시각(ISO, 시간대 포함)으로 실행")
    r.add_argument("--skip-jobs", action="store_true", help="제출 파일 반영과 사이트 생성만")
    sub.add_parser("build-site")
    lr = sub.add_parser("local-refresh")
    lr.add_argument("--prices", action="store_true", help="보유종목 평가용 KRX 가격도 받기(내 .env의 키 사용)")
    sub.add_parser("init-from-local")
    args = p.parse_args(argv)
    return {"run": cmd_run, "build-site": cmd_build_site, "local-refresh": cmd_local_refresh,
            "init-from-local": cmd_init_from_local}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
