"""
출처와 원본 보관.

등급 정책(guide/OPERATIONS.md에도 기록):
- A: 거래소·공식 통계·규제 공시·기업 공식 실적자료 등 1차 자료
- B: 출처와 시장 범위를 확인할 수 있는 데이터 제공처
- C: 신뢰할 수 있는 언론·리서치의 명시적인 보도·분석
- D: 미확인 게시물·소문·출처 불명

독립 근거 계산: origin_key가 같은 출처(같은 보도자료를 재전재한 기사 등)는 하나로 센다.
기업 공식자료(is_company_claim)는 경영진 주장일 수 있으므로, '독립 검증' 계산에서는
기업 자료끼리만으로 2개를 채울 수 없게 한다(기업 자료는 최대 1개로 셈).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RawDocument, Source

GRADES = ("A", "B", "C", "D")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(obj) -> str:  # noqa: ANN001
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def store_raw(db: Session, provider: str, url: str | None, body: str, media_type: str = "application/json") -> str:
    digest = sha256_text(body)
    if db.get(RawDocument, digest) is None:
        db.add(RawDocument(sha256=digest, provider=provider, url=url, media_type=media_type, body=body))
        db.flush()
    return digest


def add_source(db: Session, *, url: str, title: str, provider: str, grade: str, origin_key: str | None,
               checked_at: datetime, published_at: datetime | None = None, is_company_claim: bool = False,
               raw_sha256: str | None = None, note: str | None = None, ref_key: str | None = None) -> Source:
    grade = grade.upper()
    if grade not in GRADES:
        raise ValueError("출처 등급은 A, B, C, D 중 하나여야 합니다.")
    if not url.startswith(("https://", "http://")):
        raise ValueError("출처 URL은 http(s)로 시작해야 합니다.")
    if ref_key is not None:
        ref_key = ref_key.strip()
        if not ref_key or len(ref_key) > 100:
            raise ValueError("참조 이름은 1~100자여야 합니다.")
        from sqlalchemy import select as _select
        if db.scalar(_select(Source).where(Source.ref_key == ref_key)) is not None:
            raise ValueError(f"참조 이름 '{ref_key}'이(가) 이미 쓰였습니다.")
    src = Source(url=url, title=title, provider=provider, grade=grade, origin_key=(origin_key or url).strip(),
                 checked_at=checked_at, published_at=published_at, is_company_claim=is_company_claim,
                 raw_sha256=raw_sha256, note=note, ref_key=ref_key)
    db.add(src)
    db.flush()
    return src


def load_sources(db: Session, ids: list[int]) -> list[Source]:
    if not ids:
        return []
    return list(db.scalars(select(Source).where(Source.id.in_(ids))))


def independent_ac_count(sources: list[Source]) -> int:
    """A~C 등급, origin_key 기준 중복 제거, 기업 공식자료는 최대 1개로 센다."""
    origins: set[str] = set()
    company_counted = False
    for s in sources:
        if s.grade not in ("A", "B", "C"):
            continue
        if s.origin_key in origins:
            continue
        if s.is_company_claim:
            if company_counted:
                continue
            company_counted = True
        origins.add(s.origin_key)
    return len(origins)


def only_d_grade(sources: list[Source]) -> bool:
    return bool(sources) and all(s.grade == "D" for s in sources)
