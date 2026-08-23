@echo off
rem Build the portable Windows executable: dist\Bookkeeping.exe
rem
rem The previous dist\Bookkeeping.exe is copied to dist\Bookkeeping.previous.exe
rem first. Build output is deliberately not in version control, so Git cannot
rem bring back a working binary if a new build turns out to be broken -- that
rem copy is the only way back.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -m venv .venv || goto :failed
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :failed
)

".venv\Scripts\python.exe" -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo Installing PyInstaller...
    ".venv\Scripts\python.exe" -m pip install pyinstaller || goto :failed
)

if not exist "assets\icon.ico" (
    echo Generating the icon...
    ".venv\Scripts\python.exe" make_icon.py || goto :failed
)

if exist "dist\Bookkeeping.exe" (
    echo Keeping the previous build as dist\Bookkeeping.previous.exe
    copy /y "dist\Bookkeeping.exe" "dist\Bookkeeping.previous.exe" >nul
)

echo Building...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm Bookkeeping.spec || goto :failed

echo.
echo Done: dist\Bookkeeping.exe
echo Copy that single file anywhere. It keeps its books in a "data" folder
echo beside itself, or in %%LOCALAPPDATA%%\Bookkeeping if that folder is read-only.
goto :eof

:failed
echo.
echo Build failed. See the output above.
pause
