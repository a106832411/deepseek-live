# 停止 鲸live 相关进程（TTS / 鲸live / OBS / 星露谷 / 抖音中继）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\stop-all.ps1
param(
  [switch]$KeepOBS,
  [switch]$KeepGame
)

$ErrorActionPreference = 'Continue'
Write-Host "🛑 停止 鲸live 相关进程…" -ForegroundColor Cyan

foreach ($n in @('electron')) {
  Get-Process $n -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.Path -like '*deepseek-live*' -or $_.Path -like '*node_modules\electron*') {
      Write-Host "  停止 $($_.ProcessName) pid=$($_.Id)"; Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
  }
}
if (-not $KeepOBS) {
  Get-Process obs64 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  停止 OBS pid=$($_.Id)"; Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
}
if (-not $KeepGame) {
  Get-Process 'StardewModdingAPI','Stardew Valley' -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  停止 $($_.ProcessName) pid=$($_.Id)"; Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
}
# TTS：api_v2.py / tts_proxy.py
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object {
  $_.CommandLine -match 'api_v2\.py|tts_proxy\.py'
} | ForEach-Object { Write-Host "  停止 TTS pid=$($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Seconds 1
Write-Host "完成。" -ForegroundColor Green
