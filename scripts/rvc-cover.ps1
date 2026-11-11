<#
.SYNOPSIS
  鲸live 翻唱一键脚本：原曲 → demucs 分离 → RVC 换声 → 混音 → mp3。
  用的是唱歌工坊同款流水线（extras/python/song-cover）。

.EXAMPLE
  .\scripts\rvc-cover.ps1 -Model "D:\models\my_voice.pth" -InputFile "D:\music\宁夏.mp3" -VoiceName 小深
  .\scripts\rvc-cover.ps1 -Model "D:\models\my_voice.pth" -Index "D:\models\my_voice.index" -InputFile "D:\music\宁夏.mp3" -F0 12
#>
param(
  [Parameter(Mandatory = $true)][string]$Model,
  [string]$Index = "",
  [string]$VoiceName = "我的声线",
  [Parameter(Mandatory = $true)][string]$InputFile,
  [int]$F0 = 0,
  [string]$OutDir = "",
  [string]$WorkDir = "",
  [string]$VoicesFile = "",
  [string]$Python = "",
  [string]$Ffmpeg = "",
  [ValidateSet("cpu:0", "cuda:0")][string]$Device = "cpu:0",
  [switch]$Keep
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$CoverDir = Join-Path $Repo "extras\python\song-cover"
$CoverPy = Join-Path $CoverDir "cover.py"
$SingDir = Join-Path $Repo "logs\sing"

if (-not (Test-Path $CoverPy)) { throw "找不到 cover.py: $CoverPy" }
if (-not (Test-Path $Model)) { throw "找不到 RVC 模型: $Model" }
if (-not (Test-Path $InputFile)) { throw "找不到输入音频: $InputFile" }

if (-not $Python) {
  $cands = @(
    "D:\文档\deepseek-harness\default-workspace\whaletalk\gsv\GPT-SoVITS-v2pro-20250604\runtime\python.exe",
    "D:\文档\deepseek-harness\default-workspace\whaletalk\gsv_gpu\venv\Scripts\python.exe"
  )
  $Python = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $Python) { throw "没自动找到 python，请用 -Python 指定（需要装好 demucs/rvc-python 的 python.exe）" }
}
if (-not $Ffmpeg) {
  $Ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
  if (-not $Ffmpeg) {
    $cands = @("C:\Program Files\nodejs\ffmpeg.exe")
    $Ffmpeg = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
  }
  if (-not $Ffmpeg) { throw "没找到 ffmpeg，请用 -Ffmpeg 指定" }
}

New-Item -ItemType Directory -Force -Path $SingDir | Out-Null
if (-not $OutDir) { $OutDir = Join-Path $SingDir "cover" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if (-not $VoicesFile) { $VoicesFile = Join-Path $OutDir "voices.json" }
$voicesFile = $VoicesFile
$voices = @{}
if (Test-Path $voicesFile) {
  try { $voices = Get-Content $voicesFile -Raw -Encoding UTF8 | ConvertFrom-Json -AsHashtable } catch { $voices = @{} }
}
if ($null -eq $voices) { $voices = @{} }
$voices[$VoiceName] = @{ pth = ($Model -replace '\\', '/'); index = ($Index -replace '\\', '/') }
$voices | ConvertTo-Json -Depth 5 | Set-Content $voicesFile -Encoding UTF8

if (-not $WorkDir) { $WorkDir = Join-Path $OutDir "work" }
$work = $WorkDir
New-Item -ItemType Directory -Force -Path $work | Out-Null
$cfgFile = Join-Path $OutDir "cover-config.json"
@{
  ffmpeg      = ($Ffmpeg -replace '\\', '/')
  voices_file = ($voicesFile -replace '\\', '/')
  demucs_model = "htdemucs"
  work_dir    = ($work -replace '\\', '/')
  out_dir     = ($OutDir -replace '\\', '/')
  device      = $Device
  hf_endpoint = "https://hf-mirror.com"
} | ConvertTo-Json | Set-Content $cfgFile -Encoding UTF8

Write-Host "python : $Python"
Write-Host "ffmpeg : $Ffmpeg"
Write-Host "声线   : $VoiceName -> $Model"
Write-Host "输入   : $InputFile"
Write-Host "设备   : $Device（CPU 一首约 6~8 分钟）"
Write-Host ""

$args = @(
  $CoverPy,
  "--input", $InputFile,
  "--voice", $VoiceName,
  "--voices-file", $voicesFile,
  "--config", $cfgFile,
  "--outdir", $OutDir,
  "--device", $Device,
  "--f0", "$F0",
  "--clip-seconds", "0",
  "--name", [System.IO.Path]::GetFileNameWithoutExtension($InputFile)
)
if ($Keep) { $args += "--keep-work" }

$sw = [System.Diagnostics.Stopwatch]::StartNew()
& $Python @args
$code = $LASTEXITCODE
$sw.Stop()
if ($code -ne 0) { throw "cover.py 失败（exit $code）" }
Write-Host ""
Write-Host ("完成，用时 " + [math]::Round($sw.Elapsed.TotalSeconds) + " 秒，成品在: " + $OutDir)
Get-ChildItem $OutDir -Filter "*_full.mp3" | Sort-Object LastWriteTime -Descending | Select-Object -First 3 |
  ForEach-Object { Write-Host ("  " + $_.FullName + "  " + [math]::Round($_.Length / 1MB, 1) + " MB") }
