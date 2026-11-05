# deepseek-live · 鲸语直播系统（Vue + Electron 移植版）

DeepSeek 驱动的 AI 虚拟主播直播系统：抖音弹幕接入 → AI 回复 → TTS 语音 → OBS 网页叠加层，全套自托管。

> 本仓库是原版 WhaleTalk（PyInstaller exe）的**移植版**，保持原 HTTP API 兼容，
> 所以原有的抖音中继、OBS 网页源、看门狗脚本都可以直接继续用。

## 快速开始（3 步）

```bash
git clone https://github.com/a106832411/deepseek-live.git
cd deepseek-live
cp config.example.json config.json    # Windows: copy config.example.json config.json
```

编辑 `config.json`，填三样东西：

1. `ai.openai_key` —— DeepSeek API Key（https://platform.deepseek.com）
2. `tts.ref_audio` / `tts.prompt_text` —— GPT-SoVITS 参考音频路径与对应文字（本机需跑 9881 TTS 服务）
3. `douyin.web_rid` —— 你的抖音直播间号

然后启动（**不需要装任何 npm 依赖**就能跑服务端）：

```bash
node server/index.cjs --port 8788
```

| 地址 | 用途 |
|---|---|
| http://127.0.0.1:8788/dashboard | 控制台（会话/统计/事件/手动说话/配置） |
| http://127.0.0.1:8788/overlay | 弹幕层（OBS 浏览器源） |
| http://127.0.0.1:8788/songs | 点歌列表（OBS 浏览器源） |

## Electron 桌面版（可选）

```bash
npm install            # 或 pnpm install
npm run electron       # 打开桌面窗口，内部自动起 8788 服务
```

## 架构

```
抖音（经中继 gift_scraper）──POST /api/douyin/ingest──┐
                                                      ▼
                             server/core.cjs（Node，零依赖）
                        ┌─────────────┼──────────────┐
                        ▼             ▼              ▼
                   DeepSeek AI    GPT-SoVITS     WebSocket /ws
                   回复文本        9881 合成语音    ├─ overlay.html（OBS 弹幕层）
                        │             │            └─ songs.html（歌单）
                        └──── 播放 wav ─┘
```

## 目录

```
server/core.cjs     核心服务：HTTP API + WS + AI + TTS + 事件处理（零 npm 依赖）
server/index.cjs    启动入口：node server/index.cjs --port 8788
public/             控制台 / 弹幕层 / 歌单 三个页面
electron/           Electron 主进程（内嵌服务 + 窗口）
src/App.vue         Vue 版控制台
extras/python/      可选：点歌播放、真游戏采集桥、PvZ AI（见该目录 README）
docs/移植说明.md     功能对照与移植细节
scripts/            Windows 一键脚本
```

## API（与鲸语原版兼容）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/session/start` · `/api/session/stop` | 开始/结束会话 |
| POST | `/api/douyin/ingest` | 抖音事件投递 `{events:[...]}` |
| POST | `/api/say` | 手动说话 `{text}` |
| POST | `/api/test_event` | 测试事件 `{event:{...}}` |
| GET/POST | `/api/config` | 读/改配置 |
| GET | `/api/health` `/api/douyin/health` `/api/recent` | 状态 |
| POST | `/api/stats/reset` | 清空统计 |
| WS | `/ws` | 推送 event / reply / speak / status / hello |

事件格式：

```json
{"type":"danmaku","name":"用户名","text":"你好"}
{"type":"enter","name":"用户名"}
{"type":"gift","name":"用户名","gift":"小心心","num":1,"value":0.1}
{"type":"follow","name":"用户名"}
{"type":"like","name":"用户名"}
```

## 功能对照

| 功能 | 状态 |
|---|---|
| 会话 / 事件（进房·弹幕·礼物·关注·点赞）/ 冷却 / 模板 / 固定回复 | ✅ |
| DeepSeek AI 回复 | ✅ |
| TTS（GPT-SoVITS 9881） | ✅（需本机 TTS 服务） |
| OBS 弹幕层 / 歌单 / 控制台 | ✅ |
| 点歌（转交点歌系统） | ✅ |
| 真游戏画面采集桥（OBS 抓不到老 DirectDraw 游戏时用） | ✅ extras/python/gamecam.py |
| Live2D 皮套渲染 | ⏳ 待移植（建议 pixi-live2d-display） |
| 自动歌单播放 | ⏳ 先用 extras/python/songbot.py |

## License

MIT（移植代码本身）。GPT-SoVITS、DeepSeek、抖音相关服务请遵守各自条款；直播内容需遵守平台规则（AI 主播建议明示）。
