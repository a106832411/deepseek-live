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
                try { fish = location.getFish(rod.fishingNibbleAccumulator, baitId, 3, who, 0.0, bobberTile); } catch { }
                if (fish == null || ItemRegistry.GetDataOrErrorItem(fish.QualifiedItemId).IsErrorItem)
                    fish = ItemRegistry.Create("(O)168", 1, 0);

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
