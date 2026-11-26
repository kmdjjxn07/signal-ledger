"""
공개 원장 상태 파일(텍스트 SQL) 저장·복원.

GitHub Actions는 실행할 때마다 빈 컴퓨터에서 시작하므로, DB를 저장소 안의 텍스트 파일로 보관한다.

    state/ledger.sql                     스키마 + 작은 표(설정·캘린더·종목·출처·브리핑·연구·가설·실험 등)
    state/tables/<표>/<YYYY-MM>.sql      시간이 지나며 쌓이는 표를 월별로 나눔(지난 달 파일은 다시 바뀌지 않음)

공개 저장소에 들어가므로 다음은 '행'을 저장하지 않는다(스키마만 저장):
    users, user_sessions            로그인 정보
    holdings, holding_transactions  내 보유종목(이 컴퓨터에만 보관)
    raw_documents                   공급자 원본 응답(이용조건 미확인 → 해시와 출처 링크만 공개)
    job_locks                       실행 중 잠금(일시적)

KRX 공식 일별 가격은 기록이 인용하는 종목(가설·심층검증·모의운용 종목)과 지수만 남긴다.
출력은 같은 DB면 항상 같은 글자가 나오도록(결정적) 만든다 → Git 변경분이 작다.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

PRIVATE_TABLES = ("users", "user_sessions", "holdings", "holding_transactions", "raw_documents", "job_locks")
LOCAL_ONLY_TABLES = ("users", "holdings", "holding_transactions")  # local-refresh 때 내 컴퓨터 쪽 행을 보존

# 표 → 월 구분에 쓰는 열
PARTITIONED = {
    "audit_logs": "at",
    "fetch_logs": "requested_at",
    "observations": "requested_at",
    "paper_events": "at",
    "job_runs": "started_at",
    "official_prices": "trading_date",
}

HEADER = "-- 시그널 원장 공개 상태 파일. 손으로 고치지 마세요(app/state.py가 생성).\n"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                      "ORDER BY name")]


def _insert_rows(con: sqlite3.Connection, table: str, where: str = "1=1", params: tuple = ()) -> list[str]:
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({_q(table)})")]
    expr = " || ',' || ".join(f"quote({_q(c)})" for c in cols)
    sql = (f"SELECT 'INSERT INTO {_q(table).replace(chr(39), chr(39) * 2)} VALUES(' || {expr} || ');' "
           f"FROM {_q(table)} WHERE {where} ORDER BY rowid")
    return [r[0] for r in con.execute(sql, params)]


def _retained_price_filter(con: sqlite3.Connection) -> str:
    """공식 가격 중 공개 상태에 남길 행의 조건. 기록이 인용하는 종목·가격만."""
    names = set(_tables(con))
    parts = ["code LIKE 'IDX:%'"]
    if "hypotheses" in names:
        parts.append("code IN (SELECT code FROM hypotheses)")
        parts.append("id IN (SELECT base_price_id FROM hypotheses)")
    if "hypothesis_evaluations" in names:
        parts.append("id IN (SELECT price_id FROM hypothesis_evaluations WHERE price_id IS NOT NULL)")
    if "research_versions" in names:
        parts.append("code IN (SELECT code FROM research_versions)")
    if "paper_events" in names:
        parts.append("code IN (SELECT json_extract(payload, '$.code') FROM paper_events "
                     "WHERE json_valid(payload) AND json_extract(payload, '$.code') IS NOT NULL)")
    return "(" + " OR ".join(parts) + ")"


def dump(db_path: Path) -> dict[str, str]:
    """{상대경로: 내용}. 상대경로는 state/ 기준."""
    con = sqlite3.connect(str(db_path))
    try:
        files: dict[str, str] = {}
        core = [HEADER, "PRAGMA foreign_keys=OFF;", "BEGIN TRANSACTION;"]
        schema = con.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                             "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name").fetchall()
        core += [f"{sql};" for _, _, sql in schema]
        price_filter = _retained_price_filter(con)
        for table in _tables(con):
            if table in PRIVATE_TABLES:
                continue
            where = price_filter if table == "official_prices" else "1=1"
            if table in PARTITIONED:
                col = _q(PARTITIONED[table])
                months = [r[0] for r in con.execute(
                    f"SELECT DISTINCT COALESCE(substr({col}, 1, 7), 'undated') FROM {_q(table)} WHERE {where} ORDER BY 1")]
                for m in months:
                    cond = f"{col} IS NULL" if m == "undated" else f"substr({col}, 1, 7) = ?"
                    rows = _insert_rows(con, table, f"{where} AND {cond}", () if m == "undated" else (m,))
                    files[f"tables/{table}/{m}.sql"] = "\n".join([HEADER + "BEGIN TRANSACTION;", *rows, "COMMIT;", ""])
            else:
                core += _insert_rows(con, table, where)
        core += ["COMMIT;", ""]
        files["ledger.sql"] = "\n".join(core)
        return files
    finally:
        con.close()


def scrub(text: str, secret_values: list[str]) -> str:
    """혹시 오류 메시지 등에 비밀값이 섞여 들어갔더라도 공개 파일에는 남기지 않는다."""
    for v in secret_values:
        if v and len(v) >= 6:
            text = text.replace(v, "[비밀값 가림]")
    return text


def write_state(db_path: Path, state_dir: Path, secret_values: list[str] | None = None) -> dict:
    """바뀐 파일만 쓴다. 더 이상 없는 월 파일은 지운다(행이 공개 범위에서 빠진 경우)."""
    files = {k: scrub(v, secret_values or []) for k, v in dump(db_path).items()}
    changed, removed = [], []
    for rel, text in files.items():
        p = state_dir / rel
        if p.exists() and p.read_text(encoding="utf-8") == text:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        changed.append(rel)
    tables_dir = state_dir / "tables"
    if tables_dir.exists():
        for p in sorted(tables_dir.rglob("*.sql")):
            rel = p.relative_to(state_dir).as_posix()
            if rel not in files:
                p.unlink()
                removed.append(rel)
    return {"changed": changed, "removed": removed, "files": len(files)}


def has_state(state_dir: Path) -> bool:
    return (state_dir / "ledger.sql").exists()


def restore(state_dir: Path, db_path: Path) -> int:
    """상태 파일로 새 SQLite DB를 만든다(기존 파일은 덮어씀). 불러온 파일 수를 돌려준다."""
    if db_path.exists():
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            side.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    n = 0
    try:
        con.executescript((state_dir / "ledger.sql").read_text(encoding="utf-8"))
        n += 1
        tables_dir = state_dir / "tables"
        if tables_dir.exists():
            for p in sorted(tables_dir.rglob("*.sql")):
                con.executescript(p.read_text(encoding="utf-8"))
                n += 1
        con.execute("PRAGMA foreign_keys=ON")
        bad = con.execute("PRAGMA foreign_key_check").fetchall()
        problems = [r for r in bad if r[0] not in PRIVATE_TABLES]
        if problems:
            raise RuntimeError(f"상태 파일 복원 후 참조 무결성 오류: {problems[:5]}")
        con.commit()
    finally:
        con.close()
    return n


def copy_rows(src_db: Path, dst_db: Path, tables: tuple[str, ...]) -> dict[str, int]:
    """src의 지정 표 행을 dst로 옮긴다(dst 쪽 같은 표는 비운 뒤). 열 이름이 같은 것만 옮긴다."""
    out = {}
    src = sqlite3.connect(str(src_db))
    dst = sqlite3.connect(str(dst_db))
    try:
        dst.execute("PRAGMA foreign_keys=OFF")
        src_tables = set(_tables(src))
        for t in tables:
            if t not in src_tables:
                out[t] = 0
                continue
            s_cols = [r[1] for r in src.execute(f"PRAGMA table_info({_q(t)})")]
            d_cols = {r[1] for r in dst.execute(f"PRAGMA table_info({_q(t)})")}
            cols = [c for c in s_cols if c in d_cols]
            rows = src.execute(f"SELECT {', '.join(_q(c) for c in cols)} FROM {_q(t)} ORDER BY rowid").fetchall()
            dst.execute(f"DELETE FROM {_q(t)}")
            dst.executemany(f"INSERT INTO {_q(t)} ({', '.join(_q(c) for c in cols)}) VALUES "
                            f"({', '.join('?' for _ in cols)})", rows)
            out[t] = len(rows)
        dst.commit()
    finally:
        src.close()
        dst.close()
    return out
