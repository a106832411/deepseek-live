/**
 * 极简 obs-websocket v5 客户端（零 npm 依赖）
 * 只实现控制台需要的：连接 / 请求 / 建场景 / 建浏览器源
 */
const crypto = require('crypto');
const net = require('net');

const OP = { HELLO: 0, IDENTIFY: 1, IDENTIFIED: 2, REIDENTIFY: 3, EVENT: 5, REQUEST: 6, RESPONSE: 7 };

function frame(payload) {
  const data = Buffer.from(payload, 'utf8');
  const len = data.length;
  let head;
  if (len < 126) {
    head = Buffer.alloc(2);
    head[1] = 0x80 | len;
  } else if (len < 65536) {
    head = Buffer.alloc(4);
    head[1] = 0x80 | 126;
    head.writeUInt16BE(len, 2);
  } else {
    head = Buffer.alloc(10);
    head[1] = 0x80 | 127;
    head.writeBigUInt64BE(BigInt(len), 2);
  }
  head[0] = 0x81; // FIN + text
  const mask = crypto.randomBytes(4);
  const masked = Buffer.alloc(len);
  for (let i = 0; i < len; i++) masked[i] = data[i] ^ mask[i % 4];
  return Buffer.concat([head, mask, masked]);
}

function parseFrames(buf) {
  const out = [];
  let offset = 0;
  while (offset + 2 <= buf.length) {
    const b0 = buf[offset];
    const b1 = buf[offset + 1];
    const opcode = b0 & 0x0f;
    const masked = (b1 & 0x80) !== 0;
    let len = b1 & 0x7f;
    let pos = offset + 2;
    if (len === 126) {
      if (pos + 2 > buf.length) break;
      len = buf.readUInt16BE(pos);
      pos += 2;
    } else if (len === 127) {
      if (pos + 8 > buf.length) break;
      len = Number(buf.readBigUInt64BE(pos));
      pos += 8;
    }
    let mask = null;
    if (masked) {
      if (pos + 4 > buf.length) break;
      mask = buf.slice(pos, pos + 4);
      pos += 4;
    }
    if (pos + len > buf.length) break;
    let payload = buf.slice(pos, pos + len);
    if (mask) {
      const d = Buffer.alloc(len);
      for (let i = 0; i < len; i++) d[i] = payload[i] ^ mask[i % 4];
      payload = d;
    }
    out.push({ opcode, payload });
    offset = pos + len;
  }
  return { frames: out, rest: buf.slice(offset) };
}

class ObsClient {
  constructor(opts) {
    this.opts = Object.assign({ host: '127.0.0.1', port: 4455, password: '', timeout: 8000 }, opts || {});
    this.sock = null;
    this.pending = new Map();
    this.queue = Promise.resolve();
    this.seq = 0;
    this.hello = null;
    this.events = [];
  }

