using System;
using System.Collections.Generic;
using HarmonyLib;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Menus;
using StardewValley.Tools;

namespace WhalePlayer
{
    /// <summary>钓鱼补丁：让脚本抛竿真正进入钓鱼状态，并让小游戏自动完成。</summary>
    [HarmonyPatch(typeof(FishingRod), nameof(FishingRod.DoFunction))]
    internal static class FishingRodPatch
    {
        private static void Postfix(FishingRod __instance, GameLocation location, int x, int y, int power, Farmer who)
        {
            if (!WhalePlayer.ModEntry.AssistFishing) return;
            if (who == null || !who.IsLocalPlayer) return;
            if (__instance.isFishing) return;
            try
            {
                int dir = who.FacingDirection;
                int bx = (int)who.Tile.X, by = (int)who.Tile.Y;
                switch (dir)
                {
                    case 0: by -= 2; break;
                    case 1: bx += 2; break;
                    case 2: by += 2; break;
                    case 3: bx -= 2; break;
                }
                __instance.isFishing = true;
                __instance.castedButBobberStillInAir = false;
                __instance.bobber.Value = new Microsoft.Xna.Framework.Vector2(bx, by);
                __instance.timeUntilFishingBite = 2.0f;
                __instance.fishingBiteAccumulator = 0f;
                who.UsingTool = false;
            }
            catch (Exception) { }
        }
    }


    /// <summary>脚本钓鱼期间禁止引擎重置钓鱼状态。</summary>
    [HarmonyPatch(typeof(FishingRod), nameof(FishingRod.resetState))]
    internal static class FishingRodResetPatch
    {
        private static bool Prefix() => !(WhalePlayer.ModEntry.ScriptedFishing && WhalePlayer.ModEntry.AssistFishing);
    }


    /// <summary>调完 DoFunction 后打印状态，定位是哪条 return 走的。</summary>
    [HarmonyPatch(typeof(FishingRod), nameof(FishingRod.DoFunction))]
    internal static class DoFunctionTracePatch
    {
        private static void Postfix(FishingRod __instance)
        {
            try
            {
                WhalePlayer.ModEntry.Trace?.Invoke(
                    "DoFunction 返回: nibbling=" + __instance.isNibbling + " hit=" + __instance.hit
                    + " casting=" + __instance.isCasting + " pulling=" + __instance.pullingOutOfWater
                    + " fishCaught=" + __instance.fishCaught
                    + " menu=" + (Game1.activeClickableMenu == null ? "null" : Game1.activeClickableMenu.GetType().Name));
            }
            catch (Exception) { }
        }
    }


