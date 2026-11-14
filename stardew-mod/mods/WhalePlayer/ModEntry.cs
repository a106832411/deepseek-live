using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Menus;
using StardewValley.Objects;
using StardewValley.TerrainFeatures;
using StardewValley.Tools;
using Microsoft.Xna.Framework;

namespace WhalePlayer
{
    /// <summary>
    /// 鲸live：直接驱动"玩家本人"的可视化操作模组（不涉及同伴）。
    /// 订单：Mods/WhalePlayer/orders/*.json ；状态：Mods/WhalePlayer/player_state.json
    ///
    /// 支持动作：
    ///   walk_to {x,y}                      寻路走过去（可见走路）
    ///   use_tool {tool,x,y}                举工具→动画→生效
    ///   interact {x,y}                     对目标格执行游戏交互（收获/开箱/捡东西）
    ///   shop_buy {item,count}              打开皮埃尔商店界面并购买
    ///   ship                               把背包里的作物放进出货箱
    ///   sleep                              睡觉进入下一天（含存档）
    /// </summary>
    public class ModEntry : Mod
    {
        public static bool ScriptedFishing = false;
        public static Action<string> Trace = null;
        private string ordersDir;
        private string statePath;
        private int toolTicks = -1;
        private string pendingKind = "";
        private Vector2 pendingTile;
        private GameLocation pendingLoc;
        private int closeMenuInTicks = -1;
        private bool gotoShop = false;     // 兼容旧名：跨图状态机
        private System.Collections.IEnumerator saveEnum = null;
        private bool saveRequested = false;
        private bool autoFish = false;
        private bool biteClicked = false;
        private Vector2 fishSpot = new Vector2(-1, -1);
        private int castCooldown = 0;
        private bool fishingPump = false;
        private int biteCountdown = -1;
        private string pendingCatch = null;
        private int castingPending = -1;
        private int hopIndex = 0;
        private Dictionary<string, HashSet<string>> graph = null;
        private string travelDest = null;  // 目标场景（goto 动作）
        private Point hopApproach = Point.Zero;
        private Point hopWarp = Point.Zero;
        private int warpCooldown = 0;
        private readonly Dictionary<string, int> hopAttempts = new Dictionary<string, int>();
        private int shopWaitTicks = 0;

