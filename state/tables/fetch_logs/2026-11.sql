-- 시그널 원장 공개 상태 파일. 손으로 고치지 마세요(app/state.py가 생성).
BEGIN TRANSACTION;
INSERT INTO "fetch_logs" VALUES(1,'dart','corp_codes','2026-11-26 15:55:58.349203','ok',NULL,'',NULL,4021);
INSERT INTO "fetch_logs" VALUES(2,'krx','stock_daily:KOSPI','2026-11-26 23:50:51.645314','ok',NULL,'','b11cbb35827bf8b2d55f6e5bf9e8481a0315a06c4393c7ed8d4e1e20fa8e753c',935);
INSERT INTO "fetch_logs" VALUES(3,'krx','index_daily:KOSPI','2026-11-26 23:50:51.906829','ok',NULL,'','ab6c0d76bc2c2ab265944e3371c4b54dbb54055515c5d37fe0368ef311424625',1);
INSERT INTO "fetch_logs" VALUES(4,'krx','stock_daily:KOSDAQ','2026-11-26 23:50:59.908041','ok',NULL,'','7bea10ac19176b316444c737388e3bd472964112f750273251f3b700fa23ec34',1837);
INSERT INTO "fetch_logs" VALUES(5,'krx','index_daily:KOSDAQ','2026-11-26 23:51:00.505862','ok',NULL,'','e6907c7467fcd25d16664616df5591212b45f049fb1e37028205c71cbb36b9fa',1);
INSERT INTO "fetch_logs" VALUES(6,'fred','latest:DGS10','2026-11-26 23:51:00.761379','ok',NULL,'',NULL,0);
INSERT INTO "fetch_logs" VALUES(7,'fred','latest:DGS2','2026-11-26 23:51:00.981875','ok',NULL,'',NULL,0);
INSERT INTO "fetch_logs" VALUES(8,'fred','latest:SP500','2026-11-26 23:51:01.184119','ok',NULL,'',NULL,0);
INSERT INTO "fetch_logs" VALUES(9,'fred','latest:NASDAQCOM','2026-11-26 23:51:01.407499','ok',NULL,'',NULL,0);
INSERT INTO "fetch_logs" VALUES(10,'fred','latest:DCOILWTICO','2026-11-26 23:51:01.587291','ok',NULL,'',NULL,0);
COMMIT;
