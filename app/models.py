"""
DB 모델 (정본).

원칙
- 금액·가격은 원 단위 정수(BigInteger), 비율은 bp 정수. 부동소수점 금지.
- 시각은 UTC naive로 저장하고 화면에서 KST로 변환한다.
- 원장형 테이블(출처, 공식가격, 관측, 가설평가, 모의운용 사건, 감사로그, 보유종목 거래)은
  append-only다. ORM 레벨에서 UPDATE/DELETE를 막는다(아래 _guard_* 이벤트).
- 가설의 고정 필드(기준가격·방향·판정일·무효화 조건 등)는 발행 후 변경할 수 없다.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON, BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, event, inspect,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutil import utcnow


class LedgerViolation(Exception):
    """append-only 또는 불변 필드 규칙 위반."""


# ---------------------------------------------------------------- 인증·설정·감사

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Setting(Base):
    """관리자가 바꾸는 운영 설정(예약 시각 등). 비밀키는 여기에 저장하지 않는다(환경변수 전용)."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String(100))  # "owner:<name>" / "job:<name>" / "system"
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


# ---------------------------------------------------------------- 출처·원본

class RawDocument(Base):
    """수집한 원본 응답. 해시로 식별한다. 해시는 변조 탐지용이지 사실성 보증이 아니다."""
    __tablename__ = "raw_documents"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_type: Mapped[str] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Source(Base):
    """브리핑·연구·가설에서 인용하는 출처. 등급 A~D. origin_key가 같으면 독립 근거로 세지 않는다."""
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(100))
    grade: Mapped[str] = mapped_column(String(1))
    origin_key: Mapped[str] = mapped_column(String(200))  # 같은 보도자료를 재전재한 기사는 같은 origin_key
    is_company_claim: Mapped[bool] = mapped_column(Boolean, default=False)  # 기업 공식자료(경영진 주장)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime)
    raw_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 제출 파일에서 아직 번호가 없는 출처를 가리킬 때 쓰는 이름(예: "ref:hd-order-2026q4")
    ref_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FetchLog(Base):
    """공급자 호출 결과. 빈 응답·미발행·인증 실패·접근 거부·휴장을 서로 다른 status로 남긴다."""
    __tablename__ = "fetch_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    operation: Mapped[str] = mapped_column(String(100))
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(30))
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------- 캘린더·종목·가격

class CalendarYear(Base):
    """연도별 휴장일 목록을 공식 공지로 확인했는지. 확인되지 않은 연도는 거래일 판단을 하지 않는다."""
    __tablename__ = "calendar_years"
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    regular_open: Mapped[str] = mapped_column(String(5), default="09:00")
    regular_close: Mapped[str] = mapped_column(String(5), default="15:30")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class CalendarException(Base):
    """휴장(holiday), 임시휴장(closure), 특별 거래시간(special_hours)."""
    __tablename__ = "calendar_exceptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(20))
    open_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    close_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Instrument(Base):
    __tablename__ = "instruments"
    code: Mapped[str] = mapped_column(String(12), primary_key=True)  # 6자리 단축코드 또는 IDX:KOSPI
    name: Mapped[str] = mapped_column(String(200))
    market: Mapped[str | None] = mapped_column(String(20), nullable=True)  # KOSPI/KOSDAQ/INDEX
    is_etf: Mapped[bool] = mapped_column(Boolean, default=False)
    corp_code: Mapped[str | None] = mapped_column(String(8), nullable=True)  # DART 고유번호(기업만)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    managed_issue: Mapped[bool] = mapped_column(Boolean, default=False)  # 관리종목·거래정지 등 위험 표시
    source: Mapped[str] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OfficialPrice(Base):
    """KRX 공식 일별 가격. 정정값은 새 행으로 추가하고 이전 행을 supersedes로 연결한다."""
    __tablename__ = "official_prices"
    __table_args__ = (UniqueConstraint("code", "trading_date", "raw_sha256", name="uq_official_price_content"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trading_date: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[int] = mapped_column(BigInteger)
    open: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    high: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    low: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    trading_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    market_cap: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 지수는 소수점 둘째 자리까지를 정수(×100)로 저장한다.
    scale: Mapped[int] = mapped_column(Integer, default=1)
    provider: Mapped[str] = mapped_column(String(50))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_sha256: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("official_prices.id"), nullable=True)


class Observation(Base):
    """장중 참고 시세 관측(네이버 원본 등). 모의체결 근거 적격 여부를 원본 필드로 판정해 함께 저장."""
    __tablename__ = "observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    requested_at: Mapped[datetime] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    source_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 원본 기준시각(UTC로 변환)
    price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    market_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    session_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    exchange_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    ineligible_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class InvestorFlow(Base):
    """당일 종목별 투자자 수급. 날짜·확인시각·단위·KRX 정규장 범위가 모두 있어야 저장한다."""
    __tablename__ = "investor_flows"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    trading_date: Mapped[date] = mapped_column(Date)
    checked_at: Mapped[datetime] = mapped_column(DateTime)
    scope: Mapped[str] = mapped_column(String(40))  # 반드시 "KRX_REGULAR"
    investor_type: Mapped[str] = mapped_column(String(30))
    net_value_krw: Mapped[int] = mapped_column(BigInteger)
    unit: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(50))
    grade: Mapped[str] = mapped_column(String(1))
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=True)
    raw_sha256: Mapped[str] = mapped_column(String(64))