        public override void Entry(IModHelper helper)
        {
            this.ordersDir = Path.Combine(helper.DirectoryPath, "orders");
            this.statePath = Path.Combine(helper.DirectoryPath, "player_state.json");
            Directory.CreateDirectory(this.ordersDir);
            helper.Events.GameLoop.UpdateTicked += this.OnUpdateTicked;
            helper.Events.GameLoop.SaveLoaded += (s, e) => this.Monitor.Log("WhalePlayer: 已进入存档", LogLevel.Info);
            Trace = msg => this.Monitor.Log(msg, LogLevel.Alert);
            try
            {
                var harmony = new HarmonyLib.Harmony("Whale.WhalePlayer.patch");
                harmony.PatchAll();
                this.Monitor.Log("WhalePlayer: Harmony 补丁已应用（钓鱼）", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log($"Harmony 应用失败：{ex.Message}", LogLevel.Error); }
        }

        private void OnUpdateTicked(object sender, UpdateTickedEventArgs e)
        {
            if (!Context.IsWorldReady) return;

            // 钓鱼泵：按原版状态机推进（bite → isNibbling → 提竿 → BobberBar）
            if (this.fishingPump)
            {
                // 小游戏开着时什么都不做，交给 BobberBar 补丁完成
                if (Game1.activeClickableMenu is StardewValley.Menus.BobberBar) return;
                var p0 = Game1.player;
                var rd = p0.Items.OfType<StardewValley.Tools.FishingRod>().FirstOrDefault();
                if (rd == null) { this.fishingPump = false; }
                else if (this.castingPending > 0)
                {
                    this.castingPending--;
                    if (this.castingPending == 0)
                    {
                        try { rd.castingEndFunction(p0); } catch { }
                        if (this.fishSpot.X >= 0) { try { rd.bobber.Value = this.fishSpot; } catch { } }
                        try
                        {
                            rd.isNibbling = false; rd.hit = false; rd.fishingBiteAccumulator = 0f;
                            var t0 = typeof(StardewValley.Tools.FishingRod);
                            var bf0 = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            void ResetBool(string nm)
                            {
                                var f = t0.GetField(nm, bf0);
                                if (f == null) return;
                                var cur = f.GetValue(rd);
                                if (cur == null) { f.SetValue(rd, false); return; }
                                var vp = cur.GetType().GetProperty("Value");
                                if (vp != null && vp.CanWrite) vp.SetValue(cur, false);
                                else f.SetValue(rd, false);
                            }
                            ResetBool("isCasting"); ResetBool("pullingOutOfWater"); ResetBool("fishCaught");
                            ResetBool("isReeling"); ResetBool("showingTreasure"); ResetBool("castedButBobberStillInAir");
                        }
                        catch { }
                        this.Monitor.Log($"钓鱼: 抛竿完成 isFishing={rd.isFishing} bobber=({rd.bobber.X:F0},{rd.bobber.Y:F0}) bite={rd.timeUntilFishingBite:F1}", LogLevel.Info);
                    }
                }
                else if (rd.isFishing && !rd.isNibbling && rd.timeUntilFishingBite > 0f)
                {
                    // 咬钩计时完全交给游戏自己的 tickUpdate，这里不重复累加
                }
                else if (rd.isNibbling)
                {
                    if (this.castCooldown > 0) { this.castCooldown--; return; }
                    this.castCooldown = 40;
                    try
                    {
                        // 补齐原版咬钩分支所需状态
                        var tp = typeof(StardewValley.Tools.FishingRod);
                        var bf = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                        void SetF(string name, object val)
                        {
                            var f = tp.GetField(name, bf);
                            if (f == null) return;
                            var cur = f.GetValue(rd);
                            if (cur == null) { f.SetValue(rd, val); return; }
                            var vp = cur.GetType().GetProperty("Value");
                            if (vp != null && vp.CanWrite) vp.SetValue(cur, val);
                            else f.SetValue(rd, val);
                        }
                        SetF("clearWaterDistance", 3);
                        SetF("pullingOutOfWater", false);
                        SetF("isCasting", false);
                        this.Monitor.Log($"闸门: isCasting={rd.isCasting} pulling={rd.pullingOutOfWater} fishCaught={rd.fishCaught} isFishing={rd.isFishing} nibbling={rd.isNibbling} reeling={rd.isReeling}", LogLevel.Alert);
                        SetF("fishingNibbleAccumulator", 500f);
                        SetF("isNibbling", true);
                        SetF("hit", false);
                        rd.DoFunction(p0.currentLocation, (int)rd.bobber.X, (int)rd.bobber.Y, 1, p0);
                        var menu = Game1.activeClickableMenu;
                        if (menu is StardewValley.Menus.BobberBar)
                        {
                            rd.isNibbling = false;
                            rd.hit = false;
                            this.castCooldown = 600;
                            this.Monitor.Log("钓鱼: 小游戏已开启（原版 BobberBar）", LogLevel.Info);
                        }
                        else this.Monitor.Log($"钓鱼: 提竿 menu={menu?.GetType().Name}", LogLevel.Trace);
                    }
                    catch (Exception ex) { this.Monitor.Log($"提竿异常：{ex.Message}", LogLevel.Warn); }
                }
                else if (!rd.isFishing && !(Game1.activeClickableMenu is StardewValley.Menus.BobberBar))
                {
                    if (this.castCooldown > 0) { this.castCooldown--; return; }
                    this.castCooldown = 60;
                    try { rd.beginUsing(p0.currentLocation, (int)this.fishSpot.X, (int)this.fishSpot.Y, p0); this.castingPending = 45; } catch { }
                }
            }

            // 主动存档（协程逐帧推进）
            if (this.saveRequested && this.saveEnum == null)
            {
                try
                {
                    this.saveEnum = StardewValley.SaveGame.Save();
                    this.Monitor.Log("save: 开始存档", LogLevel.Info);
                }
                catch (Exception ex)
                {
                    this.Monitor.Log($"save 启动失败：{ex.Message}", LogLevel.Error);
                    this.saveRequested = false;
                }
            }
            if (this.saveEnum != null)
            {
                bool more;
                try { more = this.saveEnum.MoveNext(); }
                catch (Exception ex)
                {
                    this.Monitor.Log($"save 异常：{ex.Message}", LogLevel.Error);
                    this.saveEnum = null; this.saveRequested = false; return;
                }
                if (!more)
                {
                    this.saveEnum = null;
                    this.saveRequested = false;
                    this.Monitor.Log("save: 存档完成", LogLevel.Info);
                }
            }

            // 工具动画 → 延迟生效
            if (this.toolTicks > 0)
            {
                this.toolTicks--;
                if (this.toolTicks == 0) this.ApplyPendingTool();
            }

            // 商店界面停留一会儿后自动关闭
            if (this.closeMenuInTicks > 0)
            {
                this.closeMenuInTicks--;
                if (this.closeMenuInTicks == 0 && Game1.activeClickableMenu is ShopMenu)
                {
                    Game1.activeClickableMenu = null;
                }
            }

            // 跨图去商店：每 0.5 秒检查一次，到店/走不动就推进或结束
            if (this.gotoShop && e.IsMultipleOf(30)) this.TickTravel();

            if (e.IsMultipleOf(6)) this.ProcessOrders();     // ~0.1s，动作更跟手
            if (e.IsMultipleOf(15)) this.WriteState();           // ~0.25s，状态刷新更快
        }

        // ---------------- 订单 ----------------
        private void ProcessOrders()
        {
            string[] files;
            try { files = Directory.GetFiles(this.ordersDir, "*.json"); }
            catch { return; }
            if (files.Length == 0) return;
            Array.Sort(files, StringComparer.Ordinal);
            foreach (var file in files)
            {
                string json;
                try { json = File.ReadAllText(file); File.Delete(file); }
                catch { continue; }
                if (string.IsNullOrWhiteSpace(json)) continue;
                try { this.Handle(json); }
                catch (Exception ex) { this.Monitor.Log($"订单处理失败：{ex}", LogLevel.Error); }
            }
        }

        private void Handle(string json)
        {
            var jo = JObject.Parse(json);
            string action = (string)jo["actionType"] ?? (string)jo["type"] ?? "";
            switch (action)
            {
                case "walk_to": this.WalkTo(jo); break;
                case "exit": this.ExitMap(); break;
                case "use_tool": this.UseTool(jo); break;
                case "interact": this.Interact(jo); break;
                case "plant": this.Plant(jo); break;
                case "fish": this.Fish(); break;
                case "goto_shop": this.StartTravel("SeedShop"); break;
                case "goto": this.StartTravel((string)jo["location"] ?? ""); break;
                case "warp_to": this.WarpDirect((string)jo["location"] ?? ""); break;
                case "shop_buy": this.ShopBuy(jo); break;
                case "ship": this.Ship(); break;
                case "sleep": this.Sleep(); break;
                case "save": this.saveRequested = true; break;
                case "upgrade_tools": this.UpgradeTools(); break;
                case "fix_tool_frames": this.FixToolFrames(); break;
                case "rebuild_axe": this.RebuildAxe(); break;
                case "scan_objects": this.ScanObjects(); break;
                case "fish_state": this.FishState(); break;
                case "go_fish": this.GoFish(); break;
                case "tidy": this.Tidy(jo); break;
                case "stash_all": this.StashAll(); break;
                default: this.Monitor.Log($"未知动作：{action}", LogLevel.Warn); break;
            }
        }

        private void WalkTo(JObject jo)
        {
            int x = (int)jo["x"], y = (int)jo["y"];
            var loc = Game1.player.currentLocation;
            var player = Game1.player;
            if (!this.IsSafeTile(loc, x, y))
            {
                this.Monitor.Log($"walk_to: ({x},{y}) 不可通行/超出地图，拒绝（防穿模）", LogLevel.Warn);
                return;
            }
            if (player.Tile.X == x && player.Tile.Y == y)
            {
                this.Monitor.Log($"walk_to: 已经站在 ({x},{y})", LogLevel.Info);
                return;
            }
            try
            {
                player.controller = new StardewValley.Pathfinding.PathFindController(
                    player, loc, new Point(x, y), 3);
                this.Monitor.Log($"walk_to: 开始走向 ({x},{y})", LogLevel.Info);
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"walk_to 失败：{ex.Message}", LogLevel.Warn);
            }
        }

