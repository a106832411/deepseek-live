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

        /// <summary>自动钓鱼：自动咬钩 + 自动完成小游戏。</summary>
        public bool AutoFishing { get; set; } = false;
        private long lastHookAt = 0;

        public void TickFishingHelp()
        {
            if (!this.AutoFishing) return;
            try
            {
                if (Game1.activeClickableMenu is StardewValley.Menus.BobberBar fishBar)
                {
                    var df = typeof(StardewValley.Menus.BobberBar).GetField("distanceFromCatching",
                        System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                    if (df != null) df.SetValue(fishBar, 1f);
                    var ib = typeof(StardewValley.Menus.BobberBar).GetField("bobberInBar",
                        System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                    if (ib != null) ib.SetValue(fishBar, true);
                }
                if (Game1.player.CurrentTool is StardewValley.Tools.FishingRod rod)
                {
                    // 手动推进鱼竿状态机（玩家不是通过正常输入抛竿，需要手动 tick）
                    try
                    {
                        if (rod.inUse() && Game1.currentGameTime != null)
                            rod.tickUpdate(Game1.currentGameTime, Game1.player);
                    }
                    catch { }
                    long now = Environment.TickCount64;
                    bool minigame = Game1.activeClickableMenu is StardewValley.Menus.BobberBar;
                    if (!minigame && rod.isNibbling && !rod.isReeling && !rod.hit && !rod.pullingOutOfWater && now - this.lastHookAt > 2500)
                    {
                        this.lastHookAt = now;
                        try { rod.beginReeling(); } catch (Exception ex) { this.monitor.Log("beginReeling err: " + ex.Message, LogLevel.Warn); }
                        this.monitor.Log($"Auto-hook fish -> menu={Game1.activeClickableMenu?.GetType().Name}", LogLevel.Info);
                    }
                }

            }
            catch { }
        }

        /// <summary>自动推进对话框 / 跳过剧情，避免 AI 卡在对话上。</summary>
        public bool AutoDismiss { get; set; } = true;

        public void AutoDismissDialogue()
        {
            if (!this.AutoDismiss) return;
            try
            {
                if (Game1.activeClickableMenu is StardewValley.Menus.DialogueBox db)
                {
                    var m = db.GetType().GetMethods(System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic)
                        .FirstOrDefault(mm => mm.Name.Equals("receiveLeftClick", StringComparison.OrdinalIgnoreCase)
                            && mm.GetParameters().Length == 3
                            && mm.GetParameters()[0].ParameterType == typeof(int)
                            && mm.GetParameters()[1].ParameterType == typeof(int));
                    if (m == null) return;
                    // 问答式对话：点第一个选项按钮
                    if (db.isQuestion && (bool)(db.GetType().GetField("_showedOptions",
                            System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic)?.GetValue(db) ?? false))
                    {
                        var ccField = db.GetType().GetField("responseCC",
                            System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                        if (ccField?.GetValue(db) is System.Collections.IEnumerable ccs)
                        {
                            foreach (var cc in ccs)
                            {
                                var bounds = cc.GetType().GetProperty("bounds")?.GetValue(cc);
                                if (bounds is Microsoft.Xna.Framework.Rectangle r)
                                {
                                    m.Invoke(db, new object[] { r.Center.X, r.Center.Y, true });
                                    return;
                                }
                            }
                        }
                    }
                    m.Invoke(db, new object[] { Game1.viewport.Width / 2, Game1.viewport.Height / 2, true });
                    return;
                }
                if (Game1.dialogueUp && Game1.activeClickableMenu == null)
                {
                    Game1.dialogueUp = false;
                }
                var ev = Game1.currentLocation?.currentEvent;
                if (ev != null)
                {
                    try { ev.skipEvent(); } catch { }
                    this.monitor.Log("Auto-skip event", LogLevel.Debug);
                }
            }
            catch { }
        }

        private readonly System.Collections.Generic.Dictionary<string, (string loc, int x, int y)> savedPlacements = new System.Collections.Generic.Dictionary<string, (string loc, int x, int y)>();

        /// <summary>存档前把同伴从地图上移除，避免 XmlSerializer 序列化自定义 NPC 失败。</summary>
        public void RemoveCompanionsForSave()
        {
            this.savedPlacements.Clear();
            foreach (var kv in this.companions)
            {
                var npc = kv.Value.Companion.Visual;
                var loc = npc.currentLocation;
                if (loc == null) continue;
                this.savedPlacements[kv.Key] = (loc.Name, (int)npc.Tile.X, (int)npc.Tile.Y);
                loc.characters.Remove(npc);
            }
            if (this.savedPlacements.Count > 0)
                this.monitor.Log($"Companions removed for save: {this.savedPlacements.Count}", LogLevel.Info);
        }

        /// <summary>存档完成后把同伴放回原位置。</summary>
        public void RestoreCompanionsAfterSave()
        {
            foreach (var kv in this.companions)
            {
                if (!this.savedPlacements.TryGetValue(kv.Key, out var p)) continue;
                var npc = kv.Value.Companion.Visual;
                var loc = Game1.getLocationFromName(p.loc);
                if (loc == null) continue;
                if (!loc.characters.Contains(npc)) loc.characters.Add(npc);
                npc.currentLocation = loc;
                npc.Position = new Microsoft.Xna.Framework.Vector2(p.x * 64f, p.y * 64f);
                npc.controller = null;
            }
            this.savedPlacements.Clear();
        }

        private bool autoNoPause;
        private int pendingNudgeX;
        private int pendingNudgeY;
        private int pendingTargetX = -1;
        private int pendingTargetY = -1;

        public void TickNudge()
        {
            {
                try
                {
                    Game1.options.pauseWhenOutOfFocus = false;
                    var pf = typeof(Game1).GetField("paused",
                        System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                    if (pf != null && pf.FieldType == typeof(bool) && (bool)pf.GetValue(null)) pf.SetValue(null, false);
                }
                catch { }
            }
            if (this.pendingNudgeX == 0 && this.pendingNudgeY == 0) return;
            try
            {
                Game1.player.controller = null;
                Game1.player.Position = new Microsoft.Xna.Framework.Vector2(
                    Game1.player.Position.X + this.pendingNudgeX * 48f,
                    Game1.player.Position.Y + this.pendingNudgeY * 48f);
                this.monitor.Log($"auto-nudge ({this.pendingNudgeX},{this.pendingNudgeY})", LogLevel.Info);
                this.pendingNudgeX = 0;
                this.pendingNudgeY = 0;
                this.pendingTargetX = -1;
                this.pendingTargetY = -1;
            }
            catch { }
        }

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
                }
                catch (Exception ex)
                {
                    this.monitor.Log($"{kvp.Key}: AI tick error (recovering): {ex.Message}", LogLevel.Error);
                }
            }
        }

        /// <summary>Get expanded per-companion status for bridge data (includes surroundings in Player mode).</summary>
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

                // ---------------- 主角控制（player） ----------------
                case "player_move_to":
                    if (root.TryGetProperty("x", out var pmx) && root.TryGetProperty("y", out var pmy))
                    {
                        var ploc = Game1.player.currentLocation;
                        try
                        {
                            Game1.player.controller = new PathFindController(Game1.player, ploc, new Microsoft.Xna.Framework.Point(pmx.GetInt32(), pmy.GetInt32()), 1000);
                            this.monitor.Log($"Player pathing to ({pmx.GetInt32()},{pmy.GetInt32()})", LogLevel.Info);
                        }
                        catch (Exception ex)
                        {
                            Game1.player.Position = new Microsoft.Xna.Framework.Vector2(pmx.GetInt32() * 64f, pmy.GetInt32() * 64f);
                            Game1.player.controller = null;
                            this.monitor.Log($"Player teleported ({pmx.GetInt32()},{pmy.GetInt32()}): {ex.Message}", LogLevel.Warn);
                        }
                    }
                    break;

                case "upgrade_tool":
                    {
                        try
                        {
                            string tv = root.TryGetProperty("tool", out var tvEl) ? tvEl.GetString() : "pickaxe";
                            var theTool = Game1.player.Items.OfType<StardewValley.Tool>().FirstOrDefault(t =>
                            {
                                var n = t.GetType().Name.ToLower();
                                return tv switch
                                {
                                    "pickaxe" => n.Contains("pickaxe"),
                                    "axe" => n.Contains("axe"),
                                    "hoe" => n.Contains("hoe"),
                                    "wateringcan" or "watering_can" => n.Contains("wateringcan"),
                                    _ => n.Contains(tv.ToLower())
                                };
                            });
                            if (theTool == null) { this.monitor.Log("upgrade_tool: no tool", LogLevel.Warn); break; }
                            int lvl = theTool.UpgradeLevel;
                            if (lvl >= 4) { this.monitor.Log("upgrade_tool: already max", LogLevel.Warn); break; }
                            int bars = 5;
                            var barItem = Game1.player.Items.FirstOrDefault(i => i != null && i.QualifiedItemId == "(O)334");
                            int haveBars = 0;
                            foreach (var it in Game1.player.Items)
                                if (it != null && it.QualifiedItemId == "(O)334") haveBars += it.Stack;
                            if (haveBars < bars || Game1.player.Money < 2000)
                            {
                                this.monitor.Log($"upgrade_tool: not enough materials bars={haveBars} money={Game1.player.Money}", LogLevel.Warn);
                                break;
                            }
                            Game1.player.Money -= 2000;
                            int need = bars;
                            for (int i = 0; i < Game1.player.Items.Count && need > 0; i++)
                            {
                                var it = Game1.player.Items[i];
                                if (it == null || it.QualifiedItemId != "(O)334") continue;
                                int take = Math.Min(need, it.Stack);
                                it.Stack -= take;
                                need -= take;
                                if (it.Stack <= 0) Game1.player.Items[i] = null;
                            }
                            theTool.UpgradeLevel = lvl + 1;
                            this.monitor.Log($"upgrade_tool: {theTool.GetType().Name} {lvl} -> {theTool.UpgradeLevel}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("upgrade_tool err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "set_clock":
                    {
                        try
                        {
                            if (root.TryGetProperty("time", out var tEl))
                            {
                                Game1.timeOfDay = tEl.GetInt32();
                                this.monitor.Log($"clock set to {Game1.timeOfDay}", LogLevel.Info);
                            }
                        }
                        catch (Exception ex) { this.monitor.Log("set_clock err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "reset_state":
                    {
                        try
                        {
                            Game1.eventUp = false;
                            Game1.fadeToBlack = false;
                            Game1.fadeIn = false;
                            Game1.paused = false;
                            Game1.dialogueUp = false;
                            Game1.activeClickableMenu = null;
                            if (Game1.currentLocation != null) Game1.currentLocation.currentEvent = null;
                            if (Game1.player != null) { Game1.player.controller = null; Game1.player.Halt(); Game1.player.UsingTool = false; }
                            this.monitor.Log("reset_state done", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("reset_state err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "list_doors":
                    {
                        try
                        {
                            var dl = Game1.player.currentLocation;
                            int w = dl.Map.Layers[0].LayerWidth, h = dl.Map.Layers[0].LayerHeight;
                            var parts = new System.Collections.Generic.List<string>();
                            for (int dy = 0; dy < h; dy++)
                                for (int dx = 0; dx < w; dx++)
                                {
                                    var act = dl.doesTileHaveProperty(dx, dy, "Action", "Buildings");
                                    if (!string.IsNullOrEmpty(act) && act.ToLower().Contains("warp"))
                                        parts.Add($"{dx},{dy}={act}");
                                }
                            this.monitor.Log($"DOORS {dl.Name}: " + string.Join(" | ", parts), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("list_doors err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "enter_location":
                    {
                        try
                        {
                            string doorTarget = root.TryGetProperty("name", out var tg) ? tg.GetString() : "";
                            var el = Game1.player.currentLocation;
                            int ew = el.Map.Layers[0].LayerWidth, eh = el.Map.Layers[0].LayerHeight;
                            int foundX = -1, foundY = -1;
                            string foundAct = null;
                            for (int yy = 0; yy < eh && foundX < 0; yy++)
                                for (int xx = 0; xx < ew; xx++)
                                {
                                    var act = el.doesTileHaveProperty(xx, yy, "Action", "Buildings");
                                    if (string.IsNullOrEmpty(act)) continue;
                                    if (act.IndexOf(doorTarget, StringComparison.OrdinalIgnoreCase) >= 0 && act.ToLower().Contains("warp"))
                                    { foundX = xx; foundY = yy; foundAct = act; break; }
                                }
                            if (foundX < 0)
                            {
                                // 退而求其次：直接找该 location 实例并 warp
                                var locInst = Game1.getLocationFromName(doorTarget);
                                if (locInst != null)
                                {
                                    Game1.warpFarmer(locInst.Name, 8, 10, false);
                                    this.monitor.Log($"enter_location: no door, direct warp to {doorTarget}", LogLevel.Info);
                                }
                                else this.monitor.Log($"enter_location: door/location not found for {doorTarget}", LogLevel.Warn);
                                break;
                            }
                            Game1.player.Position = new Microsoft.Xna.Framework.Vector2(foundX * 64f, foundY * 64f);
                            el.checkAction(new xTile.Dimensions.Location(foundX, foundY), Game1.viewport, Game1.player);
                            this.monitor.Log($"enter_location: stepped on door ({foundX},{foundY}) act={foundAct}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("enter_location err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "find_action":
                    {
                        try
                        {
                            string needle = root.TryGetProperty("needle", out var nd) ? nd.GetString() : "Mine";
                            var fa = Game1.player.currentLocation;
                            int fw = fa.Map.Layers[0].LayerWidth, fh = fa.Map.Layers[0].LayerHeight;
                            var found = new System.Collections.Generic.List<string>();
                            for (int yy = 0; yy < fh; yy++)
                                for (int xx = 0; xx < fw; xx++)
                                {
                                    var act = fa.doesTileHaveProperty(xx, yy, "Action", "Buildings");
                                    if (!string.IsNullOrEmpty(act) && act.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0)
                                        found.Add($"{xx},{yy}={act}");
                                }
                            this.monitor.Log($"FINDACTION {fa.Name}: " + string.Join(" | ", found), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("find_action err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "set_tile":
                    {
                        try
                        {
                            int sx2 = root.GetProperty("x").GetInt32();
                            int sy2 = root.GetProperty("y").GetInt32();
                            Game1.player.Position = new Microsoft.Xna.Framework.Vector2(sx2 * 64f, sy2 * 64f);
                            this.monitor.Log($"set_tile ({sx2},{sy2})", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("set_tile err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "nudge":
                    {
                        try
                        {
                            int ndx = root.TryGetProperty("dx", out var nx) ? nx.GetInt32() : 0;
                            int ndy = root.TryGetProperty("dy", out var ny) ? ny.GetInt32() : 0;
                            Game1.player.Position = new Microsoft.Xna.Framework.Vector2(
                                Game1.player.Position.X + ndx * 64f, Game1.player.Position.Y + ndy * 64f);
                            this.monitor.Log($"nudge ({ndx},{ndy})", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("nudge err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "reset_rod":
                    {
                        try
                        {
                            var rods = Game1.player.Items.OfType<StardewValley.Tools.FishingRod>().ToList();
                            foreach (var r in rods)
                            {
                                foreach (var name in new[] { "inUse", "isNibbling", "hit", "doneFishing" })
                                {
                                    var f = typeof(StardewValley.Tools.FishingRod).GetField(name,
                                        System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                                    if (f != null && f.FieldType == typeof(bool)) f.SetValue(r, false);
                                }
                                foreach (var name in new[] { "castingPower", "castTimer" })
                                {
                                    var f = typeof(StardewValley.Tools.FishingRod).GetField(name,
                                        System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                                    if (f != null && f.FieldType == typeof(float)) f.SetValue(r, 0f);
                                }
                            }
                            Game1.player.UsingTool = false;
                            try { Game1.player.completelyStopAnimatingOrDoingAction(); } catch { }
                            try { Game1.player.Halt(); } catch { }
                            this.monitor.Log($"reset_rod done (rods={rods.Count})", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("reset_rod err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "enter_exit":
                    {
                        try
                        {
                            string et = root.TryGetProperty("target", out var etEl) ? etEl.GetString() : "";
                            var eloc = Game1.player.currentLocation;
                            StardewValley.Warp chosen = null;
                            foreach (var w in eloc.warps)
                                if (w.TargetName != null && w.TargetName.IndexOf(et, StringComparison.OrdinalIgnoreCase) >= 0) { chosen = w; break; }
                            if (chosen == null) { this.monitor.Log($"enter_exit: no warp to {et}", LogLevel.Warn); break; }
                            // 走到出口后再触发换图（等价于踩上 warp 格）
                            Game1.warpFarmer(chosen.TargetName, chosen.TargetX, chosen.TargetY, false);
                            this.monitor.Log($"enter_exit {eloc.Name} -> {chosen.TargetName} ({chosen.TargetX},{chosen.TargetY})", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("enter_exit err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "walk_to_target":
                    {
                        try
                        {
                            string wt = root.TryGetProperty("target", out var wtEl) ? wtEl.GetString() : "";
                            var wloc = Game1.player.currentLocation;
                            int wx2 = -1, wy2 = -1;
                            foreach (var w in wloc.warps)
                            {
                                if (w.TargetName != null && w.TargetName.IndexOf(wt, StringComparison.OrdinalIgnoreCase) >= 0)
                                { wx2 = w.X; wy2 = w.Y; break; }
                            }
                            if (wx2 < 0)
                            {
                                int ew2 = wloc.Map.Layers[0].LayerWidth, eh2 = wloc.Map.Layers[0].LayerHeight;
                                for (int yy = 0; yy < eh2 && wx2 < 0; yy++)
                                    for (int xx = 0; xx < ew2; xx++)
                                    {
                                        var act = wloc.doesTileHaveProperty(xx, yy, "Action", "Buildings");
                                        if (string.IsNullOrEmpty(act)) continue;
                                        if (act.ToLower().Contains("warp") && act.IndexOf(wt, StringComparison.OrdinalIgnoreCase) >= 0)
                                        { wx2 = xx; wy2 = yy; break; }
                                    }
                            }
                            if (wx2 < 0) { this.monitor.Log($"walk_to_target: no exit to {wt} in {wloc.Name}", LogLevel.Warn); break; }
                            int mapW = wloc.Map.Layers[0].LayerWidth, mapH = wloc.Map.Layers[0].LayerHeight;
                            int pathX = wx2, pathY = wy2;
                            this.pendingNudgeX = 0; this.pendingNudgeY = 0;
                            // 只有真正在地图外的出口（负坐标）才用边界外推；房间门口格（y==mapH 之类）保持原样寻路
                            if (wx2 < 0) { pathX = 0; }
                            if (wy2 < 0) { pathY = 0; }
                            Game1.player.controller = new PathFindController(Game1.player, wloc,
                                new Microsoft.Xna.Framework.Point(pathX, pathY), 1000);
                            this.monitor.Log($"Walking to exit {wt} at ({wx2},{wy2}) from {wloc.Name}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("walk_to_target err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "player_warp":
                    if (root.TryGetProperty("location", out var pwloc) && root.TryGetProperty("x", out var pwx) && root.TryGetProperty("y", out var pwy))
                    {
                        Game1.warpFarmer(pwloc.GetString(), pwx.GetInt32(), pwy.GetInt32(), false);
                        this.monitor.Log($"Player warped to {pwloc.GetString()} ({pwx.GetInt32()},{pwy.GetInt32()})", LogLevel.Info);
                    }
                    break;

                case "player_face":
                    if (root.TryGetProperty("direction", out var pfd))
                        Game1.player.faceDirection(pfd.GetInt32());
                    break;

                case "player_use_tool":
                    if (root.TryGetProperty("tool", out var put) && root.TryGetProperty("x", out var pux) && root.TryGetProperty("y", out var puy))
                    {
                        var want = put.GetString().ToLower();
                        var tool = Game1.player.Items.OfType<StardewValley.Tool>().FirstOrDefault(t =>
                        {
                            var n = t.GetType().Name.ToLower();
                            return want switch
                            {
                                "pickaxe" => n.Contains("pickaxe"),
                                "axe" => n.Contains("axe"),
                                "hoe" => n.Contains("hoe"),
                                "wateringcan" or "watering_can" => n.Contains("wateringcan"),
                                "sword" or "weapon" => n.Contains("weapon"),
                                _ => n == want
                            };
                        });
                        if (tool != null)
                        {
                            int idx = Game1.player.Items.IndexOf(tool);
                            if (idx >= 0) Game1.player.CurrentToolIndex = idx;
                            tool.DoFunction(Game1.player.currentLocation, pux.GetInt32() * 64, puy.GetInt32() * 64, tool.UpgradeLevel + 1, Game1.player);
                            this.monitor.Log($"Player used {want} at ({pux.GetInt32()},{puy.GetInt32()})", LogLevel.Info);
                        }
                        else this.monitor.Log($"Player tool not found: {want}", LogLevel.Warn);
                    }
                    break;

                case "player_save":
                    try
                    {
                        StardewValley.SaveGame.Save();
                        this.monitor.Log("Player save requested", LogLevel.Info);
                    }
                    catch (Exception ex) { this.monitor.Log($"Player save failed: {ex.Message}", LogLevel.Error); }
                    break;

                case "player_sleep":
                    {
                        // 不在农舍就先把玩家送到床边
                        if (!(Game1.player.currentLocation is StardewValley.Locations.FarmHouse))
                        {
                            try
                            {
                                var house = Game1.getLocationFromName("FarmHouse") as StardewValley.Locations.FarmHouse;
                                if (house != null)
                                {
                                    var bedSpot = house.GetPlayerBedSpot();
                                    Game1.warpFarmer("FarmHouse", bedSpot.X, bedSpot.Y, false);
                                    this.monitor.Log($"Warped player to bed ({bedSpot.X},{bedSpot.Y})", LogLevel.Info);
                                }
                                else this.monitor.Log("FarmHouse not found", LogLevel.Warn);
                            }
                            catch (Exception ex) { this.monitor.Log("warp to bed failed: " + ex.Message, LogLevel.Warn); }
                            break;
                        }
                        var sleepLoc = Game1.player.currentLocation;
                        bool invoked = false;
                        foreach (var name in new[] { "startSleep", "doSleep", "sleep" })
                        {
                            var ms = new System.Collections.Generic.List<System.Reflection.MethodInfo>();
                            for (var tt = sleepLoc.GetType(); tt != null && tt != typeof(object); tt = tt.BaseType)
                                ms.AddRange(tt.GetMethods(System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic)
                                    .Where(mm => mm.Name == name && !ms.Contains(mm)));
                            foreach (var m in ms)
                            {
                                try
                                {
                                    var args = m.GetParameters().Select(pp =>
                                        pp.ParameterType == typeof(Farmer) ? (object)Game1.player :
                                        pp.ParameterType == typeof(bool) ? (object)false :
                                        pp.ParameterType == typeof(int) ? (object)0 :
                                        pp.ParameterType == typeof(string) ? (object)"" :
                                        null).ToArray();
                                    var r = m.Invoke(sleepLoc, args);
                                    invoked = true;
                                    this.monitor.Log($"sleep {name}({string.Join(",", m.GetParameters().Select(pp => pp.ParameterType.Name))}) -> {r}", LogLevel.Info);
                                }
                                catch (Exception ex) { this.monitor.Log($"sleep {name} err: {ex.InnerException?.Message ?? ex.Message}", LogLevel.Warn); }
                            }
                        }
                        if (!invoked) this.monitor.Log("no sleep method invoked", LogLevel.Warn);
                        break;
                    }

                // ---------------- 主角种田/经济（完整流程） ----------------
                case "player_till_all":
                    this.PlayerTillAll();
                    break;

                case "player_plant":
                    this.PlayerPlant();
                    break;

                case "player_water_all":
                    this.WaterAll();
                    break;

                case "player_harvest_all":
                    this.HarvestAll();
                    break;

                case "player_sell_instant":
                    {
                        int sold = 0; int total = 0;
                        foreach (var item in Game1.player.Items.ToList())
                        {
                            if (!(item is StardewValley.Object o) || o.Stack <= 0) continue;
                            if (o.Category == -74) continue;   // 不卖种子
                            int price = 0;
                            try { price = (int)o.sellToStorePrice(); } catch { }
                            if (price <= 0) continue;
                            total += price * o.Stack;
                            sold += o.Stack;
                            Game1.player.removeItemFromInventory(o);
                        }
                        if (total > 0) Game1.player.Money += total;
                        this.monitor.Log($"Sold {sold} items for {total}g", LogLevel.Info);
                        break;
                    }

                case "player_sell_all":
                    this.PlayerSellAll();
                    break;

                case "player_buy_seed":
                    if (root.TryGetProperty("seed", out var bsSeed))
                    {
                        int cnt = root.TryGetProperty("count", out var bsCnt) ? bsCnt.GetInt32() : 10;
                        this.PlayerBuySeed(bsSeed.GetString(), cnt);
                    }
                    break;

                case "player_walk_to":
                    if (root.TryGetProperty("x", out var wtx) && root.TryGetProperty("y", out var wty))
                    {
                        var walkLoc = Game1.player.currentLocation;
                        try
                        {
                            Game1.player.controller = new PathFindController(Game1.player, walkLoc,
                                new Microsoft.Xna.Framework.Point(wtx.GetInt32(), wty.GetInt32()), 1000);
                            this.monitor.Log($"Walking to ({wtx.GetInt32()},{wty.GetInt32()})", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("walk failed: " + ex.Message, LogLevel.Warn); }
                    }
                    break;

                case "player_interact_at":
                    if (root.TryGetProperty("x", out var itx) && root.TryGetProperty("y", out var ity))
                    {
                        // 原版交互：相当于玩家走到该格并点击（对话/开店/出货箱/床/收获）
                        var ok = Game1.player.currentLocation.checkAction(
                            new xTile.Dimensions.Location(itx.GetInt32(), ity.GetInt32()), Game1.viewport, Game1.player);
                        this.monitor.Log($"Interact ({itx.GetInt32()},{ity.GetInt32()}) -> {ok}", LogLevel.Info);
                    }
                    break;

                case "player_sleep_bed":
                    {
                        // 走路到床边，再调用游戏自己的睡觉流程
                        var house = Game1.getLocationFromName("FarmHouse") as StardewValley.Locations.FarmHouse;
                        if (house == null) { this.monitor.Log("no farmhouse", LogLevel.Warn); break; }
                        var bed = house.GetPlayerBedSpot();
                        if (Game1.player.currentLocation != house)
                        {
                            Game1.warpFarmer("FarmHouse", bed.X, bed.Y, false);
                            this.monitor.Log("Entered FarmHouse at bed", LogLevel.Info);
                            break;
                        }
                        // 找床边可站立格（床本身不可走）
                        int standX = bed.X, standY = bed.Y + 1;
                        try
                        {
                            if (!house.isTilePassable(new xTile.Dimensions.Location(bed.X, bed.Y + 1), Game1.viewport))
                            {
                                if (house.isTilePassable(new xTile.Dimensions.Location(bed.X, bed.Y - 1), Game1.viewport)) { standY = bed.Y - 1; }
                                else if (house.isTilePassable(new xTile.Dimensions.Location(bed.X - 1, bed.Y), Game1.viewport)) { standX = bed.X - 1; standY = bed.Y; }
                                else if (house.isTilePassable(new xTile.Dimensions.Location(bed.X + 1, bed.Y), Game1.viewport)) { standX = bed.X + 1; standY = bed.Y; }
                            }
                        }
                        catch { }
                        if (Game1.player.Tile.X != standX || Game1.player.Tile.Y != standY)
                        {
                            Game1.player.controller = new PathFindController(Game1.player, house,
                                new Microsoft.Xna.Framework.Point(standX, standY), 1000);
                            this.monitor.Log($"Walking to bedside ({standX},{standY}), from ({Game1.player.Tile.X},{Game1.player.Tile.Y})", LogLevel.Info);
                            break;
                        }
                        var ms = typeof(StardewValley.GameLocation).GetMethods(System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic)
                            .Where(mm => mm.Name == "startSleep").ToList();
                        foreach (var m in ms)
                        {
                            try
                            {
                                var args = m.GetParameters().Select(pp =>
                                    pp.ParameterType == typeof(Farmer) ? (object)Game1.player :
                                    pp.ParameterType == typeof(bool) ? (object)false :
                                    pp.ParameterType == typeof(int) ? (object)0 : null).ToArray();
                                m.Invoke(house, args);
                                this.monitor.Log($"Bed sleep invoked ({m.GetParameters().Length} params)", LogLevel.Info);
                            }
                            catch (Exception ex) { this.monitor.Log("bed sleep err: " + (ex.InnerException?.Message ?? ex.Message), LogLevel.Warn); }
                        }
                        break;
                    }

                case "festival_info":
                    {
                        try
                        {
                            string fest = Game1.whereIsTodaysFest ?? "";
                            this.monitor.Log($"Festival check: isFestival={Game1.isFestival()}, today={(fest == "" ? "none" : fest)}, weather={(Game1.isRaining ? "rain" : Game1.isSnowing ? "snow" : Game1.isLightning ? "storm" : "sunny")}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("festival check failed: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "craft_item":
                    {
                        try
                        {
                            string want = root.TryGetProperty("item", out var ci) ? ci.GetString() : "Torch";
                            string recipeKey = null;
                            var aliasMap = new System.Collections.Generic.Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
                            {
                                { "火把", "Torch" }, { "木箱", "Chest" }, { "篱笆", "Fence" }, { "稻草人", "Scarecrow" },
                                { "洒水器", "Sprinkler" }, { "肥料", "Basic Fertilizer" }, { "熔炉", "Furnace" },
                                { "木桶", "Keg" }, { "蜂箱", "Bee House" }, { "蛋黄酱机", "Mayonnaise Machine" },
                                { "工作台", "Workbench" }, { "楼梯", "Staircase" }
                            };
                            string wantKey = want;
                            if (aliasMap.TryGetValue(want, out var mapped)) wantKey = mapped;
                            foreach (var key in Game1.player.craftingRecipes.Keys)
                            {
                                if (key.IndexOf(wantKey, StringComparison.OrdinalIgnoreCase) >= 0 || key.IndexOf(want, StringComparison.OrdinalIgnoreCase) >= 0)
                                {
                                    recipeKey = key;
                                    break;
                                }
                            }
                            if (recipeKey == null) { this.monitor.Log($"Recipe not found: {want}", LogLevel.Warn); break; }
                            var recipe = new StardewValley.CraftingRecipe(recipeKey, false);
                            var haveM = typeof(StardewValley.CraftingRecipe).GetMethod("doesFarmerHaveIngredientsInInventory",
                                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                            bool can = true;
                            if (haveM != null)
                            {
                                var args = haveM.GetParameters().Select(pp => pp.ParameterType == typeof(Farmer) ? (object)Game1.player : null).ToArray();
                                var r = haveM.Invoke(recipe, args);
                                can = r is bool b && b;
                            }
                            if (!can) { this.monitor.Log($"Missing ingredients for {recipeKey}", LogLevel.Warn); break; }
                            var createM = typeof(StardewValley.CraftingRecipe).GetMethod("createItem",
                                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                            var consumeM = typeof(StardewValley.CraftingRecipe).GetMethod("consumeIngredients",
                                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                            int made = 0;
                            for (int n = 0; n < 1; n++)
                            {
                                if (createM != null)
                                {
                                    var args2 = createM.GetParameters().Select(pp =>
                                        pp.ParameterType == typeof(Farmer) ? (object)Game1.player :
                                        pp.ParameterType == typeof(bool) ? (object)false : null).ToArray();
                                    var item = createM.Invoke(recipe, args2) as StardewValley.Item;
                                    if (item != null) { Game1.player.addItemToInventory(item); made++; }
                                }
                                if (consumeM != null)
                                {
                                    var args3 = consumeM.GetParameters().Select(pp =>
                                        pp.ParameterType == typeof(Farmer) ? (object)Game1.player :
                                        pp.ParameterType == typeof(bool) ? (object)false : null).ToArray();
                                    consumeM.Invoke(recipe, args3);
                                }
                            }
                            this.monitor.Log($"Crafted {made}x {recipeKey}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("craft failed: " + (ex.InnerException?.Message ?? ex.Message), LogLevel.Warn); }
                        break;
                    }

                case "talk_to_npc":
                    {
                        try
                        {
                            string npcName = root.TryGetProperty("npc", out var nn) ? nn.GetString() : "";
                            var npcLoc = Game1.player.currentLocation;
                            var npc = npcLoc?.characters.FirstOrDefault(c => c.Name == npcName);
                            if (npc == null)
                            {
                                // 全地图找 NPC（不在当前场景就去他/她所在场景）
                                foreach (var l in Game1.locations)
                                {
                                    var found = l.characters.FirstOrDefault(c => c.Name == npcName);
                                    if (found != null) { npc = found; npcLoc = l; break; }
                                }
                                if (npc == null) { this.monitor.Log($"NPC not found: {npcName}", LogLevel.Warn); break; }
                                Game1.warpFarmer(npcLoc.Name, (int)npc.Tile.X, (int)npc.Tile.Y + 1, false);
                                this.monitor.Log($"Warped to {npcLoc.Name} for {npcName}", LogLevel.Info);
                            }
                            // 站到 NPC 身边再交互（真实距离）
                            Game1.player.Position = new Microsoft.Xna.Framework.Vector2(npc.Tile.X * 64f, (npc.Tile.Y + 1) * 64f);
                            Game1.player.faceDirection(0);
                            npc.checkAction(Game1.player, npcLoc);
                            int pts = 0;
                            if (Game1.player.friendshipData.ContainsKey(npc.Name)) pts = Game1.player.friendshipData[npc.Name].Points;
                            this.monitor.Log($"Talked to {npcName}, friendship={pts}, dialogue={Game1.dialogueUp}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("talk failed: " + (ex.InnerException?.Message ?? ex.Message), LogLevel.Warn); }
                        break;
                    }

                case "open_shop_menu":
                    {
                        try
                        {
                            string shopId = root.TryGetProperty("shop", out var sid) ? sid.GetString() : "SeedShop";
                            string owner = root.TryGetProperty("owner", out var ow) ? ow.GetString() : null;
                            var m = typeof(Utility).GetMethod("TryOpenShopMenu",
                                System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic,
                                null, new[] { typeof(string), typeof(string), typeof(bool) }, null);
                            if (m == null) { this.monitor.Log("TryOpenShopMenu not found", LogLevel.Warn); break; }
                            var res = m.Invoke(null, new object[] { shopId, owner, false });
                            this.monitor.Log($"TryOpenShopMenu({shopId}) -> {res}, menu={Game1.activeClickableMenu?.GetType().Name}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("open_shop_menu failed: " + (ex.InnerException?.Message ?? ex.Message), LogLevel.Warn); }
                        break;
                    }

                case "open_shop":
                    {
                        // 传送到 SeedShop，找 Pierre 交互开店（真实原版开店）
                        try
                        {
                            var shopLoc = Game1.getLocationFromName("SeedShop");
                            if (shopLoc == null) { this.monitor.Log("SeedShop not found", LogLevel.Warn); break; }
                            if (Game1.player.currentLocation != shopLoc)
                            {
                                Game1.warpFarmer("SeedShop", 12, 14, false);
                                this.monitor.Log("Warped into SeedShop", LogLevel.Info);
                            }
                            var pierre = shopLoc.characters.FirstOrDefault(c => c.Name == "Pierre");
                            if (pierre == null) { this.monitor.Log("Pierre not found", LogLevel.Warn); break; }
                            Game1.player.Position = new Microsoft.Xna.Framework.Vector2((pierre.Tile.X) * 64f, (pierre.Tile.Y + 1) * 64f);
                            pierre.checkAction(Game1.player, shopLoc);
                            this.monitor.Log($"Pierre checkAction -> menu={Game1.activeClickableMenu?.GetType().Name}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("open shop failed: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "player_select_tool":
                    if (root.TryGetProperty("tool", out var stsel))
                    {
                        string stw = stsel.GetString().ToLower();
                        var t = Game1.player.Items.OfType<StardewValley.Tool>().FirstOrDefault(tt =>
                        {
                            var n = tt.GetType().Name.ToLower();
                            return stw switch
                            {
                                "pickaxe" => n.Contains("pickaxe"),
                                "axe" => n.Contains("axe"),
                                "hoe" => n.Contains("hoe"),
                                "wateringcan" or "watering_can" => n.Contains("wateringcan"),
                                "sword" or "weapon" => n.Contains("weapon"),
                                _ => n == stw
                            };
                        });
                        if (t != null)
                        {
                            Game1.player.CurrentToolIndex = Game1.player.Items.IndexOf(t);
                            this.monitor.Log($"Selected tool {t.GetType().Name}", LogLevel.Info);
                        }
                        else this.monitor.Log($"tool not found: {stw}", LogLevel.Warn);
                    }
                    break;

                case "player_face_tile":
                    if (root.TryGetProperty("x", out var ftx) && root.TryGetProperty("y", out var fty))
                    {
                        var ft = Game1.player.Tile;
                        int dx = ftx.GetInt32() - (int)ft.X, dy = fty.GetInt32() - (int)ft.Y;
                        int dir = Math.Abs(dx) > Math.Abs(dy) ? (dx > 0 ? 1 : 3) : (dy > 0 ? 2 : 0);
                        Game1.player.faceDirection(dir);
                        this.monitor.Log($"Facing {dir}", LogLevel.Info);
                    }
                    break;

                case "player_mine_at":
                    {
                        if (root.TryGetProperty("x", out var mx) && root.TryGetProperty("y", out var my))
                        {
                            int tx = mx.GetInt32(), ty = my.GetInt32();
                            var mineLoc = Game1.player.currentLocation;
                            int[][] dirs = { new[]{0,-1,2}, new[]{1,0,1}, new[]{0,1,0}, new[]{-1,0,3} };
                            bool placed = false;
                            foreach (var d in dirs)
                            {
                                int sx = tx + d[0], sy = ty + d[1];
                                if (!mineLoc.isTilePassable(new xTile.Dimensions.Location(sx, sy), Game1.viewport)) continue;
                                Game1.player.Position = new Microsoft.Xna.Framework.Vector2(sx * 64f, sy * 64f);
                                Game1.player.faceDirection(d[2]);
                                placed = true;
                                break;
                            }
                            if (!placed) { this.monitor.Log("no stand tile", LogLevel.Warn); break; }
                            var tool = Game1.player.Items.OfType<StardewValley.Tools.Pickaxe>().FirstOrDefault();
                            if (tool != null)
                            {
                                Game1.player.CurrentToolIndex = Game1.player.Items.IndexOf(tool);
                                tool.DoFunction(mineLoc, tx * 64, ty * 64, tool.UpgradeLevel + 1, Game1.player);
                                this.monitor.Log($"Mined at ({tx},{ty})", LogLevel.Info);
                            }
                            else this.monitor.Log("no pickaxe", LogLevel.Warn);
                        }
                        break;
                    }

                case "machine_drop_in":
                    {
                        try
                        {
                            int mx = root.GetProperty("x").GetInt32();
                            int my = root.GetProperty("y").GetInt32();
                            string itemName = root.TryGetProperty("item", out var itEl) ? itEl.GetString() : "";
                            var loc2 = Game1.player.currentLocation;
                            var tv = new Microsoft.Xna.Framework.Vector2(mx, my);
                            if (!loc2.Objects.TryGetValue(tv, out var mobj) || mobj == null) { this.monitor.Log("machine_drop_in: no object", LogLevel.Warn); break; }
                            var invItem = Game1.player.Items.FirstOrDefault(i => i != null && (i.DisplayName == itemName || i.Name == itemName));
                            if (invItem == null) { this.monitor.Log($"machine_drop_in: no item {itemName}", LogLevel.Warn); break; }
                            bool res = mobj.performObjectDropInAction(invItem, false, Game1.player);
                            this.monitor.Log($"machine_drop_in {mobj.DisplayName} <- {itemName} -> {res}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("machine_drop_in err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "machine_collect":
                    {
                        try
                        {
                            int mx = root.GetProperty("x").GetInt32();
                            int my = root.GetProperty("y").GetInt32();
                            var loc3 = Game1.player.currentLocation;
                            var tv2 = new Microsoft.Xna.Framework.Vector2(mx, my);
                            if (loc3.Objects.TryGetValue(tv2, out var mobj2) && mobj2 != null)
                            {
                                bool ok2 = mobj2.checkForAction(Game1.player);
                                this.monitor.Log($"machine_collect {mobj2.DisplayName} -> {ok2}", LogLevel.Info);
                            }
                        }
                        catch (Exception ex) { this.monitor.Log("machine_collect err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "learn_recipe":
                    {
                        string rn = root.TryGetProperty("name", out var rnEl) ? rnEl.GetString() : "";
                        if (string.IsNullOrEmpty(rn)) break;
                        bool ok = Game1.player.craftingRecipes.ContainsKey(rn);
                        if (!ok)
                        {
                            try { var recipe = new StardewValley.CraftingRecipe(rn, false); Game1.player.craftingRecipes[rn] = 0; ok = true; }
                            catch (Exception ex) { this.monitor.Log("recipe err: " + ex.Message, LogLevel.Warn); }
                        }
                        this.monitor.Log($"learn_recipe {rn} -> {ok}", LogLevel.Info);
                        break;
                    }

                case "place_item_here":
                    {
                        string pn = root.TryGetProperty("name", out var pnEl) ? pnEl.GetString() : "";
                        var pit = Game1.player.Items.FirstOrDefault(i => i != null && (i.DisplayName == pn || i.Name == pn || i.QualifiedItemId == pn));
                        if (pit == null) { this.monitor.Log($"place_item: no item {pn}", LogLevel.Warn); break; }
                        int dir = Game1.player.FacingDirection;
                        var ptile = Game1.player.Tile;
                        int ox = 0, oy = 0;
                        if (dir == 0) oy = -1; else if (dir == 1) ox = 1; else if (dir == 2) oy = 1; else ox = -1;
                        var ptgt = new Microsoft.Xna.Framework.Vector2((ptile.X + ox) * 64f, (ptile.Y + oy) * 64f);
                        var plac = Game1.player.currentLocation;
                        bool placed = false;
                        try { placed = (pit as StardewValley.Object)?.placementAction(plac, (int)ptgt.X, (int)ptgt.Y, Game1.player) ?? false; } catch (Exception ex) { this.monitor.Log("place err: " + ex.Message, LogLevel.Warn); }
                        if (placed) { Game1.player.removeItemFromInventory(pit); }
                        this.monitor.Log($"place_item {pn} placed={placed} at {(ptile.X + ox)},{(ptile.Y + oy)}", LogLevel.Info);
                        break;
                    }

                case "list_warps":
                    {
                        try
                        {
                            var warpLoc = Game1.player.currentLocation;
                            var parts = new System.Collections.Generic.List<string>();
                            foreach (var w in warpLoc.warps)
                                parts.Add($"{w.X},{w.Y}->{w.TargetName}:{w.TargetX},{w.TargetY}");
                            this.monitor.Log($"WARPS {warpLoc.Name}: " + string.Join(" | ", parts), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("list warps failed: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "player_wake":
                    {
                        try
                        {
                            var fType = typeof(Farmer);
                            foreach (var name in new[] { "isInBed", "sleeping", "inBed", "sleepingInBed" })
                            {
                                var f = fType.GetField(name, System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                                if (f != null && f.FieldType == typeof(bool)) { f.SetValue(Game1.player, false); this.monitor.Log($"Woke: {name}=false", LogLevel.Info); }
                            }
                            Game1.player.controller = null;
                            Game1.player.Halt();
                        }
                        catch (Exception ex) { this.monitor.Log("wake failed: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "close_menu":
                    {
                        try
                        {
                            var m = Game1.activeClickableMenu?.GetType().GetMethod("exitThisMenu",
                                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                            if (m != null) m.Invoke(Game1.activeClickableMenu, new object[] { false });
                            else Game1.exitActiveMenu();
                            this.monitor.Log($"Closed menu -> {Game1.activeClickableMenu?.GetType().Name ?? "none"}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("close menu failed: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "shop_stock":
                    {
                        try
                        {
                            string shopId = root.TryGetProperty("shop", out var sid3) ? sid3.GetString() : "SeedShop";
                            var om = typeof(Utility).GetMethod("TryOpenShopMenu",
                                System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic,
                                null, new[] { typeof(string), typeof(string), typeof(bool) }, null);
                            if (om != null) om.Invoke(null, new object[] { shopId, null, false });
                            var shop = Game1.activeClickableMenu as StardewValley.Menus.ShopMenu;
                            if (shop == null) { this.monitor.Log($"shop_stock: cannot open {shopId}", LogLevel.Warn); break; }
                            var pf = typeof(StardewValley.Menus.ShopMenu).GetField("itemPriceAndStock",
                                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                            var dict = pf?.GetValue(shop) as System.Collections.IDictionary;
                            var names = new System.Collections.Generic.List<string>();
                            if (dict != null)
                                foreach (System.Collections.DictionaryEntry e in dict)
                                {
                                    var it = e.Key;
                                    var dn = it?.GetType().GetProperty("DisplayName")?.GetValue(it)?.ToString() ?? "?";
                                    var arr = e.Value as int[];
                                    names.Add($"{dn}({arr?[0]})");
                                    if (names.Count > 40) break;
                                }
                            this.monitor.Log($"STOCK {shopId}: " + string.Join(", ", names), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("shop_stock failed: " + (ex.InnerException?.Message ?? ex.Message), LogLevel.Warn); }
                        break;
                    }

                case "shop_buy":
                    {
                        try
                        {
                            string reqShop = root.TryGetProperty("shop", out var rs) ? rs.GetString() : "SeedShop";
                            var shop = Game1.activeClickableMenu as StardewValley.Menus.ShopMenu;
                            if (shop != null && shop.ShopId != reqShop)
                            {
                                try { shop.exitThisMenu(false); } catch { }
                                Game1.exitActiveMenu();
                                shop = null;
                            }
                            if (shop == null)
                            {
                                // 同一 tick 内自己开店再买（菜单打开时外部 action 会被跳过）
                                string shopId = root.TryGetProperty("shop", out var sid2) ? sid2.GetString() : "SeedShop";
                                var om = typeof(Utility).GetMethod("TryOpenShopMenu",
                                    System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic,
                                    null, new[] { typeof(string), typeof(string), typeof(bool) }, null);
                                if (om != null) om.Invoke(null, new object[] { shopId, null, false });
                                shop = Game1.activeClickableMenu as StardewValley.Menus.ShopMenu;
                                this.monitor.Log($"Auto-opened shop {shopId} -> {shop != null}", LogLevel.Info);
                            }
                            if (shop == null) { this.monitor.Log("Cannot open shop", LogLevel.Warn); break; }
                            var forSaleField = typeof(StardewValley.Menus.ShopMenu).GetField("forSale",
                                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                            // 商店货架：itemPriceAndStock 是 Dictionary<ISalable,int[]>（[价格,库存]）
                            object rawDict = null;
                            var pf = typeof(StardewValley.Menus.ShopMenu).GetField("itemPriceAndStock",
                                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                            if (pf != null) rawDict = pf.GetValue(shop);
                            if (rawDict == null) rawDict = forSaleField?.GetValue(shop);
                            var dict = rawDict as System.Collections.IDictionary;
                            var list = rawDict as System.Collections.IEnumerable;
                            this.monitor.Log($"shop stock: {(dict != null ? "dict" : (list != null ? "list" : "null"))}", LogLevel.Info);
                            if (dict == null && list == null) { this.monitor.Log("shop stock unavailable", LogLevel.Warn); break; }
                            string want = root.TryGetProperty("item", out var bi) ? bi.GetString() : "seed";
                            int count = root.TryGetProperty("count", out var bc) ? bc.GetInt32() : 1;
                            object foundItem = null; int price = 0;
                            if (dict != null)
                            {
                                foreach (System.Collections.DictionaryEntry e in dict)
                                {
                                    var it = e.Key;
                                    if (it == null) continue;
                                    var dn = it.GetType().GetProperty("DisplayName")?.GetValue(it)?.ToString() ?? "";
                                    string alias = want;
                                    if (want.Equals("Wheat", StringComparison.OrdinalIgnoreCase)) alias = "小麦";
                                    if (want.Equals("Blueberry", StringComparison.OrdinalIgnoreCase)) alias = "蓝莓";
                                    if (want.Equals("Melon", StringComparison.OrdinalIgnoreCase)) alias = "甜瓜";
                                    if (want.Equals("Pepper", StringComparison.OrdinalIgnoreCase)) alias = "辣椒";
                                    if (want.Equals("Tomato", StringComparison.OrdinalIgnoreCase)) alias = "番茄";
                                    if (want.Equals("Corn", StringComparison.OrdinalIgnoreCase)) alias = "玉米";
                                    if (dn.IndexOf(want, StringComparison.OrdinalIgnoreCase) >= 0 || dn.IndexOf(alias, StringComparison.OrdinalIgnoreCase) >= 0)
                                    {
                                        foundItem = it;
                                        var arr = e.Value as int[];
                                        if (arr != null && arr.Length > 0) price = arr[0];
                                        break;
                                    }
                                }
                            }
                            else
                            {
                                foreach (var it in (System.Collections.IEnumerable)list)
                                {
                                    if (it == null) continue;
                                    var dn = it.GetType().GetProperty("DisplayName")?.GetValue(it)?.ToString() ?? "";
                                    if (dn.IndexOf(want, StringComparison.OrdinalIgnoreCase) >= 0) { foundItem = it; price = 10; break; }
                                }
                            }
                            if (foundItem == null) { this.monitor.Log($"shop item not found: {want}", LogLevel.Warn); break; }
                            // 用商店库存价格真实扣钱 + 发货（等同于点“购买”）
                            var itemObj = foundItem as StardewValley.Item;
                            if (itemObj == null) { this.monitor.Log("Shop item type unsupported", LogLevel.Warn); break; }
                            int qty = Math.Max(1, count);
                            int unit = price > 0 ? price : 10;
                            int total = unit * qty;
                            if (Game1.player.Money < total) { qty = Math.Max(1, Game1.player.Money / unit); total = unit * qty; }
                            if (qty <= 0 || Game1.player.Money < total) { this.monitor.Log("Not enough money for shop purchase", LogLevel.Warn); break; }
                            Game1.player.Money -= total;
                            var bought = itemObj.getOne();
                            bought.Stack = qty;
                            Game1.player.addItemToInventory(bought);
                            this.monitor.Log($"Shop bought {qty}x {want} for {total}g (unit {unit}) money={Game1.player.Money}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("shop buy failed: " + (ex.InnerException?.Message ?? ex.Message), LogLevel.Warn); }
                        break;
                    }

                case "enter_mine":
                    {
                        int level = root.TryGetProperty("level", out var lv) ? lv.GetInt32() : 1;
                        try
                        {
                            Game1.enterMine(level);
                            this.monitor.Log($"Entered mine level {level}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("enter mine failed: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "stand_by_water":
                    {
                        // 站到水边、面朝水、手持鱼竿（不抛竿，交给真实点击）
                        try
                        {
                            var rod = Game1.player.Items.OfType<StardewValley.Tools.FishingRod>().FirstOrDefault();
                            if (rod == null) { rod = new StardewValley.Tools.FishingRod(); Game1.player.Items.Add(rod); }
                            Game1.player.CurrentToolIndex = Game1.player.Items.IndexOf(rod);
                            var waterLoc = Game1.player.currentLocation;
                            var myTile = Game1.player.Tile;
                            int bx = -1, by = -1; float best = float.MaxValue;
                            for (int dx = -14; dx <= 14; dx++)
                            for (int dy = -14; dy <= 14; dy++)
                            {
                                int x = (int)myTile.X + dx, y = (int)myTile.Y + dy;
                                if (waterLoc.isWaterTile(x, y))
                                {
                                    float d = Math.Abs(dx) + Math.Abs(dy);
                                    if (d < best) { best = d; bx = x; by = y; }
                                }
                            }
                            if (bx < 0) { this.monitor.Log("no water", LogLevel.Warn); break; }
                            // 找岸边陆地
                            bool moved = false;
                            for (int r = 1; r <= 15 && !moved; r++)
                                for (int dx = -r; dx <= r && !moved; dx++)
                                    for (int dy = -r; dy <= r && !moved; dy++)
                                    {
                                        if (Math.Abs(dx) != r && Math.Abs(dy) != r) continue;
                                        int lx = bx + dx, ly = by + dy;
                                        if (waterLoc.isWaterTile(lx, ly)) continue;
                                        if (!waterLoc.isTilePassable(new xTile.Dimensions.Location(lx, ly), Game1.viewport)) continue;
                                        Game1.player.Position = new Microsoft.Xna.Framework.Vector2(lx * 64f, ly * 64f);
                                        myTile = new Microsoft.Xna.Framework.Vector2(lx, ly);
                                        moved = true;
                                    }
                            int facing = 2; int bestStep = 99;
                            int[] ddx = { 0, 1, 0, -1 }, ddy = { -1, 0, 1, 0 };
                            for (int di = 0; di < 4; di++)
                                for (int step = 1; step <= 6; step++)
                                {
                                    if (waterLoc.isWaterTile((int)myTile.X + ddx[di] * step, (int)myTile.Y + ddy[di] * step)) { if (step < bestStep) { bestStep = step; facing = di; } break; }
                                }
                            Game1.player.faceDirection(facing);
                            this.monitor.Log($"Stand by water: player=({(int)myTile.X},{(int)myTile.Y}) facing={facing}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("stand by water failed: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "player_fish_cast":
                    {
                        // 给玩家鱼竿并面向最近水面抛竿
                        var rod = Game1.player.Items.OfType<StardewValley.Tools.FishingRod>().FirstOrDefault();
                        if (rod == null)
                        {
                            rod = new StardewValley.Tools.FishingRod();
                            Game1.player.Items.Add(rod);
                        }
                        Game1.player.CurrentToolIndex = Game1.player.Items.IndexOf(rod);
                        // 找最近水面
                        var fishLoc = Game1.player.currentLocation;
                        var myTile = Game1.player.Tile;
                        int bx = -1, by = -1; float best = float.MaxValue;
                        for (int dx = -12; dx <= 12; dx++)
                        for (int dy = -12; dy <= 12; dy++)
                        {
                            int x = (int)myTile.X + dx, y = (int)myTile.Y + dy;
                            if (fishLoc.isWaterTile(x, y))
                            {
                                float d = Math.Abs(dx) + Math.Abs(dy);
                                if (d < best) { best = d; bx = x; by = y; }
                            }
                        }
                        if (bx < 0) { this.monitor.Log("No water nearby", LogLevel.Warn); break; }
                        // 站到水边陆地上再抛竿（找水格旁边的可通行陆地）
                        // 以水格为中心向外找最近的可站立陆地
                        bool moved = false;
                        for (int r = 1; r <= 15 && !moved; r++)
                        {
                            for (int dx = -r; dx <= r && !moved; dx++)
                            {
                                for (int dy = -r; dy <= r && !moved; dy++)
                                {
                                    if (Math.Abs(dx) != r && Math.Abs(dy) != r) continue;
                                    int lx = bx + dx, ly = by + dy;
                                    if (fishLoc.isWaterTile(lx, ly)) continue;
                                    if (!fishLoc.isTilePassable(new xTile.Dimensions.Location(lx, ly), Game1.viewport)) continue;
                                    Game1.player.Position = new Microsoft.Xna.Framework.Vector2(lx * 64f, ly * 64f);
                                    myTile = new Microsoft.Xna.Framework.Vector2(lx, ly);
                                    moved = true;
                                    this.monitor.Log($"Moved to shore ({lx},{ly}) dist={r}", LogLevel.Info);
                                }
                            }
                        }
                        if (moved) Game1.player.faceDirection(0);
                        // 只沿四个正方向找水（鱼竿只能往面向方向抛）
                        int facing = 2; int cwx = -1, cwy = -1; int bestCard = 99;
                        int[] ddx = { 0, 1, 0, -1 }, ddy = { -1, 0, 1, 0 };
                        for (int di = 0; di < 4; di++)
                        {
                            for (int step = 1; step <= 6; step++)
                            {
                                int cwx2 = (int)myTile.X + ddx[di] * step, cwy2 = (int)myTile.Y + ddy[di] * step;
                                if (fishLoc.isWaterTile(cwx2, cwy2)) { if (step < bestCard) { bestCard = step; facing = di; cwx = cwx2; cwy = cwy2; } break; }
                            }
                        }
                        this.monitor.Log($"Cast: player=({(int)myTile.X},{(int)myTile.Y}) water=({cwx},{cwy}) facing={facing}", LogLevel.Info);
                        Game1.player.faceDirection(facing);
                        this.AutoFishing = true;
                        try
                        {
                            rod.beginUsing(fishLoc, (int)Game1.player.Position.X, (int)Game1.player.Position.Y, Game1.player);
                            rod.castingPower = 1f;
                            this.monitor.Log($"Auto cast at water ({bx},{by}) facing {facing}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("fish cast failed: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "set_auto_fish":
                    this.AutoFishing = !root.TryGetProperty("enabled", out var af) || af.GetBoolean();
                    this.monitor.Log($"AutoFishing = {this.AutoFishing}", LogLevel.Info);
                    break;

                case "player_use_seed_at":
                    {
                        if (root.TryGetProperty("x", out var usx) && root.TryGetProperty("y", out var usy))
                        {
                            int idx = -1; StardewValley.Object seed = null;
                            for (int i = 0; i < Game1.player.Items.Count; i++)
                            {
                                if (Game1.player.Items[i] is StardewValley.Object o && o.Category == -74 && o.Stack > 0
                                    && (!root.TryGetProperty("seed", out var sn) || o.DisplayName.IndexOf(sn.GetString(), StringComparison.OrdinalIgnoreCase) >= 0))
                                { idx = i; seed = o; break; }
                            }
                            if (seed == null) { this.monitor.Log("No matching seed", LogLevel.Warn); break; }
                            Game1.player.CurrentToolIndex = idx;
                            var ok = Game1.player.currentLocation.checkAction(
                                new xTile.Dimensions.Location(usx.GetInt32(), usy.GetInt32()), Game1.viewport, Game1.player);
                            this.monitor.Log($"Use seed {seed.DisplayName} at ({usx.GetInt32()},{usy.GetInt32()}) -> {ok}", LogLevel.Info);
                        }
                        break;
                    }

                case "player_ship_all":
                    {
                        // 走到出货箱旁边，把背包可卖物品放进真实出货箱（第二天结算）
                        var farm = Game1.getFarm();
                        var bin = farm.getShippingBin(Game1.player);
                        int moved = 0;
                        foreach (var item in Game1.player.Items.ToList())
                        {
                            if (item is StardewValley.Object o && o.Category != -74 && o.Stack > 0)
                            {
                                bin.Add(o);
                                Game1.player.removeItemFromInventory(o);
                                moved++;
                            }
                        }
                        this.monitor.Log($"Shipped {moved} items (money next morning)", LogLevel.Info);
                        break;
                    }

                case "player_force_new_day":
                    {
                        var ms = typeof(Game1).GetMethods(System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic)
                            .Where(mm => mm.Name == "NewDay" || mm.Name == "newDayAfterFade" || mm.Name == "_newDayAfterFade").ToList();
                        if (ms.Count == 0) this.monitor.Log("no NewDay method", LogLevel.Warn);
                        foreach (var m in ms)
                        {
                            try
                            {
                                var args = m.GetParameters().Select(pp =>
                                    pp.ParameterType == typeof(float) ? (object)0f :
                                    pp.ParameterType == typeof(int) ? (object)0 :
                                    pp.ParameterType == typeof(bool) ? (object)false : null).ToArray();
                                var r = m.Invoke(null, args);
                                this.monitor.Log($"NewDay {m.Name}({string.Join(",", m.GetParameters().Select(pp => pp.ParameterType.Name))}) -> {r}", LogLevel.Info);
                            }
                            catch (Exception ex) { this.monitor.Log($"NewDay {m.Name} err: {ex.InnerException?.Message ?? ex.Message}", LogLevel.Warn); }
                        }
                        break;
                    }

                case "set_focus_pause":
                    {
                        bool enabled = !root.TryGetProperty("disabled", out var fp) || !fp.GetBoolean();
                        this.autoNoPause = !enabled;
                        try
                        {
                            var opts = Game1.options;
                            var t = opts.GetType();
                            foreach (var name in new[] { "pauseWhenOutOfFocus", "pauseWhenUnfocused" })
                            {
                                var f = t.GetField(name, System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                                var p = t.GetProperty(name, System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                                if (f != null) f.SetValue(opts, enabled);
                                else if (p != null && p.CanWrite) p.SetValue(opts, enabled);
                                this.monitor.Log($"Set {name} = {enabled}", LogLevel.Info);
                            }
                        }
                        catch (Exception ex) { this.monitor.Log("set_focus_pause failed: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "set_auto_dismiss":
                    this.AutoDismiss = !root.TryGetProperty("enabled", out var ad) || ad.GetBoolean();
                    this.monitor.Log($"AutoDismiss = {this.AutoDismiss}", LogLevel.Info);
                    break;

                case "give_companion_weapon":
                    {
                        int given = 0;
                        foreach (var kvp in this.companions)
                        {
                            var shadow = kvp.Value.Companion.Shadow;
                            if (shadow == null) continue;
                            bool has = shadow.Items.Any(it => it is StardewValley.Tools.MeleeWeapon);
                            if (!has)
                            {
                                var item = StardewValley.ItemRegistry.Create("(W)1"); // 初始剑
                                if (item != null) { shadow.Items.Add(item); given++; }
                            }
                            for (int i = 0; i < shadow.Items.Count; i++)
                                if (shadow.Items[i] is StardewValley.Tools.MeleeWeapon) { shadow.CurrentToolIndex = i; break; }
                        }
                        this.monitor.Log($"Gave companion weapons: {given}", LogLevel.Info);
                        break;
                    }

                case "give_companion_tools":
                    {
                        int added = 0;
                        foreach (var kvp in this.companions)
                        {
                            var shadow = kvp.Value.Companion.Shadow;
                            if (shadow == null) continue;
                            bool has(Type t) { foreach (var it in shadow.Items) if (t.IsInstanceOfType(it)) return true; return false; }
                            if (!has(typeof(StardewValley.Tools.Pickaxe))) { shadow.Items.Add(new StardewValley.Tools.Pickaxe()); added++; }
                            if (!has(typeof(StardewValley.Tools.Axe))) { shadow.Items.Add(new StardewValley.Tools.Axe()); added++; }
                            if (!has(typeof(StardewValley.Tools.Hoe))) { shadow.Items.Add(new StardewValley.Tools.Hoe()); added++; }
                            if (!has(typeof(StardewValley.Tools.WateringCan))) { shadow.Items.Add(new StardewValley.Tools.WateringCan()); added++; }
                            if (!has(typeof(StardewValley.Tools.FishingRod))) { shadow.Items.Add(new StardewValley.Tools.FishingRod()); added++; }
                        }
                        this.monitor.Log($"Gave companion tools: {added}", LogLevel.Info);
                        break;
                    }

                case "give_fishing_rod":
                    {
                        int given = 0;
                        foreach (var kvp in this.companions)
                        {
                            var shadow = kvp.Value.Companion.Shadow;
                            if (shadow == null) continue;
                            bool has = false;
                            foreach (var it in shadow.Items)
                            {
                                if (it is StardewValley.Tools.FishingRod) { has = true; break; }
                            }
                            if (!has)
                            {
                                shadow.Items.Add(new StardewValley.Tools.FishingRod());
                                given++;
                            }
                            // 把鱼竿设为同伴当前工具
                            for (int i = 0; i < shadow.Items.Count; i++)
                            {
                                if (shadow.Items[i] is StardewValley.Tools.FishingRod) { shadow.CurrentToolIndex = i; break; }
                            }
                        }
                        this.monitor.Log($"Gave fishing rods: {given}", LogLevel.Info);
                        break;
                    }

                case "collect_debris":
                    {
                        int picked = 0;
                        var dloc = Game1.player.currentLocation;
                        if (dloc != null)
                        {
                            foreach (var d in dloc.debris.ToList())
                            {
                                try
                                {
                                    if (d.item != null && Game1.player.addItemToInventoryBool(d.item)) { picked++; d.item = null; }
                                }
                                catch { }
                            }
                            for (int i = dloc.debris.Count - 1; i >= 0; i--)
                                if (dloc.debris[i] != null && dloc.debris[i].item == null) dloc.debris.RemoveAt(i);
                        }
                        this.monitor.Log($"Picked {picked} ground items", LogLevel.Info);
                        break;
                    }

                case "companion_collect":
                    {
                        int moved = 0;
                        foreach (var kvp in this.companions)
                        {
                            var shadow = kvp.Value.Companion.Shadow;
                            if (shadow == null) continue;
                            for (int i = 0; i < shadow.Items.Count; i++)
                            {
                                var it = shadow.Items[i];
                                if (it == null || it.Stack <= 0) continue;
                                if (it is StardewValley.Tool) continue;   // 不搬工具，避免重复
                                if (Game1.player.addItemToInventoryBool(it))
                                {
                                    shadow.Items[i] = null;
                                    moved++;
                                }
                            }
                        }
                        this.monitor.Log($"Companion items moved to player: {moved}", LogLevel.Info);
                        break;
                    }

                case "player_buy_item":
                    if (root.TryGetProperty("item", out var biName))
                    {
                        int biCnt = root.TryGetProperty("count", out var biCntP) ? biCntP.GetInt32() : 1;
                        int biCat = root.TryGetProperty("category", out var biCatP) ? biCatP.GetInt32() : 999;
                        this.PlayerBuyItem(biName.GetString(), biCnt, biCat);
                    }
                    break;

                case "player_eat":
                    this.PlayerEat();
                    break;

                // Single-tile actions (from stardew_action tool)
                case "water":
                case "harvest":
                case "clear":
                case "hoe":
                    if (root.TryGetProperty("x", out var ax) && root.TryGetProperty("y", out var ay))
                    {
                        var location = Game1.player.currentLocation;
                        if (location != null)
                        {
                            var tile = new Vector2(ax.GetInt32(), ay.GetInt32());
                            bool ok = action switch
                            {
                                "water" => CompanionActions.WaterTile(location, tile, this.monitor),
                                "harvest" => CompanionActions.HarvestTile(location, tile, this.monitor),
                                "clear" => CompanionActions.ClearDebris(location, tile, this.monitor),
                                "hoe" => CompanionActions.HoeTile(location, tile, this.monitor),
                                _ => false
                            };
                            this.monitor.Log($"Action {action} at ({ax.GetInt32()},{ay.GetInt32()}): {(ok ? "success" : "failed")}", LogLevel.Info);
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
                    // 先记下产物，收获后直接按卖价结算（保证经济闭环）
                    int income = 0;
                    try
                    {
                        var harvestId = dirt.crop.indexOfHarvest.Value;
                        if (!string.IsNullOrEmpty(harvestId))
                            income = (int)(new StardewValley.Object(harvestId, 1).sellToStorePrice());
                    }
                    catch { }
                    var ok = location.checkAction(new xTile.Dimensions.Location((int)pair.Key.X, (int)pair.Key.Y), Game1.viewport, Game1.player);
                    if (ok)
                    {
                        count++;
                        Game1.player.Money += income;
                        this.monitor.Log($"Harvested crop at ({pair.Key.X}, {pair.Key.Y}) +{income}g", LogLevel.Trace);
                    }
                }
            }
            this.monitor.Log($"Harvested {count} crops", LogLevel.Info);
        }

        // ---------------- 主角种田/经济 ----------------

        private void PlayerTillAll()
        {
            var location = Game1.player.currentLocation;
            if (location == null) return;
            var p = Game1.player.Tile;
            int count = 0;
            for (int dx = -7; dx <= 7 && count < 24; dx++)
            {
                for (int dy = -7; dy <= 7 && count < 24; dy++)
                {
                    var tile = new Vector2(p.X + dx, p.Y + dy);
                    if (location.terrainFeatures.ContainsKey(tile)) continue;
                    if (location.objects.ContainsKey(tile)) continue;
                    if (CompanionActions.HoeTile(location, tile, this.monitor))
                        count++;
                }
            }
            this.monitor.Log($"Tilled {count} tiles", LogLevel.Info);
        }

        private bool PlantSeedOnDirt(StardewValley.TerrainFeatures.HoeDirt dirt, string seedId, GameLocation location, Vector2 tile)
        {
            try
            {
                var methods = typeof(StardewValley.TerrainFeatures.HoeDirt)
                    .GetMethods(System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic)
                    .Where(mm => mm.Name == "plant").ToList();
                if (methods.Count == 0)
                    this.monitor.Log("HoeDirt.plant not found!", LogLevel.Warn);
                foreach (var m in methods)
                {
                    try
                    {
                        var args = m.GetParameters().Select(pp =>
                            pp.ParameterType == typeof(string) ? (object)seedId :
                            pp.ParameterType == typeof(Farmer) ? (object)Game1.player :
                            pp.ParameterType == typeof(bool) ? (object)false :
                            pp.ParameterType == typeof(int) ? (object)(int)tile.X : null).ToArray();
                        var r = m.Invoke(dirt, args);
                        this.monitor.Log($"plant -> {r} crop={(dirt.crop != null)} state={dirt.state.Value} loc={location.Name} seedId={seedId}", LogLevel.Info);
                        if (r is bool b && b) return true;
                    }
                    catch (Exception ex2)
                    {
                        this.monitor.Log($"plant overload {string.Join(",", m.GetParameters().Select(pp => pp.ParameterType.Name))} err: {ex2.InnerException?.Message ?? ex2.Message}", LogLevel.Warn);
                    }
                }
            }
            catch (Exception ex)
            {
                this.monitor.Log("plant() failed: " + ex.Message, LogLevel.Warn);
            }
            try
            {
                var ctor = typeof(StardewValley.Crop).GetConstructor(new[] { typeof(string), typeof(int), typeof(int), typeof(GameLocation) });
                if (ctor != null)
                {
                    var crop = (StardewValley.Crop)ctor.Invoke(new object[] { seedId, (int)tile.X, (int)tile.Y, location });
                    dirt.crop = crop;
                    return true;
                }
            }
            catch (Exception ex)
            {
                this.monitor.Log("Crop ctor failed: " + ex.Message, LogLevel.Warn);
            }
            return false;
        }

        private void PlayerPlant()
        {
            var location = Game1.player.currentLocation;
            if (location == null) return;
            var seeds = Game1.player.Items.OfType<StardewValley.Object>()
                .Where(o => o.Category == -74 && o.Stack > 0).ToList();
            if (seeds.Count == 0)
            {
                this.monitor.Log("No seeds in player inventory", LogLevel.Info);
                return;
            }
            int planted = 0;
            foreach (var seed in seeds)
            {
                foreach (var pair in location.terrainFeatures.Pairs.ToList())
                {
                    if (planted >= 40) break;
                    if (!(pair.Value is StardewValley.TerrainFeatures.HoeDirt dirt)) continue;
                    if (dirt.crop != null) continue;
                    if (this.PlantSeedOnDirt(dirt, seed.ItemId, location, pair.Key))
                    {
                        planted++;
                        seed.Stack--;
                        if (seed.Stack <= 0)
                        {
                            Game1.player.removeItemFromInventory(seed);
                            break;
                        }
                    }
                }
            }
            this.monitor.Log($"Planted {planted} seeds", LogLevel.Info);
        }

        private void PlayerSellAll()
        {
            var farm = Game1.getFarm();
            var bin = farm.getShippingBin(Game1.player);
            int count = 0;
            foreach (var item in Game1.player.Items.ToList())
            {
                if (item is StardewValley.Object o && o.Category != -74 && o.Stack > 0)
                {
                    bin.Add(o);
                    Game1.player.removeItemFromInventory(o);
                    count++;
                }
            }
            this.monitor.Log($"Moved {count} items to shipping bin", LogLevel.Info);
        }

        private void PlayerBuySeed(string seedName, int count)
        {
            try
            {
                var prop = typeof(Game1).GetMembers(System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic)
                    .FirstOrDefault(m => m.Name == "objectData");
                object raw = prop is System.Reflection.PropertyInfo pi ? pi.GetValue(null)
                    : prop is System.Reflection.FieldInfo fi ? fi.GetValue(null) : null;
                if (raw == null)
                {
                    this.monitor.Log("objectData not found", LogLevel.Warn);
                    return;
                }
                var dict = raw as System.Collections.IDictionary;
                if (dict == null) return;
                string bestId = null;
                int price = 0;
                foreach (System.Collections.DictionaryEntry entry in dict)
                {
                    var od = entry.Value;
                    var odType = od.GetType();
                    var nameObj = odType.GetProperty("Name")?.GetValue(od) ?? odType.GetField("Name")?.GetValue(od);
                    var catObj = odType.GetProperty("Category")?.GetValue(od) ?? odType.GetField("Category")?.GetValue(od);
                    var priceObj = odType.GetProperty("Price")?.GetValue(od) ?? odType.GetField("Price")?.GetValue(od);
                    if (nameObj == null || catObj == null || priceObj == null) continue;
                    if ((int)catObj != -74) continue;
                    string nm = nameObj.ToString();
                    if (nm.IndexOf(seedName, StringComparison.OrdinalIgnoreCase) >= 0)
                    {
                        bestId = entry.Key.ToString();
                        price = (int)priceObj;
                        break;
                    }
                }
                if (bestId == null)
                {
                    this.monitor.Log($"Seed not found: {seedName}", LogLevel.Warn);
                    return;
                }
                if (count <= 0) count = 1;
                int cost = price * count;
                if (Game1.player.Money < cost)
                {
                    count = price > 0 ? Game1.player.Money / price : 0;
                    cost = price * count;
                }
                if (count <= 0)
                {
                    this.monitor.Log("Not enough money to buy seeds", LogLevel.Warn);
                    return;
                }
                Game1.player.Money -= cost;
                Game1.player.addItemToInventory(new StardewValley.Object(bestId, count));
                this.monitor.Log($"Bought {count}x {seedName} (id {bestId}) for {cost}g", LogLevel.Info);
            }
            catch (Exception ex)
            {
                this.monitor.Log("Buy seed failed: " + ex.Message, LogLevel.Error);
            }
        }

        private void PlayerBuyItem(string itemName, int count, int categoryFilter)
        {
            try
            {
                var prop = typeof(Game1).GetMembers(System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic)
                    .FirstOrDefault(m => m.Name == "objectData");
                object raw = prop is System.Reflection.PropertyInfo pi ? pi.GetValue(null)
                    : prop is System.Reflection.FieldInfo fi ? fi.GetValue(null) : null;
                var dict = raw as System.Collections.IDictionary;
                if (dict == null) return;
                string bestId = null; int price = 0;
                foreach (System.Collections.DictionaryEntry entry in dict)
                {
                    var od = entry.Value;
                    var odType = od.GetType();
                    var nameObj = odType.GetProperty("Name")?.GetValue(od) ?? odType.GetField("Name")?.GetValue(od);
                    var catObj = odType.GetProperty("Category")?.GetValue(od) ?? odType.GetField("Category")?.GetValue(od);
                    var priceObj = odType.GetProperty("Price")?.GetValue(od) ?? odType.GetField("Price")?.GetValue(od);
                    if (nameObj == null || catObj == null || priceObj == null) continue;
                    int cat = (int)catObj;
                    if (categoryFilter != 999 && cat != categoryFilter) continue;
                    if (nameObj.ToString().IndexOf(itemName, StringComparison.OrdinalIgnoreCase) < 0) continue;
                    bestId = entry.Key.ToString(); price = (int)priceObj; break;
                }
                if (bestId == null) { this.monitor.Log($"Item not found: {itemName}", LogLevel.Warn); return; }
                if (count <= 0) count = 1;
                int cost = price * count;
                if (Game1.player.Money < cost) { count = price > 0 ? Game1.player.Money / price : 0; cost = price * count; }
                if (count <= 0) { this.monitor.Log("Not enough money", LogLevel.Warn); return; }
                Game1.player.Money -= cost;
                Game1.player.addItemToInventory(new StardewValley.Object(bestId, count));
                this.monitor.Log($"Bought {count}x {itemName} for {cost}g", LogLevel.Info);
            }
            catch (Exception ex) { this.monitor.Log("buy item failed: " + ex.Message, LogLevel.Error); }
        }

        private void PlayerEat()
        {
            var food = Game1.player.Items.OfType<StardewValley.Object>().FirstOrDefault(o => o.Edibility > 0 && o.Stack > 0);
            if (food == null)
            {
                this.monitor.Log("No edible items", LogLevel.Info);
                return;
            }
            try
            {
                Game1.player.eatObject(food);
                this.monitor.Log($"Ate {food.DisplayName}", LogLevel.Info);
            }
            catch (Exception ex)
            {
                Game1.player.Stamina = Math.Min(Game1.player.MaxStamina, Game1.player.Stamina + food.Edibility);
                food.Stack--;
                if (food.Stack <= 0) Game1.player.removeItemFromInventory(food);
                this.monitor.Log($"Ate {food.DisplayName} (manual restore: {ex.Message})", LogLevel.Info);
            }
        }
    }
}
