#!/usr/bin/env node
/**
 * 抖音弹幕中继（鲸live 内置版，零 npm 依赖）
 *
 * 做法：用 Chrome 打开直播间网页 → CDP 连上去 → 在页面里挂一个 MutationObserver 读聊天 DOM
 *      → 正则分类成 弹幕/进房/礼物/关注/点赞 → POST 给鲸live 的 /api/douyin/ingest。
 * 不用 douyin 私有 WebSocket / 不需要签名，登录状态靠 Chrome profile 自己保留。
 *
 * 用法：
 *   node extras/douyin/relay.js                    # 用 config.json 里的 douyin.web_rid / room_url
 *   node extras/douyin/relay.js --rid 123456789     # 指定直播间号
 *   node extras/douyin/relay.js --url https://live.douyin.com/xxx --api http://127.0.0.1:8788
 *   node extras/douyin/relay.js --port 9333 --profile "D:/tmp/douyin-profile" --keep-chrome
 *   node extras/douyin/relay.js --url file:///C:/tmp/fake.html --match fake   # 本地假页面自测
 *
 * 参数：
 *   --rid <号>        直播间 web_rid（默认读 config.json）
 *   --url <地址>      直播间 URL（默认 https://live.douyin.com/<rid>）
 *   --api <地址>      鲸live 地址（默认 http://127.0.0.1:8788，也认 DEEPLIVE_PORT/WT_PORT）
 *   --port <端口>     Chrome 调试端口（默认 9333）
 *   --profile <目录>  Chrome profile 目录（默认 %LOCALAPPDATA%\jinglive-douyin-profile，登录一次后复用）
 *   --chrome <路径>   Chrome 可执行文件（默认自动找）
 *   --match <子串>    调试用：页面 URL 里包含这个子串就接管（默认匹配 live.douyin.com）
 *   --keep-chrome     退出时不关闭 Chrome
 *   --once            只跑一轮抓取就退出（调试用）
 *
 * 注意：连上后**不要按 F5**（刷新会重建页面，中继会自动重新注入，但旧的已读标记会丢，可能重播几条）；
 *      Chrome 窗口不要关，关了中继就没画面可读了（脚本会提示）。
 */
'use strict';

const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { spawn, execFileSync } = require('child_process');

// ---------------- 参数 ----------------
function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  if (i < 0) return def;
  const v = process.argv[i + 1];
  if (v === undefined || v.startsWith('--')) return true;
  return v;
}
const REPO = path.resolve(__dirname, '..', '..');

function loadConfig() {
  try {
    const f = process.env.WT_CONFIG || path.join(REPO, 'config.json');
    return JSON.parse(fs.readFileSync(f, 'utf8').replace(/^\uFEFF/, ''));
  } catch (e) {
    return {};
  }
}
const CFG = loadConfig();
const CFG_DOUYIN = CFG.douyin || {};
const API = String(arg('api', process.env.STA_RELAY_API || `http://127.0.0.1:${process.env.DEEPLIVE_PORT || process.env.WT_PORT || 8788}`)).replace(/\/$/, '');
const RID = String(arg('rid', CFG_DOUYIN.web_rid || '') || '');
const ROOM_URL = String(arg('url', CFG_DOUYIN.room_url || (RID ? `https://live.douyin.com/${RID}` : '')) || '');
const DEBUG_PORT = Number(arg('port', 9333));
// 默认接管抖音直播间页面；本地自测页面就按完整 URL 匹配
const MATCH = String(arg('match', /douyin\.com/i.test(ROOM_URL) ? 'live.douyin.com' : ROOM_URL));
const KEEP_CHROME = !!arg('keep-chrome', false);
const ONCE = !!arg('once', false);
const PROFILE = String(arg('profile', CFG_DOUYIN.profile_dir ||
  path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'jinglive-douyin-profile')));
const CHROME = String(arg('chrome', process.env.CHROME_PATH || '')) ||
  ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'].find((p) => { try { return fs.existsSync(p); } catch (e) { return false; } }) || '';

if (!ROOM_URL) {
  console.error('[FATAL] 没有直播间地址：在 config.json 填 douyin.room_url / web_rid，或传 --rid / --url');
  process.exit(2);
}

function log(msg) {
  const line = `[${new Date().toTimeString().slice(0, 8)}] ${msg}`;
  console.log(line);
}

