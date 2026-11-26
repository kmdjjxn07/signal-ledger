@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo 공개 사이트를 내 컴퓨터에서 미리 만들어 봅니다(_site 폴더). GitHub에 올라가지 않습니다.
call .venv\Scripts\activate.bat
python -m app.ci build-site
start "" "%~dp0_site\index.html"
pause
