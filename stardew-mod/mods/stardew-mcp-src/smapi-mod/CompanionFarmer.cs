using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Monsters;
using StardewValley.TerrainFeatures;
using StardewValley.Tools;
using SObject = StardewValley.Object;

namespace StardewMCPBridge
{
    /// <summary>
    /// Pairs a visible NPC (custom sprite) with a shadow BotFarmer (game mechanics).
    /// The NPC walks around and is what the player sees.
    /// The shadow farmer performs tool use, combat, fishing, and shopping.
    /// </summary>
    public class CompanionFarmer
    {
        public BotFarmer Shadow { get; private set; }
        public NPC Visual { get; private set; }
        public string Name { get; private set; }

        private readonly IMonitor monitor;
        private readonly IModHelper helper;

        public CompanionFarmer(NPC visualNpc, string name, IMonitor monitor, IModHelper helper)
        {
            this.Visual = visualNpc;
            this.Name = name;
            this.monitor = monitor;
            this.helper = helper;

            this.Shadow = new BotFarmer();
            this.Shadow.UniqueMultiplayerID = helper.Multiplayer.GetNewID();
            this.Shadow.Name = name + "_shadow";
            this.Shadow.displayName = name;
            this.Shadow.Speed = 2;
            this.Shadow.Stamina = Farmer.startingStamina;
            this.Shadow.MaxItems = 36;

            // Give initial tools
            var tools = Farmer.initialTools();
            foreach (var tool in tools)
                this.Shadow.Items.Add(tool);

            // Pad inventory
            for (int i = this.Shadow.Items.Count; i < 36; i++)
                this.Shadow.Items.Add(null);

            this.SyncFromNpc();

            // NOTE: Do NOT add Shadow to Game1.otherFarmers. That collection is the
            // network-synced remote-player table; Multiplayer.updateRoots() walks it every
            // tick expecting net-backed farmers and NPEs on a hand-built one. The visual NPC
            // handles rendering and the shadow's mechanics are driven directly, so it isn't needed.
            this.monitor.Log($"Shadow farmer created for {name} (UID: {this.Shadow.UniqueMultiplayerID})", LogLevel.Info);
        }

        /// <summary>Sync shadow farmer position/location from the visible NPC.</summary>
        public void SyncFromNpc()
        {
            this.Shadow.Position = this.Visual.Position;
            this.Shadow.currentLocation = this.Visual.currentLocation ?? Game1.player.currentLocation;
            this.Shadow.FacingDirection = this.Visual.FacingDirection;
        }

        // ======================
        // TOOL USE
        // ======================

        /// <summary>Use a tool at a specific tile. Shadow farmer handles the game mechanics.</summary>
        private int toolAnimTicks = -1;   // 鲸live：工具挥动动画剩余帧数（-1=没有进行中）
        private string pendingKind = "";  // hoe / water / clear
        private Vector2 pendingTile;
        private GameLocation pendingLocation;

        /// <summary>每帧调用：把"举着工具"的动画演完，再让效果生效。</summary>
        public void TickPendingTool()
        {
            if (toolAnimTicks < 0) return;
            toolAnimTicks--;
            if (toolAnimTicks > 0) return;
            toolAnimTicks = -1;
            try { this.Shadow.UsingTool = false; } catch { }
            try
            {
                // 游戏自带的 EndUsingTool 对"影子农民"不一定会真的执行工具，
                // 所以动画演完后由我们调用可靠的地块逻辑（观感上仍是挥完才生效）。
                bool ok = pendingKind switch
                {
                    "hoe" => CompanionActions.HoeTile(pendingLocation, pendingTile, this.monitor),
                    "water" => CompanionActions.WaterTile(pendingLocation, pendingTile, this.monitor),
                    "clear" => CompanionActions.ClearDebris(pendingLocation, pendingTile, this.monitor),
                    _ => false
                };
                this.monitor.Log($"{this.Name}: {pendingKind} ({pendingTile.X},{pendingTile.Y}) 生效：{(ok ? "成功" : "失败")}", LogLevel.Info);
            }
            catch (Exception ex)
            {
                this.monitor.Log($"{this.Name}: 工具生效失败：{ex.Message}", LogLevel.Warn);
            }
            pendingKind = "";
        }

