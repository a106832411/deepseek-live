/**
 * 鲸语核心服务（移植版，零 npm 依赖）
 * - 兼容原 WhaleTalk 的 HTTP API（8787 那套）+ WS /ws
 * - AI 回复走 DeepSeek，语音走本地 GPT-SoVITS 代理（9881）
 * - 点歌请求转发给独立点歌系统（写 song_requests.jsonl）
 * - 静态页：/dashboard /overlay /songs
 */
const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PUBLIC = path.join(ROOT, 'public');
const LOG_DIR = path.join(ROOT, 'logs');
fs.mkdirSync(LOG_DIR, { recursive: true });

function log(msg) {
  const line = `[${new Date().toISOString().slice(11, 19)}] ${msg}`;
  console.log(line);
  try {
    fs.appendFileSync(path.join(LOG_DIR, 'app.log'), line + '\n');
  } catch (e) {}
}

// ---------------- 配置 ----------------
const CONFIG_PATH = process.env.WT_CONFIG || path.join(ROOT, 'config.json');
const DEFAULT_CONFIG = {
  platform: 'douyin',
  douyin: { mode: 'relay', web_rid: '447058766923' },
  ai: { backend: 'openai', openai_base: 'https://api.deepseek.com/v1', openai_model: 'deepseek-chat', openai_key: '', thinking: 'off', max_chars: 20 },
  persona: { system: '你是直播间里的小鲸鱼，说话可爱简短。' },
  tts: { api_url: 'http://127.0.0.1:9881', ref_audio: '', prompt_text: '梁山在上，今天大白饭也要变得美味呀。', speed: 0.95, sample_steps: 32, top_k: 20, temperature: 1.0, auto_play: true },
  events: {
    danmaku: { enabled: true, probability: 100, cooldown_user: 5, mode: 'ai' },
    enter: { enabled: true, cooldown_user: 30, mode: 'template', templates: ['欢迎{name}，随便坐别客气～'] },
    gift: { enabled: true, cooldown_user: 10, mode: 'ai' },
    follow: { enabled: true, cooldown_user: 60, mode: 'template', templates: ['谢谢{name}的关注！'] },
    like: { enabled: true, cooldown_user: 60 }
  },
  fixed_replies: [],
  merge_window: 0,
  songs: { enabled: true, request_file: 'song_requests.jsonl', max_minutes: 10, max_queue: 20 }
};

function deepMerge(a, b) {
  const out = Array.isArray(a) ? a.slice() : Object.assign({}, a);
  for (const k of Object.keys(b || {})) {
    if (b[k] && typeof b[k] === 'object' && !Array.isArray(b[k])) out[k] = deepMerge(out[k] || {}, b[k]);
    else out[k] = b[k];
  }
  return out;
}

let config = deepMerge(DEFAULT_CONFIG, {});
function loadConfig() {
  try {
    if (fs.existsSync(CONFIG_PATH)) {
      config = deepMerge(DEFAULT_CONFIG, JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8')));
      log('已加载配置: ' + CONFIG_PATH);
    } else {
      log('未找到配置，使用默认配置');
    }
  } catch (e) {
    log('配置读取失败: ' + e.message);
  }
}
function saveConfig() {
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2), 'utf8');
}

// ---------------- 状态 ----------------
const state = {
  started: false,
  startedAt: 0,
  connected: process.env.WT_RELAY === undefined ? true : false,
  stats: { enter: 0, danmaku: 0, gift: 0, follow: 0, like: 0, say: 0 },
  recent: [],
  lastUser: {},
  history: [],
  agents: new Set(),
  ttsQueue: Promise.resolve(),
  speaking: false
};

function pushRecent(kind, name, text) {
  state.recent.unshift({ t: new Date().toTimeString().slice(0, 8), kind, name, text });
  state.recent = state.recent.slice(0, 50);
}

