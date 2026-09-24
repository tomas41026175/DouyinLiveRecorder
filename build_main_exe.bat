@echo off
REM ==========================================================
REM   build_main_exe.bat
REM   Rebuilds DouyinLiveRecorder.exe with the duration tracker.
REM   Output: dist\DouyinLiveRecorder\
REM ==========================================================
setlocal
cd /d "%~dp0"

if not exist "pyembed\python.exe" (
    echo [ERROR] pyembed\python.exe not found.
    echo Please run install_webui.bat first.
    pause & exit /b 1
)

REM --- Bootstrap pip if missing ---
"pyembed\python.exe" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo === Bootstrapping pip into pyembed ===
    for %%f in ("pyembed\python*._pth") do (
        powershell -NoProfile -Command "$p='%%f'; $c=Get-Content -LiteralPath $p; if ($c -notmatch '^\s*import\s+site') { Add-Content -LiteralPath $p -Value 'import site' }"
    )
    curl -L -# -o "pyembed\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
    if errorlevel 1 ( echo [ERROR] download get-pip.py failed & pause & exit /b 1 )
    "pyembed\python.exe" "pyembed\get-pip.py" --no-warn-script-location
    if errorlevel 1 ( echo [ERROR] pip bootstrap failed & pause & exit /b 1 )
    del /q "pyembed\get-pip.py"
)

set PIP_FLAGS=--no-warn-script-location --disable-pip-version-check

echo.
echo === Step 1/5: Installing setuptools + wheel ===
"pyembed\python.exe" -m pip install %PIP_FLAGS% --upgrade setuptools wheel
if errorlevel 1 ( echo [ERROR] setuptools install failed & pause & exit /b 1 )

echo.
echo === Step 2/5: Installing wheel-only deps from requirements.txt ===
REM Install everything except PyExecJS (which is sdist-only and needs special handling)
"pyembed\python.exe" -m pip install %PIP_FLAGS% ^
    "requests>=2.31.0" ^
    "loguru>=0.7.3" ^
    "pycryptodome>=3.20.0" ^
    "distro>=1.9.0" ^
    "tqdm>=4.67.1" ^
    "httpx[http2]>=0.28.1"
if errorlevel 1 ( echo [ERROR] wheel deps install failed & pause & exit /b 1 )

echo.
echo === Step 3/5: Installing PyExecJS (sdist, needs no-build-isolation) ===
"pyembed\python.exe" -m pip install %PIP_FLAGS% --no-build-isolation "PyExecJS>=1.5.1"
if errorlevel 1 ( echo [ERROR] PyExecJS install failed & pause & exit /b 1 )

echo.
echo === Step 4/5: Installing PyInstaller ===
"pyembed\python.exe" -m pip install %PIP_FLAGS% pyinstaller
if errorlevel 1 ( echo [ERROR] PyInstaller install failed & pause & exit /b 1 )

echo.
echo === Step 5/5: Building dist\DouyinLiveRecorder\ ===
if exist "build" rmdir /s /q "build"
if exist "dist\DouyinLiveRecorder" rmdir /s /q "dist\DouyinLiveRecorder"
"pyembed\python.exe" -m PyInstaller --clean main.spec
if errorlevel 1 ( echo [ERROR] PyInstaller build failed & pause & exit /b 1 )

echo.
echo ============================================================
echo   Build complete. New executable:
echo     dist\DouyinLiveRecorder\DouyinLiveRecorder.exe
echo.
echo   Next step -- deploy:
echo     1. Stop the old DouyinLiveRecorder.exe if running.
echo     2. Back up old folder (recommended):
echo          xcopy /e /i ..\* ..\..\DouyinLiveRecorder_v4.0.7_backup\
echo     3. Copy new build over the old install:
echo          xcopy /e /y dist\DouyinLiveRecorder\* ..\
echo        (Your config\ and downloads\ folders are preserved.)
echo ============================================================
echo.
pause
