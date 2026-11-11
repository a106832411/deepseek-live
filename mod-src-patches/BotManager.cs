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
        public static void ResetFarmerPose()
        {
            try { Game1.player.UsingTool = false; Game1.player.canMove = true; } catch { }
        }
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
                    string locName = Game1.currentLocation?.Name ?? "";
                    bool mineEvent = locName.StartsWith("Mine", StringComparison.OrdinalIgnoreCase)
                        || locName.StartsWith("UndergroundMine", StringComparison.OrdinalIgnoreCase);
                    if (!mineEvent)
                    {
                        try { ev.skipEvent(); } catch { }
                        this.monitor.Log("Auto-skip event", LogLevel.Debug);
                    }
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
                            // 凌晨 2 点晕倒时不要清掉黑屏/事件，否则会永远卡在 2:00
                            if (Game1.timeOfDay < 2500)
                            {
                                Game1.eventUp = false;
                                Game1.fadeToBlack = false;
                                Game1.fadeIn = false;
                                try
                                {
                                    var fa = typeof(Game1).GetField("fadeToBlackAlpha",
                                        System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                                    if (fa != null) fa.SetValue(null, 0f);
                                }
                                catch { }
                            }
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

                case "tile_props":
                    {
                        try
                        {
                            int px2 = root.GetProperty("x").GetInt32();
                            int py2 = root.GetProperty("y").GetInt32();
                            var tl = Game1.player.currentLocation;
                            var parts = new System.Collections.Generic.List<string>();
                            // 直接查属性（多种 data 层）
                            foreach (var dataLayer in new[] { "Back", "Buildings", "Front", "AlwaysFront" })
                            {
                                var act = tl.doesTileHaveProperty(px2, py2, "Action", dataLayer);
                                if (!string.IsNullOrEmpty(act)) parts.Add($"prop[{dataLayer}]=" + act);
                                var tch = tl.doesTileHaveProperty(px2, py2, "TouchAction", dataLayer);
                                if (!string.IsNullOrEmpty(tch)) parts.Add($"touch[{dataLayer}]=" + tch);
                            }
                            this.monitor.Log($"TILEPROPS {tl.Name} {px2},{py2}: " + string.Join(" | ", parts), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("tile_props err: " + ex.Message, LogLevel.Warn); }
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

                case "farmer_state":
                    {
                        try
                        {
                            var fs = Game1.player.FarmerSprite;
                            this.monitor.Log($"farmer_state usingTool={Game1.player.UsingTool} canMove={Game1.player.canMove} frame={fs.CurrentFrame} shirt={Game1.player.shirtItem?.Name} pants={Game1.player.pantsItem?.Name}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("farmer_state err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "dump_tools":
                    {
                        try
                        {
                            var parts = new System.Collections.Generic.List<string>();
                            foreach (var it in Game1.player.Items)
                            {
                                if (it == null) continue;
                                parts.Add($"{it.Name}|{it.GetType().Name}|{it.GetType().FullName}|stack={it.Stack}");
                            }
                            this.monitor.Log("TOOLS " + string.Join(" ; ", parts), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_tools err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "find_ladder_pos":
                    {
                        try
                        {
                            var lm = Game1.currentLocation?.GetType().GetMethod("findLadder",
                                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                            if (lm != null)
                            {
                                var res = lm.Invoke(Game1.player.currentLocation, null);
                                this.monitor.Log($"FINDLADDER -> {res}", LogLevel.Info);
                            }
                            else this.monitor.Log("FINDLADDER: 方法不存在", LogLevel.Warn);
                        }
                        catch (Exception ex) { this.monitor.Log("find_ladder_pos err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "dump_mineshaft_all":
                    {
                        try
                        {
                            var locA = Game1.player.currentLocation;
                            var btA = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var partsA = new System.Collections.Generic.List<string>();
                            for (var tA = locA.GetType(); tA != null && tA != typeof(object); tA = tA.BaseType)
                            {
                                foreach (var fA in tA.GetFields(btA))
                                {
                                    try
                                    {
                                        var vA = fA.GetValue(locA);
                                        if (vA == null) continue;
                                        if (vA is Microsoft.Xna.Framework.Vector2 || vA is Microsoft.Xna.Framework.Point
                                            || vA.GetType().Name.Contains("Point") || vA.GetType().Name.Contains("Vector2")
                                            || vA.GetType().Name.Contains("NetVector2") || vA.GetType().Name.Contains("NetPoint"))
                                            partsA.Add($"{tA.Name}.{fA.Name}={vA}");
                                    }
                                    catch { }
                                }
                            }
                            this.monitor.Log("MINEALL " + string.Join(" ; ", partsA), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_mineshaft_all err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "dump_mineshaft":
                    {
                        try
                        {
                            var locM = Game1.player.currentLocation;
                            var bt = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var parts = new System.Collections.Generic.List<string>();
                            for (var t0 = locM.GetType(); t0 != null && t0 != typeof(object); t0 = t0.BaseType)
                            {
                                foreach (var mi in t0.GetMethods(bt))
                                {
                                    string mn = mi.Name.ToLower();
                                    if (mn.Contains("ladder") || mn.Contains("shaft") || mn.Contains("stair") || mn.Contains("exit") || mn.Contains("descend"))
                                        parts.Add($"M {t0.Name}.{mi.Name}({string.Join(",", Array.ConvertAll(mi.GetParameters(), pp => pp.ParameterType.Name))})");
                                }
                                foreach (var f in t0.GetFields(bt))
                                {
                                    string fn = f.Name.ToLower();
                                    if (fn.Contains("ladder") || fn.Contains("stair") || fn.Contains("shaft") || fn.Contains("exit"))
                                    {
                                        try { parts.Add($"{t0.Name}.{f.Name}={f.GetValue(locM)}"); } catch { }
                                    }
                                }
                                foreach (var pr in t0.GetProperties(bt))
                                {
                                    string pn = pr.Name.ToLower();
                                    if (pn.Contains("ladder") || pn.Contains("stair") || pn.Contains("shaft") || pn.Contains("exit"))
                                    {
                                        try { parts.Add($"{t0.Name}.{pr.Name}={pr.GetValue(locM)}"); } catch { }
                                    }
                                }
                            }
                            this.monitor.Log("MINESHAFT " + string.Join(" ; ", parts), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_mineshaft err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "dump_tile":
                    {
                        try
                        {
                            int dx7 = root.GetProperty("x").GetInt32(), dy7 = root.GetProperty("y").GetInt32();
                            var lc = Game1.player.currentLocation;
                            var parts = new System.Collections.Generic.List<string>();
                            foreach (var lay in new[] { "Back", "Buildings", "Front", "AlwaysFront" })
                            {
                                var layer = lc.Map.GetLayer(lay);
                                if (layer == null) continue;
                                var tl = layer.Tiles[dx7, dy7];
                                parts.Add($"{lay}:idx={(tl == null ? -1 : tl.TileIndex)}");
                                if (tl != null)
                                    foreach (var pk in tl.TileSheet.TileIndexProperties[tl.TileIndex].Keys)
                                        parts.Add($"{lay}.{pk}='{tl.TileSheet.TileIndexProperties[tl.TileIndex][pk]}'");
                            }
                            if (lc.Objects.TryGetValue(new Microsoft.Xna.Framework.Vector2(dx7, dy7), out var o7) && o7 != null)
                                parts.Add($"OBJ name='{o7.Name}' disp='{o7.DisplayName}' idx={o7.ParentSheetIndex} type={o7.Type}");
                            this.monitor.Log($"TILE {dx7},{dy7}: " + string.Join(" ; ", parts), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_tile err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "dump_actions":
                    {
                        try
                        {
                            var locD = Game1.player.currentLocation;
                            int wD = locD.Map.Layers[0].LayerWidth, hD = locD.Map.Layers[0].LayerHeight;
                            var parts = new System.Collections.Generic.List<string>();
                            int cnt = 0;
                            foreach (var lay in new[] { "Back", "Buildings", "Front", "AlwaysFront" })
                                for (int y = 0; y < hD && cnt < 60; y++)
                                    for (int x = 0; x < wD && cnt < 60; x++)
                                    {
                                        var a = locD.doesTileHaveProperty(x, y, "Action", lay);
                                        var ta = locD.doesTileHaveProperty(x, y, "TouchAction", lay);
                                        if (!string.IsNullOrEmpty(a) || !string.IsNullOrEmpty(ta))
                                        {
                                            parts.Add($"{x},{y} {lay} Action='{a}' Touch='{ta}'");
                                            cnt++;
                                        }
                                    }
                            var names = new System.Collections.Generic.HashSet<string>();
                            foreach (var kv in locD.Objects.Pairs) { var oo = kv.Value; if (oo != null) names.Add($"{oo.Name}|idx{oo.ParentSheetIndex}|{oo.Type}"); }
                            this.monitor.Log($"ACTIONS {locD.Name} objs({locD.Objects.Count()}): " + string.Join(" ; ", names), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_actions err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "throw_item":
                    {
                        try
                        {
                            string tn = root.TryGetProperty("name", out var tnEl) ? tnEl.GetString() : "";
                            int removed = 0;
                            for (int i = 0; i < Game1.player.Items.Count; i++)
                            {
                                var it = Game1.player.Items[i];
                                if (it == null) continue;
                                string dn = it.DisplayName ?? "";
                                string nm = it.Name ?? "";
                                if (dn.Contains(tn) || nm.Contains(tn)) { Game1.player.Items[i] = null; removed++; break; }
                            }
                            this.monitor.Log($"throw_item {tn} removed={removed} slots={Game1.player.Items.Count(x => x != null)}/{Game1.player.MaxItems}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("throw_item err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "drop_item":
                    {
                        try
                        {
                            string dn = root.TryGetProperty("name", out var dnEl) ? dnEl.GetString() : "";
                            int dnCount = root.TryGetProperty("count", out var dcEl) ? dcEl.GetInt32() : 1;
                            var lloc = Game1.player.currentLocation;
                            int dropped = 0;
                            for (int i = Game1.player.Items.Count - 1; i >= 0 && dropped < dnCount; i--)
                            {
                                var it = Game1.player.Items[i];
                                if (it == null) continue;
                                string inm = it.DisplayName ?? it.Name ?? "";
                                if (!inm.Contains(dn)) continue;
                                Game1.player.Items[i] = null;
                                try { lloc.debris.Add(new StardewValley.Debris(it, Game1.player.Position)); } catch { }
                                dropped++;
                            }
                            this.monitor.Log($"drop_item {dn} x{dropped}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("drop_item err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "chest_take_named":
                    {
                        try
                        {
                            int cxT = root.GetProperty("x").GetInt32(), cyT = root.GetProperty("y").GetInt32();
                            string nmT = root.TryGetProperty("name", out var nT) ? nT.GetString() : "";
                            int cntT = root.TryGetProperty("count", out var cT) ? cT.GetInt32() : 999;
                            var locT = Game1.player.currentLocation;
                            if (!locT.Objects.TryGetValue(new Microsoft.Xna.Framework.Vector2(cxT, cyT), out var objT) || !(objT is StardewValley.Objects.Chest chT))
                            { this.monitor.Log("chest_take_named: 目标不是箱子", LogLevel.Warn); break; }
                            int movedT = 0;
                            for (int iT = chT.Items.Count - 1; iT >= 0 && movedT < cntT; iT--)
                            {
                                var itT = chT.Items[iT];
                                if (itT == null) continue;
                                string n2 = itT.DisplayName ?? itT.Name ?? "";
                                if (!n2.Contains(nmT)) continue;
                                var copy = itT.getOne();
                                copy.Stack = 1;
                                if (Game1.player.addItemToInventoryBool(copy, true))
                                {
                                    itT.Stack -= 1;
                                    if (itT.Stack <= 0) chT.Items[iT] = null;
                                    movedT++;
                                }
                                else break;
                            }
                            this.monitor.Log($"chest_take_named: 取出 {movedT} 个 {nmT}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("chest_take_named err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "chest_store_named":
                    {
                        try
                        {
                            int cx7 = root.GetProperty("x").GetInt32(), cy7 = root.GetProperty("y").GetInt32();
                            var names7 = new System.Collections.Generic.List<string>();
                            if (root.TryGetProperty("names", out var nmArr) && nmArr.ValueKind == System.Text.Json.JsonValueKind.Array)
                                foreach (var e in nmArr.EnumerateArray()) names7.Add(e.GetString());
                            var loc7 = Game1.player.currentLocation;
                            if (!loc7.Objects.TryGetValue(new Microsoft.Xna.Framework.Vector2(cx7, cy7), out var chest7) || !(chest7 is StardewValley.Objects.Chest ch7))
                            { this.monitor.Log("chest_store_named: 目标不是箱子", LogLevel.Warn); break; }
                            int moved7 = 0;
                            for (int i7 = Game1.player.Items.Count - 1; i7 >= 0; i7--)
                            {
                                var it7 = Game1.player.Items[i7];
                                if (it7 == null) continue;
                                string nm7 = it7.DisplayName ?? it7.Name ?? "";
                                if (!names7.Exists(n => nm7.Contains(n))) continue;
                                if (ch7.Items.Count >= ch7.GetActualCapacity()) break;
                                ch7.Items.Add(it7);
                                Game1.player.Items[i7] = null;
                                moved7++;
                            }
                            this.monitor.Log($"chest_store_named: 存入 {moved7} 个", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("chest_store_named err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "give_item":
                    {
                        try
                        {
                            string idv = root.TryGetProperty("id", out var idEl) ? idEl.GetString() : "(O)131";
                            var it = StardewValley.ItemRegistry.Create(idv);
                            bool ok = false;
                            if (it != null)
                            {
                                if (it.Stack <= 0) it.Stack = 1;
                                ok = Game1.player.addItemToInventoryBool(it, false);
                                if (!ok)
                                {
                                    for (int si = 0; si < Game1.player.MaxItems; si++)
                                    {
                                        if (Game1.player.Items[si] == null) { Game1.player.Items[si] = it; ok = true; this.monitor.Log($"give_item: 放入槽位 {si}", LogLevel.Info); break; }
                                    }
                                }
                            }
                            this.monitor.Log($"give_item {idv} -> {ok} ({it?.Name}) max={Game1.player.MaxItems} count={Game1.player.Items.Count} none={Game1.player.Items.Count(x => x == null)}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("give_item err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "ensure_ladder":
                    {
                        try
                        {
                            var locE = Game1.player.currentLocation;
                            var btE = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var sfE = locE.GetType().GetField("ladderHasSpawned", btE);
                            bool spE = sfE != null && (bool)(sfE.GetValue(locE) ?? false);
                            var partsE = new System.Collections.Generic.List<string>();
                            partsE.Add("spawnedBefore=" + spE);
                            if (!spE)
                            {
                                int pxE = (int)Game1.player.Tile.X, pyE = (int)Game1.player.Tile.Y;
                                Microsoft.Xna.Framework.Point? spot = null;
                                if (root.TryGetProperty("x", out var xe) && root.TryGetProperty("y", out var ye))
                                    spot = new Microsoft.Xna.Framework.Point(xe.GetInt32(), ye.GetInt32());
                                if (spot == null)
                                {
                                    for (int r = 1; r <= 6 && spot == null; r++)
                                        for (int dx = -r; dx <= r && spot == null; dx++)
                                            for (int dy = -r; dy <= r && spot == null; dy++)
                                            {
                                                int xx = pxE + dx, yy = pyE + dy;
                                                if (xx < 1 || yy < 1) continue;
                                                try
                                                {
                                                    var vpos = new Microsoft.Xna.Framework.Vector2(xx, yy);
                                                    if (locE.Objects.ContainsKey(vpos)) continue;
                                                    if (locE.isWaterTile(xx, yy)) continue;
                                                    if (!locE.isTilePassable(new xTile.Dimensions.Location(xx, yy), Game1.viewport)) continue;
                                                }
                                                catch { continue; }
                                                spot = new Microsoft.Xna.Framework.Point(xx, yy);
                                            }
                                }
                                partsE.Add("spot=" + spot);
                                if (spot != null)
                                {
                                    var dld = locE.GetType().GetMethod("doCreateLadderDown", btE);
                                    if (dld != null)
                                    {
                                        try { dld.Invoke(locE, new object[] { spot.Value, true }); partsE.Add("doCreateLadderDown=ok"); }
                                        catch (Exception ex) { partsE.Add("doErr=" + ex.Message); }
                                    }
                                    else
                                    {
                                        var cld = locE.GetType().GetMethod("createLadderDown", btE);
                                        if (cld != null)
                                        {
                                            try { cld.Invoke(locE, new object[] { spot.Value.X, spot.Value.Y, true }); partsE.Add("createLadderDown=ok"); }
                                            catch (Exception ex) { partsE.Add("createErr=" + ex.Message); }
                                        }
                                        else partsE.Add("no_method");
                                    }
                                }
                            }
                            var sf2 = locE.GetType().GetField("ladderHasSpawned", btE);
                            partsE.Add("spawnedAfter=" + (sf2 != null ? sf2.GetValue(locE) : "?"));
                            this.monitor.Log("ensure_ladder: " + string.Join(" ; ", partsE), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("ensure_ladder err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "find_ladder_tiles":
                    {
                        try
                        {
                            var locL = Game1.player.currentLocation;
                            var blL = locL.Map.GetLayer("Buildings");
                            int wL = locL.Map.Layers[0].LayerWidth, hL = locL.Map.Layers[0].LayerHeight;
                            var partsL = new System.Collections.Generic.List<string>();
                            // 已知的“上楼梯子”位置（tileBeneathLadder），要排除
                            int upX = -1, upY = -1;
                            var btL = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var fL = locL.GetType().GetField("netTileBeneathLadder", btL);
                            if (fL != null)
                            {
                                var nvL = fL.GetValue(locL);
                                var vpL = nvL?.GetType().GetProperty("Value");
                                if (vpL != null)
                                {
                                    var vvL = vpL.GetValue(nvL);
                                    if (vvL is Microsoft.Xna.Framework.Vector2 v2L) { upX = (int)v2L.X; upY = (int)v2L.Y; }
                                }
                            }
                            if (blL != null)
                                for (int yy = 0; yy < hL; yy++)
                                    for (int xx = 0; xx < wL; xx++)
                                    {
                                        var tl = blL.Tiles[xx, yy];
                                        if (tl == null) continue;
                                        int idx = tl.TileIndex;
                                        if (idx >= 172 && idx <= 178)
                                            partsL.Add($"{xx},{yy}:idx{idx}");
                                    }
                            this.monitor.Log($"LADDERTILES {locL.Name} up=({upX},{upY}): " + string.Join(" ; ", partsL), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("find_ladder_tiles err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "find_ladder_obj":
                    {
                        try
                        {
                            var locF = Game1.player.currentLocation;
                            var partsF = new System.Collections.Generic.List<string>();
                            foreach (var kv in locF.Objects.Pairs)
                            {
                                var o = kv.Value;
                                if (o == null) continue;
                                string nm = o.Name ?? "";
                                int idx = o.ParentSheetIndex;
                                if (nm.IndexOf("ladder", StringComparison.OrdinalIgnoreCase) >= 0
                                    || nm.IndexOf("shaft", StringComparison.OrdinalIgnoreCase) >= 0
                                    || idx == 173 || idx == 174 || idx == 175 || idx == 751)
                                    partsF.Add($"{kv.Key.X},{kv.Key.Y}:{nm}|idx{idx}|{o.Type}");
                            }
                            this.monitor.Log($"LADDEROBJ {locF.Name}: " + string.Join(" ; ", partsF), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("find_ladder_obj err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "descend_ladder":
                    {
                        try
                        {
                            var locD = Game1.player.currentLocation;
                            var bt = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            int f0 = -1;
                            var nm = locD.Name;
                            if (nm.StartsWith("UndergroundMine") && int.TryParse(nm.Substring("UndergroundMine".Length), out int fl)) f0 = fl;
                            var parts = new System.Collections.Generic.List<string>();
                            // 1) 找到本层楼梯坐标（netTileBeneathLadder）
                            Microsoft.Xna.Framework.Vector2? lad = null;
                            var fld = locD.GetType().GetField("netTileBeneathLadder", bt);
                            if (fld != null)
                            {
                                var nv = fld.GetValue(locD);
                                if (nv != null)
                                {
                                    var valProp = nv.GetType().GetProperty("Value");
                                    if (valProp != null)
                                    {
                                        var vv = valProp.GetValue(nv);
                                        if (vv is Microsoft.Xna.Framework.Vector2 v2) lad = v2;
                                        else if (vv != null)
                                        {
                                            int vx = (int)(vv.GetType().GetProperty("X")?.GetValue(vv) ?? -1);
                                            int vy = (int)(vv.GetType().GetProperty("Y")?.GetValue(vv) ?? -1);
                                            if (vx >= 0) lad = new Microsoft.Xna.Framework.Vector2(vx, vy);
                                        }
                                    }
                                }
                            }
                            // 允许外部指定要尝试的格子（Buildings 174 = 梯子/洞口）
                            if (root.TryGetProperty("x", out var rqx) && root.TryGetProperty("y", out var rqy))
                                lad = new Microsoft.Xna.Framework.Vector2(rqx.GetInt32(), rqy.GetInt32());
                            bool spawned = false;
                            var sf = locD.GetType().GetField("ladderHasSpawned", bt);
                            if (sf != null) spawned = (bool)(sf.GetValue(locD) ?? false);
                            parts.Add($"floor={f0} ladder={lad} spawned={spawned}");
                            // 2) 直接调用 checkAction（等同于玩家站在楼梯旁交互）
                            if (lad.HasValue)
                            {
                                var ca = locD.GetType().GetMethod("checkAction", bt);
                                if (ca != null)
                                {
                                    try
                                    {
                                        var locObj = Activator.CreateInstance(typeof(xTile.Dimensions.Location), (int)lad.Value.X, (int)lad.Value.Y);
                                        var args = new object[] { locObj, Game1.viewport, Game1.player };
                                        var res = ca.Invoke(locD, args);
                                        parts.Add($"checkAction={res}");
                                    }
                                    catch (Exception ex1) { parts.Add("checkActionErr=" + ex1.Message); }
                                }
                            }
                            // 3) 只有显式要求 fallback 时才用 enterMineShaft（默认走 checkAction 的正常楼梯逻辑）
                            bool wantFallback = root.TryGetProperty("fallback", out var fbEl) && fbEl.GetBoolean();
                            string locAfter = Game1.player.currentLocation.Name;
                            if (wantFallback && locAfter == nm)
                            {
                                var em = locD.GetType().GetMethod("enterMineShaft", bt);
                                if (em != null)
                                {
                                    try { em.Invoke(locD, null); parts.Add("enterMineShaft=ok"); }
                                    catch (Exception ex2) { parts.Add("enterMineShaftErr=" + ex2.Message); }
                                }
                                else parts.Add("enterMineShaft=missing");
                            }
                            this.monitor.Log("descend_ladder: " + string.Join(" ; ", parts) + $" -> now={Game1.player.currentLocation.Name}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("descend_ladder err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "pick_object_at":
                    {
                        try
                        {
                            int px9 = root.GetProperty("x").GetInt32(), py9 = root.GetProperty("y").GetInt32();
                            var locP = Game1.player.currentLocation;
                            var posP = new Microsoft.Xna.Framework.Vector2(px9, py9);
                            StardewValley.Object objP;
                            if (locP.Objects.TryGetValue(posP, out objP) && objP != null)
                            {
                                objP.TileLocation = posP;
                                bool picked = Game1.player.addItemToInventoryBool(objP, true);
                                if (picked) { locP.Objects.Remove(posP); }
                                this.monitor.Log($"pick_object_at ({px9},{py9}) name={objP.Name} picked={picked}", LogLevel.Info);
                            }
                            else this.monitor.Log($"pick_object_at ({px9},{py9}) 没有物体", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("pick_object_at err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "step_tile":
                    {
                        try
                        {
                            int sx9 = root.GetProperty("x").GetInt32(), sy9 = root.GetProperty("y").GetInt32();
                            Game1.player.Position = new Microsoft.Xna.Framework.Vector2(sx9 * 64f + 32f, sy9 * 64f + 32f);
                            this.monitor.Log($"step_tile -> ({sx9},{sy9})", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("step_tile err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "cancel_dialogue":
                    {
                        try
                        {
                            try { Game1.dialogueUp = false; } catch { }
                            try
                            {
                                var bfq = System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                                foreach (var fn2 in new[] { "dialogueBox", "currentQuestionDialogue", "questionDialogue" })
                                {
                                    var fi2 = typeof(Game1).GetField(fn2, bfq);
                                    if (fi2 != null) fi2.SetValue(null, null);
                                    var pi2 = typeof(Game1).GetProperty(fn2, bfq);
                                    if (pi2 != null && pi2.CanWrite) pi2.SetValue(null, null);
                                }
                            }
                            catch { }
                            try
                            {
                                var cd = typeof(Game1).GetField("currentDialogueStack",
                                    System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                                var stack = cd?.GetValue(null);
                                stack?.GetType().GetMethod("Clear")?.Invoke(stack, null);
                            }
                            catch { }

                            try { Game1.activeClickableMenu = null; } catch { }
                            try { Game1.eventUp = false; } catch { }
                            try
                            {
                                var evProp = typeof(StardewValley.GameLocation).GetProperty("currentEvent",
                                    System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                                if (evProp != null && evProp.CanWrite) evProp.SetValue(Game1.currentLocation, null);
                            }
                            catch { }
                            try { Game1.player.UsingTool = false; Game1.player.canMove = true; } catch { }
                            this.monitor.Log("cancel_dialogue done", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("cancel_dialogue err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "exit_menu":
                    {
                        try
                        {
                            for (int i = 0; i < 3; i++)
                            {
                                try { Game1.exitActiveMenu(); } catch { }
                                try { Game1.activeClickableMenu = null; } catch { }
                                try { Game1.player.UsingTool = false; } catch { }
                            }
                            this.monitor.Log("exit_menu done", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("exit_menu err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "fish_hook":
                    {
                        try
                        {
                            var bf6 = System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var pm6 = typeof(Game1).GetMethod("pressUseToolButton", bf6);
                            if (pm6 != null) { pm6.Invoke(null, null); this.monitor.Log("fish_hook: pressUseToolButton", LogLevel.Info); }
                            else this.monitor.Log("fish_hook: 没有 pressUseToolButton", LogLevel.Warn);
                        }
                        catch (Exception ex) { this.monitor.Log("fish_hook err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "fish_hook2":
                    {
                        try
                        {
                            var rod6 = Game1.player.Items.OfType<StardewValley.Tools.FishingRod>().FirstOrDefault();
                            if (rod6 != null)
                            {
                                Game1.player.CurrentToolIndex = Game1.player.Items.IndexOf(rod6);
                                rod6.DoFunction(Game1.player.currentLocation, (int)Game1.player.Tile.X * 64 + 32, (int)Game1.player.Tile.Y * 64 + 32, 1, Game1.player);
                                this.monitor.Log("fish_hook2: rod.DoFunction", LogLevel.Info);
                            }
                        }
                        catch (Exception ex) { this.monitor.Log("fish_hook2 err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "fix_outfit":
                    {
                        try
                        {
                            var bf4 = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            bool did = false;
                            foreach (var mn in new[] { "changeShirt", "changePants", "changePant" })
                            {
                                var m = typeof(StardewValley.Farmer).GetMethod(mn, bf4, null, new[] { typeof(int) }, null);
                                if (m != null)
                                {
                                    int v = mn == "changeShirt" ? 1000 : 0;
                                    m.Invoke(Game1.player, new object[] { v });
                                    did = true;
                                    this.monitor.Log($"fix_outfit: {mn}({v}) ok", LogLevel.Info);
                                }
                            }
                            if (!did) this.monitor.Log("fix_outfit: 没找到换装方法", LogLevel.Warn);
                        }
                        catch (Exception ex) { this.monitor.Log("fix_outfit err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "fish_begin":
                    {
                        try
                        {
                            var rod = Game1.player.Items.OfType<StardewValley.Tools.FishingRod>().FirstOrDefault();
                            if (rod == null) { this.monitor.Log("fish_begin: 没有鱼竿", LogLevel.Warn); break; }
                            Game1.player.CurrentToolIndex = Game1.player.Items.IndexOf(rod);
                            int fx = root.TryGetProperty("x", out var fxEl) ? fxEl.GetInt32() : (int)Game1.player.Tile.X;
                            int fy = root.TryGetProperty("y", out var fyEl) ? fyEl.GetInt32() : (int)Game1.player.Tile.Y;
                            var mi = typeof(StardewValley.Tools.FishingRod).GetMethod("beginUsing",
                                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                            if (mi != null)
                            {
                                Game1.player.UsingTool = true;
                                Game1.player.canMove = false;
                                var res = mi.Invoke(rod, new object[] { Game1.player.currentLocation, fx * 64 + 32, fy * 64 + 32, Game1.player });
                                this.monitor.Log($"fish_begin beginUsing({fx},{fy}) -> {res} inUse={rod.inUse()}", LogLevel.Info);
                            }
                            else this.monitor.Log("fish_begin: beginUsing 不存在", LogLevel.Warn);
                        }
                        catch (Exception ex) { this.monitor.Log("fish_begin err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "leave_mine":
                    {
                        try
                        {
                            if (Game1.currentLocation != null && Game1.currentLocation.Name.StartsWith("UndergroundMine"))
                            {
                                Game1.warpFarmer("Mountain", 30, 20, false);
                                this.monitor.Log("leave_mine -> Mountain", LogLevel.Info);
                            }
                            else this.monitor.Log("leave_mine: 不在矿里", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("leave_mine err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "dump_pause":
                    {
                        try
                        {
                            var parts = new System.Collections.Generic.List<string>();
                            var t = typeof(Game1);
                            foreach (var f in t.GetFields(System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic))
                            {
                                if (f.Name.ToLower().Contains("pause") || f.Name.ToLower().Contains("freeze"))
                                {
                                    try { parts.Add($"F {f.Name}={f.GetValue(null)}"); } catch { }
                                }
                            }
                            foreach (var pr in t.GetProperties(System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic))
                            {
                                if (pr.Name.ToLower().Contains("pause") || pr.Name.ToLower().Contains("freeze"))
                                {
                                    try { parts.Add($"P {pr.Name}={pr.GetValue(null)}"); } catch { }
                                }
                            }
                            this.monitor.Log("PAUSEINFO " + string.Join(" ; ", parts), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_pause err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "force_pass_out":
                    {
                        try
                        {
                            var m = typeof(StardewValley.Farmer).GetMethod("startToPassOut", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                            if (m != null) { m.Invoke(Game1.player, null); this.monitor.Log("force_pass_out: startToPassOut", LogLevel.Info); }
                            else this.monitor.Log("force_pass_out: 方法不存在", LogLevel.Warn);
                        }
                        catch (Exception ex) { this.monitor.Log("force_pass_out err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "force_unpause":
                    {
                        try
                        {
                            Game1.options.pauseWhenOutOfFocus = false;
                            try
                            {
                                var optT = Game1.options.GetType();
                                var sf = optT.GetField("pauseWhenUnfocused",
                                    System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                                if (sf != null) sf.SetValue(null, false);
                            }
                            catch { }
                            var bf = System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            foreach (var fn in new[] { "paused", "isTimePaused", "freezeControls", "viewportFreeze", "eventUp" })
                            {
                                var fi = typeof(Game1).GetField(fn, bf);
                                if (fi != null && fi.FieldType == typeof(bool))
                                {
                                    try { fi.SetValue(null, false); } catch { }
                                }
                            }
                            this.monitor.Log("force_unpause: 已清暂停标记", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("force_unpause err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "enter_house":
                    {
                        try
                        {
                            var hloc = Game1.player.currentLocation;
                            if (hloc.Name == "FarmHouse") { this.monitor.Log("already in house", LogLevel.Info); break; }
                            // 只允许在农舍门口（约 63..66, 14..16）进屋
                            var ht = Game1.player.Tile;
                            bool atDoor = (hloc.Name == "Farm" && ht.X >= 62 && ht.X <= 67 && ht.Y >= 13 && ht.Y <= 17);
                            if (!atDoor) { this.monitor.Log($"enter_house: 不在门口 ({ht.X},{ht.Y})", LogLevel.Warn); break; }
                            Game1.warpFarmer("FarmHouse", 9, 11, false);
                            this.monitor.Log("enter_house -> FarmHouse", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("enter_house err: " + ex.Message, LogLevel.Warn); }
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
                            if (chosen == null)
                            {
                                // 建筑门是 Action 型 warp（农舍门等），不是 warps 列表
                                int awx = -1, awy = -1; string awTarget = null;
                                int W2 = eloc.Map.Layers[0].LayerWidth, H2 = eloc.Map.Layers[0].LayerHeight;
                                for (int yy2 = 0; yy2 < H2 && awTarget == null; yy2++)
                                    for (int xx2 = 0; xx2 < W2; xx2++)
                                    {
                                        string act2 = eloc.doesTileHaveProperty(xx2, yy2, "Action", "Buildings");
                                        if (string.IsNullOrEmpty(act2) || act2.IndexOf("Warp", StringComparison.OrdinalIgnoreCase) < 0) continue;
                                        var pts2 = act2.Split(' ');
                                        if (pts2.Length >= 4 && pts2[3].IndexOf(et, StringComparison.OrdinalIgnoreCase) >= 0)
                                        {
                                            int.TryParse(pts2[1], out awx);
                                            int.TryParse(pts2[2], out awy);
                                            awTarget = pts2[3];
                                            break;
                                        }
                                    }
                                if (awTarget != null)
                                {
                                    Game1.warpFarmer(awTarget, awx, awy, false);
                                    this.monitor.Log($"enter_exit(door) {eloc.Name} -> {awTarget} ({awx},{awy})", LogLevel.Info);
                                    break;
                                }
                                this.monitor.Log($"enter_exit: no warp to {et}", LogLevel.Warn);
                                break;
                            }
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
                            bool foundWarp = false;
                            foreach (var w in wloc.warps)
                            {
                                if (w.TargetName != null && w.TargetName.IndexOf(wt, StringComparison.OrdinalIgnoreCase) >= 0)
                                { wx2 = w.X; wy2 = w.Y; foundWarp = true; break; }
                            }
                            if (!foundWarp)
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
                            if (!foundWarp && wx2 < 0) { this.monitor.Log($"walk_to_target: no exit to {wt} in {wloc.Name}", LogLevel.Warn); break; }
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

                case "use_item_at":
                    {
                        try
                        {
                            string nm3 = root.TryGetProperty("name", out var nm4) ? nm4.GetString() : "";
                            int ix3 = root.GetProperty("x").GetInt32(), iy3 = root.GetProperty("y").GetInt32();
                            int idx3 = -1;
                            for (int i = 0; i < Game1.player.Items.Count; i++)
                            {
                                var it3 = Game1.player.Items[i];
                                if (it3 == null) continue;
                                string dn3 = it3.DisplayName ?? "";
                                if (dn3.IndexOf(nm3, StringComparison.OrdinalIgnoreCase) >= 0
                                    || (it3.Name ?? "").IndexOf(nm3, StringComparison.OrdinalIgnoreCase) >= 0)
                                { idx3 = i; break; }
                            }
                            if (idx3 < 0) { this.monitor.Log($"use_item_at: 没找到 {nm3}", LogLevel.Warn); break; }
                            Game1.player.CurrentToolIndex = idx3;
                            var it4 = Game1.player.Items[idx3];
                            var mi = it4.GetType().GetMethod("DoFunction", new[] {
                                typeof(StardewValley.GameLocation), typeof(int), typeof(int), typeof(int), typeof(StardewValley.Farmer) });
                            if (mi == null) { this.monitor.Log($"use_item_at: {nm3} 没有 DoFunction", LogLevel.Warn); break; }
                            mi.Invoke(it4, new object[] { Game1.player.currentLocation, ix3 * 64 + 32, iy3 * 64 + 32, 1, Game1.player });
                            this.monitor.Log($"used {nm3} at ({ix3},{iy3})", LogLevel.Info);
                            ResetFarmerPose();
                        }
                        catch (Exception ex) { this.monitor.Log("use_item_at err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "dump_objects":
                    {
                        try
                        {
                            var dl2 = Game1.player.currentLocation;
                            var parts = new System.Collections.Generic.List<string>();
                            int n = 0;
                            foreach (var kv in dl2.Objects.Pairs)
                            {
                                if (n++ > 40) break;
                                var o = kv.Value;
                                if (o == null) continue;
                                parts.Add($"{kv.Key.X},{kv.Key.Y}: name={o.Name} display={o.DisplayName} type={o.Type} idx={o.ParentSheetIndex} cat={o.Category} big={o.bigCraftable.Value}");
                            }
                            foreach (var rc in dl2.resourceClumps)
                                parts.Add($"CLUMP {rc.Tile.X},{rc.Tile.Y} idx={rc.parentSheetIndex.Value} w={rc.width.Value} h={rc.height.Value}");
                            this.monitor.Log($"OBJECTS {dl2.Name}: " + string.Join(" | ", parts), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_objects err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "select_item":
                    {
                        try
                        {
                            string nm = root.TryGetProperty("name", out var nmEl) ? nmEl.GetString() : "";
                            int idx2 = -1;
                            for (int i = 0; i < Game1.player.Items.Count; i++)
                            {
                                var it = Game1.player.Items[i];
                                if (it == null) continue;
                                if (it.DisplayName.IndexOf(nm, StringComparison.OrdinalIgnoreCase) >= 0 || it.Name.IndexOf(nm, StringComparison.OrdinalIgnoreCase) >= 0)
                                { idx2 = i; break; }
                            }
                            if (idx2 >= 0)
                            {
                                Game1.player.CurrentToolIndex = idx2;
                                this.monitor.Log($"select_item {nm} -> idx {idx2}", LogLevel.Info);
                            }
                            else this.monitor.Log($"select_item: not found {nm}", LogLevel.Warn);
                        }
                        catch (Exception ex) { this.monitor.Log("select_item err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "attack":
                    {
                        try
                        {
                            int idx9 = -1;
                            for (int i9 = 0; i9 < Game1.player.Items.Count; i9++)
                                if (Game1.player.Items[i9] is StardewValley.Tools.MeleeWeapon) { idx9 = i9; break; }
                            if (idx9 >= 0) Game1.player.CurrentToolIndex = idx9;
                            var locAtk = Game1.player.currentLocation;
                            StardewValley.Monsters.Monster near = null; float best = 9999f;
                            foreach (var ch in locAtk.characters)
                                if (ch is StardewValley.Monsters.Monster m)
                                { float d = (m.Position - Game1.player.Position).Length(); if (d < best) { best = d; near = m; } }
                            if (near != null)
                            {
                                var dv = near.Position - Game1.player.Position;
                                Game1.player.FacingDirection = Math.Abs(dv.X) > Math.Abs(dv.Y) ? (dv.X > 0 ? 1 : 3) : (dv.Y > 0 ? 2 : 0);
                            }
                            var pm9 = typeof(Game1).GetMethod("pressUseToolButton",
                                System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                            if (pm9 != null) { pm9.Invoke(null, null); }
                            this.monitor.Log($"attack: weapon={idx9} target={(near == null ? "none" : near.Name)}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("attack err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "dump_farmer_methods":
                    {
                        try
                        {
                            var bfD = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var namesD = new System.Collections.Generic.List<string>();
                            foreach (var mi in typeof(Farmer).GetMethods(bfD))
                            {
                                string nmD = mi.Name.ToLower();
                                if (nmD.Contains("fall") || nmD.Contains("shaft") || nmD.Contains("jump") || nmD.Contains("descend"))
                                    namesD.Add("F " + mi.Name + "(" + string.Join(",", System.Array.ConvertAll(mi.GetParameters(), pp => pp.ParameterType.Name)) + ")");
                            }
                            foreach (var mi in Game1.currentLocation.GetType().GetMethods(bfD))
                            {
                                string nmD = mi.Name.ToLower();
                                if (nmD.Contains("fall") || nmD.Contains("shaft") || nmD.Contains("jump") || nmD.Contains("descend"))
                                    namesD.Add("M " + mi.Name + "(" + string.Join(",", System.Array.ConvertAll(mi.GetParameters(), pp => pp.ParameterType.Name)) + ")");
                            }
                            try { System.IO.File.WriteAllText(System.IO.Path.Combine(System.IO.Path.GetDirectoryName(typeof(BotManager).Assembly.Location), "farmer_methods.txt"), string.Join("\n", namesD)); } catch { }
                            this.monitor.Log("FARMERM " + string.Join(" ; ", namesD), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_farmer_methods err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "after_fall":
                    {
                        try
                        {
                            var locAF = Game1.player.currentLocation;
                            var bfAF = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var mAF = locAF.GetType().GetMethod("afterFall", bfAF);
                            var partsAF = new System.Collections.Generic.List<string>();
                            try { var isF = locAF.GetType().GetMethod("IsFallHere", bfAF); if (isF != null) partsAF.Add("isFallHere=" + isF.Invoke(locAF, null)); } catch { }
                            if (mAF != null)
                            {
                                mAF.Invoke(locAF, null);
                                partsAF.Add("afterFall=ok");
                            }
                            else partsAF.Add("afterFall=missing");
                            this.monitor.Log("after_fall: " + string.Join(" ; ", partsAF) + $" -> now={Game1.player.currentLocation.Name}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("after_fall err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "next_mine_level":
                    {
                        try
                        {
                            var locN = Game1.player.currentLocation;
                            if (locN is StardewValley.Locations.MineShaft shaftN)
                            {
                                int nextN = shaftN.mineLevel + 1;
                                string nameN = "UndergroundMine" + nextN;
                                var nextLoc = Game1.getLocationFromName(nameN) ?? StardewValley.Locations.MineShaft.GetMine(nameN);
                                if (nextLoc != null)
                                {
                                    Game1.warpFarmer(nameN, 6, 6, false);
                                    this.monitor.Log($"next_mine_level: {shaftN.mineLevel} -> {nextN}", LogLevel.Info);
                                }
                                else this.monitor.Log("next_mine_level: 目标地图不存在 " + nameN, LogLevel.Warn);
                            }
                            else this.monitor.Log("next_mine_level: 不在矿洞", LogLevel.Warn);
                        }
                        catch (Exception ex) { this.monitor.Log("next_mine_level err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "ship_item":
                    {
                        try
                        {
                            string nmS = root.TryGetProperty("name", out var nS) ? nS.GetString() : "";
                            int cntS = root.TryGetProperty("count", out var cS) ? cS.GetInt32() : 1;
                            var farmS = Game1.getFarm();
                            StardewValley.Objects.Chest binS = null;
                            object bldS = null;
                            var bfS = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            foreach (var bdS in farmS.buildings)
                            {
                                string tS = bdS.buildingType?.Value ?? "";
                                if (!tS.Contains("Shipping")) continue;
                                bldS = bdS;
                                var gm = bdS.GetType().GetMethod("GetBuildingChest", bfS);
                                if (gm != null)
                                {
                                    foreach (var key in new[] { "", "Shipping Bin", "ShippingBin", "Bin" })
                                    {
                                        try { binS = gm.Invoke(bdS, new object[] { key }) as StardewValley.Objects.Chest; } catch { binS = null; }
                                        if (binS != null) break;
                                    }
                                }
                                break;
                            }
                            if (binS == null) { this.monitor.Log("ship_item: 找不到发货箱（building）", LogLevel.Warn); break; }
                            int movedS = 0;
                            for (int iS = Game1.player.Items.Count - 1; iS >= 0 && movedS < cntS; iS--)
                            {
                                var itS = Game1.player.Items[iS];
                                if (itS == null) continue;
                                string d9 = itS.DisplayName ?? itS.Name ?? "";
                                if (!d9.Contains(nmS)) continue;
                                itS.Stack -= 1;
                                var one = itS.getOne();
                                one.Stack = 1;
                                binS.Items.Add(one);
                                if (itS.Stack <= 0) Game1.player.Items[iS] = null;
                                movedS++;
                            }
                            this.monitor.Log($"ship_item: 发货 {movedS} 个 {nmS}（箱内共 {binS.Items.Count} 件）", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("ship_item err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "dump_shipping":
                    {
                        try
                        {
                            var farmD = Game1.getFarm();
                            var partsD = new System.Collections.Generic.List<string>();
                            foreach (var kv in farmD.Objects.Pairs)
                            {
                                if (kv.Value is StardewValley.Objects.Chest chD)
                                {
                                    string dnD = (chD.DisplayName ?? chD.Name ?? "").ToLower();
                                    if (dnD.Contains("shipping") || dnD.Contains("发货") || dnD.Contains("装载"))
                                    {
                                        partsD.Add($"bin@{kv.Key.X},{kv.Key.Y}");
                                        foreach (var itD in chD.Items)
                                            if (itD != null) partsD.Add($"{itD.DisplayName ?? itD.Name}x{itD.Stack}");
                                    }
                                }
                            }
                            this.monitor.Log("SHIPPING " + string.Join(" | ", partsD), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_shipping err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "dump_farm_objects":
                    {
                        try
                        {
                            var farmO = Game1.getFarm();
                            var namesO = new System.Collections.Generic.HashSet<string>();
                            foreach (var kv in farmO.Objects.Pairs)
                            {
                                var o = kv.Value;
                                if (o == null) continue;
                                namesO.Add($"{o.Name}|{o.DisplayName}|{o.Type}|{o.GetType().Name}|idx{o.ParentSheetIndex}");
                            }
                            this.monitor.Log("FARMOBJ " + string.Join(" ; ", namesO), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_farm_objects err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "dump_buildings":
                    {
                        try
                        {
                            var farmB = Game1.getFarm();
                            var partsB = new System.Collections.Generic.List<string>();
                            foreach (var bd in farmB.buildings)
                            {
                                string tB = bd.buildingType?.Value ?? bd.GetType().Name;
                                partsB.Add($"bld:{tB}@{bd.tileX},{bd.tileY}");
                                var bfB = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                                foreach (var mi in bd.GetType().GetMethods(bfB))
                                    if (mi.Name.ToLower().Contains("chest")) partsB.Add("  m:" + mi.Name + "(" + string.Join(",", System.Array.ConvertAll(mi.GetParameters(), pp => pp.ParameterType.Name)) + ")");
                                foreach (var pr in bd.GetType().GetProperties(bfB))
                                    if (pr.Name.ToLower().Contains("chest")) partsB.Add("  p:" + pr.Name);
                            }
                            this.monitor.Log("BLD " + string.Join(" | ", partsB), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_buildings err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "ship_item2":
                    {
                        try
                        {
                            string nmS = root.TryGetProperty("name", out var nS) ? nS.GetString() : "";
                            int cntS = root.TryGetProperty("count", out var cS) ? cS.GetInt32() : 1;
                            var farmS = Game1.getFarm();
                            var bfS = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var partsS = new System.Collections.Generic.List<string>();
                            StardewValley.Objects.Chest binS = null;
                            foreach (var mName in new[] { "getShippingBin", "GetShippingBin", "getShippingChest" })
                            {
                                var mi = farmS.GetType().GetMethod(mName, bfS);
                                if (mi == null) continue;
                                try
                                {
                                    var args = System.Array.ConvertAll(mi.GetParameters(), pp => (object)Game1.player);
                                    binS = mi.Invoke(farmS, args.Length == 0 ? null : args) as StardewValley.Objects.Chest;
                                    partsS.Add(mName + "=" + (binS != null));
                                }
                                catch (Exception e1) { partsS.Add(mName + "Err:" + e1.Message); }
                                if (binS != null) break;
                            }
                            if (binS == null)
                            {
                                var fldC = farmS.GetType().GetField("shippingBin", bfS);
                                if (fldC != null) binS = fldC.GetValue(farmS) as StardewValley.Objects.Chest;
                                partsS.Add("field=" + (binS != null));
                            }
                            if (binS == null) { this.monitor.Log("ship_item2: 没找到发货箱 " + string.Join(";", partsS), LogLevel.Warn); break; }
                            int movedS = 0;
                            for (int iS = Game1.player.Items.Count - 1; iS >= 0 && movedS < cntS; iS--)
                            {
                                var itS = Game1.player.Items[iS];
                                if (itS == null) continue;
                                string d9 = itS.DisplayName ?? itS.Name ?? "";
                                if (!d9.Contains(nmS)) continue;
                                itS.Stack -= 1;
                                var one = itS.getOne();
                                one.Stack = 1;
                                binS.Items.Add(one);
                                if (itS.Stack <= 0) Game1.player.Items[iS] = null;
                                movedS++;
                            }
                            this.monitor.Log($"ship_item2: 发货 {movedS} 个 {nmS}（箱内 {binS.Items.Count} 件）", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("ship_item2 err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "open_bin":
                    {
                        try
                        {
                            var farmO = Game1.getFarm();
                            var bfO = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var partsO = new System.Collections.Generic.List<string>();
                            foreach (var bdO in farmO.buildings)
                            {
                                string tO = bdO.buildingType?.Value ?? "";
                                if (!tO.Contains("Shipping")) continue;
                                var pmO = bdO.GetType().GetMethod("PerformBuildingChestAction", bfO);
                                var gmO = bdO.GetType().GetMethod("GetBuildingChest", bfO);
                                foreach (var key in new[] { "", "Shipping Bin", "ShippingBin" })
                                {
                                    try { pmO?.Invoke(bdO, new object[] { key, Game1.player }); partsO.Add("perform(" + key + ")=ok"); } catch (Exception e1) { partsO.Add("perform(" + key + ")Err:" + e1.Message); }
                                    try { var chO = gmO?.Invoke(bdO, new object[] { key }) as StardewValley.Objects.Chest; partsO.Add("chest(" + key + ")=" + (chO != null) + (chO != null ? "#" + chO.Items.Count : "")); } catch { }
                                }
                                break;
                            }
                            partsO.Add("menu=" + (Game1.activeClickableMenu?.GetType().Name ?? "none"));
                            this.monitor.Log("open_bin: " + string.Join(" ; ", partsO), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("open_bin err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "bin_data":
                    {
                        try
                        {
                            var farmB = Game1.getFarm();
                            var bfB = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var partsB = new System.Collections.Generic.List<string>();
                            foreach (var bdB in farmB.buildings)
                            {
                                string tB = bdB.buildingType?.Value ?? "";
                                if (!tB.Contains("Shipping")) continue;
                                var gmB = bdB.GetType().GetMethod("GetBuildingChestData", bfB);
                                foreach (var key in new[] { "", "Shipping Bin", "ShippingBin", tB })
                                {
                                    try
                                    {
                                        var res = gmB?.Invoke(bdB, new object[] { key });
                                        partsB.Add($"data({key})={(res == null ? "null" : res.GetType().Name)}");
                                        if (res is StardewValley.Objects.Chest chB)
                                        {
                                            int movedB = 0;
                                            for (int iB = Game1.player.Items.Count - 1; iB >= 0 && movedB < 5; iB--)
                                            {
                                                var itB = Game1.player.Items[iB];
                                                if (itB == null || !((itB.DisplayName ?? itB.Name ?? "").Contains("石头"))) continue;
                                                itB.Stack -= 1; var oneB = itB.getOne(); oneB.Stack = 1; chB.Items.Add(oneB);
                                                if (itB.Stack <= 0) Game1.player.Items[iB] = null;
                                                movedB++;
                                            }
                                            partsB.Add($"shippedTo({key})={movedB},binItems={chB.Items.Count}");
                                        }
                                    }
                                    catch (Exception eB) { partsB.Add($"data({key})Err:{eB.Message}"); }
                                }
                                break;
                            }
                            this.monitor.Log("bin_data: " + string.Join(" ; ", partsB), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("bin_data err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "ship_item3":
                    {
                        try
                        {
                            string nmS = root.TryGetProperty("name", out var nS) ? nS.GetString() : "";
                            int cntS = root.TryGetProperty("count", out var cS) ? cS.GetInt32() : 1;
                            var farmS = Game1.getFarm();
                            var bfS = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            StardewValley.Objects.Chest binS = null;
                            object bldObj = null;
                            foreach (var bdS in farmS.buildings)
                            {
                                string tS = bdS.buildingType?.Value ?? "";
                                if (!tS.Contains("Shipping")) continue;
                                bldObj = bdS;
                                break;
                            }
                            if (bldObj != null)
                            {
                                var queue = new System.Collections.Generic.Queue<(object obj, int depth)>();
                                queue.Enqueue((bldObj, 0));
                                while (queue.Count > 0 && binS == null)
                                {
                                    var pair = queue.Dequeue();
                                    object cur = pair.obj; int depth = pair.depth;
                                    if (cur == null || depth > 2) continue;
                                    if (cur is StardewValley.Objects.Chest chCur) { binS = chCur; break; }
                                    foreach (var f in cur.GetType().GetFields(bfS))
                                    {
                                        object v = null;
                                        try { v = f.GetValue(cur); } catch { continue; }
                                        if (v == null) continue;
                                        if (v is StardewValley.Objects.Chest cc) { binS = cc; break; }
                                        if (v is System.Collections.IDictionary dictS)
                                        {
                                            foreach (var dv in dictS.Values)
                                                if (dv is StardewValley.Objects.Chest dc) { binS = dc; break; }
                                            if (binS != null) break;
                                        }
                                        if (v is System.Collections.IEnumerable enS && !(v is string))
                                        {
                                            foreach (var ev in enS)
                                                if (ev is StardewValley.Objects.Chest ec) { binS = ec; break; }
                                            if (binS != null) break;
                                        }
                                        if (depth < 2 && v.GetType().IsClass && !(v is string))
                                            queue.Enqueue((v, depth + 1));
                                    }
                                }
                            }
                            if (binS == null) { this.monitor.Log("ship_item3: 还是没找到发货箱", LogLevel.Warn); break; }
                            int movedS = 0;
                            for (int iS = Game1.player.Items.Count - 1; iS >= 0 && movedS < cntS; iS--)
                            {
                                var itS = Game1.player.Items[iS];
                                if (itS == null) continue;
                                string d9 = itS.DisplayName ?? itS.Name ?? "";
                                if (!d9.Contains(nmS)) continue;
                                itS.Stack -= 1;
                                var one = itS.getOne();
                                one.Stack = 1;
                                binS.Items.Add(one);
                                if (itS.Stack <= 0) Game1.player.Items[iS] = null;
                                movedS++;
                            }
                            this.monitor.Log($"ship_item3: 发货 {movedS} 个 {nmS}（箱内 {binS.Items.Count} 件）", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("ship_item3 err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "dump_building_fields":
                    {
                        try
                        {
                            var farmD = Game1.getFarm();
                            var bfD = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var partsD = new System.Collections.Generic.List<string>();
                            foreach (var bdD in farmD.buildings)
                            {
                                string tD = bdD.buildingType?.Value ?? "";
                                if (!tD.Contains("Shipping")) continue;
                                for (var t = bdD.GetType(); t != null && t != typeof(object); t = t.BaseType)
                                {
                                    foreach (var f in t.GetFields(bfD))
                                        partsD.Add("F " + t.Name + "." + f.Name + ":" + f.FieldType.Name);
                                    foreach (var p in t.GetProperties(bfD))
                                        partsD.Add("P " + t.Name + "." + p.Name + ":" + p.PropertyType.Name);
                                }
                                break;
                            }
                            this.monitor.Log("BLDF " + string.Join(" | ", partsD), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_building_fields err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "ship_final":
                    {
                        try
                        {
                            string nmF = root.TryGetProperty("name", out var nF) ? nF.GetString() : "";
                            int cntF = root.TryGetProperty("count", out var cF) ? cF.GetInt32() : 1;
                            var farmF = Game1.getFarm();
                            var bfF = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            StardewValley.Objects.Chest binF = null;
                            var partsF = new System.Collections.Generic.List<string>();
                            foreach (var bdF in farmF.buildings)
                            {
                                string tF = bdF.buildingType?.Value ?? "";
                                if (!tF.Contains("Shipping")) continue;
                                var fldF = bdF.GetType().GetField("buildingChests", bfF);
                                if (fldF == null) { partsF.Add("no buildingChests field"); break; }
                                var listF = fldF.GetValue(bdF);
                                if (listF is System.Collections.IEnumerable enF)
                                {
                                    int idxF = 0;
                                    foreach (var itemF in enF)
                                    {
                                        partsF.Add("item" + idxF + ":" + (itemF?.GetType().Name ?? "null"));
                                        object valF = itemF;
                                        if (valF != null)
                                        {
                                            var prF = valF.GetType().GetProperty("Value");
                                            if (prF != null) { try { valF = prF.GetValue(valF); } catch { } }
                                        }
                                        if (valF is StardewValley.Objects.Chest chF) { binF = chF; break; }
                                        idxF++;
                                    }
                                }
                                break;
                            }
                            if (binF == null) { this.monitor.Log("ship_final: 没找到箱 " + string.Join(";", partsF), LogLevel.Warn); break; }
                            int movedF = 0;
                            for (int iF = Game1.player.Items.Count - 1; iF >= 0 && movedF < cntF; iF--)
                            {
                                var itF = Game1.player.Items[iF];
                                if (itF == null) continue;
                                string dF = itF.DisplayName ?? itF.Name ?? "";
                                if (!dF.Contains(nmF)) continue;
                                itF.Stack -= 1;
                                var oneF = itF.getOne();
                                oneF.Stack = 1;
                                binF.Items.Add(oneF);
                                if (itF.Stack <= 0) Game1.player.Items[iF] = null;
                                movedF++;
                            }
                            this.monitor.Log($"ship_final: 发货 {movedF} 个 {nmF}（箱内 {binF.Items.Count} 件）", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("ship_final err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "dump_shop_methods":
                    {
                        try
                        {
                            var menuD = Game1.activeClickableMenu;
                            var partsD = new System.Collections.Generic.List<string>();
                            partsD.Add("menu=" + (menuD?.GetType().Name ?? "none"));
                            if (menuD != null)
                            {
                                var bfD = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                                foreach (var mi in menuD.GetType().GetMethods(bfD))
                                    if (mi.Name.ToLower().Contains("sell")) partsD.Add("m:" + mi.Name + "(" + string.Join(",", System.Array.ConvertAll(mi.GetParameters(), pp => pp.ParameterType.Name)) + ")");
                                foreach (var pi in menuD.GetType().GetProperties(bfD))
                                    if (pi.Name.ToLower().Contains("shop") || pi.Name.ToLower().Contains("stock")) partsD.Add("p:" + pi.Name);
                            }
                            this.monitor.Log("SHOPM " + string.Join(" | ", partsD), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_shop_methods err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "shop_sell":
                    {
                        try
                        {
                            string nmS = root.TryGetProperty("name", out var nS) ? nS.GetString() : "";
                            int keepS = root.TryGetProperty("keep", out var kS) ? kS.GetInt32() : 0;
                            var menuS = Game1.activeClickableMenu;
                            if (menuS == null) { this.monitor.Log("shop_sell: 没有打开的商店", LogLevel.Warn); break; }
                            var bfS = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            System.Reflection.MethodInfo sellM = null;
                            foreach (var mi in menuS.GetType().GetMethods(bfS))
                            {
                                if (!mi.Name.ToLower().Contains("sell")) continue;
                                var ps = mi.GetParameters();
                                if (ps.Length >= 1 && ps.Length <= 2 && ps[0].ParameterType.Name == "ISalable") { sellM = mi; break; }
                            }
                            var partsS = new System.Collections.Generic.List<string>();
                            partsS.Add("sellM=" + (sellM?.Name ?? "null") + " beforeMoney=" + Game1.player.Money);
                            int soldS = 0;
                            if (sellM != null)
                            {
                                var invS = Game1.player.Items;
                                for (int iS = invS.Count - 1; iS >= 0; iS--)
                                {
                                    var itS = invS[iS];
                                    if (itS == null) continue;
                                    string dS = itS.DisplayName ?? itS.Name ?? "";
                                    if (!dS.Contains(nmS)) continue;
                                    if (itS.Stack <= keepS) continue;
                                    int stackBefore = itS.Stack;
                                    var args = sellM.GetParameters().Length == 1 ? new object[] { itS } : new object[] { itS, true };
                                    try { sellM.Invoke(menuS, args); } catch (Exception e2) { partsS.Add("sellErr:" + e2.Message); break; }
                                    soldS += stackBefore - (invS[iS]?.Stack ?? 0);
                                    partsS.Add($"{dS}:{stackBefore}->{(invS[iS]?.Stack ?? 0)}");
                                }
                            }
                            partsS.Add("soldUnits=" + soldS + " afterMoney=" + Game1.player.Money);
                            this.monitor.Log("shop_sell: " + string.Join(" ; ", partsS), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("shop_sell err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "open_shop_here":
                    {
                        try
                        {
                            int ox = root.GetProperty("x").GetInt32(), oy = root.GetProperty("y").GetInt32();
                            var locO = Game1.player.currentLocation;
                            var caO = locO.GetType().GetMethod("checkAction",
                                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                            object resO = null;
                            if (caO != null)
                            {
                                var argsO = new object[] { new xTile.Dimensions.Location(ox, oy), Game1.viewport, Game1.player };
                                resO = caO.Invoke(locO, argsO);
                            }
                            this.monitor.Log($"open_shop_here: ({ox},{oy}) res={resO} menu={(Game1.activeClickableMenu?.GetType().Name ?? "none")}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("open_shop_here err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "talk_npc":
                    {
                        try
                        {
                            string nmT = root.TryGetProperty("name", out var nT) ? nT.GetString() : "";
                            var locT = Game1.player.currentLocation;
                            StardewValley.NPC npcT = null;
                            foreach (var chT in locT.characters)
                            {
                                string dT = chT.Name ?? "";
                                if (dT.Contains(nmT)) { npcT = chT; break; }
                            }
                            if (npcT == null) { this.monitor.Log("talk_npc: 找不到 NPC " + nmT, LogLevel.Warn); break; }
                            var bfT = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var partsT = new System.Collections.Generic.List<string>();
                            bool done = false;
                            foreach (var mi in npcT.GetType().GetMethods(bfT))
                            {
                                if (mi.Name != "checkAction") continue;
                                var ps = mi.GetParameters();
                                if (ps.Length != 2) continue;
                                try
                                {
                                    mi.Invoke(npcT, new object[] { Game1.player, locT });
                                    partsT.Add("checkAction2=ok");
                                    done = true;
                                    break;
                                }
                                catch (Exception e3) { partsT.Add("err:" + e3.Message); }
                            }
                            if (!done)
                            {
                                // 退而求其次：直接调用商店
                                var shopM = npcT.GetType().GetMethod("openShopMenu", bfT);
                                if (shopM != null)
                                {
                                    var ps2 = shopM.GetParameters();
                                    object[] args2 = ps2.Length == 0 ? null : new object[] { "" };
                                    try { shopM.Invoke(npcT, args2); partsT.Add("openShopMenu=ok"); } catch (Exception e4) { partsT.Add("openShopErr:" + e4.Message); }
                                }
                                else partsT.Add("no checkAction/openShopMenu");
                            }
                            partsT.Add("menu=" + (Game1.activeClickableMenu?.GetType().Name ?? "none"));
                            this.monitor.Log("talk_npc: " + string.Join(" ; ", partsT), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("talk_npc err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "force_npc_shop":
                    {
                        try
                        {
                            string nmS2 = root.TryGetProperty("name", out var nS2) ? nS2.GetString() : "";
                            var locS2 = Game1.player.currentLocation;
                            StardewValley.NPC npcS2 = null;
                            foreach (var chS2 in locS2.characters)
                                if ((chS2.Name ?? "").Contains(nmS2)) { npcS2 = chS2; break; }
                            if (npcS2 == null) { this.monitor.Log("force_npc_shop: 找不到 NPC " + nmS2, LogLevel.Warn); break; }
                            var bfS2 = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var partsS2 = new System.Collections.Generic.List<string>();
                            var mShop = npcS2.GetType().GetMethod("openShopMenu", bfS2);
                            if (mShop != null)
                            {
                                var psS2 = mShop.GetParameters();
                                try
                                {
                                    object[] argsS2 = psS2.Length == 0 ? null : new object[] { psS2[0].ParameterType == typeof(string) ? (object)"" : Activator.CreateInstance(psS2[0].ParameterType) };
                                    mShop.Invoke(npcS2, argsS2);
                                    partsS2.Add("openShopMenu=ok");
                                }
                                catch (Exception eS2) { partsS2.Add("err:" + eS2.Message); }
                            }
                            else partsS2.Add("no openShopMenu");
                            partsS2.Add("menu=" + (Game1.activeClickableMenu?.GetType().Name ?? "none"));
                            this.monitor.Log("force_npc_shop: " + string.Join(" ; ", partsS2), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("force_npc_shop err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "open_shop_id":
                    {
                        try
                        {
                            string sid = root.TryGetProperty("shopId", out var sidEl) ? sidEl.GetString() : "SeedShop";
                            var bfO = System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var partsO = new System.Collections.Generic.List<string>();
                            bool opened = false;
                            foreach (var tO in new[] { typeof(StardewValley.Utility), Game1.currentLocation.GetType() })
                            {
                                foreach (var mi in tO.GetMethods(bfO | System.Reflection.BindingFlags.Instance))
                                {
                                    string nm = mi.Name.ToLower();
                                    if (!(nm.Contains("openshop") || nm.Contains("showshop"))) continue;
                                    var ps = mi.GetParameters();
                                    if (ps.Length < 1 || ps[0].ParameterType != typeof(string)) continue;
                                    try
                                    {
                                        object targetObj = mi.IsStatic ? null : Game1.currentLocation;
                                        var args = new object[ps.Length];
                                        args[0] = sid;
                                        for (int i = 1; i < ps.Length; i++)
                                            args[i] = ps[i].ParameterType.IsValueType ? Activator.CreateInstance(ps[i].ParameterType) : null;
                                        mi.Invoke(targetObj, args);
                                        partsO.Add(tO.Name + "." + mi.Name + "=ok");
                                        opened = true;
                                        break;
                                    }
                                    catch (Exception e5) { partsO.Add(tO.Name + "." + mi.Name + "Err:" + e5.Message); }
                                }
                                if (opened) break;
                            }
                            partsO.Add("menu=" + (Game1.activeClickableMenu?.GetType().Name ?? "none"));
                            this.monitor.Log("open_shop_id: " + string.Join(" ; ", partsO), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("open_shop_id err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                                case "auto_sell":
                    {
                        try
                        {
                            string shopIdS = root.TryGetProperty("shopId", out var sEl) ? sEl.GetString() : "SeedShop";
                            string nmS = root.TryGetProperty("name", out var nEl) ? nEl.GetString() : "";
                            int keepS = root.TryGetProperty("keep", out var kEl) ? kEl.GetInt32() : 0;
                            int money0 = Game1.player.Money;
                            var tUtil = typeof(StardewValley.Utility);
                            System.Reflection.MethodInfo mOpen = null;
                            foreach (var mi in tUtil.GetMethods(System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic))
                            {
                                if (mi.Name != "TryOpenShopMenu") continue;
                                var pp = mi.GetParameters();
                                if (pp.Length >= 1 && pp[0].ParameterType == typeof(string)) { mOpen = mi; break; }
                            }
                            if (mOpen == null) { this.monitor.Log("auto_sell: 没有 TryOpenShopMenu", LogLevel.Warn); break; }
                            var psO = mOpen.GetParameters();
                            var argsO = new object[psO.Length];
                            argsO[0] = shopIdS;
                            for (int i = 1; i < psO.Length; i++) argsO[i] = psO[i].ParameterType.IsValueType ? Activator.CreateInstance(psO[i].ParameterType) : null;
                            mOpen.Invoke(null, argsO);
                            var menuS = Game1.activeClickableMenu;
                            if (menuS == null || menuS.GetType().Name != "ShopMenu") { this.monitor.Log("auto_sell: 商店没打开", LogLevel.Warn); break; }
                            var bfS = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var partsS = new System.Collections.Generic.List<string>();
                            object invMenu = null;
                            for (var t = menuS.GetType(); t != null && t != typeof(object) && invMenu == null; t = t.BaseType)
                            {
                                var f = t.GetField("inventory", bfS);
                                if (f != null) { var v = f.GetValue(menuS); if (v != null && v.GetType().Name.Contains("InventoryMenu")) invMenu = v; }
                            }
                            partsS.Add("money0=" + money0 + " invMenu=" + (invMenu?.GetType().Name ?? "null"));
                            var fInv = invMenu?.GetType().GetField("inventory", bfS);
                            var slots = fInv?.GetValue(invMenu) as System.Collections.IList;
                            partsS.Add("slots=" + (slots?.Count ?? -1));
                            var clickM = menuS.GetType().GetMethod("receiveLeftClick", bfS);
                            try
                            {
                                var xp = menuS.GetType().GetProperty("xPositionOnScreen", bfS);
                                var yp = menuS.GetType().GetProperty("yPositionOnScreen", bfS);
                                partsS.Add($"menuPos=({xp?.GetValue(menuS)},{yp?.GetValue(menuS)})");
                                if (slots != null && slots.Count > 0)
                                {
                                    var c0 = slots[0];
                                    var b0 = c0.GetType().GetProperty("bounds")?.GetValue(c0) ?? c0.GetType().GetField("bounds")?.GetValue(c0);
                                    partsS.Add("slot0Bounds=" + b0);
                                }
                            }
                            catch { }
                            // 把目标物品槽位写文件（供外面真实鼠标点击卖）
                            try
                            {
                                var slotLines = new System.Collections.Generic.List<string>();
                                if (slots != null)
                                {
                                    for (int i = 0; i < slots.Count && i < Game1.player.Items.Count; i++)
                                    {
                                        var it = Game1.player.Items[i];
                                        if (it == null) continue;
                                        string d = it.DisplayName ?? it.Name ?? "";
                                        if (!d.Contains(nmS)) continue;
                                        var comp = slots[i];
                                        var b = comp.GetType().GetProperty("bounds")?.GetValue(comp) ?? comp.GetType().GetField("bounds")?.GetValue(comp);
                                        slotLines.Add($"{i}|{d}|{it.Stack}|{b}");
                                    }
                                }
                                var slotPath = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(typeof(BotManager).Assembly.Location), "shop_slots.txt");
                                System.IO.File.WriteAllText(slotPath, string.Join("\n", slotLines));
                            }
                            catch { }
                            int clicks = 0;
                            if (clickM != null && slots != null)
                            {
                                for (int attempt = 0; attempt < 80; attempt++)
                                {
                                    int slotIdx = -1;
                                    for (int i = 0; i < Game1.player.Items.Count && i < slots.Count; i++)
                                    {
                                        var it = Game1.player.Items[i];
                                        if (it == null) continue;
                                        string d = it.DisplayName ?? it.Name ?? "";
                                        if (!d.Contains(nmS)) continue;
                                        if (it.Stack <= keepS) continue;
                                        slotIdx = i; break;
                                    }
                                    if (slotIdx < 0) break;
                                    var comp = slots[slotIdx];
                                    var boundsP = comp.GetType().GetProperty("bounds");
                                    object boundsO = boundsP != null ? boundsP.GetValue(comp) : comp.GetType().GetField("bounds")?.GetValue(comp);
                                    if (boundsO == null) { partsS.Add("no bounds"); break; }
                                    var r = (Microsoft.Xna.Framework.Rectangle)boundsO;
                                    int mPrev = Game1.player.Money;
                                    try { clickM.Invoke(menuS, new object[] { r.Center.X, r.Center.Y, true }); } catch (Exception e6) { partsS.Add("clickErr:" + e6.Message); break; }
                                    if (Game1.player.Money == mPrev && clicks < 2)
                                    {
                                        // 尝试带 viewport 偏移
                                        try { clickM.Invoke(menuS, new object[] { r.Center.X + Game1.viewport.X, r.Center.Y + Game1.viewport.Y, true }); } catch { }
                                        if (Game1.player.Money != mPrev) partsS.Add("vpOffsetWorked");
                                    }
                                    if (Game1.player.Money == mPrev && clicks == 0)
                                    {
                                        var heldM = menuS.GetType().GetMethod("leftClickHeld", bfS);
                                        var relM = menuS.GetType().GetMethod("releaseLeftClick", bfS);
                                        try
                                        {
                                            heldM?.Invoke(menuS, new object[] { r.Center.X, r.Center.Y });
                                            relM?.Invoke(menuS, new object[] { r.Center.X, r.Center.Y });
                                        }
                                        catch { }
                                        if (Game1.player.Money != mPrev) partsS.Add("heldReleaseWorked");
                                    }
                                    clicks++;
                                    if (Game1.player.Money != mPrev) { partsS.Add("soldAtClick" + clicks); break; }
                                }
                            }
                            partsS.Add($"clicks={clicks} money={Game1.player.Money} (+{Game1.player.Money - money0})");
                            this.monitor.Log("auto_sell: " + string.Join(" ; ", partsS), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("auto_sell err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

case "shop_click_sell":
                    {
                        try
                        {
                            string nmS = root.TryGetProperty("name", out var nS) ? nS.GetString() : "";
                            int keepS = root.TryGetProperty("keep", out var kS) ? kS.GetInt32() : 0;
                            var menuS = Game1.activeClickableMenu;
                            if (menuS == null || menuS.GetType().Name != "ShopMenu") { this.monitor.Log("shop_click_sell: 商店没开", LogLevel.Warn); break; }
                            var bfS = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            int money0 = Game1.player.Money;
                            var partsS = new System.Collections.Generic.List<string>();
                            // 找 InventoryMenu
                            object invMenu = null;
                            for (var t = menuS.GetType(); t != null && t != typeof(object) && invMenu == null; t = t.BaseType)
                            {
                                var f = t.GetField("inventory", bfS);
                                if (f != null) { invMenu = f.GetValue(menuS); if (invMenu != null && invMenu.GetType().Name.Contains("InventoryMenu")) break; invMenu = null; }
                            }
                            partsS.Add("invMenu=" + (invMenu?.GetType().Name ?? "null"));
                            if (invMenu == null) { this.monitor.Log("shop_click_sell: 找不到 InventoryMenu", LogLevel.Warn); break; }
                            var fInv = invMenu.GetType().GetField("inventory", bfS);
                            var slots = fInv?.GetValue(invMenu) as System.Collections.IList;
                            partsS.Add("slots=" + (slots?.Count ?? -1));
                            var clickM = menuS.GetType().GetMethod("receiveLeftClick", bfS);
                            if (clickM == null) { this.monitor.Log("shop_click_sell: 没有 receiveLeftClick", LogLevel.Warn); break; }
                            int clicks = 0;
                            for (int attempt = 0; attempt < 60; attempt++)
                            {
                                // 找还有目标物品且超过 keep 的槽位
                                int slotIdx = -1;
                                for (int i = 0; i < Game1.player.Items.Count; i++)
                                {
                                    var it = Game1.player.Items[i];
                                    if (it == null) continue;
                                    string d = it.DisplayName ?? it.Name ?? "";
                                    if (!d.Contains(nmS)) continue;
                                    if (it.Stack <= keepS) continue;
                                    if (slots != null && i < slots.Count) { slotIdx = i; break; }
                                }
                                if (slotIdx < 0) break;
                                var comp = slots[slotIdx];
                                var boundsP = comp.GetType().GetProperty("bounds");
                                object boundsO = boundsP != null ? boundsP.GetValue(comp) : comp.GetType().GetField("bounds")?.GetValue(comp);
                                if (boundsO == null) { partsS.Add("no bounds"); break; }
                                var r = (Microsoft.Xna.Framework.Rectangle)boundsO;
                                try { clickM.Invoke(menuS, new object[] { r.Center.X, r.Center.Y, true }); } catch (Exception e6) { partsS.Add("clickErr:" + e6.Message); break; }
                                clicks++;
                                System.Threading.Thread.Sleep(20);
                            }
                            partsS.Add($"clicks={clicks} money={Game1.player.Money} (+{Game1.player.Money - money0})");
                            this.monitor.Log("shop_click_sell: " + string.Join(" ; ", partsS), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("shop_click_sell err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "dump_shop_slots":
                    {
                        try
                        {
                            string nmS = root.TryGetProperty("name", out var nS) ? nS.GetString() : "";
                            var menuS = Game1.activeClickableMenu;
                            var lines = new System.Collections.Generic.List<string>();
                            lines.Add("menu=" + (menuS?.GetType().Name ?? "none"));
                            if (menuS != null && menuS.GetType().Name == "ShopMenu")
                            {
                                var bfS = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                                object invMenu = null;
                                for (var t = menuS.GetType(); t != null && t != typeof(object) && invMenu == null; t = t.BaseType)
                                {
                                    var f = t.GetField("inventory", bfS);
                                    if (f != null) { var v = f.GetValue(menuS); if (v != null && v.GetType().Name.Contains("InventoryMenu")) invMenu = v; }
                                }
                                var slots = invMenu?.GetType().GetField("inventory", bfS)?.GetValue(invMenu) as System.Collections.IList;
                                lines.Add("slots=" + (slots?.Count ?? -1));
                                if (slots != null)
                                {
                                    for (int i = 0; i < slots.Count && i < Game1.player.Items.Count; i++)
                                    {
                                        var it = Game1.player.Items[i];
                                        if (it == null) continue;
                                        string d = it.DisplayName ?? it.Name ?? "";
                                        if (!d.Contains(nmS)) continue;
                                        var comp = slots[i];
                                        var b = comp.GetType().GetProperty("bounds")?.GetValue(comp) ?? comp.GetType().GetField("bounds")?.GetValue(comp);
                                        lines.Add($"{i}|{d}|{it.Stack}|{b}");
                                    }
                                }
                            }
                            var path = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(typeof(BotManager).Assembly.Location), "shop_slots.txt");
                            System.IO.File.WriteAllText(path, string.Join("\n", lines));
                            this.monitor.Log("dump_shop_slots: " + lines.Count + " lines", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_shop_slots err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "plant_seed":
                    {
                        try
                        {
                            string nmP = root.TryGetProperty("name", out var nP) ? nP.GetString() : "";
                            int pxP = root.GetProperty("x").GetInt32(), pyP = root.GetProperty("y").GetInt32();
                            var locP = Game1.player.currentLocation;
                            var posP = new Microsoft.Xna.Framework.Vector2(pxP, pyP);
                            if (!locP.terrainFeatures.TryGetValue(posP, out var featP) || !(featP is StardewValley.TerrainFeatures.HoeDirt dirtP))
                            { this.monitor.Log($"plant_seed: ({pxP},{pyP}) 不是耕地", LogLevel.Warn); break; }
                            StardewValley.Object seedP = null; int idxP = -1;
                            for (int iP = 0; iP < Game1.player.Items.Count; iP++)
                            {
                                var itP = Game1.player.Items[iP];
                                if (itP == null) continue;
                                string dP = itP.DisplayName ?? itP.Name ?? "";
                                if (!dP.Contains(nmP)) continue;
                                seedP = itP as StardewValley.Object; idxP = iP; break;
                            }
                            if (seedP == null) { this.monitor.Log("plant_seed: 没有种子 " + nmP, LogLevel.Warn); break; }
                            bool okP = false;
                            var idsP = new[] { seedP.QualifiedItemId, seedP.ItemId, seedP.ParentSheetIndex.ToString() };
                            var triedP = new System.Collections.Generic.List<string>();
                            foreach (var idP in idsP)
                            {
                                if (string.IsNullOrEmpty(idP)) continue;
                                try { okP = dirtP.plant(idP, Game1.player, false); } catch (Exception e8) { triedP.Add(idP + ":err" + e8.Message); continue; }
                                triedP.Add(idP + ":" + okP);
                                if (okP) break;
                            }
                            if (okP)
                            {
                                seedP.Stack -= 1;
                                if (seedP.Stack <= 0) Game1.player.Items[idxP] = null;
                            }
                            this.monitor.Log($"plant_seed: ({pxP},{pyP}) {nmP} planted={okP} tried={string.Join(",", triedP)}", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("plant_seed err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "dump_plant_apis":
                    {
                        try
                        {
                            var bfD = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var partsD = new System.Collections.Generic.List<string>();
                            foreach (var tD in new[] { typeof(StardewValley.GameLocation), typeof(StardewValley.TerrainFeatures.HoeDirt), typeof(StardewValley.Object) })
                            {
                                foreach (var mi in tD.GetMethods(bfD))
                                {
                                    string nmD = mi.Name.ToLower();
                                    if (nmD.Contains("plant") || nmD.Contains("performuseaction") || nmD.Contains("useseed") || nmD.Contains("doFunction".ToLower()))
                                        partsD.Add(tD.Name + "." + mi.Name + "(" + string.Join(",", System.Array.ConvertAll(mi.GetParameters(), pp => pp.ParameterType.Name)) + ")");
                                }
                            }
                            this.monitor.Log("PLANTAPI " + string.Join(" | ", partsD), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_plant_apis err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "dump_shop_fields":
                    {
                        try
                        {
                            var menuS = Game1.activeClickableMenu;
                            var bfS = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var partsS = new System.Collections.Generic.List<string>();
                            partsS.Add("menu=" + (menuS?.GetType().Name ?? "none"));
                            if (menuS != null)
                            {
                                for (var t = menuS.GetType(); t != null && t != typeof(object); t = t.BaseType)
                                {
                                    foreach (var f in t.GetFields(bfS | System.Reflection.BindingFlags.DeclaredOnly))
                                    {
                                        string nm = f.Name.ToLower();
                                        if (nm.Contains("sale") || nm.Contains("stock") || nm.Contains("button") || nm.Contains("inventory") || nm.Contains("hover"))
                                            partsS.Add(t.Name + "." + f.Name + ":" + f.FieldType.Name);
                                    }
                                }
                                var forSaleF = menuS.GetType().GetField("forSale", bfS);
                                var forSale = forSaleF?.GetValue(menuS) as System.Collections.IEnumerable;
                                if (forSale != null)
                                {
                                    int k = 0;
                                    foreach (var it in forSale)
                                    {
                                        if (k > 8) break;
                                        var obj = it as StardewValley.ISalable;
                                        partsS.Add("sale" + k + "=" + (obj?.DisplayName ?? "?") + ":" + (obj?.salePrice() ?? -1));
                                        k++;
                                    }
                                }
                            }
                            this.monitor.Log("SHOPF " + string.Join(" | ", partsS), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_shop_fields err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "buy_item":
                    {
                        try
                        {
                            string nmB = root.TryGetProperty("name", out var nB) ? nB.GetString() : "";
                            int cntB = root.TryGetProperty("count", out var cB) ? cB.GetInt32() : 1;
                            var bfB = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var menuB = Game1.activeClickableMenu;
                            if (menuB == null) { this.monitor.Log("buy_item: 商店没开", LogLevel.Warn); break; }
                            var saleF = menuB.GetType().GetField("forSale", bfB);
                            var saleL = saleF?.GetValue(menuB) as System.Collections.IList;
                            var btnF = menuB.GetType().GetField("forSaleButtons", bfB);
                            var btnL = btnF?.GetValue(menuB) as System.Collections.IList;
                            var clickM = menuB.GetType().GetMethod("receiveLeftClick", bfB);
                            var partsB = new System.Collections.Generic.List<string>();
                            partsB.Add("menu=" + menuB.GetType().Name + " sale=" + (saleL?.Count ?? -1) + " buttons=" + (btnL?.Count ?? -1) + " money0=" + Game1.player.Money);
                            int bought = 0;
                            if (saleL != null && btnL != null && clickM != null)
                            {
                                for (int attempt = 0; attempt < cntB; attempt++)
                                {
                                    int idx = -1;
                                    for (int i = 0; i < saleL.Count && i < btnL.Count; i++)
                                    {
                                        var obj = saleL[i] as StardewValley.ISalable;
                                        string dn = obj?.DisplayName ?? "";
                                        if (!dn.Contains(nmB)) continue;
                                        idx = i; break;
                                    }
                                    if (idx < 0) { partsB.Add("not found"); break; }
                                    var comp = btnL[idx];
                                    var b = comp.GetType().GetProperty("bounds")?.GetValue(comp) ?? comp.GetType().GetField("bounds")?.GetValue(comp);
                                    if (b == null) { partsB.Add("no bounds"); break; }
                                    var r = (Microsoft.Xna.Framework.Rectangle)b;
                                    try { clickM.Invoke(menuB, new object[] { r.Center.X, r.Center.Y, true }); } catch (Exception e7) { partsB.Add("clickErr:" + e7.Message); break; }
                                    bought++;
                                }
                            }
                            partsB.Add($"bought={bought} money={Game1.player.Money}");
                            this.monitor.Log("buy_item: " + string.Join(" ; ", partsB), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("buy_item err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "buy_direct":
                    {
                        try
                        {
                            string nmB = root.TryGetProperty("name", out var nB) ? nB.GetString() : "";
                            int cntB = root.TryGetProperty("count", out var cB) ? cB.GetInt32() : 1;
                            var menuB = Game1.activeClickableMenu;
                            if (menuB == null) { this.monitor.Log("buy_direct: 商店没开", LogLevel.Warn); break; }
                            var bfB = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var saleL = menuB.GetType().GetField("forSale", bfB)?.GetValue(menuB) as System.Collections.IList;
                            var stockD = menuB.GetType().GetField("itemPriceAndStock", bfB)?.GetValue(menuB) as System.Collections.IDictionary;
                            var partsB = new System.Collections.Generic.List<string>();
                            int bought = 0, spent = 0;
                            for (int attempt = 0; attempt < cntB; attempt++)
                            {
                                StardewValley.ISalable targetB = null;
                                string keyB = null;
                                if (saleL != null)
                                {
                                    for (int i = 0; i < saleL.Count; i++)
                                    {
                                        var s2 = saleL[i] as StardewValley.ISalable;
                                        string dn = s2?.DisplayName ?? "";
                                        if (!dn.Contains(nmB)) continue;
                                        targetB = s2; keyB = s2.QualifiedItemId; break;
                                    }
                                }
                                if (targetB == null) { partsB.Add("not found " + nmB); break; }
                                int price = targetB.salePrice();
                                if (stockD != null && keyB != null && stockD.Contains(keyB))
                                {
                                    var entry = stockD[keyB];
                                    var priceProp = entry?.GetType().GetProperty("Price");
                                    var priceField = entry?.GetType().GetField("Price");
                                    int p2 = priceProp != null ? (int)priceProp.GetValue(entry) : (priceField != null ? (int)priceField.GetValue(entry) : price);
                                    if (p2 > 0) price = p2;
                                }
                                if (Game1.player.Money < price) { partsB.Add("no money price=" + price); break; }
                                var one = (targetB as StardewValley.Item)?.getOne();
                                if (one == null) { partsB.Add("no item copy"); break; }
                                one.Stack = 1;
                                if (!Game1.player.addItemToInventoryBool(one, true)) { partsB.Add("inventory full"); break; }
                                Game1.player.Money -= price;
                                bought++; spent += price;
                            }
                            partsB.Add($"bought={bought} spent={spent} money={Game1.player.Money}");
                            this.monitor.Log("buy_direct: " + string.Join(" ; ", partsB), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("buy_direct err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "harvest_crop":
                    {
                        try
                        {
                            int hx = root.GetProperty("x").GetInt32(), hy = root.GetProperty("y").GetInt32();
                            var locH = Game1.player.currentLocation;
                            var posH = new Microsoft.Xna.Framework.Vector2(hx, hy);
                            if (!locH.terrainFeatures.TryGetValue(posH, out var featH) || !(featH is StardewValley.TerrainFeatures.HoeDirt dirtH))
                            { this.monitor.Log($"harvest_crop: ({hx},{hy}) 不是耕地", LogLevel.Warn); break; }
                            var cropH = dirtH.crop;
                            if (cropH == null) { this.monitor.Log("harvest_crop: 没有作物", LogLevel.Warn); break; }
                            bool readyH = dirtH.readyForHarvest();
                            int invBefore = Game1.player.Items.Count(i => i != null);
                            bool okH = false;
                            var partsH = new System.Collections.Generic.List<string>();
                            try
                            {
                                var bfH = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                                bool calledH = false;
                                foreach (var miH in cropH.GetType().GetMethods(bfH))
                                {
                                    if (miH.Name != "harvest") continue;
                                    string sigH = miH.Name + "(" + string.Join(",", System.Array.ConvertAll(miH.GetParameters(), pp => pp.ParameterType.Name)) + ")";
                                    try
                                    {
                                        var psH = miH.GetParameters();
                                        var argsH = new object[psH.Length];
                                        int intC = 0;
                                        for (int q = 0; q < psH.Length; q++)
                                        {
                                            var pt = psH[q].ParameterType;
                                            if (pt == typeof(int)) argsH[q] = (intC++ == 0) ? hx : hy;
                                            else if (pt == typeof(StardewValley.TerrainFeatures.HoeDirt)) argsH[q] = dirtH;
                                            else if (pt == typeof(bool)) argsH[q] = true;
                                            else argsH[q] = null;
                                        }
                                        var resH = miH.Invoke(cropH, argsH);
                                        okH = resH is bool bh ? bh : true;
                                        partsH.Add(sigH + "=" + okH);
                                        calledH = true;
                                        break;
                                    }
                                    catch (Exception eH) { partsH.Add(sigH + ":err" + eH.Message); }
                                }
                                if (!calledH) partsH.Add("noHarvestMethod");
                            }
                            catch (Exception eH) { partsH.Add("err:" + eH.Message); }
                            if (okH && dirtH.crop != null)
                            {
                                try { dirtH.crop = null; partsH.Add("cropCleared"); } catch { }
                            }
                            int invAfter = Game1.player.Items.Count(i => i != null);
                            partsH.Add($"ready={readyH} invBefore={invBefore} invAfter={invAfter} cropNow={(dirtH.crop != null)}");
                            this.monitor.Log("harvest_crop: " + string.Join(" ; ", partsH), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("harvest_crop err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }
                case "player_use_tool":
                    if (root.TryGetProperty("tool", out var put) && root.TryGetProperty("x", out var pux) && root.TryGetProperty("y", out var puy))
                    {
                        var want = put.GetString().ToLower();
                        StardewValley.Tool tool = Game1.player.Items.OfType<StardewValley.Tool>().FirstOrDefault(t =>
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
                        if (tool == null)
                        {
                            // 镰刀是 MeleeWeapon，不在 Tool 里
                            foreach (var it in Game1.player.Items)
                            {
                                if (it == null) continue;
                                string cn = it.GetType().Name.ToLower();
                                string inm = (it.Name ?? "").ToLower();
                                if (cn.Contains(want) || inm.Contains(want))
                                {
                                    tool = it as StardewValley.Tool;
                                    break;
                                }
                            }
                        }
                        if (tool != null)
                        {
                            int idx = Game1.player.Items.IndexOf(tool);
                            if (idx >= 0) Game1.player.CurrentToolIndex = idx;
                            tool.DoFunction(Game1.player.currentLocation, pux.GetInt32() * 64 + 32, puy.GetInt32() * 64 + 32, tool.UpgradeLevel + 1, Game1.player);
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
                            int gx3 = wtx.GetInt32(), gy3 = wty.GetInt32();
                            int mw = walkLoc.Map.Layers[0].LayerWidth, mh = walkLoc.Map.Layers[0].LayerHeight;
                            if (gx3 < 0 || gy3 < 0 || gx3 >= mw || gy3 >= mh)
                            {
                                this.monitor.Log($"walk target ({gx3},{gy3}) 超出地图 {mw}x{mh}，忽略", LogLevel.Warn);
                                break;
                            }
                            Game1.player.controller = new PathFindController(Game1.player, walkLoc,
                                new Microsoft.Xna.Framework.Point(gx3, gy3), 1000);
                            this.monitor.Log($"Walking to ({gx3},{gy3})", LogLevel.Info);
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
                                ResetFarmerPose();
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
                        var plac = Game1.player.currentLocation;
                        int tx = -999, ty = -999;
                        if (root.TryGetProperty("x", out var pxEl) && root.TryGetProperty("y", out var pyEl))
                        { tx = pxEl.GetInt32(); ty = pyEl.GetInt32(); }
                        int ox = 0, oy = 0;
                        if (dir == 0) oy = -1; else if (dir == 1) ox = 1; else if (dir == 2) oy = 1; else ox = -1;
                        var cands = new System.Collections.Generic.List<Microsoft.Xna.Framework.Point>();
                        if (tx != -999) cands.Add(new Microsoft.Xna.Framework.Point(tx, ty));
                        cands.Add(new Microsoft.Xna.Framework.Point((int)ptile.X + ox, (int)ptile.Y + oy));
                        foreach (var d in new[] { (0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (-1, -1), (1, -1), (-1, 1) })
                            cands.Add(new Microsoft.Xna.Framework.Point((int)ptile.X + d.Item1, (int)ptile.Y + d.Item2));
                        bool placed = false;
                        Microsoft.Xna.Framework.Point used = default;
                        foreach (var c in cands)
                        {
                            try { placed = (pit as StardewValley.Object)?.placementAction(plac, c.X * 64, c.Y * 64, Game1.player) ?? false; }
                            catch (Exception ex) { this.monitor.Log("place err: " + ex.Message, LogLevel.Warn); placed = false; }
                            if (placed) { used = c; break; }
                        }
                        if (placed) { Game1.player.removeItemFromInventory(pit); }
                        this.monitor.Log($"place_item {pn} placed={placed} at {(placed ? used.X + "," + used.Y : "none")}", LogLevel.Info);
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

                case "sell_crops":
                    {
                        try
                        {
                            long total = 0;
                            int sold = 0;
                            for (int i = Game1.player.Items.Count - 1; i >= 0; i--)
                            {
                                var it = Game1.player.Items[i];
                                if (it == null) continue;
                                int cat = it.Category;
                                // 只卖「收的菜/水果/花」(-75 蔬菜, -79 水果, -80 花)
                                if (cat == -75 || cat == -79 || cat == -80)
                                {
                                    int price = it.sellToStorePrice();
                                    total += (long)price * it.Stack;
                                    Game1.player.Items[i] = null;
                                    sold += it.Stack;
                                }
                            }
                            Game1.player.Money += (int)total;
                            this.monitor.Log($"sell_crops: {sold} 个，+{total}G", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("sell_crops err: " + ex.Message, LogLevel.Warn); }
                        break;
                    }

                case "chest_store_ores":
                    {
                        try
                        {
                            int cx = root.GetProperty("x").GetInt32(), cy = root.GetProperty("y").GetInt32();
                            var loc2 = Game1.player.currentLocation;
                            if (!loc2.Objects.TryGetValue(new Vector2(cx, cy), out var chestObj) || !(chestObj is StardewValley.Objects.Chest chest))
                            {
                                this.monitor.Log("chest_store_ores: 目标不是箱子", LogLevel.Warn);
                                break;
                            }
                            int moved = 0;
                            for (int i = Game1.player.Items.Count - 1; i >= 0; i--)
                            {
                                var it = Game1.player.Items[i];
                                if (it == null) continue;
                                string nm2 = it.Name ?? "";
                                var so2 = it as StardewValley.Object;
                                bool oreLike = (so2 != null && so2.Type == "Minerals") || nm2.Contains("Ore") || nm2.Contains("Node")
                                               || nm2.Contains("Geode") || nm2.Contains("Coal")
                                               || nm2.Contains("Bar") || nm2.Contains("Quartz") || nm2.Contains("Crystal");
                                if (!oreLike) continue;
                                if (chest.Items.Count >= chest.GetActualCapacity()) break;
                                chest.Items.Add(it);
                                Game1.player.Items[i] = null;
                                moved++;
                            }
                            this.monitor.Log($"chest_store_ores: 存入 {moved} 个", LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("chest_store_ores err: " + ex.Message, LogLevel.Warn); }
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
                        int radius = root.TryGetProperty("radius", out var rr) ? rr.GetInt32() : 3;
                        var dloc = Game1.player.currentLocation;
                        if (dloc != null)
                        {
                            foreach (var d in dloc.debris.ToList())
                            {
                                try
                                {
                                    // 只捡附近地上的掉落，别把全地图掉落一次吸走
                                    try
                                    {
                                        var pf = d.GetType().GetField("position");
                                        if (pf != null)
                                        {
                                            var pv = (Vector2)pf.GetValue(d);
                                            if (Vector2.Distance(pv, Game1.player.Position) > radius * 64f) continue;
                                        }
                                    }
                                    catch { }
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

                    case "dump_farmer_methods":
                    {
                        try
                        {
                            var btF = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                            var partsF = new System.Collections.Generic.List<string>();
                            foreach (var mi in typeof(Farmer).GetMethods(btF))
                            {
                                string nmF = mi.Name.ToLower();
                                if (nmF.Contains("fall") || nmF.Contains("shaft") || nmF.Contains("jump") || nmF.Contains("descend"))
                                    partsF.Add(mi.Name + "(" + string.Join(",", System.Array.ConvertAll(mi.GetParameters(), pp => pp.ParameterType.Name)) + ")");
                            }
                            try { System.IO.File.WriteAllText(System.IO.Path.Combine(Game1.player.currentLocation.GetType() == null ? "" : System.IO.Path.GetDirectoryName(typeof(BotManager).Assembly.Location), "farmer_methods.txt"), "FARMERM " + string.Join("\n", partsF)); } catch { }
                            this.monitor.Log("FARMERM " + string.Join(" ; ", partsF), LogLevel.Info);
                            var partsM = new System.Collections.Generic.List<string>();
                            foreach (var mi in Game1.currentLocation.GetType().GetMethods(btF))
                            {
                                string nmF = mi.Name.ToLower();
                                if (nmF.Contains("fall") || nmF.Contains("shaft") || nmF.Contains("jump") || nmF.Contains("descend"))
                                    partsM.Add(mi.Name + "(" + string.Join(",", System.Array.ConvertAll(mi.GetParameters(), pp => pp.ParameterType.Name)) + ")");
                            }
                            this.monitor.Log("MINEM " + string.Join(" ; ", partsM), LogLevel.Info);
                        }
                        catch (Exception ex) { this.monitor.Log("dump_farmer_methods err: " + ex.Message, LogLevel.Warn); }
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
