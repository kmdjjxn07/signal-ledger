@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ============================================================
echo  GitHub에서 받은 최신 공개 원장을 내 컴퓨터 화면에 반영합니다.
echo  (먼저 GitHub Desktop에서 [Fetch origin] - [Pull origin] 을 하세요)
echo  run_web 창이 열려 있으면 먼저 닫으세요(Ctrl+C).
echo  로그인 계정과 내 보유종목은 그대로 유지되고, 기존 DB는 data\backups 에 백업됩니다.
echo ============================================================
pause
call .venv\Scripts\activate.bat
python -m app.ci local-refresh --prices
echo.
pause
