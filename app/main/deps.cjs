/**
 * 运行环境检测 / 一键启动（TTS · OBS · Ollama · Python · 真唱引擎）
 *
 * 零 npm 依赖：只用 net / fs / os / path / child_process。
 * 由 core.cjs 调用：
 *   const services = await deps.probe(settings)        // 探测所有依赖服务
 *   const r        = await deps.start(key, settings)   // 一键启动
 *   deps.stop(key)                                     // 停止（只停本进程启动的）
 *
 * 启动命令优先级：config.deps.<key>_start_cmd（用户配置） > 自动发现的本机路径 > 没有就报错。
 * 之所以要"自动发现"：本机 TTS 脚本在仓库旁边的 whaletalk 目录、OBS 在 D 盘、Ollama 在
 * LOCALAPPDATA，写死路径换台机器就废；发现不了时提示用户去控制台「运行环境」里填。
 */
const net = require('net');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn, spawnSync } = require('child_process');

const CHILDREN = new Map(); // key -> { pid, cmd, startedAt, log }

// ---------------- 基础工具 ----------------

/** 拆 http://host:port → {host, port}，没有协议头也能拆。 */
function parseUrlParts(raw, defHost = '127.0.0.1', defPort = 0) {
  try {
    const s = String(raw || '');
    const u = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(s) ? s : 'http://' + s);
    return { host: u.hostname || defHost, port: Number(u.port || defPort) };
  } catch (e) {
    return { host: defHost, port: defPort };
  }
}

/** TCP 端口是否有人监听（连得上就算在线）。 */
function portOpen(host, port, timeout = 700) {
  return new Promise((resolve) => {
    const p = Number(port);
    if (!p || p <= 0) return resolve(false);
    const sock = new net.Socket();
    let done = false;
    const finish = (ok) => {
      if (done) return;
      done = true;
      try { sock.destroy(); } catch (e) {}
      resolve(ok);
    };
    sock.setTimeout(timeout);
    sock.once('connect', () => finish(true));
    sock.once('timeout', () => finish(false));
    sock.once('error', () => finish(false));
    try { sock.connect(p, host || '127.0.0.1'); } catch (e) { finish(false); }
  });
}

/** 返回第一个存在的路径（空字符串 = 都没找到）。 */
function firstExisting(cands) {
  for (const c of cands) {
    if (!c) continue;
    try { if (fs.existsSync(c)) return c; } catch (e) {}
  }
  return '';
}

function quote(p) {
  return '"' + String(p).replace(/"/g, '') + '"';
}

/**
 * 把启动命令字符串拆成 exe + args（自己解析，避免 cmd.exe 的引号地狱）。
 * 支持：`"C:\Program Files\x\y.exe" arg1 arg2`、`powershell -File "D:\a b\x.ps1"`。
 */
function splitCommand(cmd) {
  const s = String(cmd || '').trim();
  const parts = [];
  let cur = '';
  let q = null;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (q) {
      if (ch === q) q = null;
      else cur += ch;
      continue;
    }
    if (ch === '"' || ch === "'") { q = ch; continue; }
    if (/\s/.test(ch)) {
      if (cur) { parts.push(cur); cur = ''; }
      continue;
    }
    cur += ch;
  }
  if (cur) parts.push(cur);
  return { exe: parts[0] || '', args: parts.slice(1) };
}

// ---------------- 自动发现本机路径 ----------------

/** TTS 启动脚本：优先读 scripts/start-all.ps1 里写的工作目录，再按常见目录结构找。 */
function detectTtsScript(root) {
  const cands = [];
  // ① 仓库自带的一键启动脚本里写着 TTS 工作目录（$Work = 'D:\...\whaletalk'），照抄即可
  try {
    const starter = path.join(root, 'scripts', 'start-all.ps1');
    if (fs.existsSync(starter)) {
      const text = fs.readFileSync(starter, 'utf8');
      const m = /^\s*\$Work\s*=\s*['"]([^'"]+)['"]/m.exec(text);
      if (m && m[1]) cands.push(path.join(m[1], 'gsv_gpu', 'run_gpu_api.ps1'));
    }
  } catch (e) {}
  // ② 常见目录结构：仓库同级 / 上级的 whaletalk
  cands.push(path.resolve(root, '..', 'whaletalk', 'gsv_gpu', 'run_gpu_api.ps1'));
  cands.push(path.resolve(root, '..', '..', 'whaletalk', 'gsv_gpu', 'run_gpu_api.ps1'));
  cands.push(path.resolve(root, 'extras', 'tts', 'run_tts.ps1'));
  return firstExisting(cands);
}

