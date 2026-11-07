using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewValley;
using StardewValley.Monsters;
using StardewValley.Objects;
using StardewValley.TerrainFeatures;
using SObject = StardewValley.Object;

namespace StardewMCPBridge
{
    /// <summary>
    /// Scans tiles around a companion and builds a structured view of their surroundings.
    /// This is how the AI companion "sees" the world.
    /// </summary>
    public static class SurroundingsScanner
    {
        private static readonly HashSet<string> CompanionNames = new HashSet<string> { "Companion1", "Companion2" };

        /// <summary>Scan tiles around a position and return structured data for the bridge.</summary>
        public static ScanResult Scan(GameLocation location, Vector2 centerTile, int radius = 8)
        {
            var result = new ScanResult
            {
                Location = location.Name,
                CenterX = (int)centerTile.X,
                CenterY = (int)centerTile.Y,
                Radius = radius
            };

            int minX = (int)centerTile.X - radius;
            int maxX = (int)centerTile.X + radius;
            int minY = (int)centerTile.Y - radius;
            int maxY = (int)centerTile.Y + radius;

            for (int x = minX; x <= maxX; x++)
            {
                for (int y = minY; y <= maxY; y++)
                {
                    var tile = new Vector2(x, y);
                    var tileInfo = ScanTile(location, tile);
                    if (tileInfo != null)
                        result.Tiles.Add(tileInfo);
                }
            }

            // Characters (NPCs, monsters) in the area
            foreach (var character in location.characters)
            {
                var charTile = character.Tile;
                if (Math.Abs(charTile.X - centerTile.X) <= radius &&
                    Math.Abs(charTile.Y - centerTile.Y) <= radius)
                {
                    if (character is Monster monster)
                    {
                        result.Monsters.Add(new MonsterInfo
                        {
                            Name = monster.Name,
                            X = (int)charTile.X,
                            Y = (int)charTile.Y,
                            Health = monster.Health,
                            MaxHealth = monster.MaxHealth
                        });
                    }
                    else if (!CompanionNames.Contains(character.Name))
                    {
                        result.Npcs.Add(new NpcInfo
                        {
                            Name = character.Name,
                            X = (int)charTile.X,
                            Y = (int)charTile.Y
                        });
                    }
                }
            }

            return result;
        }

        private static TileInfo ScanTile(GameLocation location, Vector2 tile)
        {
            int x = (int)tile.X;
            int y = (int)tile.Y;

            bool isWater = location.isWaterTile(x, y);
            bool passable;
            try
            {
                // 单参数重载；失败再用 viewport 版
                passable = location.isTilePassable(new Vector2(x, y));
            }
            catch
            {
                try { passable = location.isTilePassable(new xTile.Dimensions.Location(x, y), Game1.viewport); }
                catch { passable = false; }
            }
            if (isWater) passable = false;

            string terrainType = null;
            string cropName = null;
            bool cropReady = false;
            int waterState = -1; // -1 = no dirt

            string objectName = null;
            string objectType = null;
            bool breakable = false;
            bool interactable = false;

            if (location.terrainFeatures.TryGetValue(tile, out var feature))
            {
                if (feature is HoeDirt dirt)
                {
                    terrainType = "hoeDirt";
                    waterState = dirt.state.Value; // 0=dry, 1=watered
                    if (dirt.crop != null)
                    {
                        cropName = dirt.crop.indexOfHarvest.Value;
                        // Try to resolve display name
                        try
                        {
                            var item = ItemRegistry.Create("(O)" + dirt.crop.indexOfHarvest.Value);
                            cropName = item?.DisplayName ?? cropName;
                        }
                        catch { }
                        cropReady = dirt.readyForHarvest();
                    }
                }
                else if (feature is Tree tree)
                {
                    terrainType = "tree";
                    objectType = "tree";
                    objectName = tree.stump.Value ? "树桩" : "树";
                    breakable = true;
                }
                else if (feature is Grass)
                {
                    terrainType = "grass";
                }
                else if (feature is Bush)
                {
                    terrainType = "bush";
                }
            }

            // 梯子 / 竖井：矿洞里靠 Action 属性，不是普通物体
            try
            {
                string act0 = null;
                foreach (var lay in new[] { "Back", "Buildings", "Front", "AlwaysFront" })
                {
                    var a = location.doesTileHaveProperty(x, y, "Action", lay);
                    if (!string.IsNullOrEmpty(a) && (a.IndexOf("Ladder", StringComparison.OrdinalIgnoreCase) >= 0 || a.IndexOf("Shaft", StringComparison.OrdinalIgnoreCase) >= 0))
                    { act0 = a; break; }
                }
                if (!string.IsNullOrEmpty(act0))
                {
                    objectName = "梯子";
                    objectType = "ladder";
                    interactable = true;
                }
            }
            catch { }

            // 资源团：大石头/大木桩/大圆木（不是普通 object，单独存在 resourceClumps）
            try
            {
                foreach (var rc in location.resourceClumps)
                {
                    int rx = (int)rc.Tile.X, ry = (int)rc.Tile.Y;
                    int rw = rc.width.Value, rh = rc.height.Value;
                    if (x >= rx && x < rx + rw && y >= ry && y < ry + rh)
                    {
                        int idx = rc.parentSheetIndex.Value;
                        objectName = "资源团";
                        objectType = (idx == 600 || idx == 602) ? "clump_axe" : "clump_pick";
                        breakable = true;
                        passable = false;
                        break;
                    }
                }
            }
            catch { }

            if (location.objects.TryGetValue(tile, out var obj))
            {
                objectName = obj.DisplayName ?? obj.Name;
                objectType = GetObjectType(obj);
                breakable = IsBreakable(obj);
                interactable = IsInteractable(obj);
            }

            // 有物体的格子（石头/杂草/树枝/机器等）玩家走不进去，统一标成不可通行
            if (objectName != null) passable = false;
            // 树/灌木也挡路
            if (terrainType == "tree" || terrainType == "bush") passable = false;

            // 包含所有格子（含可通行空地），方便上层做寻路/找水边
            bool hasContent = terrainType != null || objectName != null || isWater || !passable;
            if (!hasContent && !passable) return null;

            return new TileInfo
            {
                X = x,
                Y = y,
                Passable = passable,
                IsWater = isWater,
                Terrain = terrainType,
                CropName = cropName,
                CropReady = cropReady,
                WaterState = waterState,
                ObjectName = objectName,
                ObjectType = objectType,
                Breakable = breakable,
                Interactable = interactable
            };
        }

