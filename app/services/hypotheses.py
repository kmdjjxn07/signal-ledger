"""
투자 가설 발행 gate.

품질점수 = 촉매·실제 개선·수급·상대강도·거시 적합성 각 0~2점 합계(성공확률이 아님).
- Core: 7점 이상 + 다섯 축 중 4축 이상 충족(1점 이상을 '충족'으로 정의)
- Exploration: 6점 이상 7점 미만 + 확신도 '낮음' + 신규 A~C 촉매·유동성·KRX 기준가격·무효화 조건
- 활성 카드 최대 3개, 그중 Exploration 최대 1개
- 새 기업 가설은 해당 종목의 '최신' 심층검증 판이 통과여야 하고, 그 판과 정확히 연결(researchRef)
- 수급·가격 부재, D등급 단독, 관리종목 위험, 명백한 거시 역풍이면 발행하지 않는다.
- 판정일(단기 3·중기 20거래일, 새 서비스 기본값)은 발행 시 실제 캘린더로 고정한다.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Hypothesis, Instrument, InvestorFlow, ResearchVersion
from app.services.audit import audit
from app.services.calendar import CalendarUnknown, add_trading_days, day_info
from app.services.market_data import latest_official_on_or_before
from app.services.research import latest_for_code
from app.services.settings_store import get_setting
from app.services.sources import independent_ac_count, load_sources, only_d_grade
from app.timeutil import today_kst, utcnow

AXES = ("catalyst", "improvement", "flow", "relative_strength", "macro_fit")
AXIS_LABEL = {"catalyst": "촉매", "improvement": "실제 개선", "flow": "수급", "relative_strength": "상대강도",
              "macro_fit": "거시 적합성"}


class IssueRejected(Exception):
    def __init__(self, reasons: list[str]):
        super().__init__(" / ".join(reasons))
        self.reasons = reasons


def classify(scores: dict) -> tuple[str | None, int, int]:
    total, met = 0, 0
    for a in AXES:
        v = scores.get(a)
        if not isinstance(v, int) or v not in (0, 1, 2):
            raise IssueRejected([f"{AXIS_LABEL[a]} 점수는 0, 1, 2 중 하나여야 합니다."])
        total += v
        met += v >= 1
    if total >= 7 and met >= 4:
        return "Core", total, met
    if 6 <= total < 7:
        return "Exploration", total, met
    return None, total, met


def _uid(db: Session, d: date) -> str:
    prefix = f"SL-{d.strftime('%Y%m%d')}-"
    n = db.scalar(select(func.count()).select_from(Hypothesis).where(Hypothesis.uid.like(prefix + "%"))) or 0
    return f"{prefix}{n + 1:02d}"


def issue(db: Session, payload: dict, actor: str, today: date | None = None) -> Hypothesis:
    today = today or today_kst()
    reasons: list[str] = []
    cfg = get_setting(db, "hypothesis_defaults")
    code = str(payload.get("code", "")).strip()
    inst = db.get(Instrument, code)
    if inst is None:
        raise IssueRejected(["종목 마스터에 없는 코드입니다."])
    if inst.is_etf:
        reasons.append("ETF 가설 검증 경로가 아직 구현되지 않아 발행할 수 없습니다.")
    if inst.managed_issue:
        reasons.append("관리종목·거래정지 등 위험 표시 종목입니다.")

    # 연구 연결
    rv = db.get(ResearchVersion, int(payload.get("research_version_id") or 0))
    latest = latest_for_code(db, code)
    if rv is None or rv.code != code:
        reasons.append("이 종목의 심층검증 판과 연결되지 않았습니다.")
    elif rv.status != "통과":
        reasons.append("연결한 심층검증이 통과 상태가 아닙니다.")
    elif latest is not None and latest.id != rv.id:
        reasons.append("더 최근 심층검증 판이 있습니다. 최신 판 기준으로 다시 확인하세요.")

    # 필수 서술
    for f, label in (("catalyst", "핵심 촉매"), ("not_priced_reason", "아직 가격에 반영되지 않았다고 보는 이유"),
                     ("counter_scenario", "반대 시나리오"), ("invalidation", "사실 기반 무효화 조건"),
                     ("next_check", "다음 확인 일정")):
        if not str(payload.get(f, "")).strip():
            reasons.append(f"{label}이(가) 비어 있습니다.")
    direction = payload.get("direction")
    if direction not in ("up", "down"):
        reasons.append("가격 방향은 up 또는 down이어야 합니다.")

    # 점수·등급
    try:
        tier, total, met = classify(payload.get("scores") or {})
    except IssueRejected as e:
        raise IssueRejected(reasons + e.reasons) from None
    confidence = payload.get("confidence", "")
    if tier is None:
        reasons.append(f"품질점수 {total}점 — 6점 미만은 발행하지 않습니다.")
    if tier == "Exploration" and confidence != "낮음":
        reasons.append("Exploration 카드는 확신도 '낮음'이어야 합니다.")
    if tier == "Core" and confidence not in ("낮음", "보통", "높음"):
        reasons.append("확신도를 지정하세요.")

    # 근거
    source_ids = [int(s) for s in payload.get("source_ids", [])]
    srcs = load_sources(db, source_ids)
    if not srcs:
        reasons.append("근거 출처가 없습니다.")
    elif only_d_grade(srcs):
        reasons.append("D등급 근거만으로는 발행할 수 없습니다.")
    elif independent_ac_count(srcs) < 1:
        reasons.append("신규 A~C 촉매 근거가 없습니다.")

    # 가격·유동성·수급
    price = latest_official_on_or_before(db, code, today)
    if price is None:
        reasons.append("KRX 공식 기준가격이 없습니다.")
    elif not price.trading_value:
        reasons.append("기준일 거래대금(유동성)이 확인되지 않습니다.")
    flow_srcs = load_sources(db, [int(s) for s in payload.get("flow_source_ids", [])])
    has_flow_record = price is not None and db.scalar(select(InvestorFlow).where(
        InvestorFlow.code == code, InvestorFlow.trading_date == price.trading_date).limit(1)) is not None
    if not has_flow_record and independent_ac_count(flow_srcs) < 1:
        reasons.append("수급 근거가 없습니다(당일 수급 기록 또는 A~C 출처 필요).")
    if payload.get("macro_headwind"):
        reasons.append("명백한 거시 역풍으로 표시되어 발행하지 않습니다.")

    # 활성 한도
    active = list(db.scalars(select(Hypothesis).where(Hypothesis.status == "활성")))
    if len(active) >= cfg["max_active"]:
        reasons.append(f"활성 카드가 이미 {len(active)}개입니다(최대 {cfg['max_active']}).")
    if tier == "Exploration" and sum(h.quality_tier == "Exploration" for h in active) >= cfg["max_active_exploration"]:
        reasons.append("활성 Exploration 카드는 최대 1개입니다.")

    # 판정일 고정
    short_date = medium_date = None
    if price is not None:
        if day_info(db, price.trading_date).status == "unknown":
            reasons.append("기준일 캘린더가 확인되지 않았습니다.")
        else:
            try:
                short_date = add_trading_days(db, price.trading_date, cfg["short_trading_days"])
                medium_date = add_trading_days(db, price.trading_date, cfg["medium_trading_days"])
            except CalendarUnknown as e:
                reasons.append(f"판정일을 고정할 수 없습니다: {e}")

    if reasons:
        raise IssueRejected(reasons)

    h = Hypothesis(
        uid=_uid(db, today), code=code, name=inst.name, issued_at=utcnow(),
        catalyst=payload["catalyst"].strip(), not_priced_reason=payload["not_priced_reason"].strip(),
        counter_scenario=payload["counter_scenario"].strip(), invalidation=payload["invalidation"].strip(),
        next_check=payload["next_check"].strip(), base_price=price.close, base_price_id=price.id,
        base_date=price.trading_date, direction=direction, short_date=short_date, medium_date=medium_date,
        quality_tier=tier, quality_score=total, score_breakdown={a: payload["scores"][a] for a in AXES},
        confidence=confidence, research_version_id=rv.id, research_code=rv.code,
        research_checked_at=rv.checked_at, source_ids=source_ids, evaluation_policy="absolute-price-v1",
        status="활성")
    db.add(h)
    db.flush()
    audit(db, actor, "hypothesis.issue", h.uid, {"tier": tier, "score": total})
    return h


def invalidate(db: Session, h: Hypothesis, reason: str, actor: str) -> None:
    if not reason.strip():
        raise ValueError("무효화 사유가 필요합니다.")
    if h.status in ("종료", "무효화"):
        raise ValueError("이미 끝난 가설입니다.")
    h.status = "무효화"
    audit(db, actor, "hypothesis.invalidate", h.uid, {"reason": reason})


def update_next_check(db: Session, h: Hypothesis, value: str, actor: str) -> None:
    old = h.next_check
    h.next_check = value
    audit(db, actor, "hypothesis.next_check", h.uid, {"old": old, "new": value})
