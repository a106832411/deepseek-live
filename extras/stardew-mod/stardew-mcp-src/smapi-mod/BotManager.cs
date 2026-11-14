using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Pathfinding;
using StardewValley.Tools;

namespace StardewMCPBridge
{
    public class BotManager
    {
        private readonly IMonitor monitor;
        private readonly IModHelper helper;
        private readonly Dictionary<string, CompanionAI> companions = new Dictionary<string, CompanionAI>();

        /// <summary>Per-companion last command results, written to bridge data.</summary>
        private readonly Dictionary<string, object> commandResults = new Dictionary<string, object>();
        private int closeMenuInTicks = -1;   // 鲸live：商店界面自动关闭倒计时

        public BotManager(IMonitor monitor, IModHelper helper)
        {
            this.monitor = monitor;
            this.helper = helper;
        }

        public void SpawnBot(string name, string type)
        {
            if (!Context.IsWorldReady) return;
            if (this.companions.ContainsKey(name)) return;

            this.monitor.Log($"Spawning {name} with shadow farmer", LogLevel.Info);

            try
            {
                // Remove any game-auto-created NPCs with this name from ALL locations
                // (Data/Characters injection causes the game to auto-spawn them)
                foreach (var loc in Game1.locations)
                {
                    var dupes = loc.characters.Where(c => c.Name == name).ToList();
                    foreach (var dupe in dupes)
                    {
                        loc.characters.Remove(dupe);
                        this.monitor.Log($"Removed duplicate {name} from {loc.Name}", LogLevel.Debug);
                    }
                }

                var portrait = this.helper.ModContent.Load<Microsoft.Xna.Framework.Graphics.Texture2D>($"assets/{name}_portrait.png");

                var spawnPos = Game1.player.Position;
                if (name == "Companion2") spawnPos += new Vector2(64, 0);
                else if (name == "Companion1") spawnPos += new Vector2(-64, 0);

                var botNpc = new CompanionNPC(
                    new AnimatedSprite($"Characters\\{name}", 0, 16, 32),
                    spawnPos,
                    Game1.player.currentLocation.Name,
                    2, name, portrait, false
                );
                botNpc.displayName = name;
                Game1.player.currentLocation.addCharacter(botNpc);
                this.monitor.Log($"{name} placed at pixel ({spawnPos.X},{spawnPos.Y}), tile ({spawnPos.X / 64f:F1},{spawnPos.Y / 64f:F1})", LogLevel.Info);

                var companionFarmer = new CompanionFarmer(botNpc, name, this.monitor, this.helper);
                var ai = new CompanionAI(companionFarmer, this.monitor);
                ai.Mode = CompanionMode.Follow;

                this.companions.Add(name, ai);
                this.monitor.Log($"{name} spawned with shadow farmer at {Game1.player.currentLocation.Name}", LogLevel.Info);
            }
            catch (Exception ex)
            {
                this.monitor.Log($"Failed to spawn {name}: {ex.Message}\n{ex.StackTrace}", LogLevel.Error);
            }
        }

        public void Update()
        {
            foreach (var kvp in this.companions)
            {
                try
                {
                    kvp.Value.Tick();
                    try { kvp.Value.Companion.TickPendingTool(); } catch { }
                }
                catch (Exception ex)
                {
                    this.monitor.Log($"{kvp.Key}: AI tick error (recovering): {ex.Message}", LogLevel.Error);
                }
            }

            // 鲸live：商店界面停留 1.5 秒后自动关闭，避免游戏卡在菜单
            if (this.closeMenuInTicks > 0)
            {
                this.closeMenuInTicks--;
                if (this.closeMenuInTicks == 0 && Game1.activeClickableMenu is StardewValley.Menus.ShopMenu)
                {
                    Game1.activeClickableMenu = null;
                }
            }
        }