// ---------------- 极简 WebSocket 客户端（CDP 用；零依赖，任何 Node ≥14 都能跑） ----------------
class MiniWS {
  constructor() {
    this.sock = null;
    this.buf = Buffer.alloc(0);
    this.onmessage = () => {};
    this.onclose = () => {};
    this.closed = false;
  }
  connect(url) {
    return new Promise((resolve, reject) => {
      const m = /^ws:\/\/([^/:]+)(?::(\d+))?(\/.*)?$/.exec(url);
      if (!m) return reject(new Error('只支持 ws:// 地址: ' + url));
      const host = m[1];
      const port = Number(m[2] || 80);
      const p = m[3] || '/';
      const key = crypto.randomBytes(16).toString('base64');
      const sock = net.connect(port, host);
      this.sock = sock;
      let handshook = false;
      let head = Buffer.alloc(0);
      sock.setNoDelay(true);
      sock.on('connect', () => {
        sock.write(
          `GET ${p} HTTP/1.1\r\nHost: ${host}:${port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n` +
          `Sec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`
        );
      });
      sock.on('data', (chunk) => {
        if (!handshook) {
          head = Buffer.concat([head, chunk]);
          const idx = head.indexOf('\r\n\r\n');
          if (idx < 0) return;
          const header = head.slice(0, idx).toString('utf8');
          if (!/^HTTP\/1\.1 101/i.test(header)) return reject(new Error('WebSocket 握手失败: ' + header.split('\r\n')[0]));
          handshook = true;
          this.buf = head.slice(idx + 4);
          resolve(this);
          this._drain();
          return;
        }
        this.buf = Buffer.concat([this.buf, chunk]);
        this._drain();
      });
      sock.on('error', (e) => { if (!handshook) reject(e); else this._close(); });
      sock.on('close', () => this._close());
    });
  }
  _close() {
    if (this.closed) return;
    this.closed = true;
    try { this.onclose(); } catch (e) {}
  }
  _drain() {
    for (;;) {
      if (this.buf.length < 2) return;
      const b0 = this.buf[0];
      const b1 = this.buf[1];
      const fin = (b0 & 0x80) !== 0;
      const opcode = b0 & 0x0f;
      const masked = (b1 & 0x80) !== 0;
      let len = b1 & 0x7f;
      let off = 2;
      if (len === 126) {
        if (this.buf.length < 4) return;
        len = this.buf.readUInt16BE(2);
        off = 4;
      } else if (len === 127) {
        if (this.buf.length < 10) return;
        len = Number(this.buf.readBigUInt64BE(2));
        off = 10;
      }
      let mask = null;
      if (masked) {
        if (this.buf.length < off + 4) return;
        mask = this.buf.slice(off, off + 4);
        off += 4;
      }
      if (this.buf.length < off + len) return;
      let payload = this.buf.slice(off, off + len);
      if (mask) payload = Buffer.from(payload.map((v, i) => v ^ mask[i % 4]));
      this.buf = this.buf.slice(off + len);
      if (opcode === 0x8) { this._sendFrame(0x8, Buffer.alloc(0)); return this._close(); }
      if (opcode === 0x9) { this._sendFrame(0xA, payload); continue; }
      if (opcode === 0xA) continue;
      // 文本帧（CDP 全是文本帧；怕分片就简单拼一下）
      const text = payload.toString('utf8');
      try { this.onmessage(text); } catch (e) {}
      if (!fin) continue;
    }
  }
  _sendFrame(opcode, payload) {
    if (!this.sock || this.closed) return;
    const len = payload.length;
    let header;
    if (len < 126) header = Buffer.from([0x80 | opcode, 0x80 | len]);
    else if (len < 65536) { header = Buffer.alloc(4); header[0] = 0x80 | opcode; header[1] = 0x80 | 126; header.writeUInt16BE(len, 2); }
    else { header = Buffer.alloc(10); header[0] = 0x80 | opcode; header[1] = 0x80 | 127; header.writeBigUInt64BE(BigInt(len), 2); }
    const mask = crypto.randomBytes(4);
    const masked = Buffer.from(payload.map((v, i) => v ^ mask[i % 4]));
    try { this.sock.write(Buffer.concat([header, mask, masked])); } catch (e) {}
  }
  send(obj) {
    this._sendFrame(0x1, Buffer.from(JSON.stringify(obj), 'utf8'));
  }
  close() {
    try { this._sendFrame(0x8, Buffer.alloc(0)); } catch (e) {}
    try { this.sock && this.sock.end(); } catch (e) {}
    this._close();
  }
}