  connect() {
    return new Promise((resolve, reject) => {
      this._rejectConnect = reject;
      const guard = setTimeout(() => {
        if (!this.identified) {
          try { this.sock && this.sock.destroy(); } catch (e) {}
          reject(new Error('OBS 认证超时（检查密码 / 连接地址）'));
        }
      }, this.opts.timeout || 8000);
      const done = (fn) => (v) => { clearTimeout(guard); fn(v); };
      const { host, port, timeout } = this.opts;
      const sock = net.connect({ host, port });
      this.sock = sock;
      let buf = Buffer.alloc(0);
      const key = crypto.randomBytes(16).toString('base64');
      sock.setTimeout(timeout);
      sock.once('timeout', () => {
        try { sock.destroy(); } catch (e) {}
        reject(new Error('OBS 连接超时'));
      });
      sock.once('error', (e) => reject(e));
      sock.once('connect', () => {
        sock.write(
          'GET / HTTP/1.1\r\n' +
            'Host: ' + host + ':' + port + '\r\n' +
            'Upgrade: websocket\r\nConnection: Upgrade\r\n' +
            'Sec-WebSocket-Key: ' + key + '\r\nSec-WebSocket-Version: 13\r\n\r\n'
        );
      });
      const onData = (chunk) => {
        buf = Buffer.concat([buf, chunk]);
        if (!this.handshaked) {
          const idx = buf.indexOf('\r\n\r\n');
          if (idx < 0) return;
          const head = buf.slice(0, idx).toString('latin1');
          buf = buf.slice(idx + 4);
          if (!/101/.test(head.split('\r\n')[0])) {
            try { sock.destroy(); } catch (e) {}
            return reject(new Error('OBS WebSocket 握手失败: ' + head.split('\r\n')[0]));
          }
          this.handshaked = true;
          sock.removeAllListeners('timeout');
          sock.setTimeout(0);
        }
        const parsed = parseFrames(buf);
        buf = parsed.rest;
        for (const f of parsed.frames) this.onFrame(f, resolve, reject);
      };
      sock.on('data', (chunk) => {
        try {
          onData(chunk);
        } catch (e) {
          reject(e);
        }
      });
      sock.on('close', () => {
        this.sock = null;
        for (const [, p] of this.pending) p.reject(new Error('OBS 连接已关闭'));
        this.pending.clear();
        if (!this.identified && this._rejectConnect) {
          this._rejectConnect(new Error('OBS 在认证阶段关闭了连接（密码错误或未开启 WebSocket？）'));
        }
      });
    });
  }

  onFrame(f, resolveConnect, rejectConnect) {
    if (f.opcode === 9) {
      // ping → pong
      this.rawPong(f.payload);
      return;
    }
    if (f.opcode === 8) {
      if (this.sock) this.sock.destroy();
      return;
    }
    if (f.opcode !== 1) return;
    let msg;
    try { msg = JSON.parse(f.payload.toString('utf8')); } catch (e) { return; }

    if (msg.op === OP.HELLO) {
      this.hello = msg.d;
      const payload = { rpcVersion: 1, eventSubscriptions: 0 };
      if (this.hello.authentication && this.opts.password) {
        const chal = this.hello.authentication;
        const secret = crypto.createHash('sha256').update(this.opts.password + chal.salt).digest('base64');
        payload.authentication = crypto.createHash('sha256').update(secret + chal.challenge).digest('base64');
      }
      this.sendRaw({ op: OP.IDENTIFY, d: payload });
      return;
    }
    if (msg.op === OP.IDENTIFIED) {
      this.identified = true;
      if (resolveConnect) resolveConnect(this);
      return;
    }
    if (msg.op === OP.RESPONSE) {
      const p = this.pending.get(msg.d.requestId);
      if (!p) return;
      this.pending.delete(msg.d.requestId);
      if (msg.d.requestStatus && msg.d.requestStatus.result) {
        p.resolve(msg.d.responseData || {});
      } else {
        p.reject(new Error((msg.d.requestStatus && msg.d.requestStatus.comment) || 'OBS 请求失败'));
      }
      return;
    }
    if (msg.op === OP.EVENT) this.events.push(msg.d);
  }

  rawPong(payload) {
    try {
      this.sock.write(Buffer.concat([Buffer.from([0x8a, 0x80 | Math.min(payload.length, 125)]), crypto.randomBytes(4), payload]));
    } catch (e) {}
  }

  sendRaw(obj) {
    if (!this.sock) throw new Error('OBS 未连接');
    this.sock.write(frame(JSON.stringify(obj)));
  }

  request(requestType, requestData) {
    const p = new Promise((resolve, reject) => {
      const requestId = 'r' + ++this.seq;
      this.pending.set(requestId, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(requestId)) {
          this.pending.delete(requestId);
          reject(new Error('OBS 请求超时: ' + requestType));
        }
      }, 8000);
      try {
        this.sendRaw({ op: OP.REQUEST, d: { requestType, requestId, requestData: requestData || {} } });
      } catch (e) {
        this.pending.delete(requestId);
        reject(e);
      }
    });
    this.queue = this.queue.then(() => p, () => p);
    return p;
  }

  close() {
    try { this.sock && this.sock.destroy(); } catch (e) {}
    this.sock = null;
  }
}

