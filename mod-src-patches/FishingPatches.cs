using System;
using System.Reflection;
using HarmonyLib;
using StardewValley;
using StardewValley.Menus;

namespace StardewMCPBridge
{
    /// <summary>
    /// 钓鱼小游戏：绿条跟随鱼 + 捕获完成时把鱼正常交付到背包。
    /// 背景：合成鼠标点击有效但「按住」无效，小游戏靠补丁控绿条；
    /// 捕获判定能触发（游戏弹过「沙丁鱼 33cm」），但物品没有进背包，这里补交付。
    /// </summary>
    [HarmonyPatch(typeof(BobberBar), "update")]
    public static class BobberBarFollowFishPatch
    {
        private static FieldInfo barPosField;
        private static FieldInfo fishPosField;
        private static FieldInfo barSpeedField;
        private static FieldInfo distanceField;
        private static FieldInfo whichFishField;
        private static FieldInfo fishSizeField;
        private static readonly System.Collections.Generic.HashSet<int> delivered = new System.Collections.Generic.HashSet<int>();

        public static void Prefix(BobberBar __instance)
        {
            try
            {
                if (barPosField == null)
                    barPosField = typeof(BobberBar).GetField("bobberBarPos",
                        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (fishPosField == null)
                    fishPosField = typeof(BobberBar).GetField("bobberPosition",
                        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (barSpeedField == null)
                    barSpeedField = typeof(BobberBar).GetField("bobberBarSpeed",
                        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);

                if (barPosField != null && fishPosField != null)
                {
                    barPosField.SetValue(__instance, fishPosField.GetValue(__instance));
                    if (barSpeedField != null) barSpeedField.SetValue(__instance, 0f);
                }
            }
            catch { }
        }

        public static void Postfix(BobberBar __instance)
        {
            try
            {
                if (distanceField == null)
                    distanceField = typeof(BobberBar).GetField("distanceFromCatching",
                        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (whichFishField == null)
                    whichFishField = typeof(BobberBar).GetField("whichFish",
                        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (fishSizeField == null)
                    fishSizeField = typeof(BobberBar).GetField("fishSize",
                        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (distanceField == null || whichFishField == null) return;

                float dist;
                try { dist = Convert.ToSingle(distanceField.GetValue(__instance)); }
                catch { return; }

                if (dist < 0.999f) return;
                var rawFish = whichFishField.GetValue(__instance);
                string fishKey = null;
                if (rawFish is int fi && fi > 0) fishKey = "(O)" + fi;
                else if (rawFish is string fstr && fstr.Length > 0) fishKey = fstr.Contains(")") ? fstr : "(O)" + fstr;
                if (fishKey == null) return;
                int key = System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(__instance);
                if (!delivered.Add(key)) return;

                // 和游戏正常钓到鱼一样：创建鱼物品并放进背包
                try { System.IO.File.AppendAllText(System.IO.Path.Combine(System.IO.Path.GetDirectoryName(typeof(BobberBarFollowFishPatch).Assembly.Location), "fish_debug.log"), $"catch dist={dist} key={fishKey} size={fishSizeField?.GetValue(__instance)}\n"); } catch { }
                var item = ItemRegistry.Create(fishKey);
                if (item == null) return;
                item.Stack = 1;
                try { Game1.player.addItemToInventoryBool(item, true); }
                catch
                {
                    try
                    {
                        for (int si = 0; si < Game1.player.MaxItems; si++)
                        {
                            if (Game1.player.Items[si] == null) { Game1.player.Items[si] = item; break; }
                        }
                    }
                    catch { }
                }
                // 关闭小游戏菜单，模拟收杆完成
                try { Game1.activeClickableMenu?.exitThisMenu(false); } catch { }
                try { Game1.player.UsingTool = false; } catch { }
            }
            catch { }
        }
    }
}
