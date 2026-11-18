# 星露谷 AI · 当前状态与待办

最后更新：2026-11-18 凌晨（第 11 天收割 542 株实测完成；旧版 P0「鱼不进背包」已作废，见文末）

---

## 0. 一句话现状

存档 `MoonFarm`（秋 · 第 4 年 · **第 11 天 18:20**）：**542 株蔓越莓已全部收割完毕**（344 + 198 两轮），
背包里 422 颗、其余已归箱；钱没动（58,918g，没卖）。钓鱼靠 **CJB 秒钓**在工作。

| 项 | 值 |
|---|---|
| 游戏 | 星露谷 1.6.15 + SMAPI 4.5.2，`D:\steam\steamapps\common\Stardew Valley` |
| 模块 | `D:\live\deepseek-live\stardew-mod\`（`agent/` Python + `mods/` C#） |
| 当前存档 | `%APPDATA%\StardewValley\Saves\MoonFarm_453567724`（第 11 天 18:20 已存档） |
| 农田 | 固定区 `45,14 → 75,36`，中心 `60,27`（`D:\live\logs\.plot_rect` / `.plot_center`） |
| 钱 | 58,918g（蔓越莓还没卖，出货箱空着） |
| 本地模型 | Ollama `D:\live\.tools\ollama\ollama.exe`，模型 `qwen25-7b-local`，`OLLAMA_MODELS=D:\live\models\ollama`，100% GPU |

---

## 1. 已验证（2026-11-17 真机实测）

| 项目 | 结果 | 证据 |
|---|---|---|
| 自动读档 | SMAPI 启动后 **约 40 秒**自动进存档，无需点「继续」 | `player_state.json` 落盘 + SMAPI 日志 `已进入存档` |
| 农田状态读取 | 542 格全部 `crop=蔓越莓 / waterState=已浇` | `tiles` 542 条 |
| 传送到海滩 | `warp_to Beach`（落点 38,1） | SMAPI 日志 `goto: FarmHouse → Beach` |
| 走到栈桥钓点 | `go_fish` 后 **约 8 秒**到 (25,36) | 轮询玩家坐标 |
| 钓鱼（CJB 秒钓） | **90 秒 7 次**：Sardine×3、Anchovy、Sea Jelly×2、Albacore、Sea Cucumber×7、Gold Ore×5、Bait×10、Iron Ore×6、Wood×4… | 背包差分 + SMAPI 日志 |
| 步行 Farm→Beach | 约 **3.5 分钟**（Farm→Backwoods→Mountain→Town→Beach，每段 40~60s） | 日志时间戳 |
| 本地 LLM | `/v1/chat/completions` 正常，5.1s / 20 tokens，100% GPU | 直接调接口验证 |
| **连睡 6 天** | 第 5 天 → 第 11 天，6 次 `go_home_sleep` 全成功，每次约 16 秒 | `stardew_harvest_verify.py --wait 6` 日志 |
| **542 株收割** | **542/542 全收**：第 1 轮 344 株、第 2 轮 198 株，收完可收数归零 | 铱镰刀生效 104 次；背包 422 颗蔓越莓 |
| **3x3 蛇形收割（新）** | 第 23 天 541 株：**81 刀、221 秒**，收 525 株，剩 16 株在边缘（**按用户要求不再补漏**） | `D:\live\logs\harvest_sweep_test.log` |
| 自动浇水 | 连睡期间 **CJB AutoWater** 一直保持「已浇」，不手动浇也能长 | 每轮 `waterState=已浇` |
| 收完自动归箱 | 背包 31 格时自动 `stash_all`，第 2 轮开局背包 5 格 | 日志 `背包 31 格，归箱` |
| **跳天 + 收成闭环** | 秋 1 种下 132 株南瓜 → `skip_until_ready` 跳 11 天 → 第 12 天全熟 → 收割 **18 刀收 132 株、0 残留**（主扫 16 刀 + 补漏 1 轮 6 株）→ 已存档 | `D:\live\logs\grow_harvest.log` |

> 农活闭环 clear→hoe→plant→water→harvest 现在 **收割环节也实测过了**。

### 收割算法（2026-11-18 改，`harvest_with_scythe`）

铱镰刀一刀是 **3x3**，所以不再「一株一株走过去挥刀」，改成按 3 行一条带扫：

```
带1：x 左 → 右
        ↓ 到右侧尽头，不回左侧
