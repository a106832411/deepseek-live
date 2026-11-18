using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.TerrainFeatures;
using StardewValley.Objects;
using SObject = StardewValley.Object;

namespace StardewMCPBridge
{
    /// <summary>
    /// Direct game-object manipulation for farm actions.
    /// No Farmer instance needed — we interact with tiles directly.
    /// </summary>
    public static class CompanionActions
    {
        /// <summary>Water a crop at the given tile.</summary>
        public static bool WaterTile(GameLocation location, Vector2 tile, IMonitor monitor)
        {
            if (location.terrainFeatures.TryGetValue(tile, out var feature) && feature is HoeDirt dirt)
            {
                if (dirt.state.Value != 1) // not already watered
                {
                    dirt.state.Value = 1;
                    location.temporarySprites.Add(new TemporaryAnimatedSprite(
                        "TileSheets\\animations", new Rectangle(0, 0, 64, 64),
                        50f, 9, 1, tile * 64f, false, false, 0.01f, 0.01f,
                        Color.White, 1f, 0f, 0f, 0f
                    ));
                    monitor.Log($"Watered tile at ({tile.X}, {tile.Y})", LogLevel.Trace);
                    return true;
                }
            }
            return false;
        }

        /// <summary>Harvest a ready crop at the given tile.</summary>
        public static bool HarvestTile(GameLocation location, Vector2 tile, IMonitor monitor)
        {
            if (location.terrainFeatures.TryGetValue(tile, out var feature) && feature is HoeDirt dirt)
            {
                if (dirt.crop != null && dirt.readyForHarvest())
                {
                    bool success = dirt.crop.harvest((int)tile.X, (int)tile.Y, dirt, null);
                    if (success)
                    {
                        monitor.Log($"Harvested crop at ({tile.X}, {tile.Y})", LogLevel.Trace);
                        return true;
                    }
                }
            }
            return false;
        }

        /// <summary>Clear a debris object (stone, weed, twig) at the given tile.</summary>
        public static bool ClearDebris(GameLocation location, Vector2 tile, IMonitor monitor)
        {
            if (location.objects.TryGetValue(tile, out var obj))
            {
                string name = obj.Name ?? "";
                // Stone, Weeds, Twigs
                if (name.Contains("Stone") || name.Contains("Weed") || name.Contains("Twig")
                    || obj.ParentSheetIndex == 294 || obj.ParentSheetIndex == 295
                    || obj.ParentSheetIndex == 343 || obj.ParentSheetIndex == 450)
                {
                    obj.performRemoveAction();
                    location.objects.Remove(tile);
                    monitor.Log($"Cleared debris at ({tile.X}, {tile.Y}): {name}", LogLevel.Trace);
                    return true;
                }
            }
            return false;
        }

        /// <summary>Hoe the ground at the given tile to create farmable dirt.</summary>
        public static bool HoeTile(GameLocation location, Vector2 tile, IMonitor monitor)
        {
            if (!location.terrainFeatures.ContainsKey(tile)
                && !location.objects.ContainsKey(tile)
                && location.doesTileHaveProperty((int)tile.X, (int)tile.Y, "Diggable", "Back") != null)
            {
                location.terrainFeatures.Add(tile, new HoeDirt(0, location));
                monitor.Log($"Hoed tile at ({tile.X}, {tile.Y})", LogLevel.Trace);
                return true;
            }
            return false;
        }

