@echo off
REM ==========================================================
REM  GPT Researcher launcher
REM  Server: http://localhost:8000
REM ==========================================================
cd /d "%~dp0"

echo [1/2] Activating virtual environment...
call ".venv\Scripts\activate.bat"

echo [2/2] Starting server on http://localhost:8000
echo       Press Ctrl+C to stop the server.
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

pause
