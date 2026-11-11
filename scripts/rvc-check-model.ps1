<#
.SYNOPSIS
  RVC 声线模型自检：确认 .pth 能被加载、结构正常，并可选跑一段 6 秒的换声冒烟测试。

.EXAMPLE
  .\scripts\rvc-check-model.ps1 -Model "D:\models\my_voice.pth"
  .\scripts\rvc-check-model.ps1 -Model "D:\models\my_voice.pth" -Index "D:\models\my_voice.index" -Sample "D:\music\test.mp3"
#>
param(
  [Parameter(Mandatory = $true)][string]$Model,
  [string]$Index = "",
  [string]$Python = "",
  [string]$Sample = "",
  [string]$VoiceName = "自检声线",
  [int]$F0 = 0,
  [string]$Device = "cpu:0"
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$RvcFull = Join-Path $Repo "extras\python\song-cover\rvc_full.py"
$Work = Join-Path $env:TEMP ("rvc-check-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $Work | Out-Null

if (-not (Test-Path $Model)) { throw "找不到模型: $Model" }
if (-not (Test-Path $RvcFull)) { throw "找不到 rvc_full.py: $RvcFull" }

if (-not $Python) {
  $cands = @(
    "D:\文档\deepseek-harness\default-workspace\whaletalk\gsv\GPT-SoVITS-v2pro-20250604\runtime\python.exe",
    "D:\文档\deepseek-harness\default-workspace\whaletalk\gsv_gpu\venv\Scripts\python.exe"
  )
  $Python = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $Python) { throw "没自动找到 python，请用 -Python 指定" }
}

# 1) 结构检查
$inspect = Join-Path $Work "inspect.py"
@'
import sys, os, json
import torch
mp = sys.argv[1]
size = os.path.getsize(mp)
ckpt = torch.load(mp, map_location="cpu", weights_only=False)
out = {"file_mb": round(size / 1e6, 1)}
if not isinstance(ckpt, dict):
    out["ok"], out["error"] = False, "torch.load 结果不是 dict：%r" % type(ckpt)
else:
    out["keys"] = sorted(ckpt.keys())[:10]
    out["version"] = ckpt.get("version")
    out["f0"] = ckpt.get("f0")
    cfg = ckpt.get("config")
    out["config_len"] = len(cfg) if isinstance(cfg, (list, tuple)) else None
    if isinstance(cfg, (list, tuple)) and len(cfg) >= 4:
        out["target_sr"] = cfg[-1]
        try:
            out["n_spk"] = int(cfg[-3])
        except Exception:
            pass
    w = ckpt.get("weight")
    if isinstance(w, dict):
        out["params"] = int(sum(v.numel() for v in w.values() if hasattr(v, "numel")))
        out["ok"] = out["version"] in ("v1", "v2") and out["f0"] in (0, 1) and "config" in ckpt
    else:
        out["ok"], out["error"] = False, "缺少 weight"
print(json.dumps(out, ensure_ascii=False))
'@ | Set-Content $inspect -Encoding UTF8

Write-Host "模型: $Model"
if ($Index) { Write-Host "index: $Index" }
$info = & $Python $inspect $Model | Select-Object -Last 1
if ($LASTEXITCODE -ne 0) { throw "模型加载失败（python 非 0 退出）" }
$j = $info | ConvertFrom-Json
Write-Host ("文件大小: " + $j.file_mb + " MB")
Write-Host ("version: " + $j.version + "   f0: " + $j.f0 + "   目标采样率: " + $j.target_sr + "   说话人: " + $j.n_spk)
if ($j.params) { Write-Host ("参数量: " + [math]::Round($j.params / 1e6, 1) + " M") }
if (-not $j.ok) { throw ("不是有效的 RVC 模型: " + $j.error) }
Write-Host "✅ 结构检查通过（version/f0/config/weight 齐全）"

# 2) 可选冒烟：跑 6 秒换声
if ($Sample) {
  if (-not (Test-Path $Sample)) { throw "找不到样例音频: $Sample" }
  $ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
  if (-not $ffmpeg) { $ffmpeg = "C:\Program Files\nodejs\ffmpeg.exe" }
  $wav = Join-Path $Work "sample6.wav"
  & $ffmpeg -y -loglevel error -t 6 -i $Sample $wav
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $wav)) { throw "ffmpeg 截取样例失败" }

  $voices = Join-Path $Work "voices.json"
  @{ $VoiceName = @{ pth = ($Model -replace '\\', '/'); index = ($Index -replace '\\', '/') } } |
    ConvertTo-Json -Depth 5 | Set-Content $voices -Encoding UTF8
  $outWav = Join-Path $Work "out6.wav"

  Write-Host ""
  Write-Host ("冒烟测试：6 秒音频 → RVC 换声（device=" + $Device + "）…")
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  & $Python $RvcFull $wav $outWav 0 0.35 0.5 --model $VoiceName --voices-file $voices --device $Device --f0-method pm
  $code = $LASTEXITCODE
  $sw.Stop()
  if ($code -ne 0 -or -not (Test-Path $outWav)) { throw "换声失败（exit $code）" }
  Write-Host ("✅ 换声完成，用时 " + [math]::Round($sw.Elapsed.TotalSeconds, 1) + " 秒，输出: " + $outWav)
  Write-Host ("   （6 秒音频耗时 × 40 ≈ 一首 4 分钟歌的大致耗时）")
}