# ---------------------------------------------------------------- 브리핑·거시

class Briefing(Base):
    """kind: pre/close. status: scheduled → draft → published, 또는 withheld(필수 수치 미검증).
    같은 거래일·종류에 여러 버전이 쌓일 수 있고, 화면은 최신 published를 보여준다."""
    __tablename__ = "briefings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trading_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(10))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20))
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MacroOutlook(Base):
    __tablename__ = "macro_outlooks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quarter_id: Mapped[str] = mapped_column(String(10), unique=True)  # 예: 2026Q4
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MacroRevision(Base):
    """분기 전망의 각 판. 수정은 새 판을 추가한다(이전 판 보존)."""
    __tablename__ = "macro_revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    outlook_id: Mapped[int] = mapped_column(ForeignKey("macro_outlooks.id"))
    revision_no: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    data_as_of: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    content: Mapped[dict] = mapped_column(JSON)  # facts, interpretation, variables, transmission, risks, next_checks
    source_ids: Mapped[list] = mapped_column(JSON, default=list)


# ---------------------------------------------------------------- 기업 검증·가설

class ResearchVersion(Base):
    """기업 심층검증의 한 판. 교체 시 새 판을 추가하고 이전 판은 그대로 보존한다(history)."""
    __tablename__ = "research_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime)
    items: Mapped[dict] = mapped_column(JSON)  # 9개 항목: {key: {conclusion, verified, source_ids}}
    red_flags: Mapped[list] = mapped_column(JSON, default=list)
    liquidity_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # "통과" / "관찰 보류"
    core_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    fully_verified_items: Mapped[int] = mapped_column(Integer, default=0)
    failed_reasons: Mapped[list] = mapped_column(JSON, default=list)
    next_checks: Mapped[list] = mapped_column(JSON, default=list)
    is_current_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


HYPOTHESIS_IMMUTABLE = {
    "uid", "code", "issued_at", "catalyst", "not_priced_reason", "counter_scenario", "invalidation",
    "base_price", "base_price_id", "base_date", "direction", "short_date", "medium_date",
    "quality_tier", "quality_score", "score_breakdown", "confidence", "research_version_id",
    "research_code", "research_checked_at", "source_ids", "evaluation_policy",
}


