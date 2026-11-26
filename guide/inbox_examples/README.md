# 제출 파일 예시

`inbox/` 폴더에 넣을 수 있는 JSON 형식입니다. 보통은 화면(편집기 모드)이 자동으로 만들어 주므로 손으로 쓸 일은 드뭅니다.

```json
{"commands": [ {"kind": "명령 이름", "payload": { ... }} ]}
```

| kind | 하는 일 |
|---|---|
| `calendar.year` | 연도 휴장일 목록을 공식 공지 URL과 함께 '확인됨'으로 등록 |
| `calendar.exception` | 임시휴장·특별 거래시간 |
| `schedule.set` | 예약 시각(HH:MM) |
| `naver.validation` | 네이버 원본 검증 기준 확정 |
| `instrument.update` | 업종·관리종목·ETF 표시 |
| `source.add` | 출처 등록(`ref`로 참조 이름) |
| `research.submit` | 기업 9항목 심층검증 판 |
| `hypothesis.issue` / `hypothesis.invalidate` / `hypothesis.next_check` | 가설 발행·무효화·다음 확인 일정 |
| `briefing.draft` / `briefing.publish` | 소유자 브리핑 초안·발행 |
| `macro.revision` | 분기 전망 새 판 |
| `experiment.register` / `experiment.discard` | 모의운용 실험 등록·폐기 |
| `import.briefs` | 과거 briefs.json 가져오기(`apply: false`면 미리보기만) |

출처 칸에는 번호(`12`) 또는 참조 이름(`ref:cpi-nov`)을 쉼표로 구분해 적습니다.
시각은 한국시간 `YYYY-MM-DDTHH:MM`, 날짜는 `YYYY-MM-DD`입니다.

예시 파일 `example-source-and-macro.json`은 **형식 설명용**이며 실제 자료가 아닙니다. 그대로 올리지 마세요.
캘린더 휴장일은 반드시 KRX 공식 공지를 직접 확인해 채우세요(예시를 두지 않습니다).
