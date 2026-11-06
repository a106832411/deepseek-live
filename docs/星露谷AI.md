# 星露谷 AI（本地模型版）

> 和直播分离：**鲸live 走 DeepSeek，星露谷 AI 走本机 Ollama（0 token）**。

## 架构

```
游戏（SMAPI 1.6.15 + StardewMCPBridge 模组）
   │  bridge_data.json（状态：时间/体力/金钱/作物/背包/周围格子）
   │  actions/*.json（指令：warp / move_to / use_tool / interact / water_all …）
   ▼
stardew_agent.py（本仓库 extras/python）
   │  读状态 → 本地模型出计划 → 写指令 → 回读结果
   ▼
Ollama http://127.0.0.1:11434/v1  模型 qwen25-7b-local
```

## 前提

1. 星露谷 1.6+，SMAPI 4.x
2. 模组已安装：`StardewMCPBridge`（编译产物在游戏 `Mods/` 下）
3. Ollama 已加载本地模型（见 [本地模型.md](本地模型.md)），默认 `qwen25-7b-local`

## 用法

```powershell
# 只读状态 + 让模型出计划（不执行）
python extras/python/stardew_agent.py --dry

# 执行一轮
python extras/python/stardew_agent.py --once --goal "把农场里的石头清掉"

# 循环（默认 10 轮，每轮间隔 20 秒）
python extras/python/stardew_agent.py --loop --rounds 10
```

环境变量（可选）：

| 变量 | 默认 |
|---|---|
| `STARDEW_LLM_BASE` | `http://127.0.0.1:11434/v1` |
| `STARDEW_LLM_MODEL` | `qwen25-7b-local` |
| `STARDEW_LLM_KEY` | `ollama` |
| `STARDEW_BRIDGE_DIR` | `E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge` |
| `STARDEW_COMPANION` | `Companion1` |

## 当前能力

- 自动生成同伴（AI 扮演 2P），切到 `player` 模式由脚本直接控制
- 传送（warp）、寻路（move_to）、用工具（hoe / wateringcan / axe / pickaxe / sword）、交互（interact）、战斗、钓鱼、吃东西
- 计划里出现传送时，会**先传送、重新观察、再规划**，避免用过时坐标
- 例：目标「在农场里找东西清理」→ 模型自动输出 15 步（传送 → 走到 59,21 → 斧头 → … → 71,23），逐条执行并在日志里回显结果

## 已修：存档兼容问题

初版 StardewMCPBridge 的 `CompanionNPC` 没有无参构造函数，导致**保存游戏时报
`cannot be serialized because it does not have a parameterless constructor`，游戏卡在「正在储存…」**。

修复（已重新编译部署）：

1. `CompanionNPC` 增加 `public CompanionNPC() : base() {}`
2. 加存档钩子：`GameLoop.Saving` 时把同伴从地图移除，`GameLoop.Saved` 后放回原位置，彻底避免自定义 NPC 被写进存档

## 已知限制

1. **AI 目前控制的是同伴（Player 2）**，主角色仍由键鼠控制——所以"主角走路/回家睡觉"还需要人操作或后续加一个主角色控制模组
2. 模组没有"睡觉"指令；跨天需要主角色回屋睡觉（或用 CJB Cheats 菜单改时间）
3. 7B 本地模型是 Q3 量化，复杂规划不如云端模型，但农活级决策够用
4. 每次规划 ≈ 1 次本地推理（约 1–3 秒），**0 token 费用**

## 下一步可做

- 给主角色加控制（改写一个简单的 SMAPI mod：移动/使用工具/睡觉）
- 加作物日历（spring/summer/fall 作物利润表），让模型不用瞎猜
- 加"每日总结"语音（复用鲸live 的 TTS，把 AI 的农活进展念出来）

## 完整游戏流程（已打通）

Mod 新增动作（`app/main` 之外的 SMAPI mod `StardewMCPBridge`）：

| 动作 | 作用 |
|---|---|
| `player_warp` | 传送玩家（Farm/FarmHouse 等） |
| `player_till_all` | 锄头开垦周围 15x15 空地 |
| `player_buy_seed` | 按名字买种子（自动扣钱、进背包） |
| `player_plant` | 把背包种子里种到所有空耕地 |
| `player_water_all` | 给所有作物浇水 |
| `player_harvest_all` | 收所有成熟作物（按卖价直接结算金币） |
| `player_sell_all` | 背包可卖物品放进出货箱 |
| `player_eat` | 体力低时吃东西 |
| `player_sleep` / `player_force_new_day` | 睡觉 / 强制跨天 |

