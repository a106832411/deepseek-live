@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if not exist node_modules (
  echo [提示] 首次运行需要安装依赖...
  call npm install
)
call npm run electron
pause