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
INSERT INTO "calendar_exceptions" VALUES(1,'2026-12-25','holiday',NULL,NULL,'https://open.krx.co.kr/contents/MKD/01/0110/01100305/MKD01100305.jsp','공식 휴장일','2026-11-26 15:07:26.350300');
INSERT INTO "calendar_exceptions" VALUES(2,'2026-12-31','holiday',NULL,NULL,'https://open.krx.co.kr/contents/MKD/01/0110/01100305/MKD01100305.jsp','공식 휴장일','2026-11-26 15:07:26.350303');
INSERT INTO "calendar_years" VALUES(2026,1,'https://open.krx.co.kr/contents/MKD/01/0110/01100305/MKD01100305.jsp','2026-11-26 15:07:26.347214','09:00','15:30',NULL);
INSERT INTO "settings" VALUES('schedule','{"pre_brief_time": "08:20", "close_brief_time": "15:50", "settlement_time": "16:10"}','2026-11-26 15:07:26.369223');
COMMIT;