        private void ExitMap()
        {
            var loc = Game1.player.currentLocation;
            if (loc?.warps == null || loc.warps.Count == 0)
            {
                this.Monitor.Log("exit: 这里没有出口", LogLevel.Warn);
                return;
            }
            var p = Game1.player.Tile;
            var warp = loc.warps
                .OrderBy(w => Math.Abs(w.X - p.X) + Math.Abs(w.Y - p.Y))
                .FirstOrDefault();
            if (warp == null) { this.Monitor.Log("exit: 找不到出口", LogLevel.Warn); return; }
            try
            {
                Game1.player.controller = new StardewValley.Pathfinding.PathFindController(
                    Game1.player, loc, new Point(warp.X, warp.Y), 3);
                this.Monitor.Log($"exit: 走向出口 ({warp.X},{warp.Y}) → {warp.TargetName}", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log($"exit 失败：{ex.Message}", LogLevel.Warn); }
        }

        private void Face(Vector2 tile)
        {
            var p = Game1.player.Tile;
            int dx = (int)(tile.X - p.X), dy = (int)(tile.Y - p.Y);
            if (Math.Abs(dx) > Math.Abs(dy))
                Game1.player.FacingDirection = dx > 0 ? 1 : 3;
            else
                Game1.player.FacingDirection = dy > 0 ? 2 : 0;
        }

        private void UseTool(JObject jo)
        {
            string toolName = ((string)jo["tool"] ?? "hoe").ToLower();
            int x = (int)jo["x"], y = (int)jo["y"];
            var tile = new Vector2(x, y);
            var player = Game1.player;

            Type toolType = toolName switch
            {
                "hoe" => typeof(Hoe),
                "wateringcan" or "watering_can" => typeof(WateringCan),
                "axe" => typeof(Axe),
                "pickaxe" => typeof(Pickaxe),
                _ => typeof(Hoe)
            };
            var tool = player.Items.FirstOrDefault(i => i is Tool t && toolType.IsInstanceOfType(t)) as Tool;
            if (tool == null)
            {
                this.Monitor.Log($"use_tool: 没有 {toolType.Name}", LogLevel.Warn);
                return;
            }

            var pTile = player.Tile;
            if (Math.Abs(pTile.X - tile.X) + Math.Abs(pTile.Y - tile.Y) > 2.0f)
            {
                this.Monitor.Log($"use_tool: 离 ({x},{y}) 太远，先 walk_to", LogLevel.Warn);
                return;
            }

            this.Face(tile);
            player.CurrentTool = tool;
            this.pendingKind = tool is Hoe ? "hoe" : (tool is WateringCan ? "water" : "clear");
            this.pendingTile = tile;
            this.pendingLoc = player.currentLocation;
            try
            {
                player.BeginUsingTool();      // 播放挥动动画（+音效）
                this.toolTicks = 20;          // 约 0.33 秒后生效
                this.Monitor.Log($"use_tool: 挥动 {tool.Name} 朝向 ({x},{y})", LogLevel.Trace);
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"use_tool 失败：{ex.Message}", LogLevel.Warn);
                this.toolTicks = -1;
            }
        }

        private void ApplyPendingTool()
        {
            if (this.pendingKind == "fishcast")
            {
                try
                {
                    var rod = Game1.player.CurrentTool as StardewValley.Tools.FishingRod;
                    if (rod != null)
                    {
                        Game1.player.EndUsingTool();
                        this.Monitor.Log($"auto_fish: 抛竿完成 isFishing={rod.isFishing}", LogLevel.Info);
                    }
                }
                catch (Exception ex) { this.Monitor.Log($"auto_fish 抛竿异常：{ex.Message}", LogLevel.Warn); }
                this.pendingKind = "";
                return;
            }
            if (this.pendingKind == "fish")
            {
                try { Game1.player.EndUsingTool(); } catch { }
                try { Game1.player.UsingTool = false; } catch { }
                this.Monitor.Log("fish: 收线", LogLevel.Info);
                this.pendingKind = "";
                return;
            }
            try { Game1.player.EndUsingTool(); } catch { }
            try { Game1.player.UsingTool = false; } catch { }
            this.Monitor.Log($"工具生效：{this.pendingKind} @ ({this.pendingTile.X},{this.pendingTile.Y})", LogLevel.Info);
            this.pendingKind = "";
        }

