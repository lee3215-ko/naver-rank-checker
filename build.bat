@echo off
setlocal
cd /d "%~dp0"

echo [1/3] Installing build dependencies...
python -m pip install -r requirements.txt pyinstaller --quiet
if errorlevel 1 goto fail

echo [2/3] Building NaverRankChecker ...
python -m PyInstaller build.spec --noconfirm --clean
if errorlevel 1 goto fail

echo.
echo [3/3] Done.
echo Output folder: dist\NaverRankChecker\
echo Run: dist\NaverRankChecker\NaverRankChecker.exe
echo.
echo 배포 시 dist\NaverRankChecker 폴더 전체를 복사하세요.
echo Data is saved in %%APPDATA%%\NaverRankChecker\entries.json
goto end

:fail
echo Build failed.
exit /b 1

:end
endlocal