带2：x 右 → 左
        ↓ 到左侧尽头
带3：x 左 → 右   …… 整体 S 形
```

- 带内每 3 列一站，朝前进方向最远的那一格挥刀，一刀清一块 3x3；
- **主扫一遍 + 补漏**：主扫走完 S 形后，再读一次剩余成熟格，**只对剩下那几株**补刀
  （最多 3 轮，不整片重扫）；
- 规划带用的是固定农田区 `53,21→74,26`，不是当次剩余作物的 bbox，避免越扫越乱；
- 日志按带打印「累计 N 刀」，最后报「共挥 N 刀、收 M 株、剩 K 株」。

实测（132 格杨桃地）：121 株 → 16 刀 / 33 秒收 117 株；补漏后剩余 4 株在扫描之外。

---

## 2. 已知问题

### ✅ 已修 · 四个可复现问题（2026-11-18 夜，全部实测通过）

| # | 问题 | 修法 | 验证 |
|---|---|---|---|
| 1 | 取种把整箱搬空（9999 个种子一次进背包） | `Withdraw()` 按需拆堆：`want = min(stack, limit - moved)`，搬完减堆、空了清格 | 向种子箱取 20 个草莓种子 → 背包正好 +20（原来 +9999） |
| 2 | 清障 120 刀上限清一半就开种 | 上限提到 400 刀 + 600 秒预算，清到没有障碍为止 | 36 处障碍 → **36/36 全清**，没有截断 |
| 3 | 季末不抢收，换季作物全烂 | 新增 `season_end_harvest()`：本季剩 ≤2 天自动抢收；**会先判断人在不在农场，不在就先走回去** | 第 27 天从 FarmHouse 调用 → 自动回农场 → 收 117 株杨桃（16 刀 / 33 秒） |
| 4 | 清障太慢（2 秒一刀、来回横穿） | 每刀挑**离角色最近**的障碍（原来按行排序）、挥刀等待 0.9→0.55 秒、走位参数收紧 | 36 处障碍 **19 刀 / 26 秒**（1.4 秒/刀，36 处全清） |

### ✅ 已加 · 左上角状态面板（HUD）→ 已改为 OBS 叠加层

游戏内 HUD 已按用户要求关掉（`ModEntry` 里那行订阅被注释，想开回来去掉注释即可）。
状态改由 **OBS 浏览器源**显示：`http://127.0.0.1:8788/ov_stardew`

链路：

```
stardew_agent.send_order()/set_activity() → D:\live\logs\ai_status.json
        ↓
extras/python/status_server.py (/api/stardew 合并 activity / lastOrder)
        ↓
public/ov_stardew.html 新增一行「最近指令 = 高层动作 · 具体订单」
        ↓
OBS 浏览器源（http://127.0.0.1:8788/ov_stardew）
```

效果（headless 截图核对过）：

```
第 12 天 | 在农场干活      💰 58,918  体力 100%  生命 90%
最近指令 收割南瓜 · 用工具 scythe
```

> OBS 那边要**手动刷新一次浏览器源**（右键 → 刷新）才能加载新版页面。

### ✅ 已修 · 举着工具「蓄力」卡死（2026-11-17 夜）

- **现象**：角色在田里举着工具保持蓄力姿势不动，之后所有动作失效。
- **原因**：`UseTool()` 先调 `player.BeginUsingTool()`，约 0.33 秒后才在 `ApplyPendingTool()`
  里真正生效；而「目标格有摆放物（宝箱/熔炉/稻草人）」的保护分支直接 `return`，
  **没有收掉挥动状态** → 永远卡在蓄力。
- **修法**：保护分支退出前补 `EndUsingTool()` + `UsingTool=false`；并在 `OnUpdateTicked`
  加看门狗：`UsingTool && !canReleaseTool` 持续超过 60 帧就强制复位。
- **验证**：修复后整轮 542 株收割跑完，104 次挥动零卡死（看门狗 0 次触发、保护 0 次拦截）。

### P0 · `daysLeft` 导出是垃圾值（100005）

