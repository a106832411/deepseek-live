/**
 * Electron IPC 桥：把核心服务暴露给渲染进程（Vue 控制台）。
 * 渲染进程通过 preload 的 window.whale.invoke(channel, payload) 调用。
 */
const { ipcMain } = require('electron');

const CHANNELS = {
  status: (svc) => svc.status(),
  'session:start': (svc) => svc.startSession(),
  'session:stop': (svc) => svc.stopSession(),
  'config:get': (svc) => svc.config,
  'config:set': (svc, patch) => svc.setConfig(patch),
  say: (svc, payload) => svc.say((payload && payload.text) || ''),
  'event:test': (svc, payload) => svc.testEvent((payload && payload.event) || payload),
  ingest: (svc, payload) => svc.ingest((payload && payload.events) || []),
  'stats:reset': (svc) => svc.resetStats(),
  'songs:state': (svc) => {
    const cfgSongs = (svc.config && svc.config.songs) || {};
    return {
      mode: cfgSongs.perform === 'play' ? 'play' : 'sing',
      playing: !!svc.state.song.current,
      current: svc.state.song.current,
      queue: svc.state.song.queue.map((x) => ({ name: x.song.name, by: x.by }))
    };
  },
  'songs:request': (svc, payload) => {
    const name = (payload && payload.name) || '手动点歌';
    const query = (payload && (payload.query || payload.song)) || '';
    const play = ((svc.config && svc.config.songs) || {}).perform === 'play';
    return play ? svc.requestSong(name, query) : svc.requestSing(name, query);
  },
  'songs:skip': (svc) => svc.skipSong(),
  'obs:status': (svc, payload) => svc.obsProbe(payload || {}),
  'obs:setup': (svc, payload) => svc.obsSetup(payload || {}),
  'deps:get': (svc) => svc.depsStatus(),
  'deps:start': (svc, payload) => svc.depsStart((payload && (payload.name || payload.key)) || ''),
  'deps:stop': (svc, payload) => svc.depsStop((payload && (payload.name || payload.key)) || '')
};

function registerIpc(service) {
  for (const [channel, handler] of Object.entries(CHANNELS)) {
    ipcMain.handle(channel, async (_event, payload) => {
      try {
        return await handler(service, payload);
      } catch (e) {
        return { ok: false, error: String(e && e.message ? e.message : e) };
      }
    });
  }
}

module.exports = { registerIpc, CHANNELS };
