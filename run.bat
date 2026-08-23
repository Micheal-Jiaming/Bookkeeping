@echo off
rem Run Bookkeeping from source (development). For the portable program, build
rem dist\Bookkeeping.exe with build.bat and run that instead.
rem
rem This uses the same entry point as the .exe, so behaviour matches what users
rem get -- except for --keep-alive, which stops the app from closing itself when
rem no browser tab is open. That auto-close is right for a desktop program and
rem wrong for a development server you keep restarting.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -m venv .venv || goto :failed
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :failed
)

".venv\Scripts\python.exe" bookkeeping.py --keep-alive %*
goto :eof

:failed
echo.
echo Setup failed. Check that Python 3.11+ is installed and on PATH ("py --version").
pause