function detectObsExe() {
  return firstExisting([
    'D:\\obs-studio\\bin\\64bit\\obs64.exe',
    'C:\\Program Files\\obs-studio\\bin\\64bit\\obs64.exe',
    'C:\\Program Files (x86)\\obs-studio\\bin\\64bit\\obs64.exe'
  ]);
}

function detectOllamaExe() {
  const la = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
  return firstExisting([
    path.join(la, 'Programs', 'Ollama', 'ollama.exe'),
    path.join(la, 'AMD', 'AI_Bundle', 'Ollama', 'ollama.exe'),
    path.join(la, 'Ollama', 'ollama.exe')
  ]);
}

/** Python 是否可用 + 版本（跑一次 --version，最多 4 秒）。 */
function pythonInfo(pythonPath) {
  let exe = pythonPath || 'python';
  // 'python' 这类裸命令，用 where 解析成真实路径（面板上好看也好排查）
  if (!/[\\/]/.test(exe)) {
    try {
      const w = spawnSync('where', [exe], { timeout: 3000, windowsHide: true, encoding: 'utf8' });
      const first = String(w.stdout || '').split(/\r?\n/).map((s) => s.trim()).filter(Boolean)[0];
      if (first) exe = first;
    } catch (e) {}
  }
  try {
    const r = spawnSync(exe, ['--version'], { timeout: 4000, windowsHide: true, encoding: 'utf8' });
    const out = String((r.stdout || '') + (r.stderr || '')).trim();
    if (r.status === 0 && /Python\s+\d/i.test(out)) return { ok: true, path: exe, version: out.split(/\r?\n/)[0] };
    return { ok: false, path: exe, version: out || ('exit ' + r.status) };
  } catch (e) {
    return { ok: false, path: exe, version: String(e.message || e) };
  }
}

// ---------------- 启动命令解析 ----------------

/** 某个服务该用什么命令启动；返回 null 表示既没配置也没发现。 */
function resolveStart(key, settings) {
  const s = settings || {};
  const c = s.deps || {};
  if (key === 'tts') {
    if (c.tts_start_cmd) return { cmd: String(c.tts_start_cmd), source: '配置' };
    const script = detectTtsScript(s.root || process.cwd());
    if (script) return { cmd: `powershell -NoProfile -ExecutionPolicy Bypass -File ${quote(script)}`, source: '自动发现' };
  }
  if (key === 'obs') {
    if (c.obs_start_cmd) return { cmd: String(c.obs_start_cmd), source: '配置' };
    const exe = detectObsExe();
    if (exe) return { cmd: quote(exe), cwd: path.dirname(exe), source: '自动发现' };
  }
  if (key === 'ollama') {
    if (c.ollama_start_cmd) return { cmd: String(c.ollama_start_cmd), source: '配置' };
    const exe = detectOllamaExe();
    if (exe) return { cmd: `${quote(exe)} serve`, source: '自动发现' };
  }
  if (key === 'douyin') {
    if (c.douyin_start_cmd) return { cmd: String(c.douyin_start_cmd), source: '配置' };
    const script = path.join(s.root || process.cwd(), 'extras', 'douyin', 'relay.js');
    if (fs.existsSync(script)) {
      const dj = s.douyin || {};
      const parts = [quote(process.execPath), quote(script), '--api', `http://127.0.0.1:${s.port || 8788}`];
      if (dj.rid) parts.push('--rid', quote(dj.rid));
      if (dj.url) parts.push('--url', quote(dj.url));
      // 打包后的 Electron 里 process.execPath 是 electron.exe：加这个环境变量让它当 Node 跑；
      // 纯 node server 启动时该变量无副作用（Node 会忽略它）
      const env = /electron/i.test(path.basename(process.execPath)) ? { ELECTRON_RUN_AS_NODE: '1' } : null;
      return { cmd: parts.join(' '), env, source: '自动发现' };
    }
  }
  return null;
}

// ---------------- 探测 ----------------

/**
 * 探测所有依赖服务。
 * settings = {
 *   root, pythonPath, ttsUrl, obs: {host, port}, ollamaBase, deps: {...}, sing: {ace:{python, script, svc_python, svc_script}}
 * }
 */
