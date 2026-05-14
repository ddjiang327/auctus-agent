param(
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PScriptRoot
Set-Location $Root
$ProjectDir = (Get-Location).Path

# Color helpers
function Write-Info { param([string]$Msg) Write-Host "  $Msg" }
function Write-Success { param([string]$Msg) Write-Host "✓ $Msg" -ForegroundColor Green }
function Write-Warn { param([string]$Msg) Write-Host "⚠ $Msg" -ForegroundColor Yellow }
function Write-Fail { param([string]$Msg) Write-Host "✗ $Msg" -ForegroundColor Red }

function Fail {
  param([string]$Step, [string]$Detail = "")
  Write-Host ""
  Write-Host "========================================================" -ForegroundColor Red
  Write-Host "  安装失败：$Step" -ForegroundColor Red
  Write-Host "========================================================" -ForegroundColor Red
  Write-Host ""

  switch -Wildcard ($Step) {
    "*找不到*Python*" {
      Write-Host "  【问题】找不到 Python 或 Python 未正确安装"
      Write-Host ""
      Write-Host "  【解决步骤】"
      Write-Host "  1. 下载 Python 3.10 或更新版本："
      Write-Host "     https://www.python.org/downloads/"
      Write-Host "  2. 运行安装程序，务必勾选："
      Write-Host "     ☑ Add Python to PATH"
      Write-Host "     ☑ Install pip"
      Write-Host "  3. 安装完成后，重新双击 Setup.bat"
      Write-Host ""
      Write-Host "  【验证安装】安装完成后，在命令行运行：python --version"
    }
    "*Python 版本*" {
      Write-Host "  【问题】Python 版本过低"
      Write-Host ""
      Write-Host "  当前需要 Python 3.10 或更高版本。"
      Write-Host "  请从以下地址下载最新版："
      Write-Host "     https://www.python.org/downloads/"
    }
    "*权限*" {
      Write-Host "  【问题】没有足够权限完成安装"
      Write-Host ""
      Write-Host "  【解决方法】"
      Write-Host "  1. 右键点击 Setup.bat"
      Write-Host "  2. 选择「以管理员身份运行」"
      Write-Host ""
      Write-Host "  如果问题仍然存在，可能是杀毒软件阻止了安装。"
    }
    "*venv*" {
      Write-Host "  【问题】无法创建虚拟环境"
      Write-Host ""
      Write-Host "  可能原因："
      Write-Host "  1. 之前安装中断，残留了损坏的 .venv 文件夹"
      Write-Host "  2. 权限不足"
      Write-Host ""
      Write-Host "  【解决方法】"
      Write-Host "  1. 关闭所有命令行窗口"
      Write-Host "  2. 删除 .venv 文件夹（如果存在）"
      Write-Host "  3. 重新双击 Setup.bat"
    }
    "*依赖*安装失败*" {
      Write-Host "  【问题】依赖安装失败"
      Write-Host ""
      Write-Host "  可能原因："
      Write-Host "  1. 网络连接不稳定（推荐使用稳定的网络）"
      Write-Host "  2. pip 源被屏蔽（国内用户可能需要换源）"
      Write-Host "  3. 磁盘空间不足"
      Write-Host ""
      Write-Host "  【解决方法】"
      Write-Host "  1. 检查网络后重试"
      Write-Host "  2. 如果是国内用户，可以手动设置 pip 镜像源："
      Write-Host "     pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple"
      Write-Host "  3. 确认磁盘有至少 1GB 可用空间"
    }
    "*超时*" {
      Write-Host "  【问题】安装超时"
      Write-Host ""
      Write-Host "  依赖下载时间较长，可能是因为网络较慢。"
      Write-Host "  请确保网络稳定后重试。"
    }
    "*路径*" {
      Write-Host "  【问题】项目路径有问题"
      Write-Host ""
      Write-Host "  当前路径：$ProjectDir"
      Write-Host "  请确保："
      Write-Host "  1. 不要把项目放在桌面深层嵌套的文件夹中"
      Write-Host "  2. 路径中不要包含特殊字符或中文（如果可能）"
    }
    default {
      Write-Host "  【问题】安装时遇到未知错误"
      if ($Detail) {
        Write-Host ""
        Write-Host "  错误详情："
        Write-Host "  $Detail"
      }
      Write-Host ""
      Write-Host "  【一般解决步骤】"
      Write-Host "  1. 关闭所有命令行窗口"
      Write-Host "  2. 删除 .venv 文件夹（如果存在）"
      Write-Host "  3. 重新双击 Setup.bat"
      Write-Host ""
      Write-Host "  如果问题持续，请截图此错误信息并联系支持。"
    }
  }

  Write-Host ""
  Write-Host "========================================================" -ForegroundColor Red
  Write-Host ""
  Write-Host "按回车键关闭..."
  Read-Host
  exit 1
}

function Check-DiskSpace {
  $drive = Split-Path -Qualifier $ProjectDir
  $freeSpace = (Get-PSDrive -Name $drive.TrimEnd(':')).Free
  $minSpace = 500MB  # Minimum 500MB free space
  if ($freeSpace -lt $minSpace) {
    Write-Warn "磁盘空间不足：剩余 $([math]::Round($freeSpace/1MB))MB"
    Write-Info "建议至少保留 500MB 可用空间"
  }
}

function Test-PythonCommand {
  param([string]$Cmd)
  try {
    $null = & $Cmd -c "import sys" 2>&1
    return $true
  } catch {
    return $false
  }
}

# ── Step 0: Pre-flight checks ─────────────────────────────────────────────────
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Auctus Agent 安装程序" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Info "检查系统环境..."

Check-DiskSpace

# ── Step 1: Python check ──────────────────────────────────────────────────────
Write-Host ""
Write-Info "检查 Python..."

# Try multiple common Python commands
$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py", "C:\Python*\python.exe")) {
  if (Test-Path $cmd) {
    $pythonCmd = $cmd
    break
  }
  if ($cmd -eq "py") {
    try {
      $version = & $cmd --version 2>&1
      if ($LASTEXITCODE -eq 0) {
        $pythonCmd = $cmd
        break
      }
    } catch { }
  }
}

