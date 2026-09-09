@echo off
rem Build the portable Windows executable.
rem
rem   build.bat            dist\Bookkeeping.exe       30 MB, Windows OCR
rem   build.bat --full     dist\BookkeepingFull.exe  125 MB, adds RapidOCR
rem
rem The full build reads receipts markedly better and costs 95 MB and about
rem 1.7 seconds of start-up for it, so which one you want depends on whether the
rem file is going on a USB stick. Both come out of Bookkeeping.spec.
rem
rem The previous build of whichever kind is copied to *.previous.exe first.
rem Build output is deliberately not in version control, so Git cannot bring
rem back a working binary if a new build turns out to be broken -- that copy is
rem the only way back.

setlocal
cd /d "%~dp0"

set "TARGET=Bookkeeping"
set "BOOKKEEPING_FULL_BUILD="
if /i "%~1"=="--full" (
    set "TARGET=BookkeepingFull"
    set "BOOKKEEPING_FULL_BUILD=1"
)

if defined BOOKKEEPING_FULL_BUILD (
    ".venv\Scripts\python.exe" -c "import rapidocr, onnxruntime" 2>nul
    if errorlevel 1 (
        echo Installing RapidOCR for the full build...
        ".venv\Scripts\python.exe" -m pip install rapidocr onnxruntime || goto :failed
    )
)

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

if exist "dist\%TARGET%.exe" (
    echo Keeping the previous build as dist\%TARGET%.previous.exe
    copy /y "dist\%TARGET%.exe" "dist\%TARGET%.previous.exe" >nul
)

echo Building...
rem Separate work folders. PyInstaller names its cache after the spec, and both
rem builds share one spec -- so without this the slim and full builds overwrite
rem each other's analysis and each one re-runs from scratch.
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --workpath "build\%TARGET%" Bookkeeping.spec || goto :failed

echo.
echo Done: dist\%TARGET%.exe
echo Copy that single file anywhere. It keeps its books in a "data" folder
echo beside itself, or in %%LOCALAPPDATA%%\Bookkeeping if that folder is read-only.
goto :eof

:failed
echo.
echo Build failed. See the output above.
pause
