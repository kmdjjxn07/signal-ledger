from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30}
        db_path = url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args=connect_args, future=True)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    return engine


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def configure(url: str | None = None) -> None:
    """테스트에서 다른 DB로 바꿀 때도 이 함수를 쓴다."""
    global _engine, _SessionLocal
    _engine = make_engine(url or get_settings().database_url)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)


def engine() -> Engine:
    if _engine is None:
        configure()
    return _engine  # type: ignore[return-value]


def session_factory() -> sessionmaker:
    if _SessionLocal is None:
        configure()
    return _SessionLocal  # type: ignore[return-value]


@contextmanager
def session_scope():
    s: Session = session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db():
    s: Session = session_factory()()
    try:
        yield s
    finally:
        s.close()


def create_all() -> None:
    from app import models  # noqa: F401
    Base.metadata.create_all(engine())


def begin_real_transaction(s: Session) -> None:
    """SQLite 드라이버는 읽기만 한 상태에서는 실제 BEGIN을 보내지 않는다. 그 상태에서 SAVEPOINT를 쓰면
    RELEASE가 바깥 작업까지 확정해 버리므로, 부분 되돌리기를 쓰기 전에 실제 트랜잭션을 연다."""
    conn = s.connection()
    raw = conn.connection.dbapi_connection
    if conn.dialect.name == "sqlite" and not getattr(raw, "in_transaction", True):
        conn.exec_driver_sql("BEGIN")