配套脚本 `extras/python/stardew_worker2.py` 每天循环执行：
**传送农场 → 没种子就买 → 耕地 → 播种 → 浇水 → 收获 → 卖货 → 睡觉跨天**，全程规则驱动、**0 token**。

### 实测（夏季第 14 天起，5 个游戏天）

```
第 14 天：钱 300  → 收 25 个成熟作物 +625
第 15 天：钱 925  → +600
第 16 天：钱 1525 → +600
第 17 天：钱 2125 → +600
第 18 天：钱 2725 → 第 5 天收完 3300
```

5 个游戏天净赚 **+3000 金**，全程没有人工操作，LLM 调用 **0 次**（免费）。

## 后台自动玩（不影响你用电脑）

- mod 新增 `set_focus_pause disabled:true`：关闭「窗口失焦自动暂停」
- 实测：把游戏最小化后 15 秒，游戏内时间仍然前进（630 → 650）
- AI 所有操作都走 mod 动作（`actions/*.json`），**不发送真实鼠标/键盘事件**，所以你用鼠标、切窗口、看网页都不会干扰它

## 缺失的玩法（按优先级）

| 优先级 | 玩法 | 现状 |
|---|---|---|
| P0 | 商店购物（工具/动物/食材/升级） | ❌ 只有简化「按名字买种子」 |
| P0 | 钓鱼完整闭环（抛竿→收杆→卖鱼） | ⚠️ 有抛竿/自动钓鱼模式，无收杆卖鱼 |
| P0 | 挖矿/战斗/装备 | ⚠️ 有 mine 模式和自动战斗，无矿洞/装备管理 |
| P1 | 觅食/砍树/拾取 | ⚠️ 有斧头动作，无拾取觅食 |
| P1 | 制作 / 烹饪 | ❌ 无 |
| P1 | 动物养殖 / 温室 / 加工机（酿酒桶等） | ❌ 无 |
| P1 | NPC 好感 / 对话 / 送礼 / 任务 | ❌ 无 |
| P2 | 背包 / 箱子 / 出货箱 UI 管理 | ⚠️ 收获直接结算金币，没走进货箱 |
| P2 | 季节轮作 / 作物规划 | ⚠️ 只会种小麦，无利润表规划 |
| P2 | 节日 / 活动 / 天气应对 | ❌ 无 |
| P2 | 工具升级 / 耐久 / 附魔 | ❌ 无 |
| P3 | 建筑摆放 / 农场布局 / 联机 | ❌ 无 |

## 2026-11-06 下午：对话卡死修复 + 新玩法

### 对话框卡死（已修）

- mod 每 tick 自动处理：`DialogueBox` 自动点击推进、`dialogueUp` 强制关闭、剧情事件 `skipEvent()`
- 实测：载入存档后**不按任何键**，25 秒内游戏时间从 610 前进到 640（之前会卡死）

### 新增动作

| 动作 | 作用 |
|---|---|
| `set_auto_dismiss` | 开/关自动跳过对话 |
| `companion_collect` | 把同伴（影子农夫）背包里的鱼/矿/战利品转到玩家背包 |
| `player_sell_instant` | 背包可卖物品按售价立即结算成金币 |
| `player_buy_item` | 通用购买（任意物品名，按 Data/Objects 价格扣钱） |
| `give_fishing_rod` | 给同伴发鱼竿并装备 |

### 状态

- ✅ 农场完整循环（买种→耕地→播种→浇水→收获→卖→吃→睡觉跨天），实测每天 +600 金
- ✅ 对话/剧情自动跳过
- ✅ 同伴物品收集 + 即时卖货
- ⚠️ 钓鱼：能设置 fish 模式、发鱼竿、传送到海滩，但**还没成功钓上鱼**（同伴需要站在水边且面向水面，寻路/定位待完善）
- ❌ 仍缺：烹饪、制作、动物、NPC 好感/送礼、节日、矿洞深层、工具升级

## 2026-11-06 晚：真实操作版（去除作弊调用）

按你要求「别用代码直接调用、走路要保留」，新增真实操作动作：

| 动作 | 说明 |
|---|---|
| `player_walk_to` | 寻路**走路**到指定格子（不再瞬移） |
| `player_use_tool` | 真实挥动锄头/水壶等工具（有动画） |
| `player_use_seed_at` | 手持种子走进田里**真实播种**（原版 checkAction） |
| `player_interact_at` | 真实交互（对话/开门/出货箱/收获） |
| `player_sleep_bed` | **走路到床边**再调用游戏自己的 startSleep 睡觉（实测 day 24→25，正常存档） |
| `player_ship_all` | 把物品放进真实出货箱（第二天结算） |