if ($null -eq $pythonCmd) {
  Fail "找不到 Python" "系统未找到 python/python3 命令"
}

# Check Python version
try {
  $versionInfo = & $pythonCmd -c "import sys; print(sys.version_info.major, sys.version_info.minor)"
  $major, $minor = $versionInfo -split ' '
  if ([int]$major -lt 3 -or ([int]$major -eq 3 -and [int]$minor -lt 10)) {
    Fail "Python 版本过低" "当前版本：$major.$minor，需要 3.10 或更高"
  }
  Write-Success "Python $major.$minor 检测通过"
} catch {
  Fail "Python 版本检查" $_
}

# ── Step 2: Directories ───────────────────────────────────────────────────────
Write-Host ""
Write-Info "创建工作目录..."
New-Item -ItemType Directory -Force -Path inputs, outputs, logs, data | Out-Null
Write-Success "目录创建完成"

# ── Step 3: Virtualenv ────────────────────────────────────────────────────────
Write-Host ""
Write-Info "检查虚拟环境..."

if (Test-Path ".venv") {
  if (Test-Path ".venv\Scripts\python.exe") {
    Write-Success "虚拟环境已存在，跳过创建"
  } else {
    Write-Warn "检测到损坏的虚拟环境，正在删除并重新创建..."
    Remove-Item -Recurse -Force ".venv" -ErrorAction SilentlyContinue
  }
}

if (!(Test-Path ".venv")) {
  Write-Info "创建虚拟环境..."
  try {
    & $pythonCmd -m venv .venv
    if (!(Test-Path ".venv\Scripts\python.exe")) {
      throw "虚拟环境创建后未找到 python.exe"
    }
    Write-Success "虚拟环境创建完成"
  } catch {
    Fail "创建 venv" $_
  }
}

# Upgrade pip first
Write-Host ""
Write-Info "升级 pip..."
try {
  & ".\.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --no-warn-script-location
  Write-Success "pip 升级完成"
} catch {
  Write-Warn "pip 升级失败，继续安装依赖..."
}

# ── Step 4: Dependencies ──────────────────────────────────────────────────────
Write-Host ""
Write-Info "安装依赖（首次约需 2-5 分钟，请稍候）..."

$pipCmd = ".\.venv\Scripts\pip.exe"
if (!(Test-Path $pipCmd)) {
  $pipCmd = ".\.venv\Scripts\pip3.exe"
}

try {
  # Install with progress indication
  & ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet --no-warn-script-location
  Write-Success "依赖安装完成"
} catch {
  Fail "依赖安装失败" $_
}

# ── Step 5: .env ──────────────────────────────────────────────────────────────
Write-Host ""
Write-Info "配置文件..."

if (!(Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Success "已创建 .env 配置文件"
  Write-Host ""
  Write-Host "  💡 提示：你可以先启动 Web UI，在「首次设置向导」里完成模型与 API key 配置。"
  Write-Host "          无需手动编辑 .env 文件。"
} else {
  Write-Success "配置文件已存在"
}

# ── Step 6: Doctor check (non-fatal) ─────────────────────────────────────────
Write-Host ""
Write-Info "运行环境检查..."
try {
  $doctorOutput = & ".\.venv\Scripts\python.exe" agent.py doctor 2>&1
  $doctorOutput = $doctorOutput -join "`n"
  if ($doctorOutput -match "missing API key|未配置|not found") {
    Write-Warn "API key 未配置（这是正常的，首次使用需要配置）"
    Write-Host "  你可以在 Web UI 的「首次设置向导」里配置。"
  } else {
    Write-Success "环境检查通过"
  }
} catch {
  Write-Warn "环境检查未完成，但不影响安装。"
}

# ── Step 7: Desktop launcher ──────────────────────────────────────────────────
Write-Host ""
Write-Info "创建桌面启动入口..."
$Desktop = [System.Environment]::GetFolderPath("Desktop")
$LauncherPath = Join-Path $Desktop "启动 Auctus Agent.bat"

if (Test-Path $Desktop) {
  if (!(Test-Path $LauncherPath)) {
    $LauncherContent = "@echo off`r`ntitle Auctus Agent`r`ncd /d `"$ProjectDir`"`r`nstart `"`" `"http://127.0.0.1:8000`"`r`npowershell -ExecutionPolicy Bypass -File scripts\start_windows.ps1`r`n"
    [System.IO.File]::WriteAllText($LauncherPath, $LauncherContent, [System.Text.Encoding]::ASCII)
    Write-Success "已在桌面创建：「启动 Auctus Agent.bat」"
  } else {
    Write-Info "桌面启动入口已存在，跳过创建"
  }
} else {
  Write-Warn "无法访问桌面，跳过创建桌面入口"
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
Write-Success "Auctus Agent 安装完成！"
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "  📌 接下来："
Write-Host "  1. 双击桌面上的「启动 Auctus Agent.bat」"
Write-Host "  2. 浏览器会自动打开 http://127.0.0.1:8000"
Write-Host "  3. 按照首次设置向导完成配置"
Write-Host ""
Write-Host "  💡 提示："
Write-Host "  - 首次配置需要选择模型并填写 API key"
Write-Host "  - Telegram 配置是可选的，用于手机远程控制"
Write-Host ""
