# 鲸live · DeepLive

开源 AI 虚拟主播直播控制台：抖音弹幕接入 → DeepSeek 生成回复 → TTS 语音播报 → Live2D 皮套 + OBS 网页叠加层，全部自托管。

- **零依赖核心**：`app/main/core.cjs` 只用 Node 标准库，HTTP + WebSocket + AI + TTS + 点歌全在里面
- **桌面控制台**：Electron 主进程拉起核心服务，渲染层通过 IPC 控制（也可纯网页使用）
- **实时皮套**：pixi.js + pixi-live2d-display（Cubism 4），说话时自动口型、随机表情
- **多音源点歌**：网易云 + QQ 音乐，自动回退
- **OBS 友好**：`/live2d` `/overlay` `/songs` 三个透明网页源

## 目录结构

```
app/main/core.cjs      核心服务（零 npm 依赖）
app/main/ipc.cjs       IPC 通道表
electron/main.cjs      Electron 主进程（起服务 + 开窗口）
electron/preload.cjs   渲染层 API（window.whale.invoke）
src/                   Vue 3 控制台（Vite 构建）
public/                免构建页面：console/dashboard/live2d/overlay/songs
server/index.cjs       纯 Node 启动入口（无 Electron）
extras/python/         可选增强脚本（点歌/合成/游戏画面等）
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
| `DEEPLIVE_SONGS_ENABLED` / `_SOURCES` / `_MAX_QUEUE` | 点歌开关、音源顺序、队列长度 |
| `DEEPLIVE_OBS_HOST` / `_PORT` / `_PASSWORD` | OBS WebSocket 地址（默认 4455）与密码 |
| `DEEPLIVE_OBS_SCENE` / `_BG` | 自动创建的场景名、背景色（hex） |
| `DEEPLIVE_MERGE_WINDOW` | 同用户事件合并窗口（秒） |
| `DEEPLIVE_FILTER_BLOCKLIST` | 弹幕过滤词，逗号分隔 |

优先级：**真实环境变量 > `.env` > `config.json` > 内置默认值**。

## 地址

| 地址 | 用途 |
|---|---|
| `/console` | 完整控制台（网页版） |
| `/live2d` | Live2D 皮套（OBS 浏览器源，透明） |
| `/overlay` | 弹幕层（OBS 浏览器源） |
| `/songs` | 点歌列表（OBS 浏览器源） |
| `/dashboard` | 精简控制台 |

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
   - 歌单 `/songs`
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
| GET | `/api/songs/state` | 点歌队列 |
| POST | `/api/songs/request` | 点歌 `{name, query}` |
| WS | `/ws` | 事件/回复/日志/状态推送 |

## 桌面版特性

- 左侧菜单树控制台（会话 / 事件 / 配置 / 皮套 / 叠加层 / 歌单 / 点歌 / 说话）
- 全部配置表单化（AI、TTS、事件触发、固定回复、过滤词、点歌音源），也支持高级 JSON 编辑
- 黑金主题，可二次开发换肤

## 依赖服务

| 服务 | 用途 |
|---|---|
| DeepSeek API | AI 回复 |
| GPT-SoVITS（可选） | 语音克隆 TTS；不可用时自动降级 edge-tts |
| OBS Studio（可选） | 直播推流与叠加层合成 |
| 抖音中继脚本（可选） | 弹幕采集后 POST 到 `/api/douyin/ingest` |

## 许可

MIT