- **现象**：`player_state.json` 里蔓越莓的 `daysLeft` 全是 `100005`（第 5 天、第 11 天都一样）。
- **原因**：可重复收获作物的 `Crop.phaseDays` 是 `[1,1,1,1,2,99999]`，末尾 `99999` 是
  「成熟后再生」哨兵值；`ModEntry.cs` 直接 `foreach (var pd in phaseDays) total += pd;`
  把哨兵也加了 → 真实 6 天 + 99999 = **100005**，数字完全对得上。
- **修法**（约 3 行）：求和/累计时跳过 `pd <= 0 || pd >= 1000`，`fullyGrown` 时置 0。
- **状态**：11-17 曾改好并实测输出 `6`（与存档离线解析一致），但按「回到今晚原样」已回滚，
  **当前仓库是未修状态**。要修的话单独提一次。

### P0 · 钓鱼实现错位：真正干活的是 CJB，不是我们自己的补丁

- `ModEntry.AssistFishing` **从未被置为 true**，`FishingPatch.cs` 里
  `FishingRodPatch / FishingRodResetPatch / DoFunctionTracePatch / NibbleBranchPatch`
  全是死代码（`if (!ModEntry.AssistFishing) return;` 直接返回）。
- 实际链路：`ModEntry` 钓鱼泵抛竿 → 引擎咬钩 → CJB `InstantBite/InstantCatch`
  把鱼塞进背包 → 泵检测 `fishCaught` 后调 `doneHoldingFish` 收尾。
- **结论**：旧 TODO 的「P0 鱼不进背包」不是被代码修好的，是被 CJB 绕过去的。
  目前用户明确要 CJB 秒钓，**不要再动钓鱼实现**（11-17 试过改回原版小游戏，结果 2 分钟 0 收获）。
- 相关配置：`Mods\CJBCheatsMenu\config.json` 的 `InstantBite / InstantCatch` 必须都是 `true`。

### P1 · 背包里出现两根 Fishing Rod

`ensure_tools` 会再给一根鱼竿（不判断是否已有）。功能上无碍，但要清掉。

### P1 · `player_state.json` 偶发写不进去

SMAPI 日志出现过 `状态写入失败：Access to the path is denied.`，被别的进程短暂占用
（本地脚本读状态文件时）。可加重试或改成原子替换。

### P2 · 步行赶路太慢，直播效果差

Farm→Beach 3.5 分钟纯走路。建议「分段 `warp_to` + 最后一段走路」，或只在镜头内保留走路。

---

## 3. 待验证

| 项 | 为什么没验 | 怎么验 |
|---|---|---|
| 矿洞：下梯子、捡矿、打怪 | 只敲过表面石头 | `stardew_mine2.py --rounds 12 --levels 45,50,55` |
| 熔炉冶炼 | 2 个重型熔炉没试过 | `use_tool` 对熔炉 + 等产物 |
| 饰品槽解锁 | 数据已装备但 UI 未解锁（1.6 要走正规解锁） | 事件解锁后再验 |
| 90 分钟长跑 | 还没跑第二轮 | `python agent\stardew_supervisor.py --minutes 90 --pattern "farm,farm,fish"` |
| 换季播种 | 还没跨季（当前秋 11 天，秋 28 天换冬） | 睡过第 28 天，看 `plant_timing` 选种 |
| 卖货 | 422 颗蔓越莓还在背包/箱子里，钱没变 | `ship` 或 `sell`（只卖作物） |

---

## 4. 部署 / 排障速查

```powershell
# 编译（源码在 deepseek-live 下，不是老的 D:\live\stardew-mod）
$env:PATH = "D:\live\.tools\dotnet;" + $env:PATH
cd D:\live\deepseek-live\stardew-mod\mods\WhalePlayer
dotnet build -c Release "-p:GamePath=D:\steam\steamapps\common\Stardew Valley"

# 部署（游戏必须已关闭，否则 DLL 被占用）
Copy-Item bin\Release\WhalePlayer.dll "D:\steam\steamapps\common\Stardew Valley\Mods\WhalePlayer\" -Force

# 启动游戏（要抓日志就加重定向，SMAPI 输出是 UTF-16）
Start-Process "D:\steam\steamapps\common\Stardew Valley\StardewModdingAPI.exe" `
  -WorkingDirectory "D:\steam\steamapps\common\Stardew Valley"

# 看 SMAPI 日志
%APPDATA%\StardewValley\ErrorLogs\SMAPI-latest.txt