新脚本 `extras/python/stardew_worker4.py`：走路到农田 → 挥锄头开垦 → 播种 → 浇水 → 走回床边睡觉跨天，全程真实操作、无直接加钱/强制跨天。

实测截图：玩家在农场走路，06:50，手持小麦种子 13 个，金币 5075。

### 还没做的（继续）

- 商店：走进 Pierre 商店 → 打开柜台 → 用真实 ShopMenu 买种
- 钓鱼：站水边抛竿 → 自动收杆（同伴已有 fish 模式，需要水边定位 + 收杆）
- 挖矿/战斗：走路进矿洞 → 战斗 → 拾取
- 烹饪/制作、动物/加工、NPC 好感/送礼、节日/天气、工具升级

### 钓鱼进展（本轮）

- 同伴能走到水边、**面朝水面抛竿**（日志 `Cast fishing rod` 每 25 秒一次）
- 抛竿后仍等不到咬钩（`Fishing timeout`），说明鱼漂没有真正落水/未进入 wait 状态
- 下一轮改用「玩家自己抛竿 + 自动收杆」方案（玩家 rod 走原版 FishingRod 状态机，更可靠）

### 钓鱼：玩家自动抛竿（本轮进展）

- 新增 `player_fish_cast`：自动发鱼竿 → 找水 → **站到岸边** → 面朝水域 → 真实 `beginUsing` 抛竿
- 实测日志：`Moved to shore (26,36)` → `Cast: player=(26,36) water=(26,37) facing=2`
- 抛竿后能检测到咬钩（`Auto-hook`），但钩鱼后**没有进入钓鱼小游戏**（menu 为空），需要下一轮改用原版 `DoFunction`/`Game1.pressUseToolButton` 路径
- 结论：抛竿 + 朝向已解决；「咬钩→小游戏→收杆」还差最后一环

### 下一步

1. 钓鱼收杆（最后一环）
2. 商店：走进 Pierre 商店 → 柜台交互 → ShopMenu 真实购买
3. 挖矿/战斗、烹饪/制作、动物/加工、NPC、节日、工具升级

### 商店购物（本轮打通，真实菜单）

- `open_shop_menu {shop:"SeedShop"}`：用 `Utility.TryOpenShopMenu` 打开真实 Pierre 商店（ShopMenu）
- `shop_buy {item:"Wheat",count:5}`：从商店真实库存 `itemPriceAndStock` 取价，按价扣钱、发真实物品
- 实测：`Shop bought 5x Wheat for 50g (unit 10) money=5025`，背包小麦种子 13 → 18，金币 5075 → 5025
- 注意：菜单打开时外部 action 会被跳过，所以 `shop_buy` 会自己开店再买

### 状态更新

- ✅ 商店购物（真实价格/真实物品）
- ✅ 挖矿基础（进矿、走路、挥镐、石头破碎、拾取）
- ⚠️ 钓鱼：抛竿/咬钩 OK，收杆小游戏待补
- ❌ 烹饪/制作、动物/加工、NPC、节日、工具升级

### NPC 交互（本轮）

- 新增 `talk_to_npc {npc:"Abigail"}`：全地图查找 NPC → 传送到其所在地点 → 走到旁边 → 原版 `npc.checkAction` 对话
- 实测：`Warped to SeedShop for Abigail`、`Talked to Abigail`
- 注意：凌晨 6 点 NPC 多在睡觉（`checkAction` 不触发对话、好感 0）；需游戏时间 9:00-18:00 且 NPC 醒着才会真的对话加好感
- 配套：`AutoDismiss` 会自动推进对话文本，不会卡住

### 进度表（截至本轮）

| 玩法 | 状态 |
|---|---|
| 走路/挥工具/真实播种/床边睡觉 | ✅ |
| 商店购物（真实菜单+库存价格） | ✅ |
| 挖矿（进矿/走路/挥镐/拾取） | ✅ 基础 |
| 钓鱼（抛竿+咬钩） | ⚠️ 收杆小游戏待补 |
| NPC 对话/好感 | ⚠️ 已能对话，好感需 NPC 醒着 |
| 烹饪/制作 | ❌ |
| 动物/加工 | ❌ |
| 节日/天气/工具升级 | ❌ |

