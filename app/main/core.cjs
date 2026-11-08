/**
 * 鲸live核心服务（零 npm 依赖）
 * - HTTP API + WS /ws
 * - AI 回复走 DeepSeek，语音走本地 GPT-SoVITS 代理（9881）
 * - 点歌/唱歌 = 清唱：AI 用 TTS 自己的声音清唱（抓歌词 → 逐句合成 → 旋律后处理 → 串成一首）
 * - 旧的"播放原曲"保留：songs.perform = 'play'
 * - 静态页：/dashboard /overlay /songs
 */
const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');
const { spawn } = require('child_process');
const obs = require('./obs.cjs');

const ROOT = path.resolve(__dirname, '..', '..');
const PUBLIC = path.join(ROOT, 'public');
const LOG_DIR = path.join(ROOT, 'logs');
fs.mkdirSync(LOG_DIR, { recursive: true });

let LOG_TAG = '';
function log(msg) {
  const line = `[${new Date().toISOString().slice(11, 19)}]${LOG_TAG ? ' ' + LOG_TAG : ''} ${msg}`;
  console.log(line);
  try {
    fs.appendFileSync(path.join(LOG_DIR, 'app.log'), line + '\n');
  } catch (e) {}
}

// ---------------- .env（本地配置，不入库）----------------
function loadDotEnv() {
  const candidates = [
    process.env.DEEPLIVE_ENV || '',
    path.join(process.cwd(), '.env'),
    path.join(ROOT, '.env')
  ].filter(Boolean);
  const out = {};
  for (const p of candidates) {
    if (!fs.existsSync(p)) continue;
    const text = fs.readFileSync(p, 'utf8');
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith('#')) continue;
      const eq = line.indexOf('=');
      if (eq <= 0) continue;
      const key = line.slice(0, eq).trim();
      let val = line.slice(eq + 1).trim();
      if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
        val = val.slice(1, -1);
      }
      out[key] = val.replace(/\\n/g, '\n');
    }
    log('已加载环境变量: ' + p);
    break;
  }
  return out;
}
const DOTENV = loadDotEnv();

/** 取值优先级：真实环境变量 > .env > 默认值 */
function envOf(key, def) {
  if (process.env[key] !== undefined && process.env[key] !== '') return process.env[key];
  if (DOTENV[key] !== undefined && DOTENV[key] !== '') return DOTENV[key];
  return def;
}

function applyEnv(c) {
  const setPath = (pathArr, v) => {
    let o = c;
    for (let i = 0; i < pathArr.length - 1; i++) {
      if (!o[pathArr[i]] || typeof o[pathArr[i]] !== 'object') o[pathArr[i]] = {};
      o = o[pathArr[i]];
    }
    o[pathArr[pathArr.length - 1]] = v;
  };
  const str = (k, pathArr) => { const v = envOf(k); if (v !== undefined) setPath(pathArr, v); };
  const num = (k, pathArr) => { const v = envOf(k); if (v !== undefined) setPath(pathArr, Number(v)); };
  const bool = (k, pathArr) => { const v = envOf(k); if (v !== undefined) setPath(pathArr, ['1', 'true', 'yes', 'on'].includes(String(v).toLowerCase())); };
  const list = (k, pathArr) => { const v = envOf(k); if (v !== undefined) setPath(pathArr, String(v).split(',').map((x) => x.trim()).filter(Boolean)); };

  str('DEEPLIVE_AI_BASE', ['ai', 'openai_base']);
  str('DEEPLIVE_AI_MODEL', ['ai', 'openai_model']);
  str('DEEPLIVE_AI_KEY', ['ai', 'openai_key']);
  num('DEEPLIVE_AI_MAX_CHARS', ['ai', 'max_chars']);
  num('DEEPLIVE_AI_TEMPERATURE', ['ai', 'temperature']);
  str('DEEPLIVE_PERSONA', ['persona', 'system']);

  str('DEEPLIVE_TTS_URL', ['tts', 'api_url']);
  str('DEEPLIVE_TTS_REF_AUDIO', ['tts', 'ref_audio']);
  str('DEEPLIVE_TTS_PROMPT_TEXT', ['tts', 'prompt_text']);
  num('DEEPLIVE_TTS_SPEED', ['tts', 'speed']);
  num('DEEPLIVE_TTS_STEPS', ['tts', 'sample_steps']);
  num('DEEPLIVE_TTS_TOP_K', ['tts', 'top_k']);
  num('DEEPLIVE_TTS_TEMPERATURE', ['tts', 'temperature']);
  num('DEEPLIVE_TTS_REP', ['tts', 'repetition_penalty']);
  bool('DEEPLIVE_TTS_AUTO_PLAY', ['tts', 'auto_play']);

  str('DEEPLIVE_DOUYIN_MODE', ['douyin', 'mode']);
  str('DEEPLIVE_DOUYIN_RID', ['douyin', 'web_rid']);
  str('DEEPLIVE_DOUYIN_ROOM_URL', ['douyin', 'room_url']);

  bool('DEEPLIVE_SONGS_ENABLED', ['songs', 'enabled']);
  bool('DEEPLIVE_SONGS_INTERNAL', ['songs', 'internal']);
  bool('DEEPLIVE_SONGS_DEDUPE', ['songs', 'dedupe']);
  bool('DEEPLIVE_SONGS_AUTO_PLAY', ['songs', 'auto_play']);
  num('DEEPLIVE_SONGS_MAX_QUEUE', ['songs', 'max_queue']);
  list('DEEPLIVE_SONGS_SOURCES', ['songs', 'sources']);
  str('DEEPLIVE_SONGS_PERFORM', ['songs', 'perform']);

  // 清唱（唱歌 = 用 TTS 合成清唱，不播原曲）
  bool('DEEPLIVE_SING_ENABLED', ['sing', 'enabled']);
  str('DEEPLIVE_SING_MODE', ['sing', 'mode']);
  str('DEEPLIVE_SING_LYRICS', ['sing', 'lyrics']);
  num('DEEPLIVE_SING_MAX_LINES', ['sing', 'max_lines']);
  num('DEEPLIVE_SING_MAX_CHARS', ['sing', 'max_chars']);
  bool('DEEPLIVE_SING_INTRO', ['sing', 'intro']);
  bool('DEEPLIVE_SING_OUTRO', ['sing', 'outro']);
  bool('DEEPLIVE_SING_V2', ['sing', 'v2']);
  num('DEEPLIVE_SING_BEAT_SCALE', ['sing', 'beat_scale']);
  str('DEEPLIVE_SING_ENGINE', ['sing', 'engine']);
  str('DEEPLIVE_SING_MELODY', ['sing', 'melody']);
  num('DEEPLIVE_SING_BPM', ['sing', 'bpm']);
  bool('DEEPLIVE_SING_VIBRATO', ['sing', 'vibrato']);
  num('DEEPLIVE_SING_GAP_MS', ['sing', 'gap_ms']);
  str('DEEPLIVE_SING_FILLER', ['sing', 'filler']);

  str('DEEPLIVE_OBS_HOST', ['obs', 'host']);
  num('DEEPLIVE_OBS_PORT', ['obs', 'port']);
  str('DEEPLIVE_OBS_PASSWORD', ['obs', 'password']);
  str('DEEPLIVE_OBS_SCENE', ['obs', 'scene']);
  str('DEEPLIVE_OBS_BG', ['obs', 'bgColor']);
  str('DEEPLIVE_OBS_PRESET', ['obs', 'preset']);

  num('DEEPLIVE_MERGE_WINDOW', ['merge_window']);
  list('DEEPLIVE_FILTER_BLOCKLIST', ['filter', 'blocklist']);
  return c;
}

/** 端口解析：显式参数 > DEEPLIVE_PORT > WT_PORT > 8788 */
function resolvePort(p) {
  return Number(p || envOf('DEEPLIVE_PORT') || envOf('WT_PORT') || 8788);
}

