<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue';
import { invoke } from './api';

const API = (typeof window !== 'undefined' && window.whale && window.whale.base)
  || (location.port === '5173' ? 'http://127.0.0.1:8788' : location.origin);

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
  <div class="app">
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
              <div class="card-head"><h2>🎵 点歌</h2></div>
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
body { margin: 0; background: radial-gradient(1200px 800px at 80% -10%, #16294a 0%, #0b1220 55%); color: #e8f1ff; font-family: "Microsoft YaHei", "Segoe UI Emoji", "Segoe UI Symbol", system-ui, sans-serif; }
.app { display: flex; height: 100vh; }

/* 侧栏 */
.sidebar { width: 236px; flex: 0 0 236px; display: flex; flex-direction: column; background: linear-gradient(180deg, #101c33 0%, #0b1424 100%); border-right: 1px solid #1d2c48; box-shadow: 4px 0 24px rgba(0, 0, 0, .35); }
.brand { display: flex; align-items: center; gap: 10px; padding: 16px 14px 14px; }
.brand .logo { width: 38px; height: 38px; display: grid; place-items: center; font-size: 22px; border-radius: 12px; background: linear-gradient(135deg, #1e5eff 0%, #23d5ab 100%); box-shadow: 0 6px 18px rgba(35, 150, 255, .35); }
.brand .name { font-size: 15px; font-weight: 700; letter-spacing: .3px; }
.brand .ver { font-size: 11px; color: #6f8bb3; margin-top: 2px; }
.tree { flex: 1; overflow-y: auto; padding: 4px 8px; }
.group { margin-bottom: 6px; }
.group-head { display: flex; align-items: center; gap: 7px; width: 100%; background: transparent; border: 0; color: #93a9cc; font-size: 12px; padding: 8px 8px; cursor: pointer; border-radius: 8px; }
.group-head:hover { background: #142242; color: #cfe2ff; }
.chev { font-size: 10px; color: #5b7aa6; transition: transform .15s; }
.chev.open { transform: rotate(90deg); }
.gtitle { letter-spacing: .6px; }
.children { display: flex; flex-direction: column; gap: 3px; padding-left: 10px; margin-left: 15px; border-left: 1px solid #1b2b49; }
.leaf { display: flex; align-items: center; gap: 9px; width: 100%; text-align: left; background: transparent; border: 0; color: #c2d6f0; font-size: 13px; padding: 9px 10px; border-radius: 9px; cursor: pointer; transition: background .12s; }
.leaf:hover { background: #152444; }
.leaf.active { background: linear-gradient(90deg, rgba(30, 94, 255, .32), rgba(35, 213, 171, .12)); color: #fff; box-shadow: inset 0 0 0 1px rgba(90, 160, 255, .45); }
.licon { font-size: 14px; }
.side-foot { margin: 8px; padding: 10px 12px; border-radius: 12px; background: #0d1930; border: 1px solid #1c2c49; display: flex; gap: 9px; align-items: center; font-size: 12px; color: #b9cde8; }
.side-foot .tiny { font-size: 11px; color: #6f8bb3; }
.dot { width: 9px; height: 9px; border-radius: 50%; background: #ff7b8a; flex: 0 0 9px; }
.dot.on { background: #35e39a; box-shadow: 0 0 0 3px rgba(53, 227, 154, .18); animation: pulse 1.6s infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .45; } }

/* 主区 */
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.topbar { height: 56px; flex: 0 0 56px; display: flex; align-items: center; padding: 0 20px; background: rgba(13, 22, 40, .82); backdrop-filter: blur(6px); border-bottom: 1px solid #1c2b49; }
.crumb { font-size: 13px; color: #cfe2ff; }
.crumb .sep { color: #3c5a86; margin: 0 8px; }
.top-actions { margin-left: auto; display: flex; align-items: center; gap: 10px; }
.pill { font-size: 11px; font-weight: 700; letter-spacing: 1px; padding: 4px 10px; border-radius: 999px; background: #1b2a45; color: #9fc4ff; }
.pill.on { background: linear-gradient(90deg, #12452f, #1d6b4a); color: #6ef2b0; }
.pill.off { background: #3a1a1f; color: #ff8f9a; }
.content { flex: 1; overflow-y: auto; padding: 20px; }

button { font-family: inherit; }
.primary { background: linear-gradient(135deg, #2b6cff, #1f9fff); color: #fff; border: 0; border-radius: 10px; padding: 9px 18px; font-size: 13px; font-weight: 600; cursor: pointer; box-shadow: 0 6px 16px rgba(43, 108, 255, .28); }
.primary:hover { filter: brightness(1.08); }
.ghost { background: #14233d; color: #cfe2ff; border: 1px solid #27406a; border-radius: 10px; padding: 8px 14px; font-size: 13px; cursor: pointer; }
.ghost:hover { background: #1a2d4e; }
.ghost.warn { background: #4a2230; border-color: #7d3350; color: #ffc3cc; }
.ghost.sm { padding: 5px 10px; font-size: 12px; }

/* 统计卡 */
.stat-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 16px; }
.stat-card { border-radius: 14px; padding: 14px; background: #101d34; border: 1px solid #1d2f4f; position: relative; overflow: hidden; }
.stat-card::after { content: ''; position: absolute; inset: 0; background: linear-gradient(135deg, var(--glow), transparent 60%); opacity: .5; }
.stat-card .s-dot { width: 10px; height: 10px; border-radius: 3px; background: #58a0ff; box-shadow: 0 0 10px currentColor; }
.stat-card.c1 .s-dot { background: #58a0ff; color: #58a0ff; }
.stat-card.c2 .s-dot { background: #23d5ab; color: #23d5ab; }
.stat-card.c3 .s-dot { background: #ffaa3c; color: #ffaa3c; }
.stat-card.c4 .s-dot { background: #c878ff; color: #c878ff; }
.stat-card.c5 .s-dot { background: #ff6982; color: #ff6982; }
.stat-card.c6 .s-dot { background: #78dcff; color: #78dcff; }
.stat-card .s-num { font-size: 26px; font-weight: 800; margin-top: 4px; letter-spacing: .5px; }
.stat-card .s-label { font-size: 12px; color: #93a9cc; margin-top: 2px; }
.stat-card.c1 { --glow: rgba(88, 160, 255, .25); }
.stat-card.c2 { --glow: rgba(35, 213, 171, .22); }
.stat-card.c3 { --glow: rgba(255, 170, 60, .22); }
.stat-card.c4 { --glow: rgba(200, 120, 255, .22); }
.stat-card.c5 { --glow: rgba(255, 105, 130, .2); }
.stat-card.c6 { --glow: rgba(120, 220, 255, .2); }

.grid2 { display: grid; grid-template-columns: 1fr 1.15fr; gap: 16px; }
.card { background: rgba(16, 29, 52, .85); border: 1px solid #1d2f4f; border-radius: 14px; padding: 16px; }
.card-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 12px; }
.card-head h2 { font-size: 14px; margin: 0; color: #cfe2ff; font-weight: 700; }
.card-head .hint { font-size: 12px; color: #6f8bb3; }
.chips { display: flex; gap: 8px; flex-wrap: wrap; }
.chip { background: #16263f; border: 1px solid #27406a; color: #cfe2ff; border-radius: 999px; padding: 8px 14px; font-size: 13px; cursor: pointer; }
.chip:hover { background: #1c3153; border-color: #3a5c8f; }

.feed { list-style: none; margin: 0; padding: 0; max-height: 440px; overflow: auto; }
.feed li { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px dashed #1a2740; font-size: 13px; }
.feed li.empty { color: #6f8bb3; }
.tag { font-size: 11px; padding: 2px 7px; border-radius: 6px; background: #1c2f4d; color: #9fc4ff; flex: 0 0 auto; }
.tag.enter { background: #173a2c; color: #6ef2b0; }
.tag.gift { background: #3d2b12; color: #ffcf8a; }
.tag.follow { background: #2f2347; color: #cfa8ff; }
.tag.say { background: #12303f; color: #7fe0ff; }
.nick { color: #7fd0ff; font-weight: 600; flex: 0 0 auto; }
.txt { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #dbe9ff; }
.time { color: #5b7aa6; font-size: 11px; flex: 0 0 auto; }

.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.frames { display: flex; gap: 14px; flex-wrap: wrap; }
.fcol { flex: 1 1 320px; }
.flabel { font-size: 12px; color: #93a9cc; margin-bottom: 6px; }
.frame { width: 100%; height: 560px; border: 1px solid #1d2f4f; border-radius: 12px; background: #0c1526; }
.checker { background: repeating-conic-gradient(#1b2740 0% 25%, #131d31 0% 50%) 50% / 24px 24px; }

.now-playing { margin: 12px 0; padding: 12px 14px; border-radius: 12px; background: linear-gradient(90deg, rgba(30, 94, 255, .22), rgba(35, 213, 171, .1)); border: 1px solid #27406a; }
.eq { color: #35e39a; letter-spacing: -2px; margin-right: 6px; animation: pulse 1.2s infinite; }
.queue { list-style: none; margin: 10px 0 0; padding: 0; }
.queue li { display: flex; align-items: center; gap: 10px; padding: 9px 0; border-bottom: 1px dashed #1a2740; font-size: 13px; }
.queue li.empty { color: #6f8bb3; }
.idx { width: 22px; color: #7fb6ff; font-weight: 700; }
.qname { flex: 1; }

/* 配置 */
.cfg-actions { display: flex; gap: 10px; align-items: center; margin-bottom: 14px; position: sticky; top: -20px; padding: 10px 0; background: rgba(11, 18, 32, .92); backdrop-filter: blur(6px); z-index: 3; }
.dirty { color: #ffcc80; font-size: 12px; }
.cfg-sections { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.cfg-sections .card { display: flex; flex-direction: column; gap: 10px; }
.field { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: #93a9cc; }
.field > span { color: #93a9cc; }
.field.inline { flex-direction: row; align-items: center; gap: 8px; }
.field.inline > span { flex: 0 0 auto; }
.field.inline input, .field.inline select { width: auto; min-width: 90px; }
.field.inline b { color: #cfe2ff; }
.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
input, select, textarea { width: 100%; background: #0c1729; color: #e3efff; border: 1px solid #24405f; border-radius: 9px; padding: 9px 11px; font-size: 13px; font-family: inherit; outline: none; }
input:focus, select:focus, textarea:focus { border-color: #3b7bd8; box-shadow: 0 0 0 3px rgba(59, 123, 216, .16); }
textarea { min-height: 92px; line-height: 1.55; resize: vertical; }
textarea.json { min-height: 300px; font-family: Consolas, monospace; }
.check { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #cfe2ff; }
.check input { width: auto; }
.event-row { display: grid; grid-template-columns: 140px 84px 150px 120px 120px; gap: 10px; align-items: center; padding: 8px 0; border-bottom: 1px dashed #1a2740; }
.ev-title { font-size: 13px; color: #dbe9ff; }
.fixed-row { display: grid; grid-template-columns: 1fr 1.4fr auto; gap: 8px; align-items: center; margin-bottom: 8px; }

.about { list-style: none; margin: 0; padding: 0; }
.about li { padding: 8px 0; border-bottom: 1px dashed #1a2740; font-size: 13px; }
.small { font-size: 12px; }
a { color: #7fb6ff; font-size: 13px; }
.muted { color: #6f8bb3; font-size: 12px; }
.save-bar { position: fixed; right: 20px; bottom: 20px; display: flex; align-items: center; gap: 10px; padding: 12px 16px; border-radius: 14px; background: #14233d; border: 1px solid #2f5287; box-shadow: 0 10px 30px rgba(0, 0, 0, .4); font-size: 13px; color: #cfe2ff; }
.toast { position: fixed; right: 20px; bottom: 86px; background: #1d3a66; border: 1px solid #2f5287; padding: 10px 16px; border-radius: 12px; font-size: 13px; box-shadow: 0 8px 24px rgba(0, 0, 0, .4); }
.fade-enter-active, .fade-leave-active { transition: opacity .18s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
