# 데이터 공급자 설정

모든 공급자는 `.env`에 키를 넣고 웹·worker를 다시 시작하면 켜집니다. 키가 없으면 '연결 필요'로 표시되며, 가짜 데이터로 대체하지 않습니다.
**이 코드는 작성 환경의 네트워크 제한 때문에 실제 응답으로 검증하지 못했습니다.** 아래 probe 명령으로 먼저 실제 응답을 확인하세요.
접근이 거부(403)되거나 유료 제한이 있으면 우회하지 않고 오류로 기록합니다.

| 공급자 | 환경변수 | 등급 | 용도 | 확인 명령 |
|---|---|---|---|---|
| KRX 정보데이터시스템 Open API | `KRX_API_KEY` | A | 주식·지수 공식 일별 가격, 종목 마스터 | `python -m app.cli probe-krx --date YYYY-MM-DD` |
| DART Open API | `DART_API_KEY` | A | 공시 목록, 종목코드→기업 고유번호 | `python -m app.cli probe-dart` |
| 네이버 공개 시세 원본 | `NAVER_QUOTE_ENABLED=true` | B | 장중 참고 시세, 모의체결 근거 후보 | `python -m app.cli probe-naver 005930` |
| FRED | `FRED_API_KEY` | B | 미국 금리·지수·WTI | 개장 전 브리핑 실행 후 관리 화면 |
| Gemini | `GEMINI_API_KEY`, `GEMINI_MODEL` | — | 해석 제안·모의판단 제안 | 관리 화면 상태 요약 |

## KRX
- 엔드포인트: `https://data-dbg.krx.co.kr/svc/apis` + `/sto/stk_bydd_trd`, `/sto/ksq_bydd_trd`, `/idx/kospi_dd_trd`, `/idx/kosdaq_dd_trd`
  (`basDd=YYYYMMDD`, 헤더 `AUTH_KEY`). KRX 문서와 실제 응답으로 경로·필드명을 확인하세요. 필드가 다르면 `app/providers/krx.py`의
  필드명만 고치면 됩니다.
- 거래일인데 빈 응답 → '미발행', 캘린더상 휴장 → '휴장', 401 → '인증 실패', 403 → '접근 거부'로 구분 기록.
- 지수는 소수 둘째 자리까지 정수(×100)로 저장합니다.

## DART
- `corpCode.xml`(zip) → 상장사만 매핑. 관리 화면 'DART 고유번호 매핑 동기화'. ETF는 매핑하지 않으며 다른 기업 공시를 연결하지 않습니다.
- `list.json` 상태코드: 000 정상, 013 데이터 없음, 010/011 키 오류, 020 호출 제한.

## 네이버 원본 시세
- 이 주소가 항상 동작하거나 필드를 계속 준다고 가정하지 않습니다. 이용조건을 직접 확인한 뒤 `NAVER_QUOTE_ENABLED=true`.
- `probe-naver`로 `itemCode`, `closePrice`, `localTradedAt`, `marketStatus`, `marketSessionType`, `stockExchangeType`의 실제 값을 확인하고,
  관리 화면에서 허용값(장 상태·세션·거래소 코드)을 정한 뒤 **'확정'** 체크. 확정 전에는 어떤 관측도 체결 근거가 되지 않습니다.
- `overMarketPriceInfo`, `integratedPriceInfo`는 가격으로 읽지 않습니다. 시간대 없는 시각은 부적격입니다.

## 당일 종목별 투자자 수급 (미연결)
필요 요건: 종목코드·거래일·확인시각·KRX 정규장 범위·투자자 구분·단위·원출처·등급·원본 해시. 이를 만족한다고 확인된 공급자가 없어
`app/providers/flows.py`는 '연결 필요'를 반환하고, 모의 주문 필수 요건이 충족되지 않아 **신규 모의 주문은 차단**됩니다.
공급자를 찾으면 `FlowProvider`를 구현하고 `validate_flow_record`를 통과한 레코드만 `investor_flows`에 저장하세요.
전일 누계·범위 불명 추정치·화면 스크래핑 값으로 대체하지 마세요.

## 환율 (미연결)
서울 외환시장 종가·은행 고시환율·NDF는 다른 값입니다. 이를 구분해 주는 공급자(예: 한국은행 ECOS)를 연결하기 전까지 '연결 필요'.

## AI (Gemini)
- `GEMINI_MODEL`에 사용할 모델 이름을 직접 지정합니다(특정 모델을 가정하지 않음). 요금·무료 한도는 확인하지 않았으니 직접 확인하세요.
- AI에는 공개 자료만 보냅니다. 보유종목·실험 원장 원문·비밀값이 입력에 섞이면 호출 전에 거부합니다.
- AI 응답은 JSON 스키마로 받고, 서버가 점수·필수 요건·위험을 다시 검증합니다. AI가 DB를 직접 수정하지 않습니다.
