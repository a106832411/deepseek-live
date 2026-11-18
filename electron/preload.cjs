/** 安全桥：渲染进程通过 window.whale.invoke(channel, payload) 调用主进程核心服务 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('whale', {
  version: process.versions.electron,
  platform: process.platform,
  base: process.env.WT_BASE || 'http://127.0.0.1:8788',
  invoke: (channel, payload) => ipcRenderer.invoke(channel, payload)
});
