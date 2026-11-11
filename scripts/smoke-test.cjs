#!/usr/bin/env node
/**
 * 鲸live 冒烟测试：临时起一个隔离的核心服务（临时数据目录、随机端口），
 * 把关键页面/接口全部打一遍，任何一项不对就以非 0 退出。
 *
 * 用法：
 *   node scripts/smoke-test.cjs            # 跑完自动关服务
 *   node scripts/smoke-test.cjs --keep     # 跑完保留服务（调试用，会打印端口）
 *   node scripts/smoke-test.cjs --port 8799
 *
 * 设计上的两点：
 * - WT_DATA 指向临时目录：logs/config 不污染仓库，也不会动你的真实配置；
 * - WT_NO_AUDIO=1：不会真的去调 TTS / 放声音。
 */
'use strict';

const fs = require('fs');
const os = require('os');
const net = require('net');
const path = require('path');
const { spawn, execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const args = process.argv.slice(2);
const keep = args.includes('--keep');
const argVal = (name, def) => {
  const i = args.indexOf('--' + name);
  return i >= 0 && args[i + 1] ? args[i + 1] : def;
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, '127.0.0.1', () => {
      const p = srv.address().port;
      srv.close(() => resolve(p));
    });
    srv.on('error', reject);
  });
}

async function waitHealth(base, timeoutMs = 20000) {
  const t0 = Date.now();
  let lastErr = '';
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch(base + '/api/health');
      if (r.ok) return await r.json();
      lastErr = 'HTTP ' + r.status;
    } catch (e) {
      lastErr = e.message;
    }
    await sleep(300);
  }
  throw new Error('服务没起来：' + lastErr);
}

async function get(base, p, opts) {
  const r = await fetch(base + p, opts);
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch (e) {}
  return { status: r.status, text, json, type: r.headers.get('content-type') || '' };
}