// ---------------- 极简 WebSocket 服务端 ----------------
const WS_MAGIC = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';
function wsAccept(key) {
  return crypto.createHash('sha1').update(key + WS_MAGIC).digest('base64');
}
function wsFrame(str) {
  const payload = Buffer.from(str, 'utf8');
  const len = payload.length;
  let header;
  if (len < 126) header = Buffer.from([0x81, len]);
  else if (len < 65536) {
    header = Buffer.alloc(4);
    header[0] = 0x81; header[1] = 126; header.writeUInt16BE(len, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x81; header[1] = 127; header.writeBigUInt64BE(BigInt(len), 2);
  }
  return Buffer.concat([header, payload]);
}
function wsBroadcast(obj) {
  const frame = wsFrame(JSON.stringify(obj));
  for (const sock of state.agents) {
    try { sock.write(frame); } catch (e) { state.agents.delete(sock); }
  }
}
function handleWsUpgrade(req, socket) {
  const key = req.headers['sec-websocket-key'];
  if (!key) return socket.destroy();
  socket.write(
    'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n' +
    'Sec-WebSocket-Accept: ' + wsAccept(key) + '\r\n\r\n'
  );
  state.agents.add(socket);
  socket.on('data', (buf) => {
    // 忽略客户端帧内容（overlay 只收不发），但要处理 ping
    if (buf.length >= 2 && (buf[0] & 0x0f) === 0x9) {
      try { socket.write(Buffer.from([0x8a, 0x00])); } catch (e) {}
    }
  });
  socket.on('close', () => state.agents.delete(socket));
  socket.on('error', () => state.agents.delete(socket));
  safeSend(socket, {
    kind: 'hello',
    session: { active: state.started, since: state.startedAt },
    config: { persona: config.persona, tts: { speed: config.tts.speed } },
    stats: state.stats,
    recent: state.recent.slice(0, 6)
  });
}
function safeSend(sock, obj) {
  try { sock.write(wsFrame(JSON.stringify(obj))); } catch (e) {}
}

// ---------------- HTTP 工具 ----------------
function json(res, obj, code = 200) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(body) });
  res.end(body);
}
function readBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c) => (data += c));
    req.on('end', () => {
      try { resolve(data ? JSON.parse(data) : {}); } catch (e) { resolve({}); }
    });
  });
}
function httpPostJson(url, obj, headers = {}, timeout = 30000) {
  return new Promise((resolve, reject) => {
    try {
      const u = new URL(url);
      const lib = u.protocol === 'https:' ? https : http;
      const body = Buffer.from(JSON.stringify(obj));
      const req = lib.request(
        { hostname: u.hostname, port: u.port || (u.protocol === 'https:' ? 443 : 80), path: u.pathname + u.search, method: 'POST',
          headers: Object.assign({ 'Content-Type': 'application/json', 'Content-Length': body.length }, headers), timeout },
        (res) => {
          const chunks = [];
          res.on('data', (c) => chunks.push(c));
          res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks) }));
        }
      );
      req.on('error', reject);
      req.on('timeout', () => req.destroy(new Error('timeout')));
      req.write(body);
      req.end();
    } catch (e) { reject(e); }
  });
}

// ---------------- AI ----------------
async function aiReply(event, extraPrompt) {
  const ai = config.ai || {};
  if (!ai.openai_key) return null;
  const persona = (config.persona && config.persona.system) || '你是直播间的小鲸鱼。';
  const sys = `你是直播间的虚拟主播小鲸鱼。${persona}
规则：只输出要说的那一句话，20字以内，口语化，不要解释、不要引号、不要表情符号。`;
  let user = '';
  if (event.type === 'enter') user = `观众「${event.name}」进入直播间，欢迎他（她）。`;
  else if (event.type === 'follow') user = `观众「${event.name}」关注了直播间，感谢他（她）。`;
  else if (event.type === 'gift') user = `观众「${event.name}」送出礼物「${event.gift}」${event.num > 1 ? ' x' + event.num : ''}，感谢他（她）。`;
  else user = `观众「${event.name}」说：${event.text}`;
  if (extraPrompt) user += ' ' + extraPrompt;
  try {
    const r = await httpPostJson((ai.openai_base || 'https://api.deepseek.com/v1').replace(/\/$/, '') + '/chat/completions', {
      model: ai.openai_model || 'deepseek-chat',
      messages: [{ role: 'system', content: sys }, { role: 'user', content: user }],
      max_tokens: 120,
      temperature: ai.temperature === undefined ? 0.8 : ai.temperature
    }, { Authorization: 'Bearer ' + ai.openai_key }, 30000);
    if (r.status !== 200) { log('AI 返回失败 ' + r.status); return null; }
    const j = JSON.parse(r.body.toString('utf8'));
    let text = (j.choices && j.choices[0] && j.choices[0].message && j.choices[0].message.content) || '';
    text = text.replace(/\s+/g, ' ').trim();
    const max = Number(ai.max_chars) || 20;
    if (text.length > max) text = text.slice(0, max);
    return text || null;
  } catch (e) {
    log('AI 调用异常: ' + e.message);
    return null;
  }
}

