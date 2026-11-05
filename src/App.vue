<script setup>
import { onMounted, onUnmounted, ref } from 'vue';

const API = location.port === '5173' ? 'http://127.0.0.1:8788' : location.origin;

const running = ref(false);
const mode = ref('relay');
const uptime = ref(0);
const stats = ref({ enter: 0, danmaku: 0, gift: 0, follow: 0, like: 0, say: 0 });
const feed = ref([]);
const cfgText = ref('');
const sayText = ref('');
const toast = ref('');
let timer = null;
let ws = null;

async function api(path, body) {
  const r = await fetch(API + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  });
  return r.json();
}

function showToast(msg) {
  toast.value = msg;
  setTimeout(() => (toast.value = ''), 1800);
}

async function refresh() {
  try {
    const h = await (await fetch(API + '/api/health')).json();
    running.value = !!h.running;
    mode.value = h.mode;
    uptime.value = h.uptime || 0;
    if (h.stats) stats.value = h.stats;
    feed.value = await (await fetch(API + '/api/recent')).json();
  } catch (e) {}
}

async function loadCfg() {
  const j = await (await fetch(API + '/api/config')).json();
  cfgText.value = JSON.stringify(j, null, 2);
}

async function saveCfg() {
  try {
    await api('/api/config', JSON.parse(cfgText.value));
    showToast('配置已保存');
  } catch (e) {
    showToast('JSON 错误: ' + e.message);
  }
}

async function say() {
  if (!sayText.value.trim()) return;
  await api('/api/say', { text: sayText.value.trim() });
  sayText.value = '';
}

async function testEvent(kind) {
  const map = {
    danmaku: { type: 'danmaku', name: '测试观众', text: '你好呀' },
    enter: { type: 'enter', name: '进房测试鱼' },
    gift: { type: 'gift', name: '送礼测试鱼', gift: '小心心', num: 1, value: 0.1 }
  };
  await api('/api/test_event', { event: map[kind] });
}

function connectWs() {
  try {
    ws = new WebSocket(API.replace(/^http/, 'ws') + '/ws');
    ws.onmessage = () => refresh();
    ws.onclose = () => setTimeout(connectWs, 3000);
  } catch (e) {}
}

onMounted(async () => {
  await loadCfg();
  await refresh();
  connectWs();
  timer = setInterval(refresh, 1500);
});
onUnmounted(() => {
  clearInterval(timer);
  try { ws && ws.close(); } catch (e) {}
});
</script>

<template>
  <div class="page">
    <header>
      <h1>🐳 鲸语 · 移植版</h1>
      <span class="pill" :class="running ? 'on' : 'off'">{{ running ? '会话进行中' : '未开始' }}</span>
      <span class="pill">mode: {{ mode }}</span>
      <span class="muted">{{ uptime }}s</span>
      <nav class="muted">
        <a :href="API + '/overlay'" target="_blank">弹幕层</a>
        <a :href="API + '/songs'" target="_blank">歌单</a>
        <a :href="API + '/dashboard'" target="_blank">无构建面板</a>
      </nav>
    </header>

    <main>
      <section class="card">
        <h2>会话</h2>
        <div class="row">
          <button @click="api('/api/session/start', {}).then(refresh)">开始</button>
          <button class="warn" @click="api('/api/session/stop', {}).then(refresh)">结束</button>
          <button @click="api('/api/stats/reset', {}).then(refresh)">清空统计</button>
        </div>
        <div class="stats">
          <div v-for="(v, k) in stats" :key="k" class="stat">
            <b>{{ v }}</b><span>{{ { enter: '进房', danmaku: '弹幕', gift: '礼物', follow: '关注', like: '点赞', say: '说话' }[k] || k }}</span>
          </div>
        </div>
        <h2>手动说话</h2>
        <div class="row">
          <input v-model="sayText" placeholder="输入要说的内容" @keyup.enter="say" />
          <button @click="say">说</button>
        </div>
        <h2>测试事件</h2>
        <div class="row">
          <button @click="testEvent('danmaku')">弹幕</button>
          <button @click="testEvent('enter')">进房</button>
          <button @click="testEvent('gift')">礼物</button>
        </div>
      </section>

      <section class="card">
        <h2>实时事件</h2>
        <ul>
          <li v-for="(x, i) in feed" :key="i"><span class="k">{{ x.t }} {{ x.kind }}</span><b v-if="x.name">{{ x.name }}：</b>{{ x.text }}</li>
        </ul>
      </section>

      <section class="card wide">
        <h2>配置 JSON</h2>
        <textarea v-model="cfgText"></textarea>
        <div class="row">
          <button @click="saveCfg">保存配置</button>
          <button @click="loadCfg">重新读取</button>
          <span class="muted">TTS → 9881，点歌 → tools/logs/song_requests.jsonl</span>
        </div>
      </section>
    </main>

    <div v-if="toast" class="toast">{{ toast }}</div>
  </div>
</template>

<style>
* { box-sizing: border-box; }
.page { min-height: 100vh; background: #0b1220; color: #e6f0ff; }
header { display: flex; align-items: center; gap: 14px; padding: 14px 20px; background: #101a2e; border-bottom: 1px solid #22314d; }
header h1 { font-size: 18px; margin: 0; }
nav { margin-left: auto; display: flex; gap: 12px; }
a { color: #7fb6ff; text-decoration: none; font-size: 13px; }
.pill { font-size: 12px; padding: 3px 10px; border-radius: 999px; background: #1b2a45; color: #9fc4ff; }
.pill.on { background: #123a2a; color: #6ef2b0; }
.pill.off { background: #3a1a1f; color: #ff8f9a; }
main { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 18px 20px; }
.card { background: #101a2e; border: 1px solid #22314d; border-radius: 12px; padding: 14px; }
.card.wide { grid-column: 1 / -1; }
.card h2 { font-size: 14px; margin: 12px 0 8px; color: #9fc4ff; font-weight: 600; }
.card h2:first-child { margin-top: 0; }
.row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
button { background: #1d3a66; color: #dbeaff; border: 1px solid #2f5287; border-radius: 8px; padding: 7px 14px; cursor: pointer; font-size: 13px; }
button:hover { background: #25497e; }
button.warn { background: #4a2230; border-color: #7d3350; }
input, textarea { width: 100%; background: #0c1526; color: #dbeaff; border: 1px solid #22314d; border-radius: 8px; padding: 8px 10px; font-size: 13px; }
textarea { min-height: 260px; font-family: Consolas, monospace; line-height: 1.5; }
.row input { flex: 1; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 10px 0; }
.stat { background: #0c1526; border-radius: 10px; padding: 10px; text-align: center; }
.stat b { display: block; font-size: 22px; }
.stat span { font-size: 12px; color: #7f9ac0; }
ul { list-style: none; margin: 0; padding: 0; max-height: 380px; overflow: auto; }
li { padding: 6px 0; border-bottom: 1px dashed #1d2a42; font-size: 13px; color: #c9dcf5; }
li .k { color: #7f9ac0; font-size: 12px; margin-right: 6px; }
.muted { color: #7f9ac0; font-size: 12px; }
.toast { position: fixed; right: 20px; bottom: 20px; background: #1d3a66; padding: 10px 16px; border-radius: 10px; font-size: 13px; }
</style>
