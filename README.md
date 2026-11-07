# 鲸live · DeepLive

开源 AI 虚拟主播直播控制台：抖音弹幕接入 → DeepSeek 生成回复 → TTS 语音播报 → Live2D 皮套 + OBS 网页叠加层，全部自托管。

- **零依赖核心**：`app/main/core.cjs` 只用 Node 标准库，HTTP + WebSocket + AI + TTS + 点歌/清唱全在里面
- **桌面控制台**：Electron 主进程拉起核心服务，渲染层通过 IPC 控制（也可纯网页使用）
- **实时皮套**：pixi.js + pixi-live2d-display（Cubism 4），说话时自动口型、随机表情
- **清唱点歌**：弹幕点歌 → AI 用自己的 TTS 声音清唱（抓歌词 → 逐句合成 → WORLD 加旋律 → 串成一首），
  不播原曲、无版权风险；旧版「播放原曲」保留（`songs.perform=play`），见 [docs/清唱.md](docs/清唱.md)
- **OBS 友好**：`/live2d` `/overlay` `/songs` `/subtitle` 透明网页源

## 目录结构

```
app/main/core.cjs      核心服务（零 npm 依赖）
app/main/ipc.cjs       IPC 通道表
electron/main.cjs      Electron 主进程（起服务 + 开窗口）
electron/preload.cjs   渲染层 API（window.whale.invoke）
src/                   Vue 3 控制台（Vite 构建）
public/                免构建页面：console/dashboard/live2d/overlay/songs
server/index.cjs       纯 Node 启动入口（无 Electron）
extras/python/         可选增强脚本（清唱音频管线 sing_tts.py/点歌/合成/游戏画面等）
```

## 快速开始

```bash
git clone https://github.com/a106832411/deepseek-live.git
cd deepseek-live
copy config.example.json config.json      # Linux/macOS: cp
```

编辑 `config.json` 填三项：

1. `ai.openai_key` —— DeepSeek API Key（https://platform.deepseek.com）
2. `tts.ref_audio` / `tts.prompt_text` —— GPT-SoVITS 参考音频路径与对应文字（本机需跑 TTS 服务）
3. `douyin.web_rid` —— 抖音直播间号

启动：

```bash
node server/index.cjs --port 8788     # 纯服务
npm install && npm run electron        # 桌面控制台（自动起 8788 + IPC）
```

## 环境变量（.env）

推荐用 `.env` 管密钥，不要写进 `config.json`：

```bash
copy .env.example .env      # Linux/macOS: cp
```

常用变量：

| 变量 | 说明 |
|---|---|
| `DEEPLIVE_PORT` | 服务端口，默认 8788 |
| `DEEPLIVE_AI_BASE` / `DEEPLIVE_AI_MODEL` / `DEEPLIVE_AI_KEY` | DeepSeek 接口、模型、Key |
| `DEEPLIVE_AI_MAX_CHARS` / `DEEPLIVE_AI_TEMPERATURE` | 回复字数上限、温度 |
| `DEEPLIVE_PERSONA` | 主播人设（system prompt），换行写 `\n` |
| `DEEPLIVE_TTS_URL` | TTS 代理地址（默认 127.0.0.1:9881） |
| `DEEPLIVE_TTS_REF_AUDIO` / `DEEPLIVE_TTS_PROMPT_TEXT` | 参考音频路径与对应文字 |
| `DEEPLIVE_TTS_SPEED` / `_STEPS` / `_TOP_K` / `_TEMPERATURE` | 合成参数 |
| `DEEPLIVE_DOUYIN_MODE` / `DEEPLIVE_DOUYIN_RID` | 抖音接入方式（relay/browser）与房间号 |
| `DEEPLIVE_SONGS_ENABLED` / `_SOURCES` / `_MAX_QUEUE` | 点歌/清唱开关、音源顺序（play 模式）、队列长度 |
| `DEEPLIVE_SONGS_PERFORM` | 唱歌方式：`sing` 清唱（默认）/ `play` 播放原曲（旧行为） |
| `DEEPLIVE_SING_ENABLED` / `_MODE` / `_LYRICS` | 清唱开关、唱法 `melody`（带旋律）/`read`（纯念唱）、歌词来源 `netease`（抓原词）/`ai`（AI 现编） |
| `DEEPLIVE_SING_MAX_LINES` / `_MAX_CHARS` | 每首唱几句（默认 6）、整首歌词字数上限（默认 100） |
| `DEEPLIVE_SING_INTRO` / `_OUTRO` / `_FILLER` | 开唱报幕 / 唱完道谢 / 合成等待时的垫话（`false` 可关） |
| `DEEPLIVE_SING_MELODY` / `_BPM` / `_VIBRATO` / `_GAP_MS` | 旋律（auto/xiaoxingxing/shengri/folk1/folk2）、节拍、颤音、句间停顿 |
| `DEEPLIVE_OBS_HOST` / `_PORT` / `_PASSWORD` | OBS WebSocket 地址（默认 4455）与密码 |
| `DEEPLIVE_OBS_SCENE` / `_BG` | 自动创建的场景名、背景色（hex） |
| `DEEPLIVE_MERGE_WINDOW` | 同用户事件合并窗口（秒） |
| `DEEPLIVE_FILTER_BLOCKLIST` | 弹幕过滤词，逗号分隔 |

优先级：**真实环境变量 > `.env` > `config.json` > 内置默认值**。

> 注意：清唱参数如果在 `.env` 里写了（如 `DEEPLIVE_SING_FILLER`），会覆盖 `config.json`/控制台里改的值。

## 清唱（唱歌 = 用主播自己的声音清唱）

