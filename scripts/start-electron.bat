@echo off
chcp 65001 >nul
set ELECTRON_RUN_AS_NODE=
rem 清掉 ELECTRON_RUN_AS_NODE，否则 Electron 会以纯 Node 方式运行
cd /d "%~dp0.."
if not exist node_modules (
  echo [提示] 首次运行需要安装依赖...
  call npm install
)
call npm run electron
pause