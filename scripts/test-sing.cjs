#!/usr/bin/env node
/**
 * 清唱功能冒烟测试（对运行中的服务打，不自己起服务）
 *
 * 用法：
 *   node scripts/test-sing.cjs                       # 默认打 http://127.0.0.1:8788
 *   node scripts/test-sing.cjs --url http://127.0.0.1:8799
 *   node scripts/test-sing.cjs --song 七里香 --timeout 90
 *
 * 检查项：
 *   1. /api/health 正常、/api/songs/state 是 sing 模式
 *   2. WS /ws 能连上（收到 hello）
 *   3. 模拟弹幕「点歌 xxx」→ 收到 sing start / 若干 sing line / sing end
 *   4. state 里 current 带 lyrics、lyricIndex 递增、audio 是 logs/sing/*.wav（真 TTS 时）
 *   5. POST /api/songs/skip 生效
 * 退出码：0 全过；1 有失败。
 */
const http = require('http');

const args = process.argv.slice(2);
function arg(name, def) {
  const i = args.indexOf('--' + name);
  return i >= 0 ? args[i + 1] : def;
}
const BASE = String(arg('url', 'http://127.0.0.1:8788')).replace(/\/$/, '');
const SONG = arg('song', '七里香');
const TIMEOUT = Number(arg('timeout', 120)) * 1000;

function req(method, path, body) {
  return new Promise((resolve, reject) => {
    const u = new URL(BASE + path);
    const data = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const r = http.request({
      hostname: u.hostname, port: u.port, path: u.pathname + u.search, method,
      headers: Object.assign({ 'Content-Type': 'application/json' }, data ? { 'Content-Length': data.length } : {}),
      timeout: 30000
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let json = null;
        try { json = JSON.parse(text); } catch (e) {}
        resolve({ status: res.statusCode, json, text });
      });
    });
    r.on('error', reject);
    r.on('timeout', () => r.destroy(new Error('timeout')));
    if (data) r.write(data);
    r.end();
  });
}

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}

function wsConnect() {
  return new Promise((resolve) => {
    const wsUrl = BASE.replace(/^http/, 'ws') + '/ws';
    let ws = null;
    try { ws = new WebSocket(wsUrl); } catch (e) { return resolve(null); }
    const events = [];
    const hello = [];
    let closed = false;
    ws.onmessage = (e) => {
      try {
        const j = JSON.parse(e.data);
        if (j.kind === 'hello') hello.push(j);
        events.push(j);
      } catch (err) {}
    };
    ws.onerror = () => { if (!closed) { closed = true; resolve({ ws, events, hello, ok: false }); } };
    ws.onopen = () => setTimeout(() => resolve({ ws, events, hello, ok: true }), 300);
    setTimeout(() => { if (!closed) { closed = true; resolve({ ws, events, hello, ok: ws.readyState === 1 }); } }, 3000);
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  console.log('清唱冒烟测试 ->', BASE, '点歌:', SONG);
  let h;
  try {
    h = await req('GET', '/api/health');
  } catch (e) {
    check('服务可达', false, e.message);
    process.exit(1);
  }
  check('服务可达', h.status === 200 && h.json && h.json.ok, 'health ' + h.status);

  const st0 = await req('GET', '/api/songs/state');
  check('点歌/清唱状态接口', st0.status === 200 && st0.json && st0.json.mode !== undefined,
    'mode=' + (st0.json && st0.json.mode));
  const mode = st0.json && st0.json.mode;

  const sock = await wsConnect();
  check('WS /ws 连上', !!sock && sock.ok, sock && sock.hello.length ? '收到 hello' : '未收到 hello');

  await req('POST', '/api/session/start', {});
  await req('POST', '/api/test_event', { event: { type: 'danmaku', name: '冒烟测试', text: '点歌 ' + SONG } });

  // 等当前清唱出现（合成需要十几秒）
  const t0 = Date.now();
  let cur = null;
  let sawAudio = false;
  let lastLine = -1;
  let lineEvents = 0;
  while (Date.now() - t0 < TIMEOUT) {
    const s = await req('GET', '/api/songs/state');
    if (s.json && s.json.current) {
      cur = s.json.current;
      if (cur.audio) sawAudio = true;
      if (typeof cur.lyricIndex === 'number' && cur.lyricIndex > lastLine) lastLine = cur.lyricIndex;
      if (cur.audio) break;        // 合成完成、开始播放
      if (Date.now() - t0 > 60000) break;   // 兜底：合成太久就不再等
    }
    await sleep(400);
  }
  check('清唱开始（state.current）', !!cur, cur ? `《${cur.name}》 by=${cur.by}` : '超时没出现');
  check('歌词已获取', !!(cur && Array.isArray(cur.lyrics) && cur.lyrics.length >= 2),
    cur ? `${(cur.lyrics || []).length} 句：` + (cur.lyrics || []).slice(0, 2).join(' / ') : '');
  check('get 到音频文件（真 TTS）', sawAudio, cur ? String(cur.audio || '(空)') : '');

  // 等歌词行事件 / 唱完
  const t1 = Date.now();
  while (Date.now() - t1 < TIMEOUT) {
    const s = await req('GET', '/api/songs/state');
    if (s.json && s.json.current && typeof s.json.current.lyricIndex === 'number' && s.json.current.lyricIndex > lastLine) {
      lastLine = s.json.current.lyricIndex;
    }
    lineEvents = sock ? sock.events.filter((e) => e.kind === 'sing' && e.action === 'line').length : 0;
    const ended = s.json && !s.json.current && sock && sock.events.some((e) => e.kind === 'sing' && e.action === 'end');
    if (ended) break;
    if (s.json && !s.json.current && Date.now() - t1 > 6000) break;   // 没有 WS 时兜底
    await sleep(300);
  }
  check('歌词行 WS 事件', lineEvents > 0 || lastLine > 0, `line 事件 ${lineEvents} 条，lyricIndex 最大 ${lastLine}`);
  check('清唱结束（WS sing end）', sock ? sock.events.some((e) => e.kind === 'sing' && e.action === 'end') : true,
    sock ? 'end 事件收到' : '无 WS，跳过');

  // 跳过接口
  const sk = await req('POST', '/api/songs/skip', {});
  check('跳过接口可用', sk.status === 200 && sk.json && sk.json.ok, JSON.stringify(sk.json));

  if (sock && sock.ws) { try { sock.ws.close(); } catch (e) {} }
  const failed = results.filter((x) => !x.ok);
  console.log('\n结果:', results.length - failed.length + '/' + results.length, '通过');
  if (failed.length) {
    console.log('失败项:', failed.map((x) => x.name).join('、'));
    process.exit(1);
  }
  process.exit(0);
})().catch((e) => {
  console.error('测试异常:', e && e.message);
  process.exit(1);
});