默认 `songs.perform=sing`：弹幕点歌不再播放原曲，而是抓网易云歌词 → 逐句 TTS 合成 →
WORLD 给每句加旋律 → 串成一首无伴奏清唱，播报/字幕/皮套口型全部跟着 timing 走。

```bash
# 弹幕里发（全角斜杠/冒号/书名号都兼容）
唱歌                 # 不带歌名：从 sing.pick_list 随机
唱歌 稻香            # 「唱歌/唱一首/来一首」类需要空格或冒号分隔歌名
清唱 晴天
点歌 七里香
/点歌 宁夏
跳过                 # 跳过当前清唱 / 排队中的下一首
```

- 需要本地 **GPT-SoVITS TTS**（见下文「依赖服务」）；参考音配 `DEEPLIVE_TTS_REF_AUDIO`。
- 控制台「清唱队列」看当前/排队，「配置 → 🎤 清唱」改唱法（melody/read）、歌词来源、句数、BPM、垫话等。
- 想回到旧行为（播原曲）：`.env` 里 `DEEPLIVE_SONGS_PERFORM=play` 或控制台「表演模式 → 播放原曲」，
  旧接口/旧状态形状都保留。
- 详细原理、参数、API、排障见 **[docs/清唱.md](docs/清唱.md)**。

## 地址

| 地址 | 用途 |
|---|---|
| `/console` | 完整控制台（网页版） |
| `/live2d` | Live2D 皮套（OBS 浏览器源，透明） |
| `/overlay` | 弹幕层（OBS 浏览器源） |
| `/songs` | 清唱列表 / 当前歌词（OBS 浏览器源） |
| `/subtitle` | 清唱歌词字幕条（OBS 浏览器源） |
| `/dashboard` | 精简控制台 |

## 星露谷 AI（本地模型）

AI 玩星露谷走**本机模型、0 token**；鲸live 的直播回复仍走 DeepSeek，两套互不影响：

```
鲸live 直播回复  → DeepSeek（云端，$DEEPLIVE_AI_*）
星露谷 AI        → Ollama qwen25-7b-local（本机，$STARDEW_LLM_*）
```

```powershell
python extras/python/stardew_agent.py --dry          # 只出计划
python extras/python/stardew_agent.py --once         # 执行一轮
python extras/python/stardew_agent.py --loop         # 循环
```

架构、能力和限制见 [docs/星露谷AI.md](docs/星露谷AI.md)。

## 本地模型（省钱模式）

不想按 token 付费，可以用本机 Ollama 跑 7B 模型，token 成本归零：

```ini
DEEPLIVE_AI_BASE=http://127.0.0.1:11434/v1
DEEPLIVE_AI_MODEL=qwen2.5:7b
DEEPLIVE_AI_KEY=ollama
```

详细步骤（模型下载、测速、常见问题）见 [docs/本地模型.md](docs/本地模型.md)。

## OBS 一键配置

控制台「形象与画面 → OBS 一键配置」，或直接调接口：

```bash
curl -X POST http://127.0.0.1:8788/api/obs/probe   -d '{}'   # 连接测试
curl -X POST http://127.0.0.1:8788/api/obs/setup   -d '{}'   # 一键建场景
```

会自动在 OBS 里：

1. 创建/复用场景（默认名 `鲸live`，可配置）
2. 加背景色层（默认 `#0b0c0e`）
3. 加 3 个浏览器源并按画布自动摆位（横屏/竖屏自动判断）：
   - 皮套 `/live2d?scale=0.95`
   - 弹幕层 `/overlay`
   - 清唱列表 `/songs`（需要歌词字幕可再加一个 `/subtitle`）
4. 切到该场景

需要在 OBS 里开启：**工具 → WebSocket 服务器设置 → 启用**，把端口/密码填进 `.env` 或控制台。

## 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/session/start` `/api/session/stop` | 开始/结束会话 |
| POST | `/api/douyin/ingest` | 弹幕中继投递（外部脚本 → 服务） |
| POST | `/api/say` | 手动让主播说话（走 TTS） |
| GET/POST | `/api/config` | 读/改配置（即时生效） |
| POST | `/api/test_event` | 注入测试事件 |
| GET | `/api/songs/state` | 清唱状态 `{mode:'sing', sing{enabled,style,lyrics}, current{lyrics,lyricIndex,audio,duration}, queue}`（`perform=play` 时返回旧形状） |
| POST | `/api/songs/request` | 点歌/清唱 `{name, query}`；返回 `{ok, position, song}` |
| POST | `/api/songs/skip` | 跳过（正在唱→打断；排队→删队首；查歌词窗口→3 秒内取消；空闲→无操作），见 [docs/清唱.md](docs/清唱.md) |
| WS | `/ws` | 事件/回复/日志/状态推送；清唱事件 `sing start/line/end`、`song queued` |

## 桌面版特性

- 左侧菜单树控制台（会话 / 事件 / 配置 / 皮套 / 叠加层 / 清唱队列 / 说话）
- 全部配置表单化（AI、TTS、事件触发、固定回复、过滤词、清唱/点歌），也支持高级 JSON 编辑
- 黑金主题，可二次开发换肤

## 依赖服务

| 服务 | 用途 |
|---|---|
| DeepSeek API | AI 回复 |
| GPT-SoVITS（清唱必需） | 语音克隆 TTS（9881 代理 → 9882）：说话和清唱都用它；TTS 不通时清唱会退化成逐句念唱/无声，但不会卡队列 |
| OBS Studio（可选） | 直播推流与叠加层合成 |
| 抖音中继脚本（可选） | 弹幕采集后 POST 到 `/api/douyin/ingest` |

## 许可

MIT
