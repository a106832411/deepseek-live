using System;
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

    /// <summary>小游戏补丁：每帧把绿条贴到鱼身上，并加速进度，自动钓上来。</summary>
    [HarmonyPatch(typeof(BobberBar), "update")]
    internal static class BobberBarPatch
    {
        private static void Postfix(BobberBar __instance, Microsoft.Xna.Framework.GameTime time)
        {
            try
            {
                float target = __instance.bobberPosition - (__instance.bobberBarHeight / 2f - 8f);
                __instance.bobberBarPos = Math.Clamp(target, 0f, 548f - __instance.bobberBarHeight);
                __instance.bobberBarSpeed = 0f;
                if (__instance.distanceFromCatching < 0.99f)
                    __instance.distanceFromCatching = Math.Min(1f, __instance.distanceFromCatching + 0.02f);
                if (__instance.treasure && __instance.treasureCatchLevel > 0.01f)
                    __instance.treasureCatchLevel = Math.Min(1f, __instance.treasureCatchLevel + 0.03f);
            }
            catch (Exception) { }
        }
    }
}