async function probe(settings) {
  const s = settings || {};
  const root = s.root || process.cwd();

  const tts = parseUrlParts(s.ttsUrl || 'http://127.0.0.1:9881', '127.0.0.1', 9881);
  // GPT-SoVITS 引擎（9882）：代理 9881 后面挂的推理服务，代理在线时顺带看一眼
  const enginePort = tts.port === 9881 ? 9882 : tts.port + 1;
  const obsCfg = s.obs || {};
  const obsHost = obsCfg.host || '127.0.0.1';
  const obsPort = Number(obsCfg.port || 4455);
  const ollama = parseUrlParts(s.ollamaBase || 'http://127.0.0.1:11434', '127.0.0.1', 11434);
  // 只有 AI 接口本身就是本机地址时才认为「本地模型模式」；用 DeepSeek 云端时固定探测本机 11434
  const aiHostIsLocal = /^(127\.0\.0\.1|localhost|::1)$/i.test(ollama.host || '');
  const ollamaHost = aiHostIsLocal ? ollama.host : '127.0.0.1';
  const ollamaPort = aiHostIsLocal && ollama.port ? ollama.port : 11434;

  const [ttsOk, engineOk, obsOk, ollamaOk] = await Promise.all([
    portOpen(tts.host, tts.port),
    portOpen(tts.host, enginePort),
    portOpen(obsHost, obsPort),
    portOpen(ollamaHost, ollamaPort)
  ]);

  const py = pythonInfo(s.pythonPath);
  const ace = (s.sing && s.sing.ace) || {};
  const acePython = ace.python && fs.existsSync(String(ace.python).replace(/\//g, '\\'));
  const aceScript = ace.script && fs.existsSync(String(ace.script).replace(/\//g, '\\'));
  const aceOk = !!(acePython && aceScript);

  const ttsStart = resolveStart('tts', { root, deps: s.deps });
  const obsStart = resolveStart('obs', { root, deps: s.deps });
  const ollamaStart = resolveStart('ollama', { root, deps: s.deps });
  const douyinStart = resolveStart('douyin', { root, deps: s.deps, douyin: s.douyin, port: s.port });
  const dj = s.douyin || {};
  const douyinOk = !!(dj.active || (dj.ingestAgo !== null && dj.ingestAgo !== undefined && dj.ingestAgo < 120));

  const svc = (o) => Object.assign({ managed: false, startable: false, port: 0, detail: '', hint: '' }, o);

  return [
    svc({
      key: 'tts', name: 'GPT-SoVITS 语音（说话 / 清唱）', icon: '🗣', ok: ttsOk,
      port: tts.port, startable: !!ttsStart, managed: CHILDREN.has('tts'),
      detail: ttsOk
        ? `代理 ${tts.host}:${tts.port} 在线${engineOk ? `，引擎 ${enginePort} 在线` : `（引擎 ${enginePort} 没响应，合成可能失败）`}`
        : `端口 ${tts.port} 没有服务在监听`,
      hint: '没它 AI 不会出声；首次启动要加载模型 1~3 分钟',
      command: ttsStart ? ttsStart.cmd : ''
    }),
    svc({
      key: 'douyin', name: '抖音弹幕中继（Chrome 抓弹幕）', icon: '📺', ok: douyinOk,
      startable: !!douyinStart, managed: CHILDREN.has('douyin'),
      detail: douyinOk
        ? `中继在跑${dj.ingestAgo !== null && dj.ingestAgo !== undefined ? `，最近一条事件 ${dj.ingestAgo}s 前` : ''}${dj.lastCount ? `（上次 ${dj.lastCount} 个事件）` : ''}`
        : (dj.heartbeatAgo !== null && dj.heartbeatAgo !== undefined
          ? `中继进程还在（心跳 ${dj.heartbeatAgo}s 前），但最近没收到弹幕`
          : '中继没在跑'),
      hint: '点一键启动会开一个静音 Chrome 抓直播间聊天 DOM；首次要扫码登录，登录状态会存下来',
      command: douyinStart ? douyinStart.cmd : ''
    }),
    svc({
      key: 'obs', name: 'OBS Studio（推流 / 叠加层）', icon: '🎬', ok: obsOk,
      port: obsPort, startable: !!obsStart, managed: CHILDREN.has('obs'),
      detail: obsOk ? `WebSocket ${obsHost}:${obsPort} 已连接` : `WebSocket ${obsHost}:${obsPort} 连不上`,
      hint: 'OBS 里要开启「工具 → WebSocket 服务器设置 → 启用」',
      command: obsStart ? obsStart.cmd : ''
    }),
    svc({
      key: 'ollama', name: '本地模型 Ollama（省钱模式）', icon: '🧠', ok: ollamaOk,
      port: ollamaPort, startable: !!ollamaStart, managed: CHILDREN.has('ollama'),
      detail: ollamaOk ? `127.0.0.1:${ollamaPort} 在线` : `127.0.0.1:${ollamaPort} 连不上`,
      hint: '把 AI 切换成本机模型时用（docs/本地模型.md），用 DeepSeek 云端可不启动',
      command: ollamaStart ? ollamaStart.cmd : ''
    }),
    svc({
      key: 'python', name: 'Python（清唱 / 脚本管线）', icon: '🐍', ok: py.ok,
      detail: py.ok ? `${py.version}  ·  ${py.path}` : `跑不起来：${py.version}`,
      hint: '清唱、星露谷脚本都靠它；没有就去装 Python 3.12'
    }),
    svc({
      key: 'sing', name: '真唱引擎 ACE-Step + Seed-VC（可选）', icon: '🎤', ok: aceOk,
      detail: aceOk ? `已部署：${ace.python}` : '未部署（不影响默认 TTS 清唱）',
      hint: '真唱按 docs/清唱.md §6.5 单独部署；约 3~4 分钟/首'
    })
  ];
}

// ---------------- 启动 / 停止 ----------------

/**
 * 一键启动某个服务（命令走 cmd.exe /c，兼容 exe 和 ps1；日志写 logs/deps-<key>.log）。
 * 返回 { ok, started, running, pid, command, source, log, service }
 */
async function start(key, settings, log) {
  const s = settings || {};
  const say = typeof log === 'function' ? log : () => {};
  if (!['tts', 'obs', 'ollama', 'douyin'].includes(key)) {
    return { ok: false, error: `不支持一键启动：${key}（Python / 真唱引擎需要手动装）` };
  }
  const spec = resolveStart(key, { root: s.root, deps: s.deps, douyin: s.douyin, port: s.port });
  if (!spec) {
    return { ok: false, error: `没找到 ${key} 的启动方式：请到「运行环境」里填启动命令，或手动启动` };
  }

  const logDir = s.logDir || path.join(s.root || process.cwd(), 'logs');
  try { fs.mkdirSync(logDir, { recursive: true }); } catch (e) {}
  const logFile = path.join(logDir, `deps-${key}.log`);
  let out = null;
  try {
    out = fs.openSync(logFile, 'a');
    fs.writeSync(out, `\n===== ${new Date().toISOString()} 启动 ${key} =====\n${spec.cmd}\n`);
  } catch (e) {}

  let child;
  try {
    const parsed = splitCommand(spec.cmd);
    if (!parsed.exe) return { ok: false, error: '启动命令为空' };
    child = spawn(parsed.exe, parsed.args, {
      cwd: spec.cwd || s.root || process.cwd(),
      detached: true,
      stdio: ['ignore', out || 'ignore', out || 'ignore'],
      windowsHide: true,
      env: spec.env ? Object.assign({}, process.env, spec.env) : process.env
    });
    child.on('error', (e) => {
      say(`启动 ${key} 失败: ${e.message}`);
      try { fs.appendFileSync(logFile, `启动失败: ${e.message}\n`); } catch (err) {}
    });
    child.unref();
  } catch (e) {
    return { ok: false, error: `启动失败：${e.message}` };
  } finally {
    if (out !== null) { try { fs.closeSync(out); } catch (e) {} }
  }
  if (!child.pid) return { ok: false, error: '进程没起来（命令找不到？）', command: spec.cmd };

  CHILDREN.set(key, { pid: child.pid, cmd: spec.cmd, startedAt: Date.now(), log: logFile });
  say(`启动依赖 ${key}: ${spec.cmd}（pid ${child.pid}，日志 ${logFile}）`);

  // 给服务一点启动时间再探测（TTS 模型加载要几十秒，这里只判断"端口起来了"）
  await new Promise((r) => setTimeout(r, key === 'tts' ? 2500 : 1500));
  let services = [];
  try { services = await probe(s); } catch (e) {}
  const svc = services.find((x) => x.key === key) || null;
  if (svc) svc.managed = CHILDREN.has(key);
  return {
    ok: true, started: true, running: !!(svc && svc.ok), pid: child.pid,
    command: spec.cmd, source: spec.source, log: logFile, service: svc
  };
}

/** 停止本进程启动过的服务（不碰用户自己开的）。 */
function stop(key) {
  const rec = CHILDREN.get(key);
  if (!rec) return { ok: false, error: '这个服务不是鲸live启动的，面板不替你关（避免误杀你自己开的）' };
  try {
    spawnSync('taskkill', ['/PID', String(rec.pid), '/T', '/F'], { windowsHide: true });
  } catch (e) {}
  CHILDREN.delete(key);
  return { ok: true, stopped: rec.pid };
}

/** 当前由鲸live启动、还没停的服务。 */
function managed() {
  return Array.from(CHILDREN.entries()).map(([k, v]) => ({ key: k, pid: v.pid, cmd: v.cmd, startedAt: v.startedAt }));
}

module.exports = { probe, start, stop, managed, parseUrlParts, portOpen, detectTtsScript, detectObsExe, detectOllamaExe };