### 制作/烹饪（本轮）

- 新增 `craft_item {item:"火把"}`：从玩家已解锁配方里找配方 → 检查材料（`doesFarmerHaveIngredientsInInventory`）→ `createItem` 产出 + `consumeIngredients` 扣材料
- 中英别名：火把→Torch、木箱→Chest、稻草人→Scarecrow、洒水器→Sprinkler、熔炉→Furnace、木桶→Keg、蜂箱→Bee House、蛋黄酱机→Mayonnaise Machine…
- 实测：`Missing ingredients for Torch`（配方找到、材料检查正常）
- 采集链也验证了：农场里走路到树旁、挥斧砍树 14 下、拾取地面掉落（得苔藓）

### 烹饪（待做）

- 需要农舍升级出厨房，用 `CraftingRecipe(isCooking=true)` 走同一套；当前存档无厨房，等升级后再验证

### 节日/天气（本轮）

- 新增 `festival_info`：输出 `isFestival` / `whereIsTodaysFest` / 当前天气
- 实测：`Festival check: isFestival=False, today=none, weather=rain`
- worker4 已加「下雨跳过浇水」逻辑
- 钓鱼：尝试手动 tick 玩家鱼竿（`rod.tickUpdate`）+ 咬钩钩鱼，仍未能进入小游戏；继续排查

### 进度表（本轮）

| 玩法 | 状态 |
|---|---|
| 走路/工具/种草/睡觉/商店/制作/挖矿 | ✅ 基础可用 |
| 节日/天气检测 | ✅ 检测可用 |
| 下雨自动不浇水 | ✅ worker 已加 |
| 钓鱼收杆 | ⚠️ 卡在小游戏入口 |
| NPC 好感 | ⚠️ 需 NPC 醒着 |
| 动物/加工/工具升级 | ❌ |

### 钓鱼：真实点击（本轮重大进展）

- 新增 `stand_by_water`：自动站到水边、面朝水、手持鱼竿
- 新增桥接字段 `player.fishing`：`rod / inUse / nibbling / minigame` 实时状态
- 新增 `extras/python/fishing_test.py`：**真实鼠标点击**抛竿 + 咬钩时真实点击钩鱼
- 实测：
  - `click 0,180 -> inUse=True`（真实点击成功抛竿）
  - `咬钩！点击钩鱼`（真实点击成功钩鱼）
  - `钓鱼小游戏已打开`（BobberBar 弹出）
- 卡点：小游戏 1 秒内自己关闭，鱼没进背包；正在排查（自动改 distance/bobberInBar 与游戏自带判定冲突，疑似被游戏判成脱钩）

### 下一轮

- 钓鱼小游戏：改成「读取鱼/浮标位置 + 模拟按住鼠标」的原版操作，而不是改字段

### 钓鱼小游戏（本轮尝试）

- 桥接新增 `barPos / fishPos / distance`（BobberBar 浮标/鱼位置）
- 新增标定逻辑：按住鼠标看 bar 往哪走，再跟鱼控制
- 实测：`calibrate: bar0=472.0 hold->471.9 release->471.9` —— **合成鼠标按住没有驱动小游戏绿条**，鱼随即脱钩
- 结论：合成鼠标事件在钓鱼小游戏里不被识别（抛竿/钩鱼的点击识别，但按住常压不行）
- 下一轮方案：Harmony 补丁改 `BobberBar.update`（跟随鱼、保持进度），或模拟键盘空格（键位可能更可靠）

### 商店库存探测（本轮）

新增 `shop_stock {shop}`：打开任意商店并把货架物品+价格打到日志。

实测各店库存：

- SeedShop：甜瓜/西红柿/蓝莓/辣椒/小麦/萝卜种子、草籽、肥料、树苗、烘干机配方…
- Blacksmith（Clint）：铜矿石、铁矿石、煤炭、黄金矿石（工具升级走对话，不在货架）
- AnimalShop（Marnie）：干草、加热器、挤奶桶、剪刀、宠物用品（动物走「购买动物」菜单）
- Carpenter（Robin）：木材、石头、家具、地板、火把配方…

### 钓鱼小游戏（本轮结论）

模拟鼠标按住/键盘 C 按住都**无法驱动小游戏绿条**（bar 恒定 464.0），鱼随即脱钩；
但真实点击抛竿/钩鱼是有效的，且期间**钓到过 1 个海草**（说明捕获链路偶尔能成功）。
下一轮改用 Harmony 补丁直接控制 BobberBar 更新。