        private static string GetObjectType(SObject obj)
        {
            if (obj is Chest) return "chest";
            string nm = obj.Name ?? "";
            string tp = obj.Type ?? "";
            if (nm.Contains("Weed")) return "weed";
            if (nm.Contains("Twig")) return "twig";
            if (nm.Contains("Ladder") || nm.Contains("Shaft")) return "ladder";
            if (tp == "Ore" || nm.Contains("Node") || nm.Contains("Ore")) return "ore";
            if (tp == "Minerals") return "mineral";
            if (tp == "Stone" || nm.Contains("Stone") || nm.Contains("Rock") || nm.Contains("Geode")) return "stone";
            if (obj.bigCraftable.Value) return "machine";
            return "object";
        }

        private static bool IsBreakable(SObject obj)
        {
            string nm = obj.Name ?? "";
            string tp = obj.Type ?? "";
            if (nm.Length == 0) return false;
            if (tp == "Stone" || tp == "Ore" || tp == "Litter" || tp == "Minerals" || tp == "Twig" || tp == "Weed") return true;
            if (nm.Contains("Stone") || nm.Contains("Weed") || nm.Contains("Twig")
                || nm.Contains("Node") || nm.Contains("Ore") || nm.Contains("Rock") || nm.Contains("Geode")
                || nm.Contains("Crystal") || nm.Contains("Quartz") || nm.Contains("Frozen Tear") || nm.Contains("Fire Quartz"))
                return true;
            // 常见可敲物件：石头 294/295/343/450、晶球 44/45/46/47、矿物节点等
            int idx = obj.ParentSheetIndex;
            return idx == 294 || idx == 295 || idx == 343 || idx == 450 || idx == 44 || idx == 45 || idx == 46 || idx == 47;
        }

        private static bool IsInteractable(SObject obj)
        {
            if (obj is Chest) return true;
            if (obj.bigCraftable.Value) return true; // machines
            if (obj.Name != null && (obj.Name.Contains("Ladder") || obj.Name.Contains("Shaft"))) return true;
            return false;
        }
    }

    // ==============================
    // Data classes for serialization
    // ==============================

    public class ScanResult
    {
        public string Location { get; set; }
        public int CenterX { get; set; }
        public int CenterY { get; set; }
        public int Radius { get; set; }
        public List<TileInfo> Tiles { get; set; } = new List<TileInfo>();
        public List<MonsterInfo> Monsters { get; set; } = new List<MonsterInfo>();
        public List<NpcInfo> Npcs { get; set; } = new List<NpcInfo>();
    }

    public class TileInfo
    {
        public int X { get; set; }
        public int Y { get; set; }
        public bool Passable { get; set; }
        public bool IsWater { get; set; }
        public string Terrain { get; set; }
        public string CropName { get; set; }
        public bool CropReady { get; set; }
        public int WaterState { get; set; }
        public string ObjectName { get; set; }
        public string ObjectType { get; set; }
        public bool Breakable { get; set; }
        public bool Interactable { get; set; }
    }

    public class MonsterInfo
    {
        public string Name { get; set; }
        public int X { get; set; }
        public int Y { get; set; }
        public int Health { get; set; }
        public int MaxHealth { get; set; }
    }

    public class NpcInfo
    {
        public string Name { get; set; }
        public int X { get; set; }
        public int Y { get; set; }
    }
}