        public bool UseToolAt(Vector2 tile, Type toolType)
        {
            this.SyncFromNpc();

            var tool = this.Shadow.Items.FirstOrDefault(i => i != null && toolType.IsInstanceOfType(i)) as Tool;
            if (tool == null)
            {
                this.monitor.Log($"{this.Name}: Don't have a {toolType.Name}", LogLevel.Warn);
                return false;
            }

            if (this.Shadow.Stamina <= 0)
            {
                this.monitor.Log($"{this.Name}: Out of energy", LogLevel.Warn);
                return false;
            }

            // 鲸live：必须站在目标格旁边才动手（不然画面里人和效果对不上）
            var npcTile = this.Visual.Tile;
            float dist = Math.Abs(npcTile.X - tile.X) + Math.Abs(npcTile.Y - tile.Y);
            if (dist > 1.6f)
            {
                this.monitor.Log($"{this.Name}: 离 ({tile.X},{tile.Y}) 太远（{dist:F1} 格），先 move_to", LogLevel.Warn);
                return false;
            }

            this.Shadow.FaceToward(tile);
            float oldStamina = this.Shadow.Stamina;

            try
            {
                if (tool is MeleeWeapon weapon)
                {
                    var toolLoc = this.Shadow.GetToolLocation(true);
                    weapon.DoDamage(this.Shadow.currentLocation,
                        (int)toolLoc.X, (int)toolLoc.Y,
                        this.Shadow.FacingDirection, 1, this.Shadow);
                }
                else
                {
                    // 鲸live：走真实的"开始使用工具"流程 → 播放挥动动画 + 音效
                    this.Shadow.CurrentTool = tool;
                    this.Shadow.BeginUsingTool();
                    this.toolAnimTicks = 20;      // ~0.33 秒后生效（60fps）
                    this.pendingKind = tool is Hoe ? "hoe" : (tool is WateringCan ? "water" : "clear");
                    this.pendingTile = tile;
                    this.pendingLocation = this.Shadow.currentLocation;
                    this.pendingOldStamina = oldStamina;
                    this.monitor.Log($"{this.Name}: 挥动 {tool.Name} 朝向 ({tile.X},{tile.Y})", LogLevel.Trace);
                }
                return true;
            }
            catch (Exception ex)
            {
                this.monitor.Log($"{this.Name}: Tool use failed: {ex.Message}", LogLevel.Warn);
                try { this.Shadow.UsingTool = false; } catch { }
                return false;
            }
        }

        private float pendingOldStamina;

        // ======================
        // COMBAT
        // ======================

        /// <summary>Find the nearest monster within range.</summary>
        public Monster FindNearestMonster(float range = 256f)
        {
            this.SyncFromNpc();
            Monster nearest = null;
            float nearestDist = range;

            foreach (var c in this.Shadow.currentLocation.characters)
            {
                if (c is Monster m && !m.IsInvisible && m.Health > 0)
                {
                    float dist = Vector2.Distance(this.Shadow.Position, m.Position);
                    if (dist < nearestDist)
                    {
                        nearestDist = dist;
                        nearest = m;
                    }
                }
            }
            return nearest;
        }

        /// <summary>Attack nearby monsters using equipped weapon.</summary>
        public bool AttackNearbyMonsters()
        {
            this.SyncFromNpc();

            var weapon = this.Shadow.Items.FirstOrDefault(i => i is MeleeWeapon) as MeleeWeapon;
            if (weapon == null) return false;

            var monster = this.FindNearestMonster();
            if (monster == null) return false;

            Vector2 monsterTile = monster.Tile;
            this.Shadow.FaceToward(monsterTile);

            var toolLoc = this.Shadow.GetToolLocation(true);
            try
            {
                weapon.DoDamage(this.Shadow.currentLocation,
                    (int)toolLoc.X, (int)toolLoc.Y,
                    this.Shadow.FacingDirection, 1, this.Shadow);
                this.monitor.Log($"{this.Name}: Attacked monster at ({monsterTile.X},{monsterTile.Y})", LogLevel.Trace);
                return true;
            }
            catch (Exception ex)
            {
                this.monitor.Log($"{this.Name}: Attack failed: {ex.Message}", LogLevel.Warn);
                return false;
            }
        }

        /// <summary>Damage monsters in an area around the companion.</summary>
        public bool AreaAttack(int minDmg = 5, int maxDmg = 15)
        {
            this.SyncFromNpc();
            var pos = this.Shadow.Position;
            var area = new Rectangle((int)pos.X - 64, (int)pos.Y - 64, 192, 192);

            try
            {
                bool hit = this.Shadow.currentLocation.damageMonster(
                    area, minDmg, maxDmg, false, 1f, 0, 0.02f, 3f, true, this.Shadow);
                if (hit)
                    this.monitor.Log($"{this.Name}: Area attack hit!", LogLevel.Trace);
                return hit;
            }
            catch { return false; }
        }

        // ======================
        // MINING
        // ======================

        /// <summary>Break a rock/stone at a tile using the pickaxe.</summary>
        public bool MineRock(Vector2 tile)
        {
            return this.UseToolAt(tile, typeof(Pickaxe));
        }

        /// <summary>Find nearest breakable rock in the current location.</summary>
        public Vector2? FindNearestRock()
        {
            this.SyncFromNpc();
            var location = this.Shadow.currentLocation;
            Vector2 myTile = this.Shadow.Tile;
            Vector2? nearest = null;
            float nearestDist = float.MaxValue;

            foreach (var pair in location.objects.Pairs)
            {
                if (pair.Value.Name != null && pair.Value.Name.Contains("Stone"))
                {
                    float dist = Vector2.Distance(myTile, pair.Key);
                    if (dist < nearestDist)
                    {
                        nearestDist = dist;
                        nearest = pair.Key;
                    }
                }
            }
            return nearest;
        }

