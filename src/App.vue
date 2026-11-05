<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { invoke } from './api';

const API = (typeof window !== 'undefined' && window.whale && window.whale.base)
  || (location.port === '5173' ? 'http://127.0.0.1:8788' : location.origin);

const tabs = [
  { key: 'session', label: '会话' },
  { key: 'events', label: '事件' },
  { key: 'config', label: '配置' },
  { key: 'live2d', label: '皮套' },
  { key: 'overlay', label: '叠加层' },
  { key: 'songs', label: '歌单' }
];
const tab = ref('session');
const STAT_LABEL = { enter: '进房', danmaku: '弹幕', gift: '礼物', follow: '关注', like: '点赞', say: '说话' };

const running = ref(false);
const mode = ref('relay');
const uptime = ref(0);
const stats = ref({});
const feed = ref([]);
const cfgText = ref('');
const sayText = ref('');
const toast = ref('');
const songState = ref({ current: null, queue: [] });
const iframeKey = ref(0);
let timer = null;
let songTimer = null;

const statList = computed(() =>
  Object.keys(STAT_LABEL).map((k) => ({ key: k, label: STAT_LABEL[k], value: (stats.value && stats.value[k]) || 0 }))
);

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
  setTimeout(() => (toast.value = ''), 1600);
}
async function refresh() {
  try {
    const st = await invoke('status');
    running.value = !!st.running;
    mode.value = st.mode || 'relay';
    uptime.value = st.uptime || 0;
    stats.value = st.stats || {};
    feed.value = st.recent || [];
  } catch (e) {}
}
async function refreshSongs() {
  try {
    songState.value = await invoke('songs:state');
  } catch (e) {}
}
async function loadCfg() {
  cfgText.value = JSON.stringify(await invoke('config:get'), null, 2);
}
async function saveCfg() {
  try {
    await invoke('config:set', JSON.parse(cfgText.value));
    showToast('配置已保存');
  } catch (e) {
    showToast('JSON 错误: ' + e.message);
  }
}
async function say() {
  if (!sayText.value.trim()) return;
  await invoke('say', { text: sayText.value.trim() });
  sayText.value = '';
}
async function testEvent(kind) {
  const map = {
    danmaku: { type: 'danmaku', name: '测试观众', text: '你好呀' },
    enter: { type: 'enter', name: '进房测试鱼' },
    gift: { type: 'gift', name: '送礼测试鱼', gift: '小心心', num: 1, value: 0.1 },
    follow: { type: 'follow', name: '关注测试鱼' }
  };
  await invoke('event:test', { event: map[kind] });
}
function reloadFrames() {
  iframeKey.value++;
}
onMounted(async () => {
  await loadCfg();
  await refresh();
  await refreshSongs();
  timer = setInterval(refresh, 1500);
  songTimer = setInterval(refreshSongs, 3000);
});
onUnmounted(() => {
  clearInterval(timer);
  clearInterval(songTimer);
});
</script>

<template>
  <div class="page">
    <header>
      <h1>🐳 鲸语 · 移植版</h1>
      <span class="pill" :class="running ? 'on' : 'off'">{{ running ? '会话进行中' : '未开始' }}</span>
      <span class="pill">mode: {{ mode }}</span>
      <span class="muted">{{ uptime }}s</span>
      <nav>
        <button v-for="t in tabs" :key="t.key" :class="{ active: tab === t.key }" @click="tab = t.key">{{ t.label }}</button>
      </nav>
    </header>

    <main>
      <template v-if="tab === 'session'">
        <section class="card">
          <h2>会话控制</h2>
          <div class="row">
            <button @click="invoke('session:start').then(refresh)">开始</button>
            <button class="warn" @click="invoke('session:stop').then(refresh)">结束</button>
            <button @click="invoke('stats:reset').then(refresh)">清空统计</button>
          </div>
          <div class="stats">
            <div v-for="s in statList" :key="s.key" class="stat"><b>{{ s.value }}</b><span>{{ s.label }}</span></div>
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
            <button @click="testEvent('follow')">关注</button>
          </div>
        </section>
        <section class="card">
          <h2>实时事件</h2>
          <ul>
            <li v-for="(x, i) in feed.slice(0, 30)" :key="i">
              <span class="k">{{ x.t }} {{ x.kind }}</span><b v-if="x.name">{{ x.name }}：</b>{{ x.text }}
            </li>
          </ul>
        </section>
      </template>

      <section v-if="tab === 'events'" class="card wide">
        <h2>事件流</h2>
        <ul>
          <li v-for="(x, i) in feed" :key="i">
            <span class="k">{{ x.t }} {{ x.kind }}</span><b v-if="x.name">{{ x.name }}：</b>{{ x.text }}
          </li>
        </ul>
      </section>

      <section v-if="tab === 'config'" class="card wide">
        <h2>config.json</h2>
        <textarea v-model="cfgText"></textarea>
        <div class="row">
          <button @click="saveCfg">保存配置</button>
          <button @click="loadCfg">重新读取</button>
        </div>
      </section>

      <section v-if="tab === 'live2d'" class="card wide">
        <h2>Live2D 皮套（OBS 地址：/live2d）</h2>
        <div class="row">
          <button @click="reloadFrames">重新加载</button>
          <a :href="API + '/live2d'" target="_blank">新窗口打开</a>
        </div>
        <iframe :key="'l2d' + iframeKey" class="frame checker" :src="API + '/live2d?scale=0.92'"></iframe>
      </section>

      <section v-if="tab === 'overlay'" class="card wide">
        <h2>OBS 叠加层预览</h2>
        <div class="row">
          <button @click="reloadFrames">刷新</button>
          <a :href="API + '/overlay'" target="_blank">弹幕层</a>
          <a :href="API + '/songs'" target="_blank">歌单</a>
        </div>
        <div class="frames">
          <iframe :key="'ov' + iframeKey" class="frame" :src="API + '/overlay'"></iframe>
          <iframe :key="'sg' + iframeKey" class="frame" :src="API + '/songs'"></iframe>
        </div>
      </section>

      <section v-if="tab === 'songs'" class="card wide">
        <h2>点歌队列</h2>
        <div class="muted">当前：{{ songState.current ? songState.current.name + ' · ' + (songState.current.by || '') : '无' }}</div>
        <ul>
          <li v-for="(x, i) in songState.queue" :key="i">{{ i + 1 }}. {{ x.name }} <span class="muted">{{ x.by }} 点</span></li>
          <li v-if="!songState.queue.length" class="muted">队列为空</li>
        </ul>
        <div class="muted">「点歌 歌名」会自动入队（内嵌网易云搜索，无需登录 cookie）</div>
      </section>
    </main>

    <div v-if="toast" class="toast">{{ toast }}</div>
  </div>
