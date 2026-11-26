"""
관리자가 화면에서 바꾸는 운영 설정. 비밀키는 저장하지 않는다(환경변수 전용).
기본값은 '새 서비스의 기본값'이며 과거 시스템 설정을 복원한 것이 아니다.
"""
from __future__ import annotations

from copy import deepcopy

from sqlalchemy.orm import Session

from app.models import Setting

DEFAULTS: dict[str, dict] = {
    # 개장 전·마감 후 브리핑 시각은 관리자가 정한다. 비어 있으면 해당 작업은 예약되지 않는다.
    "schedule": {
        "pre_brief_time": None,          # 예: "08:10"
        "close_brief_time": None,        # 예: "16:20"
        "settlement_time": None,         # 예: "17:00"
        "intraday_audit_hours": [10, 11, 12, 13, 14, 15],
        "engine_interval_min": 10,
    },
    # 네이버 원본 응답 검증 기준. 실제 응답을 probe로 확인한 뒤 소유자가 확정해야 한다.
    "naver_validation": {
        "confirmed_by_owner": False,
        "code_fields": ["itemCode", "code"],
        "price_field": "closePrice",
        "time_field": "localTradedAt",
        "market_status_field": "marketStatus",
        "session_type_field": "marketSessionType",
        "exchange_type_field": "stockExchangeType",
        "allowed_market_status": ["OPEN"],
        "allowed_session_types": ["REGULAR"],
        "allowed_exchange_codes": ["KS", "KQ"],
        "forbidden_fields_as_price": ["overMarketPriceInfo", "integratedPriceInfo"],
    },
    "paper_defaults": {
        "initial_capital": 1_000_000,
        "duration_trading_days": 5,
        "max_positions": 3,
        "max_position_bp": 3500,
        "cost_bps": 15,
        "fill_window_sec": 120,
        "max_loss_bp_of_nav": 100,
        "exploration_min_bp": 1000,
        "exploration_max_bp": 1500,
    },
    "hypothesis_defaults": {"short_trading_days": 3, "medium_trading_days": 20, "max_active": 3,
                            "max_active_exploration": 1},
    "ai": {"enabled": False, "provider": "gemini"},
}


def get_setting(db: Session, key: str) -> dict:
    row = db.get(Setting, key)
    merged = deepcopy(DEFAULTS.get(key, {}))
    if row is not None:
        merged.update(row.value)
    return merged


def put_setting(db: Session, key: str, value: dict) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = {**row.value, **value}
