# 唱歌工坊跑 WSL GPU：桥接方案

> 目标：DSH 插件 **唱歌工坊（dsh-song-studio）** 的「做歌」按钮，实际用 **WSL 里的 ROCm python + RX 7900 GRE** 跑
> demucs 分离 + RVC 换声，而不是 Windows 上那套 CPU 环境。

## 为什么需要桥

插件源码里是：

```js
spawn(pythonPath, [cover.py, '--input', winPath, '--config', winConfig, ...])
```

- `pythonPath` 必须是 Windows 上真实存在的可执行文件 → WSL 的 python 不能直接填；
- 传给 cover.py 的全是 Windows 路径（`C:\...`、`D:\...`）→ WSL 里的 python 打不开。

所以加一层：

```
唱歌工坊插件
   └─ spawn(song-studio-wsl.exe, [cover.py, --input C:\..., --config C:\...])
        └─ C:\Python312\python.exe bridge.py <参数>
             ├─ 读插件生成的 cover-config.json，把 ffmpeg/voices_file/out_dir/work_dir 换成 /mnt/x/...
             ├─ 把 --input / --config 也换成 /mnt/x/...
             └─ wsl.exe -d Ubuntu-22.04 -u root -- env PYTHONPATH=/opt/rvc/compat /opt/rvc/bin/python
                  /mnt/d/.../extras/python/song-cover/cover.py --input /mnt/... --config /mnt/...
```

成品 mp3 由 WSL 里的 cover.py 写到 `out_dir`，因为走 `/mnt/d/...`，Windows 这边**直接就能看到**，
插件自己扫 `out_dir` 里的 `*_full.mp3` 判断成品，不用额外拷回。

## 文件

| 文件 | 说明 |
|---|---|
| `extras/python/song-studio-wsl/bridge.py` | 路径翻译 + 调 WSL（仓库内，随包走） |
| `tools/song-studio-wsl/song-studio-wsl.exe` | 启动器（C，124KB），插件「配置 → Python」填它 |
| `tools/song-studio-wsl/main.c` | 启动器源码 |

启动器默认去找：
- `C:\Python312\python.exe` 跑 bridge.py（可被 `SONG_STUDIO_WSL_PYTHON` 覆盖）
- 仓库里的 `extras/python/song-studio-wsl/bridge.py`（可被 `SONG_STUDIO_WSL_BRIDGE` 覆盖）

bridge.py 支持的环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SONG_WSL_DISTRO` | `Ubuntu-22.04` | WSL 发行版 |
| `SONG_WSL_USER` | `root` | WSL 用户 |
| `SONG_WSL_PYTHON` | `/opt/rvc/bin/python` | WSL 里的 python |
| `SONG_WSL_COMPAT` | `/opt/rvc/compat` | fairseq shim（免装 fairseq） |
| `SONG_WSL_COVER` | `/mnt/d/.../extras/python/song-cover/cover.py` | WSL 里的流水线脚本 |
| `SONG_WSL_PRINT_ONLY` | 空 | `1` = 只打印命令不执行（自检） |

## 配到唱歌工坊里

插件「设置 → 唱歌工坊 → 配置」：

| 字段 | 填什么 |
|---|---|
| Python | `D:\文档\deepseek-harness\default-workspace\tools\song-studio-wsl\song-studio-wsl.exe` |
| ffmpeg | `C:\Program Files\nodejs\ffmpeg.exe`（会被 bridge 换成 WSL 的 /usr/bin/ffmpeg） |
| 声线表 | Windows 下的 `voices.json`（bridge 会转成 /mnt 路径） |
| 音频目录 | 留空或填素材目录 |
| 输出目录 | `D:\文档\deepseek-harness\default-workspace\tmp\song-studio\out`（/mnt 可直接看到） |

然后重启 DSH / 刷新设置页，「做歌」就跑到 GPU 上了。

## 自检（不依赖 WSL）

```powershell
$env:SONG_WSL_PRINT_ONLY='1'
tools\song-studio-wsl\song-studio-wsl.exe `
  C:\Users\49820\Desktop\deepseek-live\extras\python\song-cover\cover.py `
  --input <某个 wav> --voice 我的声线 --config <插件生成的 cover-config.json> `
  --f0 0 --clip-seconds 0 --name 晴天
```

应打印一行 `PRINT_ONLY 命令: wsl.exe -d Ubuntu-22.04 ... /mnt/...`，exit 0。
（2026-11-11 已验证：`--input`/`--config`/`voices_file`/`out_dir` 都正确转成 `/mnt/...`，ffmpeg 换成 `/usr/bin/ffmpeg`。）

## 可选：不换插件配置，直接用鲸live

鲸live 侧已经内置同样的 WSL 调用（`sing.engine="rvc"`，`sing.rvc.mode="wsl"`），
不需要这个 exe；桥只是给插件 UI 用的。
