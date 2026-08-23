@echo off
rem Run Bookkeeping from source (development). For the program itself, build
rem dist\Bookkeeping.exe with build.bat and run that.
rem
rem This uses the same entry point as the .exe, so what you see here is what
rem users get. Any arguments are passed straight through:
rem     run.bat --data-dir C:\temp\books
rem     run.bat --allow-second-window

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -m venv .venv || goto :failed
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :failed
)

".venv\Scripts\python.exe" bookkeeping.py %*
goto :eof

:failed
echo.
echo Setup failed. Check that Python 3.11+ is installed and on PATH ("py --version").
pause