// ---------------- 配置 ----------------
const CONFIG_PATH = process.env.WT_CONFIG || path.join(ROOT, 'config.json');
const DEFAULT_CONFIG = {
  platform: 'douyin',
  douyin: { mode: 'relay', web_rid: '447058766923' },
  ai: { backend: 'openai', openai_base: 'https://api.deepseek.com/v1', openai_model: 'deepseek-chat', openai_key: '', thinking: 'off', max_chars: 20 },
  persona: { system: '你是直播间里的虚拟主播小深，说话自然简洁。' },
  // prompt_text 必须和参考音逐字一致，否则 GPT-SoVITS 会念得又平又假
  tts: { api_url: 'http://127.0.0.1:9881', ref_audio: '', prompt_text: '梁山在上，今天大白饭也要变得美味呀。', speed: 1.0, sample_steps: 32, top_k: 30, temperature: 1.3, repetition_penalty: 1.1, auto_play: true },
  obs: { host: '127.0.0.1', port: 4455, password: '', scene: '鲸live直播', bgColor: '0b0c0e' },
  events: {
    danmaku: { enabled: true, probability: 100, cooldown_user: 5, mode: 'ai' },
    enter: { enabled: true, cooldown_user: 30, mode: 'template', templates: ['欢迎{name}，随便坐别客气～'] },
    gift: { enabled: true, cooldown_user: 10, mode: 'ai' },
    follow: { enabled: true, cooldown_user: 60, mode: 'template', templates: ['谢谢{name}的关注！'] },
    like: { enabled: true, cooldown_user: 60 }
  },
  fixed_replies: [],
  merge_window: 0,
  filter: { blocklist: ['送出', '送出了'] },
  songs: { enabled: true, internal: true, request_file: 'song_requests.jsonl', max_queue: 20, auto_play: true, dedupe: true, sources: ['netease', 'qq'], perform: 'sing' },
  // 清唱：点歌/唱歌时，用 AI 主播自己的 TTS 声音清唱（无伴奏），不播原曲
  sing: {
    enabled: true,
    mode: 'melody',          // melody=带旋律清唱 | read=纯念唱（自然朗读节奏）
    lyrics: 'netease',       // netease=抓原歌词 | ai=让 AI 现编几句
    max_lines: 6,            // 每首唱几句
    max_chars: 100,          // 整首歌词字数上限
    intro: true,             // 唱之前报幕（"一首《x》，清唱版"）
    outro: true,             // 唱完道谢
    melody: 'auto',          // auto | xiaoxingxing | shengri | folk1 | folk2
    bpm: 92,
    vibrato: true,
    gap_ms: 380,
    natural: true,              // melody 模式走自然旋律（弱颤音/不硬量化/句尾轻拖）
    v2: true,                   // 唱腔化 v2：按节拍拉伸元音（一字 0.4~0.8s）+ legato + 颤音，唱法感更强
    beat_scale: 0.75,           // v2 档位：0.55 保守 / 0.75 中等 / 0.95 明显
    engine: 'tts',              // tts=TTS 清唱（默认）| ace=真唱（ACE-Step 生成人声 + Seed-VC 换声，需本地部署）
    ace: {
      python: 'D:/sing_gpu/venv/Scripts/python.exe',
      script: 'D:/sing_gpu/ace_sing.py',
      svc_python: 'D:/seedvc/venv/Scripts/python.exe',
      svc_script: 'D:/seedvc/convert.py',
      duration: 22,             // 生成时长（秒）
      cfg: 0.75,                // Seed-VC 音色相似度 cfg
      steps: 30,                // Seed-VC diffusion steps
      timeout_sec: 600,
      tags: [                   // 每次随机挑一组 → 旋律/唱法不固定
        'female solo vocal, a cappella, no instruments, pure vocal, cute, clear diction, mandarin, 80 BPM',
        'female solo vocal, a cappella, no instruments, pure vocal, sweet, gentle, clear pronunciation, mandarin, 72 BPM',
        'female solo vocal, a cappella, no instruments, pure vocal, bright, playful, clear diction, mandarin, 92 BPM',
        'female solo vocal, a cappella, no instruments, pure vocal, soft breathy, tender, slow, mandarin, 68 BPM'
      ]
    },
    filler: '我清清嗓子，准备开唱啦～歌词有点长，我再看一眼～马上就好，再等我一下下～',
    pick_list: ['小星星', '生日快乐', '月亮代表我的心', '童年', '后来', '稻香', '晴天', '童话', '勇气', '宁夏']
  }
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
      const raw = fs.readFileSync(CONFIG_PATH, 'utf8').replace(/^\uFEFF/, '');   // 兼容记事本写的 BOM
      config = deepMerge(DEFAULT_CONFIG, JSON.parse(raw));
      log('已加载配置: ' + CONFIG_PATH);
    } else {
      log('未找到配置，使用默认配置');
    }
    applyEnv(config);
    // 防呆：参考文本/参考音没配对是"声音又平又假"最常见的原因
    const ttsCfg = config.tts || {};
    if (/替换|这个声音说的那句话|这里填/.test(String(ttsCfg.prompt_text || ''))) {
      log('⚠️ tts.prompt_text 看起来还是占位符，必须改成参考音里逐字一致的那句话，否则声音会很平很假');
    }
    if (!ttsCfg.ref_audio || /替换/.test(String(ttsCfg.ref_audio))) {
      log('⚠️ tts.ref_audio 还没配置（参考音频路径）');
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
  speaking: false,
  playback: null,
  song: { current: null, queue: [], seen: {}, playing: false, skip: false, skipUntil: 0, inflight: 0 }
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
  const persona = (config.persona && config.persona.system) || '你是直播间的小深。';
  const sys = `你是直播间的虚拟主播小深。${persona}
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

function playWavHandle(file) {
  if (process.env.WT_NO_AUDIO === '1') {
    return { promise: Promise.resolve(), kill() {} };
  }
  const code = [
    'import sys, soundfile as sf, sounddevice as sd',
    'y, sr = sf.read(sys.argv[1], dtype="float32")',
    'sd.play(y, sr)',
    'sd.wait()'
  ].join('\n');
  const p = spawn(pythonPath(), ['-c', code, file], { windowsHide: true });
  const promise = new Promise((resolve) => {
    p.on('exit', () => resolve());
    p.on('error', () => resolve());
  });
  return {
    promise,
    kill() { try { p.kill(); } catch (e) {} }
  };
}

function playWav(file) {
  return playWavHandle(file).promise;
}

/** 跑一个 python 子进程，收 stdout/stderr（带超时，超时直接 kill） */
function runPy(args, timeoutMs = 120000, pyExe = null) {
  return new Promise((resolve) => {
    let stdout = '';
    let stderr = '';
    let done = false;
    const p = spawn(pyExe || pythonPath(), args, { windowsHide: true, cwd: ROOT });
    const finish = (code, extra) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve({ code, stdout, stderr: stderr + (extra || '') });
    };
    p.stdout.on('data', (d) => { stdout += d.toString(); });
    p.stderr.on('data', (d) => { stderr += d.toString(); });
    const timer = setTimeout(() => { try { p.kill(); } catch (e) {} finish(-1, '\n[超时]'); }, timeoutMs);
    p.on('close', (code) => finish(code));
    p.on('error', (e) => finish(-1, String(e)));
  });
}

async function ttsSpeak(text) {
  if (!text) return;
  const tts = config.tts || {};
  state.stats.say++;
  pushRecent('say', '小深', text);
  wsBroadcast({ kind: 'speak', text, t: Date.now() });
  state.speaking = true;
  try {
    const r = await httpPostJson((tts.api_url || 'http://127.0.0.1:9881').replace(/\/$/, '') + '/tts', {
      text,
      text_lang: 'zh',
      ref_audio_path: tts.ref_audio,
      prompt_text: tts.prompt_text || '',
      prompt_lang: 'zh',
      top_k: tts.top_k || 30,
      top_p: 1.0,
      temperature: tts.temperature === undefined ? 1.3 : Number(tts.temperature),
      repetition_penalty: tts.repetition_penalty === undefined ? 1.1 : Number(tts.repetition_penalty),
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

// ---------------- 点歌（内嵌，匿名网易云） ----------------
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36';

function httpsForm(url, body, headers = {}, timeout = 20000) {
  return new Promise((resolve, reject) => {
    try {
      const u = new URL(url);
      const data = Buffer.from(body);
      const req = https.request({
        hostname: u.hostname, port: 443, path: u.pathname + u.search, method: 'POST',
        headers: Object.assign({
          'User-Agent': UA, 'Referer': 'https://music.163.com/', 'Content-Type': 'application/x-www-form-urlencoded',
          'Content-Length': data.length
        }, headers), timeout
      }, (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString('utf8') }));
      });
      req.on('error', reject);
      req.on('timeout', () => req.destroy(new Error('timeout')));
      req.write(data);
      req.end();
    } catch (e) { reject(e); }
  });
}

function normSongQuery(q) {
  return String(q || '').toLowerCase().replace(/[\s“”"'《》()（）\[\]【】·,，.。!！?？~～-]/g, '');
}

async function neteaseSearch(kw, limit = 8) {
  const body = 's=' + encodeURIComponent(kw) + '&type=1&limit=' + limit + '&offset=0';
  try {
    const r = await httpsForm('https://music.163.com/api/cloudsearch/pc', body);
    if (r.status !== 200) return [];
    const j = JSON.parse(r.body);
    const songs = (j.result && j.result.songs) || [];
    return songs.map((x) => ({
      id: x.id, name: x.name,
      artist: (x.ar || []).map((a) => a.name).join('、'),
      dur: Math.round(((x.dt || 0) / 1000))
    }));
  } catch (e) { log('网易云搜索失败: ' + e.message); return []; }
}

async function neteaseUrl(id) {
  try {
    const body = 'ids=' + encodeURIComponent(JSON.stringify([id])) + '&br=320000';
    const r = await httpsForm('https://music.163.com/api/song/enhance/player/url', body);
    if (r.status !== 200) return null;
    const j = JSON.parse(r.body);
    for (const it of (j.data || [])) {
      if (it.url && !it.freeTrialInfo) return it.url;
    }
  } catch (e) { log('取播放地址失败: ' + e.message); }
  return null;
}

function downloadFile(url, dest) {
  return new Promise((resolve, reject) => {
    const attempt = (u, redirects = 0) => {
      const lib = u.startsWith('https:') ? https : http;
      lib.get(u, { headers: { 'User-Agent': UA }, timeout: 60000 }, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location && redirects < 4) {
          res.resume();
          return attempt(new URL(res.headers.location, u).toString(), redirects + 1);
        }
        if (res.statusCode !== 200) { res.resume(); return reject(new Error('http ' + res.statusCode)); }
        const ws = fs.createWriteStream(dest);
        res.pipe(ws);
        ws.on('finish', () => ws.close(() => resolve(dest)));
        ws.on('error', reject);
      }).on('error', reject);
    };
    attempt(url);
  });
}

function playMp3(file, durSec) {
  if (process.env.WT_NO_AUDIO === '1') return Promise.resolve();
  return new Promise((resolve) => {
    const ps = [
      '$ErrorActionPreference="SilentlyContinue"',
      '$w = New-Object -ComObject WMPlayer.OCX',
      '$w.URL = "' + file.replace(/\\/g, '\\\\').replace(/"/g, '`"') + '"',
      '$w.controls.play()',
      '$limit = ' + Math.max(15, Math.ceil((durSec || 180) + 15)),
      'for ($i = 0; $i -lt $limit; $i++) { Start-Sleep -Seconds 1; if ($w.playState -eq 1 -or $w.playState -eq 8) { break } }',
      '$w.controls.stop()',
      'try { $w.close() } catch {}'
    ].join('; ');
    const p = spawn('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ps], { windowsHide: true });
    p.on('exit', () => resolve());
    p.on('error', () => resolve());
  });
}

