# 시그널 원장

한국 주식 리서치·브리핑·투자 가설 검증·모의투자를 한곳에 모은 **개인용 투자 연구 웹서비스**입니다.
기존 ChatGPT Sites 사이트와 무관한 독립 서비스이며, 실제 증권 주문 기능은 없습니다.

> 근거·시각·검증 가능성·기록의 일관성을 그럴듯한 문장보다 우선합니다.
> 확인되지 않은 값은 0이나 가짜 숫자로 채우지 않고 '확인 안 됨', '연결 필요'로 표시합니다.

---

## 0. 서버 없이 무료로 쓰기 (권장) → `guide/GITHUB_무료운영.md`

컴퓨터를 계속 켜 둘 수 없다면 **GitHub Actions + 공개 저장소** 방식을 쓰세요(0원).
GitHub가 평일 10분마다 원장을 실행하고, 결과를 공개 읽기 전용 사이트(GitHub Pages)로 보여 줍니다.

- 내 컴퓨터 = **편집기**: 화면 입력은 `inbox/` 제출 파일로 저장 → GitHub Desktop으로 올리면 서버 규칙으로 검증·반영
- **공개되는 것**: 브리핑·연구·가설·판정·모의운용 원장(`state/`), 제출 파일, 코드
- **공개되지 않는 것**: 보유종목·로그인 정보(내 컴퓨터에만), API 키(GitHub Secrets에만), 공급자 원본 응답(해시와 링크만)
- 관련 파일: `.github/workflows/ledger.yml`, `app/ci.py`, `app/state.py`, `app/site.py`, `app/commands.py`,
  `refresh_local.bat`, `preview_site.bat`, `first_publish.bat`

## 1. 지금 상태 한눈에 보기

| 구분 | 내용 |
|---|---|
| **구현 완료·테스트 통과** | 로그인(소유자 1명), 9개 화면, GitHub Actions 무료 운영(제출 파일·공개 상태 파일·정적 공개 사이트·편집기 모드), 브리핑 발행 규칙, 거래소 캘린더, 출처 등급, 기업 9항목 검증 gate, 가설 발행 gate·고정 필드 불변, absolute-price-v1 평가·통계, 모의운용(원장 해시 체인·위험 검증·120초 즉시 체결·종료·정산), 보유종목, 예약 작업 실행기(잠금·멱등성·재시도), 관리자 화면, 가져오기/내보내기, 백업·복구 검증 — **자동 테스트 72개 통과** |
| **코드는 있으나 실제 연결 대기** | KRX 공식 일별 가격, DART 공시·기업코드, 네이버 참고 시세, FRED 해외 지표, Gemini AI. 키를 넣기 전에는 화면에 '연결 필요'로 표시됩니다. 작성 환경에서 이 사이트들에 접속이 막혀 있어 **실제 응답으로는 검증하지 못했습니다**(테스트는 가짜 응답으로 검증). |
| **미구현(차단 상태 유지)** | 당일 종목별 투자자 수급 공급자(요건 충족 공급자를 찾지 못함 → 모의 주문 차단), 서울 외환시장 환율 공급자, ETF 전용 검증 경로(ETF 가설 발행 차단) |
| **배포** | GitHub Actions 실행기와 공개 사이트 생성은 작성 환경에서 모의 실행으로 확인했습니다. 실제 GitHub 서버에서 돌려 보지는 못했습니다(계정 없음). 해외 서버(GitHub)에서 KRX·네이버 접속이 허용되는지도 미확인입니다. Docker/PostgreSQL 경로는 실제 서버로 시험하지 않았습니다. |

## 2. 기술 선택 (짧게)

- **Python 3 + FastAPI + SQLAlchemy**: 사용자 PC에 이미 Python이 있어 바로 실행 가능. 명세의 TypeScript 대신 동등한 대안으로 선택했습니다.
- **DB**: 개발은 SQLite(설치 불필요), 운영은 PostgreSQL. 스키마는 Alembic 마이그레이션으로 관리합니다.
- **금액**: 원 단위 정수, 비율은 bp 정수. 부동소수점을 쓰지 않습니다.
- **예약 작업**: 웹서버와 별도 프로세스(`python -m app.worker`). 브라우저를 닫아도 동작합니다.
- **화면**: 서버에서 그리는 한국어 HTML(PC·모바일 겸용), 외부 스크립트·CDN 없음.
- **Windows + Python 3.14**: 모든 의존성이 미리 컴파일된 휠로 설치되는 것을 확인했습니다(컴파일 도구 불필요).

## 3. 처음 설치하기 (Windows, 초보자용)