// ---------------- TTS ----------------
function pythonPath() {
  const cands = [
    'D:\\gsv_rocm_gpu\\venv\\Scripts\\python.exe',
    'python'
  ];
  for (const c of cands) { try { if (c === 'python' || fs.existsSync(c)) return c; } catch (e) {} }
  return 'python';
}

function playWav(file) {
  return new Promise((resolve) => {
    const code = [
      'import sys, soundfile as sf, sounddevice as sd',
      'y, sr = sf.read(sys.argv[1], dtype="float32")',
      'sd.play(y, sr)',
      'sd.wait()'
    ].join('\n');
    const p = spawn(pythonPath(), ['-c', code, file], { windowsHide: true });
    p.on('exit', () => resolve());
    p.on('error', () => resolve());
  });
}

async function ttsSpeak(text) {
  if (!text) return;
  const tts = config.tts || {};
  state.stats.say++;
  pushRecent('say', '小鲸鱼', text);
  wsBroadcast({ kind: 'speak', text, t: Date.now() });
  state.speaking = true;
  try {
    const r = await httpPostJson((tts.api_url || 'http://127.0.0.1:9881').replace(/\/$/, '') + '/tts', {
      text,
      text_lang: 'zh',
      ref_audio_path: tts.ref_audio,
      prompt_text: tts.prompt_text || '',
      prompt_lang: 'zh',
      top_k: tts.top_k || 20,
      text_split_method: 'cut0',
      batch_size: 1,
      speed_factor: tts.speed || 1.0,
      sample_steps: tts.sample_steps || 32
    }, {}, 120000);
    if (r.status === 200 && r.body.length > 1000) {
      const file = path.join(os.tmpdir(), 'wt_' + Date.now() + '.wav');
      fs.writeFileSync(file, r.body);
      if (tts.auto_play !== false) await playWav(file);
      try { fs.unlinkSync(file); } catch (e) {}
    } else {
      log('TTS 失败 http=' + r.status);
    }
  } catch (e) {
    log('TTS 异常: ' + e.message);
  } finally {
    state.speaking = false;
    wsBroadcast({ kind: 'speak_end', t: Date.now() });
  }
}

function enqueueSpeech(text) {
  state.ttsQueue = state.ttsQueue.then(() => ttsSpeak(text)).catch(() => {});
  return state.ttsQueue;
}

// ---------------- 事件处理 ----------------
function pickTemplate(list, name) {
  if (!Array.isArray(list) || !list.length) return null;
  const t = list[Math.floor(Math.random() * list.length)];
  return String(t).replace(/\{name\}/g, name).replace(/\{user\}/g, name);
}