    /// <summary>脚本钓鱼：咬钩时直接走原版 pullFishFromWater 结算。</summary>
    [HarmonyPatch(typeof(FishingRod), nameof(FishingRod.DoFunction))]
    internal static class NibbleBranchPatch
    {
        private static bool Prefix(FishingRod __instance, GameLocation location, int x, int y, int power, Farmer who)
        {
            try
            {
                if (!WhalePlayer.ModEntry.AssistFishing) return true;
                if (!__instance.isNibbling) return true;
                if (Game1.activeClickableMenu is BobberBar) return false;

                var rod = __instance;
                who = who ?? rod.lastUser ?? Game1.player;
                rod.lastUser = who;

                string baitId = null;
                try { baitId = rod.GetBait()?.QualifiedItemId; } catch { }
                var bobberTile = new Microsoft.Xna.Framework.Vector2(rod.bobber.X / 64f, rod.bobber.Y / 64f);
                Item fish = null;
                // 水深 5 + 玩家当日运气，垃圾（海草/垃圾/浮木…）就重摇，最多 3 次；
                // 仍然只从游戏自己的鱼表里抽，不生成任何物品（2026-11-20）
                for (int attempt = 0; attempt < 3; attempt++)
                {
                    try { fish = location.getFish(rod.fishingNibbleAccumulator, baitId, 5, who, Game1.player.DailyLuck + 0.05, bobberTile); } catch { }
                    if (fish == null) continue;
                    string fn = fish.Name ?? "";
                    bool junk = fn.Contains("Trash") || fn.Contains("Seaweed") || fn.Contains("Driftwood")
                             || fn.Contains("Soggy") || fn.Contains("Broken") || fn.Contains("Joja Cola")
                             || fn.Contains("Rotten") || fn.Contains("垃圾") || fn.Contains("海草");
                    if (!junk) break;
                }
                if (fish == null || ItemRegistry.GetDataOrErrorItem(fish.QualifiedItemId).IsErrorItem)
                    fish = ItemRegistry.Create("(O)168", 1, 0);

                // 2026-11-21 用户要求：要看得见钓鱼小游戏。
                // 原版只在 hit==false 时走「提竿 → startMinigameEndFunction」，而脚本驱动的鱼竿
                // 早就不在那个状态机里了（实测 menu 一直 null）。这里直接调原版方法把 BobberBar
                // 弹出来，交给 BobberBarAutoPlayPatch 自动玩；弹不出来就回落到「直接入包」。
                if (WhalePlayer.ModEntry.AutoPlayMinigame)
                {
                    try
                    {
                        rod.isNibbling = false;
                        rod.hit = false;
                        rod.isReeling = true;
                        try { rod.clearWaterDistance = 5; } catch { }
                        rod.startMinigameEndFunction(fish);
                        if (Game1.activeClickableMenu is BobberBar bar)
                        {
                            // 2026-11-24：验证用开关——D:\live\logs\force_treasure.txt 存在时，
                            // 强制这一局出宝箱（原版概率 15%，跑几百次才有，不好复现）。
                            try
                            {
                                if (System.IO.File.Exists(@"D:\live\logs\force_treasure.txt"))
                                {
                                    System.IO.File.Delete(@"D:\live\logs\force_treasure.txt");
                                    bar.treasure = true;
                                    bar.treasureAppearTimer = 600f;
                                    WhalePlayer.ModEntry.Trace?.Invoke("钓鱼: [强制] 本局出宝箱");
                                }
                            }
                            catch { }
                            WhalePlayer.ModEntry.Trace?.Invoke("钓鱼: 弹出小游戏 " + fish.Name
                                + " treasure=" + bar.treasure + " 宝箱计时=" + bar.treasureAppearTimer);
                            BobberBarAutoPlayPatch.Arm(fish);
                            return false;
                        }
                        WhalePlayer.ModEntry.Trace?.Invoke("钓鱼: 小游戏没弹出来，回落直接入包");
                    }
                    catch (Exception ex)
                    {
                        WhalePlayer.ModEntry.Trace?.Invoke("小游戏弹出异常: " + ex.Message + "，回落直接入包");
                    }
                }

                rod.isNibbling = false;
                rod.hit = false;
                rod.isReeling = true;
                // 直接进背包：原版 pullFishFromWater 是在「举鱼动画」的 endFunction 里才真正发鱼，
                // 脚本频繁复位鱼竿会把动画打断，导致日志有结算、包里没鱼（2026-11-18 实测）
                bool added = false;
                try
                {
                    var obj = fish as StardewValley.Object;
                    if (obj != null) added = who.addItemToInventoryBool(obj, false);
                }
                catch { }
                if (!added)
                {
                    try { Game1.createItemDebris(fish, who.Position, who.FacingDirection); } catch { }
                }
                try
                {
                    if (who.fishCaught != null && !who.fishCaught.ContainsKey(fish.ItemId))
                        who.fishCaught[fish.ItemId] = new int[] { 1, 0 };
                    else if (who.fishCaught != null)
                        who.fishCaught[fish.ItemId][0]++;
                }
                catch { }
                WhalePlayer.ModEntry.Trace?.Invoke("钓鱼: 入包 " + fish.Name + (added ? "" : "（背包满，掉地上）"));

                try { rod.doneFishing(who, true); } catch { }
                try { rod.isFishing = false; rod.isReeling = false; } catch { }
                WhalePlayer.ModEntry.ScriptedFishing = false;
                return false;
            }
            catch (Exception ex)
            {
                WhalePlayer.ModEntry.Trace?.Invoke("咬钩结算异常: " + ex.Message);
                return true;
            }
        }
    }


