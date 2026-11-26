"""
설정. 환경변수 또는 프로젝트 루트의 .env 파일에서 읽는다.
비밀값(API 키)은 서버 프로세스 안에서만 사용하고, 화면이나 API 응답에 값 자체를 내보내지 않는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """의존성을 늘리지 않으려고 아주 단순한 .env 파서를 직접 둔다. 이미 설정된 환경변수는 덮어쓰지 않는다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str) -> str:
    """비어 있는 값(.env에 'KEY=' 만 있는 경우)은 설정하지 않은 것으로 보고 기본값을 쓴다."""
    value = os.environ.get(name, "").strip()
    return value or default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    env: str = field(default_factory=lambda: _env("SL_ENV", "development"))
    database_url: str = field(
        default_factory=lambda: _env("SL_DATABASE_URL", f"sqlite:///{(ROOT / 'data' / 'signal_ledger.db').as_posix()}")
    )
    data_dir: Path = field(default_factory=lambda: Path(_env("SL_DATA_DIR", str(ROOT / "data"))))
    cookie_secure: bool = field(default_factory=lambda: _bool("SL_COOKIE_SECURE", False))
    session_hours: int = field(default_factory=lambda: int(_env("SL_SESSION_HOURS", "12")))

    # 공급자 키 (없으면 해당 기능은 '연결 필요')
    krx_api_key: str | None = field(default_factory=lambda: os.environ.get("KRX_API_KEY") or None)
    dart_api_key: str | None = field(default_factory=lambda: os.environ.get("DART_API_KEY") or None)
    gemini_api_key: str | None = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY") or None)
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", ""))
    fred_api_key: str | None = field(default_factory=lambda: os.environ.get("FRED_API_KEY") or None)
    # 네이버 공개 시세 엔드포인트는 키가 없지만, 이용조건을 소유자가 확인한 뒤에만 켠다.
    naver_quote_enabled: bool = field(default_factory=lambda: _bool("NAVER_QUOTE_ENABLED", False))

    http_timeout_sec: float = field(default_factory=lambda: float(_env("SL_HTTP_TIMEOUT", "8")))

    # direct: 화면 입력을 내 DB에 바로 반영 / editor: inbox/ 제출 파일로 저장(공개 원장은 GitHub 실행기만 씀)
    mode: str = field(default_factory=lambda: _env("SL_MODE", "direct").strip().lower())

    @property
    def is_editor(self) -> bool:
        return self.mode == "editor"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"


def get_settings() -> Settings:
    return Settings()
