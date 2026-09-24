@echo off
REM ==========================================================
REM   deploy.bat - deploy the freshly built DouyinLiveRecorder.exe
REM
REM   Steps:
REM     1. Stop running recorder / web_ui / widget
REM     2. Back up config\ only (settings/cookies/limits; not the big videos)
REM     3. Copy new exe + _internal over the parent install dir
REM        (your config\ and downloads\ are left untouched)
REM     4. Restart everything via start_widget.bat
REM
REM   Double-click from the source_with_duration_tracker\ folder.
REM   (Kept ASCII-only on purpose: non-ASCII .bat text breaks under the
REM    Chinese Windows codepage.)
REM ==========================================================
setlocal enableextensions
cd /d "%~dp0"

set "DIST=dist\DouyinLiveRecorder"
for %%I in ("%~dp0..") do set "INSTALL=%%~fI"

echo ============================================================
echo   Target install: %INSTALL%
echo   New build     : %CD%\%DIST%
echo ============================================================

if not exist "%DIST%\DouyinLiveRecorder.exe" (
    echo [ERROR] %DIST%\DouyinLiveRecorder.exe not found.
    echo Run build_main_exe.bat first.
    pause & exit /b 1
)
if not exist "%INSTALL%\config" (
    echo [WARN] No config\ in the parent dir - this may be the wrong location:
    echo        %INSTALL%
    echo Press any key to continue anyway, or close this window to abort.
    pause
)

REM --- Step 1: stop running processes ---
echo.
echo [1/4] Stopping old recorder / web_ui / widget ...
taskkill /f /t /im DouyinLiveRecorder.exe >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe' or name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'web_ui\.py|widget(_window)?\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" 2>nul
timeout /t 2 /nobreak >nul

REM --- Step 2: back up config\ only (fast, safe) ---
echo.
echo [2/4] Backing up config\ ...
for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%T"
set "BAK=%INSTALL%\config_backup_%STAMP%"
if exist "%INSTALL%\config" (
    xcopy /e /i /y /q "%INSTALL%\config" "%BAK%" >nul
    if errorlevel 1 (
        echo [WARN] config backup failed. Press any key to continue or Ctrl+C to abort.
        pause
    ) else (
        echo       Backed up to: %BAK%
    )
)

REM --- Step 3: copy new exe + _internal (config/downloads untouched) ---
echo.
echo [3/4] Copying new exe + _internal ...
copy /y "%DIST%\DouyinLiveRecorder.exe" "%INSTALL%\DouyinLiveRecorder.exe" >nul
if errorlevel 1 (
    echo [ERROR] Copy exe failed (still running? close it first).
    pause & exit /b 1
)
if exist "%DIST%\_internal" (
    if exist "%INSTALL%\_internal" rmdir /s /q "%INSTALL%\_internal"
    xcopy /e /i /y /q "%DIST%\_internal" "%INSTALL%\_internal" >nul
    if errorlevel 1 (
        echo [ERROR] Copy _internal failed.
        pause & exit /b 1
    )
)
echo       Deploy done.

REM --- Step 4: restart everything ---
echo.
echo [4/4] Restarting recorder + web_ui + widget ...
if exist "start_widget.bat" (
    call "start_widget.bat"
) else (
    echo [WARN] start_widget.bat not found - start manually.
)

echo.
echo ============================================================
echo   Done. New engine deployed and started.
echo   Verify priority: open the recorder console; a priority
echo   streamer's loop wait should count down from 60s
echo   (normal streamers ~300s). To see the countdown, set
echo   config\config.ini option "show loop seconds" to Yes.
echo ============================================================
pause
endlocal
