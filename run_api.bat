@echo off
REM Activate environment and start the FastAPI server via uvicorn
if not exist ".venv\Scripts\activate.bat" (
    echo Virtual environment not found. Run run_setup.bat first.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
REM Check DB connectivity and run ingestion if reachable
%VENV_PY% -u check_db.py
if %ERRORLEVEL% == 0 (
    echo DB reachable — running ingestion
    %VENV_PY% ingest.py || echo Ingestion failed; starting API anyway
) else (
    echo DB not reachable — API will start without DB data
)

uvicorn api:app --host 127.0.0.1 --port 8000 --reload
pause