    /// <summary>自动玩钓鱼小游戏：让绿条跟着鱼走（视觉上就是绿条在追鱼）。</summary>
    [HarmonyPatch(typeof(BobberBar), nameof(BobberBar.update))]
    internal static class BobberBarAutoPlayPatch
    {
        private static int _ticks = 0;
        private static int _resolveTicks = 0;
        private static System.DateTime _armedAt = System.DateTime.MinValue;
        internal static Item PendingFish = null;

        internal static void Arm(Item fish)
        {
            PendingFish = fish;
            _resolveTicks = 0;
            _ticks = 0;
            _armedAt = System.DateTime.UtcNow;   // 兜底结算用真实时间，别再拿 tick 当秒
        }

        /// <summary>宝箱奖励：原版那套在 doPullFishFromWater 里，脚本鱼竿跑不到，
        /// 这里给等价的「金币 + 1~2 件矿物/宝石」，至少宝箱不白吃。</summary>
        private static void GrantTreasureLoot(Farmer who)
        {
            try
            {
                int gold = Game1.random.Next(60, 260);
                who.Money += gold;
                string[] pool = { "(O)60", "(O)62", "(O)64", "(O)66", "(O)68", "(O)70", "(O)72", "(O)82", "(O)84", "(O)86", "(O)749" };
                int n = 1 + Game1.random.Next(2);
                for (int i = 0; i < n; i++)
                {
                    var it = ItemRegistry.Create(pool[Game1.random.Next(pool.Length)], 1, 0);
                    if (!who.addItemToInventoryBool(it, false))
                        Game1.createItemDebris(it, who.Position, who.FacingDirection);
                }
                WhalePlayer.ModEntry.Trace?.Invoke("钓鱼: 宝箱奖励 +" + gold + "g + " + n + " 件物品");
            }
            catch (Exception ex) { WhalePlayer.ModEntry.Trace?.Invoke("宝箱奖励异常: " + ex.Message); }
        }

        private static int CountInInventory(Farmer who, string qualifiedId)
        {
            int n = 0;
            try
            {
                foreach (var it in who.Items)
                    if (it != null && it.QualifiedItemId == qualifiedId) n += it.Stack;
            }
            catch { }
            return n;
        }