// ---------------- CDP 客户端 ----------------
class CDP {
  constructor(ws) {
    this.ws = ws;
    this.seq = 0;
    this.pending = new Map();
    this.onEvent = () => {};
    ws.onmessage = (text) => {
      let msg;
      try { msg = JSON.parse(text); } catch (e) { return; }
      if (msg.id && this.pending.has(msg.id)) {
        const p = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) p.reject(new Error(msg.error.message || 'CDP error'));
        else p.resolve(msg.result || {});
        return;
      }
      try { this.onEvent(msg); } catch (e) {}
    };
    ws.onclose = () => { this.closed = true; };
  }
  async send(method, params = {}, timeout = 25000) {
    if (this.closed) throw new Error('CDP 已断开');
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => { this.pending.delete(id); reject(new Error(method + ' 超时')); }, timeout);
      this.pending.set(id, {
        resolve: (v) => { clearTimeout(t); resolve(v); },
        reject: (e) => { clearTimeout(t); reject(e); }
      });
      this.ws.send({ id, method, params });
    });
  }
  async eval(expr, timeout = 25000) {
    const r = await this.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true }, timeout);
    if (r.exceptionDetails) throw new Error('页面脚本出错: ' + (r.exceptionDetails.text || ''));
    return r.result ? r.result.value : undefined;
  }
}

// ---------------- 页面里注入的采集脚本（读聊天 DOM，不点页面） ----------------
function collector() {
  const norm = (s) => String(s || '')
    .replace(/\u00a0/g, ' ')
    .replace(/[\u200b-\u200d\ufeff]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  const SEL = '[class*="webcast-chatroom___item"], [data-e2e="chat-message"]';
  const Q = (window.__jlQ = window.__jlQ || []);
  if (window.__jlHook) return JSON.stringify(Q.splice(0));
  window.__jlHook = 1;
  const seen = new Set();
  const mark = (el) => {
    const id = el.getAttribute('data-id') || '';
    const t = norm(el.innerText).slice(0, 300);
    if (id) seen.add(id);
    if (t) seen.add('t:' + t);
  };
  // 注入瞬间把已有消息记为已读，避免重启后重播旧聊天
  try { for (const el of document.querySelectorAll(SEL)) mark(el); } catch (e) {}
  const grab = (el) => {
    const id = el.getAttribute('data-id') || (el.parentElement && el.parentElement.getAttribute('data-id')) || '';
    if (id) {
      if (seen.has(id)) return;
      seen.add(id);
      if (seen.size > 4000) { const it = seen.values(); for (let i = 0; i < 1500; i++) seen.delete(it.next().value); }
    }
    const roomMsg = !!el.querySelector('[class*="room-message"]');
    const nickEl = el.querySelector('[class*="nickname"], [class*="v8LY0gZF"], [class*="user-name"]');
    const bodyEl = el.querySelector('[class*="content-with-emoji-text"], [class*="chat-content"]');
    const full = norm(el.innerText).slice(0, 300);
    if (!full) return;
    const rec = {
      id,
      t: Date.now(),
      full,
      nick: nickEl ? norm(nickEl.innerText).slice(0, 60) : '',
      body: bodyEl ? norm(bodyEl.innerText).slice(0, 240) : '',
      room: roomMsg
    };
    const tkey = 't:' + full;
    if (!id) { if (seen.has(tkey)) return; seen.add(tkey); }
    Q.push(rec);
  };
  const obs = new MutationObserver((muts) => {
    for (const m of muts) {
      for (const n of m.addedNodes) {
        if (n.nodeType !== 1) continue;
        if (n.matches && n.matches(SEL)) { grab(n); continue; }
        if (n.querySelectorAll) for (const el of n.querySelectorAll(SEL)) grab(el);
      }
    }
  });
  try { obs.observe(document.body, { childList: true, subtree: true }); } catch (e) {}
  // 兜底全量扫描：进房/点赞这类有时是就地改写节点，MutationObserver 抓不到
  setInterval(() => {
    try {
      const arr = Array.from(document.querySelectorAll(SEL)).slice(-80);
      for (const el of arr) if (!el.__jlSeen) { el.__jlSeen = 1; grab(el); }
    } catch (e) {}
  }, 2500);
  return 'hooked';
}

// ---------------- 文本分类（移植自经验证的 DOM 规则） ----------------
const PRICES = {
  小心心: 1, 玫瑰: 1, 小花花: 1, 大啤酒: 2, 加油鸭: 5, 人气票: 1, 粉丝团灯牌: 1,
  Thuglife: 99, 亲吻: 99, 比心兔兔: 299, 热气球: 520, PK宝箱: 600, 跑车: 1200,
  私人飞机: 3000, 豪华邮轮: 6000, 抖音1号: 10001, 嘉年华: 30000, 火山: 30000,
  宇宙飞船: 30000, 魔法书: 30, 啤酒: 2, 爱心: 1, 飞吻: 1, 拥抱: 1, 棒棒糖: 1,
  鲜花: 1, 鼓掌: 1, 蛋糕: 1, 奶茶: 30, 火箭: 10001
};
const GIFT_VERB = '(?:送出了)';
const GIFT_TAIL = '\\s*(?:[×xX*]\\s*(\\d+))?\\s*$';
const RE_GIFT_BODY = new RegExp('^' + GIFT_VERB + '\\s*(.+?)' + GIFT_TAIL);
const RE_GIFT_FULL = new RegExp('^(.+?)\\s*[：:]\\s*' + GIFT_VERB + '\\s*(.+?)' + GIFT_TAIL);
const RE_GIFT_SPACE = new RegExp('^(.+?)\\s+' + GIFT_VERB + '\\s*(.+?)' + GIFT_TAIL);
const RE_ENTER = /^(.*?)\s*(?:来了|进入了直播间|进入直播间|来到直播间|加入了直播间)\s*$/;
const RE_LIKE = /^(.*?)\s*(?:为主播点赞了?|点亮了|点赞了)\s*$/;
const RE_FOLLOW = /^(.*?)\s*关注了(?:主播|直播间)?\s*$/;
const RE_GUARD = /^(.*?)\s*(?:加入|开通|续费|购买)了?\s*(粉丝团|会员|灯牌)\s*$/;
const RE_SPLIT = /^([^：:]{1,40})[：:]\s*(.+)$/;
const RE_BADGE = /^(?:[\[【『（(][^\]】』）)]{1,10}[\]】』）)]\s*)+/;

