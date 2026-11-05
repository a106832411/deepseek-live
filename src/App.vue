<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue';
import { invoke } from './api';

const API = (typeof window !== 'undefined' && window.whale && window.whale.base)
  || (location.port === '5173' ? 'http://127.0.0.1:8788' : location.origin);

const THEMES = [
  { key: 'aurora', label: '极光', c1: '#22d3ee', c2: '#a855f7' },
  { key: 'emerald', label: '翡翠', c1: '#34d399', c2: '#22d3ee' },
  { key: 'sunset', label: '日落', c1: '#fb923c', c2: '#f472b6' },
  { key: 'royal', label: '深海', c1: '#60a5fa', c2: '#818cf8' }
];
const theme = ref(localStorage.getItem('wt_theme') || 'aurora');
function setTheme(k) {
  theme.value = k;
  localStorage.setItem('wt_theme', k);
}

const MENU = [
  { title: '直播控制', icon: '🎛', items: [
    { key: 'session', label: '会话总览', icon: '📊' },
    { key: 'events', label: '实时事件', icon: '💬' }
  ] },
  { title: '形象与画面', icon: '🎭', items: [
    { key: 'live2d', label: 'Live2D 皮套', icon: '🐳' },
    { key: 'overlay', label: 'OBS 叠加层', icon: '🖼' }
  ] },
  { title: '互动玩法', icon: '🎁', items: [
    { key: 'songs', label: '点歌队列', icon: '🎵' },
    { key: 'say', label: '手动说话', icon: '🗣' }
  ] },
  { title: '系统设置', icon: '⚙', items: [
    { key: 'config', label: '全部配置', icon: '📝' },
    { key: 'about', label: '关于', icon: 'ℹ' }
  ] }
];
const openGroup = ref({ '直播控制': true, '形象与画面': true, '互动玩法': true, '系统设置': true });
const current = ref('session');
const currentLabel = computed(() => {
  for (const g of MENU) for (const it of g.items) if (it.key === current.value) return it.label;
  return '';
});

const STAT_META = [
  { key: 'enter', label: '进房', icon: '🚪', color: 'c1' },
  { key: 'danmaku', label: '弹幕', icon: '💬', color: 'c2' },
  { key: 'gift', label: '礼物', icon: '🎁', color: 'c3' },
  { key: 'follow', label: '关注', icon: '⭐', color: 'c4' },
  { key: 'like', label: '点赞', icon: '👍', color: 'c5' },
  { key: 'say', label: '说话', icon: '🔊', color: 'c6' }
];

const running = ref(false);
const mode = ref('relay');
const uptime = ref(0);
const stats = ref({});
const feed = ref([]);
const toast = ref('');
const songState = ref({ current: null, queue: [] });
const iframeKey = ref(0);
const l2dScale = ref(0.92);
const sayText = ref('');
const manualSong = ref('');

// 配置表单
const cfg = ref({});
const dirty = ref(false);
const showJson = ref(false);
const jsonText = ref('');
const enterTpl = ref('');
const followTpl = ref('');
const blockText = ref('');
const fixedRows = ref([]);
const saving = ref(false);

const SOURCE_LIST = [
  { key: 'netease', label: '网易云', na: false },
  { key: 'qq', label: 'QQ 音乐', na: false },
  { key: 'kugou', label: '酷狗', na: true },
  { key: 'qishui', label: '汽水音乐', na: true }
];

const EVENT_META = [
  { key: 'danmaku', label: '弹幕回复', icon: '💬' },
  { key: 'enter', label: '进房欢迎', icon: '🚪' },
  { key: 'gift', label: '礼物感谢', icon: '🎁' },
  { key: 'follow', label: '关注感谢', icon: '⭐' },
  { key: 'like', label: '点赞提一下', icon: '👍' }
];

let timer = null;
let songTimer = null;

