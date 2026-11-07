using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using HarmonyLib;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.GameData.Characters;

namespace StardewMCPBridge
{
    public class ModEntry : Mod
    {
        private string bridgePath;
        private string actionDir;
        private BotManager botManager;
        private Texture2D companion1Portrait;
        private Texture2D companion2Portrait;
        private Texture2D companion1Sprite;
        private Texture2D companion2Sprite;

        public override void Entry(IModHelper helper)
        {
            this.botManager = new BotManager(this.Monitor, helper);
            this.bridgePath = Path.Combine(helper.DirectoryPath, "bridge_data.json");
            try { new Harmony("StardewMCPBridge.Patches").PatchAll(); this.Monitor.Log("Harmony patches applied", LogLevel.Info); } catch (Exception ex) { this.Monitor.Log("Harmony patch failed: " + ex.Message, LogLevel.Error); }
            this.actionDir = Path.Combine(helper.DirectoryPath, "actions");

            helper.Events.GameLoop.GameLaunched += this.OnGameLaunched;
            helper.Events.GameLoop.UpdateTicked += this.OnUpdateTicked;
            helper.Events.GameLoop.DayStarted += this.OnDayStarted;
            helper.Events.GameLoop.UpdateTicking += (s2, e2) => { this.botManager.TickFishingHelp(); this.botManager.TickNudge(); };
            helper.Events.GameLoop.Saving += (s, e) => this.botManager.RemoveCompanionsForSave();
            helper.Events.GameLoop.Saved += (s, e) => this.botManager.RestoreCompanionsAfterSave();
            helper.Events.GameLoop.DayEnding += this.OnDayEnding;
            helper.Events.GameLoop.TimeChanged += this.OnTimeChanged;
            helper.Events.GameLoop.ReturnedToTitle += this.OnReturnedToTitle;
            helper.Events.Content.AssetRequested += this.OnAssetRequested;

            this.Monitor.Log("Stardew MCP Bridge initialized. Content pipeline registered.", LogLevel.Debug);
        }

        private void OnGameLaunched(object sender, GameLaunchedEventArgs e)
        {
            this.companion1Portrait = this.Helper.ModContent.Load<Texture2D>("assets/Companion1_portrait.png");
            this.companion2Portrait = this.Helper.ModContent.Load<Texture2D>("assets/Companion2_portrait.png");
            this.companion1Sprite = this.Helper.ModContent.Load<Texture2D>("assets/Companion1_sprite.png");
            this.companion2Sprite = this.Helper.ModContent.Load<Texture2D>("assets/Companion2_sprite.png");
            this.Monitor.Log("Bridge online. Portraits and sprites loaded. Waiting for world.", LogLevel.Info);
        }

        private void OnAssetRequested(object sender, AssetRequestedEventArgs e)
        {
            // Inject portrait textures
            if (e.NameWithoutLocale.IsEquivalentTo("Portraits/Companion1"))
            {
                e.LoadFrom(() => this.companion1Portrait, AssetLoadPriority.Exclusive);
            }
            else if (e.NameWithoutLocale.IsEquivalentTo("Portraits/Companion2"))
            {
                e.LoadFrom(() => this.companion2Portrait, AssetLoadPriority.Exclusive);
            }
            // Custom sprite sheets for walking animation
            else if (e.NameWithoutLocale.IsEquivalentTo("Characters/Companion1"))
            {
                e.LoadFrom(() => this.companion1Sprite, AssetLoadPriority.Exclusive);
            }
            else if (e.NameWithoutLocale.IsEquivalentTo("Characters/Companion2"))
            {
                e.LoadFrom(() => this.companion2Sprite, AssetLoadPriority.Exclusive);
            }
            // Inject NPC data so the game considers us valid
            else if (e.NameWithoutLocale.IsEquivalentTo("Data/Characters"))
            {
                e.Edit(asset =>
                {
                    var data = asset.AsDictionary<string, CharacterData>();

                    if (!data.Data.ContainsKey("Companion1"))
                    {
                        data.Data["Companion1"] = new CharacterData
                        {
                            DisplayName = "Companion1",
                            HomeRegion = "Town",
                        };
                    }

                    if (!data.Data.ContainsKey("Companion2"))
                    {
                        data.Data["Companion2"] = new CharacterData
                        {
                            DisplayName = "Companion2",
                            HomeRegion = "Town",
                        };
                    }
                });
            }
        }

