# API

모든 경로는 로그인 세션 쿠키가 필요합니다(없으면 HTML은 `/login`으로 303, API는 401 JSON).
자동 API 문서(`/docs`)는 보안상 꺼 두었습니다. 쓰기 요청은 모두 HTML 폼 POST이며 `csrf_token`과 같은 출처(Origin/Referer)를 검사합니다.

## 읽기 (JSON)

| 메서드·경로 | 설명 |
|---|---|
| `GET /health` | 로그인 불필요. `{"status":"ok","env":...}` |
| `GET /api/status` | 배포·AI·수집·판단·검증된 체결 상태, `liveExecutionVerified`, 엔진 상태 |
| `GET /api/export` | 호환 JSON 내보내기(아래) |
| `GET /api/quote/{6자리}` | 참고 시세 조회·원본 저장. `status`가 `ok`가 아니면 `detail`에 사유(`not_configured` 등). 공식 평가 아님 |

### `/api/export` 최상위 키
`updatedAt`, `currentTradingDate`, `pre`, `close`, `archive`, `deepResearch{candidate, status, history}`, `hypotheses[]`,
`hypothesisEvaluationPolicy`, `performance`, `performanceHistory`, `macroOutlook[]`, `modelPortfolio{runs[], verified}`, `systemRecovery`.
가설 항목은 `evaluationPolicy: "absolute-price-v1"`, `absoluteDirection`, `qualityTier`, `qualityScore`, `scoreBreakdown`,
`researchRef{code, checkedAt}`를 포함합니다. 모의운용 run은 전체 사건과 해시를 포함합니다.

## 쓰기 (폼 POST, 모두 CSRF 필요)

| 경로 | 설명 |
|---|---|
| `POST /login`, `POST /logout` | 로그인·로그아웃 |
| `POST /admin/schedule` | 예약 시각 |
| `POST /admin/calendar/year`, `/admin/calendar/exception` | 캘린더 |
| `POST /admin/naver/confirm` | 네이버 원본 검증 기준 확정 |
| `POST /admin/jobs/run` | 작업 수동 실행(같은 잠금·멱등성 규칙) |
| `POST /admin/official/sync`, `/admin/dart/corp-codes`, `/admin/instrument` | 데이터 동기화·종목 보정 |
| `POST /admin/sources` | 출처 등록 |
| `POST /admin/research` | 심층검증 판 저장 |
| `POST /admin/hypothesis`, `/admin/hypothesis/{id}/invalidate`, `/admin/hypothesis/{id}/next-check` | 가설 |
| `POST /admin/briefing`, `/admin/briefing/{id}/publish` | 브리핑 초안·발행 |
| `POST /admin/macro` | 분기 전망 새 판 |
| `POST /admin/experiments`, `/admin/experiments/{id}/discard` | 모의 실험 등록·폐기 |
| `POST /admin/holdings`, `/admin/holdings/{id}/tx` | 보유종목·거래(정정은 `voids_id`) |
| `POST /admin/import` | 파일 가져오기(`kind`=briefs/holdings, `apply` 없으면 미리보기) |
| `POST /admin/backup` | 백업 + 복구 검증 |
