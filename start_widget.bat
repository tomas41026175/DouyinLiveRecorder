@echo off
REM ==========================================================
REM   start_widget.bat
REM   One-click "start everything":
REM     1. (re)start DouyinLiveRecorder.exe in parent folder
REM     2. (re)start web_ui in background (silent, no browser pop)
REM     3. (re)start tray widget + floating window
REM   Kills any previous instances first; safe to run repeatedly.
REM
REM   Background processes are logged to logs\web_ui.log and
REM   logs\widget.log so silent crashes are visible. The window
REM   stays open (pause) if something fails to come up.
REM ==========================================================
setlocal enableextensions
cd /d "%~dp0"

if not exist "pyembed\python.exe" (
    echo [ERROR] pyembed\python.exe not found.
    echo Please run install_all.bat first.
    pause
    exit /b 1
)

if not exist "logs" mkdir "logs"

REM --- Step 1: Kill old web_ui / widget processes (precise match) ---
echo Stopping any existing web_ui / widget processes...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe' or name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'web_ui\.py|widget(_window)?\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" 2>nul
timeout /t 1 /nobreak >nul

REM --- Step 2: Install widget/web deps if missing ---
echo Checking Python dependencies...
"pyembed\python.exe" -c "import pystray, PIL, requests, webview, flask"
if errorlevel 1 (
    echo === Installing missing deps (flask + pystray + Pillow + pywebview + requests) ===
    "pyembed\python.exe" -m pip install --no-warn-script-location --disable-pip-version-check ^
        flask pystray Pillow requests pywebview
    if errorlevel 1 (
        echo [ERROR] deps install failed.
        pause & exit /b 1
    )
    REM Verify the install actually fixed the imports.
    "pyembed\python.exe" -c "import pystray, PIL, requests, webview, flask"
    if errorlevel 1 (
        echo [ERROR] Dependencies still cannot be imported after install.
        echo Run this to see the real error:
        echo     pyembed\python.exe -c "import pystray, PIL, requests, webview, flask"
        pause & exit /b 1
    )
)

REM --- Step 3: Start DouyinLiveRecorder.exe in parent folder if not running ---
REM Resolve parent folder to an absolute path for reliability.
for %%I in ("%~dp0..") do set "PARENT_DIR=%%~fI"
set "RECORDER_EXE=%PARENT_DIR%\DouyinLiveRecorder.exe"

REM Check if a recorder instance is already alive (robust against tasklist filter quirks).
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

REM --- Step 4: Start Web UI in background (silent, no browser), logged ---
REM Use `cmd /c` so the redirection is performed inside the child shell and the
REM python process's stdout/stderr are actually captured (a plain `start /b ... >file`
REM creates the file but the child often does NOT inherit the handle -> empty log).
echo Starting Web UI... (log: logs\web_ui.log)
start "DLR-WebUI" /b cmd /c ""pyembed\python.exe" web_ui.py 1>"logs\web_ui.log" 2>&1"

REM Wait for Web UI to be reachable (max 10s)
echo Waiting for Web UI to come up...
set RETRIES=0
:WAIT_WEB_UI
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/status' -UseBasicParsing -TimeoutSec 1) | Out-Null; exit 0 } catch { exit 1 }" 2>nul
if not errorlevel 1 goto WEB_UI_OK
set /a RETRIES+=1
if %RETRIES% GEQ 10 (
    echo [WARN] Web UI did not respond in 10s.
    echo ---------- last lines of logs\web_ui.log ----------
    powershell -NoProfile -Command "if ((Test-Path 'logs\web_ui.log') -and ((Get-Item 'logs\web_ui.log').Length -gt 0)) { Get-Content 'logs\web_ui.log' -Tail 25 } else { '(web_ui.log empty) re-running web_ui.py in foreground to capture the error...' }"
    echo ---------------------------------------------------
    echo [DIAG] Running web_ui.py in the foreground. Press Ctrl+C to stop it,
    echo        then the widget step will continue.
    echo ============================================================
    "pyembed\python.exe" web_ui.py
    echo ============================================================
    echo The widget will start but may show offline.
    goto START_WIDGET
)
timeout /t 1 /nobreak >nul
goto WAIT_WEB_UI

:WEB_UI_OK
echo Web UI is up.

REM --- Open the control panel in the default browser ---
REM Read the actual bound port (port-fallback may have moved it off 8765).
REM webui_port.txt lives in the recorder install dir (parent) config folder;
REM fall back to a local config copy, then to 8765.
set "WEBUI_PORT=8765"
if exist "%PARENT_DIR%\config\webui_port.txt" (
    for /f "usebackq delims=" %%P in ("%PARENT_DIR%\config\webui_port.txt") do set "WEBUI_PORT=%%P"
) else if exist "config\webui_port.txt" (
    for /f "usebackq delims=" %%P in ("config\webui_port.txt") do set "WEBUI_PORT=%%P"
)
echo Opening control panel: http://127.0.0.1:%WEBUI_PORT%/
start "" "http://127.0.0.1:%WEBUI_PORT%/"

:START_WIDGET
REM --- Step 5: Start tray widget in the FOREGROUND ---
REM Running in the foreground (python.exe, not pythonw.exe) keeps the tray alive
REM as long as this window is open AND makes any startup crash visible right here
REM instead of disappearing silently. Close this window or pick "Exit widget" in
REM the tray menu to stop it.
echo.
echo Starting tray widget. Look for the icon in the system tray (bottom-right).
echo Keep this window open; closing it (or "Exit widget" in the tray) stops the widget.
echo ============================================================
"pyembed\python.exe" widget.py
echo ============================================================

REM If we reach here, widget.py has exited. If it crashed, the traceback is shown above.
echo.
echo [INFO] The tray widget has exited. If you see a Python error above, copy it.
echo If the widget closed immediately with an error, that error is shown above.
pause
endlocal
exit /b %errorlevel%
