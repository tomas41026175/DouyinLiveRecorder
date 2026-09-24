@echo off
REM ==========================================================
REM   start_console.bat - lightweight launcher (NO tray widget)
REM     1. (re)start DouyinLiveRecorder.exe in the parent folder
REM     2. (re)start web_ui in the background (logged)
REM     3. open the control panel in the default browser
REM
REM   Skips the tray widget entirely (it needs pywebview/WebView2 and
REM   was exiting silently on this machine). Everything you need is the
REM   web console. Safe to run repeatedly.
REM ==========================================================
setlocal enableextensions
cd /d "%~dp0"

if not exist "pyembed\python.exe" (
    echo [ERROR] pyembed\python.exe not found. Run install_all.bat first.
    pause & exit /b 1
)
if not exist "logs" mkdir "logs"

REM --- Step 1: stop any existing web_ui (leave the recorder alone) ---
echo Stopping any existing web_ui ...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe' or name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'web_ui\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" 2>nul
timeout /t 1 /nobreak >nul

REM --- Step 2: ensure web deps (flask + requests + discord.py; NO pywebview/pystray) ---
REM discord.py is only used by the optional Discord control-channel bot
REM (add/remove streamers via chat commands); it is installed unconditionally
REM here so it is ready if the user later enables it from the settings page,
REM but web_ui.py itself still works fine even if this install step fails
REM (src/discord_bot.py treats it as optional and no-ops without it).
echo Checking Python dependencies...
"pyembed\python.exe" -c "import flask, requests, discord"
if errorlevel 1 (
    echo === Installing missing deps (flask + requests + discord.py) ===
    "pyembed\python.exe" -m pip install --no-warn-script-location --disable-pip-version-check flask requests discord.py
    if errorlevel 1 ( echo [ERROR] deps install failed. & pause & exit /b 1 )
)

REM --- Step 3: start the recorder if not already running ---
for %%I in ("%~dp0..") do set "PARENT_DIR=%%~fI"
set "RECORDER_EXE=%PARENT_DIR%\DouyinLiveRecorder.exe"
set "REC_RUNNING="
for /f "tokens=*" %%P in ('powershell -NoProfile -Command "(Get-Process -Name DouyinLiveRecorder -ErrorAction SilentlyContinue | Measure-Object).Count"') do set "REC_RUNNING=%%P"
if "%REC_RUNNING%"=="0" (
    if exist "%RECORDER_EXE%" (
        echo Starting recorder: %RECORDER_EXE%
        start "DouyinLiveRecorder" /d "%PARENT_DIR%" "%RECORDER_EXE%"
    ) else (
        echo [WARN] Recorder exe not found at %RECORDER_EXE%
    )
) else (
    echo Recorder already running ^(%REC_RUNNING% instance^).
)

REM --- Step 4: start Web UI in background, logged ---
echo Starting Web UI... (log: logs\web_ui.log)
REM -u = unbuffered stdout/stderr. Without it, CPython block-buffers stdout
REM whenever it's redirected to a file (stderr is unbuffered already, which
REM is why Flask's request-log lines always showed up instantly but print()
REM diagnostics could sit invisible in the buffer for a long time / until
REM exit -- this cost real time misdiagnosing the discord bot's LoginFailure).
start "DLR-WebUI" /b cmd /c ""pyembed\python.exe" -u web_ui.py 1>"logs\web_ui.log" 2>&1"

REM --- Step 5: wait for it to answer, then open the browser ---
echo Waiting for Web UI to come up...
set RETRIES=0
:WAIT
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/status' -UseBasicParsing -TimeoutSec 1) | Out-Null; exit 0 } catch { exit 1 }" 2>nul
if not errorlevel 1 goto UP
set /a RETRIES+=1
if %RETRIES% GEQ 12 (
    echo [WARN] Web UI did not respond in 12s. Last log lines:
    powershell -NoProfile -Command "if ((Test-Path 'logs\web_ui.log') -and ((Get-Item 'logs\web_ui.log').Length -gt 0)) { Get-Content 'logs\web_ui.log' -Tail 25 } else { '(web_ui.log empty)' }"
    pause & exit /b 1
)
timeout /t 1 /nobreak >nul
goto WAIT

:UP
echo Web UI is up.

REM Read the actual bound port (port-fallback may have moved it off 8765).
set "WEBUI_PORT=8765"
if exist "%PARENT_DIR%\config\webui_port.txt" (
    for /f "usebackq delims=" %%P in ("%PARENT_DIR%\config\webui_port.txt") do set "WEBUI_PORT=%%P"
) else if exist "config\webui_port.txt" (
    for /f "usebackq delims=" %%P in ("config\webui_port.txt") do set "WEBUI_PORT=%%P"
)
echo Opening control panel: http://127.0.0.1:%WEBUI_PORT%/
start "" "http://127.0.0.1:%WEBUI_PORT%/"

echo.
echo ============================================================
echo   Done. Recorder + Web UI running; control panel opened.
echo   Web UI keeps running in the background after this window closes.
echo   URL: http://127.0.0.1:%WEBUI_PORT%/
echo ============================================================
timeout /t 3 /nobreak >nul
endlocal
