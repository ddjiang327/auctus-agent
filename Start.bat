@echo off
setlocal

REM Double-click: start Auctus Agent on Windows after setup.
set ROOT=%~dp0
cd /d "%ROOT%"

:: Start server in background first, wait for it to be ready, then open browser
start "Auctus Agent Server" powershell -ExecutionPolicy Bypass -File "%ROOT%scripts\start_windows.ps1"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8000"

endlocal

