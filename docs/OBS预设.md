# OBS 完整直播预设（game）素材说明

> 一句话：完整直播预设里有两张图片是**随包的本地素材**，放在 `assets/obs/`；想换图就换 `assets/obs/` 下的同名文件。
> 代码位置：`app/main/obs.cjs` 的 `presetSources()` / `presetAsset()`；打包配置见 `electron-builder.yml` 的 `asarUnpack`。

## 1. 完整预设包含哪些源

「完整直播预设」（设置里选 `game`，控制台文案「完整直播（旧版场景）」）一键创建/复用场景，按 1080×1920 基准布局（`scaleSpec()` 会按 OBS 当前画布等比缩放）摆放：

| 顺序 | 源名 | 类型 | 内容 |
| --- | --- | --- | --- |
| 1 | 背景板 | image（**随包图片**） | 星空鲸海背景板 |
| 2 | 游戏画面 | browser | `http://127.0.0.1:9800/cam`（采集卡/游戏画面） |
| 3 | 文字叠加 | image（**随包图片**） | 竖屏文字版式叠加层 |
| 4 | 皮套 | browser | `/live2d?scale=0.95` |
| 5 | 本场数据 | text | 文字源（可自行改成浏览器源） |
| 6 | 歌单 | browser | `/songs` |
| 7 | 字幕 | browser | `/overlay` |
| 8 | 弹幕 | browser | `/overlay` |
| 9 | 游戏面板 | browser（默认隐藏） | `/overlay` |

另外 `setupScene()` 会先建一个纯色源「背景」（OBS 场景底色），与这两张图无关。

> 简单预设（`simple`，默认）只建 3 个浏览器源：皮套、弹幕层、歌单，**不依赖这两张图**。

## 2. 两张图现在在仓库哪里

| 文件 | 尺寸 | 大小 | 在仓库里的路径 |
| --- | --- | --- | --- |
| `bg3_星空鲸海.png`（背景板） | 1080×1920 | 2.44 MB | `assets/obs/bg3_星空鲸海.png` |
| `叠加层_竖屏_文字_v2.png`（文字叠加） | 1080×1920 | 0.09 MB | `assets/obs/叠加层_竖屏_文字_v2.png` |

这两张图随仓库一起提交（不再依赖本机 `D:\文档\...` 下的原文件）。

## 3. 运行时路径怎么解析

`obs.cjs` 里的 `presetAsset(name)` 按当前运行方式算真实文件路径，**不写死盘符**：

| 运行方式 | 返回路径 |
| --- | --- |
| 源码运行（`node app/main/core.cjs` / `npm run dev`） | `<仓库根>/assets/obs/<文件名>` |
| 打包后（`__dirname` 含 `app.asar`） | `process.resourcesPath/app.asar.unpacked/assets/obs/<文件名>` |
| 兜底 | 上面路径不存在、但旧的本机绝对路径（`D:\文档\...`）还在时，用旧的 |

- `electron-builder.yml` 的 `asarUnpack` 加了 `assets/obs/**`，打包后图片会解到 `resources\app.asar.unpacked\assets\obs\`。
- **为什么必须 unpack**：OBS 的 `image_source` 需要真实文件路径，而且 OBS 是外部进程，读不到 asar 内部路径。`public/` 那种由 Electron 自己 `fs` 读的资源留在 asar 里没问题，图片交给 OBS 的就必须 unpack。
- 代码里建源前还会 `fs.existsSync()` 检查，图片缺失时会记一条「跳过 背景板/文字叠加：图片不存在 <路径>」，其余源照常创建。

## 4. 想换图怎么换

1. 把新图命名为同名文件，覆盖 `assets/obs/bg3_星空鲸海.png` / `assets/obs/叠加层_竖屏_文字_v2.png`；
   或者改 `obs.cjs` 里 `presetSources()` 中两处 `presetAsset('...')` 的文件名，把新图一起放进 `assets/obs/`。
2. 源码运行：重启服务后再点一次「一键配置」即可，OBS 里已有的同名源会被 `SetInputSettings` 更新成新图。
3. 打包版：需要重新 `npm run build:exe`（图片是打进包里的）。
4. 建议保持 1080×1920 竖屏尺寸，和布局基准一致；换比例也不会报错，但摆位需要自己再调。
