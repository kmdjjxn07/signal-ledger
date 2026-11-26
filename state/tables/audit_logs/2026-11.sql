-- 시그널 원장 공개 상태 파일. 손으로 고치지 마세요(app/state.py가 생성).
BEGIN TRANSACTION;
INSERT INTO "audit_logs" VALUES(1,'2026-11-26 15:07:26.349600','owner:inbox','calendar.confirm_year','2026','{"holidays": 2, "source": "https://open.krx.co.kr/contents/MKD/01/0110/01100305/MKD01100305.jsp"}');
INSERT INTO "audit_logs" VALUES(2,'2026-11-26 15:07:26.368821','owner:inbox','settings.schedule',NULL,'{"pre_brief_time": "08:20", "close_brief_time": "15:50", "settlement_time": "16:10"}');
INSERT INTO "audit_logs" VALUES(3,'2026-11-26 15:55:58.364922','job:dart_corp_codes','dart.corp_codes',NULL,'{"updated": 0}');
INSERT INTO "audit_logs" VALUES(4,'2026-11-26 23:51:01.593187','job:pre_brief','briefing.published','pre:2026-11-27:v1','null');
COMMIT;