function bgAbgr(hexLike) {
  const hex = String(hexLike || '0b0c0e').replace('#', '').padStart(6, '0');
  const r = parseInt(hex.slice(0, 2), 16) || 0x0b;
  const g = parseInt(hex.slice(2, 4), 16) || 0x0c;
  const b = parseInt(hex.slice(4, 6), 16) || 0x0e;
  return (0xff000000 + (b << 16) + (g << 8) + r) >>> 0;
}

function layoutFor(canvasW, canvasH) {
  const portrait = canvasH > canvasW;
  const W = canvasW;
  const H = canvasH;
  const r = (v) => Math.round(v);
  if (portrait) {
    return [
      { name: '皮套', url: '/live2d?scale=0.95', w: r(W * 0.9), h: r(H * 0.55), x: r(W * 0.05), y: r(H * 0.30) },
      { name: '弹幕层', url: '/overlay', w: r(W * 0.94), h: r(H * 0.2), x: r(W * 0.03), y: r(H * 0.02) },
      { name: '歌单', url: '/songs', w: r(W * 0.4), h: r(H * 0.22), x: r(W * 0.57), y: r(H * 0.76) }
    ];
  }
  return [
    { name: '皮套', url: '/live2d?scale=0.95', w: r(W * 0.42), h: r(H * 0.78), x: r(W * 0.29), y: r(H * 0.2) },
    { name: '弹幕层', url: '/overlay', w: r(W * 0.35), h: r(H * 0.28), x: r(W * 0.02), y: r(H * 0.7) },
    { name: '歌单', url: '/songs', w: r(W * 0.2), h: r(H * 0.22), x: r(W * 0.78), y: r(H * 0.04) }
  ];
}

/** 一键配置：创建（或复用）场景 + 三个浏览器源并按画布摆好 */
function scaleSpec(spec, W, H) {
  // 旧场景基准 1080x1920
  const sx = W / 1080;
  const sy = H / 1920;
  const out = Object.assign({}, spec);
  if (out.x != null) out.x = Math.round(out.x * sx);
  if (out.y != null) out.y = Math.round(out.y * sy);
  if (out.w != null) out.w = Math.round(out.w * sx);
  if (out.h != null) out.h = Math.round(out.h * sy);
  return out;
}

function presetSources(preset, W, H, api) {
  const p = preset === 'game' ? 'game' : 'simple';
  if (p === 'simple') {
    return [
      { name: '皮套', kind: 'browser', url: '/live2d?scale=0.95', iw: 972, ih: 1056, w: 972, h: 1056, x: Math.round(W * 0.05), y: Math.round(H * 0.30) },
      { name: '弹幕层', kind: 'browser', url: '/overlay', iw: 1015, ih: 384, w: 1015, h: 384, x: Math.round(W * 0.03), y: Math.round(H * 0.02) },
      { name: '歌单', kind: 'browser', url: '/songs', iw: 432, ih: 422, w: 432, h: 422, x: Math.round(W * 0.57), y: Math.round(H * 0.76) }
    ].map((s) => scaleSpec(s, W, H));
  }

  // 完整直播：复刻旧版「AI主播直播间-竖屏」布局（1080x1920 基准）
  const base = [
    { name: '背景板', kind: 'image', file: 'D:/文档/deepseek-harness/default-workspace/whaletalk/assemble/ai_bg/1080x1920/bg3_星空鲸海.png', w: 1080, h: 1920, x: 0, y: 0 },
    { name: '游戏画面', kind: 'browser', url: 'http://127.0.0.1:9800/cam', iw: 800, ih: 600, w: 1080, h: 810, x: 0, y: 190 },
    { name: '文字叠加', kind: 'image', file: 'D:\文档\deepseek-harness\default-workspace\whaletalk\assemble\叠加层_竖屏_文字_v2.png', w: 1080, h: 1920, x: -5, y: 0 },
    { name: '皮套', kind: 'browser', url: '/live2d?scale=0.95', iw: 1080, ih: 1080, w: 324, h: 324, x: 745, y: 1500 },
    { name: '本场数据', kind: 'text', text: '本场数据', w: 234, h: 126, x: 640, y: 950 },
    { name: '歌单', kind: 'browser', url: '/songs', iw: 432, ih: 422, w: 268, h: 262, x: 575, y: 1050 },
    { name: '字幕', kind: 'browser', url: '/overlay', iw: 1280, ih: 200, w: 1024, h: 160, x: 0, y: 1005 },
    { name: '弹幕', kind: 'browser', url: '/overlay', iw: 420, ih: 400, w: 260, h: 248, x: 10, y: 1050 },
    { name: '游戏面板', kind: 'browser', url: '/overlay', iw: 760, ih: 700, w: 760, h: 700, x: 160, y: 600, hidden: true }
  ];
  return base.map((x) => scaleSpec(x, W, H)).map((x) => {
    if (x.kind === 'browser' && x.url && !x.url.startsWith('http')) x.url = '/live2d' === x.url ? x.url : x.url;
    return x;
  });
}

