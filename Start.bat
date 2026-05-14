@echo off
setlocal

REM Double-click: start Auctus Agent on Windows after setup.
set ROOT=%~dp0
cd /d "%ROOT%"

start "" "http://127.0.0.1:8000"
powershell -ExecutionPolicy Bypass -File "%ROOT%scripts\start_windows.ps1"

endlocal