class Hypothesis(Base):
    __tablename__ = "hypotheses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str] = mapped_column(String(30), unique=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(200))
    issued_at: Mapped[datetime] = mapped_column(DateTime)
    catalyst: Mapped[str] = mapped_column(Text)
    not_priced_reason: Mapped[str] = mapped_column(Text)
    counter_scenario: Mapped[str] = mapped_column(Text)
    invalidation: Mapped[str] = mapped_column(Text)
    next_check: Mapped[str] = mapped_column(Text)  # 다음 확인 일정 (변경 시 감사로그)
    base_price: Mapped[int] = mapped_column(BigInteger)
    base_price_id: Mapped[int] = mapped_column(ForeignKey("official_prices.id"))
    base_date: Mapped[date] = mapped_column(Date)
    direction: Mapped[str] = mapped_column(String(4))  # up/down
    short_date: Mapped[date] = mapped_column(Date)
    medium_date: Mapped[date] = mapped_column(Date)
    quality_tier: Mapped[str] = mapped_column(String(12))  # Core/Exploration
    quality_score: Mapped[int] = mapped_column(Integer)
    score_breakdown: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[str] = mapped_column(String(10))
    research_version_id: Mapped[int] = mapped_column(ForeignKey("research_versions.id"))
    research_code: Mapped[str] = mapped_column(String(12))
    research_checked_at: Mapped[datetime] = mapped_column(DateTime)
    source_ids: Mapped[list] = mapped_column(JSON)
    evaluation_policy: Mapped[str] = mapped_column(String(30), default="absolute-price-v1")
    status: Mapped[str] = mapped_column(String(20), default="활성")  # 활성/단기확정/중기추적/종료/무효화


class HypothesisEvaluation(Base):
    """판정 사건(append-only). outcome: hit/miss/flat/pending. 정정은 supersedes_id로 새 사건 추가."""
    __tablename__ = "hypothesis_evaluations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hypothesis_id: Mapped[int] = mapped_column(ForeignKey("hypotheses.id"), index=True)
    horizon: Mapped[str] = mapped_column(String(10))  # short/medium
    judgement_date: Mapped[date] = mapped_column(Date)
    price_id: Mapped[int | None] = mapped_column(ForeignKey("official_prices.id"), nullable=True)
    close: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    change_pct: Mapped[str | None] = mapped_column(String(20), nullable=True)  # Decimal 문자열
    outcome: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("hypothesis_evaluations.id"), nullable=True)


