from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AuditLog


def audit(db: Session, actor: str, action: str, target: str | None = None, detail: dict | None = None) -> None:
    db.add(AuditLog(actor=actor, action=action, target=target, detail=_redact(detail)))


_SECRET_HINTS = ("key", "token", "password", "secret", "authorization")


def _redact(detail: dict | None) -> dict | None:
    """감사로그에 비밀값이 섞이지 않도록 키 이름 기준으로 가린다."""
    if detail is None:
        return None
    clean = {}
    for k, v in detail.items():
        if any(h in k.lower() for h in _SECRET_HINTS):
            clean[k] = "***"
        elif isinstance(v, dict):
            clean[k] = _redact(v)
        else:
            clean[k] = v
    return clean
