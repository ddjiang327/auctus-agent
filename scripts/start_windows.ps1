param(
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (!(Test-Path ".\.venv\Scripts\uvicorn.exe")) {
  Write-Error "Missing .venv\Scripts\uvicorn.exe. Run .\scripts\install_windows.ps1 first."
}

Write-Host "Starting Auctus Agent on http://$HostName`:$Port"
& ".\.venv\Scripts\uvicorn.exe" app.server:app --host $HostName --port $Port
