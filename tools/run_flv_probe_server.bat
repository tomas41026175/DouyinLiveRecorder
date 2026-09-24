@echo off
REM ==========================================================
REM   run_flv_probe_server.bat - launcher for flv_probe_server.py
REM   (MONITOR_PLAN.md Stage 3 diagnostic tool)
REM
REM   Double-clicking the .py file directly does not work: Windows either
REM   has no python.exe file association, or opens it with the wrong
REM   (system) Python that does not have Flask installed, and the console
REM   window closes itself immediately on error so you never see why.
REM   This batch file uses the same pyembed\python.exe that web_ui.py
REM   runs on, and pauses at the end so any error stays on screen.
REM ==========================================================
setlocal enableextensions
cd /d "%~dp0.."

if not exist "pyembed\python.exe" (
    echo [ERROR] pyembed\python.exe not found. Run install_all.bat first, or make sure this file is inside the DouyinLiveRecorder folder.
    pause
    exit /b 1
)

echo Checking Python dependencies (flask)...
"pyembed\python.exe" -c "import flask" 2>nul
if errorlevel 1 (
    echo === Installing missing dep (flask) ===
    "pyembed\python.exe" -m pip install --no-warn-script-location --disable-pip-version-check flask
    if errorlevel 1 (
        echo [ERROR] flask install failed.
        pause
        exit /b 1
    )
)

echo.
echo Starting flv_probe_server.py ...
echo Open http://127.0.0.1:8901/ in a browser once it says "open http://...".
echo Press Ctrl+C to stop this server when you are done testing.
echo.
"pyembed\python.exe" "tools\flv_probe_server.py"

echo.
echo Server stopped (or failed to start -- see any error above).
pause
