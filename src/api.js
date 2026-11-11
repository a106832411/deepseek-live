/**
 * 统一调用层：
 * - Electron 内：走 IPC（window.whale.invoke），由主进程直接控制核心服务
 * - 浏览器/无 Electron（headless）：回退到 HTTP API
 */
const hasIpc = typeof window !== 'undefined' && window.whale && typeof window.whale.invoke === 'function';
const BASE = (typeof window !== 'undefined' && window.whale && window.whale.base)
  || (typeof location !== 'undefined' && location.port === '5173' ? 'http://127.0.0.1:8788' : location.origin);

async function fetchJson(path, opts) {
  const r = await fetch(BASE + path, opts);
  return r.json();
}

async function rest(cmd, payload) {
  switch (cmd) {
    case 'status': {
      const [h, recent] = await Promise.all([fetchJson('/api/health'), fetchJson('/api/recent').catch(() => [])]);
      return { running: h.running, mode: h.mode, uptime: h.uptime, stats: h.stats, recent };
    }
    case 'session:start':
      return fetchJson('/api/session/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    case 'session:stop':
      return fetchJson('/api/session/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    case 'config:get':
      return fetchJson('/api/config');
    case 'config:set':
      return fetchJson('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}) });
    case 'say':
      return fetchJson('/api/say', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}) });
    case 'event:test':
      return fetchJson('/api/test_event', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}) });
    case 'stats:reset':
      return fetchJson('/api/stats/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    case 'songs:state':
      return fetchJson('/api/songs/state');
    case 'songs:request':
      return fetchJson('/api/songs/request', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}) });
    case 'deps:get':
      return fetchJson('/api/deps');
    case 'deps:start':
      return fetchJson('/api/deps/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}) });
    case 'deps:stop':
      return fetchJson('/api/deps/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}) });
    default:
      throw new Error('unknown command: ' + cmd);
  }
}

export async function invoke(cmd, payload) {
  if (hasIpc) return window.whale.invoke(cmd, payload);
  return rest(cmd, payload);
}

export const isElectron = () => hasIpc;
