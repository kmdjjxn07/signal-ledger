"""
관리 명령.

    python -m app.cli init-db                 DB 생성/마이그레이션(alembic upgrade head)
    python -m app.cli create-owner            소유자 계정 생성(비밀번호는 화면에 표시되지 않게 입력)
    python -m app.cli set-password <아이디>
    python -m app.cli probe-naver 005930      네이버 원본 응답 필드 확인(키 없음, 이용조건 확인 후 사용)
    python -m app.cli probe-krx --date 2026-11-20   KRX 응답 구조 확인(키 값은 출력하지 않음)
    python -m app.cli probe-dart              DART 키·매핑 확인
    python -m app.cli run-job close           예약 작업 수동 실행
    python -m app.cli export out.json         호환 JSON 내보내기
    python -m app.cli backup | verify-backup <파일> | restore <파일>
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import date, datetime

from app.timeutil import KST


def _init_db() -> None:
    from alembic import command
    from alembic.config import Config

    from app.config import ROOT, get_settings
    url = get_settings().database_url
    if url.startswith("sqlite:///"):
        from pathlib import Path
        Path(url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)  # 새 설치: data 폴더 생성
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.upgrade(cfg, "head")
    print("DB 준비 완료:", get_settings().database_url.split("///")[-1] if "sqlite" in get_settings().database_url
          else "PostgreSQL")


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0911, PLR0912
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    co = sub.add_parser("create-owner")
    co.add_argument("--username")
    sp = sub.add_parser("set-password")
    sp.add_argument("username")
    pn = sub.add_parser("probe-naver")
    pn.add_argument("code")
    pk = sub.add_parser("probe-krx")
    pk.add_argument("--date", required=True)
    sub.add_parser("probe-dart")
    rj = sub.add_parser("run-job")
    rj.add_argument("name")
    ex = sub.add_parser("export")
    ex.add_argument("path")
    sub.add_parser("backup")
    vb = sub.add_parser("verify-backup")
    vb.add_argument("path")
    rs = sub.add_parser("restore")
    rs.add_argument("path")
    args = p.parse_args(argv)

    if args.cmd == "init-db":
        _init_db()
        return 0

    from app.db import session_scope

    if args.cmd == "create-owner":
        from app.security import create_owner
        username = args.username or input("소유자 아이디: ").strip()
        pw = getpass.getpass("비밀번호(10자 이상, 입력이 보이지 않음): ")
        if pw != getpass.getpass("비밀번호 확인: "):
            print("비밀번호가 일치하지 않습니다.")
            return 1
        try:
            with session_scope() as db:
                create_owner(db, username, pw)
        except ValueError as e:
            print(e)
            return 1
        print(f"소유자 '{username}' 계정을 만들었습니다.")
        return 0

    if args.cmd == "set-password":
        from app.security import set_password
        pw = getpass.getpass("새 비밀번호: ")
        with session_scope() as db:
            set_password(db, args.username, pw)
        print("비밀번호를 바꿨고 기존 로그인 세션을 모두 끊었습니다.")
        return 0

    if args.cmd == "probe-naver":
        from app.providers.naver import NaverQuoteProvider, validate_quote
        from app.services.settings_store import get_setting
        res = NaverQuoteProvider(enabled=True).fetch(args.code)
        print("상태:", res.status.value, res.detail, "HTTP", res.http_status)
        if res.ok:
            d = (res.data.get("datas") or [{}])[0]
            print("최상위 필드:", sorted(d.keys()))
            for k in ("itemCode", "code", "closePrice", "localTradedAt", "marketStatus", "marketSessionType",
                      "stockExchangeType", "tradeStopType"):
                print(f"  {k} = {d.get(k)!r}")
            with session_scope() as db:
                rules = get_setting(db, "naver_validation")
            q = validate_quote(args.code, res.data, {**rules, "confirmed_by_owner": True})
            print("현재 규칙으로 판정(확정 여부 제외):", "적격" if q.eligible else f"부적격 — {q.reason}")
            print("※ 확인 후 관리·운영 화면에서 허용값을 확정하세요. 이용조건을 확인한 뒤 NAVER_QUOTE_ENABLED=true로 켭니다.")
        return 0

    if args.cmd == "probe-krx":
        from app.providers.krx import KrxDailyProvider
        d = date.fromisoformat(args.date)
        prov = KrxDailyProvider()
        for market in ("KOSPI", "KOSDAQ"):
            r = prov.index_daily(market, d)
            print(market, "지수:", r.status.value, r.detail, (r.data.close if r.ok else ""))
            r2 = prov.stock_daily(market, d)
            print(market, "종목:", r2.status.value, r2.detail, (len(r2.data) if r2.ok else ""))
            if r2.ok:
                print("  첫 행 원본 필드:", sorted(r2.data[0].raw.keys()))
        return 0

    if args.cmd == "probe-dart":
        from app.providers.dart import DartProvider
        r = DartProvider().corp_codes()
        print("DART 매핑:", r.status.value, r.detail, (len(r.data) if r.ok else ""))
        return 0

    if args.cmd == "run-job":
        from app.jobs import JOBS, run_job
        if args.name not in JOBS:
            print("작업 이름:", ", ".join(JOBS))
            return 1
        now = datetime.now(KST)
        run = run_job(args.name, f"cli:{now.isoformat(timespec='seconds')}", now.date(), now)
        print(run.status if run else "잠금 때문에 실행하지 않음", run.summary if run else "", run.error if run else "")
        return 0

    if args.cmd == "export":
        from app.services.portability import export_compat
        with session_scope() as db:
            data = export_compat(db)
        with open(args.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        print("내보냄:", args.path)
        return 0

    from app.services import portability
    if args.cmd == "backup":
        info = portability.backup_sqlite("cli")
        print(json.dumps({**info, "verification": portability.verify_backup(info["path"])}, ensure_ascii=False,
                         indent=2, default=str))
        return 0
    if args.cmd == "verify-backup":
        print(json.dumps(portability.verify_backup(args.path), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.cmd == "restore":
        if input("웹서버와 worker를 모두 멈췄습니까? (yes 입력): ").strip() != "yes":
            print("취소했습니다.")
            return 1
        print(json.dumps(portability.restore_sqlite(args.path), ensure_ascii=False, indent=2, default=str))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
