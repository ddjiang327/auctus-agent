param(
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

New-Item -ItemType Directory -Force -Path inputs, outputs, logs, data | Out-Null

if (!(Test-Path ".venv")) {
  & $Python -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

if (!(Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example. Add your model API key before starting Auctus Agent."
}

& ".\.venv\Scripts\python.exe" agent.py doctor

Write-Host ""
Write-Host "Auctus Agent installed."
Write-Host "Start it with: .\scripts\start_windows.ps1"
