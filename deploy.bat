@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\publish.ps1" %*
if errorlevel 1 (
    echo.
    echo 배포 실패. 최초 1회는 scripts\setup-github.ps1 실행 후 gh auth login 이 필요합니다.
    exit /b 1
)
endlocal
