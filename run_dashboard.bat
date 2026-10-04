@echo off
REM Start the local HKJC results dashboard
if not exist ".venv\Scripts\activate.bat" (
    echo Virtual environment not found. Run run_setup.bat first.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
.venv\Scripts\python.exe dashboard.py
pause