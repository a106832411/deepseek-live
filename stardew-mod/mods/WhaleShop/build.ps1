# WhaleShop 构建脚本：编译并把 mod 装进 Mods 目录
# 用法：
#   .\build.ps1 -GameDir "E:\steam\steamapps\common\Stardew Valley"
#   .\build.ps1 -GameDir "..." -Dotnet "C:\path\dotnet.exe" -NoInstall
param(
  [Parameter(Mandatory = $true)][string]$GameDir,
  [string]$Dotnet = "",
  [switch]$NoInstall
)

$ErrorActionPreference = 'Stop'
$Here = $PSScriptRoot
Write-Host "== WhaleShop 构建 ==" -ForegroundColor Cyan

# 1) 校验游戏目录
if (-not (Test-Path (Join-Path $GameDir 'Stardew Valley.dll'))) {
  throw "找不到 $GameDir\Stardew Valley.dll，-GameDir 要指到游戏根目录（不是 Mods）"
}
if (-not (Test-Path (Join-Path $GameDir 'StardewModdingAPI.dll'))) {
  throw "找不到 $GameDir\StardewModdingAPI.dll，确认已装 SMAPI"
}
Write-Host "游戏目录 OK：$GameDir" -ForegroundColor Green

# 2) 找 dotnet
function Find-Dotnet {
  if ($Dotnet -and (Test-Path $Dotnet)) { return $Dotnet }
  foreach ($c in @('dotnet', 'D:\live\.tools\dotnet\dotnet.exe', "$env:ProgramFiles\dotnet\dotnet.exe",
                   "${env:ProgramFiles(x86)}\dotnet\dotnet.exe", "$env:LOCALAPPDATA\Microsoft\dotnet\dotnet.exe")) {
    try {
      $cmd = Get-Command $c -ErrorAction Stop
      return $cmd.Source
    } catch { }
  }
  return $null
}

$dn = Find-Dotnet
if (-not $dn -and -not $NoInstall) {
  $installer = 'D:\live\deepseek-live\_dotnet\dotnet-install.ps1'
  if (Test-Path $installer) {
    Write-Host "没找到 dotnet，用仓库里的 dotnet-install.ps1 装到 D:\live\.tools\dotnet ..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path 'D:\live\.tools\dotnet' | Out-Null
    & powershell -NoProfile -ExecutionPolicy Bypass -File $installer -Channel 6.0 -InstallDir 'D:\live\.tools\dotnet'
    $dn = 'D:\live\.tools\dotnet\dotnet.exe'
  }
}
if (-not $dn) { throw "没找到 dotnet SDK 6.0+，先装一个（或传 -Dotnet 路径）" }
Write-Host "dotnet：$dn" -ForegroundColor Green
& $dn --version

# 3) 编译
Push-Location $Here
try {
  & $dn build "WhaleShop.csproj" -c Release "-p:GamePath=$GameDir" --nologo
  if ($LASTEXITCODE -ne 0) { throw "编译失败（exit $LASTEXITCODE）" }
} finally { Pop-Location }

# 4) 安装
$outDll = Join-Path $Here 'bin\Release\net6.0\WhaleShop.dll'
if (-not (Test-Path $outDll)) { $outDll = Join-Path $Here 'bin\Release\WhaleShop.dll' }
if (-not (Test-Path $outDll)) { throw "编译产物没找到：$outDll" }

$target = Join-Path $GameDir 'Mods\WhaleShop'
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item $outDll $target -Force
Copy-Item (Join-Path $Here 'Manifest.json') $target -Force
New-Item -ItemType Directory -Force -Path (Join-Path $target 'orders') | Out-Null

Write-Host ""
Write-Host "✅ 已安装到 $target" -ForegroundColor Green
Write-Host "   启动游戏后应看到日志：WhaleShop 就绪，订单目录：$target\orders"
Write-Host "   试一单（游戏里加载存档后执行）："
$demo = '{"actionType":"buy_seed","item":"Parsnip","count":5}'
Write-Host ("     '" + $demo + "' | Set-Content '" + $target + "\orders\test.json' -Encoding UTF8")
