@echo off
REM ==========================================================
REM   start_webui_silent.bat
REM   Like start_webui.bat but does NOT open browser.
REM   Used by autostart so the browser doesn't pop on every login.
REM ==========================================================
setlocal
cd /d "%~dp0"

if not exist "pyembed\python.exe" (
    echo [ERROR] pyembed\python.exe not found. Run install_webui.bat first.
    pause & exit /b 1
)

REM Ensure web deps are present (same set as start_console.bat). This path
REM is also used by autostart, where nobody is watching, so install quietly
REM and keep going even if it fails -- web_ui.py itself still starts fine
REM without discord.py (src/discord_bot.py treats it as optional).
"pyembed\python.exe" -c "import flask, requests, discord" >nul 2>&1
if errorlevel 1 (
    "pyembed\python.exe" -m pip install --no-warn-script-location --disable-pip-version-check flask requests discord.py >nul 2>&1
)

REM -u = unbuffered stdout/stderr, matches start_console.bat (see comment
REM there): without it, print() output can sit invisible in a buffer for a
REM long time whenever stdout is redirected to a file/pipe.
if exist "pyembed\pythonw.exe" (
    "pyembed\pythonw.exe" -u web_ui.py %*
) else (
    "pyembed\python.exe" -u web_ui.py %*
)
