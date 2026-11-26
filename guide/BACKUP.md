# 백업·복구

## SQLite (기본)
- 백업: 관리 화면 '지금 백업 + 복구 검증' 또는 `python -m app.cli backup`.
  SQLite 온라인 백업 API로 `data/backups/signal-ledger-<시각>-<라벨>.db`를 만들고 SHA-256 매니페스트(`.json`)를 함께 저장합니다.
- 검증: `python -m app.cli verify-backup <파일>` — 백업 **사본**을 열어 매니페스트 해시, `PRAGMA integrity_check`,
  모든 실험의 해시 체인, 주요 테이블 행 수를 확인합니다(원본 DB는 건드리지 않음).
- 복구: **웹서버와 worker를 모두 멈춘 뒤** `python -m app.cli restore <파일>`. 검증에 통과한 백업만 복구하며,
  복구 직전 현재 DB를 `before-restore` 백업으로 남깁니다.
- 가져오기(`apply`) 전에는 자동으로 `before-import` 백업을 만듭니다.
- 권장: 매일 장 마감 후 백업 + 주 1회 다른 디스크(외장·클라우드)에 복사.

## PostgreSQL (운영)
```bash
pg_dump -Fc -d signal_ledger -f signal-ledger-$(date +%Y%m%d).dump      # 백업
createdb signal_ledger_restore_test && pg_restore -d signal_ledger_restore_test <파일>   # 복구 시험
SL_DATABASE_URL=postgresql+psycopg://.../signal_ledger_restore_test python -m app.cli export check.json  # 읽기 확인
```
복구 시험 DB에서 관리 화면의 '모의운용 원장 점검'(해시 체인)이 모두 통과하는지 확인한 뒤 운영 DB를 교체하세요.
PostgreSQL 경로는 이 저장소의 자동 테스트 대상이 아닙니다(SQLite로만 시험함).