        private void OnUpdateTicked(object sender, UpdateTickedEventArgs e)
        {
            this.botManager.AutoDismissDialogue();
            this.botManager.TickFishingHelp();
            if (!Context.IsWorldReady) return;

            // AI ticks every frame (60/sec) for responsive combat, pathfinding, stuck detection
            this.botManager.Update();

            // Bridge I/O every 30 ticks (~0.5s) to avoid thrashing disk
            if (e.IsMultipleOf(30))
            {
                this.SyncGameState();
                this.ProcessActions();
            }
        }

        private void OnTimeChanged(object sender, TimeChangedEventArgs e)
        {
            // Safety net: if it's 2:00 AM (forced pass-out time), signal bots ready
            if (e.NewTime >= 2600)
                this.botManager.SignalAllSleepReady();
        }

        private void OnDayEnding(object sender, DayEndingEventArgs e)
        {
            // Signal all bot farmers as sleep-ready so the game doesn't deadlock
            this.botManager.SignalAllSleepReady();
            this.Monitor.Log("Day ending: bot farmers signaled sleep ready", LogLevel.Debug);
        }

        private void OnDayStarted(object sender, DayStartedEventArgs e)
        {
            this.botManager.OnDayStarted();
            this.Monitor.Log("New day: companion stamina restored", LogLevel.Info);
        }

        private void OnReturnedToTitle(object sender, ReturnedToTitleEventArgs e)
        {
            this.botManager.Cleanup();
            this.Monitor.Log("Returned to title: companions cleaned up", LogLevel.Info);
        }

        private static float GetBobberField(string name)
        {
            try
            {
                if (Game1.activeClickableMenu is StardewValley.Menus.BobberBar bar)
                {
                    var f = typeof(StardewValley.Menus.BobberBar).GetField(name,
                        System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
                    if (f != null)
                    {
                        var v = f.GetValue(bar);
                        if (v is float fv) return fv;
                        if (v is int iv) return iv;
                    }
                }
            }
            catch { }
            return 0f;
        }

        private static int CountCrops(GameLocation location)
        {
            if (location == null) return 0;
            int n = 0;
            foreach (var kv in location.terrainFeatures.Pairs)
            {
                if (kv.Value is StardewValley.TerrainFeatures.HoeDirt d && d.crop != null) n++;
            }
            return n;
        }

        private void SyncGameState()
        {
            try
            {
                var state = new
                {
                    time = Game1.timeOfDay,
                    day = Game1.dayOfMonth,
                    season = Game1.currentSeason,
                    weather = Game1.isLightning ? "storm" : Game1.isRaining ? "rain" : Game1.isSnowing ? "snow" : Game1.isDebrisWeather ? "windy" : "sunny",
                    location = Game1.currentLocation?.Name,
                    player = new
                    {
                        name = Game1.player.Name,
                        health = Game1.player.health,
                        stamina = Game1.player.Stamina,
                        money = Game1.player.Money,
                        tile = new { x = (int)Game1.player.Tile.X, y = (int)Game1.player.Tile.Y },
                        position = new { x = Game1.player.Position.X, y = Game1.player.Position.Y },
                        inventory = Game1.player.Items.Where(it => it != null).Take(36).Select(it => new
                        {
                            name = it.DisplayName,
                            id = it.ItemId,
                            stack = it.Stack,
                            category = it is StardewValley.Object obj ? obj.Category : 0
                        }).ToList(),
                        crops = CountCrops(Game1.player.currentLocation),
                        xp = Game1.player.experiencePoints,
                        mouse = new { x = Game1.getMouseX(), y = Game1.getMouseY() },
                        currentTool = Game1.player.CurrentTool?.DisplayName,
                        currentToolIndex = Game1.player.CurrentToolIndex,
                        viewport = new { x = Game1.viewport.X, y = Game1.viewport.Y, w = Game1.viewport.Width, h = Game1.viewport.Height },
                        skills = new
                        {
                            farming = Game1.player.FarmingLevel,
                            mining = Game1.player.MiningLevel,
                            fishing = Game1.player.FishingLevel,
                            foraging = Game1.player.ForagingLevel,
                            combat = Game1.player.CombatLevel
                        },
                        tools = Game1.player.Items.Where(it => it is StardewValley.Tool).Select(it => new { name = it.DisplayName, level = (it as StardewValley.Tool).UpgradeLevel }).ToList(),
                        toolUpgrading = Game1.player.toolBeingUpgraded != null,
                        toolUpgradeDays = Game1.player.daysLeftForToolUpgrade,
                        fishing = new
                        {
                            rod = Game1.player.CurrentTool is StardewValley.Tools.FishingRod,
                            inUse = (Game1.player.CurrentTool as StardewValley.Tools.FishingRod)?.inUse() ?? false,
                            nibbling = (Game1.player.CurrentTool as StardewValley.Tools.FishingRod)?.isNibbling ?? false,
                            minigame = Game1.activeClickableMenu is StardewValley.Menus.BobberBar,
                            barPos = GetBobberField("bobberBarPos"),
                            fishPos = GetBobberField("bobberPosition"),
                            distance = GetBobberField("distanceFromCatching"),
                            activeMenu = Game1.activeClickableMenu?.GetType().Name
                        }
                    },
                    companions = this.botManager.GetBotStatus(),
                    surroundings = SurroundingsScanner.Scan(Game1.player.currentLocation, Game1.player.Tile, 9),
                    exits = Game1.player.currentLocation?.warps.Select(w => new {
                        x = w.X, y = w.Y, target = w.TargetName,
                        tx = w.TargetX, ty = w.TargetY
                    }).ToList(),
                    npcs = Game1.currentLocation?.characters.Select(c => new {
                        name = c.Name,
                        position = new { x = c.Position.X, y = c.Position.Y }
                    }).ToList(),
                    syncedAt = DateTime.UtcNow.ToString("o")
                };

                string json = JsonSerializer.Serialize(state, new JsonSerializerOptions { WriteIndented = true });
                // Atomic write with retries (reader/AV may lock the file)
                string tmpPath = this.bridgePath + ".tmp";
                for (int attempt = 0; attempt < 4; attempt++)
                {
                    try
                    {
                        File.WriteAllText(tmpPath, json);
                        File.Move(tmpPath, this.bridgePath, true);
                        break;
                    }
                    catch (IOException)
                    {
                        System.Threading.Thread.Sleep(40 * (attempt + 1));
                    }
                }
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"Bridge Sync Error: {ex.Message}", LogLevel.Error);
            }
        }