        /// <summary>Get expanded per-companion status for bridge data (includes surroundings in Player mode).</summary>
        // ======================
        // SEED LOOKUP (鲸live 补丁)
        // ======================
        private (Farmer owner, Item seed) FindSeed(string want)
        {
            var owners = new List<Farmer> { Game1.player };
            foreach (var ai in this.companions.Values)
            {
                try { if (ai.Companion?.Shadow != null) owners.Add(ai.Companion.Shadow); } catch { }
            }
            foreach (var owner in owners)
            {
                foreach (var item in owner.Items)
                {
                    if (item is StardewValley.Object obj
                        && (obj.Type == "Seeds" || obj.Category == StardewValley.Object.SeedsCategory)
                        && (string.IsNullOrEmpty(want)
                            || obj.ItemId == want
                            || (obj.Name ?? "").IndexOf(want, StringComparison.OrdinalIgnoreCase) >= 0))
                    {
                        return (owner, item);
                    }
                }
            }
            return (null, null);
        }

        /// <summary>鲸live 补丁：把玩家和同伴背包里能卖的东西直接卖掉，返回 (件数, 金额)。</summary>
        private (int sold, int gold) SellAll(int limit)
        {
            var owners = new List<Farmer> { Game1.player };
            foreach (var bot in this.companions.Values)
            {
                try { if (bot.Companion?.Shadow != null) owners.Add(bot.Companion.Shadow); } catch { }
            }
            int sold = 0, gold = 0;
            foreach (var owner in owners)
            {
                // 背包必须是 36 格定长；被缩短过就先补齐，否则买/种都会失败
                while (owner.Items.Count < 36) owner.Items.Add(null);
                for (int i = 0; i < owner.Items.Count; i++)
                {
                    var item = owner.Items[i];
                    if (item == null || sold >= limit) continue;
                    if (item is Tool || item is MeleeWeapon || item is FishingRod
                        || item is StardewValley.Objects.Hat || item is StardewValley.Objects.Ring) continue;
                    if (!(item is StardewValley.Object obj)) continue;
                    // 只卖作物：蔬菜(-75)/水果(-79)/花(-80)。种子、矿石、宝石、鱼、手工品都不动
                    int cat = obj.Category;
                    if (cat != -75 && cat != -79 && cat != -80) continue;
                    int price;
                    try { price = obj.salePrice(); } catch { continue; }
                    if (price <= 0) continue;
                    int n = obj.Stack;
                    gold += price * n;
                    sold += n;
                    owner.Items[i] = null;                        // 置空而不是 Remove
                }
            }
            if (sold > 0)
            {
                Game1.player.Money += gold;
                this.monitor.Log($"鲸live sell: 卖掉 {sold} 件，得 {gold}g（现有 {Game1.player.Money}g）", LogLevel.Info);
            }
            return (sold, gold);
        }

        public object GetBotStatus()
        {
            var statuses = new List<object>();
            foreach (var kvp in this.companions)
            {
                var ai = kvp.Value;
                var companion = ai.Companion;
                var pos = companion.Visual.Position;
                var tile = companion.Visual.Tile;

                var status = new Dictionary<string, object>
                {
                    ["name"] = kvp.Key,
                    ["position"] = new { x = pos.X, y = pos.Y },
                    ["tile"] = new { x = (int)tile.X, y = (int)tile.Y },
                    ["location"] = companion.Visual.currentLocation?.Name ?? "unknown",
                    ["status"] = ai.GetStatusDescription(),
                    ["mode"] = ai.Mode.ToString().ToLower(),
                    ["stamina"] = companion.GetStaminaPercent(),
                    ["health"] = companion.Shadow.health,
                    ["maxHealth"] = companion.Shadow.maxHealth,
                    ["autoCombat"] = ai.AutoCombat
                };

                // Include surroundings scan in Player mode (the companion's "eyes")
                if (ai.Mode == CompanionMode.Player)
                {
                    var scan = companion.GetSurroundings(8);
                    if (scan != null)
                    {
                        status["surroundings"] = new
                        {
                            tiles = scan.Tiles.Select(t => new
                            {
                                x = t.X, y = t.Y,
                                passable = t.Passable,
                                water = t.IsWater,
                                terrain = t.Terrain,
                                crop = t.CropName,
                                cropReady = t.CropReady,
                                waterState = t.WaterState,
                                obj = t.ObjectName,
                                objType = t.ObjectType,
                                breakable = t.Breakable,
                                interactable = t.Interactable
                            }),
                            monsters = scan.Monsters.Select(m => new
                            {
                                name = m.Name,
                                x = m.X, y = m.Y,
                                health = m.Health,
                                maxHealth = m.MaxHealth
                            }),
                            npcs = scan.Npcs.Select(n => new
                            {
                                name = n.Name,
                                x = n.X, y = n.Y
                            })
                        };
                    }

                    status["inventory"] = companion.GetInventoryData();

                    // Include last command result if any
                    if (this.commandResults.TryGetValue(kvp.Key, out var result))
                        status["lastCommandResult"] = result;
                }

                statuses.Add(status);
            }
            return statuses;
        }

