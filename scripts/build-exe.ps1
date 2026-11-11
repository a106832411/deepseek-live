<#
.SYNOPSIS
  鲸live 打包脚本：前置检查 -> vite build -> electron-builder 出 Windows 产物

.DESCRIPTION
  默认产出便携版（portable）exe，免安装、双击即用。
  -Target nsis 产出 NSIS 安装版（首次需要联网下载 nsis 工具链）。
  失败时给出可读报错，不会留下后台进程。

.EXAMPLE
  npm run build:exe

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -Target nsis

.EXAMPLE
  # 复用已有 dist/，只重新打包
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -SkipBuild

.EXAMPLE
  # 临时兜底：不用 asar，产物目录结构更接近源码，asar 路径问题消失（见 docs/打包.md）
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -NoAsar
#>
[CmdletBinding()]
param(
  [ValidateSet('portable', 'nsis')]
  [string]$Target = 'portable',
  [switch]$SkipBuild,
  [switch]$NoAsar,
  [switch]$NoExeEdit
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg) { Write-Host $msg -ForegroundColor Green }
function Fail([string]$msg) {
  Write-Host "`n[打包失败] $msg" -ForegroundColor Red
  exit 1
}

$modeLabel = ''
if ($NoAsar) { $modeLabel = $modeLabel + '（asar=false 兜底模式）' }
if ($NoExeEdit) { $modeLabel = $modeLabel + '（不写 exe 图标/版本信息）' }

Write-Host "鲸live 打包" -ForegroundColor Green
Write-Host "  仓库: $root"
Write-Host "  目标: $Target$modeLabel"

# ---------------- 1) 前置检查 ----------------
Write-Step '检查 Node / 依赖 / 入口文件'

$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCmd) { Fail '找不到 node，请先安装 Node.js 22.x 并加入 PATH' }
Write-Host ("  node {0}" -f (& node -v))

$requireFiles = @(
  @{ Path = 'node_modules';             Hint = 'node_modules 不存在，先运行: npm install' },
  @{ Path = 'node_modules\electron\package.json';
                                        Hint = '缺 electron，先运行: npm install' },
  @{ Path = 'node_modules\electron-builder\cli.js';
                                        Hint = '缺 electron-builder，先运行: npm install --save-dev electron-builder' },
  @{ Path = 'electron\main.cjs';        Hint = '缺 electron/main.cjs（package.json 的 main 入口），仓库不完整' },
  @{ Path = 'electron-builder.yml';     Hint = '缺 electron-builder.yml（electron-builder 配置）' },
  @{ Path = 'assets\icon.png';          Hint = '缺 assets/icon.png，打不出带图标的 exe' }
)
foreach ($f in $requireFiles) {
  if (-not (Test-Path (Join-Path $root $f.Path))) { Fail $f.Hint }
}

# ---------------- 2) vite build ----------------
if ($SkipBuild) {
  if (-not (Test-Path (Join-Path $root 'dist\index.html'))) {
    Fail '指定了 -SkipBuild，但 dist/index.html 不存在；去掉 -SkipBuild 重新构建'
  }
  Write-Step '跳过 vite build（复用现有 dist/）'
} else {
  Write-Step 'vite build（构建 Vue 控制台 -> dist/）'
  & npm.cmd run build
  if ($LASTEXITCODE -ne 0) { Fail "vite build 失败（exit $LASTEXITCODE）" }
  if (-not (Test-Path (Join-Path $root 'dist\index.html'))) { Fail 'vite build 没产出 dist/index.html' }
}

# ---------------- 3) electron-builder ----------------
Write-Step "electron-builder --win $Target"

$cli = Join-Path $root 'node_modules\electron-builder\cli.js'
$builderArgs = @($cli, '--win', $Target, '--publish', 'never')
$outDir = 'release'
if ($NoAsar) {
  $outDir = 'release-noasar'
  $builderArgs += @('--config.asar=false', "--config.directories.output=$outDir")
}
if ($NoExeEdit) {
  # 跳过 rcedit：不把图标/版本信息写进 exe 本体（winCodeSign 解压失败时的兜底）
  $builderArgs += @('--config.win.signAndEditExecutable=false')
}

Write-Host ("  命令: node {0}" -f ($builderArgs -join ' '))
Write-Host '  （首次运行会下载 winCodeSign / nsis 工具链到 %LOCALAPPDATA%\electron-builder\Cache，可能较慢）'

& node @builderArgs
if ($LASTEXITCODE -ne 0) {
  Write-Host @"

常见原因排查：
  1. 联网下载工具链失败（GitHub 慢/被墙）：设置镜像后重试，例如
       `$env:ELECTRON_BUILDER_BINARIES_MIRROR = 'https://npmmirror.com/mirrors/electron-builder-binaries/'
       `$env:ELECTRON_MIRROR = 'https://npmmirror.com/mirrors/electron/'
     然后 npm run build:exe
  2. winCodeSign 解压报 "Cannot create symbolic link ... 客户端没有所需的特权"：
     Windows 默认不允许普通用户创建符号链接（没开开发者模式/不是管理员）。
     处理办法二选一：
       a) 管理员开一次开发者模式，或右键以管理员身份跑本脚本
       b) 兜底：npm run build:exe -- -NoExeEdit （跳过 exe 图标/版本信息写入，不影响功能）
     详细修复见 docs/打包.md「winCodeSign 解压失败」
  3. NSIS 目标失败但 portable 成功：说明只是安装器工具链下载失败，先用 npm run build:exe
  4. 端口/文件被占用：关掉正在运行的 鲸live / electron 进程后重试
  5. 详细日志：加 --config.debug=1 重跑，或看 %LOCALAPPDATA%\electron-builder\Cache
"@ -ForegroundColor Yellow
  Fail "electron-builder 打包失败（exit $LASTEXITCODE）"
}

# ---------------- 4) 汇报产物 ----------------
Write-Step '打包完成，产物如下'
$outAbs = Join-Path $root $outDir
if (-not (Test-Path $outAbs)) { Fail "没找到产物目录: $outAbs" }

$artifacts = @(Get-ChildItem $outAbs -Filter '*.exe' -File | Sort-Object LastWriteTime -Descending)
if ($artifacts.Count -eq 0) { Fail "产物目录里没有 exe: $outAbs" }

foreach ($a in $artifacts) {
  $mb = [math]::Round($a.Length / 1MB, 1)
  Write-Ok ("  {0}" -f $a.FullName)
  Write-Host  ("    大小: {0} MB    修改时间: {1}" -f $mb, $a.LastWriteTime)
}
Write-Host ("  产物目录: {0}" -f $outAbs)
Write-Host ''
Write-Host '提示：config.json / .env 不打进包里，需要放在 exe 同目录（或设 WT_CONFIG / DEEPLIVE_ENV 环境变量）'
Write-Host '     详见 docs/打包.md'
exit 0
