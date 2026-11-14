# 星露谷 AI · 待办（明天接着改）

最后更新：2026-11-14 23:30

## P0 · 钓鱼收鱼结算（差最后一步）

**现象**：整条链路已通，唯独鱼不进背包。

```
栈桥固定点(25,36) → 原版抛竿 → 浮漂(25,39) → 咬钩(isNibbling) 
→ 接管 DoFunction 咬钩分支 → location.getFish() 抽鱼（Herring/Tuna/Sardine…）
→ 原版 BobberBar 弹出 → 小游戏自动完成 → fadeOut 收盘
→ ❌ pullFishFromWater 没触发
```

**下一步（先加诊断，不用猜）**：在 `FishingPatch.cs` 的 `BobberBarPatch.Postfix` 里打印：

```csharp
var rod = Game1.player.CurrentTool as FishingRod;
ModEntry.Trace?.Invoke(
  $"bar: progress={__instance.distanceFromCatching:F2} rod={(rod==null?"null":"ok")} " +
  $"isFishing={rod?.isFishing} fadeOut={GetFadeOut(__instance)} menu={Game1.activeClickableMenu?.GetType().Name}");
```

**两个怀疑点**：
1. `BobberBar` 收盘时 `Game1.player.CurrentTool` 不是鱼竿 → `fishingRod == null` → 走了 `doneFishing` 分支；
2. `distanceFromCatching` 是 NetFloat，直接赋值未生效 → 判断 `> 0.9` 时仍是旧值。

对应修法：
- 若是 1：`BobberBar` 创建后把 `Game1.player.CurrentTool = rod`，或在 Prefix 里保存 rod 引用并在收盘时直接调 `rod.pullFishFromWater(...)`；
- 若是 2：通过 NetFloat 的 `.Value` 赋值。

**参考源码**（已反编译）：
- `D:\live\FishingRod.cs`（2886 行）
- `D:\live\BobberBar.cs`（733 行，结算在 L355-362）

## P1 · 钓鱼小游戏观感

- 现在进度每帧钉 1.0，小游戏不到 1 秒就结束，观众看不清 → 可改成每帧 +0.02（约 50 帧）并保留 `fadeOut` 触发。

## P2 · 其他待验证

- [ ] 矿洞：进洞后下梯子、捡矿石、打怪（目前只敲表面石头）
- [ ] 熔炉：2 个重型熔炉未实测冶炼
- [ ] 饰品槽：魔法箭袋数据已装备但 UI 未解锁（1.6 需正规解锁）
- [ ] 商店往返：一次 60~90 秒，考虑做成"节目效果"
- [ ] 长跑管家：`agent/stardew_supervisor.py --minutes 90` 再跑一轮出日志

## 启动/部署速查

```powershell
# 启动游戏
Start-Process "D:\steam\steamapps\common\Stardew Valley\StardewModdingAPI.exe"

# 编译 mod
cd D:\live\stardew-mod\WhalePlayer
dotnet build -c Release "-p:GamePath=D:\steam\steamapps\common\Stardew Valley"
# 部署
Copy-Item bin\Release\WhalePlayer.dll "D:\steam\steamapps\common\Stardew Valley\Mods\WhalePlayer\" -Force

# Agent（游戏已启动）
python D:\live\deepseek-live\stardew-mod\agent\stardew_agent.py --once
```

## 仓库

https://github.com/a106832411/deepseek-live → `stardew-mod/`（mods + agent + README）
