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
    say('画布：' + W + '×' + H);

    const sceneName = o.scene || '深语直播';
    if (o.autoCanvas !== false) {
      // 不改用户画布，仅按当前画布布局
    }

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
          sceneItemTransform: { positionX: 0, positionY: 0 }
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
    const layout = o.sources || layoutFor(W, H);
    for (const s of layout) {
      const url = s.url.startsWith('http') ? s.url : api + s.url;
      if (existing.has(s.name)) {
        try {
          await client.request('SetInputSettings', {
            inputName: s.name,
            inputSettings: { url, width: s.w, height: s.h },
            overlay: true
          });
          const idRes = await client.request('GetSceneItemId', { sceneName, sourceName: s.name });
          await client.request('SetSceneItemTransform', {
            sceneName,
            sceneItemId: idRes.sceneItemId,
            sceneItemTransform: { positionX: s.x, positionY: s.y, width: s.w, height: s.h }
          });
          say('更新源：' + s.name + ' → ' + url);
        } catch (e) {
          say('更新失败 ' + s.name + '：' + e.message);
        }
        continue;
      }
      try {
        try {
          await client.request('CreateInput', {
            sceneName,
            inputName: s.name,
            inputKind: 'browser_source',
            inputSettings: { url, width: s.w, height: s.h, reroute_audio: false, shutdown: false, restart_when_active: false },
            sceneItemEnabled: true
          });
        } catch (e) {
          // 源已存在于其他场景：直接复用
          if (!/already exists/i.test(e.message)) throw e;
          await client.request('CreateSceneItem', { sceneName, sourceName: s.name, sceneItemEnabled: true });
          await client.request('SetInputSettings', {
            inputName: s.name,
            inputSettings: { url, width: s.w, height: s.h },
            overlay: true
          });
        }
        const idRes = await client.request('GetSceneItemId', { sceneName, sourceName: s.name });
        await client.request('SetSceneItemTransform', {
          sceneName,
          sceneItemId: idRes.sceneItemId,
          sceneItemTransform: { positionX: s.x, positionY: s.y, width: s.w, height: s.h }
        });
        say('已添加源：' + s.name + ' → ' + url + ' @ ' + s.w + '×' + s.h + ' (' + s.x + ',' + s.y + ')');
      } catch (e) {
        say('添加失败 ' + s.name + '：' + e.message);
      }
    }

    await client.request('SetCurrentProgramScene', { sceneName });
    say('完成 ✅ 场景「' + sceneName + '」已就绪');
    return { ok: true, scene: sceneName, canvas: [W, H], logs };
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