async function createOrUpdateSource(client, sceneName, spec, api, existing) {
  const fullUrl = spec.url && spec.url.startsWith('http') ? spec.url : spec.url ? api + spec.url : '';
  const isBrowser = spec.kind === 'browser';
  const isImage = spec.kind === 'image';
  const isText = spec.kind === 'text';

  let inputKind = 'browser_source';
  if (isImage) inputKind = 'image_source';
  if (isText) inputKind = 'text_gdiplus_v3';

  let settings = {};
  if (isBrowser) settings = { url: fullUrl, width: spec.iw || 800, height: spec.ih || 600, reroute_audio: false };
  if (isImage) settings = { file: spec.file, unload: false };
  if (isText) settings = {
    text: spec.text || sceneName,
    font: { face: 'Microsoft YaHei', size: 46, style: 'Bold' },
    color: 0xffffffff,
    align: 'center',
    valign: 'center',
    outline: true,
    outline_color: 0xff000000,
    outline_size: 2
  };

  if (existing.has(spec.name)) {
    if (isBrowser || isImage || isText) {
      await client.request('SetInputSettings', { inputName: spec.name, inputSettings: settings, overlay: true }).catch(() => {});
    }
    const idRes = await client.request('GetSceneItemId', { sceneName, sourceName: spec.name });
    await client.request('SetSceneItemTransform', {
      sceneName,
      sceneItemId: idRes.sceneItemId,
      sceneItemTransform: { positionX: spec.x || 0, positionY: spec.y || 0, width: spec.w || 0, height: spec.h || 1 }
    });
    if (spec.hidden) {
      await client.request('SetSceneItemEnabled', { sceneName, sceneItemId: idRes.sceneItemId, sceneItemEnabled: false }).catch(() => {});
    }
    return '更新';
  }

  try {
    await client.request('CreateInput', {
      sceneName,
      inputName: spec.name,
      inputKind,
      inputSettings: settings,
      sceneItemEnabled: !spec.hidden
    });
  } catch (e) {
    if (!/already exists/i.test(e.message)) throw e;
    await client.request('CreateSceneItem', { sceneName, sourceName: spec.name, sceneItemEnabled: !spec.hidden });
  }
  const idRes = await client.request('GetSceneItemId', { sceneName, sourceName: spec.name });
  await client.request('SetSceneItemTransform', {
    sceneName,
    sceneItemId: idRes.sceneItemId,
    sceneItemTransform: { positionX: spec.x || 0, positionY: spec.y || 0, width: spec.w || 0, height: spec.h || 1 }
  });
  if (spec.hidden) {
    await client.request('SetSceneItemEnabled', { sceneName, sceneItemId: idRes.sceneItemId, sceneItemEnabled: false }).catch(() => {});
  }
  return '添加';
}

