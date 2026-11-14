using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Menus;

namespace WhaleAutoLoad
{
    /// <summary>
    /// 启动后自动加载存档：优先读本目录 config.json 的 save 字段，
    /// 没配就挑 Saves 目录里最近修改的那个。加载成功后不再动作；按 F8 可取消（本次）。
    /// </summary>
    public class ModEntry : Mod
    {
        private bool _done;
        private string _save = "";
        private int _delayTicks = 0;

        public override void Entry(IModHelper helper)
        {
            _save = ReadConfiguredSave();
            Helper.Events.GameLoop.UpdateTicked += OnUpdateTicked;
            Helper.Events.Input.ButtonPressed += (s, e) =>
            {
                if (e.Button == SButton.F8) { _done = true; Monitor.Log("AutoLoad 已取消（F8）", LogLevel.Warn); }
            };
            Monitor.Log($"WhaleAutoLoad 就绪，目标存档：{(_save == "" ? "(自动取最新)" : _save)}", LogLevel.Info);
        }

        private string ReadConfiguredSave()
        {
            try
            {
                var path = Path.Combine(Helper.DirectoryPath, "config.json");
                if (!File.Exists(path)) return "";
                var jo = JObject.Parse(File.ReadAllText(path));
                return (string)jo["save"] ?? "";
            }
            catch (Exception ex)
            {
                Monitor.Log($"读 config.json 失败：{ex.Message}", LogLevel.Warn);
                return "";
            }
        }

        private string LatestSave()
        {
            try
            {
                var dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                                       "StardewValley", "Saves");
                if (!Directory.Exists(dir)) return "";
                var best = new DirectoryInfo(dir).GetDirectories()
                    .OrderByDescending(d => d.LastWriteTimeUtc).FirstOrDefault();
                return best?.Name ?? "";
            }
            catch (Exception ex)
            {
                Monitor.Log($"找存档失败：{ex.Message}", LogLevel.Warn);
                return "";
            }
        }

        private void OnUpdateTicked(object sender, UpdateTickedEventArgs e)
        {
            if (_done) return;
            if (!(Game1.activeClickableMenu is TitleMenu)) return;
            if (e.Ticks % 30 != 0) return;

            _delayTicks += 30;
            if (_delayTicks < 60) return;   // 等标题界面稳定 ~1 秒

            string save = string.IsNullOrEmpty(_save) ? LatestSave() : _save;
            if (string.IsNullOrEmpty(save))
            {
                _done = true;
                Monitor.Log("没有找到存档，取消自动加载", LogLevel.Warn);
                return;
            }

            _done = true;
            try
            {
                Game1.activeClickableMenu = null;
                SaveGame.Load(save);
                Monitor.Log($"正在加载存档：{save}", LogLevel.Info);
            }
            catch (Exception ex)
            {
                Monitor.Log($"自动加载失败：{ex}", LogLevel.Error);
            }
        }
    }
}