</template>

<style>
* { box-sizing: border-box; }
.page { min-height: 100vh; background: #0b1220; color: #e6f0ff; }
header { display: flex; align-items: center; gap: 12px; padding: 12px 18px; background: #101a2e; border-bottom: 1px solid #22314d; position: sticky; top: 0; z-index: 5; flex-wrap: wrap; }
header h1 { font-size: 17px; margin: 0; }
nav { margin-left: auto; display: flex; gap: 6px; flex-wrap: wrap; }
nav button { background: #16233c; border: 1px solid #26395c; color: #bcd6ff; border-radius: 8px; padding: 6px 12px; cursor: pointer; font-size: 13px; }
nav button.active { background: #1d3a66; border-color: #3b68aa; color: #fff; }
.pill { font-size: 12px; padding: 3px 10px; border-radius: 999px; background: #1b2a45; color: #9fc4ff; }
.pill.on { background: #123a2a; color: #6ef2b0; }
.pill.off { background: #3a1a1f; color: #ff8f9a; }
main { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; padding: 16px 18px; }
.card { background: #101a2e; border: 1px solid #22314d; border-radius: 12px; padding: 14px; }
.card.wide { grid-column: 1 / -1; }
.card h2 { font-size: 13px; margin: 12px 0 8px; color: #9fc4ff; font-weight: 600; }
.card h2:first-child { margin-top: 0; }
.row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
button { background: #1d3a66; color: #dbeaff; border: 1px solid #2f5287; border-radius: 8px; padding: 7px 14px; cursor: pointer; font-size: 13px; }
button:hover { background: #25497e; }
button.warn { background: #4a2230; border-color: #7d3350; }
input, textarea { width: 100%; background: #0c1526; color: #dbeaff; border: 1px solid #22314d; border-radius: 8px; padding: 8px 10px; font-size: 13px; }
textarea { min-height: 300px; font-family: Consolas, monospace; line-height: 1.5; }
.row input { flex: 1; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 10px 0; }
.stat { background: #0c1526; border-radius: 10px; padding: 10px; text-align: center; }
.stat b { display: block; font-size: 22px; }
.stat span { font-size: 12px; color: #7f9ac0; }
ul { list-style: none; margin: 0; padding: 0; max-height: 420px; overflow: auto; }
li { padding: 6px 0; border-bottom: 1px dashed #1d2a42; font-size: 13px; color: #c9dcf5; }
li .k { color: #7f9ac0; font-size: 12px; margin-right: 6px; }
.muted { color: #7f9ac0; font-size: 12px; }
a { color: #7fb6ff; font-size: 13px; }
.toast { position: fixed; right: 20px; bottom: 20px; background: #1d3a66; padding: 10px 16px; border-radius: 10px; font-size: 13px; }
.frame { width: 100%; height: 620px; border: 1px solid #22314d; border-radius: 12px; background: #0c1526; }
.frames { display: flex; gap: 14px; flex-wrap: wrap; }
.frames .frame { flex: 1 1 320px; }
.checker { background: repeating-conic-gradient(#1b2740 0% 25%, #131d31 0% 50%) 50% / 24px 24px; }
</style>