async function main() {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8').replace(/^\uFEFF/, ''));
  const port = Number(argVal('port', 0)) || (await freePort());
  const base = 'http://127.0.0.1:' + port;
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'jinglive-smoke-'));
  const results = [];
  const check = async (name, fn) => {
    try {
      const extra = await fn();
      results.push({ name, ok: true, extra: extra || '' });
    } catch (e) {
      results.push({ name, ok: false, extra: String((e && e.message) || e) });
    }
  };

  const child = spawn(process.execPath, [path.join(ROOT, 'server', 'index.cjs'), '--port', String(port)], {
    cwd: ROOT,
    env: Object.assign({}, process.env, {
      WT_DATA: dataDir,
      WT_NO_AUDIO: '1',
      WT_CONFIG: path.join(dataDir, 'config.json'),
      DEEPLIVE_ENV: path.join(dataDir, '.env')   // 不读仓库 .env，避免把真实 key/地址带进来
    }),
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true
  });
  let out = '';
  child.stdout.on('data', (d) => { out += d.toString(); });
  child.stderr.on('data', (d) => { out += d.toString(); });

  try {
    const health = await waitHealth(base);
    console.log(`\n🐳 鲸live 冒烟测试  ${base}  (v${health.version || '?'}, 数据目录 ${dataDir})\n`);

    await check('健康检查 /api/health', async () => {
      if (!health.ok) throw new Error('ok!=true');
      if (health.version !== pkg.version) throw new Error(`版本不一致：health=${health.version} package.json=${pkg.version}`);
      return 'v' + health.version;
    });

    await check('配置 /api/config 字段齐全', async () => {
      const r = await get(base, '/api/config');
      if (r.status !== 200 || !r.json) throw new Error('HTTP ' + r.status);
      for (const k of ['ai', 'tts', 'sing', 'deps', 'live2d', 'events', 'obs', 'douyin']) {
        if (!(k in r.json)) throw new Error('缺字段 ' + k);
      }
      return 'keys=' + Object.keys(r.json).length;
    });

    await check('运行环境 /api/deps 六个服务', async () => {
      const r = await get(base, '/api/deps');
      if (r.status !== 200 || !r.json) throw new Error('HTTP ' + r.status);
      const keys = (r.json.services || []).map((s) => s.key).sort();
      const want = ['douyin', 'obs', 'ollama', 'python', 'sing', 'tts'];
      for (const k of want) if (!keys.includes(k)) throw new Error('缺服务 ' + k);
      return keys.join(',');
    });

    await check('皮套模型 /api/live2d/models', async () => {
      const r = await get(base, '/api/live2d/models');
      if (!r.json || !Array.isArray(r.json.models) || !r.json.models.length) throw new Error('没有模型');
      if (!/\.model3\.json$/.test(r.json.current || '')) throw new Error('current 不是 model3.json');
      return r.json.models.length + ' 个，当前 ' + r.json.current;
    });

    await check('抖音状态 /api/douyin/health', async () => {
      const r = await get(base, '/api/douyin/health');
      if (!r.json || !r.json.relay) throw new Error('缺 relay 字段');
      for (const k of ['active', 'heartbeatAgo', 'ingestAgo']) if (!(k in r.json.relay)) throw new Error('relay 缺 ' + k);
      return 'active=' + r.json.relay.active;
    });

    await check('清唱状态 /api/songs/state', async () => {
      const r = await get(base, '/api/songs/state');
      if (!r.json || !Array.isArray(r.json.queue)) throw new Error('queue 不是数组');
      return 'mode=' + r.json.mode;
    });

    await check('本场数据落盘 /api/stats/history', async () => {
      await get(base, '/api/session/start', { method: 'POST' });
      const health2 = await get(base, '/api/session/stop', { method: 'POST' });
      if (health2.status !== 200) throw new Error('session/stop HTTP ' + health2.status);
      const r = await get(base, '/api/stats/history');
      if (!r.json || !Array.isArray(r.json.items) || !r.json.items.length) throw new Error('没有历史记录');
      if (!r.json.items[0].stats) throw new Error('历史记录缺 stats');
      return r.json.items.length + ' 条';
    });

    const pages = ['/', '/dashboard', '/console', '/overlay', '/songs', '/stats', '/subtitle', '/live2d', '/stardew'];
    for (const p of pages) {
      await check('页面 ' + p, async () => {
        const r = await get(base, p);
        if (r.status !== 200) throw new Error('HTTP ' + r.status);
        if (!/text\/html/.test(r.type)) throw new Error('不是 html: ' + r.type);
        return r.text.length + 'B';
      });
    }

    await check('控制台是 Vue 构建产物（/console）', async () => {
      const r = await get(base, '/console');
      if (!/assets\/index-[^"']+\.js/.test(r.text)) throw new Error('没引用 assets/index-*.js（像旧免构建版）');
      const m = /src="\.\/(assets\/[^"]+)"/.exec(r.text);
      if (!m) throw new Error('找不到资产路径');
      return m[1];
    });

    await check('静态资产 /assets/*', async () => {
      const r0 = await get(base, '/console');
      const m = /src="\.\/(assets\/[^"]+)"/.exec(r0.text);
      if (!m) throw new Error('找不到资产');
      const r = await get(base, '/' + m[1]);
      if (r.status !== 200 || r.text.length < 1000) throw new Error('资产 HTTP ' + r.status + ' len=' + r.text.length);
      return r.text.length + 'B';
    });

    await check('404 /nope', async () => {
      const r = await get(base, '/nope');
      if (r.status !== 404) throw new Error('HTTP ' + r.status);
      return 'OK';
    });
  } catch (e) {
    results.push({ name: '启动/连接', ok: false, extra: String((e && e.message) || e) });
  } finally {
    if (!keep) {
      try {
        if (process.platform === 'win32') execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true });
        else child.kill('SIGTERM');
      } catch (e) {}
      try { fs.rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
    }
  }

  const bad = results.filter((r) => !r.ok);
  console.log('检查结果：');
  for (const r of results) console.log(`  ${r.ok ? '✅' : '❌'} ${r.name}${r.extra ? '  — ' + r.extra : ''}`);
  console.log(`\n${results.length - bad.length}/${results.length} 通过` + (keep ? `（服务保留在 ${base}）` : ''));
  if (bad.length) {
    console.error('\n失败详情：');
    for (const r of bad) console.error('  ❌ ' + r.name + ': ' + r.extra);
    if (out.trim()) console.error('\n服务输出：\n' + out.trim().split('\n').slice(-15).join('\n'));
    process.exit(1);
  }
  process.exit(0);
}

main().catch((e) => {
  console.error('[FATAL] ' + (e && e.stack ? e.stack : e));
  process.exit(1);
});