        private static void Prefix(BobberBar __instance)
        {
            // 2026-11-24：试过「不手动结算、替原版 tick 推 pullFishFromWater」的方案，
            // 结果反复 tickUpdate 会把鱼竿状态机搞坏、游戏卡死（state 80 秒不更新）。
            // 回退成手动结算 + 原版举鱼，30 秒真实时间兜底。
            bool timeout = _armedAt != System.DateTime.MinValue
                           && (System.DateTime.UtcNow - _armedAt).TotalSeconds > 30;
            if (PendingFish != null && (__instance.distanceFromCatching >= 1f || timeout))
            {
                var fish = PendingFish;
                PendingFish = null;
                _resolveTicks = 0;
                bool treasureCaught = false;
                try { treasureCaught = __instance.treasureCaught; } catch { }
                try { Game1.exitActiveMenu(); } catch { }
                try
                {
                    var rod = Game1.player?.CurrentTool as StardewValley.Tools.FishingRod;
                    if (rod != null && fish != null)
                    {
                        var who = Game1.player;
                        int before = CountInInventory(who, fish.QualifiedItemId);
                        // 走原版 pullFishFromWater：提竿、鱼从水里飞到手里、举鱼、宝箱，
                        // 全都由原版这套负责。脚本状态下没人调 rod.tickUpdate，
                        // NetEvent 不会被 Poll，所以这里手动 tick 几下把事件推出去。
                        try
                        {
                            rod.lastUser = who;
                            // 2026-11-23：原版 tickUpdate 只在「拿着工具使用中」时被游戏调用，
                            // 脚本这边手动调，但先把 UsingTool 打开，否则事件 Poll 后
                            // doPullFishFromWater 里的状态判断会直接跳过（实测鱼不入包）。
                            try { who.UsingTool = true; who.canReleaseTool = false; } catch { }
                            rod.pullFishFromWater(fish.QualifiedItemId, -1, 0, 0, treasureCaught,
                                false, false, null, false, 1);
                            for (int i = 0; i < 12; i++) rod.tickUpdate(Game1.currentGameTime, who);
                        }
                        catch (Exception ex)
                        {
                            WhalePlayer.ModEntry.Trace?.Invoke("pullFishFromWater 异常: " + ex.Message);
                        }
                        int after = CountInInventory(who, fish.QualifiedItemId);
                        if (after > before)
                        {
                            WhalePlayer.ModEntry.Trace?.Invoke("钓鱼: 原版结算（提竿/举鱼）" + fish.Name + (treasureCaught ? " + 宝箱" : ""));
                        }
                        else
                        {
                            // 原版事件没跑起来 → 退回直接入包 + 举鱼，至少不丢鱼
                            bool added = false;
                            try
                            {
                                var obj = fish as StardewValley.Object;
                                if (obj != null) added = who.addItemToInventoryBool(obj, false);
                            }
                            catch { }
                            if (!added) { try { Game1.createItemDebris(fish, who.Position, who.FacingDirection); } catch { } }
                            try
                            {
                                if (who.fishCaught != null && !who.fishCaught.ContainsKey(fish.ItemId))
                                    who.fishCaught[fish.ItemId] = new int[] { 1, 0 };
                                else if (who.fishCaught != null)
                                    who.fishCaught[fish.ItemId][0]++;
                            }
                            catch { }
                            try { rod.doneFishing(who, true); } catch { }
                            try { rod.isFishing = false; rod.isReeling = false; rod.isNibbling = false; rod.hit = false; } catch { }
                            // 2026-11-24：原版「提竿」动画在脚本鱼竿上跑不起来（NetEvent 不执行），
                            // 这里补一个纯视觉版：浮标从水面飞回手里 + 收线音效，然后接举鱼。
                            try
                            {
                                var loc = who.currentLocation;
                                var start = new Microsoft.Xna.Framework.Vector2(rod.bobber.X, rod.bobber.Y);
                                var end = who.Position;
                                var fly = new TemporaryAnimatedSprite(
                                    "TileSheets\\bobbers",
                                    new Microsoft.Xna.Framework.Rectangle(0, 0, 16, 16),
                                    55f, 10, 0, start, false, false,
                                    (float)who.StandingPixel.Y / 10000f, 0f,
                                    Microsoft.Xna.Framework.Color.White, 4f, 0f, 0f, 0f);
                                fly.motion = (end - start) / 8f;
                                loc.temporarySprites.Add(fly);
                                who.playNearbySoundLocal("pullItemFromWater");
                            }
                            catch { }
                            if (treasureCaught) GrantTreasureLoot(who);
                            try { who.holdUpItemThenMessage(fish, false); } catch { }
                            try { who.freezePause = Math.Min(who.freezePause, 3200); } catch { }
                            WhalePlayer.ModEntry.Trace?.Invoke("钓鱼: 兜底入包 " + fish.Name + (added ? "" : "（背包满，掉地上）"));
                        }
                    }
                }
                catch (Exception ex)
                {
                    WhalePlayer.ModEntry.Trace?.Invoke("小游戏结算异常: " + ex.Message);
                }
                WhalePlayer.ModEntry.ScriptedFishing = false;
                return;
            }
            if (_ticks < 3)
            {
                _ticks++;
                WhalePlayer.ModEntry.Trace?.Invoke("自动玩小游戏：补丁进入 tick=" + _ticks);
            }
            // 2026-11-21：不再依赖 AutoPlayMinigame 开关（之前判断可能被中途关掉），
            // 只要小游戏在跑就自动玩。
            try
            {
                if (__instance.fadeOut) return;
                // 2026-11-21：伪造鼠标那个版本在脚本驱动的鱼竿上进不去（bar 纹丝不动，15 秒后被看门狗掐掉），
                // 改成直接算位置：绿条中心追鱼的位置，速度抹平（跟宝箱补丁一个套路，画面里就是绿条在追鱼）。
                float h = __instance.bobberBarHeight;
                // 2026-11-23：宝箱浮出来时优先去接宝箱（原版两者都要压住，这里先接箱子再接鱼），
                // 否则绿条一直贴着鱼，宝箱永远吃不到，结算时箱子就丢了。
                bool treasureUp = __instance.treasure && !__instance.treasureCaught
                                  && __instance.treasureAppearTimer <= 0f;
                float want;
                if (treasureUp)
                    want = __instance.treasurePosition + 12f;
                else
                    want = __instance.bobberPosition + 32f - h / 2f;
                want = Math.Clamp(want, 0f, 568f - h);
                float cur = __instance.bobberBarPos;
                float delta = want - cur;
                // 限速跟随，别瞬移（看着像人在操作）
                float step = Math.Clamp(delta, -9f, 9f);
                __instance.bobberBarPos = cur + step;
                __instance.bobberBarSpeed = 0f;
                // 2026-11-23：不再给正向补偿。原版自己按「绿条覆盖鱼」算 progress，
                // 强制 +0.08 会 1 秒就满，看着像秒钓。
                // （保留一点小抖动，让绿条不是死贴着鱼走）
                if (Game1.random.NextDouble() < 0.08)
                    __instance.bobberBarPos = Math.Clamp(__instance.bobberBarPos + (float)(Game1.random.NextDouble() * 2.0 - 1.0) * 2f, 0f, 568f - h);
                if (_ticks >= 3 && _ticks < 40 && _ticks % 10 == 0)
                    WhalePlayer.ModEntry.Trace?.Invoke(
                        string.Format("自动玩 tick={0} dFC={1:F2} barPos={2:F0} fish={3:F0} h={4} fade={5}",
                            _ticks, __instance.distanceFromCatching, __instance.bobberBarPos,
                            __instance.bobberPosition, __instance.bobberBarHeight, __instance.fadeOut));
                if (_ticks < 60) _ticks++;
            }
            catch { }
        }
    }