        // ======================
        // SLEEP SIGNAL
        // ======================

        /// <summary>Signal all bot farmers as sleep-ready. Call when host goes to bed.</summary>
        public void SignalAllSleepReady()
        {
            foreach (var kvp in this.companions)
            {
                kvp.Value.Companion.SignalSleepReady();
                this.monitor.Log($"{kvp.Key}: Signaled sleep ready", LogLevel.Debug);
            }
        }

        // ======================
        // DAY TRANSITION
        // ======================

        /// <summary>Restore companion stamina on new day; reset state.</summary>
        public void OnDayStarted()
        {
            foreach (var kvp in this.companions)
            {
                var ai = kvp.Value;
                var shadow = ai.Companion.Shadow;
                shadow.WakeUp();

                // Reset AI state for fresh day (keep Player mode if set)
                if (ai.Mode != CompanionMode.Player)
                    ai.Mode = CompanionMode.Follow;

                // If companion was in a mine/dungeon, warp to farm
                var loc = ai.Companion.Visual.currentLocation;
                if (loc is StardewValley.Locations.MineShaft || loc?.Name == "VolcanoDungeon")
                {
                    ai.Companion.WarpTo("Farm", 64, 15);
                    this.monitor.Log($"{kvp.Key}: Was in {loc.Name} at day end — warped to Farm", LogLevel.Info);
                }

                this.monitor.Log($"{kvp.Key}: New day — stamina restored, mode: {ai.Mode}", LogLevel.Info);
            }
        }

        /// <summary>Clean up companions on return to title.</summary>
        public void Cleanup()
        {
            foreach (var kvp in this.companions)
            {
                var npc = kvp.Value.Companion.Visual;
                npc.currentLocation?.characters.Remove(npc);
            }
            this.companions.Clear();
            this.commandResults.Clear();
            this.monitor.Log("All companions cleaned up", LogLevel.Info);
        }

        // ======================
        // ACTION PROCESSING
        // ======================

