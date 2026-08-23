@echo off
rem Start the Bookkeeping app and open it in the default browser.
rem
rem First run creates .venv and installs the dependencies; later runs just start
rem the server, so this is the only command needed day to day.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -m venv .venv || goto :failed
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :failed
)

set HOST=127.0.0.1
set PORT=8765

echo Bookkeeping is starting on http://%HOST%:%PORT%
start "" "http://%HOST%:%PORT%"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host %HOST% --port %PORT%
goto :eof

:failed
echo.
echo Setup failed. Check that Python 3.11+ is installed and on PATH ("py --version").
pause
