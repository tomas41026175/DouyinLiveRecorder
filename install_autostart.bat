@echo off
REM ==========================================================
REM   install_autostart.bat
REM   Adds shortcuts to the per-user Startup folder so the
REM   recorder, Web UI, and tray widget all start on login.
REM
REM   No admin / no Task Scheduler / no registry edits needed.
REM   To disable: run uninstall_autostart.bat
REM ==========================================================
setlocal
cd /d "%~dp0"

set SRC_DIR=%~dp0
REM Strip trailing backslash for cleaner display
if "%SRC_DIR:~-1%"=="\" set SRC_DIR=%SRC_DIR:~0,-1%

REM Parent folder = DouyinLiveRecorder install dir
for %%I in ("%SRC_DIR%\..") do set PARENT_DIR=%%~fI

set EXE_PATH=%PARENT_DIR%\DouyinLiveRecorder.exe
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup

echo.
echo ============================================================
echo   DLR Autostart Installer
echo ============================================================
echo   Recorder        : %EXE_PATH%
echo   Web UI script   : %SRC_DIR%\start_webui_silent.bat  (launched hidden via run_hidden.vbs)
echo   Startup folder  : %STARTUP%
echo.
echo   NOTE: the tray-widget shortcut is no longer installed -- it's
echo   superseded by ToolLauncher (or the Web UI on its own). If you
echo   still want the old floating widget, run start_widget.bat by hand.
echo ============================================================
echo.

if not exist "%EXE_PATH%" (
    echo [WARNING] DouyinLiveRecorder.exe not found at:
    echo            %EXE_PATH%
    echo You can still continue; shortcut will be created and
    echo will start working after the recorder is deployed.
    echo.
)

REM /Y (or env AUTOSTART_YES=1) skips the interactive prompt -- used when this
REM script is launched programmatically (e.g. ToolLauncher's "啟用" button via
REM a freshly spawned console). A spawned console's stdin isn't always wired
REM up to real keyboard input the same way a console you opened yourself is;
REM when it isn't, `set /p` reads nothing/EOF and silently takes the "not Y"
REM branch below, so the window closes in a flash with nothing visible --
REM which looks exactly like a bug even though the script itself is fine.
if /i "%~1"=="/Y" set AUTOSTART_YES=1
if "%AUTOSTART_YES%"=="1" (
    echo Install autostart shortcuts? (Y/N): Y  [auto-confirmed]
) else (
    set /p YN=Install autostart shortcuts? (Y/N):
    if /i not "%YN%"=="Y" exit /b 0
)

REM --- Create shortcuts via PowerShell COM ---
echo Creating shortcuts in Startup folder...

powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%STARTUP%\DLR-Recorder.lnk'); $s.TargetPath='%EXE_PATH%'; $s.WorkingDirectory='%PARENT_DIR%'; $s.WindowStyle=7; $s.Description='DouyinLiveRecorder (auto-start)'; $s.Save()"
if errorlevel 1 ( echo [ERROR] Failed to create recorder shortcut & if not "%AUTOSTART_YES%"=="1" pause & exit /b 1 )

REM Web UI: launched through run_hidden.vbs instead of a raw WindowStyle=Minimized
REM shortcut. A plain .lnk shortcut to a .bat only supports Normal/Maximized/
REM Minimized -- there is no true "Hidden" -- so the cmd.exe window that
REM interprets start_webui_silent.bat briefly flashes on screen and then
REM closes (the .bat just fires pythonw.exe and exits) every time this
REM shortcut runs. run_hidden.vbs uses WScript.Shell.Run with windowStyle=0,
REM which is genuinely hidden from the start -- no flash at all.
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%STARTUP%\DLR-WebUI.lnk'); $s.TargetPath='%SystemRoot%\System32\wscript.exe'; $s.Arguments='""%SRC_DIR%\run_hidden.vbs"" ""%SRC_DIR%\start_webui_silent.bat""'; $s.WorkingDirectory='%SRC_DIR%'; $s.Description='DLR Web Console (auto-start, hidden)'; $s.Save()"
if errorlevel 1 ( echo [ERROR] Failed to create web UI shortcut & if not "%AUTOSTART_YES%"=="1" pause & exit /b 1 )

REM 舊版會多裝一個 DLR-Widget.lnk（系統匣小工具，前景視窗設計，跟
REM ToolLauncher/純 Web UI 重複也會多閃一次視窗）。如果是從舊版升級上來，
REM 清掉殘留的那個捷徑，避免使用者以為只有兩個卻還在背景多跑一份。
if exist "%STARTUP%\DLR-Widget.lnk" del /q "%STARTUP%\DLR-Widget.lnk"

echo.
echo ============================================================
echo   Installed. The next time you log in, the recorder and Web UI
echo   will auto-start (Web UI fully hidden, no flash).
echo.
echo   Shortcuts placed in:
echo     %STARTUP%
echo.
echo   To disable autostart, run uninstall_autostart.bat
echo ============================================================
echo.
if not "%AUTOSTART_YES%"=="1" pause
