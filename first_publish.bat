@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ============================================================
echo  (처음 한 번만) 지금까지 이 컴퓨터에 입력한 기록으로 공개 상태 파일(state 폴더)을 만듭니다.
echo  로그인 계정, 보유종목, 원본 응답은 넣지 않습니다.
echo  이미 state 폴더가 있으면 아무것도 하지 않습니다.
echo ============================================================
pause
call .venv\Scripts\activate.bat
python -m app.ci init-from-local
pause
