<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { invoke } from './api';

const API = (typeof window !== 'undefined' && window.whale && window.whale.base)
  || (location.port === '5173' ? 'http://127.0.0.1:8788' : location.origin);

const MENU = [
  {
    title: '直播控制', icon: '🎛',
    items: [
      { key: 'session', label: '会话总览', icon: '📊' },
      { key: 'events', label: '实时事件', icon: '💬' }
    ]
  },
  {
    title: '形象与画面', icon: '🎭',
    items: [
      { key: 'live2d', label: 'Live2D 皮套', icon: '🐳' },
      { key: 'overlay', label: 'OBS 叠加层', icon: '🖼' }
    ]
  },
  {
    title: '互动', icon: '🎁',
    items: [
      { key: 'songs', label: '点歌队列', icon: '🎵' },
      { key: 'say', label: '手动说话', icon: '🗣' }
    ]
  },
  {
    title: '设置', icon: '⚙',
    items: [
      { key: 'config', label: '配置文件', icon: '📝' },
      { key: 'about', label: '关于', icon: 'ℹ' }
    ]
  }
];

const open = ref({ '直播控制': true, '形象与画面': true, '互动': true, '设置': true });
const current = ref('session');
const currentLabel = computed(() => {
  for (const g of MENU) for (const it of g.items) if (it.key === current.value) return it.label;
  return '';
});

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

function toggleGroup(title) {
  open.value = { ...open.value, [title]: !open.value[title] };
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
  try { songState.value = await invoke('songs:state'); } catch (e) {}
}
async function loadCfg() {
  try { cfgText.value = JSON.stringify(await invoke('config:get'), null, 2); } catch (e) {}
}
async function saveCfg() {
  try {
    await invoke('config:set', JSON.parse(cfgText.value));
    showToast('配置已保存');
  } catch (e) { showToast('JSON 错误: ' + e.message); }
}
async function say() {
  if (!sayText.value.trim()) return;
  await invoke('say', { text: sayText.value.trim() });
  sayText.value = '';
  showToast('已提交 TTS');
}
async function testEvent(kind) {
  const map = {
    danmaku: { type: 'danmaku', name: '测试观众', text: '你好呀' },
    enter: { type: 'enter', name: '进房测试鱼' },
    gift: { type: 'gift', name: '送礼测试鱼', gift: '小心心', num: 1, value: 0.1 },
    follow: { type: 'follow', name: '关注测试鱼' }
  };
  await invoke('event:test', { event: map[kind] });
  showToast('已发送测试事件');
}
function showToast(msg) {
  toast.value = msg;
  setTimeout(() => (toast.value = ''), 1600);
}
function reloadFrames() { iframeKey.value++; }
onMounted(async () => {
  await Promise.all([loadCfg(), refresh(), refreshSongs()]);
  timer = setInterval(refresh, 1500);
  songTimer = setInterval(refreshSongs, 3000);
});
onUnmounted(() => {
  clearInterval(timer);
  clearInterval(songTimer);
});
</script>