class LegacyRecord(Base):
    """가져온 과거 기록(상대성과 가설 등). 읽기 전용, 신규 통계에 포함하지 않는다."""
    __tablename__ = "legacy_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))  # legacyRelative / briefing / research / journal ...
    original_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    import_id: Mapped[int] = mapped_column(ForeignKey("import_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- 모의운용

EXPERIMENT_CONFIG_FIELDS = {"initial_capital", "duration_trading_days", "max_positions", "max_position_bp", "cost_bps"}


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    initial_capital: Mapped[int] = mapped_column(BigInteger)
    duration_trading_days: Mapped[int] = mapped_column(Integer)
    max_positions: Mapped[int] = mapped_column(Integer)
    max_position_bp: Mapped[int] = mapped_column(Integer)
    cost_bps: Mapped[int] = mapped_column(Integer)
    benchmark: Mapped[str] = mapped_column(String(20), default="IDX:KOSPI")
    # 등록 / 첫판단대기 / 운용중 / 정산대기 / 검증완료 / 폐기
    status: Mapped[str] = mapped_column(String(20), default="등록")
    execution_owner: Mapped[str] = mapped_column(String(50), default="engine")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    config_locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_imported_readonly: Mapped[bool] = mapped_column(Boolean, default=False)


class PaperEvent(Base):
    """모의운용 사건(append-only, 실험별 해시 체인).
    type: lifecycle/decision/order/fill/cancel/expiry/nav/settlement/ops_error"""
    __tablename__ = "paper_events"
    __table_args__ = (UniqueConstraint("experiment_id", "seq", name="uq_paper_seq"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(20))
    at: Mapped[datetime] = mapped_column(DateTime)
    payload: Mapped[dict] = mapped_column(JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


# ---------------------------------------------------------------- 보유종목

class Holding(Base):
    __tablename__ = "holdings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HoldingTransaction(Base):
    """사용자 입력 매수·매도 기록(append-only). 수정은 새 행 + voids_id로 이전 행 무효화."""
    __tablename__ = "holding_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    holding_id: Mapped[int] = mapped_column(ForeignKey("holdings.id"), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    side: Mapped[str] = mapped_column(String(4))  # buy/sell
    quantity: Mapped[int] = mapped_column(BigInteger)
    unit_price: Mapped[int] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    voids_id: Mapped[int | None] = mapped_column(ForeignKey("holding_transactions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- 작업·가져오기

class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (UniqueConstraint("job_name", "slot_key", name="uq_job_slot"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(50))
    slot_key: Mapped[str] = mapped_column(String(100))  # 멱등성 키 (예: 2026-11-24T10:00)
    status: Mapped[str] = mapped_column(String(20))  # running/succeeded/failed/skipped
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class JobLock(Base):
    __tablename__ = "job_locks"
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    holder: Mapped[str] = mapped_column(String(100))
    acquired_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class ImportRun(Base):
    __tablename__ = "import_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(300))
    file_sha256: Mapped[str] = mapped_column(String(64))
    dry_run: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(20))
    report: Mapped[dict] = mapped_column(JSON)
    backup_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- append-only / 불변 필드 보호

APPEND_ONLY = (AuditLog, RawDocument, Source, FetchLog, OfficialPrice, Observation, InvestorFlow,
               HypothesisEvaluation, PaperEvent, HoldingTransaction, MacroRevision, LegacyRecord)


def _forbid_update(mapper, connection, target):  # noqa: ANN001
    raise LedgerViolation(f"{type(target).__name__}는 append-only입니다. 수정 대신 정정 기록을 추가하세요.")


def _forbid_delete(mapper, connection, target):  # noqa: ANN001
    raise LedgerViolation(f"{type(target).__name__}는 삭제할 수 없습니다.")


for _cls in APPEND_ONLY:
    event.listen(_cls, "before_update", _forbid_update)
    event.listen(_cls, "before_delete", _forbid_delete)


@event.listens_for(Hypothesis, "before_update")
def _guard_hypothesis(mapper, connection, target):  # noqa: ANN001
    state = inspect(target)
    for attr in HYPOTHESIS_IMMUTABLE:
        if state.attrs[attr].history.has_changes():
            raise LedgerViolation(f"가설의 고정 필드 '{attr}'는 발행 후 변경할 수 없습니다.")


@event.listens_for(Hypothesis, "before_delete")
def _guard_hypothesis_delete(mapper, connection, target):  # noqa: ANN001
    raise LedgerViolation("발행된 가설은 삭제할 수 없습니다.")


@event.listens_for(Experiment, "before_update")
def _guard_experiment(mapper, connection, target):  # noqa: ANN001
    state = inspect(target)
    locked_hist = state.attrs["config_locked_at"].history
    was_locked = bool(locked_hist.deleted and locked_hist.deleted[0]) or (
        target.config_locked_at is not None and not locked_hist.has_changes())
    if was_locked:
        for attr in EXPERIMENT_CONFIG_FIELDS | {"start_date", "end_date", "execution_owner", "benchmark"}:
            if state.attrs[attr].history.has_changes():
                raise LedgerViolation(f"첫 판단 이후 실험 설정 '{attr}'는 바꿀 수 없습니다.")
    if state.attrs["status"].history.has_changes():
        old = state.attrs["status"].history.deleted
        if old and old[0] in ("검증완료", "폐기"):
            raise LedgerViolation("종료된(검증완료·폐기) 실험은 다시 활성화할 수 없습니다.")


@event.listens_for(Experiment, "before_delete")
def _guard_experiment_delete(mapper, connection, target):  # noqa: ANN001
    raise LedgerViolation("실험은 삭제할 수 없습니다. 폐기 상태로 전환하세요.")
