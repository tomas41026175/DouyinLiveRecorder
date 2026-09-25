@echo off
REM ==========================================================
REM   install_all.bat
REM   One-click full installer:
REM     1. Portable Python (skipped if pyembed\ already there)
REM     2. Build DouyinLiveRecorder.exe with duration tracker
REM     3. Stop old recorder + back up old install
REM     4. Deploy new exe to parent folder
REM     5. Launch new recorder + Web UI
REM ==========================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set ROOT_DIR=%~dp0
set PARENT_DIR=%~dp0..
set BACKUP_DIR=%PARENT_DIR%\..\DouyinLiveRecorder_v4.0.7_backup
set PY_VER=3.11.9
set PY_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-embed-amd64.zip
set GETPIP_URL=https://bootstrap.pypa.io/get-pip.py
set PIP_FLAGS=--no-warn-script-location --disable-pip-version-check

echo.
echo ============================================================
echo   DouyinLiveRecorder All-in-One Installer
echo   Working dir : %ROOT_DIR%
echo   Deploy to   : %PARENT_DIR%
echo ============================================================
echo.
echo This will:
echo   * Download portable Python (~10MB) if not present
echo   * Install all build deps (~150MB) if not present
echo   * Build a new DouyinLiveRecorder.exe with duration tracker
echo   * Stop the old recorder, back up the install, deploy new exe
echo   * Launch the new recorder + Web UI
echo.
set /p YN=Continue? (Y/N): 
if /i not "%YN%"=="Y" exit /b 0


REM =========================================================
REM   STEP 1 -- Portable Python
REM =========================================================
if exist "pyembed\python.exe" (
    echo [1/5] pyembed\python.exe already exists, skipping.
) else (
    echo.
    echo === [1/5] Downloading Python %PY_VER% embeddable ===
    curl -L -# -o "python-embed.zip" "%PY_URL%"
    if errorlevel 1 ( echo [ERROR] download failed & pause & exit /b 1 )
    powershell -NoProfile -Command "Expand-Archive -LiteralPath 'python-embed.zip' -DestinationPath 'pyembed' -Force"
    if not exist "pyembed\python.exe" ( echo [ERROR] extract failed & pause & exit /b 1 )
    del /q "python-embed.zip"
    for %%f in ("pyembed\python*._pth") do (
        powershell -NoProfile -Command "$p='%%f'; $c=Get-Content -LiteralPath $p; if ($c -notmatch '^\s*import\s+site') { Add-Content -LiteralPath $p -Value 'import site' }"
    )
)


REM =========================================================
REM   STEP 2 -- pip + deps + PyInstaller
REM =========================================================
"pyembed\python.exe" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo === [2/5] Bootstrapping pip ===
    curl -L -# -o "pyembed\get-pip.py" "%GETPIP_URL%"
    if errorlevel 1 ( echo [ERROR] get-pip download failed & pause & exit /b 1 )
    "pyembed\python.exe" "pyembed\get-pip.py" --no-warn-script-location
    if errorlevel 1 ( echo [ERROR] pip install failed & pause & exit /b 1 )
    del /q "pyembed\get-pip.py"
) else (
    echo [2/5] pip already installed.
)

REM Check if PyInstaller already there to skip dep install
"pyembed\python.exe" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo === Installing setuptools + wheel + flask + widget deps ===
    "pyembed\python.exe" -m pip install %PIP_FLAGS% --upgrade setuptools wheel flask pystray Pillow requests pywebview
    if errorlevel 1 ( echo [ERROR] setuptools/flask install failed & pause & exit /b 1 )
    echo === Installing main.py runtime deps ===
    "pyembed\python.exe" -m pip install %PIP_FLAGS% ^
        "requests>=2.31.0" ^
        "loguru>=0.7.3" ^
        "pycryptodome>=3.20.0" ^
        "distro>=1.9.0" ^
        "tqdm>=4.67.1" ^
        "httpx[http2]>=0.28.1"
    if errorlevel 1 ( echo [ERROR] wheel deps install failed & pause & exit /b 1 )
    echo === Installing PyExecJS (sdist) ===
    "pyembed\python.exe" -m pip install %PIP_FLAGS% --no-build-isolation "PyExecJS>=1.5.1"
    if errorlevel 1 ( echo [ERROR] PyExecJS install failed & pause & exit /b 1 )
    echo === Installing PyInstaller ===
    "pyembed\python.exe" -m pip install %PIP_FLAGS% pyinstaller
    if errorlevel 1 ( echo [ERROR] PyInstaller install failed & pause & exit /b 1 )
) else (
    echo [2/5] Build dependencies already installed.
)


REM =========================================================
REM   STEP 3 -- Build new DouyinLiveRecorder.exe
REM =========================================================
echo.
echo === [3/5] Building dist\DouyinLiveRecorder\ ===
if exist "build" rmdir /s /q "build"
if exist "dist\DouyinLiveRecorder" rmdir /s /q "dist\DouyinLiveRecorder"
"pyembed\python.exe" -m PyInstaller --clean main.spec
if errorlevel 1 ( echo [ERROR] build failed & pause & exit /b 1 )

if not exist "dist\DouyinLiveRecorder\DouyinLiveRecorder.exe" (
    echo [ERROR] dist\DouyinLiveRecorder\DouyinLiveRecorder.exe not found after build
    pause & exit /b 1
)


REM =========================================================
REM   STEP 4 -- Stop old, back up, deploy
REM =========================================================
echo.
echo === [4/5] Stopping old recorder + deploying ===
taskkill /f /t /im DouyinLiveRecorder.exe >nul 2>&1
timeout /t 1 /nobreak >nul

if not exist "%BACKUP_DIR%" (
    echo Backing up current install to %BACKUP_DIR% ...
    mkdir "%BACKUP_DIR%" 2>nul
    xcopy /e /q /y /i "%PARENT_DIR%\DouyinLiveRecorder.exe" "%BACKUP_DIR%\" >nul
    xcopy /e /q /y /i "%PARENT_DIR%\_internal" "%BACKUP_DIR%\_internal\" >nul
    echo Backup done.
) else (
    echo Backup folder already exists, skipping.
)

echo Copying new build to %PARENT_DIR% ...
xcopy /e /y "dist\DouyinLiveRecorder\*" "%PARENT_DIR%\" >nul
if errorlevel 1 ( echo [ERROR] deploy copy failed & pause & exit /b 1 )


REM =========================================================
REM   STEP 5 -- Launch
REM =========================================================
echo.
echo === [5/5] Launching ===
echo.
echo ============================================================
echo   ALL DONE.
echo.
echo   New recorder    : %PARENT_DIR%\DouyinLiveRecorder.exe
echo   Web UI          : http://127.0.0.1:8765/
echo   Backup of old   : %BACKUP_DIR%
echo ============================================================
echo.

set /p LAUNCH=Launch recorder + Web UI now? (Y/N): 
if /i "%LAUNCH%"=="Y" (
    start "DouyinLiveRecorder" "%PARENT_DIR%\DouyinLiveRecorder.exe"
    timeout /t 2 /nobreak >nul
    start "DouyinLiveRecorder Web UI" "%~dp0start_webui.bat"
)

echo Done.
pause