async function setupScene(opts) {
  const o = Object.assign({}, opts || {});
  const logs = [];
  const client = new ObsClient(o);
  const say = (m) => { logs.push(m); return m; };
  try {
    say('连接 OBS ' + o.host + ':' + o.port + ' …');
    await client.connect();
    const ver = await client.request('GetVersion');
    say('已连接：OBS ' + (ver.obsVersion || '?') + ' / obs-websocket ' + (ver.obsWebSocketVersion || '?'));

    const video = await client.request('GetVideoSettings');
    const W = Number(video.baseWidth) || 1080;
    const H = Number(video.baseHeight) || 1920;
    say('画布：' + W + '×' + H + '，布局：' + (o.preset === 'game' ? '完整直播（旧版场景）' : '简单直播'));

    const sceneName = o.scene || '鲸live直播';
    const scenes = await client.request('GetSceneList');
    const exists = (scenes.scenes || []).some((s) => s.sceneName === sceneName);
    if (!exists) {
      await client.request('CreateScene', { sceneName });
      say('已创建场景：' + sceneName);
    } else {
      say('复用已有场景：' + sceneName);
    }
    await client.request('SetCurrentProgramScene', { sceneName });
    say('已切换到场景：' + sceneName);

    const items = await client.request('GetSceneItemList', { sceneName });
    const existing = new Set((items.sceneItems || []).map((i) => i.sourceName));

    // 背景色
    if (!existing.has('背景')) {
      try {
        await client.request('CreateInput', {
          sceneName,
          inputName: '背景',
          inputKind: 'color_source_v3',
          inputSettings: { color: bgAbgr(o.bgColor), width: W, height: H },
          sceneItemEnabled: true
        });
        const bgItem = await client.request('GetSceneItemId', { sceneName, sourceName: '背景' });
        await client.request('SetSceneItemTransform', {
          sceneName,
          sceneItemId: bgItem.sceneItemId,
          sceneItemTransform: { positionX: 0, positionY: 0, width: W, height: H }
        });
        say('已添加背景层');
      } catch (e) {
        say('背景层跳过：' + e.message);
      }
    } else {
      try {
        await client.request('SetInputSettings', {
          inputName: '背景',
          inputSettings: { color: bgAbgr(o.bgColor), width: W, height: H },
          overlay: true
        });
        say('已更新背景色');
      } catch (e) {
        say('背景更新失败：' + e.message);
      }
    }

    const api = o.baseUrl || 'http://127.0.0.1:8788';
    const sources = o.sources || presetSources(o.preset || 'simple', W, H, api);
    for (const spec of sources) {
      const title = spec.kind === 'browser' ? (spec.url || '') : spec.kind === 'image' ? (spec.file || '') : (spec.text || '');
      try {
        if (spec.kind === 'image' && spec.file && !require('fs').existsSync(spec.file)) {
          say('跳过 ' + spec.name + '：图片不存在 ' + spec.file);
          continue;
        }
        const action = await createOrUpdateSource(client, sceneName, spec, api, existing);
        say(action + '源：' + spec.name + ' → ' + title + (spec.hidden ? '（隐藏）' : ''));
      } catch (e) {
        say('失败 ' + spec.name + '：' + e.message);
      }
    }

    await client.request('SetCurrentProgramScene', { sceneName });
    say('完成 ✅ 场景「' + sceneName + '」已就绪');
    return { ok: true, scene: sceneName, canvas: [W, H], preset: o.preset || 'simple', logs };
  } catch (e) {
    say('失败：' + e.message);
    return { ok: false, error: e.message, logs };
  } finally {
    client.close();
  }
}

async function probe(opts) {
  const client = new ObsClient(opts || {});
  try {
    await client.connect();
    const ver = await client.request('GetVersion');
    const video = await client.request('GetVideoSettings');
    const scenes = await client.request('GetSceneList');
    return {
      ok: true,
      version: ver.obsVersion,
      websocket: ver.obsWebSocketVersion,
      canvas: [video.baseWidth, video.baseHeight],
      scenes: (scenes.scenes || []).map((s) => s.sceneName),
      current: scenes.currentProgramSceneName
    };
  } catch (e) {
    return { ok: false, error: e.message };
  } finally {
    client.close();
  }
}

module.exports = { ObsClient, setupScene, probe, layoutFor };