        private void ProcessActions()
        {
            try
            {
                if (!Directory.Exists(this.actionDir))
                    return;

                // Drain the queue oldest-first. Each command is its own file named
                // <timestamp>-<seq>.json, so ordinal filename sort is chronological.
                string[] files = Directory.GetFiles(this.actionDir, "*.json");
                if (files.Length == 0)
                    return;
                Array.Sort(files, StringComparer.Ordinal);

                foreach (string file in files)
                {
                    string json;
                    try
                    {
                        json = File.ReadAllText(file);
                        // Delete immediately so each command is consumed exactly once,
                        // even if handling below throws.
                        File.Delete(file);
                    }
                    catch (Exception ex)
                    {
                        this.Monitor.Log($"Action read error ({Path.GetFileName(file)}): {ex.Message}", LogLevel.Error);
                        continue;
                    }

                    if (string.IsNullOrWhiteSpace(json))
                        continue;

                    try
                    {
                        this.HandleAction(json);
                    }
                    catch (Exception ex)
                    {
                        this.Monitor.Log($"Action handling error: {ex.Message}", LogLevel.Error);
                    }
                }
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"Action Processing Error: {ex.Message}", LogLevel.Error);
            }
        }

        private void HandleAction(string json)
        {
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;

            if (!root.TryGetProperty("actionType", out var actionType))
                return;

            // Route to bot manager for companion actions
            this.botManager.ProcessAction(json);

            // Handle chat separately (not a companion action)
            if (actionType.GetString() == "chat")
            {
                if (root.TryGetProperty("metadata", out var meta) &&
                    meta.TryGetProperty("message", out var msg))
                {
                    string text = msg.GetString();
                    if (!string.IsNullOrEmpty(text))
                    {
                        Game1.chatBox?.addMessage(text, Microsoft.Xna.Framework.Color.Gold);
                        this.Monitor.Log($"Chat sent: {text}", LogLevel.Info);
                    }
                }
            }
        }
    }
}
