# extras/python（可选）

这些脚本不是核心服务，按需使用：

| 文件 | 作用 | 依赖 |
|---|---|---|
| `songbot.py` | 独立点歌系统：读 `tools/logs/song_requests.jsonl`，网易云匿名搜索→播放→播报 | pip: aiohttp numpy soundfile librosa sounddevice |
| `gamecam.py` | 把游戏窗口截帧成 MJPEG（`http://127.0.0.1:9800/cam`），OBS 用浏览器源接它 —— 解决 OBS 抓不到老 DirectDraw 游戏（白屏）的问题 | pip: windows-capture aiohttp pillow |
| `real_ai.py` `util.py` `brain.py` `winrt_ocr.py` | 用 DeepSeek 自动玩《植物大战僵尸》真游戏（截图识别 → 决策 → 点击） | pip: windows-capture pillow numpy opencv-python |
| `pvz_config.example.json` | PvZ AI 的坐标配置模板 | — |

这些脚本里的路径（如 `D:\gsv_rocm_gpu\venv`、`deeplive\...`）是按开发机写的，
克隆后请把路径改成你自己的，或设置对应环境变量。

启动示例：

```bash
python extras/python/gamecam.py           # OBS 浏览器源接 http://127.0.0.1:9800/cam
python extras/python/songbot.py           # 点歌播放
python extras/python/real_ai.py --loop    # AI 自动玩 PvZ
```