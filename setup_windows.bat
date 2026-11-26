@echo off
chcp 65001 > nul
REM 시그널 원장 최초 설치 (Windows). 이 파일이 있는 폴더에서 더블클릭하거나 명령창에서 실행하세요.
where python > nul 2>&1 || (echo [오류] Python이 없습니다. python.org 에서 설치할 때 "Add Python to PATH"를 체크하세요. & pause & exit /b 1)
if not exist .venv (python -m venv .venv || (echo [오류] 가상환경 생성 실패 & pause & exit /b 1))
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || (echo [오류] 설치 실패. 화면의 오류 문구를 복사해 문의하세요. & pause & exit /b 1)
if not exist .env copy .env.example .env > nul
python -m app.cli init-db || (pause & exit /b 1)
echo.
echo 소유자 계정을 만듭니다(이미 있으면 오류가 나도 괜찮습니다).
python -m app.cli create-owner
echo.
echo 설치 완료. run_web.bat 과 run_worker.bat 을 각각 실행하세요.
pause