const norm = (s) => String(s || '')
  .replace(/\u00a0/g, ' ')
  .replace(/[\u200b-\u200d\ufeff]/g, '')
  .replace(/\s+/g, ' ')
  .trim();
const cleanName = (s) => RE_BADGE.test(String(s)) ? norm(String(s).replace(RE_BADGE, '')).replace(/[：:\s]+$/, '') : norm(s).replace(/[：:\s]+$/, '');
function cleanEnterName(s) {
  let v = cleanName(s);
  const m = /^(.+?)(?:等\s*\d+\+?\s*人|等\s*\d+)$/.exec(v);
  if (m) v = m[1];
  return v.replace(/^@/, '').trim();
}
function giftEvent(name, gift, num, priceTable) {
  const n = Math.max(1, parseInt(num || '1', 10) || 1);
  const nm = cleanEnterName(name);
  const g = norm(gift).replace(/[×xX*]\s*\d+\s*$/, '').trim();
  if (!nm || !g || nm.length > 24) return null;
  const ev = { type: 'gift', name: nm, gift: g, num: n };
  const coins = (priceTable || PRICES)[g];
  if (coins) ev.value = Math.round((coins * n / 10) * 100) / 100;  // 抖币 → 元
  return ev;
}
function bodyEvent(name, body) {
  const nm = cleanName(name);
  const b = norm(body);
  // 1) 礼物正文："送出了 小心心 × 2"
  let m = RE_GIFT_BODY.exec(b);
  if (m) { const ev = giftEvent(name, m[1], m[2]); if (ev) return ev; }
  // 2) 正文很短、本身就叫 "关注了主播 / 点亮了 / 来了"（昵称在另一个 span 里）
  if (b.length <= 14) {
    for (const [re, kind] of [[RE_GUARD, 'guard'], [RE_FOLLOW, 'follow'], [RE_LIKE, 'like'], [RE_ENTER, 'enter']]) {
      m = re.exec(b);
      if (!m) continue;
      const nn = cleanEnterName(nm || m[1]);
      if (!nn || nn.length > 24) continue;
      if (kind === 'guard') return { type: 'guard', name: nn, guard: norm(m[2] || '粉丝团'), num: 1, value: 0 };
      return { type: kind, name: nn };
    }
  }
  return null;
}
/** 一条 DOM 记录 → 鲸live 事件（或 null） */
function classify(rec) {
  const full = norm(rec.full);
  if (!full) return null;
  const body = norm(rec.body);
  const nick = cleanName(rec.nick);
  if (rec.room) return null;   // 系统公告/房管提示，不当弹幕

  let m = RE_GIFT_FULL.exec(full);
  if (m) { const ev = giftEvent(m[1], m[2], m[3]); if (ev) return ev; }
  if (body) { m = RE_GIFT_BODY.exec(body); if (m) { const ev = giftEvent(nick, m[1], m[2]); if (ev) return ev; } }
  m = RE_GIFT_SPACE.exec(full);
  if (m) { const ev = giftEvent(m[1], m[2], m[3]); if (ev) return ev; }

  m = RE_SPLIT.exec(full);
  if (m) {
    const name = cleanName(m[1]);
    const text = norm(m[2]);
    if (!name || !text) return null;
    const ev = bodyEvent(name, text);
    if (ev) return ev;
    return { type: 'danmaku', name, text };
  }
  // 整行形态：进房 / 关注 / 点赞 / 粉丝团
  for (const [re, kind] of [[RE_GUARD, 'guard'], [RE_FOLLOW, 'follow'], [RE_LIKE, 'like'], [RE_ENTER, 'enter']]) {
    m = re.exec(full);
    if (!m) continue;
    const nm = cleanEnterName(m[1]);
    if (!nm || nm.length > 24) continue;
    if (kind === 'guard') return { type: 'guard', name: nm, guard: norm(m[2] || '粉丝团'), num: 1, value: 0 };
    return { type: kind, name: nm };
  }
  return null;
}

