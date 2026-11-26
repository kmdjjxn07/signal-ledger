@echo off
chcp 65001 > nul
call .venv\Scripts\activate.bat
echo 브라우저에서 http://localhost:8000 을 여세요. 끄려면 Ctrl+C.
uvicorn app.main:app --host 127.0.0.1 --port 8000