// ---------------- QQ 音乐 ----------------
const QQ_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36';

async function qqSearch(kw, limit = 8) {
  try {
    const q = encodeURIComponent(kw);
    const r = await httpsGetText('https://c.y.qq.com/soso/fcgi-bin/client_search_cp?w=' + q +
      '&format=json&n=' + limit + '&p=1&cr=1&new_json=1', { Referer: 'https://y.qq.com/', 'User-Agent': QQ_UA });
    const j = JSON.parse(r);
    const list = (j.data && j.data.song && j.data.song.list) || [];
    return list.map((x) => ({
      id: x.mid || x.songmid,
      name: x.name || x.songname,
      artist: (x.singer || []).map((a) => a.name).join('、'),
      dur: (x.interval || 0)
    }));
  } catch (e) { log('QQ 搜索失败: ' + e.message); return []; }
}

async function qqUrl(mid) {
  try {
    const body = JSON.stringify({
      req: { module: 'CDN.SrfCdnDispatchServer', method: 'GetCdnDispatch', param: { guid: '3982823384', calltype: 0, userip: '' } },
      req_0: { module: 'vkey.GetVkeyServer', method: 'CgiGetVkey', param: { guid: '3982823384', songmid: [mid], songtype: [0], uin: '0', loginflag: 1, platform: '20' } },
      comm: { uin: 0, format: 'json', ct: 24, cv: 0 }
    });
    const r = await httpsPostRaw('https://u.y.qq.com/cgi-bin/musicu.fcg', body,
      { Referer: 'https://y.qq.com/', 'User-Agent': QQ_UA, 'Content-Type': 'application/json' });
    const j = JSON.parse(r);
    const d = j.req_0 && j.req_0.data;
    if (!d) return null;
    const purl = (d.midurlinfo && d.midurlinfo[0] && d.midurlinfo[0].purl) || '';
    if (!purl) return null;
    const sip = (d.sip && d.sip[0]) || 'http://ws.stream.qqmusic.qq.com/';
    return sip + purl;
  } catch (e) { log('QQ 取地址失败: ' + e.message); return null; }
}

// ---------------- 通用 GET/POST 文本 ----------------
function httpsGetText(url, headers = {}, timeout = 20000) {
  return new Promise((resolve, reject) => {
    try {
      const u = new URL(url);
      const lib = u.protocol === 'https:' ? https : http;
      lib.get({ hostname: u.hostname, port: u.port || (u.protocol === 'https:' ? 443 : 80), path: u.pathname + u.search,
        headers: Object.assign({ 'User-Agent': UA }, headers), timeout }, (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
      }).on('error', reject).on('timeout', function () { this.destroy(new Error('timeout')); });
    } catch (e) { reject(e); }
  });
}

function httpsPostRaw(url, body, headers = {}, timeout = 20000) {
  return new Promise((resolve, reject) => {
    try {
      const u = new URL(url);
      const data = Buffer.from(body);
      const req = https.request({ hostname: u.hostname, port: 443, path: u.pathname + u.search, method: 'POST',
        headers: Object.assign({ 'User-Agent': UA, 'Content-Length': data.length }, headers), timeout }, (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
      });
      req.on('error', reject);
      req.on('timeout', () => req.destroy(new Error('timeout')));
      req.write(data);
      req.end();
    } catch (e) { reject(e); }
  });
}

// ---------------- 多源调度 ----------------
const SOURCES = {
  netease: { name: '网易云', search: neteaseSearch, url: (it) => neteaseUrl(it.id) },
  qq: { name: 'QQ音乐', search: qqSearch, url: (it) => qqUrl(it.id) }
};
const SOURCE_LABEL = { netease: '网易云', qq: 'QQ音乐', kugou: '酷狗', qishui: '汽水音乐' };

