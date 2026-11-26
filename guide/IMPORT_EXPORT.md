# 가져오기·내보내기

## 내보내기
`GET /api/export` 또는 `python -m app.cli export out.json`. 형식은 guide/API.md 참고. DB가 정본이며 JSON은 생성물입니다.

## 가져오기 — briefs.json (과거 원장)
과거 데이터 파일이 제공될 때만 사용합니다. 파일이 없으면 과거 기록은 빈 상태이며 데모 데이터로 채우지 않습니다.

절차: 크기(20MB 이하)·JSON 형식·최상위 키 검사 → (적용 시) DB 자동 백업 → 미리보기 보고서 → 항목별 SHA-256으로 기존 기록과 중복 확인 →
매핑·충돌(알 수 없는 키) 보고 → 한 트랜잭션으로 적용 → `import_runs`와 감사로그에 기록.

| 원본 위치 | 저장 위치(`legacy_records.kind`) |
|---|---|
| `pre`, `close`, `archive[]` | `briefing` |
| `hypotheses[]` (absolute-price-v1이 아닌 것) | `legacyRelative` |
| `hypotheses[]` (absolute-price-v1) | `legacyHypothesis` |
| `deepResearch`, `deepResearch.history[]` | `research` |
| `macroOutlook` | `macro` |
| `modelPortfolio.runs[]`, `archivedRuns[]` | `experiment` |

- 원본 ID·시각·출처·정책은 payload에 그대로 보존됩니다.
- 모두 **읽기 전용**이며, 신규 적중률 통계·연구 통과·품질군·체결 인증을 소급 부여하지 않습니다. 과거 실험을 재개하지 않습니다.
- 같은 파일을 다시 적용해도 중복 저장되지 않습니다(멱등성).

## 가져오기 — 보유종목 JSON
```json
[{"code": "005380", "name": "현대차",
  "purchase_history": [{"date": "2026-04-24", "quantity": 8, "unit_price": 513000}]}]
```
이미 등록된 종목은 건너뜁니다. 거래는 사용자 기록으로 append됩니다.
