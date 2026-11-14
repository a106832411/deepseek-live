using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.GameData.Shops;

namespace WhaleShop
{
    /// <summary>
    /// 给鲸live 的星露谷 AI 补一个"买种子/买物品"动作：
    /// Agent 往 Mods/WhaleShop/orders/xxx.json 写订单，本 mod 每 ~0.33 秒扫一次并执行，
    /// 结果写到同目录 xxx.result.json，Agent 轮询读取。
    ///
    /// 订单格式：
    ///   {"actionType":"buy_seed","item":"Strawberry","count":8}
    ///   {"actionType":"buy_seed","itemId":"745","count":8,"price":100}   // 可强制指定单价
    /// </summary>
    public class ModEntry : Mod
    {
        private string OrdersDir = "";
        private readonly HashSet<string> InFlight = new();

        public override void Entry(IModHelper helper)
        {
            OrdersDir = Path.Combine(Helper.DirectoryPath, "orders");
            Directory.CreateDirectory(OrdersDir);
            Helper.Events.GameLoop.UpdateTicked += OnUpdateTicked;
            Helper.Events.GameLoop.GameLaunched += (s, e) =>
                Monitor.Log($"WhaleShop 就绪，订单目录：{OrdersDir}", LogLevel.Info);
        }

        private void OnUpdateTicked(object sender, UpdateTickedEventArgs e)
        {
            if (!Context.IsWorldReady) return;
            if (e.Ticks % 20 != 0) return;   // 约每 1/3 秒

            string[] files;
            try { files = Directory.GetFiles(OrdersDir, "*.json"); }
            catch { return; }

            foreach (var path in files)
            {
                if (path.EndsWith(".result.json", StringComparison.OrdinalIgnoreCase)) continue;
                if (InFlight.Contains(path)) continue;
                InFlight.Add(path);
                try { ProcessOrder(path); }
                catch (Exception ex)
                {
                    Monitor.Log($"处理订单失败：{ex}", LogLevel.Error);
                    WriteResult(path, new JObject { ["ok"] = false, ["error"] = ex.Message });
                }
                finally
                {
                    InFlight.Remove(path);
                    try { File.Delete(path); } catch { }
                }
            }
        }

        private void ProcessOrder(string path)
        {
            var jo = JObject.Parse(File.ReadAllText(path));
            var action = (string)jo["actionType"] ?? (string)jo["type"] ?? "";
            if (action == "inventory")
            {
                var items = new JArray();
                // 顺手修复：背包被缩短过就补回 36 格
                while (Game1.player.Items.Count < 36) Game1.player.Items.Add(null);
                int free = 0;
                foreach (var it in Game1.player.Items)
                {
                    if (it == null) { free++; continue; }
                    int price = 0;
                    try { price = it is StardewValley.Object o ? o.salePrice() : 0; } catch { }
                    items.Add(new JObject
                    {
                        ["name"] = it.Name ?? "",
                        ["id"] = it.ItemId ?? "",
                        ["stack"] = it.Stack,
                        ["type"] = (it as StardewValley.Object)?.Type ?? it.GetType().Name,
                        ["price"] = price
                    });
                }
                WriteResult(path, new JObject
                {
                    ["ok"] = true,
                    ["money"] = Game1.player.Money,
                    ["free"] = free,
                    ["slots"] = Game1.player.Items.Count,
                    ["items"] = items
                });
                return;
            }

            if (action != "buy_seed" && action != "buy")
            {
                WriteResult(path, new JObject { ["ok"] = false, ["error"] = $"不支持的动作：{action}" });
                return;
            }

            int count = jo["count"]?.Value<int>() ?? 1;
            count = Math.Clamp(count, 1, 999);

            string itemId = (string)jo["itemId"];
            if (string.IsNullOrWhiteSpace(itemId))
            {
                var names = new List<string>();
                var single = (string)jo["item"];
                if (!string.IsNullOrWhiteSpace(single)) names.Add(single.Trim());
                var many = jo["items"] as JArray;
                if (many != null) names.AddRange(many.Select(x => (x?.ToString() ?? "").Trim()).Where(x => x.Length > 0));
                itemId = ResolveSeedId(names);
            }

            if (string.IsNullOrWhiteSpace(itemId))
            {
                WriteResult(path, new JObject
                {
                    ["ok"] = false,
                    ["error"] = "找不到对应的种子（把 item 换成游戏内英文名，或直接给 itemId）",
                    ["seeds"] = string.Join("、", Game1.objectData.Where(kv => (kv.Value.Type ?? "") == "Seeds").Take(40).Select(kv => $"{kv.Key}:{kv.Value.Name}"))
                });
                return;
            }

            int unit = jo["price"]?.Value<int>() ?? LookupPrice(itemId) ?? 0;
            if (unit <= 0)
            {
                WriteResult(path, new JObject { ["ok"] = false, ["error"] = $"查不到 {itemId} 的价格，请在订单里带 price 字段" });
                return;
            }

            int cost = unit * count;
            if (Game1.player.Money < cost)
            {
                WriteResult(path, new JObject
                {
                    ["ok"] = false,
                    ["error"] = "钱不够",
                    ["need"] = cost,
                    ["money"] = Game1.player.Money
                });
                return;
            }

            var item = ItemRegistry.Create(itemId, count, 0);
            // 背包必须是 36 格定长（被别的 mod 或 Remove 缩短过就先补齐）
            while (Game1.player.Items.Count < 36) Game1.player.Items.Add(null);
            bool added = Game1.player.addItemToInventoryBool(item, false);
            if (!added) added = Game1.player.addItemToInventoryBool(item, true);
            if (!added)
            {
                int free = 0;
                try
                {
                    foreach (var slot in Game1.player.Items)
                        if (slot == null) free++;
                }
                catch { }
                WriteResult(path, new JObject
                {
                    ["ok"] = false,
                    ["error"] = $"背包放不下（空位 {free} 个）",
                    ["free"] = free
                });
                return;
            }

            Game1.player.Money -= cost;
            WriteResult(path, new JObject
            {
                ["ok"] = true,
                ["itemId"] = itemId,
                ["name"] = item?.Name ?? "",
                ["count"] = count,
                ["unit"] = unit,
                ["cost"] = cost,
                ["money"] = Game1.player.Money
            });
            Monitor.Log($"买入 {count} 个 {item?.Name}（{cost}g），余额 {Game1.player.Money}g", LogLevel.Info);
        }