async function handleEvent(ev) {
  if (!state.started) return;
  const evCfg = (config.events || {})[ev.type] || {};
  if (evCfg.enabled === false) return;
  const now = Date.now();
  if (evCfg.cooldown_user) {
    const key = ev.type + ':' + (ev.name || '');
    if (state.lastUser[key] && now - state.lastUser[key] < evCfg.cooldown_user * 1000) return;
    state.lastUser[key] = now;
  }
  if (evCfg.probability !== undefined && evCfg.probability < 100) {
    if (Math.random() * 100 > Number(evCfg.probability)) return;
  }
  state.stats[ev.type] = (state.stats[ev.type] || 0) + 1;
  pushRecent('event', ev.name, ev.text || ev.gift || '');
  wsBroadcast({ kind: 'event', event: ev, t: now });

  // 点歌优先转交独立点歌系统
  if (ev.type === 'danmaku' && ev.text) {
    const m = ev.text.match(/^\s*[/／]?\s*(?:点歌|點歌)\s*[:：]?\s*(.+?)\s*$/);
    if (m) {
      const file = path.join(ROOT, 'tools', 'logs', config.songs.request_file || 'song_requests.jsonl');
      try {
        fs.mkdirSync(path.dirname(file), { recursive: true });
        fs.appendFileSync(file, JSON.stringify({ t: new Date().toTimeString().slice(0, 8), name: ev.name, query: m[1] }) + '\n');
        log('点歌转交: ' + ev.name + ' -> ' + m[1]);
        await enqueueSpeech(`收到${ev.name}点的歌，去排队啦！`);
      } catch (e) { log('点歌写入失败: ' + e.message); }
      return;
    }
  }

  // 固定回复
  if (ev.type === 'danmaku' && ev.text) {
    for (const rule of config.fixed_replies || []) {
      const kws = rule.keywords || rule.keyword || [];
      const arr = Array.isArray(kws) ? kws : [kws];
      if (arr.some((k) => k && ev.text.includes(k)) && rule.reply) {
        const text = String(rule.reply).replace(/\{name\}/g, ev.name);
        log('固定回复: ' + text);
        wsBroadcast({ kind: 'reply', text, by: ev.name, t: Date.now() });
        await enqueueSpeech(text);
        return;
      }
    }
  }

  const mode = evCfg.mode || 'ai';
  let text = null;
  if (mode === 'template') {
    if (ev.type === 'enter') text = pickTemplate(evCfg.templates || (config.events.enter.templates), ev.name);
    else if (ev.type === 'follow') text = pickTemplate(evCfg.templates || (config.events.follow.templates), ev.name);
  }
  if (!text && epAIEnabled(ev)) text = await aiReply(ev);
  if (!text && ev.type === 'enter') text = `欢迎${ev.name}！`;
  if (!text) return;
  log('回复: ' + text);
  wsBroadcast({ kind: 'reply', text, by: ev.name, t: Date.now() });
  await enqueueSpeech(text);
}

function epAIEnabled(ev) {
  const c = config.events[ev.type] || {};
  if (c.mode === 'template') return false;
  if (c.mode === 'off') return false;
  return !!config.ai.openai_key;
}