async function findPlayable(query) {
  const order = ((config.songs || {}).sources || ['netease', 'qq']).filter((k) => SOURCES[k]);
  if (!order.length) return null;
  for (const key of order) {
    const src = SOURCES[key];
    const list = await src.search(query);
    for (const it of list.slice(0, 4)) {
      const u = await src.url(it);
      if (u) return { source: key, sourceName: src.name, song: it, url: u };
    }
    log(`音源 ${src.name} 未命中《${query}》，尝试下一个`);
  }
  return null;
}

async function songQueueLoop() {
  if (state.song.playing) return;
  state.song.playing = true;
  try {
    while (state.song.queue.length) {
      const item = state.song.queue[0];
      state.song.current = { name: item.song.name, artist: item.song.artist, by: item.by, source: item.source || 'netease' };
      wsBroadcast({ kind: 'song', action: 'play', current: state.song.current, t: Date.now() });
      log(`开始播放: 《${item.song.name}》- ${item.song.artist}`);
      await enqueueSpeech(`下面播放${item.by}点的《${item.song.name}》。`);
      const file = path.join(os.tmpdir(), 'wt_song_' + item.song.id + '.mp3');
      try {
        if (!fs.existsSync(file) || fs.statSync(file).size < 10000) {
          const u = item.url || await neteaseUrl(item.song.id);
          if (!u) throw new Error('无播放地址');
          await downloadFile(u, file);
        }
        await playMp3(file, item.song.dur);
      } catch (e) {
        log('播放失败: ' + e.message);
      } finally {
        try { fs.unlinkSync(file); } catch (e) {}
        state.song.queue.shift();
        state.song.current = null;
        wsBroadcast({ kind: 'song', action: 'next', t: Date.now() });
      }
    }
  } finally {
    state.song.playing = false;
  }
}

async function requestSong(name, query) {
  const cfgSongs = config.songs || {};
  if (cfgSongs.enabled === false) return { ok: false, error: '点歌已关闭' };
  const nq = normSongQuery(query);
  if (!nq) return { ok: false, error: '空歌名' };
  if (cfgSongs.dedupe !== false && state.song.seen[nq]) {
    log('重复点歌跳过: ' + query);
    return { ok: false, error: '本场已点过' };
  }
  if (state.song.queue.length >= (cfgSongs.max_queue || 20)) {
    await enqueueSpeech(`${name}，歌单排满啦！`);
    return { ok: false, error: '队列已满' };
  }
  const found = await findPlayable(query);
  if (!found) {
    await enqueueSpeech(`抱歉${name}，没找到能播的《${query}》。`);
    return { ok: false, error: '所有音源都没找到' };
  }
  const chosen = found.song;
  chosen.source = found.source;
  state.song.seen[nq] = name;
  const pos = state.song.queue.length + 1;
  state.song.queue.push({ by: name, song: chosen, url: found.url, query, source: found.source });
  log(`点歌入队: ${name} -> 《${chosen.name}》[${found.sourceName}] 第${pos}首`);
  await enqueueSpeech(`收到${name}点的《${chosen.name}》，排在第${pos}首！`);
  wsBroadcast({ kind: 'song', action: 'queued', queue: state.song.queue.length, t: Date.now() });
  songQueueLoop().catch(() => {});
  return { ok: true, position: pos, song: chosen };
}

// ---------------- 清唱（唱歌 = 用 TTS 合成清唱，不播原曲） ----------------
const SING_CREDIT_RE = /(作词|作曲|编曲|制作人|制作|混音|母带|录音|出品|监制|发行|演唱|原唱|翻唱|录音室|吉他|贝斯|鼓|键盘|弦乐|和声|统筹|企划|策划|宣发|版权|OP|SP|℗|©|ISRC|LRC|词：|曲：)/i;

function cleanLyricLine(line) {
  let s = String(line || '').replace(/^\[[^\]]*\]/g, '').trim();   // LRC 时间标签
  s = s.replace(/^\d+[.、]\s*/, '').trim();                        // 纯文本歌词的行号
  if (!s) return '';
  if (SING_CREDIT_RE.test(s)) return '';                           // 作词/作曲等制作信息
  if (!/[\u4e00-\u9fa5]/.test(s)) return '';                       // 没有中文（纯英文制作信息/logo）
  if (s.length < 3) return '';
  if (s.length > 24) {
    const parts = s.split(/[，,。.！!？?]/).map((x) => x.trim()).filter((x) => x.length >= 3);
    if (!parts.length) return '';
    s = parts[0];
  }
  return s;
}

/** 从整首歌词里挑 N 句：偏向副歌（后半段），控制总字数，去掉相邻重复 */
function pickLyricLines(rawLines, maxLines, maxChars) {
  const lines = [];
  for (const raw of rawLines) {
    const s = cleanLyricLine(raw);
    if (!s) continue;
    if (lines[lines.length - 1] === s) continue;
    lines.push(s);
  }
  if (!lines.length) return [];
  maxLines = Math.max(1, Math.min(12, Number(maxLines) || 6));
  maxChars = Math.max(20, Number(maxChars) || 100);
  const start = Math.max(0, Math.floor(lines.length * 0.4) - 1);
  const take = (from) => {
    const out = [];
    let chars = 0;
    for (let i = from; i < lines.length && out.length < maxLines; i++) {
      if (chars + lines[i].length > maxChars) break;
      out.push(lines[i]);
      chars += lines[i].length;
    }
    return out;
  };
  const picked = take(start);
  return picked.length ? picked : take(0);
}

async function neteaseLyric(id) {
  try {
    const body = 'os=pc&id=' + encodeURIComponent(id) + '&lv=-1&kv=-1&tv=-1';
    const r = await httpsForm('https://music.163.com/api/song/lyric?' + body, '');
    if (r.status !== 200) return [];
    const j = JSON.parse(r.body);
    const raw = (j.lrc && j.lrc.lyric) || '';
    return raw.split(/\r?\n/).map(cleanLyricLine).filter(Boolean);
  } catch (e) {
    log('取歌词失败: ' + e.message);
    return [];
  }
}

/** 用网易云搜一下，拿到标准歌名/歌手（清唱不需要播放地址，搜不到也能唱） */
async function searchSongMeta(query) {
  try {
    const list = await neteaseSearch(query, 6);
    if (!list.length) return null;
    const nq = normSongQuery(query);
    return list.find((x) => normSongQuery(x.name) === nq) || list[0];
  } catch (e) {
    return null;
  }
}