        /// <summary>Scan a location for tiles that need work and return a prioritized task list.</summary>
        public static List<FarmTask> ScanForTasks(GameLocation location, IMonitor monitor)
        {
            var tasks = new List<FarmTask>();

            foreach (var pair in location.terrainFeatures.Pairs)
            {
                if (pair.Value is HoeDirt dirt)
                {
                    // Harvest-ready crops (highest priority)
                    if (dirt.crop != null && dirt.readyForHarvest())
                    {
                        tasks.Add(new FarmTask
                        {
                            Type = FarmTaskType.Harvest,
                            Tile = pair.Key,
                            Priority = 10
                        });
                    }
                    // Unwatered crops
                    else if (dirt.crop != null && dirt.state.Value != 1 && !Game1.isRaining)
                    {
                        tasks.Add(new FarmTask
                        {
                            Type = FarmTaskType.Water,
                            Tile = pair.Key,
                            Priority = 8
                        });
                    }
                }
            }

            // Debris on the farm
            foreach (var pair in location.objects.Pairs)
            {
                var obj = pair.Value;
                string name = obj.Name ?? "";
                if (name.Contains("Stone") || name.Contains("Weed") || name.Contains("Twig")
                    || obj.ParentSheetIndex == 294 || obj.ParentSheetIndex == 295
                    || obj.ParentSheetIndex == 343 || obj.ParentSheetIndex == 450)
                {
                    tasks.Add(new FarmTask
                    {
                        Type = FarmTaskType.ClearDebris,
                        Tile = pair.Key,
                        Priority = 3
                    });
                }
            }

            // Sort by priority descending, then distance to center
            tasks.Sort((a, b) => b.Priority.CompareTo(a.Priority));
            return tasks;
        }

        /// <summary>Execute a task at a tile.</summary>
        public static bool ExecuteTask(FarmTask task, GameLocation location, IMonitor monitor)
        {
            switch (task.Type)
            {
                case FarmTaskType.Water:
                    return WaterTile(location, task.Tile, monitor);
                case FarmTaskType.Harvest:
                    return HarvestTile(location, task.Tile, monitor);
                case FarmTaskType.ClearDebris:
                    return ClearDebris(location, task.Tile, monitor);
                case FarmTaskType.Hoe:
                    return HoeTile(location, task.Tile, monitor);
                default:
                    return false;
            }
        }
    }

    public enum FarmTaskType
    {
        Water,
        Harvest,
        Hoe,
        ClearDebris,
        Plant
    }

    public class FarmTask
    {
        public FarmTaskType Type { get; set; }
        public Vector2 Tile { get; set; }
        public int Priority { get; set; }
    }

    /// <summary>播种（鲸live 补丁：原版只有 Plant 枚举值，没有实现）。</summary>
    public static class WhalePlant
    {
        public static bool PlantTile(GameLocation location, Vector2 tile, Item seedItem, Farmer who, IMonitor monitor)
        {
            if (seedItem == null) { monitor?.Log("PlantTile: 没有种子", LogLevel.Trace); return false; }
            if (!location.terrainFeatures.TryGetValue(tile, out var feature)
                || !(feature is StardewValley.TerrainFeatures.HoeDirt dirt))
            {
                monitor?.Log($"PlantTile: ({tile.X},{tile.Y}) 不是耕地", LogLevel.Trace);
                return false;
            }
            if (dirt.crop != null)
            {
                monitor?.Log($"PlantTile: ({tile.X},{tile.Y}) 已有作物", LogLevel.Trace);
                return false;
            }

            // 优先调用游戏自身的 plant 逻辑（会处理季节/种子白名单）
            foreach (var m in typeof(StardewValley.TerrainFeatures.HoeDirt).GetMethods()
                         .Where(m => m.Name == "plant"))
            {
                var ps = m.GetParameters();
                var args = new object[ps.Length];
                bool ok = true;
                for (int i = 0; i < ps.Length; i++)
                {
                    var pt = ps[i].ParameterType;
                    if (pt == typeof(Item)) args[i] = seedItem;
                    else if (pt == typeof(Farmer)) args[i] = who;
                    else if (pt == typeof(bool)) args[i] = false;
                    else if (pt == typeof(string)) args[i] = seedItem.ItemId;
                    else { ok = false; break; }
                }
                if (!ok) continue;
                try
                {
                    if (m.Invoke(dirt, args) is bool b && b) return true;
                }
                catch (Exception ex)
                {
                    monitor?.Log($"PlantTile 调用 plant() 失败：{ex.InnerException?.Message ?? ex.Message}", LogLevel.Trace);
                }
            }

            // 兜底：直接建 Crop
            try
            {
                dirt.crop = new StardewValley.Crop(seedItem.ItemId, (int)tile.X, (int)tile.Y, location);
                if (dirt.crop != null) return true;
            }
            catch (Exception ex)
            {
                monitor?.Log($"PlantTile 兜底失败：{ex.Message}", LogLevel.Trace);
            }
            return false;
        }
    }
}
