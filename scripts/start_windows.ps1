param(
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

try {
  $Root = Split-Path -Parent $PSScriptRoot
  Set-Location $Root

  function Test-VenvPython {
    try {
      if (!(Test-Path ".\.venv\Scripts\python.exe")) { return $false }
      $null = & ".\.venv\Scripts\python.exe" -c "import sys; print(sys.version)" 2>&1
      return $LASTEXITCODE -eq 0
    } catch {
      return $false
    }
  }

  if (!(Test-VenvPython)) {
    if (Test-Path ".venv") {
      Write-Host "检测到损坏或不兼容的 .venv，正在重新安装..."
      Remove-Item -Recurse -Force ".venv" -ErrorAction SilentlyContinue
    }
    Write-Host "缺少 .venv\Scripts\python.exe，正在先执行安装..."
    & powershell -ExecutionPolicy Bypass -File ".\scripts\install_windows.ps1"
    if ($LASTEXITCODE -ne 0) {
      throw "安装没有成功完成。请截图安装窗口里的错误信息。"
    }
    if (!(Test-VenvPython)) {
      throw "安装结束后 .venv\Scripts\python.exe 仍无法运行。请重新安装 Windows 版 Python 3.10 或更新版本。"
    }
  }

  & ".\.venv\Scripts\python.exe" -c "import uvicorn" 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "缺少 uvicorn Python 模块，正在重新安装依赖..."
    & powershell -ExecutionPolicy Bypass -File ".\scripts\install_windows.ps1"
    if ($LASTEXITCODE -ne 0) {
      throw "依赖安装没有成功完成。请截图安装窗口里的错误信息。"
    }
  }

  Write-Host "Starting Auctus Agent on http://$HostName`:$Port"
  & ".\.venv\Scripts\python.exe" -m uvicorn app.server:app --host $HostName --port $Port
} catch {
  Write-Host ""
  Write-Host "Auctus Agent 启动失败：" -ForegroundColor Red
  Write-Host $_ -ForegroundColor Red
  Write-Host ""
  Write-Host "请截图这个窗口发给支持。"
  Read-Host "按回车键关闭"
  exit 1
}
