# 테스트 목록과 결과

실행: `python -m pytest -q` (2026-11-24, Python 3.13 · Linux) → **72 passed**

모든 테스트는 임시 SQLite에 합성 데이터만 넣고, 네트워크는 가짜 응답(httpx.MockTransport)·가짜 AI·가짜 시계로 대체합니다.
따라서 '실제 KRX/DART/네이버/Gemini 응답과 호환되는지'는 이 테스트로 증명되지 않습니다(guide/PROVIDERS.md의 probe로 확인).

## test_calendar_money.py (7개)

금액 정수·비용 올림, 휴장·특별 거래시간·임시휴장, 캘린더 미확인 시 거래일 판단 거부

- `test_trade_cost_rounds_up_integer`
- `test_pct_change_decimal`
- `test_parse_krw_rejects_decimal`
- `test_unknown_year_is_not_trading`
- `test_weekday_holiday_weekend`
- `test_special_hours_and_closure`
- `test_add_trading_days_skips_holidays_and_stops_at_unknown`

## test_providers.py (10개)

공급자 오류(빈 응답·인증 실패·접근 거부·리다이렉트·네트워크) 구분, 원본 시장·세션·시각 검증, NXT/장외 가격 차단, DART 기업코드 매핑·타사 공시 제거, ETF 공시 미연결, 공식 가격 중복 방지·정정 보존·실패가 성공값을 덮지 않음, 휴장/미발행 구분, 비밀 파라미터 제거

- `test_krx_not_configured_without_key`
- `test_krx_distinguishes_empty_auth_forbidden_network`
- `test_krx_parses_rows_and_rejects_wrong_date`
- `test_krx_index_scaled`
- `test_secret_params_stripped_from_logged_url`
- `test_dart_corp_codes_and_other_company_filtered`
- `test_naver_validation_rules`
- `test_naver_disabled_by_default`
- `test_official_sync_dedupe_correction_and_failure_preserves`
- `test_etf_not_linked_to_disclosures`

## test_briefings_macro.py (8개)

archive 보존, scheduled/published 전환, 지연된 개장 전 작업이 발행본을 덮지 않음, 필수 수치 미검증 시 보류, 휴장일 종가 복제 금지, D등급 사실 발행 차단, 분기 전망 판 append

- `test_pre_brief_publishes_facts_and_creates_scheduled_close`
- `test_pre_brief_without_facts_is_withheld`
- `test_delayed_pre_job_does_not_reset_published_close`
- `test_close_brief_withheld_without_official_and_published_with`
- `test_close_brief_skips_holiday_without_copying`
- `test_archive_keeps_versions_and_publish_validation`
- `test_macro_revisions_append_and_quarter`
- `test_macro_revision_requires_sources_and_preserves_history`

## test_research_hypotheses.py (12개)

독립 근거(재전재·기업자료) 계산, 연구 gate·ETF 차단, 후보 1개, 점수 분류, 가설 고정 필드 불변, 발행 gate(D등급·Exploration·수급·거시·최신 연구·활성 한도), 통계 분모·보합·보류·추적 중, 평가 사건 append-only

- `test_research_gate_independent_sources`
- `test_company_claims_count_once`
- `test_research_requires_next_checks_when_failing_and_blocks_etf`
- `test_passed_research_becomes_single_candidate`
- `test_classify_scores`
- `test_issue_and_immutability`
- `test_issue_rejections`
- `test_issue_requires_latest_research_and_limits`
- `test_active_and_exploration_limits`
- `test_evaluation_hit_flat_pending_and_status`
- `test_statistics_denominators`
- `test_evaluation_events_append_only`

## test_paper.py (11개)

config 잠금, 공급자 미연결=운영 오류(판단 없음), 전체 즉시 체결 흐름과 liveExecutionVerified, 결정 전 가격 차단, 120초 창 만료·이월 없음, 체결가 재검증 취소, 단일 executionOwner, 종료·폐기 실험 거래 차단·재활성화 금지, 원장 append-only·변조 탐지, 정수·현금·비중·손실설계, 정산·기간 연장 금지·평균·산출 대기, 실험별 자본 분리

