/** Electron 主进程：内嵌启动鲸live核心服务 + IPC 桥 + 控制台窗口 */
const path = require('path');
const { app, BrowserWindow, shell, Menu } = require('electron');

const core = require('../app/main/core.cjs');
const { registerIpc } = require('../app/main/ipc.cjs');

const PORT = core.resolvePort(Number(process.env.DEEPLIVE_PORT || process.env.WT_PORT || 0) || undefined);
process.env.WT_BASE = 'http://127.0.0.1:' + PORT;
let win = null;
function createWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 960,
    backgroundColor: '#0b1220',
    title: '鲸live · 直播控制台',
    icon: path.join(__dirname, '..', 'assets', 'icon.png'),
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });
  const fs = require('fs');
  const devUrl = process.env.VITE_DEV_SERVER_URL;
  const distIndex = path.join(__dirname, '..', 'dist', 'index.html');
  if (devUrl) win.loadURL(devUrl);
  else if (fs.existsSync(distIndex)) win.loadFile(distIndex);
  else win.loadURL(`http://127.0.0.1:${PORT}/console`);
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

app.whenReady().then(() => {
  // 去掉默认系统菜单栏（File/Edit/View...）
  Menu.setApplicationMenu(null);
  // 1) 核心服务（HTTP + WS + AI + TTS + 点歌），供 OBS / 中继 / 外部脚本使用
  //    端口被占用时核心会自动 +1 顺延，所以窗口要等真正监听成功再开（否则 IPC 基址会错）
  core.start(PORT, (actualPort) => {
    process.env.WT_BASE = 'http://127.0.0.1:' + actualPort;
    // 2) IPC 桥：Vue 控制台直接控制核心（不经过 HTTP）
    registerIpc(core);
    createWindow();
  });
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
