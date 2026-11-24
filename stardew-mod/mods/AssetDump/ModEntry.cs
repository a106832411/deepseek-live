using System;
using System.IO;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;

namespace AssetDump
{
    /// <summary>临时工具：把 farmer 相关贴图导出成 PNG，方便做 CP 包时当底图。</summary>
    public class ModEntry : Mod
    {
        public override void Entry(IModHelper helper)
        {
            helper.Events.GameLoop.SaveLoaded += (s, e) => Dump();
        }

        private void Dump()
        {
            string dir = @"D:\live\logs\farmer_assets";
            Directory.CreateDirectory(dir);

            // 先记下当前角色穿了什么，方便挑替换目标
            try
            {
                var p = Game1.player;
                var sb = new System.Text.StringBuilder();
                sb.AppendLine("name=" + p.Name);
                sb.AppendLine("isMale=" + p.IsMale);
                sb.AppendLine("shirtItem=" + (p.shirtItem.Value?.QualifiedItemId ?? "null"));
                sb.AppendLine("pantsItem=" + (p.pantsItem.Value?.QualifiedItemId ?? "null"));
                sb.AppendLine("hat=" + (p.hat.Value?.QualifiedItemId ?? "null"));
                File.WriteAllText(Path.Combine(dir, "player_look.txt"), sb.ToString());
            }
            catch (Exception ex) { Monitor.Log("dump look fail: " + ex.Message, LogLevel.Warn); }

            // 衣服数据（挑「像女仆装」的原版件用）—— 1.6 是强类型数据，用反射导出
            try
            {
                var lines = new System.Collections.Generic.List<string>();
                foreach (var assetName in new[] { "Data/Shirts", "Data/Pants" })
                {
                    object data = Game1.content.Load<object>(assetName);
                    var dict = data as System.Collections.IDictionary;
                    if (dict == null) { lines.Add(assetName + "\tNOT A DICT: " + data.GetType()); continue; }
                    foreach (System.Collections.DictionaryEntry de in dict)
                    {
                        var parts = new System.Collections.Generic.List<string>();
                        var v = de.Value;
                        if (v != null)
                        {
                            foreach (var prop in v.GetType().GetProperties())
                            {
                                object pv = null;
                                try { pv = prop.GetValue(v); } catch { }
                                if (pv == null) continue;
                                parts.Add(prop.Name + "=" + pv);
                            }
                        }
                        lines.Add(assetName + "\t" + de.Key + "\t" + string.Join("; ", parts));
                    }
                }
                File.WriteAllLines(Path.Combine(dir, "clothing_data.tsv"), lines);
                Monitor.Log("dump clothing data " + lines.Count + " rows", LogLevel.Info);
                // 名称表：按 ID 建物品读名字（挑衣服最快的方式）
                var nameLines = new System.Collections.Generic.List<string>();
                foreach (var pair in new[] { new[] { "Data/Shirts", "(S)" }, new[] { "Data/Pants", "(P)" }, new[] { "Data/Hats", "(H)" } })
                {
                    object d2 = Game1.content.Load<object>(pair[0]);
                    var dd = d2 as System.Collections.IDictionary;
                    if (dd == null) continue;
                    foreach (System.Collections.DictionaryEntry de in dd)
                    {
                        string id = pair[1] + de.Key;
                        string nm = "";
                        try
                        {
                            var item = ItemRegistry.Create(id);
                            nm = item?.DisplayName ?? "";
                        }
                        catch { }
                        nameLines.Add(pair[0] + "\t" + id + "\t" + nm);
                    }
                }
                File.WriteAllLines(Path.Combine(dir, "clothing_names.tsv"), nameLines);
                Monitor.Log("dump clothing names " + nameLines.Count, LogLevel.Info);
            }
            catch (Exception ex) { Monitor.Log("dump clothing fail: " + ex, LogLevel.Warn); }

            // 一次性换装：读 D:\live\logs\look.json，应用后改名，避免每次读档都改
            try
            {
                string cfg = @"D:\live\logs\look.json";
                if (File.Exists(cfg))
                {
                    var jo = Newtonsoft.Json.Linq.JObject.Parse(File.ReadAllText(cfg));
                    var p = Game1.player;
                    if (jo["male"] != null) p.changeGender((bool)jo["male"]);
                    if (jo["hair"] != null) p.hair.Value = (int)jo["hair"];
                    if (jo["skin"] != null) p.skin.Value = (int)jo["skin"];
                    // 2026-11-24：先穿衣服再染色。之前先设 pantsColor 再设 pantsItem，
                    // 颜色会被新衣服重置成默认色（白裙子不生效就是这个）。
                    if (jo["shirt"] != null) p.shirtItem.Value = (StardewValley.Objects.Clothing)ItemRegistry.Create((string)jo["shirt"]);
                    if (jo.ContainsKey("hat"))
                    {
                        var hv = jo["hat"];
                        if (hv == null || hv.Type == Newtonsoft.Json.Linq.JTokenType.Null) p.hat.Value = null;
                        else p.hat.Value = (StardewValley.Objects.Hat)ItemRegistry.Create((string)hv);
                    }
                    if (jo["pants"] != null) p.pantsItem.Value = (StardewValley.Objects.Clothing)ItemRegistry.Create((string)jo["pants"]);
                    if (jo["hairColor"] != null)
                    {
                        var c = (string)jo["hairColor"];
                        var parts = c.Split(',');
                        p.hairstyleColor.Value = new Microsoft.Xna.Framework.Color(int.Parse(parts[0]), int.Parse(parts[1]), int.Parse(parts[2]));
                    }
                    if (jo["pantsColor"] != null)
                    {
                        var c = (string)jo["pantsColor"];
                        var parts = c.Split(',');
                        p.pantsColor.Value = new Microsoft.Xna.Framework.Color(int.Parse(parts[0]), int.Parse(parts[1]), int.Parse(parts[2]));
                    }
                    try { p.FarmerSprite.setCurrentFrame(0); } catch { }
                    try { File.Copy(cfg, cfg + ".applied", true); } catch { } File.Delete(cfg);
                    try
                    {
                        var sb2 = new System.Text.StringBuilder();
                        sb2.AppendLine("name=" + p.Name);
                        sb2.AppendLine("isMale=" + p.IsMale);
                        sb2.AppendLine("hair=" + p.hair.Value);
                        sb2.AppendLine("shirtItem=" + (p.shirtItem.Value?.QualifiedItemId ?? "null"));
                        sb2.AppendLine("pantsItem=" + (p.pantsItem.Value?.QualifiedItemId ?? "null"));
                        sb2.AppendLine("hat=" + (p.hat.Value?.QualifiedItemId ?? "null"));
                        File.WriteAllText(Path.Combine(dir, "player_look_after.txt"), sb2.ToString());
                    }
                    catch { }
                    Monitor.Log("look applied", LogLevel.Info);
                }
            }
            catch (Exception ex) { Monitor.Log("look apply fail: " + ex, LogLevel.Warn); }

            string[] assets =
            {
                "Characters/Farmer/farmer_base",
                "Characters/Farmer/farmer_girl_base",
                "Characters/Farmer/farmer_base_bald",
                "Characters/Farmer/farmer_girl_base_bald",
                "Characters/Farmer/hairstyles",
                "Characters/Farmer/hairstyles2",
                "Characters/Farmer/shirts",
                "Characters/Farmer/pants",
                "Characters/Farmer/hats",
                "Characters/Farmer/accessories",
                "Characters/Farmer/skinColors",
                "Characters/Farmer/shoeColors"
            };

            foreach (var name in assets)
            {
                try
                {
                    var tex = Game1.content.Load<Texture2D>(name);
                    string file = Path.Combine(dir, name.Replace("/", "_") + ".png");
                    using (var fs = File.Create(file))
                        tex.SaveAsPng(fs, tex.Width, tex.Height);
                    Monitor.Log($"dump {name} {tex.Width}x{tex.Height}", LogLevel.Info);
                }
                catch (Exception ex)
                {
                    Monitor.Log($"dump fail {name}: {ex.Message}", LogLevel.Warn);
                }
            }
        }
    }
}
