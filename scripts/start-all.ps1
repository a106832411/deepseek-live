# 一键启动 / 停止 鲸live 全套（TTS + 鲸live + OBS + 可选游戏/抖音中继）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1 -Game      # 顺便启动星露谷
#   powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1 -Douyin    # 顺便启动抖音中继
#   powershell -ExecutionPolicy Bypass -File scripts\stop-all.ps1
param(
  [switch]$Game,
  [switch]$Douyin,
  [switch]$NoOBS,
  [switch]$Foreground
)

$ErrorActionPreference = 'Continue'
$Repo = Split-Path -Parent $PSScriptRoot
$Work = 'D:\文档\deepseek-harness\default-workspace\whaletalk'
$TtsScript = Join-Path $Work 'gsv_gpu\run_gpu_api.ps1'
$OBS = 'D:\obs-studio\bin\64bit\obs64.exe'
$GameDir = 'E:\steam\steamapps\common\Stardew Valley'

function PortOpen([int]$port) {
  try { return (Test-NetConnection 127.0.0.1 -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue) } catch { return $false }
}
function WaitPort([int]$port, [int]$seconds, [string]$name) {
  for ($i = 0; $i -lt $seconds; $i++) {
    if (PortOpen $port) { Write-Host "  ✅ $name ($port) 已就绪" -ForegroundColor Green; return $true }
    Start-Sleep -Seconds 1
  }
  Write-Host "  ⚠️ $name ($port) 等待超时" -ForegroundColor Yellow
  return $false
}

Write-Host "🐳 鲸live 一键启动" -ForegroundColor Cyan

# 1) TTS（GPT-SoVITS 9882 + 代理 9881）
if (-not (PortOpen 9881)) {
  if (Test-Path $TtsScript) {
    Write-Host "启动 TTS 引擎（GSV 9882 + 代理 9881）…"
    Start-Process -FilePath 'powershell' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$TtsScript) -WindowStyle Hidden
  } else { Write-Host "  ⚠️ 找不到 TTS 启动脚本: $TtsScript" -ForegroundColor Yellow }
} else { Write-Host "  ✅ TTS 代理 (9881) 已在运行" -ForegroundColor Green }
Write-Host "等待 TTS 加载模型（首次约 1-3 分钟）…"
WaitPort 9881 180 'TTS 代理' | Out-Null
WaitPort 9882 300 'GPT-SoVITS 引擎' | Out-Null

# 2) 鲸live（Electron）
$electron = Join-Path $Repo 'node_modules\electron\dist\electron.exe'
$alive = PortOpen 8788
if (-not $alive -and (Test-Path $electron)) {
  Write-Host "启动鲸live 窗口…"
  $env:ELECTRON_RUN_AS_NODE = $null
  Start-Process -FilePath $electron -ArgumentList @($Repo) -WorkingDirectory $Repo
} elseif ($alive) { Write-Host "  ✅ 鲸live 核心 (8788) 已在运行" -ForegroundColor Green }
WaitPort 8788 40 '鲸live 核心' | Out-Null

# 3) OBS
if (-not $NoOBS) {
  if (-not (Get-Process obs64 -ErrorAction SilentlyContinue)) {
    if (Test-Path $OBS) {
      Write-Host "启动 OBS…"
      Start-Process -FilePath $OBS -WorkingDirectory (Split-Path $OBS)
    } else { Write-Host "  ⚠️ 找不到 OBS: $OBS" -ForegroundColor Yellow }
  } else { Write-Host "  ✅ OBS 已在运行" -ForegroundColor Green }
  WaitPort 4455 60 'OBS WebSocket' | Out-Null
}

# 4) 星露谷（可选）
if ($Game) {
  if (-not (Get-Process 'StardewModdingAPI' -ErrorAction SilentlyContinue)) {
    if (Test-Path (Join-Path $GameDir 'StardewModdingAPI.exe')) {
      Write-Host "启动星露谷（SMAPI）…"
      Start-Process -FilePath (Join-Path $GameDir 'StardewModdingAPI.exe') -WorkingDirectory $GameDir
    } else { Write-Host "  ⚠️ 找不到星露谷: $GameDir" -ForegroundColor Yellow }
  } else { Write-Host "  ✅ 星露谷已在运行" -ForegroundColor Green }
}

# 5) 抖音中继（可选）
if ($Douyin) {
  $conn = Join-Path $Work 'connect_douyin.ps1'
  if (Test-Path $conn) {
    Write-Host "启动抖音中继…"
    Start-Process -FilePath 'powershell' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$conn) -WindowStyle Hidden
  } else { Write-Host "  ⚠️ 找不到 $conn" -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "服务状态：" -ForegroundColor Cyan
foreach ($p in @(@(8788,'鲸live 核心/控制台'), @(9881,'TTS 代理'), @(9882,'GPT-SoVITS 引擎'), @(4455,'OBS WebSocket'), @(11434,'Ollama 本地模型'), @(9800,'gamecam'))) {
  $ok = PortOpen $p[0]
  $mark = if ($ok) { '✅' } else { '—' }
  Write-Host ("  {0} {1,-22} {2}" -f $mark, $p[1], $p[0])
}
Write-Host ""
Write-Host "控制台: http://127.0.0.1:8788/console" -ForegroundColor Cyan
Write-Host "OBS 场景: 鲸live直播（星露谷上半屏 + 皮套/弹幕/歌单/本场数据）" -ForegroundColor Cyan
