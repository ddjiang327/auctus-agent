param(
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (!(Test-Path ".\.venv\Scripts\uvicorn.exe")) {
  Write-Error "缺少 .venv\Scripts\uvicorn.exe。请先完成安装：推荐双击项目根目录的 Setup.bat，或运行 .\scripts\install_windows.ps1。"
}

Write-Host "Starting Auctus Agent on http://$HostName`:$Port"
& ".\.venv\Scripts\uvicorn.exe" app.server:app --host $HostName --port $Port