function toastShow(msg) {
  toast.value = msg;
  setTimeout(() => (toast.value = ''), 1800);
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
function normalize(c) {
  c = c || {};
  c.ai = c.ai || {};
  if (c.ai.temperature == null) c.ai.temperature = 0.8;
  if (c.ai.max_chars == null) c.ai.max_chars = 20;
  c.tts = c.tts || {};
  if (c.tts.speed == null) c.tts.speed = 0.95;
  if (c.tts.sample_steps == null) c.tts.sample_steps = 32;
  if (c.tts.top_k == null) c.tts.top_k = 20;
  if (c.tts.temperature == null) c.tts.temperature = 1.0;
  c.events = c.events || {};
  const defs = {
    danmaku: { mode: 'ai', probability: 100, cooldown_user: 5 },
    enter: { mode: 'template', probability: 100, cooldown_user: 30 },
    gift: { mode: 'ai', probability: 100, cooldown_user: 10 },
    follow: { mode: 'template', probability: 100, cooldown_user: 60 },
    like: { mode: 'ai', probability: 100, cooldown_user: 60 }
  };
  for (const k of Object.keys(defs)) {
    c.events[k] = c.events[k] || {};
    for (const f of Object.keys(defs[k])) {
      if (c.events[k][f] == null) c.events[k][f] = defs[k][f];
    }
  }
  c.songs = c.songs || {};
  if (c.songs.max_queue == null) c.songs.max_queue = 20;
  if (c.songs.enabled == null) c.songs.enabled = true;
  if (c.songs.dedupe == null) c.songs.dedupe = true;
  if (c.songs.internal == null) c.songs.internal = true;
  if (!Array.isArray(c.songs.sources) || !c.songs.sources.length) c.songs.sources = ['netease', 'qq'];
  if (c.douyin == null) c.douyin = { mode: 'relay', web_rid: '' };
  if (c.persona == null) c.persona = { system: '' };
  return c;
}

async function loadCfg() {
  const c = normalize(await invoke('config:get'));
  cfg.value = JSON.parse(JSON.stringify(c || {}));
  enterTpl.value = ((c.events && c.events.enter && c.events.enter.templates) || []).join('\n');
  followTpl.value = ((c.events && c.events.follow && c.events.follow.templates) || []).join('\n');
  blockText.value = (((c.filter && c.filter.blocklist) || [])).join('\n');
  fixedRows.value = ((c.fixed_replies || [])).map((r) => ({
    keywords: Array.isArray(r.keywords) ? r.keywords.join('，') : String(r.keywords || ''),
    reply: r.reply || ''
  }));
  jsonText.value = JSON.stringify(cfg.value, null, 2);
  dirty.value = false;
}
function markDirty() { dirty.value = true; }
async function saveCfg() {
  saving.value = true;
  try {
    const body = JSON.parse(JSON.stringify(cfg.value));
    body.events = body.events || {};
    body.events.enter = body.events.enter || {};
    body.events.follow = body.events.follow || {};
    body.events.enter.templates = enterTpl.value.split('\n').map((s) => s.trim()).filter(Boolean);
    body.events.follow.templates = followTpl.value.split('\n').map((s) => s.trim()).filter(Boolean);
    body.filter = { blocklist: blockText.value.split('\n').map((s) => s.trim()).filter(Boolean) };
    body.fixed_replies = fixedRows.value
      .map((r) => ({ keywords: String(r.keywords).split(/[,，\s]+/).filter(Boolean), reply: String(r.reply).trim() }))
      .filter((r) => r.keywords.length && r.reply);
    await invoke('config:set', body);
    cfg.value = body;
    jsonText.value = JSON.stringify(body, null, 2);
    dirty.value = false;
    toastShow('✅ 配置已保存并生效');
  } catch (e) {
    toastShow('保存失败: ' + e.message);
  } finally {
    saving.value = false;
  }
}
function addFixed() { fixedRows.value.push({ keywords: '', reply: '' }); }
function removeFixed(i) { fixedRows.value.splice(i, 1); }
function addTpl(which) {
  if (which === 'enter') enterTpl.value += (enterTpl.value ? '\n' : '') + '欢迎{name}，随便坐别客气～';
  else followTpl.value += (followTpl.value ? '\n' : '') + '谢谢{name}的关注！';
}
function applyJson() {
  try {
    cfg.value = JSON.parse(jsonText.value);
    loadFromCfg();
    toastShow('已应用 JSON（记得保存）');
  } catch (e) { toastShow('JSON 错误: ' + e.message); }
}
function loadFromCfg() {
  const c = cfg.value;
  enterTpl.value = ((c.events && c.events.enter && c.events.enter.templates) || []).join('\n');
  followTpl.value = ((c.events && c.events.follow && c.events.follow.templates) || []).join('\n');
  blockText.value = (((c.filter && c.filter.blocklist) || [])).join('\n');
  fixedRows.value = ((c.fixed_replies || [])).map((r) => ({
    keywords: Array.isArray(r.keywords) ? r.keywords.join('，') : String(r.keywords || ''),
    reply: r.reply || ''
  }));
}
async function doSay() {
  if (!sayText.value.trim()) return;
  await invoke('say', { text: sayText.value.trim() });
  sayText.value = '';
  toastShow('🔊 已提交 TTS');
}
async function quickSay(t) { await invoke('say', { text: t }); toastShow('🔊 ' + t); }
async function testEvent(kind) {
  const map = {
    danmaku: { type: 'danmaku', name: '测试观众', text: '你好呀' },
    enter: { type: 'enter', name: '进房测试鱼' },
    gift: { type: 'gift', name: '送礼测试鱼', gift: '小心心', num: 1, value: 0.1 },
    follow: { type: 'follow', name: '关注测试鱼' }
  };
  await invoke('event:test', { event: map[kind] });
  toastShow('已发送测试事件');
}
async function requestSong() {
  const q = manualSong.value.trim();
  if (!q) return;
  const r = await invoke('songs:request', { name: '手动点歌', query: q });
  if (r && r.ok) { toastShow(`✅ 已入队：${r.song.name}`); manualSong.value = ''; }
  else toastShow('点歌失败: ' + ((r && r.error) || '未知'));
  refreshSongs();
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
  <div class="app" :class="'theme-' + theme">
    <aside class="sidebar">
      <div class="brand">
        <div class="logo">🐳</div>
        <div>
          <div class="name">鲸语控制台</div>
          <div class="ver">deepseek-live · v0.2</div>
        </div>
      </div>
      <nav class="tree">
        <div v-for="g in MENU" :key="g.title" class="group">
          <button class="group-head" @click="openGroup[g.title] = !openGroup[g.title]">
            <span class="chev" :class="{ open: openGroup[g.title] }">▸</span>
            <span class="gicon">{{ g.icon }}</span><span class="gtitle">{{ g.title }}</span>
          </button>
          <div v-show="openGroup[g.title]" class="children">
            <button v-for="it in g.items" :key="it.key" class="leaf" :class="{ active: current === it.key }" @click="current = it.key">
              <span class="licon">{{ it.icon }}</span><span>{{ it.label }}</span>
            </button>
          </div>
        </div>
      </nav>
      <div class="theme-switch">
        <span class="ts-label">主题</span>
        <button v-for="t in THEMES" :key="t.key" class="ts-dot" :class="{ active: theme === t.key }"
          :style="{ background: 'linear-gradient(135deg,' + t.c1 + ',' + t.c2 + ')' }"
          :title="t.label" @click="setTheme(t.key)"></button>
      </div>
      <div class="side-foot">
        <span class="dot" :class="running ? 'on' : 'off'"></span>
        <div>
          <div>{{ running ? '会话进行中' : '未开始' }}</div>
          <div class="tiny">{{ mode }} · {{ uptime }}s</div>
        </div>
      </div>
    </aside>

    <section class="main">
      <header class="topbar">
        <div class="crumb"><span class="muted">鲸语</span><span class="sep">/</span><b>{{ currentLabel }}</b></div>
        <div class="top-actions">
          <button v-if="running" class="ghost warn" @click="invoke('session:stop').then(refresh)">结束会话</button>
          <button v-else class="primary" @click="invoke('session:start').then(refresh)">▶ 开始会话</button>
          <span class="pill" :class="running ? 'on' : 'off'">{{ running ? 'LIVE' : 'OFF' }}</span>
        </div>
      </header>

      <div class="content">
        <!-- 会话总览 -->
        <template v-if="current === 'session'">
          <div class="stat-grid">
            <div v-for="m in STAT_META" :key="m.key" class="stat-card" :class="m.color">
              <div class="s-dot"></div>
              <div class="s-num">{{ (stats && stats[m.key]) || 0 }}</div>
              <div class="s-label">{{ m.label }}</div>
            </div>
          </div>
          <div class="grid2">
            <div class="card">
              <div class="card-head"><h2>快捷测试</h2><span class="hint">不用开播也能测</span></div>
              <div class="chips">
                <button class="chip" @click="testEvent('danmaku')">💬 模拟弹幕</button>
                <button class="chip" @click="testEvent('enter')">🚪 模拟进房</button>
                <button class="chip" @click="testEvent('gift')">🎁 模拟礼物</button>
                <button class="chip" @click="testEvent('follow')">⭐ 模拟关注</button>
              </div>
              <div class="card-head" style="margin-top:16px"><h2>统计</h2></div>
              <button class="ghost" @click="invoke('stats:reset').then(refresh)">清空统计</button>
            </div>
            <div class="card">
              <div class="card-head"><h2>最近事件</h2><span class="hint">实时</span></div>
              <ul class="feed">
                <li v-for="(x, i) in feed.slice(0, 30)" :key="i">
                  <span class="tag" :class="x.kind">{{ x.kind }}</span>
                  <span class="nick" v-if="x.name">{{ x.name }}</span>
                  <span class="txt">{{ x.text }}</span>
                  <span class="time">{{ x.t }}</span>
                </li>
                <li v-if="!feed.length" class="empty">暂无事件，点上面的按钮试试</li>
              </ul>
            </div>
          </div>
        </template>

        <!-- 事件 -->
        <div v-if="current === 'events'" class="card">
          <div class="card-head"><h2>事件流</h2><span class="hint">最近 50 条</span></div>
          <ul class="feed">
            <li v-for="(x, i) in feed" :key="i">
              <span class="tag" :class="x.kind">{{ x.kind }}</span>
              <span class="nick" v-if="x.name">{{ x.name }}</span>
              <span class="txt">{{ x.text }}</span>
              <span class="time">{{ x.t }}</span>
            </li>
            <li v-if="!feed.length" class="empty">暂无事件</li>
          </ul>
        </div>

        <!-- 皮套 -->
        <div v-if="current === 'live2d'" class="card">
          <div class="card-head"><h2>Live2D 皮套</h2><span class="hint">OBS 浏览器源：{{ API }}/live2d</span></div>
          <div class="row">
            <label class="field inline"><span>缩放</span>
              <input type="range" min="0.5" max="1.2" step="0.02" v-model.number="l2dScale" style="width:180px" />
              <b>{{ l2dScale.toFixed(2) }}</b>
            </label>
            <button class="ghost" @click="reloadFrames">重新加载</button>
            <a :href="API + '/live2d'" target="_blank">新窗口打开</a>
          </div>
          <iframe :key="'l2d' + iframeKey + l2dScale" class="frame checker" :src="API + '/live2d?scale=' + l2dScale"></iframe>
        </div>

        <!-- 叠加层 -->
        <div v-if="current === 'overlay'" class="card">
          <div class="card-head"><h2>OBS 叠加层</h2><span class="hint">弹幕层 / 歌单</span></div>
          <div class="frames">
            <div class="fcol"><div class="flabel">弹幕层 <code>/overlay</code></div><iframe :key="'ov' + iframeKey" class="frame" :src="API + '/overlay'"></iframe></div>
            <div class="fcol"><div class="flabel">歌单 <code>/songs</code></div><iframe :key="'sg' + iframeKey" class="frame" :src="API + '/songs'"></iframe></div>
          </div>
        </div>

        <!-- 点歌 -->
        <div v-if="current === 'songs'" class="card">
          <div class="card-head"><h2>点歌队列</h2><span class="hint">匿名搜索，无需登录</span></div>
          <div class="row">
            <input v-model="manualSong" placeholder="输入歌名，回车点歌" @keyup.enter="requestSong" />
            <button class="primary" @click="requestSong">点歌</button>
          </div>
          <div class="now-playing" v-if="songState.current">
            <span class="eq">▍▍▍</span> 正在播放：<b>{{ songState.current.name }}</b>
            <span class="muted" v-if="songState.current.by"> · {{ songState.current.by }} 点</span>
          </div>
          <ul class="queue">
            <li v-for="(x, i) in songState.queue" :key="i">
              <span class="idx">{{ i + 1 }}</span><span class="qname">{{ x.name }}</span><span class="muted">{{ x.by }} 点</span>
            </li>
            <li v-if="!songState.queue.length" class="empty">队列为空，弹幕发「点歌 歌名」会自动入队</li>
          </ul>
        </div>

        <!-- 说话 -->
        <div v-if="current === 'say'" class="card">
          <div class="card-head"><h2>手动说话</h2><span class="hint">走 GPT-SoVITS 9881</span></div>
          <div class="row">
            <input v-model="sayText" placeholder="输入要让小鲸鱼说的话" @keyup.enter="doSay" />
            <button class="primary" @click="doSay">🔊 说话</button>
          </div>
          <div class="chips" style="margin-top:12px">
            <button class="chip" @click="quickSay('欢迎来到直播间，宝子们晚上好呀')">欢迎语</button>
            <button class="chip" @click="quickSay('谢谢宝子的礼物，咕噜咕噜～')">感谢礼物</button>
            <button class="chip" @click="quickSay('今天想听什么歌，点歌台给你们安排')">点歌引导</button>
          </div>
        </div>

        <!-- 配置 -->
        <template v-if="current === 'config'">
          <div class="cfg-actions">
            <button class="ghost" @click="loadCfg">重新读取</button>
            <button class="ghost" @click="showJson = !showJson">{{ showJson ? '隐藏 JSON' : '高级 JSON' }}</button>
            <button class="primary" :disabled="saving" @click="saveCfg">{{ saving ? '保存中…' : '💾 保存配置' }}</button>
            <span class="dirty" v-if="dirty">● 有未保存修改</span>
          </div>

          <div v-show="showJson" class="card">
            <div class="card-head"><h2>高级 JSON</h2><span class="hint">整份 config，可直接编辑</span></div>
            <textarea v-model="jsonText" class="json"></textarea>
            <button class="ghost" @click="applyJson">应用到表单</button>
          </div>

          <div class="cfg-sections">
            <section class="card">
              <div class="card-head"><h2>📡 直播连接</h2></div>
              <label class="field"><span>平台</span><input v-model="cfg.platform" @input="markDirty" /></label>
              <label class="field"><span>抖音模式</span>
                <select v-model="cfg.douyin.mode" @change="markDirty">
                  <option value="relay">relay（中继投递）</option>
                  <option value="browser">browser（自带网页钩子）</option>
                </select>
              </label>
              <label class="field"><span>直播间号</span><input v-model="cfg.douyin.web_rid" @input="markDirty" /></label>
              <label class="field"><span>合并窗口(秒)</span><input type="number" v-model.number="cfg.merge_window" @input="markDirty" /></label>
            </section>

            <section class="card">
              <div class="card-head"><h2>🧠 AI 大脑</h2></div>
              <label class="field"><span>接口地址</span><input v-model="cfg.ai.openai_base" @input="markDirty" /></label>
              <label class="field"><span>模型</span><input v-model="cfg.ai.openai_model" @input="markDirty" /></label>
              <label class="field"><span>API Key</span><input type="password" v-model="cfg.ai.openai_key" @input="markDirty" placeholder="sk-..." /></label>
              <div class="row2">
                <label class="field"><span>回复字数上限</span><input type="number" v-model.number="cfg.ai.max_chars" @input="markDirty" /></label>
                <label class="field"><span>温度</span><input type="number" step="0.1" v-model.number="cfg.ai.temperature" @input="markDirty" /></label>
              </div>
              <label class="field"><span>人设（system prompt）</span><textarea v-model="cfg.persona.system" @input="markDirty"></textarea></label>
            </section>

            <section class="card">
              <div class="card-head"><h2>🔊 语音 TTS</h2></div>
              <label class="field"><span>接口地址</span><input v-model="cfg.tts.api_url" @input="markDirty" /></label>
              <label class="field"><span>参考音频路径</span><input v-model="cfg.tts.ref_audio" @input="markDirty" /></label>
              <label class="field"><span>参考音频对应文字</span><input v-model="cfg.tts.prompt_text" @input="markDirty" /></label>
              <div class="row2">
                <label class="field"><span>语速</span><input type="number" step="0.01" v-model.number="cfg.tts.speed" @input="markDirty" /></label>
                <label class="field"><span>采样步数</span><input type="number" v-model.number="cfg.tts.sample_steps" @input="markDirty" /></label>
                <label class="field"><span>top_k</span><input type="number" v-model.number="cfg.tts.top_k" @input="markDirty" /></label>
                <label class="field"><span>温度</span><input type="number" step="0.1" v-model.number="cfg.tts.temperature" @input="markDirty" /></label>
              </div>
              <label class="check"><input type="checkbox" v-model="cfg.tts.auto_play" @change="markDirty" /> 合成后自动播放</label>
            </section>

            <section class="card">
              <div class="card-head"><h2>🎯 事件触发</h2><span class="hint">每个事件的开关 / 模式 / 冷却</span></div>
              <div v-for="em in EVENT_META" :key="em.key" class="event-row">
                <div class="ev-title">{{ em.icon }} {{ em.label }}</div>
                <label class="check"><input type="checkbox" v-model="cfg.events[em.key].enabled" @change="markDirty" /> 启用</label>
                <label class="field inline"><span>模式</span>
                  <select v-model="cfg.events[em.key].mode" @change="markDirty">
                    <option value="ai">AI 生成</option>
                    <option value="template">模板</option>
                  </select>
                </label>
                <label class="field inline"><span>概率%</span><input type="number" min="1" max="100" v-model.number="cfg.events[em.key].probability" @input="markDirty" /></label>
                <label class="field inline"><span>冷却(秒)</span><input type="number" v-model.number="cfg.events[em.key].cooldown_user" @input="markDirty" /></label>
              </div>
              <div class="field">
                <span>进房欢迎模板（每行一条，{name} 会替换成昵称）</span>
                <textarea v-model="enterTpl" @input="markDirty"></textarea>
                <button class="ghost sm" @click="addTpl('enter')">+ 加一条</button>
              </div>
              <div class="field">
                <span>关注感谢模板</span>
                <textarea v-model="followTpl" @input="markDirty"></textarea>
                <button class="ghost sm" @click="addTpl('follow')">+ 加一条</button>
              </div>
            </section>

            <section class="card">
              <div class="card-head"><h2>💬 固定回复</h2><span class="hint">命中关键词直接回固定句</span></div>
              <div v-for="(r, i) in fixedRows" :key="i" class="fixed-row">
                <input v-model="r.keywords" placeholder="关键词，逗号分隔" @input="markDirty" />
                <input v-model="r.reply" placeholder="回复内容" @input="markDirty" />
                <button class="ghost sm" @click="removeFixed(i); markDirty()">删</button>
              </div>
              <button class="ghost sm" @click="addFixed(); markDirty()">+ 添加一条</button>
            </section>

            <section class="card">
              <div class="card-head"><h2>🎵 点歌</h2><span class="hint">多音源自动回退</span></div>
              <div class="field">
                <span>音源优先级（勾选启用，按此顺序依次尝试）</span>
                <div class="chips">
                  <label v-for="src in SOURCE_LIST" :key="src.key" class="check" :class="{ dim: src.na }">
                    <input type="checkbox" :value="src.key" v-model="cfg.songs.sources" :disabled="src.na" @change="markDirty" />
                    {{ src.label }}<span v-if="src.na" class="muted">（暂无可用接口）</span>
                  </label>
                </div>
              </div>
              <label class="check"><input type="checkbox" v-model="cfg.songs.enabled" @change="markDirty" /> 启用点歌</label>
              <label class="check"><input type="checkbox" v-model="cfg.songs.internal" @change="markDirty" /> 使用内嵌网易云搜索</label>
              <label class="check"><input type="checkbox" v-model="cfg.songs.dedupe" @change="markDirty" /> 本场同歌去重</label>
              <div class="row2">
                <label class="field"><span>最大排队数</span><input type="number" v-model.number="cfg.songs.max_queue" @input="markDirty" /></label>
                <label class="field"><span>自动播放</span>
                  <select v-model="cfg.songs.auto_play" @change="markDirty">
                    <option :value="true">是</option>
                    <option :value="false">否</option>
                  </select>
                </label>
              </div>
            </section>

            <section class="card">
              <div class="card-head"><h2>🛡 过滤词</h2><span class="hint">弹幕命中就丢弃，每行一个</span></div>
              <textarea v-model="blockText" @input="markDirty"></textarea>
            </section>
          </div>
        </template>

        <!-- 关于 -->
        <div v-if="current === 'about'" class="card">
          <div class="card-head"><h2>关于</h2></div>
          <ul class="about">
            <li><b>deepseek-live</b> · 鲸语直播系统移植版（Vue 3 + Electron 33）</li>
            <li>核心：<code>app/main/core.cjs</code>（零依赖 Node，HTTP + WS + AI + TTS + 点歌）</li>
            <li>控制：IPC（window.whale.invoke）· OBS 叠加层走 HTTP</li>
            <li>皮套：pixi-live2d-display + Cubism 4（内置模型）</li>
            <li>仓库：<a href="https://github.com/a106832411/deepseek-live" target="_blank">github.com/a106832411/deepseek-live</a></li>
          </ul>
        </div>
      </div>
    </section>

    <transition name="fade">
      <div v-if="dirty" class="save-bar">
        <span>有未保存的配置修改</span>
        <button class="primary" :disabled="saving" @click="saveCfg">{{ saving ? '保存中…' : '保存' }}</button>
        <button class="ghost" @click="loadCfg">放弃</button>
      </div>
    </transition>
    <transition name="fade"><div v-if="toast" class="toast">{{ toast }}</div></transition>
  </div>
</template>

<style>
* { box-sizing: border-box; }
html, body, #app { height: 100%; }
body {
  margin: 0;
  color: var(--text);
  font-family: "Microsoft YaHei", "Segoe UI Emoji", system-ui, sans-serif;
  background: #060912;
  overflow: hidden;
}

/* ================= 主题变量 ================= */
.app {
  --bg0: #05080f;
  --bg1: #0a1020;
  --panel: rgba(17, 26, 47, .66);
  --panel-2: rgba(13, 20, 36, .8);
  --line: rgba(120, 160, 255, .13);
  --line-strong: rgba(120, 170, 255, .3);
  --text: #eaf2ff;
  --muted: #8aa2c8;
  --a1: #22d3ee;
  --a2: #a855f7;
  --a3: #f472b6;
  --ok: #34d399;
  --warn: #fbbf24;
  --err: #fb7185;
  position: relative;
  display: flex;
  height: 100vh;
  background:
    radial-gradient(900px 600px at 12% -10%, color-mix(in srgb, var(--a2) 22%, transparent) 0%, transparent 60%),
    radial-gradient(1000px 700px at 95% 8%, color-mix(in srgb, var(--a1) 18%, transparent) 0%, transparent 62%),
    radial-gradient(900px 700px at 60% 110%, color-mix(in srgb, var(--a3) 12%, transparent) 0%, transparent 60%),
    linear-gradient(160deg, var(--bg0), var(--bg1));
}
.theme-emerald { --a1: #34d399; --a2: #22d3ee; --a3: #a3e635; }
.theme-sunset  { --a1: #fb923c; --a2: #f472b6; --a3: #fbbf24; }
.theme-royal   { --a1: #60a5fa; --a2: #818cf8; --a3: #c084fc; }

/* 背景极光光斑动画 */
.app::before, .app::after {
  content: '';
  position: absolute;
  width: 620px; height: 620px;
  border-radius: 50%;
  filter: blur(120px);
  opacity: .35;
  pointer-events: none;
  z-index: 0;
}
.app::before { background: var(--a2); top: -220px; left: -120px; animation: float1 18s ease-in-out infinite; }
.app::after { background: var(--a1); bottom: -260px; right: -140px; animation: float2 22s ease-in-out infinite; }
@keyframes float1 { 0%,100% { transform: translate(0,0) scale(1); } 50% { transform: translate(60px,40px) scale(1.12); } }
@keyframes float2 { 0%,100% { transform: translate(0,0) scale(1); } 50% { transform: translate(-70px,-30px) scale(1.08); } }

/* ================= 侧栏 ================= */
.sidebar {
  position: relative; z-index: 2;
  width: 244px; flex: 0 0 244px;
  display: flex; flex-direction: column;
  background: linear-gradient(180deg, rgba(14, 22, 42, .88), rgba(8, 13, 26, .92));
  backdrop-filter: blur(18px);
  border-right: 1px solid var(--line);
  box-shadow: 8px 0 40px rgba(0, 0, 0, .45);
}
.sidebar::after {
  content: ''; position: absolute; top: 0; right: -1px; width: 1px; height: 100%;
  background: linear-gradient(180deg, transparent, var(--a1), var(--a2), transparent);
  opacity: .55;
}
.brand { display: flex; align-items: center; gap: 12px; padding: 20px 16px 16px; }
.brand .logo {
  width: 42px; height: 42px; display: grid; place-items: center; font-size: 23px;
  border-radius: 14px;
  background: linear-gradient(135deg, var(--a1), var(--a2));
  box-shadow: 0 8px 24px color-mix(in srgb, var(--a1) 35%, transparent), inset 0 1px 0 rgba(255, 255, 255, .35);
}
.brand .name { font-size: 16px; font-weight: 800; letter-spacing: .4px; }
.brand .ver { font-size: 11px; color: var(--muted); margin-top: 3px; letter-spacing: .6px; }

.tree { flex: 1; overflow-y: auto; padding: 6px 10px; }
.tree::-webkit-scrollbar { width: 6px; }
.tree::-webkit-scrollbar-thumb { background: var(--line-strong); border-radius: 3px; }
.group { margin-bottom: 8px; }
.group-head {
  display: flex; align-items: center; gap: 8px; width: 100%;
  background: transparent; border: 0; color: var(--muted);
  font-size: 12px; letter-spacing: 1.2px; padding: 9px 10px; cursor: pointer; border-radius: 10px;
  transition: color .15s, background .15s;
}
.group-head:hover { color: var(--text); background: rgba(120, 160, 255, .07); }
.chev { font-size: 10px; transition: transform .2s ease; color: var(--muted); }
.chev.open { transform: rotate(90deg); color: var(--a1); }
.gtitle { text-transform: uppercase; }
.children { display: flex; flex-direction: column; gap: 4px; padding-left: 10px; margin-left: 16px; border-left: 1px solid var(--line); }
.leaf {
  position: relative; display: flex; align-items: center; gap: 10px; width: 100%;
  text-align: left; background: transparent; border: 0; color: #c3d6f2;
  font-size: 13px; padding: 10px 12px; border-radius: 11px; cursor: pointer;
  transition: background .16s, color .16s, transform .12s;
}
.leaf:hover { background: rgba(120, 160, 255, .09); color: #fff; transform: translateX(2px); }
.leaf.active {
  color: #fff;
  background: linear-gradient(90deg, color-mix(in srgb, var(--a1) 26%, transparent), color-mix(in srgb, var(--a2) 16%, transparent));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--a1) 45%, transparent), 0 6px 18px color-mix(in srgb, var(--a1) 16%, transparent);
}
.leaf.active::before {
  content: ''; position: absolute; left: 0; top: 22%; bottom: 22%; width: 3px; border-radius: 3px;
  background: linear-gradient(180deg, var(--a1), var(--a2));
  box-shadow: 0 0 12px var(--a1);
}
.licon { font-size: 14px; filter: saturate(1.1); }

.theme-switch { display: flex; align-items: center; gap: 8px; padding: 10px 16px 4px; }
.ts-label { font-size: 11px; color: var(--muted); letter-spacing: 1px; }
.ts-dot { width: 16px; height: 16px; border-radius: 50%; border: 2px solid transparent; cursor: pointer; padding: 0; transition: transform .15s, box-shadow .15s; }
.ts-dot:hover { transform: scale(1.15); }
.ts-dot.active { border-color: rgba(255, 255, 255, .85); box-shadow: 0 0 12px rgba(255, 255, 255, .45); }

.side-foot {
  margin: 10px 12px 14px; padding: 12px 14px; border-radius: 14px;
  background: var(--panel-2); border: 1px solid var(--line);
  display: flex; gap: 10px; align-items: center; font-size: 12px; color: #c3d6f2;
}
.side-foot .tiny { font-size: 11px; color: var(--muted); margin-top: 2px; }
.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--err); flex: 0 0 9px; }
.dot.on { background: var(--ok); box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 22%, transparent), 0 0 14px var(--ok); animation: pulse 1.6s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .5; } }