1. **Python 설치 확인**: 명령 프롬프트에서 `python --version`. 없으면 python.org에서 설치하고 *Add Python to PATH*를 꼭 체크하세요.
2. 압축을 푼 `signal-ledger` 폴더를 엽니다.
3. **`setup_windows.bat` 더블클릭**. 가상환경 생성 → 패키지 설치 → DB 생성 → 소유자 계정 만들기까지 진행합니다.
   아이디와 비밀번호(10자 이상, 입력이 화면에 안 보이는 게 정상)를 입력하세요.
4. **`run_web.bat` 실행** → 브라우저에서 `http://localhost:8000` 접속 → 로그인.
5. **`run_worker.bat` 실행** (다른 창). 예약 작업은 이 창이 열려 있을 때 동작합니다.

### 처음 로그인한 뒤 해야 할 설정 (관리·운영 화면)

1. **거래소 캘린더**: 올해 KRX 휴장일을 공식 공지에서 확인해 등록합니다. 등록 전에는 평일이라도 거래일로 보지 않아 모든 예약 작업이 멈춰 있습니다(의도된 동작).
2. **예약 시각**: 개장 전·마감 후·정산 시각을 HH:MM으로 입력합니다. 비워 두면 예약되지 않습니다.
3. **API 키**: `.env` 파일에 `KRX_API_KEY`, `DART_API_KEY` 등을 넣고 웹·worker를 다시 시작합니다(guide/PROVIDERS.md).
4. **데이터 확인**: `python -m app.cli probe-krx --date 2026-11-20`처럼 실제 응답 구조를 먼저 확인하세요.

### 수동(명령어) 설치

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m app.cli init-db
python -m app.cli create-owner
uvicorn app.main:app --port 8000      :: 창 1
python -m app.worker                  :: 창 2
```

## 4. 화면 구성

| 화면 | 주소 | 내용 |
|---|---|---|
| 오늘의 브리핑 | `/` | 개장 전·마감 후. 요약 → 사실 → 해석 → 다음 확인 → 출처, 데이터 지연·누락 표 |
| Research & Theses | `/research`, `/research/{코드}` | 현재 심층검증 후보, 검증 이력, 가설·판정·통계(표본 20건 미만은 '추적 중') |
| 모의투자 | `/paper`, `/paper/{번호}` | 실험 등록, 설정(첫 판단 후 잠김), 현금·포지션·원장 로그·해시 체인 |
| 내 보유종목 | `/holdings` | 사용자 입력 거래 기록(정정 이력 보존), KRX 종가 공식 평가, 전일 대비 |
| 종목 검색 | `/search`, `/stock/{코드}` | 참고 시세(B), KRX 공식 차트(A), 최근 공시. 누락 구간 표시 |
| Big Picture | `/big-picture` | 분기 전망과 수정 이력 |
| 지난 브리핑 | `/archive` | 발행본 보관(삭제 없음), 가져온 과거 브리핑(읽기 전용) |
| 쉬운 용어 사전 | `/glossary` | 초보자용 용어 |
| 관리·운영 | `/admin` | 상태 요약, 공급자, 작업, 캘린더, 출처, 원장 점검, 가져오기·백업 |
| 제출 파일 | `/admin/inbox` | 편집기 모드에서 만든 제출 파일과 GitHub 처리 결과(반영/거부·이유) |
| 운영 상태 | `/status` | 공개 사이트용 운영 상태(공급자 결과, 작업 기록, 원장 무결성) |

## 5. 테스트

```bat
.venv\Scripts\activate
python -m pytest -q
```

결과(2026-11-24, Python 3.13 / Linux에서 실행): **72 passed**. 테스트는 임시 DB에 합성 데이터만 넣으며 운영 DB를 건드리지 않습니다.
테스트 목록과 대응하는 명세 항목은 `guide/TESTS.md`에 있습니다.

## 6. 문서

- `guide/GITHUB_무료운영.md` — **서버 없이 무료 운영: GitHub 가입부터 첫 실행까지 단계별 안내**
- `guide/inbox_examples/` — 제출 파일 형식
- `guide/OPERATIONS.md` — 운영 정책(출처 등급, 발행 규칙, 가설·모의운용 규칙, 권한 분리)
- `guide/PROVIDERS.md` — 데이터 공급자 설정과 검증 절차
- `guide/API.md` — JSON API
- `guide/IMPORT_EXPORT.md` — 가져오기·내보내기 형식
- `guide/BACKUP.md` — 백업·복구 절차
- `guide/DEPLOY.md` — 배포(Docker/PostgreSQL)와 예약 작업 설정
- `guide/TESTS.md` — 테스트 목록과 결과

## 7. 하지 않는 것

실제 주문·차입·공매도·레버리지, 접근 제한(403·로그인벽) 우회, 가짜 데이터로 운영 화면 채우기, 과거 기록 소급 생성·인증,
개인 보유종목·실험 원장을 외부 AI로 전송.