// ---------------- 启动 Chrome / 连接调试端口 ----------------
function launchChrome() {
  if (!CHROME) throw new Error('找不到 Chrome：请装 Chrome，或用 --chrome 指定路径');
  try { fs.mkdirSync(PROFILE, { recursive: true }); } catch (e) {}
  // 清掉残留 Singleton 锁（上次没退干净会导致新实例转去旧进程）
  try {
    for (const f of fs.readdirSync(PROFILE)) if (f.startsWith('Singleton')) { try { fs.rmSync(path.join(PROFILE, f), { force: true }); } catch (e) {} }
  } catch (e) {}
  const args = [
    `--remote-debugging-port=${DEBUG_PORT}`,
    `--user-data-dir=${PROFILE}`,
    '--no-first-run', '--no-default-browser-check',
    '--disable-popup-blocking', '--disable-blink-features=AutomationControlled', '--test-type',
    '--mute-audio',
    `--app=${ROOM_URL}`
  ];
  log(`启动 Chrome：${CHROME}`);
  log(`  调试端口 ${DEBUG_PORT}，profile ${PROFILE}`);
  // 注意：不 detached —— 这样客户端「停止中继」用 taskkill /T 能连 Chrome 一起收掉
  const child = spawn(CHROME, args, { stdio: 'ignore', windowsHide: false });
  child.unref();
  return child;
}

async function fetchJson(url, timeout = 5000) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeout);
  try {
    const r = await fetch(url, { signal: ctl.signal });
    return await r.json();
  } finally { clearTimeout(t); }
}

async function waitForPage(timeoutMs = 90000) {
  const t0 = Date.now();
  let lastErr = '';
  while (Date.now() - t0 < timeoutMs) {
    try {
      const list = await fetchJson(`http://127.0.0.1:${DEBUG_PORT}/json/list`);
      const pages = (list || []).filter((x) => x.type === 'page');
      const hit = pages.find((x) => String(x.url || '').includes(MATCH));
      if (hit && hit.webSocketDebuggerUrl) return hit;
      lastErr = `页面还没就绪（现有 ${pages.map((p) => p.url).slice(0, 3).join(' | ')}）`;
    } catch (e) {
      lastErr = '调试端口还没开';
    }
    await sleep(800);
  }
  throw new Error('等直播间页面超时：' + lastErr + '（可能没登录/网络慢，Chrome 窗口里看一眼）');
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------- 主流程 ----------------
let seenEvents = new Map();   // 本地再去重一层（id/文本，10 分钟）
function localDedupe(list) {
  const out = [];
  const now = Date.now();
  for (const [k, t] of seenEvents) if (now - t > 600000) seenEvents.delete(k);
  for (const ev of list) {
    const key = ev.type + '|' + (ev.name || '') + '|' + (ev.text || ev.gift || '') + '|' + (ev.num || '');
    if (seenEvents.has(key)) continue;
    seenEvents.set(key, now);
    out.push(ev);
  }
  return out;
}

async function post(pathname, body) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 8000);
  try {
    const r = await fetch(API + pathname, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      signal: ctl.signal
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } finally { clearTimeout(t); }
}