        /// <summary>把"作物名/种子名"解析成种子物品 ID。</summary>
        private string ResolveSeedId(List<string> names)
        {
            if (names.Count == 0) return null;
            var suffixes = new[] { "", " Seeds", " Seed", " Starter", "种子", "种子包", " Starter Pack" };

            // 1) 精确匹配（英文名 + 各种后缀），**必须 Type == Seeds**
            foreach (var raw in names)
            {
                var n = raw.Trim();
                if (n.Length == 0) continue;
                if (int.TryParse(n, out _) && Game1.objectData.ContainsKey(n)) return n;
                foreach (var suf in suffixes)
                {
                    var want = n + suf;
                    foreach (var kv in Game1.objectData)
                    {
                        if ((kv.Value.Type ?? "") != "Seeds") continue;
                        if (string.Equals(kv.Value.Name ?? "", want, StringComparison.OrdinalIgnoreCase))
                            return kv.Key;
                    }
                }
            }

            // 2) 模糊匹配：名字里含关键词且 Type == Seeds
            foreach (var raw in names)
            {
                var n = raw.Trim();
                if (n.Length == 0) continue;
                foreach (var kv in Game1.objectData)
                {
                    var od = kv.Value;
                    var nm = od.Name ?? "";
                    if ((od.Type ?? "") != "Seeds") continue;
                    if (nm.IndexOf(n, StringComparison.OrdinalIgnoreCase) >= 0) return kv.Key;
                }
            }
            return null;
        }

        /// <summary>单价：优先查 Data/Shops（种子店等）里的定价，再退到物件基础价。</summary>
        private int? LookupPrice(string itemId)
        {
            try
            {
                var shops = Game1.content.Load<Dictionary<string, ShopData>>("Data/Shops");
                foreach (var shop in shops.Values)
                {
                    if (shop?.Items == null) continue;
                    foreach (var si in shop.Items)
                    {
                        if (si == null) continue;
                        if (!string.Equals(si.ItemId, itemId, StringComparison.OrdinalIgnoreCase)) continue;
                        if (si.Price > 0) return si.Price;
                    }
                }
            }
            catch (Exception ex)
            {
                Monitor.Log($"读 Data/Shops 失败（退回基础价）：{ex.Message}", LogLevel.Trace);
            }

            if (Game1.objectData.TryGetValue(itemId, out var od) && od.Price > 0) return od.Price;
            return null;
        }

        private void WriteResult(string orderPath, JObject result)
        {
            var resultPath = Path.ChangeExtension(orderPath, ".result.json");
            result["ts"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            try { File.WriteAllText(resultPath, result.ToString()); }
            catch (Exception ex) { Monitor.Log($"写结果失败：{ex.Message}", LogLevel.Error); }
        }
    }
}
