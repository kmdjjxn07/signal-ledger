@echo off
chcp 65001 > nul
call .venv\Scripts\activate.bat
echo 예약 작업 실행기를 시작합니다. 이 창이 열려 있는 동안 예약 작업이 동작합니다. 끄려면 Ctrl+C.
python -m app.worker
