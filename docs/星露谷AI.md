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
