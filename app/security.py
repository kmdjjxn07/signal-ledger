"""
소유자 전용 인증.
- 비밀번호: 표준 라이브러리 scrypt (추가 패키지 없음)
- 세션: 무작위 토큰을 쿠키로 주고 DB에는 SHA-256 해시만 저장
- CSRF: 세션마다 별도 토큰, 모든 쓰기 요청(POST)에서 검사
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User, UserSession
from app.timeutil import utcnow

COOKIE_NAME = "sl_session"
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 64}

_failed: dict[str, list[float]] = {}
MAX_FAILS = 5
FAIL_WINDOW_SEC = 15 * 60


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("비밀번호는 10자 이상이어야 합니다.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p),
                            dklen=len(hash_hex) // 2)
    return hmac.compare_digest(digest.hex(), hash_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_rate_limited(client_key: str) -> bool:
    now = time.time()
    attempts = [t for t in _failed.get(client_key, []) if now - t < FAIL_WINDOW_SEC]
    _failed[client_key] = attempts
    return len(attempts) >= MAX_FAILS


def record_failure(client_key: str) -> None:
    _failed.setdefault(client_key, []).append(time.time())


def clear_failures(client_key: str) -> None:
    _failed.pop(client_key, None)


def create_owner(db: Session, username: str, password: str) -> User:
    if db.scalar(select(User)) is not None:
        raise ValueError("소유자 계정이 이미 있습니다. 이 서비스는 소유자 1명 전용입니다.")
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    return user


def set_password(db: Session, username: str, password: str) -> None:
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        raise ValueError("해당 사용자가 없습니다.")
    user.password_hash = hash_password(password)
    for s in db.scalars(select(UserSession).where(UserSession.user_id == user.id)):
        s.revoked = True


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        # 타이밍 차이를 줄이기 위해 가짜 검증을 한 번 수행
        verify_password(password, "scrypt$16384$8$1$" + "00" * 16 + "$" + "00" * 64)
        return None
    return user if verify_password(password, user.password_hash) else None


def start_session(db: Session, user: User) -> tuple[str, UserSession]:
    token = secrets.token_urlsafe(32)
    sess = UserSession(
        user_id=user.id, token_hash=_token_hash(token), csrf_token=secrets.token_urlsafe(24),
        expires_at=utcnow() + timedelta(hours=get_settings().session_hours),
    )
    db.add(sess)
    db.flush()
    return token, sess


def load_session(db: Session, token: str | None) -> tuple[User, UserSession] | None:
    if not token:
        return None
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    if sess is None or sess.revoked or sess.expires_at < utcnow():
        return None
    user = db.get(User, sess.user_id)
    if user is None:
        return None
    return user, sess


def end_session(db: Session, token: str | None) -> None:
    if not token:
        return
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    if sess:
        sess.revoked = True


def check_csrf(sess: UserSession, submitted: str | None) -> bool:
    return bool(submitted) and hmac.compare_digest(sess.csrf_token, submitted or "")