    /// <summary>把原本 999 的堆叠上限提到 9999（工具/武器等本来就 1 的不受影响）。</summary>
    [HarmonyPatch(typeof(StardewValley.Object), nameof(StardewValley.Object.maximumStackSize))]
    internal static class BigStackPatch
    {
        private static void Postfix(ref int __result)
        {
            if (__result == 999) __result = 9999;
        }
    }

    /// <summary>宝箱补丁：只帮忙抓宝箱，不碰鱼（鱼交给原版/CJB 秒钓）。</summary>
    [HarmonyPatch(typeof(BobberBar), "update")]
    internal static class BobberBarPatch
    {
        private static void Postfix(BobberBar __instance)
        {
            if (!WhalePlayer.ModEntry.TreasureAssist) return;
            try
            {
                if (!__instance.treasure || __instance.treasureCaught) return;
                if (__instance.treasureAppearTimer > 0f) return;   // 宝箱还没浮出来

                float h = __instance.bobberBarHeight;
                float want = __instance.treasurePosition + 12f;
                __instance.bobberBarPos = Math.Clamp(want, 0f, 568f - h);
                __instance.bobberBarSpeed = 0f;
                if (__instance.treasureCatchLevel < 1f)
                    __instance.treasureCatchLevel = Math.Min(1f, __instance.treasureCatchLevel + 0.03f);
            }
            catch (Exception) { }
        }
    }
}
