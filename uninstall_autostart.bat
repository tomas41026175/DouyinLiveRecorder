@echo off
REM ==========================================================
REM   uninstall_autostart.bat
REM   Removes the 3 autostart shortcuts from Startup folder.
REM ==========================================================
setlocal
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup

echo.
echo Removing DLR autostart shortcuts from:
echo   %STARTUP%
echo.

set FOUND=0
for %%F in ("DLR-Recorder.lnk" "DLR-WebUI.lnk" "DLR-Widget.lnk") do (
    if exist "%STARTUP%\%%~F" (
        del /q "%STARTUP%\%%~F"
        echo   [removed] %%~F
        set FOUND=1
    )
)
if "%FOUND%"=="0" echo   (no shortcuts found - already uninstalled)

echo.
echo Done.
REM /Y：由 ToolLauncher 呼叫時跳過最後的 pause，讓視窗自己關掉，
REM 不留一個等按鍵的殘留視窗（見 install_autostart.bat 同樣的註解）。
if /i not "%~1"=="/Y" pause
