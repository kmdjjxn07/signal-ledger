-- 시그널 원장 공개 상태 파일. 손으로 고치지 마세요(app/state.py가 생성).

PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE alembic_version (
	version_num VARCHAR(32) NOT NULL, 
	CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);
CREATE TABLE audit_logs (
	id INTEGER NOT NULL, 
	at DATETIME NOT NULL, 
	actor VARCHAR(100) NOT NULL, 
	action VARCHAR(100) NOT NULL, 
	target VARCHAR(200), 
	detail JSON, 
	PRIMARY KEY (id)
);
CREATE TABLE briefings (
	id INTEGER NOT NULL, 
	trading_date DATE NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	version INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	content JSON NOT NULL, 
	created_by VARCHAR(100) NOT NULL, 
	created_at DATETIME NOT NULL, 
	published_at DATETIME, 
	PRIMARY KEY (id)
);
CREATE TABLE calendar_exceptions (
	id INTEGER NOT NULL, 
	day DATE NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	open_time VARCHAR(5), 
	close_time VARCHAR(5), 
	source_url TEXT NOT NULL, 
	note TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE calendar_years (
	year INTEGER NOT NULL, 
	confirmed BOOLEAN NOT NULL, 
	source_url TEXT, 
	confirmed_at DATETIME, 
	regular_open VARCHAR(5) NOT NULL, 
	regular_close VARCHAR(5) NOT NULL, 
	note TEXT, 
	PRIMARY KEY (year)
);
CREATE TABLE experiments (
	id INTEGER NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	initial_capital BIGINT NOT NULL, 
	duration_trading_days INTEGER NOT NULL, 
	max_positions INTEGER NOT NULL, 
	max_position_bp INTEGER NOT NULL, 
	cost_bps INTEGER NOT NULL, 
	benchmark VARCHAR(20) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	execution_owner VARCHAR(50) NOT NULL, 
	created_at DATETIME NOT NULL, 
	config_locked_at DATETIME, 
	start_date DATE, 
	end_date DATE, 
	is_imported_readonly BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE fetch_logs (
	id INTEGER NOT NULL, 
	provider VARCHAR(50) NOT NULL, 
	operation VARCHAR(100) NOT NULL, 
	requested_at DATETIME NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	http_status INTEGER, 
	detail TEXT, 
	raw_sha256 VARCHAR(64), 
	record_count INTEGER NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE holding_transactions (
	id INTEGER NOT NULL, 
	holding_id INTEGER NOT NULL, 
	trade_date DATE NOT NULL, 
	side VARCHAR(4) NOT NULL, 
	quantity BIGINT NOT NULL, 
	unit_price BIGINT NOT NULL, 
	note TEXT, 
	voids_id INTEGER, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(holding_id) REFERENCES holdings (id), 
	FOREIGN KEY(voids_id) REFERENCES holding_transactions (id)
);
CREATE TABLE holdings (
	id INTEGER NOT NULL, 
	code VARCHAR(12) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code)
);
CREATE TABLE hypotheses (
	id INTEGER NOT NULL, 
	uid VARCHAR(30) NOT NULL, 
	code VARCHAR(12) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	issued_at DATETIME NOT NULL, 
	catalyst TEXT NOT NULL, 
	not_priced_reason TEXT NOT NULL, 
	counter_scenario TEXT NOT NULL, 
	invalidation TEXT NOT NULL, 
	next_check TEXT NOT NULL, 
	base_price BIGINT NOT NULL, 
	base_price_id INTEGER NOT NULL, 
	base_date DATE NOT NULL, 
	direction VARCHAR(4) NOT NULL, 
	short_date DATE NOT NULL, 
	medium_date DATE NOT NULL, 
	quality_tier VARCHAR(12) NOT NULL, 
	quality_score INTEGER NOT NULL, 
	score_breakdown JSON NOT NULL, 
	confidence VARCHAR(10) NOT NULL, 
	research_version_id INTEGER NOT NULL, 
	research_code VARCHAR(12) NOT NULL, 
	research_checked_at DATETIME NOT NULL, 
	source_ids JSON NOT NULL, 
	evaluation_policy VARCHAR(30) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(base_price_id) REFERENCES official_prices (id), 
	FOREIGN KEY(research_version_id) REFERENCES research_versions (id), 
	UNIQUE (uid)
);
CREATE TABLE hypothesis_evaluations (
	id INTEGER NOT NULL, 
	hypothesis_id INTEGER NOT NULL, 
	horizon VARCHAR(10) NOT NULL, 
	judgement_date DATE NOT NULL, 
	price_id INTEGER, 
	close BIGINT, 
	change_pct VARCHAR(20), 
	outcome VARCHAR(10) NOT NULL, 
	reason TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	supersedes_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(hypothesis_id) REFERENCES hypotheses (id), 
	FOREIGN KEY(price_id) REFERENCES official_prices (id), 
	FOREIGN KEY(supersedes_id) REFERENCES hypothesis_evaluations (id)
);
CREATE TABLE import_runs (
	id INTEGER NOT NULL, 
	kind VARCHAR(40) NOT NULL, 
	filename VARCHAR(300) NOT NULL, 
	file_sha256 VARCHAR(64) NOT NULL, 
	dry_run BOOLEAN NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	report JSON NOT NULL, 
	backup_path TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE instruments (
	code VARCHAR(12) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	market VARCHAR(20), 
	is_etf BOOLEAN NOT NULL, 
	corp_code VARCHAR(8), 
	sector VARCHAR(100), 
	managed_issue BOOLEAN NOT NULL, 
	source VARCHAR(100) NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (code)
);
CREATE TABLE investor_flows (
	id INTEGER NOT NULL, 
	code VARCHAR(12) NOT NULL, 
	trading_date DATE NOT NULL, 
	checked_at DATETIME NOT NULL, 
	scope VARCHAR(40) NOT NULL, 
	investor_type VARCHAR(30) NOT NULL, 
	net_value_krw BIGINT NOT NULL, 
	unit VARCHAR(20) NOT NULL, 
	provider VARCHAR(50) NOT NULL, 
	grade VARCHAR(1) NOT NULL, 
	is_provisional BOOLEAN NOT NULL, 
	raw_sha256 VARCHAR(64) NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE job_locks (
	name VARCHAR(100) NOT NULL, 
	holder VARCHAR(100) NOT NULL, 
	acquired_at DATETIME NOT NULL, 
	expires_at DATETIME NOT NULL, 
	PRIMARY KEY (name)
);
CREATE TABLE job_runs (
	id INTEGER NOT NULL, 
	job_name VARCHAR(50) NOT NULL, 
	slot_key VARCHAR(100) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	attempts INTEGER NOT NULL, 
	started_at DATETIME, 
	finished_at DATETIME, 
	summary JSON, 
	error TEXT, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_job_slot UNIQUE (job_name, slot_key)
);
CREATE TABLE legacy_records (
	id INTEGER NOT NULL, 
	kind VARCHAR(40) NOT NULL, 
	original_id VARCHAR(200), 
	content_sha256 VARCHAR(64) NOT NULL, 
	payload JSON NOT NULL, 
	import_id INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(import_id) REFERENCES import_runs (id), 
	UNIQUE (content_sha256)
);
CREATE TABLE macro_outlooks (
	id INTEGER NOT NULL, 
	quarter_id VARCHAR(10) NOT NULL, 
	period_start DATE NOT NULL, 
	period_end DATE NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (quarter_id)
);
CREATE TABLE macro_revisions (
	id INTEGER NOT NULL, 
	outlook_id INTEGER NOT NULL, 
	revision_no INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	data_as_of DATE NOT NULL, 
	reason TEXT NOT NULL, 
	content JSON NOT NULL, 
	source_ids JSON NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(outlook_id) REFERENCES macro_outlooks (id)
);
CREATE TABLE observations (
	id INTEGER NOT NULL, 
	code VARCHAR(12) NOT NULL, 
	provider VARCHAR(50) NOT NULL, 
	requested_at DATETIME NOT NULL, 
	received_at DATETIME NOT NULL, 
	source_time DATETIME, 
	price BIGINT, 
	market_status VARCHAR(40), 
	session_type VARCHAR(40), 
	exchange_type VARCHAR(40), 
	eligible BOOLEAN NOT NULL, 
	ineligible_reason TEXT, 
	raw_sha256 VARCHAR(64), 
	PRIMARY KEY (id)
);
CREATE TABLE official_prices (
	id INTEGER NOT NULL, 
	code VARCHAR(12) NOT NULL, 
	trading_date DATE NOT NULL, 
	close BIGINT NOT NULL, 
	open BIGINT, 
	high BIGINT, 
	low BIGINT, 
	volume BIGINT, 
	trading_value BIGINT, 
	market_cap BIGINT, 
	scale INTEGER NOT NULL, 
	provider VARCHAR(50) NOT NULL, 
	source_url TEXT, 
	raw_sha256 VARCHAR(64) NOT NULL, 
	fetched_at DATETIME NOT NULL, 
	supersedes_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(supersedes_id) REFERENCES official_prices (id), 
	CONSTRAINT uq_official_price_content UNIQUE (code, trading_date, raw_sha256)
);
CREATE TABLE paper_events (
	id INTEGER NOT NULL, 
	experiment_id INTEGER NOT NULL, 
	seq INTEGER NOT NULL, 
	type VARCHAR(20) NOT NULL, 
	at DATETIME NOT NULL, 
	payload JSON NOT NULL, 
	idempotency_key VARCHAR(200), 
	prev_hash VARCHAR(64) NOT NULL, 
	hash VARCHAR(64) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(experiment_id) REFERENCES experiments (id), 
	CONSTRAINT uq_paper_seq UNIQUE (experiment_id, seq), 
	UNIQUE (idempotency_key)
);
CREATE TABLE raw_documents (
	sha256 VARCHAR(64) NOT NULL, 
	provider VARCHAR(50) NOT NULL, 
	url TEXT, 
	media_type VARCHAR(100) NOT NULL, 
	body TEXT NOT NULL, 
	fetched_at DATETIME NOT NULL, 
	PRIMARY KEY (sha256)
);
CREATE TABLE research_versions (
	id INTEGER NOT NULL, 
	code VARCHAR(12) NOT NULL, 
	checked_at DATETIME NOT NULL, 
	items JSON NOT NULL, 
	red_flags JSON NOT NULL, 
	liquidity_ok BOOLEAN, 
	status VARCHAR(20) NOT NULL, 
	core_passed BOOLEAN NOT NULL, 
	fully_verified_items INTEGER NOT NULL, 
	failed_reasons JSON NOT NULL, 
	next_checks JSON NOT NULL, 
	is_current_candidate BOOLEAN NOT NULL, 
	created_by VARCHAR(100) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE settings (
	"key" VARCHAR(100) NOT NULL, 
	value JSON NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY ("key")
);
CREATE TABLE "sources" (
	id INTEGER NOT NULL, 
	url TEXT NOT NULL, 
	title TEXT NOT NULL, 
	provider VARCHAR(100) NOT NULL, 
	grade VARCHAR(1) NOT NULL, 
	origin_key VARCHAR(200) NOT NULL, 
	is_company_claim BOOLEAN NOT NULL, 
	published_at DATETIME, 
	checked_at DATETIME NOT NULL, 
	raw_sha256 VARCHAR(64), 
	note TEXT, 
	created_at DATETIME NOT NULL, 
	ref_key VARCHAR(100), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_sources_ref_key UNIQUE (ref_key)
);
CREATE TABLE user_sessions (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	csrf_token VARCHAR(64) NOT NULL, 
	created_at DATETIME NOT NULL, 
	expires_at DATETIME NOT NULL, 
	revoked BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	UNIQUE (token_hash)
);
CREATE TABLE users (
	id INTEGER NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	password_hash VARCHAR(300) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);
CREATE INDEX ix_briefings_trading_date ON briefings (trading_date);
CREATE INDEX ix_calendar_exceptions_day ON calendar_exceptions (day);
CREATE INDEX ix_holding_transactions_holding_id ON holding_transactions (holding_id);
CREATE INDEX ix_hypotheses_code ON hypotheses (code);
CREATE INDEX ix_hypothesis_evaluations_hypothesis_id ON hypothesis_evaluations (hypothesis_id);
CREATE INDEX ix_investor_flows_code ON investor_flows (code);
CREATE INDEX ix_observations_code ON observations (code);
CREATE INDEX ix_official_prices_code ON official_prices (code);
CREATE INDEX ix_official_prices_trading_date ON official_prices (trading_date);
CREATE INDEX ix_paper_events_experiment_id ON paper_events (experiment_id);
CREATE INDEX ix_research_versions_code ON research_versions (code);
INSERT INTO "alembic_version" VALUES('0002');
INSERT INTO "briefings" VALUES(1,'2026-11-27','pre',1,'published','{"summary": "\ud655\uc778\ub41c \uc0ac\uc2e4\ub9cc \uc815\ub9ac\ud588\uc2b5\ub2c8\ub2e4. \ud574\uc11d\uc740 \uc544\uc9c1 \uc791\uc131\ub418\uc9c0 \uc54a\uc558\uc2b5\ub2c8\ub2e4.", "regime": null, "facts": [{"text": "\ucf54\uc2a4\ud53c \uc885\uac00(KRX \uc815\uaddc\uc7a5, 2026-11-26)", "value": "4,711.21", "source_id": 1, "grade": "A", "checked_at": "2026-11-26T23:50:51.906490", "official_price_id": 936, "detail": "\uac70\ub798\ub7c9 170,956,646 \u00b7 \uac70\ub798\ub300\uae08 12,972,411,604,861\uc6d0"}, {"text": "\ucf54\uc2a4\ub2e5 \uc885\uac00(KRX \uc815\uaddc\uc7a5, 2026-11-26)", "value": "655.71", "source_id": 2, "grade": "A", "checked_at": "2026-11-26T23:51:00.505510", "official_price_id": 2774, "detail": "\uac70\ub798\ub7c9 413,925,740 \u00b7 \uac70\ub798\ub300\uae08 5,092,250,424,267\uc6d0"}, {"text": "\ubbf8\uad6d 10\ub144 \uad6d\ucc44 \uc218\uc775\ub960(%, \uc77c\ubcc4, FRED DGS10) \u2014 \uad00\uce21\uc77c 2026-11-24", "value": "4.91", "source_id": 3, "grade": "B", "checked_at": "2026-11-26T23:51:00.761895"}, {"text": "\ubbf8\uad6d 2\ub144 \uad6d\ucc44 \uc218\uc775\ub960(%, \uc77c\ubcc4, FRED DGS2) \u2014 \uad00\uce21\uc77c 2026-11-24", "value": "4.63", "source_id": 4, "grade": "B", "checked_at": "2026-11-26T23:51:00.982359"}, {"text": "S&P 500 \uc9c0\uc218(FRED SP500, \uc885\uac00) \u2014 \uad00\uce21\uc77c 2026-11-24", "value": "7201.09", "source_id": 5, "grade": "B", "checked_at": "2026-11-26T23:51:01.184634"}, {"text": "\ub098\uc2a4\ub2e5 \uc885\ud569\uc9c0\uc218(FRED NASDAQCOM, \uc885\uac00) \u2014 \uad00\uce21\uc77c 2026-11-25", "value": "23585.8", "source_id": 6, "grade": "B", "checked_at": "2026-11-26T23:51:01.408027"}, {"text": "WTI \uc6d0\uc720 \ud604\ubb3c\uac00\uaca9(\ub2ec\ub7ec/\ubc30\ub7f4, FRED DCOILWTICO) \u2014 \uad00\uce21\uc77c 2026-11-24", "value": "82.24", "source_id": 7, "grade": "B", "checked_at": "2026-11-26T23:51:01.587820"}], "interpretations": [], "counter_scenarios": [], "next_checks": [], "data_status": [{"item": "\ucf54\uc2a4\ud53c (2026-11-26)", "status": "ok", "detail": "KRX \uacf5\uc2dd \uc77c\ubcc4(A)"}, {"item": "\ucf54\uc2a4\ub2e5 (2026-11-26)", "status": "ok", "detail": "KRX \uacf5\uc2dd \uc77c\ubcc4(A)"}, {"item": "\ubbf8\uad6d 10\ub144 \uad6d\ucc44 \uc218\uc775\ub960(%, \uc77c\ubcc4, FRED DGS10)", "status": "ok", "detail": "\uad00\uce21\uc77c 2026-11-24"}, {"item": "\ubbf8\uad6d 2\ub144 \uad6d\ucc44 \uc218\uc775\ub960(%, \uc77c\ubcc4, FRED DGS2)", "status": "ok", "detail": "\uad00\uce21\uc77c 2026-11-24"}, {"item": "S&P 500 \uc9c0\uc218(FRED SP500, \uc885\uac00)", "status": "ok", "detail": "\uad00\uce21\uc77c 2026-11-24"}, {"item": "\ub098\uc2a4\ub2e5 \uc885\ud569\uc9c0\uc218(FRED NASDAQCOM, \uc885\uac00)", "status": "ok", "detail": "\uad00\uce21\uc77c 2026-11-25"}, {"item": "WTI \uc6d0\uc720 \ud604\ubb3c\uac00\uaca9(\ub2ec\ub7ec/\ubc30\ub7f4, FRED DCOILWTICO)", "status": "ok", "detail": "\uad00\uce21\uc77c 2026-11-24"}, {"item": "\uc6d0\u00b7\ub2ec\ub7ec \ud658\uc728(\uc11c\uc6b8 \uc678\ud658\uc2dc\uc7a5 \uc885\uac00)", "status": "not_configured", "detail": "\uc11c\uc6b8 \uc678\ud658\uc2dc\uc7a5 \uc885\uac00 \uacf5\uae09\uc790(\uc608: \ud55c\uad6d\uc740\ud589 ECOS) \ubbf8\uc5f0\uacb0 \u2014 \ub2e4\ub978 \ud658\uc728\ub85c \ub300\uccb4\ud558\uc9c0 \uc54a\uc74c"}, {"item": "AI \ud574\uc11d", "status": "not_configured", "detail": "AI \ubbf8\uc5f0\uacb0 \u2014 \ud574\uc11d\uc740 \uad00\ub9ac\uc790\uac00 \uc9c1\uc811 \uc791\uc131"}], "provenance": "job", "checked_at": "2026-11-26T23:51:01.588730"}','job:pre_brief','2026-11-26 23:51:01.590655','2026-11-26 23:51:01.589878');
INSERT INTO "briefings" VALUES(2,'2026-11-27','close',1,'scheduled','{"summary": "\uc624\ub298 \uc7a5 \ub9c8\uac10 \ud6c4 \ubc1c\ud589 \uc608\uc815", "regime": null, "facts": [], "interpretations": [], "counter_scenarios": [], "next_checks": [], "data_status": [], "provenance": null}','job:pre_brief','2026-11-26 23:51:01.593414',NULL);
INSERT INTO "calendar_exceptions" VALUES(1,'2026-12-25','holiday',NULL,NULL,'https://open.krx.co.kr/contents/MKD/01/0110/01100305/MKD01100305.jsp','공식 휴장일','2026-11-26 15:07:26.350300');
INSERT INTO "calendar_exceptions" VALUES(2,'2026-12-31','holiday',NULL,NULL,'https://open.krx.co.kr/contents/MKD/01/0110/01100305/MKD01100305.jsp','공식 휴장일','2026-11-26 15:07:26.350303');
INSERT INTO "calendar_years" VALUES(2026,1,'https://open.krx.co.kr/contents/MKD/01/0110/01100305/MKD01100305.jsp','2026-11-26 15:07:26.347214','09:00','15:30',NULL);
INSERT INTO "settings" VALUES('schedule','{"pre_brief_time": "08:20", "close_brief_time": "15:50", "settlement_time": "16:10"}','2026-11-26 15:07:26.369223');
INSERT INTO "sources" VALUES(1,'https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd?basDd=20261126','KRX 공식 일별 코스피','KRX','A','krx:IDX:KOSPI:2026-11-26:78dea8c7c742',0,NULL,'2026-11-26 23:50:51.906490','78dea8c7c7421f45606ec058f75b7e045f0eaffec8172304b742648dc3c0448b',NULL,'2026-11-26 23:51:00.515206',NULL);
INSERT INTO "sources" VALUES(2,'https://data-dbg.krx.co.kr/svc/apis/idx/kosdaq_dd_trd?basDd=20261126','KRX 공식 일별 코스닥','KRX','A','krx:IDX:KOSDAQ:2026-11-26:0f141b0bfd0a',0,NULL,'2026-11-26 23:51:00.505510','0f141b0bfd0a0fc203d6daf26ba878c93da9310dea7278d76cd59b112db67727',NULL,'2026-11-26 23:51:00.516073',NULL);
INSERT INTO "sources" VALUES(3,'https://fred.stlouisfed.org/series/DGS10','미국 10년 국채 수익률(%, 일별, FRED DGS10)','FRED','B','fred:DGS10:2026-11-24',0,NULL,'2026-11-26 23:51:00.761588',NULL,NULL,'2026-11-26 23:51:00.761769',NULL);
INSERT INTO "sources" VALUES(4,'https://fred.stlouisfed.org/series/DGS2','미국 2년 국채 수익률(%, 일별, FRED DGS2)','FRED','B','fred:DGS2:2026-11-24',0,NULL,'2026-11-26 23:51:00.982060',NULL,NULL,'2026-11-26 23:51:00.982230',NULL);
INSERT INTO "sources" VALUES(5,'https://fred.stlouisfed.org/series/SP500','S&P 500 지수(FRED SP500, 종가)','FRED','B','fred:SP500:2026-11-24',0,NULL,'2026-11-26 23:51:01.184306',NULL,NULL,'2026-11-26 23:51:01.184477',NULL);
INSERT INTO "sources" VALUES(6,'https://fred.stlouisfed.org/series/NASDAQCOM','나스닥 종합지수(FRED NASDAQCOM, 종가)','FRED','B','fred:NASDAQCOM:2026-11-25',0,NULL,'2026-11-26 23:51:01.407723',NULL,NULL,'2026-11-26 23:51:01.407895',NULL);
INSERT INTO "sources" VALUES(7,'https://fred.stlouisfed.org/series/DCOILWTICO','WTI 원유 현물가격(달러/배럴, FRED DCOILWTICO)','FRED','B','fred:DCOILWTICO:2026-11-24',0,NULL,'2026-11-26 23:51:01.587475',NULL,NULL,'2026-11-26 23:51:01.587687',NULL);
COMMIT;