        private void Plant(JObject jo)
        {
            int x = (int)jo["x"], y = (int)jo["y"];
            string want = (string)jo["seed"] ?? "";
            var player = Game1.player;
            var loc = player.currentLocation;
            var tile = new Vector2(x, y);
            var pTile = player.Tile;
            if (Math.Abs(pTile.X - tile.X) + Math.Abs(pTile.Y - tile.Y) > 2.0f)
            {
                this.Monitor.Log($"plant: 离 ({x},{y}) 太远，先 walk_to", LogLevel.Warn);
                return;
            }
            if (!loc.terrainFeatures.TryGetValue(tile, out var tf) || !(tf is HoeDirt dirt))
            {
                this.Monitor.Log($"plant: ({x},{y}) 不是耕地", LogLevel.Warn);
                return;
            }
            if (dirt.crop != null)
            {
                this.Monitor.Log($"plant: ({x},{y}) 已有作物", LogLevel.Warn);
                return;
            }
            Item seed = null;
            foreach (var it in player.Items)
            {
                if (it is StardewValley.Object o
                    && (o.Type == "Seeds" || o.Category == StardewValley.Object.SeedsCategory)
                    && (string.IsNullOrEmpty(want) || o.ItemId == want
                        || (o.Name ?? "").IndexOf(want, StringComparison.OrdinalIgnoreCase) >= 0
                        || want.IndexOf(o.Name ?? "___", StringComparison.OrdinalIgnoreCase) >= 0))
                {
                    seed = it;
                    break;
                }
            }
            if (seed == null) { this.Monitor.Log($"plant: 背包里没有种子 {want}", LogLevel.Warn); return; }

            this.Face(tile);
            try
            {
                var methods = typeof(HoeDirt).GetMethods().Where(m => m.Name == "plant");
                bool ok = false;
                foreach (var m in methods)
                {
                    var ps = m.GetParameters();
                    var args = new object[ps.Length];
                    bool build = true;
                    for (int i = 0; i < ps.Length; i++)
                    {
                        var pt = ps[i].ParameterType;
                        if (pt == typeof(Item)) args[i] = seed;
                        else if (pt == typeof(Farmer)) args[i] = player;
                        else if (pt == typeof(bool)) args[i] = false;
                        else if (pt == typeof(string)) args[i] = seed.ItemId;
                        else { build = false; break; }
                    }
                    if (!build) continue;
                    if (m.Invoke(dirt, args) is bool b && b) { ok = true; break; }
                }
                if (!ok)
                {
                    dirt.crop = new Crop(seed.ItemId, x, y, loc);
                    ok = dirt.crop != null;
                }
                if (ok)
                {
                    seed.Stack--;
                    if (seed.Stack <= 0)
                    {
                        int idx = player.Items.IndexOf(seed);
                        if (idx >= 0) player.Items[idx] = null;
                    }
                    this.Monitor.Log($"plant: 种下 {seed.Name} @ ({x},{y})", LogLevel.Info);
                }
                else this.Monitor.Log($"plant: 播种失败 @ ({x},{y})", LogLevel.Warn);
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"plant 异常：{ex.Message}", LogLevel.Warn);
            }
        }
        private void Interact(JObject jo)
        {
            int x = (int)jo["x"], y = (int)jo["y"];
            var player = Game1.player;
            var pTile = player.Tile;
            if (Math.Abs(pTile.X - x) + Math.Abs(pTile.Y - y) > 2.0f)
            {
                this.Monitor.Log($"interact: 离 ({x},{y}) 太远，先 walk_to", LogLevel.Warn);
                return;
            }
            this.Face(new Vector2(x, y));
            try
            {
                bool ok = player.currentLocation.checkAction(
                    new xTile.Dimensions.Location(x, y), Game1.viewport, player);
                this.Monitor.Log($"interact ({x},{y})：{(ok ? "成功" : "没有可交互的")}", LogLevel.Info);
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"interact 失败：{ex.Message}", LogLevel.Warn);
            }
        }

        private static readonly Dictionary<string, string[]> MapGraph = new Dictionary<string, string[]>
        {
            { "FarmHouse", new[] { "Farm" } },
            { "Farm", new[] { "FarmHouse", "BusStop", "Backwoods" } },
            { "BusStop", new[] { "Farm", "Town" } },
            { "Backwoods", new[] { "Farm", "Mountain" } },
            { "Town", new[] { "BusStop", "Mountain", "Beach", "SeedShop" } },
            { "Mountain", new[] { "Town", "Backwoods", "Mine" } },
            { "Beach", new[] { "Town" } }
        };

        /// <summary>BFS 找从 from 到 dest 的下一跳。</summary>
        private void BuildGraph()
        {
            graph = new Dictionary<string, HashSet<string>>();
            void AddEdge(string a, string b)
            {
                if (string.IsNullOrEmpty(a) || string.IsNullOrEmpty(b)) return;
                if (!graph.TryGetValue(a, out var set)) { set = new HashSet<string>(); graph[a] = set; }
                set.Add(b);
            }
            try
            {
                foreach (var loc in Game1.locations)
                {
                    if (loc?.warps == null) continue;
                    foreach (var w in loc.warps)
                    {
                        var t = w.TargetName;
                        if (string.IsNullOrEmpty(t)) continue;
                        AddEdge(loc.Name, t);
                        AddEdge(t, loc.Name);
                    }
                }
                // 商店门不在 warps 里，手动补
                AddEdge("Town", "SeedShop");
                AddEdge("SeedShop", "Town");
                this.Monitor.Log($"build_graph: {graph.Count} 个地图、{graph.Values.Sum(x => x.Count)} 条连线", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log($"build_graph 失败：{ex.Message}", LogLevel.Warn); }
        }

        private string DynamicNextHop(string from, string dest)
        {
            if (this.graph == null) this.BuildGraph();
            if (this.graph == null) return null;
            if (!this.graph.ContainsKey(from) || !this.graph.ContainsKey(dest)) return null;
            var prev = new Dictionary<string, string> { [from] = null };
            var q = new Queue<string>();
            q.Enqueue(from);
            while (q.Count > 0)
            {
                var cur = q.Dequeue();
                if (cur == dest)
                {
                    var step = dest;
                    while (prev[step] != from) step = prev[step];
                    return step;
                }
                foreach (var n in this.graph[cur])
                {
                    if (prev.ContainsKey(n)) continue;
                    prev[n] = cur;
                    q.Enqueue(n);
                }
            }
            return null;
        }

        private static string NextHop(string from, string dest)
        {
            if (from == dest) return null;
            var queue = new Queue<string>();
            var prev = new Dictionary<string, string>();
            queue.Enqueue(from);
            prev[from] = null;
            while (queue.Count > 0)
            {
                var cur = queue.Dequeue();
                if (!MapGraph.TryGetValue(cur, out var nbrs)) continue;
                foreach (var n in nbrs)
                {
                    if (prev.ContainsKey(n)) continue;
                    prev[n] = cur;
                    if (n == dest)
                    {
                        var step = dest;
                        while (prev[step] != from) step = prev[step];
                        return step;
                    }
                    queue.Enqueue(n);
                }
            }
            return null;
        }

        /// <summary>直接过图：用目标地图里"返回当前地图"的门作为落点，一次淡入到达。</summary>
        private void WarpDirect(string dest)
        {
            if (string.IsNullOrEmpty(dest)) { this.Monitor.Log("goto: 没给目标", LogLevel.Warn); return; }
            var cur = Game1.player.currentLocation?.Name ?? "";
            if (cur == dest) { this.Monitor.Log($"goto: 已经在 {dest}", LogLevel.Info); return; }
            int x = 10, y = 10;
            try
            {
                var target = Game1.getLocationFromName(dest);
                var back = target?.warps?.FirstOrDefault(w => (w.TargetName ?? "") == cur);
                if (back == null) back = target?.warps?.FirstOrDefault();
                if (back != null)
                {
                    x = back.X;
                    y = Math.Max(1, back.Y - 1);
                }
                Game1.warpFarmer(dest, x, y, false);
                this.Monitor.Log($"goto: {cur} → {dest}（落点 {x},{y}）", LogLevel.Info);
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"goto {dest} 失败：{ex.Message}", LogLevel.Warn);
            }
        }
        private void StartTravel(string dest)
        {
            var cur = Game1.player.currentLocation?.Name ?? "";
            if (string.IsNullOrEmpty(dest)) { this.Monitor.Log("goto: 没给目标", LogLevel.Warn); return; }
            if (cur == dest) { this.Monitor.Log($"goto: 已经在 {dest}", LogLevel.Info); this.gotoShop = false; return; }
            var next = this.DynamicNextHop(cur, dest) ?? NextHop(cur, dest);
            if (next == null)
            {
                this.Monitor.Log($"goto: 不知道怎么从 {cur} 去 {dest}", LogLevel.Warn);
                return;
            }
            this.travelDest = dest;
            this.gotoShop = true;
            this.hopIndex = 0;
            this.hopApproach = Point.Zero;
            this.hopAttempts.Clear();
            this.Monitor.Log($"goto: {cur} → 目标 {dest}（下一跳 {next}）", LogLevel.Info);
            this.TickTravel();
        }

        private void TickTravel()
        {
            if (!this.gotoShop) return;
            var loc = Game1.player.currentLocation;
            var name = loc?.Name ?? "";
            if (!string.IsNullOrEmpty(this.travelDest) && name == this.travelDest)
            {
                this.gotoShop = false;
                this.hopApproach = Point.Zero;
                this.hopAttempts.Clear();
                this.Monitor.Log($"goto: 已到达 {name}", LogLevel.Info);
                return;
            }
            if (this.warpCooldown > 0) { this.warpCooldown--; return; }

            var next = this.DynamicNextHop(name, this.travelDest ?? "") ?? NextHop(name, this.travelDest ?? "");
            if (next == null)
            {
                this.gotoShop = false;
                this.Monitor.Log($"goto: {name} 找不到去 {this.travelDest} 的路", LogLevel.Warn);
                return;
            }
            var pt0 = Game1.player.Tile;
            var cands = loc.warps
                .Where(w => (w.TargetName ?? "") == next)
                .OrderBy(w => Math.Abs(w.X - (int)pt0.X) + Math.Abs(w.Y - (int)pt0.Y))
                .ToList();
            if (cands.Count == 0)
            {
                this.gotoShop = false;
                var names = string.Join("、", loc.warps.Select(w => $"{w.TargetName}({w.X},{w.Y})"));
                this.Monitor.Log($"goto: {name} 没有去 {next} 的传送点；现有：{names}", LogLevel.Warn);
                return;
            }
            if (this.hopIndex >= cands.Count) this.hopIndex = 0;
            var warp = cands[this.hopIndex];

            var pt = Game1.player.Tile;
            int dist = Math.Abs((int)pt.X - warp.X) + Math.Abs((int)pt.Y - warp.Y);

            // 已经在门口：过图（读图淡入，等同玩家穿过门/地图边缘）
            bool edgeWarp = warp.X <= 2 || warp.Y <= 2
                            || warp.X >= loc.Map.Layers[0].LayerWidth - 3
                            || warp.Y >= loc.Map.Layers[0].LayerHeight - 3;
            if (dist <= 1 || (edgeWarp && dist <= 2))
            {
                int tx = warp.TargetX, ty = warp.TargetY;
                try
                {
                    var destLoc = Game1.getLocationFromName(warp.TargetName);
                    if (destLoc?.Map?.Layers != null && destLoc.Map.Layers.Count > 0)
                    {
                        int w = destLoc.Map.Layers[0].LayerWidth, h = destLoc.Map.Layers[0].LayerHeight;
                        tx = Math.Min(Math.Max(tx, 1), w - 2);
                        ty = Math.Min(Math.Max(ty, 1), h - 2);
                    }
                }
                catch { }
                this.warpCooldown = 60;
                this.hopApproach = Point.Zero;
                this.hopAttempts.Clear();
                this.Monitor.Log($"goto: {name} 走到门口，过图 → {warp.TargetName}", LogLevel.Info);
                Game1.warpFarmer(warp.TargetName, tx, ty, false);
                return;
            }

            // 正在走路就等
            if (Game1.player.controller != null) return;

            // 选落脚点：门本身可走就走到门上，否则走到最近的可走邻格
            Point target;
            if (this.IsSafeTile(loc, warp.X, warp.Y)) target = new Point(warp.X, warp.Y);
            else
            {
                var near = new[]
                {
                    new Point(warp.X, warp.Y - 1), new Point(warp.X, warp.Y + 1),
                    new Point(warp.X + 1, warp.Y), new Point(warp.X - 1, warp.Y)
                }.OrderBy(c => Math.Abs(c.X - (int)pt.X) + Math.Abs(c.Y - (int)pt.Y));
                Point pick = Point.Zero;
                foreach (var c in near) if (this.IsSafeTile(loc, c.X, c.Y)) { pick = c; break; }
                target = pick == Point.Zero ? new Point(warp.X, warp.Y) : pick;
            }

            int tries = this.hopAttempts.TryGetValue(name, out var n) ? n : 0;
            tries++;
            this.hopAttempts[name] = tries;
            if (tries > 4)
            {
                this.hopAttempts[name] = 0;
                this.hopIndex = (this.hopIndex + 1) % cands.Count;
                this.Monitor.Log($"goto: {name} 这个门口走不到，换下一个（{this.hopIndex + 1}/{cands.Count}）", LogLevel.Warn);
                return;
            }
            try
            {
                Game1.player.controller = new StardewValley.Pathfinding.PathFindController(
                    Game1.player, loc, target, 3);
                this.Monitor.Log($"goto: {name} 走向门口 ({target.X},{target.Y})，距门 {dist} 格（第 {tries} 次）", LogLevel.Info);
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"goto 寻路失败：{ex.Message}", LogLevel.Warn);
            }
        }

        private bool IsSafeTile(GameLocation loc, int x, int y)
        {
            try
            {
                if (loc?.Map?.Layers == null || loc.Map.Layers.Count == 0) return false;
                if (x < 0 || y < 0) return false;
                if (x >= loc.Map.Layers[0].LayerWidth || y >= loc.Map.Layers[0].LayerHeight) return false;
                return loc.isTilePassable(new xTile.Dimensions.Location(x, y), Game1.viewport);
            }
            catch { return false; }
        }

        private void UpgradeTools()
        {
            int n = 0;
            foreach (var it in Game1.player.Items)
            {
                if (it is Tool t)
                {
                    try { t.UpgradeLevel = 4; n++; }
                    catch { }
                }
            }
            this.Monitor.Log($"upgrade_tools: 升级 {n} 件工具到铱级", LogLevel.Info);
        }

        private void FixToolFrames()
        {
            // 工具每升一级贴图帧 +1（原版 5 档：0基础…4铱）
            var map = new Dictionary<string, int[]>
            {
                { "Axe", new[] { 189, 215 } },
                { "Hoe", new[] { 21, 47 } },
                { "Pickaxe", new[] { 55, 217 } },
                { "Watering Can", new[] { 273, 219 } },
                { "Fishing Rod", new[] { 261, 221 } }
            };
            int n = 0;
            foreach (var it in Game1.player.Items)
            {
                if (!(it is Tool t)) continue;
                if (!map.TryGetValue(t.Name ?? "", out var b)) continue;
                int lvl = t.UpgradeLevel;
                try
                {
                    var tp = typeof(Tool);
                    var f1 = tp.GetField("initialParentTileIndex", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                    var f2 = tp.GetField("currentParentTileIndex", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                    var f3 = tp.GetField("indexOfMenuItemView", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                    void SetField(System.Reflection.FieldInfo f, int v)
                    {
                        if (f == null) return;
                        var cur = f.GetValue(t);
                        if (cur == null) { f.SetValue(t, v); return; }
                        var vp = cur.GetType().GetProperty("Value");
                        if (vp != null && vp.CanWrite) vp.SetValue(cur, v);
                        else f.SetValue(t, v);
                    }
                    SetField(f1, b[0] + lvl);
                    SetField(f2, b[0] + lvl);
                    SetField(f3, b[1] + lvl);
                    n++;
                }
                catch (Exception ex) { this.Monitor.Log($"fix_tool_frames {t.Name}: {ex.Message}", LogLevel.Warn); }
            }
            this.Monitor.Log($"fix_tool_frames: 修正 {n} 件工具贴图", LogLevel.Info);
        }

        private void RebuildAxe()
        {
            var p = Game1.player;
            for (int i = 0; i < p.Items.Count; i++)
                if (p.Items[i] is Axe) p.Items[i] = null;
            var axe = new Axe();
            axe.UpgradeLevel = 4;
            try { axe.ResetParentSheetIndex(); } catch (Exception ex) { this.Monitor.Log($"rebuild_axe reset: {ex.Message}", LogLevel.Warn); }
            if (p.addItemToInventoryBool(axe, false))
                this.Monitor.Log($"rebuild_axe: 新斧头 等级={axe.UpgradeLevel} 帧={axe.InitialParentTileIndex}/{axe.CurrentParentTileIndex} 图标={axe.IndexOfMenuItemView}", LogLevel.Info);
            else
                this.Monitor.Log("rebuild_axe: 背包满", LogLevel.Warn);
        }

        private List<KeyValuePair<Vector2, StardewValley.Objects.Chest>> AllChests(GameLocation loc)
        {
            var list = new List<KeyValuePair<Vector2, StardewValley.Objects.Chest>>();
            foreach (var key in loc.objects.Keys.ToList())
            {
                var c = loc.objects[key] as StardewValley.Objects.Chest;
                if (c != null) list.Add(new KeyValuePair<Vector2, StardewValley.Objects.Chest>(key, c));
            }
            list.Sort((a, b) => a.Key.Y != b.Key.Y ? a.Key.Y.CompareTo(b.Key.Y) : a.Key.X.CompareTo(b.Key.X));
            return list;
        }

        private void ScanObjects()
        {
            var loc = Game1.getFarm();
            var lines = new List<string>();
            foreach (var kvp in this.AllChests(loc))
            {
                string role = "-";
                kvp.Value.modData.TryGetValue("whale.role", out role);
                lines.Add($"({kvp.Key.X},{kvp.Key.Y}) role={role} items={kvp.Value.Items.Count(x => x != null)}");
            }
            foreach (var key in loc.objects.Keys.ToList())
            {
                var o = loc.objects[key];
                if (o != null && (o.Name ?? "").Contains("Furnace")) lines.Add($"FURNACE({key.X},{key.Y})");
            }
            this.Monitor.Log("SCAN " + string.Join(" | ", lines), LogLevel.Alert);
        }

        private bool IsSeedItem(Item it)
        {
            var o = it as StardewValley.Object;
            if (o == null) return false;
            if (o.Type == "Seeds" || o.Category == -74) return true;
            return (it.Name ?? "").Contains("Seed");
        }

        /// <summary>按位置给箱子分配角色（作物1/作物2/矿物/杂物/种子），并把所有物品重新归类。</summary>
        private void Tidy(JObject jo)
        {
            var loc = Game1.getFarm();
            var chests = this.AllChests(loc);
            if (chests.Count < 5) { this.Monitor.Log($"tidy: 只有 {chests.Count} 个箱子（需要 5 个：作物×2/矿物/杂物/种子）", LogLevel.Warn); return; }
            string[] roles = { "crop1", "seeds", "crop2", "extra", "mineral", "misc" };
            if (jo != null && jo["roles"] is JArray ra)
            {
                var custom = new List<string>();
                foreach (var x in ra) custom.Add((string)x);
                if (custom.Count >= chests.Count) roles = custom.ToArray();
            }
            for (int i = 0; i < Math.Min(roles.Length, chests.Count); i++)
            {
                if (string.IsNullOrEmpty(roles[i]) || roles[i] == "extra") continue;
                chests[i].Value.modData["whale.role"] = roles[i];
            }
            for (int i = 5; i < chests.Count; i++)
                this.Monitor.Log($"tidy: 多余箱子 ({chests[i].Key.X},{chests[i].Key.Y})", LogLevel.Warn);

            Chest crop1 = null, crop2 = null, mineral = null, misc = null, seeds = null;
            for (int i = 0; i < Math.Min(roles.Length, chests.Count); i++)
            {
                switch (roles[i])
                {
                    case "crop1": crop1 = chests[i].Value; break;
                    case "crop2": crop2 = chests[i].Value; break;
                    case "mineral": mineral = chests[i].Value; break;
                    case "misc": misc = chests[i].Value; break;
                    case "seeds": seeds = chests[i].Value; break;
                }
            }
            if (crop1 == null || mineral == null || misc == null || seeds == null)
            { this.Monitor.Log("tidy: 角色不全（缺作物/矿物/杂物/种子箱）", LogLevel.Warn); return; }

            var all = new List<Item>();
            foreach (var kvp in chests)
            {
                foreach (var it in kvp.Value.Items) if (it != null) all.Add(it);
                for (int k = 0; k < kvp.Value.Items.Count; k++) kvp.Value.Items[k] = null;
            }
            int placed = 0, failed = 0, seedN = 0;
            foreach (var it in all)
            {
                var o = it as StardewValley.Object;
                int cat = o?.Category ?? 0;
                Chest target;
                if (this.IsSeedItem(it)) { target = seeds; seedN++; }
                else if (cat == -75 || cat == -79 || cat == -80)
                    target = (crop1.Items.Count(x => x != null) >= 36 && crop2 != null) ? crop2 : crop1;
                else if (cat == -2 || cat == -12 || cat == -15 || cat == -4)
                    target = mineral;
                else target = misc;
                if (target.addItem(it) == null) placed++; else failed++;
            }
            string pos = string.Join("、", chests.Take(5).Select(c => $"({c.Key.X},{c.Key.Y})"));
            this.Monitor.Log($"tidy: 箱子 {pos}；种子 {seedN}，归位 {placed}，放不下 {failed}", LogLevel.Info);
        }

        private void StashAll()
        {
            var loc = Game1.getFarm();
            var self = this.FindChestByRole(loc, "misc");
            if (self == null) { this.Monitor.Log("stash_all: 没有杂物箱，先 tidy", LogLevel.Warn); return; }
            var crop1 = this.FindChestByRole(loc, "crop1");
            var mineral = this.FindChestByRole(loc, "mineral");
            var seeds = this.FindChestByRole(loc, "seeds");
            var p = Game1.player;
            int moved = 0, kept = 0, failed = 0;
            for (int i = 0; i < p.Items.Count; i++)
            {
                var it = p.Items[i];
                if (it == null || it is Tool) { if (it is Tool) kept++; continue; }
                var o = it as StardewValley.Object;
                int cat = o?.Category ?? 0;
                Chest target;
                if (this.IsSeedItem(it) && seeds != null) target = seeds;
                else if ((cat == -75 || cat == -79 || cat == -80) && crop1 != null) target = crop1;
                else if ((cat == -2 || cat == -12 || cat == -15 || cat == -4) && mineral != null) target = mineral;
                else target = self;
                if (target.addItem(it) == null) { p.Items[i] = null; moved++; } else failed++;
            }
            this.Monitor.Log($"stash_all: 收进 {moved} 件，工具保留 {kept}，放不下 {failed}", LogLevel.Info);
        }

        private Chest FindChestByRole(GameLocation loc, string roleWanted)
        {
            foreach (var kvp in this.AllChests(loc))
            {
                string role;
                if (kvp.Value.modData.TryGetValue("whale.role", out role) && role == roleWanted) return kvp.Value;
            }
            return null;
        }

        private void GoFish()
        {
            if (Game1.player.currentLocation?.Name == "Beach")
            {
                // 固定钓点：栈桥末端 (25,36)，朝下抛到海面
                this.fishSpot = new Microsoft.Xna.Framework.Vector2(25, 39);
                Game1.player.controller = new StardewValley.Pathfinding.PathFindController(
                    Game1.player, Game1.player.currentLocation, new Point(25, 36), 3);
                this.Monitor.Log("go_fish: 海滩固定钓点，走向栈桥 (25,36)", LogLevel.Info);
                return;
            }
            var loc = Game1.player.currentLocation;
            var p = Game1.player.Tile;
            var best = new Vector2(-1, -1);
            float bestD = 9999f;
            try
            {
                int w = loc.Map.Layers[0].LayerWidth, h = loc.Map.Layers[0].LayerHeight;
                bool IsWater(int x, int y)
                {
                    if (x < 1 || y < 1 || x >= w - 1 || y >= h - 1) return false;
                    return loc.doesTileHaveProperty(x, y, "Water", "Back") != null;
                }
                for (int y = 3; y < h - 3; y++)
                {
                    for (int x = 3; x < w - 3; x++)
                    {
                        if (!IsWater(x, y)) continue;
                        // 要求周围也是水（排除地图边缘/小水洼）
                        int around = 0;
                        for (int dy = -2; dy <= 2; dy++)
                            for (int dx = -2; dx <= 2; dx++)
                                if (IsWater(x + dx, y + dy)) around++;
                        if (around < 16) continue;
                        float d = Math.Abs(x - p.X) + Math.Abs(y - p.Y);
                        if (d < bestD) { bestD = d; best = new Vector2(x, y); }
                    }
                }
            }
            catch (Exception ex) { this.Monitor.Log($"go_fish 扫描失败：{ex.Message}", LogLevel.Warn); }
            if (best.X < 0) { this.Monitor.Log("go_fish: 这张图没有开阔水面", LogLevel.Warn); return; }
            this.fishSpot = best;
            foreach (var d in new[] { new Vector2(0, 1), new Vector2(0, -1), new Vector2(1, 0), new Vector2(-1, 0) })
            {
                var t = best + d;
                if (!this.IsSafeTile(loc, (int)t.X, (int)t.Y)) continue;
                Game1.player.controller = new StardewValley.Pathfinding.PathFindController(Game1.player, loc, new Point((int)t.X, (int)t.Y), 3);
                this.Monitor.Log($"go_fish: 水面 ({best.X},{best.Y}) 开阔度 {bestD:F0}，走向水边 ({t.X},{t.Y})", LogLevel.Info);
                return;
            }
            this.Monitor.Log("go_fish: 水边没有可站的格", LogLevel.Warn);
        }

        private void FishState()
        {
            var p = Game1.player;
            var rod = p.Items.OfType<StardewValley.Tools.FishingRod>().FirstOrDefault();
            if (rod == null) { this.Monitor.Log("fish_state: 没鱼竿", LogLevel.Alert); return; }
            this.Monitor.Log($"FISH current={p.CurrentTool?.Name} isFishing={rod.isFishing} casted={rod.castedButBobberStillInAir} nibble={rod.timeUntilFishingBite:F2} bobber=({rod.bobber.X:F0},{rod.bobber.Y:F0}) using={p.UsingTool} facing={p.FacingDirection} menu={Game1.activeClickableMenu?.GetType().Name}", LogLevel.Alert);
        }

        private void Fish()
        {
            var p = Game1.player;
            var rod = p.Items.OfType<StardewValley.Tools.FishingRod>().FirstOrDefault();
            if (rod == null) { this.Monitor.Log("fish: 没有鱼竿", LogLevel.Warn); return; }
            if (this.fishSpot.X < 0) { this.Monitor.Log("fish: 先 go_fish 找水面", LogLevel.Warn); return; }
            p.CurrentTool = rod;
            this.Face(this.fishSpot);
            try
            {
                bool ok = rod.beginUsing(p.currentLocation, (int)this.fishSpot.X, (int)this.fishSpot.Y, p);
                this.castingPending = 45;   // 等挥杆动画结束
                this.Monitor.Log($"fish: beginUsing={ok}，等挥杆动画…", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log($"fish 异常：{ex.Message}", LogLevel.Warn); }
            this.fishingPump = true;
            this.biteCountdown = 120;
            ScriptedFishing = true;
        }

        private void ShopBuy(JObject jo)
        {
            string want = (string)jo["item"] ?? "";
            int count = (int?)jo["count"] ?? 1;
            if (count < 1) count = 1;

            var locName = Game1.player.currentLocation?.Name ?? "";
            bool remote = (bool?)jo["remote"] ?? false;
            if (locName != "SeedShop" && !remote)
            {
                this.Monitor.Log($"shop_buy: 要在皮埃尔商店里才能买（当前 {locName}），先 goto_shop", LogLevel.Warn);
                return;
            }
            var stock = StardewValley.Internal.ShopBuilder.GetShopStock("SeedShop");
            if (stock == null || stock.Count == 0) { this.Monitor.Log("shop_buy: 商店没货", LogLevel.Warn); return; }

            ISalable target = null;
            foreach (var key in stock.Keys)
            {
                var nm = key?.Name ?? "";
                var id = (key as Item)?.ItemId ?? "";
                if (id == want || nm.IndexOf(want, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    target = key;
                    break;
                }
            }
            if (target == null) { this.Monitor.Log($"shop_buy: 商店里没有「{want}」", LogLevel.Warn); return; }

            var menu = new ShopMenu("SeedShop", stock, 0, "Pierre", null, null, true);
            Game1.activeClickableMenu = menu;                  // 画面里弹出商店
            this.closeMenuInTicks = 90;

            var info = stock[target];
            int unit = info?.Price ?? 0;
            int maxBuy = (info == null || info.Stock < 0) ? count : Math.Min(count, info.Stock);
            int total = unit * maxBuy;
            if (unit <= 0 || maxBuy <= 0) { this.Monitor.Log($"shop_buy: {want} 价格/库存异常", LogLevel.Warn); return; }
            if (Game1.player.Money < total) { this.Monitor.Log($"shop_buy: 钱不够({total}g)", LogLevel.Warn); return; }
            if (!(target is Item proto)) return;

            var item = proto.getOne();
            item.Stack = maxBuy;
            if (!Game1.player.addItemToInventoryBool(item, false))
            {
                this.Monitor.Log("shop_buy: 背包放不下", LogLevel.Warn);
                return;
            }
            Game1.player.Money -= total;
            if (info.Stock > 0) info.Stock -= maxBuy;
            stock[target] = info;
            this.Monitor.Log($"shop_buy: 买入 {maxBuy} × {proto.Name}，花 {total}g，余额 {Game1.player.Money}g", LogLevel.Info);
        }

        private void Ship()
        {
            var farm = Game1.getFarm();
            var bin = farm?.getShippingBin(Game1.player);
            if (bin == null) { this.Monitor.Log("ship: 找不到出货箱", LogLevel.Warn); return; }
            int moved = 0;
            for (int i = 0; i < Game1.player.Items.Count; i++)
            {
                if (!(Game1.player.Items[i] is StardewValley.Object obj)) continue;
                int cat = obj.Category;
                if (cat != -75 && cat != -79 && cat != -80) continue;   // 只收作物
                bin.AddRange(new List<Item> { obj });
                Game1.player.Items[i] = null;
                moved += obj.Stack;
            }
            this.Monitor.Log($"ship: 投入 {moved} 个作物", LogLevel.Info);
        }

        private void Sleep()
        {
            try
            {
                Game1.player.isInBed.Value = true;
                Game1.NewDay(0.2f);
                this.Monitor.Log("sleep: 进入下一天（含存档）", LogLevel.Info);
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"sleep 失败：{ex.Message}", LogLevel.Error);
            }
        }

        // ---------------- 状态导出 ----------------
        private void WriteState()
        {
            try
            {
                var p = Game1.player;
                var loc = p.currentLocation;
                var tiles = new JArray();
                var dryTiles = new JArray();
                var waterTiles = new JArray();
                var origin = p.Tile;
                for (int dy = -8; dy <= 8; dy++)
                {
                    for (int dx = -8; dx <= 8; dx++)
                    {
                        int x = (int)origin.X + dx, y = (int)origin.Y + dy;
                        if (x < 0 || y < 0 || x >= loc.Map.Layers[0].LayerWidth || y >= loc.Map.Layers[0].LayerHeight) continue;
                        var tile = new Vector2(x, y);
                        string crop = null; bool ready = false; int ws = -1;
                        if (loc.terrainFeatures.TryGetValue(tile, out var tf) && tf is HoeDirt dirt)
                        {
                            ws = dirt.state.Value;
                            if (dirt.crop != null)
                            {
                                ready = dirt.readyForHarvest();
                                try { crop = ItemRegistry.GetDataOrErrorItem(dirt.crop.indexOfHarvest.Value).DisplayName; } catch { crop = "crop"; }
                            }
                        }
                        string obj = null; bool breakable = false, interactable = false;
                        if (loc.objects.TryGetValue(tile, out var o) && o != null)
                        {
                            obj = o.DisplayName;
                            breakable = o.Name.Contains("Weed") || o.Name.Contains("Stone") || o.Name.Contains("Twig") || o.Type == "Litter";
                            interactable = o is Chest || o is Furniture;
                        }
                        // 已锄好但没作物的耕地：也要导出，AI 才知道哪里能播种
                        if (crop == null && ws >= 0 && obj == null && !interactable)
                        {
                            if (tiles.Count < 200)
                                tiles.Add(new JObject
                                {
                                    ["x"] = x, ["y"] = y, ["crop"] = null, ["cropReady"] = false,
                                    ["waterState"] = ws, ["obj"] = null,
                                    ["breakable"] = false, ["interactable"] = false, ["tilled"] = true
                                });
                            continue;
                        }
                        bool isWater = false;
                        try { isWater = loc.doesTileHaveProperty(x, y, "Water", "Back") != null; } catch { }
                        if (isWater && waterTiles.Count < 40)
                        {
                            waterTiles.Add(new JObject { ["x"] = x, ["y"] = y });
                            continue;
                        }
                        if (crop == null && obj == null && !interactable)
                        {
                            // 顺便记录"可开垦的空地"，让 AI 知道哪里能锄
                            bool diggable = loc.terrainFeatures.TryGetValue(tile, out var tf2) == false
                                            && loc.doesTileHaveProperty(x, y, "Diggable", "Back") != null;
                            if (diggable && dryTiles.Count < 24)
                                dryTiles.Add(new JObject { ["x"] = x, ["y"] = y });
                            continue;
                        }
                        tiles.Add(new JObject
                        {
                            ["x"] = x, ["y"] = y, ["crop"] = crop, ["cropReady"] = ready,
                            ["waterState"] = ws, ["obj"] = obj,
                            ["breakable"] = breakable, ["interactable"] = interactable
                        });
                    }
                }

                var state = new JObject
                {
                    ["time"] = Game1.timeOfDay,
                    ["day"] = Game1.dayOfMonth,
                    ["season"] = Game1.currentSeason,
                    ["weather"] = Game1.isRaining ? "rain" : (Game1.isSnowing ? "snow" : (Game1.isLightning ? "storm" : "sunny")),
                    ["location"] = loc?.Name,
                    ["player"] = new JObject
                    {
                        ["name"] = p.Name,
                        ["money"] = p.Money,
                        ["stamina"] = p.Stamina,
                        ["health"] = p.health,
                        ["tile"] = new JObject { ["x"] = (int)p.Tile.X, ["y"] = (int)p.Tile.Y },
                        ["inventory"] = new JArray(p.Items.Where(i => i != null).Select(i => (object)i.Name))
                    },
                    ["tiles"] = tiles,
                    ["dry_tiles"] = dryTiles,
                    ["water_tiles"] = waterTiles
                };
                var tmp = this.statePath + ".tmp";
                File.WriteAllText(tmp, state.ToString());
                File.Move(tmp, this.statePath, true);
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"状态写入失败：{ex.Message}", LogLevel.Trace);
            }
        }
    }
}
