-- 시그널 원장 공개 상태 파일. 손으로 고치지 마세요(app/state.py가 생성).
BEGIN TRANSACTION;
INSERT INTO "job_runs" VALUES(1,'pre_brief','2026-11-27','succeeded',1,'2026-11-26 23:50:42.377281','2026-11-26 23:51:01.595148','{"catch_up": {"result": "synced", "date": "2026-11-26", "results": {"stock_daily:KOSPI": {"status": "ok", "created": 935, "duplicate": 0, "corrected": 0}, "index_daily:KOSPI": {"status": "ok", "created": 1, "duplicate": 0, "corrected": 0}, "stock_daily:KOSDAQ": {"status": "ok", "created": 1837, "duplicate": 0, "corrected": 0}, "index_daily:KOSDAQ": {"status": "ok", "created": 1, "duplicate": 0, "corrected": 0}}}, "result": "published", "briefing_id": 1, "facts": 7, "elapsed_sec": 19.2, "timeout_exceeded": false}',NULL);
COMMIT;
