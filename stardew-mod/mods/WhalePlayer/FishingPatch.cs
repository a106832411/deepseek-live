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
        private static bool Prefix() => !WhalePlayer.ModEntry.ScriptedFishing;
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


    /// <summary>脚本钓鱼：接管 DoFunction 的"咬钩"分支，按原版逻辑建 BobberBar。</summary>
    [HarmonyPatch(typeof(FishingRod), nameof(FishingRod.DoFunction))]
    internal static class NibbleBranchPatch
    {
        private static bool Prefix(FishingRod __instance, GameLocation location, int x, int y, int power, Farmer who)
        {
            try
            {
                if (!WhalePlayer.ModEntry.ScriptedFishing) return true;
                if (!__instance.isNibbling) return true;
                if (Game1.activeClickableMenu is BobberBar) return false;

                var rod = __instance;
                string baitId = null;
                try { baitId = rod.GetBait()?.QualifiedItemId; } catch { }
                var tackle = new List<string>();
                try { tackle = rod.GetTackleQualifiedItemIDs() ?? tackle; } catch { }

                var bobberTile = new Microsoft.Xna.Framework.Vector2(rod.bobber.X / 64f, rod.bobber.Y / 64f);
                Item fish = null;
                try
                {
                    fish = location.getFish(rod.fishingNibbleAccumulator, baitId, 3, who, 0.0, bobberTile);
                }
                catch (Exception) { }
                if (fish == null || ItemRegistry.GetDataOrErrorItem(fish.QualifiedItemId).IsErrorItem)
                    fish = ItemRegistry.Create("(O)168", 1, 0);

                Game1.activeClickableMenu = new BobberBar(fish.ItemId, 0.6f, false, tackle, fish.SetFlagOnPickup, false, baitId, false);
                WhalePlayer.ModEntry.Trace?.Invoke("咬钩分支接管: 已创建 BobberBar fish=" + fish.Name);
                return false;   // 跳过原函数
            }
            catch (Exception ex)
            {
                WhalePlayer.ModEntry.Trace?.Invoke("咬钩分支异常: " + ex.Message);
                return true;
            }
        }
    }

    /// <summary>小游戏补丁：每帧把绿条贴到鱼身上，并加速进度，自动钓上来。</summary>
    [HarmonyPatch(typeof(BobberBar), "update")]
    internal static class BobberBarPatch
    {
        private static void Postfix(BobberBar __instance)
        {
            try
            {
                float target = __instance.bobberPosition - (__instance.bobberBarHeight / 2f - 8f);
                __instance.bobberBarPos = Math.Clamp(target, 0f, 548f - __instance.bobberBarHeight);
                __instance.bobberBarSpeed = 0f;
                __instance.distanceFromCatching = 1f;   // 每帧钉满，防止 fadeOut 期间被算回去

                // 进度满了就触发收盘动画（原版在 fadeOut 结束时结算）
                if (__instance.distanceFromCatching >= 0.99f)
                {
                    var t = typeof(BobberBar);
                    var bf = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                    var ff = t.GetField("fadeOut", bf);
                    if (ff != null)
                    {
                        var cur = ff.GetValue(__instance);
                        var vp = cur?.GetType().GetProperty("Value");
                        if (vp != null && vp.CanWrite) vp.SetValue(cur, true);
                        else ff.SetValue(__instance, true);
                    }
                }
            }
            catch (Exception) { }
        }
    }
}