/** 抓不到歌词时：让 AI 现编几句原创歌词（不用受版权保护的歌词） */
async function aiSingLyrics(title, maxLines) {
  const fallback = [
    '今天想唱一首《' + title + '》',
    '没有伴奏也没关系',
    '清唱一段送给你',
    '希望你喜欢呀'
  ];
  const ai = config.ai || {};
  if (!ai.openai_key) return fallback;
  try {
    const r = await httpPostJson((ai.openai_base || 'https://api.deepseek.com/v1').replace(/\/$/, '') + '/chat/completions', {
      model: ai.openai_model || 'deepseek-chat',
      messages: [
        { role: 'system', content: '你是虚拟主播小深，会把歌名编成几句简单好唱的清唱歌词。' },
        { role: 'user', content: `为歌名《${title}》写 ${maxLines || 4} 句原创中文歌词，每句 6-12 个字，每句单独一行；不要用任何现有歌曲的歌词，不要序号、不要解释、不要引号和特殊符号。` }
      ],
      max_tokens: 220,
      temperature: 1.0
    }, { Authorization: 'Bearer ' + ai.openai_key }, 30000);
    if (r.status !== 200) return fallback;
    const j = JSON.parse(r.body.toString('utf8'));
    const text = (j.choices && j.choices[0] && j.choices[0].message && j.choices[0].message.content) || '';
    const lines = text.split(/\r?\n/).map((x) => x.replace(/^\s*\d+[.、)]?\s*/, '').replace(/[“”"'《》]/g, '').trim())
      .filter((x) => x.length >= 4 && /[\u4e00-\u9fa5]/.test(x)).slice(0, maxLines || 4);
    return lines.length >= 2 ? lines : fallback;
  } catch (e) {
    log('AI 编词失败: ' + e.message);
    return fallback;
  }
}

function pickSingTitle() {
  const list = ((config.sing || {}).pick_list || []).filter(Boolean);
  if (!list.length) return '小星星';
  return list[Math.floor(Math.random() * list.length)];
}

/** 清唱任务串到同一条音频队列上：不会和其它说话/清唱抢喇叭 */
function enqueueSing(item) {
  const task = () => performSing(item)
    .catch((e) => log('清唱异常: ' + e.message))
    .finally(() => {
      state.song.current = null;
      state.playback = null;
    });
  state.ttsQueue = state.ttsQueue.then(task).catch(() => {});
  return state.ttsQueue;
}

/** 没有 sing_tts.py 或合成失败时的兜底：逐句念唱（节奏感比唱歌弱，但不会开天窗） */
async function singFallbackLines(item) {
  for (let i = 0; i < item.lyrics.length; i++) {
    if (state.song.skip || !state.song.current) break;
    state.song.current.lyricIndex = i;
    wsBroadcast({ kind: 'sing', action: 'line', idx: i, text: item.lyrics[i], t: Date.now() });
    await ttsSpeak(item.lyrics[i]);
  }
}

function pruneSingFiles() {
  try {
    const dir = path.join(LOG_DIR, 'sing');
    for (const ext of ['.wav', '.timing.json', '.lines.json']) {
      const files = fs.readdirSync(dir)
        .filter((f) => f.endsWith(ext))
        .map((f) => ({ f, t: fs.statSync(path.join(dir, f)).mtimeMs }))
        .sort((a, b) => b.t - a.t);
      for (const x of files.slice(20)) { try { fs.unlinkSync(path.join(dir, x.f)); } catch (e) {} }
    }
  } catch (e) {}
}

/** 真正的合成：逐句 TTS + 旋律后处理 + 串接（在 requestSing 时就后台跑，轮到它时通常已经备好） */
/** 用某个 venv 的 python 读一下 wav 时长（秒） */
function probeWavDuration(pyExe, file) {
  return new Promise((resolve) => {
    const code = 'import sys,soundfile as sf;d,sr=sf.read(sys.argv[1]);print(len(d)/max(sr,1))';
    let out = '';
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    try {
      const p = spawn(pyExe, ['-c', code, file], { windowsHide: true });
      const timer = setTimeout(() => { try { p.kill(); } catch (e) {} finish(0); }, 30000);
      p.stdout.on('data', (d) => { out += d.toString(); });
      p.on('exit', () => { clearTimeout(timer); finish(Number(out.trim()) || 0); });
      p.on('error', () => { clearTimeout(timer); finish(0); });
    } catch (e) { finish(0); }
  });
}

/**
 * 真唱管线（可插拔）：
 *   ACE-Step 生成 a cappella 纯人声清唱 → Seed-VC 换成主播音色 → wav
 * 任一环节失败返回 null，调用方回退到 TTS 清唱。
 */
function aceSettings() {
  const cfg = (config.sing || {}).ace || {};
  return {
    python: String(cfg.python || 'D:/sing_gpu/venv/Scripts/python.exe'),
    script: String(cfg.script || 'D:/sing_gpu/ace_sing.py'),
    svcPython: String(cfg.svc_python || 'D:/seedvc/venv/Scripts/python.exe'),
    svcScript: String(cfg.svc_script || 'D:/seedvc/convert.py'),
    duration: Math.max(8, Math.min(60, Number(cfg.duration) || 22)),
    cfgRate: Number(cfg.cfg === undefined ? 0.75 : cfg.cfg),
    steps: Math.max(10, Math.min(100, Number(cfg.steps) || 30)),
    timeoutMs: Math.max(60000, Number(cfg.timeout_sec || 600) * 1000),
    tags: Array.isArray(cfg.tags) ? cfg.tags.filter(Boolean) : [String(cfg.tags || 'female solo vocal, a cappella, no instruments, pure vocal, cute, clear diction, mandarin, 80 BPM')]
  };
}

/** 每次随机挑一组风格 tags，配合随机 seed，让"唱法/旋律"不固定 */
function pickAceTags(ace) {
  const list = ace.tags;
  return list[Math.floor(Math.random() * list.length)];
}

function buildAceTiming(lines, duration) {
  const total = Math.max(5, Number(duration) || 20);
  const head = 0.3;
  const tail = 0.4;
  const gap = 0.15;
  const weights = lines.map((l) => Math.max(1, String(l).length));
  const sum = weights.reduce((a, b) => a + b, 0) || 1;
  const usable = Math.max(2, total - head - tail - gap * Math.max(0, lines.length - 1));
  let t = head;
  const outLines = lines.map((text, i) => {
    const d = (usable * weights[i]) / sum;
    const line = { i, text, start: Number(t.toFixed(3)), dur: Number(d.toFixed(3)) };
    t += d + gap;
    return line;
  });
  return { ok: true, sr: 32000, duration: total, mode: 'ace', engine: 'ace', lines: outLines };
}

async function aceGenerate(lyrics, outWav, tags, seed) {
  const ace = aceSettings();
  const linesPath = outWav.replace(/\.wav$/, '.lyrics.json');
  const jsonPath = outWav.replace(/\.wav$/, '.ace.json');
  const script = ace.script;
  if (!fs.existsSync(script)) { log('真唱：找不到 ACE-Step 脚本 ' + script); return null; }
  fs.writeFileSync(linesPath, JSON.stringify(lyrics), 'utf8');
  try { fs.unlinkSync(jsonPath); } catch (e) {}
  const args = [
    script,
    '--lyrics-file', linesPath,
    '--tags', tags,
    '--duration', String(ace.duration),
    '--seed', String(seed),
    '--out', outWav,
    '--json-out', jsonPath
  ];
  const t0 = Date.now();
  const r = await runPy(args, ace.timeoutMs, ace.python);
  log(`真唱 ACE-Step 结束 code=${r.code} 用时 ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  if (r.stderr && r.code !== 0) log('ACE-Step stderr: ' + r.stderr.trim().split('\n').slice(-5).join(' | '));
  let j = null;
  try { if (fs.existsSync(jsonPath)) j = JSON.parse(fs.readFileSync(jsonPath, 'utf8')); } catch (e) {}
  if (r.code !== 0 || !fs.existsSync(outWav) || (j && j.ok === false)) {
    log('真唱：ACE-Step 生成失败');
    return null;
  }
  return { outWav, duration: Number((j && j.duration) || 0) || ace.duration };
}

async function aceConvert(srcWav, outWav) {
  const ace = aceSettings();
  const tts = config.tts || {};
  const jsonPath = outWav.replace(/\.wav$/, '.svc.json');
  if (!fs.existsSync(ace.svcScript)) { log('真唱：找不到 Seed-VC 脚本 ' + ace.svcScript); return null; }
  try { fs.unlinkSync(jsonPath); } catch (e) {}
  const args = [
    ace.svcScript,
    '--source', srcWav,
    '--ref', String(tts.ref_audio || ''),
    '--out', outWav,
    '--cfg', String(ace.cfgRate),
    '--steps', String(ace.steps),
    '--json-out', jsonPath
  ];
  const t0 = Date.now();
  const r = await runPy(args, ace.timeoutMs, ace.svcPython);
  log(`真唱 Seed-VC 结束 code=${r.code} 用时 ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  if (r.stderr && r.code !== 0) log('Seed-VC stderr: ' + r.stderr.trim().split('\n').slice(-5).join(' | '));
  if (r.code !== 0 || !fs.existsSync(outWav)) {
    log('真唱：Seed-VC 换声失败');
    return null;
  }
  return { outWav };
}

async function prepareAce(item) {
  const dir = path.join(LOG_DIR, 'sing');
  fs.mkdirSync(dir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
  const safe = String(item.song.name).replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 24) || 'song';
  const aceWav = path.join(dir, `${stamp}_${safe}.ace.wav`);
  const outWav = path.join(dir, `${stamp}_${safe}.wav`);
  const ace = aceSettings();
  const tags = pickAceTags(ace);
  const seed = Math.floor(Math.random() * 999999);
  log(`真唱生成开始: 《${item.song.name}》 tags="${tags}" seed=${seed}`);
  const gen = await aceGenerate(item.lyrics, aceWav, tags, seed);
  if (!gen) return null;
  const conv = await aceConvert(aceWav, outWav);
  if (!conv) return null;
  const dur = (await probeWavDuration(ace.svcPython, outWav)) || gen.duration;
  const timing = buildAceTiming(item.lyrics, dur);
  log(`真唱生成完成: 《${item.song.name}》 ${dur.toFixed(1)}s`);
  return { outWav, timing, engine: 'ace' };
}

async function doPrep(item) {
  const cfg = config.sing || {};
  const tts = config.tts || {};
  // 真唱引擎（ACE-Step + Seed-VC）：失败自动回落到下面的 TTS 清唱
  if (String(cfg.engine || 'tts') === 'ace') {
    const r = await prepareAce(item).catch((e) => { log('真唱管线异常: ' + e.message); return null; });
    if (r) return r;
    log('真唱管线失败，回退 TTS 清唱');
  }
  const script = path.join(ROOT, 'extras', 'python', 'sing_tts.py');
  if (!fs.existsSync(script)) return null;
  const dir = path.join(LOG_DIR, 'sing');
  fs.mkdirSync(dir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
  const safe = String(item.song.name).replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 24) || 'song';
  const outWav = path.join(dir, `${stamp}_${safe}.wav`);
  const timingPath = outWav.replace(/\.wav$/, '.timing.json');
  const linesPath = outWav.replace(/\.wav$/, '.lines.json');
  fs.writeFileSync(linesPath, JSON.stringify(item.lyrics), 'utf8');
  const args = [
    script,
    '--lines-file', linesPath,
    '--out', outWav,
    '--timing-out', timingPath,
    '--tts-url', String(tts.api_url || 'http://127.0.0.1:9881'),
    '--ref-audio', String(tts.ref_audio || ''),
    '--prompt-text', String(tts.prompt_text || ''),
    '--speed', String(tts.speed || 0.95),
    '--top-k', String(tts.top_k || 30),
    '--temperature', String(tts.temperature === undefined ? 1.3 : tts.temperature),
    '--repetition-penalty', String(tts.repetition_penalty === undefined ? 1.1 : tts.repetition_penalty),
    '--sample-steps', String(tts.sample_steps || 32),
    '--mode', String(cfg.mode || 'melody'),
    '--melody', String(cfg.melody || 'auto'),
    '--bpm', String(cfg.bpm || 92),
    '--vibrato', cfg.vibrato === false ? '0' : '1',
    '--gap-ms', String(cfg.gap_ms || 380),
    '--seed', String(Math.floor(Math.random() * 99999))
  ];
  // melody 模式：默认走「唱腔化 v2」（元音按节拍拉伸 + legato + 轻颤音，最像在唱）；也可关掉退回自然旋律
  if (String(cfg.mode || 'melody') !== 'read') {
    if (cfg.v2 !== false) {
      args.push(
        '--sing-v2',
        '--beat-scale', String(Number(cfg.beat_scale) || 0.75),
        '--tail-beats', '1.8',
        '--note-fade-db', '-2.0',
        '--tail-stretch', '1.15',
        '--tail-fall', '0.3',
        '--blend', '0.55',
        '--blend-mode', 'note',
        '--vibrato', '0.6',
        '--quantize-ms', '0'
      );
    } else if (cfg.natural !== false) {
      args.push('--natural');
    }
  }
  log(`清唱合成开始: 《${item.song.name}》 ${String(cfg.mode || 'melody')} / ${String(cfg.melody || 'auto')}`);
  const t0 = Date.now();
  const r = await runPy(args, 300000);
  log(`清唱合成结束 code=${r.code} 用时 ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  if (r.stderr && r.code !== 0) log('清唱 python stderr: ' + r.stderr.trim().split('\n').slice(-6).join(' | '));
  let timing = null;
  try { if (fs.existsSync(timingPath)) timing = JSON.parse(fs.readFileSync(timingPath, 'utf8')); } catch (e) {}
  if (!timing || timing.ok === false || !timing.lines || !timing.lines.length || !fs.existsSync(outWav)) {
    log('清唱合成失败（将在轮到它时逐句念唱兜底）');
    try { fs.unlinkSync(outWav); } catch (e) {}
    return null;
  }
  return { outWav, timing };
}

/** 合成排队（串行，避免多首同时压 GPU/TTS）；返回的 Promise 给 performSing 用 */
function prepareSing(item) {
  if (!state.singPrep) state.singPrep = Promise.resolve();
  const p = state.singPrep.then(() => doPrep(item).catch((e) => { log('清唱预处理异常: ' + e.message); return null; }));
  state.singPrep = p.then(() => {}, () => {});
  return p;
}

async function performSing(item) {
  const cfg = config.sing || {};
  const song = item.song;
  state.song.skip = false;
  // 先从排队里拿出来（取消分支也要摘掉，避免幽灵队列项）
  const qi = state.song.queue.indexOf(item);
  if (qi >= 0) state.song.queue.splice(qi, 1);
  if (item.skipped || Date.now() < (state.song.skipUntil || 0)) {   // 排队期间/点歌刚发出时就被「跳过」了
    state.song.skipUntil = 0;
    log(`清唱已取消（排队时跳过）: 《${song.name}》`);
    wsBroadcast({ kind: 'sing', action: 'end', t: Date.now() });
    return;
  }
  state.song.current = {
    name: song.name, artist: song.artist, by: item.by, source: item.source,
    lyrics: item.lyrics.slice(), lyricIndex: 0, audio: null, duration: 0, t: Date.now()
  };
  wsBroadcast({ kind: 'sing', action: 'start', title: song.name, artist: song.artist, by: item.by, lyrics: item.lyrics, t: Date.now() });
  log(`开始清唱: 《${song.name}》- ${item.by} 点 | 模式 ${cfg.mode || 'melody'} | 歌词${item.lyrics.length}句`);

  // 合成大概率在 requestSing 时就跑过/正在跑；先报幕，别让直播间干等
  let prepDone = false;
  const prepP = Promise.resolve(item.prep || prepareSing(item)).then((x) => { prepDone = true; return x; });
  if (cfg.intro !== false) await ttsSpeak(`一首《${song.name}》，清唱版，献丑啦～`);
  if (state.song.skip) { state.song.skip = false; wsBroadcast({ kind: 'sing', action: 'end', t: Date.now() }); return; }
  // 还没合成完就垫一句，避免十几秒静音（合成慢就循环垫，最多 2 次）
  const fillerText = String((cfg.filler === false ? '' : cfg.filler) || '我清清嗓子，准备开唱啦～歌词有点长，我再看一眼～马上就好，再等我一下下～');
  // 真唱管线要 1~2 分钟，垫话次数按引擎放宽
  const fillerMax = String(cfg.engine || 'tts') === 'ace' ? 7 : 2;
  for (let i = 0; i < fillerMax && !prepDone && fillerText; i++) {
    await ttsSpeak(fillerText);
    if (state.song.skip) { state.song.skip = false; wsBroadcast({ kind: 'sing', action: 'end', t: Date.now() }); return; }
  }

  const prep = await prepP;
  // 合成等待期间点了跳过：合成出来也别唱了
  if (state.song.skip) { state.song.skip = false; wsBroadcast({ kind: 'sing', action: 'end', t: Date.now() }); return; }
  const timing = prep && prep.timing;
  const outWav = prep && prep.outWav;
  if (timing && timing.lines && timing.lines.length && outWav) {
    state.song.current.audio = path.relative(ROOT, outWav).replace(/\\/g, '/');
    state.song.current.duration = Number(timing.duration) || 0;
    const handle = playWavHandle(outWav);
    state.playback = handle;
    const timers = [];
    for (const l of timing.lines) {
      const ms = Math.max(0, Math.round((Number(l.start) || 0) * 1000));
      timers.push(setTimeout(() => {
        if (!state.song.current) return;
        state.song.current.lyricIndex = Number(l.i) || 0;
        wsBroadcast({ kind: 'sing', action: 'line', idx: Number(l.i) || 0, text: String(l.text || ''), dur: Number(l.dur) || 0, t: Date.now() });
      }, ms));
    }
    await handle.promise;
    // 测试模式（WT_NO_AUDIO=1）不真的放音，但仍按 realtime 走一遍时间轴，方便验证歌词事件
    if (process.env.WT_NO_AUDIO === '1' && Number(timing.duration) > 0) {
      await new Promise((r) => setTimeout(r, Math.round(Number(timing.duration) * 1000)));
    }
    timers.forEach((t) => clearTimeout(t));
    state.playback = null;
    wsBroadcast({ kind: 'sing', action: 'end', t: Date.now() });
    log(`清唱完成: 《${song.name}》 ${(Number(timing.duration) || 0).toFixed(1)}s`);
    if (cfg.outro !== false && !state.song.skip) await ttsSpeak(`唱完啦，谢谢${item.by}点的歌～`);
    state.song.skip = false;
    pruneSingFiles();
    return;
  }

  // 兜底：逐句念唱
  log(`《${song.name}》走逐句念唱兜底`);
  await singFallbackLines(item);
  wsBroadcast({ kind: 'sing', action: 'end', t: Date.now() });
  state.song.skip = false;
}

async function requestSing(name, query) {
  state.song.inflight = (state.song.inflight || 0) + 1;
  try {
    return await doRequestSing(name, query);
  } finally {
    state.song.inflight = Math.max(0, (state.song.inflight || 1) - 1);
  }
}

async function doRequestSing(name, query) {
  const cfg = config.sing || {};
  const songsCfg = config.songs || {};
  if (songsCfg.enabled === false || cfg.enabled === false) {
    return { ok: false, error: '清唱已关闭' };
  }
  let q = String(query || '').trim().replace(/[《》]/g, '');
  if (!q) q = pickSingTitle();
  const nq = normSongQuery(q);
  if (!nq) return { ok: false, error: '空歌名' };
  if (songsCfg.dedupe !== false && state.song.seen[nq]) {
    log('重复点歌跳过（本场已唱）: ' + q);
    return { ok: false, error: '本场已唱过' };
  }
  if (state.song.queue.length >= (songsCfg.max_queue || 20)) {
    enqueueSpeech(`${name}，清唱排队排满啦，等会儿再点好不好～`).catch(() => {});
    return { ok: false, error: '队列已满' };
  }

  const meta = await searchSongMeta(q);
  const title = (meta && meta.name) || q;
  const artist = (meta && meta.artist) || '';
  let lyrics = [];
  let lyricSource = 'none';
  if (cfg.lyrics !== 'ai' && meta) {
    lyrics = pickLyricLines(await neteaseLyric(meta.id), cfg.max_lines, cfg.max_chars);
    if (lyrics.length) lyricSource = 'netease';
  }
  if (!lyrics.length) {
    lyrics = await aiSingLyrics(title, cfg.max_lines);
    lyricSource = 'ai';
  }
  if (!lyrics.length) {
    enqueueSpeech(`抱歉${name}，这首歌的歌词没找到。`).catch(() => {});
    return { ok: false, error: '没拿到歌词' };
  }

  state.song.seen[nq] = name;
  const item = { by: name, song: { name: title, artist, dur: 0 }, query: q, source: lyricSource, lyrics };
  item.prep = prepareSing(item);          // 现在就开始合成；排队期间备好，轮到就能开唱
  state.song.queue.push(item);
  const pos = state.song.queue.length;
  log(`清唱入队: ${name} -> 《${title}》[歌词:${lyricSource}] 第${pos}首`);
  wsBroadcast({ kind: 'song', action: 'queued', queue: state.song.queue.length, t: Date.now() });
  // 语音回执不阻塞接口返回（前面可能还有一首正在清唱）
  enqueueSpeech(`${name}，点好啦！《${title}》清唱排第${pos}个～`).catch(() => {});
  enqueueSing(item);
  return { ok: true, position: pos, song: { name: title, artist } };
}

function skipSong() {
  // 还在排队（报幕/等待中，current 未建立）：直接把下一首从队列里去掉
  if (!state.song.current && state.song.queue.length) {
    const dropped = state.song.queue.shift();
    dropped.skipped = true;
    log(`跳过排队中的清唱: 《${dropped.song.name}》`);
    return { ok: true, skipped: dropped.song.name };
  }
  if (state.song.current) {
    state.song.skip = true;
    if (state.playback) {
      try { state.playback.kill(); } catch (e) {}
      state.playback = null;
    }
    log('清唱跳过');
    return { ok: true };
  }
  // 什么都没在唱（比如点歌刚发出、还在查歌词）：3 秒内到达的第一首直接取消
  if (state.song.inflight > 0) {
    state.song.skipUntil = Date.now() + 3000;
    log('清唱跳过（3 秒内新开始的清唱会取消）');
  } else {
    log('清唱跳过：当前没有在唱的歌');
  }
  return { ok: true };
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
  // 过滤词（弹幕/昵称命中就丢弃，避免刷屏与假礼物文案）
  const block = ((config.filter || {}).blocklist || []).filter(Boolean);
  if (ev.type === 'danmaku' && ev.text && block.some((w) => String(ev.text).includes(w))) {
    log('过滤弹幕: ' + ev.text);
    return;
  }
  state.stats[ev.type] = (state.stats[ev.type] || 0) + 1;
  pushRecent('event', ev.name, ev.text || ev.gift || '');
  wsBroadcast({ kind: 'event', event: ev, t: now });

  const cfgSongs = config.songs || {};
  const singMode = cfgSongs.perform !== 'play';   // 默认：唱歌 = 清唱（TTS 合成，不播原曲）

  // 点歌 / 唱歌 / 清唱：弹幕命令优先处理（在固定回复之前）
  if (ev.type === 'danmaku' && ev.text) {
    const text = String(ev.text).trim();
    let query = null;
    let m = text.match(/^[/／]?\s*(?:点歌|點歌|清唱)\s*[:：]?\s*(.*?)\s*$/);
    if (m) query = m[1];
    if (query === null) {
      // 「唱歌」类：必须用冒号/空格分隔歌名，避免「唱歌比赛开始了吗」这类闲聊被当成点歌
      m = text.match(/^[/／]?\s*(?:唱歌|唱一首|唱一个|来一首|来首|唱)\s*[:：]\s*(.*?)\s*$/);
      if (m) query = m[1];
    }
    if (query === null) {
      m = text.match(/^[/／]?\s*(?:唱歌|唱一首|唱一个|来一首|来首)\s+(.+?)\s*$/);
      if (m) query = m[1];
    }
    if (query === null) {
      m = text.match(/^[/／]?\s*唱\s+(.+?)\s*$/);
      if (m) query = m[1];
    }
    if (query === null) {
      m = text.match(/^[/／]?\s*(?:点歌|點歌|清唱|唱歌|唱一首|唱一个|来一首|来首|唱)\s*$/);
      if (m) query = '';
    }
    if (query !== null) {
      query = String(query).replace(/^[《「"'（(]/, '').replace(/[》」"'）)]$/, '').trim();
      if (singMode) {
        requestSing(ev.name, query).then((r) => {
          if (!r || r.ok !== false) return;
          // 已经由 requestSing 语音提示过的不重复说
          const short = {
            '本场已唱过': `${ev.name}，这首本场唱过啦，换一首吧～`,
            '清唱已关闭': '清唱暂时关掉啦，等会儿再唱～',
            '空歌名': '想听哪首呀？说个歌名给我～'
          }[r.error];
          if (short) enqueueSpeech(short).catch(() => {});
        }).catch((e) => log('清唱异常: ' + e.message));
        return;
      }
      // 旧行为：播放原曲
      if (cfgSongs.internal !== false) {
        requestSong(ev.name, query).catch((e) => log('点歌异常: ' + e.message));
        return;
      }
      const file = path.join(ROOT, 'tools', 'logs', cfgSongs.request_file || 'song_requests.jsonl');
      try {
        fs.mkdirSync(path.dirname(file), { recursive: true });
        fs.appendFileSync(file, JSON.stringify({ t: new Date().toTimeString().slice(0, 8), name: ev.name, query }) + '\n');
        log('点歌转交: ' + ev.name + ' -> ' + query);
        enqueueSpeech(`收到${ev.name}点的歌，去排队啦！`).catch(() => {});
      } catch (e) { log('点歌写入失败: ' + e.message); }
      return;
    }
    // 跳过当前清唱 / 切歌
    if (/^[/／]?\s*(?:跳过|切歌|别唱了|不唱了|stop|skip)\s*$/i.test(text)) {
      if (state.song.current || state.song.queue.length || state.song.inflight > 0) {
        skipSong();
        enqueueSpeech(`好，那这首就唱到这儿～`).catch(() => {});
        return;
      }
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
  if (method === 'GET' && p === '/stats') return serveFile(res, 'stats.html');
  if (method === 'GET' && p === '/subtitle') return serveFile(res, 'subtitle.html');
  if (method === 'GET' && p === '/console') return serveFile(res, 'console.html');
  if (method === 'GET' && p === '/live2d') return serveFile(res, 'live2d.html');
  if (method === 'GET' && p === '/stardew') return serveFile(res, 'stardew.html');
  if (p === '/api/stardew') {
    try {
      const bridge = 'E:\\steam\\steamapps\\common\\Stardew Valley\\Mods\\StardewMCPBridge\\bridge_data.json';
      const guardLog = path.join(ROOT, 'extras', 'python', 'logs', 'guard.log');
      const st = fs.statSync(bridge);
      const b = JSON.parse(fs.readFileSync(bridge, 'utf8'));
      const age = (Date.now() - st.mtimeMs) / 1000;
      let guardLines = [];
      let guardFresh = false;
      try {
        const gs = fs.statSync(guardLog);
        guardFresh = (Date.now() - gs.mtimeMs) < 7 * 60 * 1000;
        guardLines = fs.readFileSync(guardLog, 'utf8').trim().split('\n').slice(-5).map(l => l.replace(/^\[[^\]]+\]\s*/, ''));
      } catch (e) {}
      return json(res, {
        ok: true, age, guardFresh, guardLines,
        location: b.location, day: b.day, time: b.time,
        money: b.player && b.player.money,
        tile: b.player && b.player.tile,
        tool: b.player && b.player.currentTool,
        stamina: b.player && b.player.stamina,
        health: b.player && b.player.health,
        fishing: b.player && b.player.fishing ? {
          inUse: b.player.fishing.inUse, nibbling: b.player.fishing.nibbling, minigame: b.player.fishing.minigame
        } : null,
        menu: b.player && b.player.fishing && b.player.fishing.activeMenu
      });
    } catch (e) {
      return json(res, { ok: false, error: String(e) });
    }
  }
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
  if (p === '/api/obs/probe') {
    const body = await readBody(req);
    return json(res, await obsProbe(body || {}));
  }
  if (p === '/api/obs/setup') {
    const body = await readBody(req);
    return json(res, await obsSetup(body || {}));
  }
  if (p === '/api/songs/request') {
    const body = await readBody(req);
    const name = body.name || '手动点歌';
    const query = body.query || body.song || '';
    const r = ((config.songs || {}).perform === 'play')
      ? await requestSong(name, query)
      : await requestSing(name, query);
    return json(res, r, r.ok ? 200 : 400);
  }
  if (p === '/api/songs/skip') {
    return json(res, skipSong());
  }
  if (p === '/api/stats') return json(res, state.stats);
  if (p === '/api/recent') return json(res, state.recent);
  if (p === '/api/songs/state') {
    const cfgSongs2 = config.songs || {};
    const cfgSing = config.sing || {};
    const singMode = cfgSongs2.perform !== 'play';
    if (singMode) {
      return json(res, {
        mode: 'sing',
        sing: {
          enabled: cfgSing.enabled !== false && cfgSongs2.enabled !== false,
          style: cfgSing.mode || 'melody',
          lyrics: cfgSing.lyrics || 'netease'
        },
        playing: !!state.song.current,
        current: state.song.current,
        queue: state.song.queue.map((x) => ({ name: x.song.name, by: x.by }))
      });
    }
    if (cfgSongs2.internal !== false) {
      return json(res, {
        mode: 'play',
        current: state.song.current,
        playing: state.song.playing,
        sources: (cfgSongs2.sources || ['netease', 'qq']),
        queue: state.song.queue.map((x) => ({ name: x.song.name, by: x.by }))
      });
    }
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
  port = resolvePort(port);
  LOG_TAG = '[p' + port + ']';
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
    state.port = port;
  log(`鲸live服务已启动: http://127.0.0.1:${port}`);
    log(`面板 http://127.0.0.1:${port}/dashboard  弹幕层 http://127.0.0.1:${port}/overlay  歌单 http://127.0.0.1:${port}/songs`);
  });
  return server;
}

function obsSettings(overrides) {
  const c = (config && config.obs) || {};
  return Object.assign(
    {
      host: c.host || '127.0.0.1',
      port: Number(c.port || 4455),
      password: c.password || '',
      scene: c.scene || '鲸live',
      bgColor: String(c.bgColor || '0b0c0e'),
      preset: c.preset || 'simple',
      baseUrl: 'http://127.0.0.1:' + (state.port || 8788)
    },
    overrides || {}
  );
}

function obsProbe(overrides) { return obs.probe(obsSettings(overrides)); }
function obsSetup(overrides) { return obs.setupScene(obsSettings(overrides)); }

module.exports = {
  start,
  resolvePort,
  envOf,
  obsProbe,
  obsSetup,
  get config() { return config; },
  state,
  handleEvent,
  enqueueSpeech,
  aiReply,
  requestSong,
  requestSing,
  skipSong,
  loadConfig,
  saveConfig,
  setConfig(patch) {
    config = deepMerge(config, patch || {});
    saveConfig();
    return config;
  },
  resetStats() {
    state.stats = { enter: 0, danmaku: 0, gift: 0, follow: 0, like: 0, say: 0 };
    return state.stats;
  },
  startSession() { state.started = true; state.startedAt = Date.now(); wsBroadcast({ kind: 'status', running: true }); return { ok: true }; },
  stopSession() { state.started = false; wsBroadcast({ kind: 'status', running: false }); return { ok: true }; },
  say(text) { enqueueSpeech(text); return { ok: true }; },
  testEvent(ev) { handleEvent(ev); return { ok: true }; },
  ingest(events) { (events || []).forEach((e) => handleEvent(e)); return { ok: true, count: (events || []).length }; },
  status() {
    const cfgSongs = config.songs || {};
    return {
      running: state.started, startedAt: state.startedAt, connected: state.connected,
      mode: (config.douyin || {}).mode, stats: state.stats, recent: state.recent,
      song: {
        mode: cfgSongs.perform === 'play' ? 'play' : 'sing',
        current: state.song.current,
        queue: state.song.queue.map((x) => ({ name: x.song.name, by: x.by }))
      }
    };
  }
};
