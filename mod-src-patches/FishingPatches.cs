using System;
using System.Reflection;
using HarmonyLib;
using StardewValley;
using StardewValley.Menus;

namespace StardewMCPBridge
{
    /// <summary>
    /// 钓鱼小游戏自动完成：每次 update 前把进度拉满 + 判定在绿条内。
    /// 合成鼠标按住无法驱动小游戏（见 fishing_test），所以用 Harmony 在游戏内部处理。
    /// </summary>
    [HarmonyPatch(typeof(BobberBar), "update")]
    public static class BobberBarAutoCatchPatch
    {
        private static FieldInfo distanceField;
        private static FieldInfo inBarField;

        public static void Prefix(BobberBar __instance)
        {
            try
            {
                if (distanceField == null)
                    distanceField = typeof(BobberBar).GetField("distanceFromCatching",
                        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (inBarField == null)
                    inBarField = typeof(BobberBar).GetField("bobberInBar",
                        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (distanceField != null) distanceField.SetValue(__instance, 1f);
                if (inBarField != null) inBarField.SetValue(__instance, true);
            }
            catch
            {
                // 忽略，退回正常钓鱼
            }
        }
    }
}
