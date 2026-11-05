/** 安全桥：只暴露只读环境信息（需要更多能力时在这里白名单添加） */
const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('whale', {
  version: process.versions.electron,
  platform: process.platform
});
