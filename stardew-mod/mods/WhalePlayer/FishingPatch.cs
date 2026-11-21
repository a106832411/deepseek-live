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
                        if (Game1.activeClickableMenu is BobberBar)
                        {
                            WhalePlayer.ModEntry.Trace?.Invoke("钓鱼: 弹出小游戏 " + fish.Name);
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
        private static void Prefix(BobberBar __instance)
        {
            // 2026-11-21：不再依赖 AutoPlayMinigame 开关（之前判断可能被中途关掉），
            // 只要小游戏在跑就自动玩。
            try
            {
                if (__instance.fadeOut) return;
                // 2026-11-21：伪造鼠标那个版本在脚本驱动的鱼竿上进不去（bar 纹丝不动，15 秒后被看门狗掐掉），
                // 改成直接算位置：绿条中心追鱼的位置，速度抹平（跟宝箱补丁一个套路，画面里就是绿条在追鱼）。
                float h = __instance.bobberBarHeight;
                float want = __instance.bobberPosition + 32f - h / 2f;
                want = Math.Clamp(want, 0f, 568f - h);
                float cur = __instance.bobberBarPos;
                float delta = want - cur;
                // 限速跟随，别瞬移（看着像人在操作）
                float step = Math.Clamp(delta, -9f, 9f);
                __instance.bobberBarPos = cur + step;
                __instance.bobberBarSpeed = 0f;
                // 绿条压住鱼就正常涨进度；这里再给一点正向补偿，保证 1 秒内收杆
                // （原版 distanceFromCatching>=1 就结算，走正常「钓上来」的动画和音效）
                if (__instance.distanceFromCatching < 1f)
                    __instance.distanceFromCatching = Math.Min(1f, __instance.distanceFromCatching + 0.08f);
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
