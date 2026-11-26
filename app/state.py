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

공급자 자료 표(ENCRYPTED_TABLES: 종목 마스터·KRX 공식 가격·참고 시세 관측)는 암호화해서 저장한다(*.sql.enc).
KRX OPEN API 약관은 제공 자료를 제3자에게 넘기는 것을 금지하므로, 공개 저장소에서 누구나 내려받을 수 있는
평문으로 두지 않는다. 열쇠(SL_STATE_KEY)는 GitHub Secrets와 내 컴퓨터 .env에만 둔다.
열쇠 없이 이 표들에 행이 생기면 저장을 거부한다(평문으로 새어 나가지 않게).
"""
from __future__ import annotations

import hashlib
import hmac
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

# 공급자 자료라서 공개 저장소에는 암호문으로만 두는 표
ENCRYPTED_TABLES = ("instruments", "official_prices", "observations")
ENC_SUFFIX = ".enc"
ENC_MAGIC = "SLENC1"

HEADER = "-- 시그널 원장 공개 상태 파일. 손으로 고치지 마세요(app/state.py가 생성).\n"


class StateKeyError(RuntimeError):
    pass


def generate_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode("ascii")


def _fernet(key: str):  # noqa: ANN202
    from cryptography.fernet import Fernet
    try:
        return Fernet(key.strip().encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise StateKeyError("SL_STATE_KEY 형식이 올바르지 않습니다. 'python -m app.ci gen-key'로 만든 값을 그대로 넣으세요.") \
            from None


def _mac(text: str, key: str) -> str:
    return hmac.new(key.strip().encode("ascii"), text.encode("utf-8"), hashlib.sha256).hexdigest()


def encrypt_text(text: str, key: str) -> str:
    """첫 줄에 내용의 HMAC(열쇠 없이는 아무것도 알 수 없음)을 적어, 내용이 같으면 파일을 다시 쓰지 않게 한다."""
    token = _fernet(key).encrypt(text.encode("utf-8")).decode("ascii")
    return f"{ENC_MAGIC} mac={_mac(text, key)}\n{token}\n"


def _enc_mac(blob: str) -> str | None:
    first = blob.split("\n", 1)[0]
    return first.split("mac=", 1)[1].strip() if first.startswith(ENC_MAGIC) and "mac=" in first else None


def decrypt_text(blob: str, key: str) -> str:
    from cryptography.fernet import InvalidToken
    head, _, token = blob.partition("\n")
    if not head.startswith(ENC_MAGIC):
        raise StateKeyError("암호화 상태 파일 형식이 아닙니다.")
    try:
        text = _fernet(key).decrypt(token.strip().encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise StateKeyError("SL_STATE_KEY가 이 상태 파일을 만든 열쇠와 다릅니다.") from None
    if not hmac.compare_digest(_mac(text, key), _enc_mac(blob) or ""):
        raise StateKeyError("암호화 상태 파일의 검증값이 맞지 않습니다(손상 가능).")
    return text


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
            elif table in ENCRYPTED_TABLES:
                rows = _insert_rows(con, table, where)
                if rows:
                    files[f"tables/{table}/all.sql"] = "\n".join([HEADER + "BEGIN TRANSACTION;", *rows, "COMMIT;", ""])
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


def _is_encrypted_rel(rel: str) -> bool:
    parts = rel.split("/")
    return len(parts) >= 3 and parts[0] == "tables" and parts[1] in ENCRYPTED_TABLES


def _state_files(state_dir: Path) -> list[Path]:
    tables_dir = state_dir / "tables"
    if not tables_dir.exists():
        return []
    return sorted([*tables_dir.rglob("*.sql"), *tables_dir.rglob("*.sql" + ENC_SUFFIX)])


def write_state(db_path: Path, state_dir: Path, secret_values: list[str] | None = None,
                key: str | None = None) -> dict:
    """바뀐 파일만 쓴다. 더 이상 없는 월 파일은 지운다(행이 공개 범위에서 빠진 경우).
    공급자 자료 표는 key로 암호화한다. key가 없는데 그 표에 행이 있으면 아무것도 쓰지 않고 오류를 낸다."""
    plain = {k: scrub(v, (secret_values or []) + ([key] if key else [])) for k, v in dump(db_path).items()}
    need_key = sorted(rel for rel in plain if _is_encrypted_rel(rel))
    if need_key and not key:
        raise StateKeyError("공급자 자료(종목·가격·시세)가 있어 SL_STATE_KEY가 필요합니다. 평문으로 공개 저장소에 "
                            "저장하지 않으려고 중단했습니다. 'python -m app.ci gen-key'로 열쇠를 만들어 "
                            "GitHub Secrets와 .env에 넣으세요.")
    changed, removed, expected = [], [], set()
    for rel, text in plain.items():
        if _is_encrypted_rel(rel):
            rel = rel + ENC_SUFFIX
            p = state_dir / rel
            expected.add(rel)
            if p.exists() and _enc_mac(p.read_text(encoding="utf-8")) == _mac(text, key):
                continue
            out = encrypt_text(text, key)
        else:
            p = state_dir / rel
            expected.add(rel)
            if p.exists() and p.read_text(encoding="utf-8") == text:
                continue
            out = text
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(out, encoding="utf-8", newline="\n")
        changed.append(rel)
    for p in _state_files(state_dir):
        rel = p.relative_to(state_dir).as_posix()
        if rel not in expected:
            p.unlink()
            removed.append(rel)
    return {"changed": changed, "removed": removed, "files": len(expected),
            "encrypted": sum(1 for r in expected if r.endswith(ENC_SUFFIX))}


def has_state(state_dir: Path) -> bool:
    return (state_dir / "ledger.sql").exists()


def restore(state_dir: Path, db_path: Path, key: str | None = None) -> int:
    """상태 파일로 새 SQLite DB를 만든다(기존 파일은 덮어씀). 불러온 파일 수를 돌려준다.
    암호화된 파일이 있으면 key가 필요하다."""
    files = _state_files(state_dir)
    if any(p.name.endswith(ENC_SUFFIX) for p in files) and not key:
        raise StateKeyError("state/에 암호화된 공급자 자료가 있습니다. .env(또는 GitHub Secrets)에 SL_STATE_KEY를 "
                            "넣어야 복원할 수 있습니다.")
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
        for p in files:
            text = p.read_text(encoding="utf-8")
            if p.name.endswith(ENC_SUFFIX):
                text = decrypt_text(text, key)
            con.executescript(text)
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
