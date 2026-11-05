/** Electron 主进程：内嵌启动鲸语移植版服务 + 打开控制台窗口 */
const path = require('path');
const { app, BrowserWindow, shell } = require('electron');
const { start } = require('../server/core.cjs');

const PORT = Number(process.env.WT_PORT || 8788);
let win = null;

function createWindow() {
  win = new BrowserWindow({
    width: 1400,
    height: 900,
    backgroundColor: '#0b1220',
    title: '鲸语 · 移植版',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  const devUrl = process.env.VITE_DEV_SERVER_URL;
  win.loadURL(devUrl || `http://127.0.0.1:${PORT}/dashboard`);
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

app.whenReady().then(() => {
  start(PORT);
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
