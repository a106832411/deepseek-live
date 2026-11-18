# 首次安装：复制配置、安装前端依赖
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path 'config.json')) {
    Copy-Item 'config.example.json' 'config.json'
    Write-Host '[OK] 已生成 config.json，请填写 ai.openai_key / tts.ref_audio / douyin.web_rid' -ForegroundColor Yellow
}
if (Get-Command npm -ErrorAction SilentlyContinue) {
    npm install
    Write-Host '[OK] 前端依赖安装完成' -ForegroundColor Green
} else {
    Write-Host '[SKIP] 未检测到 npm；仅运行服务端不需要依赖：node server/index.cjs --port 8788' -ForegroundColor Yellow
}