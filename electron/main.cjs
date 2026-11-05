/** Electron 主进程：内嵌启动鲸语核心服务 + IPC 桥 + 控制台窗口 */
const path = require('path');
const { app, BrowserWindow, shell } = require('electron');

const core = require('../app/main/core.cjs');
const { registerIpc } = require('../app/main/ipc.cjs');

const PORT = Number(process.env.WT_PORT || 8788);
let win = null;

function createWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 960,
    backgroundColor: '#0b1220',
    title: '鲸语 · 移植版',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });
  const devUrl = process.env.VITE_DEV_SERVER_URL;
  win.loadURL(devUrl || `http://127.0.0.1:${PORT}/console`);
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

app.whenReady().then(() => {
  // 1) 核心服务（HTTP + WS + AI + TTS + 点歌），供 OBS / 中继 / 外部脚本使用
  core.start(PORT);
  // 2) IPC 桥：Vue 控制台直接控制核心（不经过 HTTP）
  registerIpc(core);
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
