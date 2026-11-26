# 배포와 예약 작업

## 1) 내 컴퓨터에서 계속 실행
`run_web.bat`과 `run_worker.bat`을 둘 다 켜 두면 됩니다. 컴퓨터가 꺼지거나 잠자기 상태면 예약 작업도 멈춥니다.
Windows 작업 스케줄러로 로그인 시 두 bat 파일을 자동 실행하도록 등록할 수 있습니다.
worker 대신 OS 스케줄러를 쓰려면 1분마다 `python -m app.worker --once`를 실행해도 됩니다(잠금·멱등성 덕분에 중복 실행 안전).

## 2) 서버(클라우드)에 배포 — Docker Compose 예시
```bash
cp .env.example .env            # 키 입력
# docker-compose.yml의 POSTGRES_PASSWORD와 DB 주소 비밀번호를 바꾸세요
docker compose up -d --build
docker compose run --rm web python -m app.cli create-owner
```
- 구성: `db`(PostgreSQL) · `migrate`(init-db) · `web`(포트 8000, 127.0.0.1에만 열림) · `worker`(예약 작업).
- 외부 공개는 HTTPS 리버스 프록시(예: Caddy, Nginx) 뒤에 두고 `SL_COOKIE_SECURE=true`.
- 호스팅 업체의 요금·무료 범위는 확인하지 않았습니다. 웹과 worker 두 프로세스를 상시 실행할 수 있는 환경이 필요합니다.
- 이 Compose 구성과 PostgreSQL 경로는 작성 환경에서 실제로 띄워 보지 못했습니다. 첫 배포 후 관리 화면 상태와 `/health`를 확인하세요.

## 개발·운영 분리
`SL_ENV=development|production`, DB 주소, 데이터 폴더를 환경별로 따로 둡니다. 테스트는 항상 임시 DB를 씁니다.
운영 DB에 합성 데이터를 넣지 마세요.