let chromeChild = null;
let stats = { recv: 0, sent: 0, errors: 0 };

async function main() {
  log('==============================');
  log('  鲸live · 抖音弹幕中继');
  log('==============================');
  log(`直播间：${ROOM_URL}`);
  log(`鲸live：${API}`);
  try {
    await fetchJson(API + '/api/health', 3000);
  } catch (e) {
    console.error(`[FATAL] 连不上鲸live（${API}）：先把客户端/服务启动起来再开中继`);
    process.exit(2);
  }

  chromeChild = launchChrome();
  log('等直播间页面加载…（首次打开要登录的话，在弹出的 Chrome 里扫码登录，登录状态会存在 profile 里）');
  const page = await waitForPage();
  log('页面已就绪：' + page.url);

  const ws = new MiniWS();
  await ws.connect(page.webSocketDebuggerUrl);
  const cdp = new CDP(ws);
  await cdp.send('Runtime.enable');
  log('已连上浏览器调试端口');

  // 标记抖音已连接
  try { await post('/api/douyin/connect', {}); } catch (e) {}

  const heartbeat = setInterval(() => {
    post('/api/douyin/heartbeat', {}).catch(() => {});
  }, 15000);
  post('/api/douyin/heartbeat', {}).catch(() => {});

  let running = true;
  const stop = () => {
    if (!running) return;
    running = false;
    clearInterval(heartbeat);
    log(`退出：收到 ${stats.recv} 条原始消息，推送 ${stats.sent} 个事件，错误 ${stats.errors}`);
    try { ws.close(); } catch (e) {}
    if (!KEEP_CHROME && chromeChild && chromeChild.pid) {
      // 只关我们自己开的那个 Chrome（按 profile 匹配，别关用户自己的窗口）
      try {
        execFileSync('taskkill', ['/PID', String(chromeChild.pid), '/T', '/F'], { windowsHide: true });
        log('已关闭中继用的 Chrome');
      } catch (e) {}
    }
    process.exit(0);
  };
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);

  let lastPrint = Date.now();
  const t0 = Date.now();
  while (running) {
    if (!ONCE && Date.now() - t0 > 0 && chromeChild && chromeChild.exitCode !== null) {
      log('Chrome 已退出，中继停止（直播间窗口关了？）');
      break;
    }
    try {
      // 一次 evaluate 里完成：确保采集脚本在 + 取走新消息
      const expr = `(() => { const r = (${collector.toString()})(); return r === 'hooked' ? '[]' : r; })()`;
      const raw = await cdp.eval(expr, 15000);
      let recs = [];
      try { recs = JSON.parse(raw || '[]'); } catch (e) {}
      if (recs && recs.length) {
        stats.recv += recs.length;
        const evs = [];
        for (const rec of recs) {
          let ev = null;
          try { ev = classify(rec); } catch (e) {}
          if (ev) evs.push(ev);
        }
        const fresh = localDedupe(evs);
        if (fresh.length) {
          try {
            await post('/api/douyin/ingest', { events: fresh });
            stats.sent += fresh.length;
            for (const ev of fresh.slice(0, 3)) {
              log(`  ${ev.type} ${ev.name || ''} ${ev.text || ev.gift || ''}`.trim());
            }
          } catch (e) { stats.errors++; }
        }
      }
    } catch (e) {
      stats.errors++;
      if (String(e.message).includes('CDP 已断开') || String(e.message).includes('断开')) {
        log('浏览器连接断开（页面刷新/关闭？），中继退出');
        break;
      }
    }
    if (Date.now() - lastPrint > 60000) {
      lastPrint = Date.now();
      log(`运行中：收到 ${stats.recv} 条，推送 ${stats.sent} 个事件，错误 ${stats.errors}`);
    }
    if (ONCE) { await sleep(1500); stop(); return; }
    await sleep(800);
  }
  stop();
}

main().catch((e) => {
  console.error('[FATAL] ' + (e && e.stack ? e.stack : e));
  if (!KEEP_CHROME && chromeChild && chromeChild.pid) {
    try { execFileSync('taskkill', ['/PID', String(chromeChild.pid), '/T', '/F'], { windowsHide: true }); } catch (err) {}
  }
  process.exit(1);
});