/* ================= 主区 ================= */
.main { position: relative; z-index: 2; flex: 1; display: flex; flex-direction: column; min-width: 0; }
.topbar {
  height: 60px; flex: 0 0 60px; display: flex; align-items: center; padding: 0 24px;
  background: rgba(9, 15, 28, .6); backdrop-filter: blur(16px);
  border-bottom: 1px solid var(--line);
  position: relative;
}
.topbar::after {
  content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 1px;
  background: linear-gradient(90deg, transparent, var(--a1), var(--a2), transparent);
  opacity: .5;
}
.crumb { font-size: 13px; color: var(--muted); display: flex; align-items: center; }
.crumb b {
  font-size: 16px; color: #fff; background: linear-gradient(90deg, var(--a1), var(--a2));
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
}
.crumb .sep { color: var(--line-strong); margin: 0 10px; }
.top-actions { margin-left: auto; display: flex; align-items: center; gap: 12px; }
.pill {
  font-size: 11px; font-weight: 800; letter-spacing: 1.6px; padding: 6px 12px; border-radius: 999px;
  background: rgba(120, 160, 255, .1); color: var(--muted); border: 1px solid var(--line);
}
.pill.on {
  color: #06210f; border: 0;
  background: linear-gradient(90deg, var(--ok), color-mix(in srgb, var(--ok) 70%, #fff));
  box-shadow: 0 0 18px color-mix(in srgb, var(--ok) 45%, transparent);
}
.pill.off { color: var(--err); border-color: color-mix(in srgb, var(--err) 40%, transparent); background: color-mix(in srgb, var(--err) 12%, transparent); }

.content { flex: 1; overflow-y: auto; padding: 24px; animation: rise .28s ease-out; }
.content::-webkit-scrollbar { width: 8px; }
.content::-webkit-scrollbar-thumb { background: var(--line-strong); border-radius: 4px; }
@keyframes rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }

/* 按钮 */
button { font-family: inherit; }
.primary {
  background: linear-gradient(135deg, var(--a1), var(--a2));
  color: #06101f; font-weight: 800; border: 0; border-radius: 12px; padding: 10px 20px; font-size: 13px;
  cursor: pointer;
  box-shadow: 0 10px 26px color-mix(in srgb, var(--a1) 28%, transparent), inset 0 1px 0 rgba(255, 255, 255, .4);
  transition: transform .15s, filter .15s;
}
.primary:hover { filter: brightness(1.07); transform: translateY(-1px); }
.primary:disabled { opacity: .6; cursor: default; }
.ghost {
  background: rgba(120, 160, 255, .08); color: #d7e6ff; border: 1px solid var(--line-strong);
  border-radius: 12px; padding: 9px 16px; font-size: 13px; cursor: pointer;
  transition: background .15s, border-color .15s;
}
.ghost:hover { background: rgba(120, 160, 255, .16); border-color: var(--a1); }
.ghost.warn { background: color-mix(in srgb, var(--err) 14%, transparent); border-color: color-mix(in srgb, var(--err) 45%, transparent); color: #ffd4da; }
.ghost.sm { padding: 6px 12px; font-size: 12px; }

/* 统计卡 */
.stat-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px; margin-bottom: 20px; }
.stat-card {
  position: relative; border-radius: 18px; padding: 16px 18px;
  background: var(--panel);
  border: 1px solid var(--line);
  backdrop-filter: blur(12px);
  overflow: hidden;
  transition: transform .18s, border-color .18s, box-shadow .18s;
}
.stat-card::before {
  content: ''; position: absolute; inset: 0; border-radius: 18px;
  background: radial-gradient(120px 90px at 100% 0%, color-mix(in srgb, var(--ac) 35%, transparent), transparent 70%);
  opacity: .8;
}
.stat-card::after {
  content: ''; position: absolute; left: 18px; right: 18px; top: 0; height: 1px;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--ac) 70%, transparent), transparent);
}
.stat-card:hover { transform: translateY(-3px); border-color: color-mix(in srgb, var(--ac) 55%, transparent); box-shadow: 0 16px 40px color-mix(in srgb, var(--ac) 18%, transparent); }
.stat-card.c1 { --ac: #38bdf8; }
.stat-card.c2 { --ac: #22d3ee; }
.stat-card.c3 { --ac: #fbbf24; }
.stat-card.c4 { --ac: #c084fc; }
.stat-card.c5 { --ac: #fb7185; }
.stat-card.c6 { --ac: #34d399; }
.s-dot { width: 10px; height: 10px; border-radius: 3px; background: var(--ac); box-shadow: 0 0 14px var(--ac); }
.s-num {
  font-size: 30px; font-weight: 900; margin-top: 8px; letter-spacing: .5px;
  background: linear-gradient(90deg, #fff, color-mix(in srgb, var(--ac) 80%, #fff));
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
}
.s-label { font-size: 12px; color: var(--muted); margin-top: 3px; letter-spacing: 1px; }

/* 卡片 */
.grid2 { display: grid; grid-template-columns: 1fr 1.15fr; gap: 18px; }
.card {
  position: relative;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 20px;
  backdrop-filter: blur(14px);
  box-shadow: 0 18px 50px rgba(0, 0, 0, .28), inset 0 1px 0 rgba(255, 255, 255, .04);
}
.card-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 16px; }
.card-head h2 {
  font-size: 15px; margin: 0; font-weight: 800; letter-spacing: .4px;
  background: linear-gradient(90deg, #fff, color-mix(in srgb, var(--a1) 70%, #fff));
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
}
.card-head .hint { font-size: 12px; color: var(--muted); }

.chips { display: flex; gap: 10px; flex-wrap: wrap; }
.chip {
  background: rgba(120, 160, 255, .08); border: 1px solid var(--line-strong); color: #d7e6ff;
  border-radius: 999px; padding: 9px 16px; font-size: 13px; cursor: pointer;
  transition: all .16s;
}
.chip:hover { background: linear-gradient(135deg, color-mix(in srgb, var(--a1) 22%, transparent), color-mix(in srgb, var(--a2) 18%, transparent)); border-color: var(--a1); transform: translateY(-1px); }

/* 事件流 */
.feed { list-style: none; margin: 0; padding: 0; max-height: 460px; overflow: auto; }
.feed li { display: flex; align-items: center; gap: 10px; padding: 10px 4px; border-bottom: 1px dashed var(--line); font-size: 13px; }
.feed li.empty { color: var(--muted); }
.tag { font-size: 11px; padding: 3px 9px; border-radius: 999px; background: rgba(120, 160, 255, .12); color: #9fc4ff; flex: 0 0 auto; letter-spacing: .5px; }
.tag.enter { background: color-mix(in srgb, var(--ok) 18%, transparent); color: #a7f3d0; }
.tag.gift { background: rgba(251, 191, 36, .16); color: #fde68a; }
.tag.follow { background: rgba(192, 132, 252, .16); color: #e9d5ff; }
.tag.say { background: rgba(34, 211, 238, .16); color: #a5f3fc; }
.nick { color: var(--a1); font-weight: 700; flex: 0 0 auto; }
.txt { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #dbe9ff; }
.time { color: var(--muted); font-size: 11px; flex: 0 0 auto; }

.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.frames { display: flex; gap: 16px; flex-wrap: wrap; }
.fcol { flex: 1 1 320px; }
.flabel { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
.frame { width: 100%; height: 560px; border: 1px solid var(--line-strong); border-radius: 16px; background: #0a1020; box-shadow: 0 16px 40px rgba(0, 0, 0, .35); }
.checker { background: repeating-conic-gradient(rgba(120, 160, 255, .08) 0% 25%, rgba(10, 16, 32, .9) 0% 50%) 50% / 26px 26px; }

/* 点歌 */
.now-playing {
  margin: 14px 0; padding: 14px 16px; border-radius: 14px;
  background: linear-gradient(90deg, color-mix(in srgb, var(--a1) 18%, transparent), color-mix(in srgb, var(--a2) 12%, transparent));
  border: 1px solid var(--line-strong);
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
}
.eq { color: var(--ok); letter-spacing: -2px; animation: pulse 1.2s infinite; }
.queue { list-style: none; margin: 12px 0 0; padding: 0; }
.queue li { display: flex; align-items: center; gap: 12px; padding: 10px 4px; border-bottom: 1px dashed var(--line); font-size: 13px; }
.queue li.empty { color: var(--muted); }
.idx { width: 24px; color: var(--a1); font-weight: 800; }
.qname { flex: 1; }

/* 配置 */
.cfg-actions {
  display: flex; gap: 12px; align-items: center; margin-bottom: 18px;
  position: sticky; top: -24px; padding: 12px 0;
  background: linear-gradient(180deg, rgba(6, 9, 18, .96) 30%, rgba(6, 9, 18, 0));
  backdrop-filter: blur(6px); z-index: 3;
}
.dirty { color: var(--warn); font-size: 12px; }
.cfg-sections { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.cfg-sections .card { display: flex; flex-direction: column; gap: 12px; }
.field { display: flex; flex-direction: column; gap: 7px; font-size: 12px; color: var(--muted); }
.field > span { color: var(--muted); letter-spacing: .4px; }
.field.inline { flex-direction: row; align-items: center; gap: 10px; }
.field.inline > span { flex: 0 0 auto; }
.field.inline input, .field.inline select { width: auto; min-width: 90px; }
.field.inline b { color: #fff; }
.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

input, select, textarea {
  width: 100%; color: #e6f0ff;
  background: rgba(9, 15, 28, .85);
  border: 1px solid var(--line-strong);
  border-radius: 11px; padding: 10px 12px; font-size: 13px; font-family: inherit; outline: none;
  transition: border-color .15s, box-shadow .15s;
}
input:focus, select:focus, textarea:focus { border-color: var(--a1); box-shadow: 0 0 0 3px color-mix(in srgb, var(--a1) 18%, transparent); }
input::placeholder, textarea::placeholder { color: #5f7aa2; }
textarea { min-height: 96px; line-height: 1.6; resize: vertical; }
textarea.json { min-height: 300px; font-family: Consolas, monospace; }
.check { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #d7e6ff; }
.check input { width: auto; accent-color: var(--a1); }
.check.dim { opacity: .5; }
.chips .check { background: rgba(120, 160, 255, .08); border: 1px solid var(--line-strong); border-radius: 999px; padding: 8px 14px; }
.chips .check input { margin-right: 4px; }
.event-row {
  display: grid; grid-template-columns: 150px 84px 152px 120px 120px; gap: 10px; align-items: center;
  padding: 10px 6px; border-bottom: 1px dashed var(--line);
  border-radius: 10px;
}
.event-row:hover { background: rgba(120, 160, 255, .05); }
.ev-title { font-size: 13px; color: #e6f0ff; }
.fixed-row { display: grid; grid-template-columns: 1fr 1.5fr auto; gap: 10px; align-items: center; margin-bottom: 10px; }

.about { list-style: none; margin: 0; padding: 0; }
.about li { padding: 10px 0; border-bottom: 1px dashed var(--line); font-size: 13px; }
.muted { color: var(--muted); font-size: 12px; }
a { color: var(--a1); font-size: 13px; text-decoration: none; }
a:hover { text-decoration: underline; }

.save-bar {
  position: fixed; right: 24px; bottom: 24px;
  display: flex; align-items: center; gap: 12px;
  padding: 14px 18px; border-radius: 16px;
  background: rgba(12, 20, 38, .92); border: 1px solid var(--line-strong);
  box-shadow: 0 18px 50px rgba(0, 0, 0, .5), 0 0 0 1px color-mix(in srgb, var(--a1) 20%, transparent);
  backdrop-filter: blur(14px);
  font-size: 13px; color: #e6f0ff;
}
.toast {
  position: fixed; right: 24px; bottom: 96px;
  background: rgba(12, 20, 38, .95); border: 1px solid var(--line-strong);
  padding: 12px 18px; border-radius: 14px; font-size: 13px;
  box-shadow: 0 14px 40px rgba(0, 0, 0, .5);
}
.fade-enter-active, .fade-leave-active { transition: opacity .18s, transform .18s; }
.fade-enter-from, .fade-leave-to { opacity: 0; transform: translateY(6px); }
</style>
