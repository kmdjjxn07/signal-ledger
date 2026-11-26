"""
예약 작업 실행기. 웹서버와 별도 프로세스로 실행한다(브라우저와 무관하게 동작).

    python -m app.worker            # 계속 실행 (Ctrl+C로 종료)
    python -m app.worker --once     # 지금 시점에 할 일만 한 번 처리하고 종료 (OS 스케줄러용)

여러 worker를 동시에 띄워도 job_locks·job_runs 유일키 때문에 같은 작업이 두 번 실행되지 않는다.
"""
from __future__ import annotations

import argparse
import logging
import time

from app.db import session_scope
from app.jobs import due_slots, run_job
from app.timeutil import now_kst

log = logging.getLogger("signal-ledger.worker")


def tick() -> list[str]:
    now = now_kst()
    with session_scope() as db:
        slots = due_slots(db, now)
    ran = []
    for name, slot in slots:
        run = run_job(name, slot, now.date(), now)
        if run is not None:
            ran.append(f"{name}@{slot}={run.status}")
    return ran


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    if args.once:
        log.info("once: %s", tick())
        return
    log.info("worker 시작 (간격 %ss)", args.interval)
    while True:
        try:
            ran = tick()
            if ran:
                log.info("%s", ran)
        except Exception:  # noqa: BLE001
            log.exception("tick 실패 — 다음 주기에 다시 시도")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