<template>
  <div class="app">
    <!-- 左侧菜单树 -->
    <aside class="sidebar">
      <div class="brand">
        <span class="logo">🐳</span>
        <div>
          <div class="name">鲸语控制台</div>
          <div class="ver">deepseek-live</div>
        </div>
      </div>
      <nav class="tree">
        <div v-for="g in MENU" :key="g.title" class="group">
          <button class="group-head" @click="toggleGroup(g.title)">
            <span class="chev" :class="{ open: open[g.title] }">▸</span>
            <span class="gicon">{{ g.icon }}</span>
            <span class="gtitle">{{ g.title }}</span>
          </button>
          <div v-show="open[g.title]" class="children">
            <button
              v-for="it in g.items" :key="it.key"
              class="leaf" :class="{ active: current === it.key }"
              @click="current = it.key"
            >
              <span class="licon">{{ it.icon }}</span><span>{{ it.label }}</span>
            </button>
          </div>
        </div>
      </nav>
      <div class="side-foot">
        <span class="dot" :class="running ? 'on' : 'off'"></span>
        {{ running ? '会话进行中' : '未开始' }} · {{ mode }}
      </div>
    </aside>

    <!-- 右侧内容 -->
    <section class="main">
      <header class="topbar">
        <div class="crumb">
          <span class="muted">鲸语</span> <span class="sep">/</span> <b>{{ currentLabel }}</b>
        </div>
        <div class="top-actions">
          <span class="pill" :class="running ? 'on' : 'off'">{{ running ? '运行中' : '未开始' }}</span>
          <span class="muted">{{ uptime }}s</span>
          <span class="muted">mode: {{ mode }}</span>
        </div>
      </header>

      <div class="content">
        <!-- 会话 -->
        <div v-if="current === 'session'" class="grid">
          <div class="card">
            <h2>会话控制</h2>
            <div class="row">
              <button @click="invoke('session:start').then(refresh)">开始会话</button>
              <button class="warn" @click="invoke('session:stop').then(refresh)">结束会话</button>
              <button @click="invoke('stats:reset').then(refresh)">清空统计</button>
            </div>
            <div class="stats">
              <div v-for="s in statList" :key="s.key" class="stat"><b>{{ s.value }}</b><span>{{ s.label }}</span></div>
            </div>
            <h2>快捷测试</h2>
            <div class="row">
              <button @click="testEvent('danmaku')">模拟弹幕</button>
              <button @click="testEvent('enter')">模拟进房</button>
              <button @click="testEvent('gift')">模拟礼物</button>
              <button @click="testEvent('follow')">模拟关注</button>
            </div>
          </div>
          <div class="card">
            <h2>最近事件</h2>
            <ul>
              <li v-for="(x, i) in feed.slice(0, 40)" :key="i">
                <span class="k">{{ x.t }} {{ x.kind }}</span><b v-if="x.name">{{ x.name }}：</b>{{ x.text }}
              </li>
              <li v-if="!feed.length" class="muted">暂无事件</li>
            </ul>
          </div>
        </div>

        <!-- 事件 -->
        <div v-if="current === 'events'" class="card">
          <h2>事件流（最近 50 条）</h2>
          <ul>
            <li v-for="(x, i) in feed" :key="i">
              <span class="k">{{ x.t }} {{ x.kind }}</span><b v-if="x.name">{{ x.name }}：</b>{{ x.text }}
            </li>
            <li v-if="!feed.length" class="muted">暂无事件</li>
          </ul>
        </div>

        <!-- 皮套 -->
        <div v-if="current === 'live2d'" class="card">
          <h2>Live2D 皮套（OBS 浏览器源：/live2d）</h2>
          <div class="row">
            <button @click="reloadFrames">重新加载</button>
            <a :href="API + '/live2d'" target="_blank">新窗口打开</a>
            <span class="muted">说话时自动口型 + 随机表情</span>
          </div>
          <iframe :key="'l2d' + iframeKey" class="frame checker" :src="API + '/live2d?scale=0.92'"></iframe>
        </div>

        <!-- 叠加层 -->
        <div v-if="current === 'overlay'" class="card">
          <h2>OBS 叠加层预览</h2>
          <div class="row">
            <button @click="reloadFrames">刷新</button>
            <a :href="API + '/overlay'" target="_blank">弹幕层 /overlay</a>
            <a :href="API + '/songs'" target="_blank">歌单 /songs</a>
          </div>
          <div class="frames">
            <iframe :key="'ov' + iframeKey" class="frame" :src="API + '/overlay'"></iframe>
            <iframe :key="'sg' + iframeKey" class="frame" :src="API + '/songs'"></iframe>
          </div>
        </div>

        <!-- 歌单 -->
        <div v-if="current === 'songs'" class="card">
          <h2>点歌队列</h2>
          <div class="song-now">
            当前：<b>{{ songState.current ? songState.current.name : '无' }}</b>
            <span class="muted" v-if="songState.current">{{ songState.current.by }} 点</span>
          </div>
          <ul>
            <li v-for="(x, i) in songState.queue" :key="i">{{ i + 1 }}. {{ x.name }} <span class="muted">{{ x.by }} 点</span></li>
            <li v-if="!songState.queue.length" class="muted">队列为空</li>
          </ul>
          <div class="muted">弹幕「点歌 歌名」自动搜索入队（内嵌网易云匿名搜索）</div>
        </div>

        <!-- 手动说话 -->
        <div v-if="current === 'say'" class="card">
          <h2>手动说话（走 TTS）</h2>
          <div class="row">
            <input v-model="sayText" placeholder="输入要让小鲸鱼说的话" @keyup.enter="say" />
            <button @click="say">说话</button>
          </div>
          <div class="muted" style="margin-top:8px">走 9881 GPT-SoVITS 代理；皮套会同步口型</div>
        </div>

        <!-- 配置 -->
        <div v-if="current === 'config'" class="card">
          <h2>config.json</h2>
          <textarea v-model="cfgText"></textarea>
          <div class="row">
            <button @click="saveCfg">保存配置</button>
            <button @click="loadCfg">重新读取</button>
            <span class="muted">改完保存即刻生效</span>
          </div>
        </div>

        <!-- 关于 -->
        <div v-if="current === 'about'" class="card">
          <h2>关于</h2>
          <ul>
            <li><b>deepseek-live</b> · 鲸语直播系统移植版（Vue + Electron）</li>
            <li>核心：app/main/core.cjs（零依赖 Node 服务，HTTP + WS + AI + TTS + 点歌）</li>
            <li>控制：IPC（window.whale.invoke），OBS 叠加层走 HTTP</li>
            <li>仓库：github.com/a106832411/deepseek-live</li>
          </ul>
        </div>
      </div>
    </section>

    <div v-if="toast" class="toast">{{ toast }}</div>
  </div>
</template>

