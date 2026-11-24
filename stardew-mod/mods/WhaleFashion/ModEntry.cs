using System;
using System.Collections;
using System.Linq;
using System.Reflection;
using FashionSense.Framework.Interfaces.API;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;

namespace WhaleFashion
{
    /// <summary>读档时自动穿 Fashion Sense 装扮（不用手动点镜子）。</summary>
    public class ModEntry : Mod
    {
        private IApi api;

        // 类型|外观名（与 Maid Dress 包里的 Name 对应）
        private static readonly string[] Plan =
        {
            "Hair|Paddy32.Maiddressoutfit|Diana Maid Hair",
            "Hat|Paddy32.Maiddressoutfit|maid hairband",
            "Shirt|Paddy32.Maiddressoutfit|maid shirt",
            "Pants|Paddy32.Maiddressoutfit|maid dress",
            "Shoes|Paddy32.Maiddressoutfit|maid shoes",
            "Sleeves|Paddy32.Maiddressoutfit|Sleeves Template",
        };

        public override void Entry(IModHelper helper)
        {
            helper.Events.GameLoop.GameLaunched += (s, e) =>
            {
                try
                {
                    api = helper.ModRegistry.GetApi<IApi>("PeacefulEnd.FashionSense");
                    Monitor.Log("Fashion Sense API: " + (api != null ? "ok" : "null"), LogLevel.Info);
                }
                catch (Exception ex) { Monitor.Log("GetApi 失败: " + ex.Message, LogLevel.Error); }
            };

            helper.Events.GameLoop.SaveLoaded += (s, e) => Apply();
            helper.Events.GameLoop.DayStarted += (s, e) => Apply();
        }

        private const string PackId = "Paddy32.Maiddressoutfit";

        private void Apply()
        {
            if (api == null) { Monitor.Log("没有拿到 FS API，跳过", LogLevel.Warn); return; }
            try
            {
                foreach (var line in Plan)
                {
                    var parts = line.Split('|');
                    var type = (IApi.Type)Enum.Parse(typeof(IApi.Type), parts[0]);
                    var packId = parts[1];
                    var want = parts[2];
                    var r = api.SetAppearance(type, packId, want, ModManifest);
                    Monitor.Log($"穿上 {type} {want} => ok={r.Key} {r.Value}", LogLevel.Info);
                    if (type == IApi.Type.Hair)
                    {
                        var rc = api.SetAppearanceColor(IApi.Type.Hair, new Microsoft.Xna.Framework.Color(95, 117, 179), ModManifest);
                        Monitor.Log("发色深蓝 => " + rc.Key + " " + rc.Value, LogLevel.Info);
                    }
                }
            }
            catch (Exception ex) { Monitor.Log("Apply 异常: " + ex, LogLevel.Error); }
        }
    }
}