- `test_register_validates_and_locks_on_first_decision`
- `test_missing_providers_is_ops_error_not_decision`
- `test_full_immediate_fill_and_live_verification`
- `test_pre_decision_price_rejected_then_first_eligible_used`
- `test_window_expiry_no_carry_over`
- `test_revalidation_at_fill_price_cancels`
- `test_single_execution_owner_and_terminal_block`
- `test_paper_events_append_only`
- `test_chain_detects_tampering`
- `test_risk_rules`
- `test_settlement_verified_pending_and_average`

## test_jobs_web_portability.py (15개)

작업 멱등성·잠금·재시도 한도·캘린더 기반 예약, 로그인·CSRF·같은 출처·XSS·비밀값 비노출·보안 헤더, 실제 브라우저 흐름(favicon) 로그인 회귀, 미연결 표시, 빈 상태를 0으로 표시하지 않음, 보유종목 정정 이력·공식 평가, import 멱등성·소급 통계 금지, 백업 복구 검증·훼손 탐지

- `test_job_slot_idempotent_and_lock`
- `test_job_failure_retry_limit`
- `test_due_slots_respect_calendar_and_settings`
- `test_auth_required`
- `test_login_rejects_bad_password_and_csrf`
- `test_all_pages_render_and_secrets_hidden`
- `test_post_requires_csrf_and_same_origin`
- `test_xss_escaped`
- `test_unconfigured_quote_shows_connection_needed`
- `test_empty_states_not_zero`
- `test_holdings_append_only_and_valuation`
- `test_briefs_import_dry_run_then_idempotent`
- `test_backup_and_restore_verification`
- `test_job_run_records_distinguish_registration_from_success`
- `test_login_survives_extra_requests_between_form_and_submit`

## 공개 모드(GitHub Actions + 공개 저장소) — `tests/test_public_mode.py`

| 테스트 | 확인하는 것 |
|---|---|
| `test_state_dump_excludes_private_rows_and_roundtrips` | 공개 상태 파일에 계정·세션·보유종목·원본 응답·잠금 행이 없음, 기록 없는 종목 가격 제외, 같은 DB → 같은 파일, 복원 후 기록 보존 |
| `test_scrub_removes_secret_values` | 오류 문구 등에 섞인 비밀값을 공개 파일에서 가림 |
| `test_paper_chain_survives_state_roundtrip` | 저장→복원 후에도 모의운용 해시 체인 검증 통과 |
| `test_inbox_atomic_per_file_with_ref_sources` | 제출 파일 단위 전부 반영/전부 거부, `ref:` 출처 참조, 허용 안 된 명령·깨진 JSON·중복 참조 이름 거부, 처리 결과 기록 |
| `test_unknown_ref_rejected` | 없는 참조·잘못된 출처 표기 거부 |
| `test_editor_mode_queues_instead_of_writing` | 편집기 모드: DB는 그대로, 미리 검사 통과한 입력만 제출 파일 생성, 대기 중 ref 사용 가능, 공개 원장 작업 실행 차단 |
| `test_direct_mode_still_applies` | 직접 모드는 같은 명령 코드로 바로 반영 |
| `test_static_site_has_no_private_or_write_elements` | 공개 사이트에 폼·관리·보유종목·비밀값·절대경로 링크 없음, 모든 화면 면책 문구, 재생성 시 변경 없음 |
| `test_static_app_refuses_private_paths` | 공개 사이트 생성용 앱은 보유종목·관리·로그인·실시간 시세 경로를 404로 거부 |

추가로 작성 환경에서 `python -m app.ci run`을 제출 파일과 함께 두 번 실행해(키 없음) 반영·거부, 상태 파일 재현성
(두 번째 실행 변경 0), 공개 사이트 링크 이동, `local-refresh`의 계정·보유종목 보존을 확인했습니다.
실제 GitHub 서버에서의 실행과 KRX·DART·네이버 실제 응답은 확인하지 못했습니다.
