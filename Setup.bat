@echo off
setlocal

REM Double-click: install dependencies and start Auctus Agent on Windows.
set ROOT=%~dp0
cd /d "%ROOT%"

echo == Auctus Agent Setup ==
echo 1^) Install dependencies
powershell -ExecutionPolicy Bypass -File "%ROOT%scripts\install_windows.ps1"
if errorlevel 1 (
  pause
  exit /b 1
)

echo.
echo 2^) Start local Web UI
:: Start server in background first, wait for it to be ready, then open browser
start "Auctus Agent Server" powershell -ExecutionPolicy Bypass -File "%ROOT%scripts\start_windows.ps1"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8000"

endlocal

