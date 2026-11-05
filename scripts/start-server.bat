@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist config.json (
  echo [提示] 没有 config.json，已从 config.example.json 复制，请先填写 API Key 等信息
  copy config.example.json config.json >nul
  notepad config.json
  pause
)
node server\index.cjs --port 8788
pause