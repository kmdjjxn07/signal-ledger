"""
Big Picture — 분기별 거시 전망.
진행 중인 분기는 '진행 중'으로 표시하고 확정 실적처럼 쓰지 않는다.
수정은 새 판(revision)을 추가하며 이유·시각·출처를 남긴다. 이전 판을 지우지 않는다.
"""
from __future__ import annotations

import re
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import MacroOutlook, MacroRevision, Source
from app.services.audit import audit

QUARTER_RE = re.compile(r"^(\d{4})Q([1-4])$")
CONTENT_KEYS = ("facts", "interpretation", "korea", "us", "global", "transmission", "risks", "next_checks")


def quarter_period(quarter_id: str) -> tuple[date, date]:
    m = QUARTER_RE.match(quarter_id)
    if not m:
        raise ValueError("분기 ID 형식은 2026Q4 처럼 써야 합니다.")
    y, q = int(m.group(1)), int(m.group(2))
    start = date(y, 3 * (q - 1) + 1, 1)
    end_month = 3 * q
    end = date(y + (end_month == 12), (end_month % 12) + 1, 1)
    from datetime import timedelta
    return start, end - timedelta(days=1)


def add_revision(db: Session, quarter_id: str, *, data_as_of: date, reason: str, content: dict,
                 source_ids: list[int], actor: str) -> MacroRevision:
    if not reason.strip():
        raise ValueError("갱신 이유가 필요합니다.")
    missing = [k for k in ("facts", "interpretation", "risks", "next_checks") if not str(content.get(k, "")).strip()]
    if missing:
        raise ValueError("필수 항목이 비어 있습니다: " + ", ".join(missing))
    if not source_ids:
        raise ValueError("출처가 최소 1개 필요합니다.")
    for sid in source_ids:
        if db.get(Source, sid) is None:
            raise ValueError(f"출처 {sid}가 없습니다.")
    outlook = db.scalar(select(MacroOutlook).where(MacroOutlook.quarter_id == quarter_id))
    if outlook is None:
        start, end = quarter_period(quarter_id)
        outlook = MacroOutlook(quarter_id=quarter_id, period_start=start, period_end=end)
        db.add(outlook)
        db.flush()
    n = db.scalar(select(func.max(MacroRevision.revision_no)).where(MacroRevision.outlook_id == outlook.id)) or 0
    rev = MacroRevision(outlook_id=outlook.id, revision_no=n + 1, data_as_of=data_as_of, reason=reason,
                        content={k: content.get(k, "") for k in CONTENT_KEYS}, source_ids=source_ids)
    db.add(rev)
    db.flush()
    audit(db, actor, "macro.revision", quarter_id, {"revision_no": rev.revision_no})
    return rev


def list_outlooks(db: Session) -> list[dict]:
    out = []
    for o in db.scalars(select(MacroOutlook).order_by(MacroOutlook.quarter_id.desc())):
        revs = list(db.scalars(select(MacroRevision).where(MacroRevision.outlook_id == o.id)
                               .order_by(MacroRevision.revision_no.desc())))
        out.append({"outlook": o, "latest": revs[0] if revs else None, "revisions": revs})
    return out


def is_in_progress(o: MacroOutlook, today: date) -> bool:
    return today <= o.period_end