        public void ProcessAction(string json)
        {
            using var doc = System.Text.Json.JsonDocument.Parse(json);
            var root = doc.RootElement;

            if (!root.TryGetProperty("actionType", out var actionType)) return;

            string action = actionType.GetString();

            // Check for companion-targeted commands (Player mode direct control)
            if (root.TryGetProperty("companion", out var companionProp))
            {
                string companionName = companionProp.GetString();
                if (this.companions.TryGetValue(companionName, out var targetAi))
                {
                    this.ProcessCompanionCommand(action, root, companionName, targetAi);
                    return;
                }
                else
                {
                    this.monitor.Log($"Unknown companion: {companionName}", LogLevel.Warn);
                    return;
                }
            }

            // Global commands (existing behavior)
            switch (action)
            {
                case "spawn":
                    this.SpawnBot("Companion1", "Guard");
                    this.SpawnBot("Companion2", "Anchor");
                    this.SetAllMode(CompanionMode.Follow);
                    this.monitor.Log("Spawned and following", LogLevel.Info);
                    break;

                case "follow":
                    this.SetAllMode(CompanionMode.Follow);
                    break;

                case "stay":
                    this.SetAllMode(CompanionMode.Idle);
                    break;

                case "farm":
                    this.SetAllMode(CompanionMode.Farm);
                    break;

                case "mine":
                    this.SetAllMode(CompanionMode.Mine);
                    break;

                case "fish":
                    this.SetAllMode(CompanionMode.Fish);
                    break;

                case "water_all":
                    this.WaterAll();
                    break;

                case "harvest_all":
                    this.HarvestAll();
                    break;

                // 鲸live：走进商店买东西（会打开真实的商店界面）
                case "shop_buy":
                {
                    string want = root.TryGetProperty("item", out var itEl) ? itEl.GetString() : "";
                    int count = root.TryGetProperty("count", out var ctEl) ? ctEl.GetInt32() : 1;
                    if (count < 1) count = 1;
                    var stock = StardewValley.Internal.ShopBuilder.GetShopStock("SeedShop");
                    if (stock == null || stock.Count == 0) { this.monitor.Log("shop_buy: 商店没货", LogLevel.Warn); break; }
                    ISalable buyTarget = null;
                    foreach (var key in stock.Keys)
                    {
                        var nm = key?.Name ?? "";
                        var id = (key as Item)?.ItemId ?? "";
                        if (id == want || nm.IndexOf(want, StringComparison.OrdinalIgnoreCase) >= 0)
                        {
                            buyTarget = key;
                            break;
                        }
                    }
                    if (buyTarget == null)
                    {
                        this.monitor.Log($"shop_buy: 商店里没有「{want}」", LogLevel.Warn);
                        break;
                    }
                    var menu = new StardewValley.Menus.ShopMenu("SeedShop", stock, 0, "Pierre", null, null, true);
                    Game1.activeClickableMenu = menu;      // 画面里会出现商店界面
                    bool bought = false;
                    try
                    {
                        var info = stock[buyTarget];
                        int unit = info?.Price ?? 0;
                        int maxBuy = (info == null || info.Stock < 0) ? count : Math.Min(count, info.Stock);
                        int total = unit * maxBuy;
                        if (unit <= 0)
                        {
                            this.monitor.Log($"shop_buy: {want} 价格异常({unit})", LogLevel.Warn);
                        }
                        else if (maxBuy <= 0)
                        {
                            this.monitor.Log($"shop_buy: {want} 没库存了", LogLevel.Warn);
                        }
                        else if (Game1.player.Money < total)
                        {
                            this.monitor.Log($"shop_buy: 钱不够（需要 {total}g，有 {Game1.player.Money}g）", LogLevel.Warn);
                        }
                        else if (buyTarget is Item proto)
                        {
                            var boughtItem = proto.getOne();
                            boughtItem.Stack = maxBuy;
                            if (Game1.player.addItemToInventoryBool(boughtItem, false))
                            {
                                Game1.player.Money -= total;      // 界面开着时扣钱，看得见
                                if (info != null && info.Stock > 0) info.Stock -= maxBuy;
                                stock[buyTarget] = info;
                                bought = true;
                                this.monitor.Log($"shop_buy: 买入 {maxBuy} × {proto.Name}，花 {total}g，余额 {Game1.player.Money}g", LogLevel.Info);
                            }
                            else
                            {
                                this.monitor.Log("shop_buy: 背包放不下", LogLevel.Warn);
                            }
                        }
                    }
                    catch (Exception ex)
                    {
                        this.monitor.Log($"shop_buy 异常：{ex.InnerException?.Message ?? ex.Message}", LogLevel.Error);
                    }
                    this.monitor.Log($"shop_buy: {want} × {count} → {(bought ? "成功" : "失败")}，余额 {Game1.player.Money}g", LogLevel.Info);
                    this.closeMenuInTicks = 90;    // 1.5 秒后自动关掉商店界面，让游戏继续
                    break;
                }

                // 鲸live：把背包里的作物投进出货箱（可见：箱盖打开 + 物品进箱）
                case "ship":
                {
                    var owners = new List<Farmer> { Game1.player };
                    foreach (var bot in this.companions.Values)
                    {
                        try { if (bot.Companion?.Shadow != null) owners.Add(bot.Companion.Shadow); } catch { }
                    }
                    var farm = Game1.getFarm();
                    var bin = farm?.getShippingBin(Game1.player);
                    if (bin == null) { this.monitor.Log("ship: 找不到出货箱", LogLevel.Warn); break; }
                    int moved = 0;
                    foreach (var owner in owners)
                    {
                        for (int i = 0; i < owner.Items.Count; i++)
                        {
                            var item = owner.Items[i];
                            if (!(item is StardewValley.Object obj)) continue;
                            int cat = obj.Category;
                            if (cat != -75 && cat != -79 && cat != -80) continue;   // 只收作物
                            bin.AddRange(new List<Item> { item });
                            owner.Items[i] = null;
                            moved += item.Stack;
                        }
                    }
                    try
                    {
                        var lid = typeof(StardewValley.Farm).GetMethod("openShippingBinLid",
                            System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                        lid?.Invoke(farm, null);
                    }
                    catch { }
                    this.monitor.Log($"ship: 投入 {moved} 个作物", LogLevel.Info);
                    break;
                }

                // 鲸live：睡觉 → 进下一天 + 自动存档
                case "sleep":
                {
                    try
                    {
                        Game1.player.isInBed.Value = true;
                        Game1.NewDay(0.2f);
                        this.monitor.Log("sleep: 进入下一日（含存档）", LogLevel.Info);
                    }
                    catch (Exception ex)
                    {
                        this.monitor.Log($"sleep 失败：{ex.Message}", LogLevel.Error);
                        try { Game1.player.isInBed.Value = false; } catch { }
                    }
                    break;
                }

                // Set individual companion mode
                case "set_mode":
                    if (root.TryGetProperty("target", out var target) &&
                        root.TryGetProperty("mode", out var modeStr))
                    {
                        string targetName = target.GetString();
                        if (this.companions.TryGetValue(targetName, out var ai) &&
                            Enum.TryParse<CompanionMode>(modeStr.GetString(), true, out var mode))
                        {
                            ai.Mode = mode;
                            this.monitor.Log($"Set {targetName} to {mode}", LogLevel.Info);
                        }
                    }
                    break;

                // Warp companions to a specific location
                case "warp":
                    if (root.TryGetProperty("location", out var loc) &&
                        root.TryGetProperty("x", out var wx) &&
                        root.TryGetProperty("y", out var wy))
                    {
                        foreach (var kvp in this.companions)
                            kvp.Value.Companion.WarpTo(loc.GetString(), wx.GetInt32(), wy.GetInt32());
                    }
                    break;

                // Single-tile actions (from stardew_action tool)
                case "water":
                case "harvest":
                case "clear":
                case "hoe":
                    if (root.TryGetProperty("x", out var ax) && root.TryGetProperty("y", out var ay))
                    {
                        // 鲸live 补丁：优先用同伴所在地图（玩家可能还在屋里）
                        GameLocation location = null;
                        if (this.companions.Count > 0)
                        {
                            try { location = this.companions.Values.First().Companion.Visual.currentLocation; } catch { }
                        }
                        if (location == null) location = Game1.player.currentLocation;
                        if (location != null)
                        {
                            var tile = new Vector2(ax.GetInt32(), ay.GetInt32());
                            // 鲸live：用真实工具动画（走到位 → 举工具 → 生效），而不是直接改地块
                            var firstBot = this.companions.Count > 0 ? this.companions.Values.First().Companion : null;
                            bool ok = action switch
                            {
                                "water" => firstBot != null && firstBot.UseToolAt(tile, typeof(WateringCan)),
                                "hoe" => firstBot != null && firstBot.UseToolAt(tile, typeof(Hoe)),
                                "clear" => firstBot != null && firstBot.UseToolAt(tile, typeof(Axe)),
                                "harvest" => CompanionActions.HarvestTile(location, tile, this.monitor),
                                _ => false
                            };
                            this.monitor.Log($"Action {action} at ({ax.GetInt32()},{ay.GetInt32()}) on {location.Name}: {(ok ? "ok" : "failed")}", LogLevel.Info);
                        }
                    }
                    break;

                // 鲸live 补丁：全局播种（同伴/玩家所在地图都行）
                case "plant":
                    if (root.TryGetProperty("x", out var px) && root.TryGetProperty("y", out var py))
                    {
                        GameLocation location = null;
                        if (this.companions.Count > 0)
                        {
                            try { location = this.companions.Values.First().Companion.Visual.currentLocation; } catch { }
                        }
                        if (location == null) location = Game1.player.currentLocation;
                        string want = root.TryGetProperty("seed", out var sp2) ? sp2.GetString() : null;
                        var found = this.FindSeed(want);
                        if (found.seed == null)
                        {
                            this.monitor.Log($"plant: 背包里没有种子 {want}", LogLevel.Warn);
                            break;
                        }
                        var tile = new Vector2(px.GetInt32(), py.GetInt32());
                        bool ok = WhalePlant.PlantTile(location, tile, found.seed, Game1.player, this.monitor);
                        this.monitor.Log($"plant {found.seed.Name} at ({tile.X},{tile.Y}) on {location?.Name}: {(ok ? "success" : "failed")}", LogLevel.Info);
                        if (ok)
                        {
                            found.seed.Stack--;
                            if (found.seed.Stack <= 0)
                            {
                                var idx = found.owner.Items.IndexOf(found.seed);
                                if (idx >= 0) found.owner.Items[idx] = null;
                            }
                        }
                    }
                    break;
            }
        }

        // ======================
        // DIRECT COMPANION COMMANDS (Player mode)
        // ======================

        private void ProcessCompanionCommand(string action, System.Text.Json.JsonElement root, string companionName, CompanionAI ai)
        {
            var companion = ai.Companion;
            bool success = false;
            string detail = "";

            try
            {
                switch (action)
                {
                    // --- Movement ---
                    case "move_to":
                    {
                        int tx = root.GetProperty("x").GetInt32();
                        int ty = root.GetProperty("y").GetInt32();
                        var npc = companion.Visual;
                        var location = npc.currentLocation ?? Game1.player.currentLocation;
                        try
                        {
                            npc.controller = new PathFindController(
                                npc, location, new Microsoft.Xna.Framework.Point(tx, ty), 2);
                            companion.SyncFromNpc();
                            success = true;
                            detail = $"Pathing to ({tx},{ty})";
                        }
                        catch
                        {
                            // Pathfinding failed — teleport
                            npc.Position = new Vector2(tx * 64f, ty * 64f);
                            npc.controller = null;
                            companion.SyncFromNpc();
                            success = true;
                            detail = $"Teleported to ({tx},{ty}) (path failed)";
                        }
                        break;
                    }

                    case "warp_to":
                    {
                        string loc = root.GetProperty("location").GetString();
                        int wx = root.GetProperty("x").GetInt32();
                        int wy = root.GetProperty("y").GetInt32();
                        companion.WarpTo(loc, wx, wy);
                        success = true;
                        detail = $"Warped to {loc} ({wx},{wy})";
                        break;
                    }

                    case "face_direction":
                    {
                        int dir = root.GetProperty("direction").GetInt32(); // 0=up,1=right,2=down,3=left
                        if (dir < 0 || dir > 3)
                        {
                            detail = $"Invalid direction: {dir} (must be 0-3)";
                            break;
                        }
                        companion.Visual.FacingDirection = dir;
                        companion.Shadow.FacingDirection = dir;
                        success = true;
                        detail = $"Facing {new[] { "up", "right", "down", "left" }[dir]}";
                        break;
                    }

                    // --- Tools ---
                    case "use_tool":
                    {
                        string toolName = root.GetProperty("tool").GetString();
                        int tx = root.GetProperty("x").GetInt32();
                        int ty = root.GetProperty("y").GetInt32();
                        var tile = new Vector2(tx, ty);

                        Type toolType = toolName.ToLower() switch
                        {
                            "pickaxe" => typeof(Pickaxe),
                            "axe" => typeof(Axe),
                            "hoe" => typeof(Hoe),
                            "wateringcan" or "watering_can" => typeof(WateringCan),
                            "sword" or "weapon" => typeof(MeleeWeapon),
                            _ => null
                        };

                        if (toolType != null)
                        {
                            success = companion.UseToolAt(tile, toolType);
                            detail = success ? $"Used {toolName} at ({tx},{ty})" : $"Failed to use {toolName}";
                        }
                        else
                        {
                            detail = $"Unknown tool: {toolName}";
                        }
                        break;
                    }

                    case "attack":
                    {
                        success = companion.AttackNearbyMonsters();
                        detail = success ? "Attacked nearby monster" : "No monsters in range";
                        break;
                    }

                    // --- Planting (鲸live 补丁) ---
                    case "plant":
                    {
                        int tx = root.GetProperty("x").GetInt32();
                        int ty = root.GetProperty("y").GetInt32();
                        var tile = new Vector2(tx, ty);
                        var location = companion.Visual.currentLocation ?? Game1.player.currentLocation;
                        string want = root.TryGetProperty("seed", out var sp) ? sp.GetString() : null;
                        var found = this.FindSeed(want);
                        if (found.seed == null)
                        {
                            detail = $"背包里没有种子：{want ?? "(任意)"}";
                            break;
                        }
                        success = WhalePlant.PlantTile(location, tile, found.seed, companion.Shadow, this.monitor);
                        if (success)
                        {
                            detail = $"Planted {found.seed.Name} at ({tx},{ty})";
                            found.seed.Stack--;
                            if (found.seed.Stack <= 0)
                            {
                                found.owner.Items.Remove(found.seed);
                                this.monitor.Log($"{found.seed.Name} 用完，已从 {found.owner.Name} 背包移除", LogLevel.Trace);
                            }
                        }
                        else
                        {
                            detail = $"Plant failed at ({tx},{ty})";
                        }
                        break;
                    }

                    // --- Sell (鲸live 补丁：把背包里的作物/产物直接卖掉换钱) ---
                    case "sell":
                    {
                        int limit = root.TryGetProperty("max", out var mp) ? mp.GetInt32() : 999;
                        var (n, g) = this.SellAll(limit);
                        success = n > 0;
                        detail = n > 0 ? $"卖掉了 {n} 件，得 {g}g（现有 {Game1.player.Money}g）" : "背包里没有可卖的东西";
                        break;
                    }

                    // --- Interact ---
                    case "interact":
                    {
                        int tx = root.GetProperty("x").GetInt32();
                        int ty = root.GetProperty("y").GetInt32();
                        var tile = new Vector2(tx, ty);
                        var location = companion.Visual.currentLocation ?? Game1.player.currentLocation;

                        // Try harvest
                        if (location.terrainFeatures.TryGetValue(tile, out var feature)
                            && feature is StardewValley.TerrainFeatures.HoeDirt dirt
                            && dirt.crop != null && dirt.readyForHarvest())
                        {
                            success = CompanionActions.HarvestTile(location, tile, this.monitor);
                            detail = success ? $"Harvested at ({tx},{ty})" : "Harvest failed";
                        }
                        // Try pick up item
                        else if (location.objects.TryGetValue(tile, out var obj))
                        {
                            if (obj.Name != null && (obj.Name.Contains("Ladder") || obj.Name.Contains("Shaft")))
                            {
                                // Descend mine
                                if (location is StardewValley.Locations.MineShaft shaft)
                                {
                                    int nextLevel = shaft.mineLevel + 1;
                                    string nextName = "UndergroundMine" + nextLevel;
                                    var nextLoc = Game1.getLocationFromName(nextName)
                                        ?? StardewValley.Locations.MineShaft.GetMine(nextName);
                                    if (nextLoc != null)
                                    {
                                        companion.WarpTo(nextLoc.Name, 6, 6);
                                        success = true;
                                        detail = $"Descended to mine level {nextLevel}";
                                    }
                                    else
                                    {
                                        detail = $"Can't find mine level {nextLevel}";
                                    }
                                }
                            }
                            else if (obj is StardewValley.Objects.Chest chest)
                            {
                                // Return chest contents as the command result
                                var items = chest.Items.Where(i => i != null).Select(i => new
                                {
                                    name = i.DisplayName ?? i.Name,
                                    stack = i.Stack,
                                    qualifiedId = i.QualifiedItemId
                                }).ToList();
                                success = true;
                                detail = $"Chest at ({tx},{ty}): {items.Count} items";
                                this.commandResults[companionName] = new { action, success, detail, items };
                                this.monitor.Log($"{companionName}: {detail}", LogLevel.Info);
                                return; // early return — custom result
                            }
                            else
                            {
                                // Try to check/interact with the object using companion's shadow farmer
                                success = obj.checkForAction(companion.Shadow);
                                detail = success ? $"Interacted with {obj.DisplayName}" : $"Can't interact with {obj.DisplayName}";
                            }
                        }
                        else
                        {
                            detail = $"Nothing to interact with at ({tx},{ty})";
                        }
                        break;
                    }

                    // --- Fishing ---
                    case "cast_fishing_rod":
                    {
                        success = ai.StartFishing();
                        detail = success ? "Cast fishing rod" : "Failed to cast";
                        break;
                    }

                    // --- Combat toggle ---
                    case "set_auto_combat":
                    {
                        bool enabled = root.GetProperty("enabled").GetBoolean();
                        ai.AutoCombat = enabled;
                        success = true;
                        detail = $"Auto-combat {(enabled ? "enabled" : "disabled")}";
                        break;
                    }

                    // --- Inventory ---
                    case "eat_item":
                    {
                        int slot = root.TryGetProperty("slot", out var slotProp) ? slotProp.GetInt32() : -1;
                        var items = companion.Shadow.Items;
                        StardewValley.Object food = null;

                        if (slot >= 0 && slot < items.Count && items[slot] is StardewValley.Object o1 && o1.Edibility > 0)
                            food = o1;
                        else
                            food = items.FirstOrDefault(i => i is StardewValley.Object o && o.Edibility > 0) as StardewValley.Object;

                        if (food != null)
                        {
                            try
                            {
                                companion.Shadow.eatObject(food);
                                success = true;
                                detail = $"Ate {food.DisplayName}";
                            }
                            catch (Exception eatEx)
                            {
                                // eatObject may access UI elements on headless farmer
                                // Fall back to direct stamina/health restoration
                                companion.Shadow.Stamina = Math.Min(companion.Shadow.MaxStamina, companion.Shadow.Stamina + food.Edibility);
                                companion.Shadow.health = Math.Min(companion.Shadow.maxHealth, companion.Shadow.health + (int)(food.Edibility * 0.4f));

                                // Consume the food item (eatObject normally handles this)
                                food.Stack--;
                                if (food.Stack <= 0)
                                {
                                    int idx = items.IndexOf(food);
                                    if (idx >= 0) items[idx] = null;
                                }

                                success = true;
                                detail = $"Ate {food.DisplayName} (manual restore, eatObject failed: {eatEx.Message})";
                            }
                        }
                        else
                        {
                            detail = "No edible items in inventory";
                        }
                        break;
                    }

                    default:
                        detail = $"Unknown companion command: {action}";
                        this.monitor.Log($"{companionName}: {detail}", LogLevel.Warn);
                        break;
                }
            }
            catch (Exception ex)
            {
                detail = $"Command error: {ex.Message}";
                this.monitor.Log($"{companionName}: {action} failed: {ex.Message}", LogLevel.Error);
            }

            this.commandResults[companionName] = new { action, success, detail };
            this.monitor.Log($"{companionName}: {action} — {detail}", LogLevel.Info);
        }

        // ======================
        // GLOBAL HELPERS
        // ======================

        private void SetAllMode(CompanionMode mode)
        {
            foreach (var ai in this.companions.Values)
                ai.Mode = mode;
            this.monitor.Log($"All companions set to {mode}", LogLevel.Info);
        }

        private void WaterAll()
        {
            var location = Game1.player.currentLocation;
            if (location == null) return;
            int count = 0;
            foreach (var pair in location.terrainFeatures.Pairs)
            {
                if (pair.Value is StardewValley.TerrainFeatures.HoeDirt dirt
                    && dirt.crop != null && dirt.state.Value != 1)
                {
                    if (CompanionActions.WaterTile(location, pair.Key, this.monitor))
                        count++;
                }
            }
            this.monitor.Log($"Watered {count} tiles", LogLevel.Info);
        }

        private void HarvestAll()
        {
            var location = Game1.player.currentLocation;
            if (location == null) return;
            int count = 0;
            foreach (var pair in location.terrainFeatures.Pairs.ToList())
            {
                if (pair.Value is StardewValley.TerrainFeatures.HoeDirt dirt
                    && dirt.crop != null && dirt.readyForHarvest())
                {
                    if (CompanionActions.HarvestTile(location, pair.Key, this.monitor))
                        count++;
                }
            }
            this.monitor.Log($"Harvested {count} crops", LogLevel.Info);
        }
    }
}