# 状态 / 订单
Mods\WhalePlayer\player_state.json
Mods\WhalePlayer\orders\*.json

# 本地模型
$env:OLLAMA_MODELS="D:\live\models\ollama"
D:\live\.tools\ollama\ollama.exe serve
D:\live\.tools\ollama\ollama.exe ps
```

Agent 入口：

```powershell
python D:\live\deepseek-live\stardew-mod\agent\stardew_agent.py --once
python D:\live\deepseek-live\stardew-mod\agent\stardew_fishsession.py --minutes 20
python D:\live\deepseek-live\stardew-mod\agent\stardew_dayloop.py --days 3
python D:\live\deepseek-live\stardew-mod\agent\stardew_harvest_verify.py --check    # 只看熟没熟
python D:\live\deepseek-live\stardew-mod\agent\stardew_harvest_verify.py --wait 6   # 连睡 N 天
```

---

## 5. 历史（已作废，留个索引）

- 旧版「P0 钓鱼收鱼结算（差最后一步）」**已作废**：那条讲的是 `BobberBar.fadeOut` 时
  `Game1.player.CurrentTool` 不是鱼竿导致走 `doneFishing` 分支；后来整条路径改成
  「咬钩直接 `pullFishFromWater`」，最终实际由 CJB 秒钓承担，自研分支未启用。
- 旧版「P1 钓鱼小游戏观感」一并作废（现在没小游戏，秒钓）。
- 11-16 一整天的 23 个提交（农田扩到 30×20、镰刀清障、批量种植、箱子分类、
  00:00 回家、晕倒保险等）细节看 `git log --oneline`。

---

## 6. 四季播种测试（2026-11-18 夜，进行中）

自然过季（睡觉推进），每个季节各跑一次 `plant_best_crops(20)`，日志 `D:\live\logs\season_run.log`。

| 季节 | 播种前作物 | 清障 | 选中 | 种下 | 备注 |
|---|---|---|---|---|---|
| 冬（第 4 年末） | 541 → 换季全死 | — | — | — | 脚本睡过头没测到，改在下一个冬天补 |
| 春 第 2 天 | 0（冬天冻死） | 325 处 → 120 刀清 253，剩 72 | 草莓 | 20 株 / 30 秒 | 取种把整箱 9999 个种子搬进背包 |
| 夏 第 1 天 | 0（换季清空） | 298 处 | 待记录 | 待记录 | |
| 秋 第 1 天 | 131 株枯死杨桃 | **131/131 全清**（57 刀 / 54 秒） | 南瓜 | **132 株 / 3.5 分钟** | 换季后先清枯死作物再种，流程跑通 |
| 冬 第 1 天 | 待记录 | | | | 预期「本季不再种」 |

**已确认的问题**

1. ~~取种会把整箱种子全搬走~~ → 已修并验证（见 §2）。
2. ~~清障 120 刀上限清不干净~~ → 已修并验证（400 刀 + 预算，36/36 全清）。
3. ~~换季作物全死，但没有季末抢收~~ → 已加 `season_end_harvest`，第 27 天实测收 117 株。
4. ~~清障太慢~~ → 已优化到 1.4 秒/刀（19 刀清 36 处）。
5. **农田区域太大** → 已按用户给的新区域收窄到 `53,21 → 74,26`（22×6 = 132 格）。
6. **新发现：没有稻草人，乌鸦会吃作物**。杨桃从 132 株掉到 121 株（睡 25 天丢了 11 株），
   这片地一个稻草人也没有。要在区域里补稻草人（`place_item` 已支持）。

**已改好、待部署（游戏正跑着，DLL 被占用）**

- ✅ `Withdraw()` 改成按需拆分搬种子（`want = min(stack, limit - moved)`），不再一次搬 9999 个。
  已编译通过、产物在 `bin\Release\WhalePlayer.dll`，**下次重启游戏时随部署生效**。

**用法（改区域只动一个文件）**

```powershell
python D:\live\deepseek-live\stardew-mod\agent\stardew_plot_rect.py            # 看当前
python D:\live\deepseek-live\stardew-mod\agent\stardew_plot_rect.py --set 50,20,62,32
```

播种 / 清障 / 收割三处读的都是 `D:\live\logs\.plot_rect`，改一处即全部生效。