// ---------------- HTTP 路由 ----------------
async function route(req, res) {
  const u = new URL(req.url, 'http://127.0.0.1');
  const p = u.pathname;
  const method = req.method || 'GET';

  // 静态
  if (method === 'GET' && (p === '/' || p === '/dashboard')) return serveFile(res, 'dashboard.html');
  if (method === 'GET' && p === '/overlay') return serveFile(res, 'overlay.html');
  if (method === 'GET' && p === '/songs') return serveFile(res, 'songs.html');
  if (method === 'GET' && p === '/console') return serveFile(res, 'console.html');
  if (method === 'GET' && p === '/live2d') return serveFile(res, 'live2d.html');
  if (method === 'GET' && p.startsWith('/live2d/')) return serveStatic(res, p.slice(1));

  if (p === '/api/health') {
    return json(res, {
      ok: true, running: state.started, mode: (config.douyin || {}).mode,
      connected: state.connected, stats: state.stats, uptime: state.startedAt ? Math.round((Date.now() - state.startedAt) / 1000) : 0
    });
  }
  if (p === '/api/session/start') {
    state.started = true; state.startedAt = Date.now();
    log('会话开始');
    wsBroadcast({ kind: 'status', running: true, t: Date.now() });
    return json(res, { ok: true, active: true });
  }
  if (p === '/api/session/stop') {
    state.started = false;
    log('会话结束');
    wsBroadcast({ kind: 'status', running: false, t: Date.now() });
    return json(res, { ok: true, active: false });
  }
  if (p === '/api/config') {
    if (method === 'GET') return json(res, config);
    const body = await readBody(req);
    config = deepMerge(config, body);
    saveConfig();
    log('配置已更新');
    return json(res, { ok: true });
  }
  if (p === '/api/say') {
    const body = await readBody(req);
    const text = (body.text || '').trim();
    if (!text) return json(res, { ok: false, error: 'empty' }, 400);
    await enqueueSpeech(text);
    return json(res, { ok: true });
  }
  if (p === '/api/test_event') {
    const body = await readBody(req);
    const ev = body.event || { type: 'danmaku', name: '测试观众', text: '你好' };
    await handleEvent(ev);
    return json(res, { ok: true });
  }
  if (p === '/api/douyin/ingest') {
    const body = await readBody(req);
    const events = body.events || (body.event ? [body.event] : []);
    for (const ev of events) await handleEvent(ev);
    return json(res, { ok: true, count: events.length });
  }
  if (p === '/api/douyin/connect' || p === '/api/douyin/disconnect') {
    if (p.endsWith('connect')) { state.connected = true; log('抖音已连接(relay)'); }
    else { state.connected = false; log('抖音已断开'); }
    return json(res, { ok: true, connected: state.connected });
  }
  if (p === '/api/douyin/health') {
    return json(res, { connected: state.connected, mode: (config.douyin || {}).mode || 'relay', running: state.started, counts: state.stats, recent: state.recent.slice(0, 8) });
  }
  if (p === '/api/stats/reset') {
    state.stats = { enter: 0, danmaku: 0, gift: 0, follow: 0, like: 0, say: 0 };
    return json(res, { ok: true });
  }
  if (p === '/api/stats') return json(res, state.stats);
  if (p === '/api/recent') return json(res, state.recent);
  if (p === '/api/songs/state') {
    try {
      const r = await new Promise((resolve, reject) => {
        http.get('http://127.0.0.1:9886/api/songs', (res2) => {
          const chunks = []; res2.on('data', (c) => chunks.push(c));
          res2.on('end', () => resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))));
        }).on('error', reject);
      });
      return json(res, r);
    } catch (e) { return json(res, { current: null, queue: [] }); }
  }
  return json(res, { ok: false, error: 'not found' }, 404);
}

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.png': 'image/png', '.jpg': 'image/jpeg',
  '.webp': 'image/webp', '.css': 'text/css; charset=utf-8', '.moc3': 'application/octet-stream',
  '.wasm': 'application/wasm', '.wav': 'audio/wav', '.mp3': 'audio/mpeg'
};

function serveStatic(res, rel) {
  const safe = rel.replace(/\\/g, '/').replace(/\.\./g, '');
  const file = path.join(PUBLIC, safe);
  if (!file.startsWith(PUBLIC) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404); return res.end('not found');
  }
  const ext = path.extname(file).toLowerCase();
  const body = fs.readFileSync(file);
  res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream', 'Content-Length': body.length, 'Cache-Control': 'no-cache' });
  res.end(body);
}

function serveFile(res, name) {
  const file = path.join(PUBLIC, name);
  if (!fs.existsSync(file)) { res.writeHead(404); return res.end('not found'); }
  const body = fs.readFileSync(file);
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Length': body.length });
  res.end(body);
}

// ---------------- 启动 ----------------
function start(port) {
  loadConfig();
  const server = http.createServer((req, res) => {
    route(req, res).catch((e) => {
      log('路由异常: ' + e.message);
      try { json(res, { ok: false, error: e.message }, 500); } catch (err) {}
    });
  });
  server.on('upgrade', (req, socket) => {
    if (req.url.startsWith('/ws')) handleWsUpgrade(req, socket);
    else socket.destroy();
  });
  server.listen(port, '127.0.0.1', () => {
    log(`鲸语移植版已启动: http://127.0.0.1:${port}`);
    log(`面板 http://127.0.0.1:${port}/dashboard  弹幕层 http://127.0.0.1:${port}/overlay  歌单 http://127.0.0.1:${port}/songs`);
  });
  return server;
}

module.exports = { start, config, state, handleEvent, enqueueSpeech, aiReply };