<style>
* { box-sizing: border-box; }
html, body, #app { height: 100%; }
body { margin: 0; background: #0b1220; }
.app { display: flex; height: 100vh; color: #e6f0ff; font-family: "Microsoft YaHei", system-ui, sans-serif; }

.sidebar { width: 232px; flex: 0 0 232px; display: flex; flex-direction: column; background: #0e1729; border-right: 1px solid #1e2c46; }
.brand { display: flex; align-items: center; gap: 10px; padding: 16px 14px 12px; border-bottom: 1px solid #1a2740; }
.brand .logo { font-size: 22px; }
.brand .name { font-size: 15px; font-weight: 600; }
.brand .ver { font-size: 11px; color: #6f8bb3; }
.tree { flex: 1; overflow-y: auto; padding: 8px 6px; }
.group { margin-bottom: 4px; }
.group-head { display: flex; align-items: center; gap: 6px; width: 100%; background: transparent; border: 0; color: #9fb4d8; font-size: 12px; padding: 7px 8px; cursor: pointer; border-radius: 6px; }
.group-head:hover { background: #14203a; }
.chev { display: inline-block; transition: transform .15s; font-size: 10px; color: #6f8bb3; }
.chev.open { transform: rotate(90deg); }
.gicon { font-size: 13px; }
.gtitle { letter-spacing: .5px; }
.children { display: flex; flex-direction: column; gap: 2px; padding-left: 10px; border-left: 1px solid #1a2740; margin-left: 14px; }
.leaf { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; background: transparent; border: 0; color: #c9dcf5; font-size: 13px; padding: 8px 10px; border-radius: 8px; cursor: pointer; }
.leaf:hover { background: #14203a; }
.leaf.active { background: #1d3a66; color: #fff; box-shadow: inset 0 0 0 1px #2f5287; }
.licon { font-size: 14px; }
.side-foot { border-top: 1px solid #1a2740; padding: 10px 14px; font-size: 12px; color: #8fa8cc; display: flex; align-items: center; gap: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #ff8f9a; }
.dot.on { background: #6ef2b0; box-shadow: 0 0 8px #6ef2b0; }

.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.topbar { height: 48px; flex: 0 0 48px; display: flex; align-items: center; gap: 12px; padding: 0 16px; background: #101a2e; border-bottom: 1px solid #1e2c46; }
.crumb { font-size: 13px; }
.crumb .sep { color: #3c5a86; margin: 0 6px; }
.top-actions { margin-left: auto; display: flex; align-items: center; gap: 10px; }
.pill { font-size: 12px; padding: 3px 10px; border-radius: 999px; background: #1b2a45; color: #9fc4ff; }
.pill.on { background: #123a2a; color: #6ef2b0; }
.pill.off { background: #3a1a1f; color: #ff8f9a; }
.content { flex: 1; overflow-y: auto; padding: 16px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.card { background: #101a2e; border: 1px solid #1e2c46; border-radius: 12px; padding: 16px; }
.card h2 { font-size: 13px; margin: 0 0 12px; color: #9fc4ff; font-weight: 600; }
.card h2 + h2 { margin-top: 18px; }
.row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
button { background: #1d3a66; color: #dbeaff; border: 1px solid #2f5287; border-radius: 8px; padding: 7px 14px; cursor: pointer; font-size: 13px; }
button:hover { background: #25497e; }
button.warn { background: #4a2230; border-color: #7d3350; }
input, textarea { width: 100%; background: #0c1526; color: #dbeaff; border: 1px solid #22314d; border-radius: 8px; padding: 8px 10px; font-size: 13px; }
textarea { min-height: 320px; font-family: Consolas, monospace; line-height: 1.55; }
.row input { flex: 1; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 12px 0; }
.stat { background: #0c1526; border-radius: 10px; padding: 12px; text-align: center; }
.stat b { display: block; font-size: 22px; }
.stat span { font-size: 12px; color: #7f9ac0; }
ul { list-style: none; margin: 0; padding: 0; max-height: 460px; overflow: auto; }
li { padding: 7px 0; border-bottom: 1px dashed #1a2740; font-size: 13px; color: #c9dcf5; }
li .k { color: #7f9ac0; font-size: 12px; margin-right: 6px; }
.muted { color: #7f9ac0; font-size: 12px; }
a { color: #7fb6ff; font-size: 13px; }
.toast { position: fixed; right: 20px; bottom: 20px; background: #1d3a66; padding: 10px 16px; border-radius: 10px; font-size: 13px; }
.frame { width: 100%; height: 560px; border: 1px solid #1e2c46; border-radius: 12px; background: #0c1526; margin-top: 8px; }
.frames { display: flex; gap: 14px; flex-wrap: wrap; }
.frames .frame { flex: 1 1 320px; }
.checker { background: repeating-conic-gradient(#1b2740 0% 25%, #131d31 0% 50%) 50% / 24px 24px; }
.song-now { margin-bottom: 10px; font-size: 14px; }
</style>