        // ======================
        // FISHING
        // ======================

        /// <summary>Cast the fishing rod. Call from a waterfront tile.</summary>
        public bool CastFishingRod()
        {
            this.SyncFromNpc();
            var rod = this.Shadow.Items.FirstOrDefault(i => i is FishingRod) as FishingRod;
            if (rod == null)
            {
                this.monitor.Log($"{this.Name}: No fishing rod", LogLevel.Warn);
                return false;
            }

            try
            {
                var pos = this.Shadow.Position;
                rod.beginUsing(this.Shadow.currentLocation, (int)pos.X, (int)pos.Y, this.Shadow);
                rod.castingPower = 1f;
                this.monitor.Log($"{this.Name}: Cast fishing rod", LogLevel.Trace);
                return true;
            }
            catch (Exception ex)
            {
                this.monitor.Log($"{this.Name}: Fishing cast failed: {ex.Message}", LogLevel.Warn);
                return false;
            }
        }

        /// <summary>Tick the fishing rod's internal state machine so it progresses (cast → wait → nibble).</summary>
        public void TickFishingRod()
        {
            var rod = this.Shadow.Items.FirstOrDefault(i => i is FishingRod) as FishingRod;
            if (rod == null || Game1.currentGameTime == null) return;

            try
            {
                rod.tickUpdate(Game1.currentGameTime, this.Shadow);
            }
            catch { /* rod update can throw during transitions */ }
        }

        /// <summary>Check if rod is nibbling and hook the fish.</summary>
        public bool CheckAndHookFish()
        {
            var rod = this.Shadow.Items.FirstOrDefault(i => i is FishingRod) as FishingRod;
            if (rod == null) return false;

            if (rod.isNibbling && !rod.isReeling && !rod.hit && !rod.pullingOutOfWater)
            {
                rod.DoFunction(this.Shadow.currentLocation, 1, 1, 1, this.Shadow);
                this.monitor.Log($"{this.Name}: Hooked a fish!", LogLevel.Info);
                return true;
            }
            return false;
        }

        // ======================
        // SHOPPING
        // ======================

        /// <summary>Buy an item by directly adding to inventory and deducting from the player's money.</summary>
        public bool BuyItem(string qualifiedItemId, int quantity, int unitPrice)
        {
            int total = unitPrice * quantity;
            if (Game1.player.Money >= total)
            {
                Game1.player.Money -= total;
                Item item = ItemRegistry.Create(qualifiedItemId, quantity);
                Game1.player.addItemToInventory(item);
                this.monitor.Log($"{this.Name}: Bought {quantity}x {item.DisplayName} for {total}g", LogLevel.Info);
                return true;
            }
            this.monitor.Log($"{this.Name}: Not enough money for purchase ({total}g needed)", LogLevel.Warn);
            return false;
        }

        // ======================
        // NAVIGATION
        // ======================

        /// <summary>Warp both visual NPC and shadow farmer to a new location.</summary>
        public void WarpTo(string locationName, int tileX, int tileY)
        {
            GameLocation target = Game1.getLocationFromName(locationName);
            if (target == null)
            {
                this.monitor.Log($"{this.Name}: Location '{locationName}' not found", LogLevel.Warn);
                return;
            }

            // Remove NPC from old location and clear stale pathfinding
            this.Visual.currentLocation?.characters.Remove(this.Visual);
            this.Visual.controller = null;

            // Move both to new location
            var pos = new Vector2(tileX * 64f, tileY * 64f);
            this.Visual.Position = pos;
            this.Visual.currentLocation = target;
            target.addCharacter(this.Visual);

            this.Shadow.Position = pos;
            this.Shadow.currentLocation = target;

            this.monitor.Log($"{this.Name}: Warped to {locationName} ({tileX},{tileY})", LogLevel.Info);
        }

        /// <summary>Get current stamina as a percentage.</summary>
        public float GetStaminaPercent()
        {
            return this.Shadow.Stamina / this.Shadow.MaxStamina * 100f;
        }

        /// <summary>Scan the companion's surroundings for bridge data.</summary>
        public ScanResult GetSurroundings(int radius = 8)
        {
            var location = this.Visual.currentLocation ?? Game1.player?.currentLocation;
            if (location == null) return null;
            return SurroundingsScanner.Scan(location, this.Visual.Tile, radius);
        }

        /// <summary>Get inventory as serializable list for bridge data.</summary>
        public List<object> GetInventoryData()
        {
            var items = new List<object>();
            foreach (var item in this.Shadow.Items)
            {
                if (item != null)
                {
                    items.Add(new
                    {
                        name = item.DisplayName ?? item.Name,
                        stack = item.Stack,
                        type = item is Tool ? "tool" : "item",
                        qualifiedId = item.QualifiedItemId
                    });
                }
            }
            return items;
        }

        /// <summary>Signal sleep readiness for day transition.</summary>
        public void SignalSleepReady()
        {
            this.Shadow.SignalSleepReady();
        }

    }
}
