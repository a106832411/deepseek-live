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
        public static bool AssistFishing = false;
        /// <summary>钓鱼小游戏自动操作（让绿条自己追鱼，保留小游戏动画）。</summary>
        public static bool AutoPlayMinigame = false;
        // 2026-11-22：举鱼动画兜底——动画被打断也要把鱼补进背包
        public static Item PendingCatchFish = null;
        public static int PendingCatchTicks = 0;
        public static int PendingCatchBefore = 0;

        public static bool TreasureAssist = true;
        public static Action<string> Trace = null;
        private string ordersDir;
        private string statePath;
        private int toolTicks = -1;
        private int stuckToolTicks = 0;
        private int barTicks = 0;
        private bool barSeen = false;
        private Vector2 lastPos = Vector2.Zero;
        private int stillTicks = 0;   // 本竿的小游戏是否见过   // 钓鱼小游戏看门狗   // 挥动/蓄力卡死看门狗计数
        private string pendingKind = "";
        private Vector2 pendingTile;
        private GameLocation pendingLoc;
        private int closeMenuInTicks = -1;
        private int pendingPower = 1;
        private int bedFixTicks = 0;
        private int bedFixCount = 0;
        private int fixCooldownTicks = 0;
        private int dlgStuckTicks = 0;
        private bool gotoShop = false;
        private double stateWriteAccumMs = 0;   // 状态写盘节流（按真实毫秒）     // 兼容旧名：跨图状态机
        private System.Collections.IEnumerator saveEnum = null;
        private bool saveRequested = false;
        private bool autoFish = false;
        private bool biteClicked = false;
        private Vector2 fishSpot = new Vector2(-1, -1);
        private int castCooldown = 0;
        private bool fishingPump = false;
        private int lastFishCount = 0;
        private int waitTicks = 0;
        private int biteCountdown = -1;
        private string pendingCatch = null;
        private int castingPending = -1;
        private int caughtTicks = 0;

        private int dtMs = 16;   // 本 tick 真实耗时(ms)：游戏 ~900fps，不能再拿 tick 当 1/60 秒
        private bool holdingDone = false;
        private bool catchDone = false;
        private int hopIndex = 0;
        private int hopFailTotal = 0;   // 门口反复走不到的累计次数（用于兜底传送）
        private Dictionary<string, HashSet<string>> graph = null;
        private string travelDest = null;  // 目标场景（goto 动作）
        private Point hopApproach = Point.Zero;
        private Point hopWarp = Point.Zero;
        private int warpCooldown = 0;
        private Vector2 doorTile = new Vector2(-1, -1);
        private string doorTileLoc = "";   // doorTile 缓存属于哪张图（换图必须重扫）
        private Vector2 mineDoor = new Vector2(-1, -1);   // Mountain 里矿洞入口那扇门
        private Vector2 pendingDoorTile = new Vector2(-1, -1);
        private int pendingDoorTicks = 0;
        private string pendingDoorAction = null;
        private readonly Dictionary<string, int> hopAttempts = new Dictionary<string, int>();
        private int shopWaitTicks = 0;
        private string lastOrder = "等待指令";          // HUD：最近收到的一条指令
        private string hudPlot = "";                 // HUD：固定农田区（读 .plot_rect）
        private long hudPlotReadTicks = 0;
        // 看得见的买东西（2026-11-21 用户要求）：打开商店界面后分几次扣钱+入包，播收银音效
        private bool buyPending = false;
        private ISalable buyTarget = null;
        private int buyUnit = 0;
        private int buyLeft = 0;
        private int buyChunk = 1;
        private int buyDelayTicks = 0;
        private int buySpent = 0;
        private int canMoveStuckTicks = 0;   // 钓鱼把 canMove 锁死的看门狗

        public override void Entry(IModHelper helper)
        {
            this.ordersDir = Path.Combine(helper.DirectoryPath, "orders");
            this.statePath = Path.Combine(helper.DirectoryPath, "player_state.json");
            Directory.CreateDirectory(this.ordersDir);
            helper.Events.GameLoop.UpdateTicked += this.OnUpdateTicked;
            // 游戏内左上角面板默认关闭（2026-11-18 用户要求改到 OBS 叠加层显示）；
            // 想临时开回来就把下面这行的注释去掉。
            // helper.Events.Display.RenderedHud += this.OnRenderedHud;
            helper.Events.GameLoop.SaveLoaded += (s, e) => { this.Monitor.Log("WhalePlayer: 已进入存档", LogLevel.Info); this.fixCooldownTicks = 1800; };
            Trace = msg => this.Monitor.Log(msg, LogLevel.Alert);
            try
            {
                var harmony = new HarmonyLib.Harmony("Whale.WhalePlayer.patch");
                int ok = 0, fail = 0;
                foreach (var type in typeof(ModEntry).Assembly.GetTypes())
                {
                    if (type.GetCustomAttributes(typeof(HarmonyLib.HarmonyPatch), true).Length == 0) continue;
                    try { harmony.CreateClassProcessor(type).Patch(); ok++; }
                    catch (Exception ex)
                    {
                        fail++;
                        this.Monitor.Log($"补丁 {type.Name} 失败：{ex.Message}", LogLevel.Warn);
                    }
                }
                this.Monitor.Log($"WhalePlayer: Harmony 补丁 {ok} 个成功、{fail} 个失败", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log($"Harmony 应用失败：{ex.Message}", LogLevel.Error); }
        }

        /// <summary>屏幕左上角状态面板：Mod 是否就绪、农田区域、日期时间、最近指令。</summary>
        private void OnRenderedHud(object sender, RenderedHudEventArgs e)
        {
            try
            {
                if (!Context.IsWorldReady || Game1.player == null) return;

                // 每秒刷新一次农田区域（agent 改 .plot_rect 后这里能立刻看到）
                if (Environment.TickCount64 - this.hudPlotReadTicks > 1000)
                {
                    this.hudPlotReadTicks = Environment.TickCount64;
                    try
                    {
                        string pf = @"D:\live\logs\.plot_rect";
                        if (File.Exists(pf)) this.hudPlot = File.ReadAllText(pf).Trim();
                    }
                    catch { }
                }

                string seasonCn = Game1.currentSeason switch
                {
                    "spring" => "春",
                    "summer" => "夏",
                    "fall" => "秋",
                    "winter" => "冬",
                    _ => Game1.currentSeason ?? "?"
                };
                int hh = Game1.timeOfDay / 100, mm = Game1.timeOfDay % 100;

                var lines = new List<string>
                {
                    "鲸live · WhalePlayer 就绪",
                    "农田区 " + (this.hudPlot.Length > 0 ? this.hudPlot : "未设置（.plot_rect）"),
                    string.Format("第 {0} 年 {1}季 第 {2} 天  {3:00}:{4:00}", Game1.year, seasonCn, Game1.dayOfMonth, hh, mm),
                    "最近指令 " + (this.lastOrder ?? "—"),
                };

                var font = Game1.smallFont;
                var b = e.SpriteBatch;
                float maxW = 0f;
                foreach (var l in lines) maxW = Math.Max(maxW, font.MeasureString(l).X);
                const int pad = 8, lineGap = 2;
                var rect = new Rectangle(12, 12,
                    (int)maxW + pad * 2 + 6,
                    lines.Count * (font.LineSpacing + lineGap) + pad * 2 - lineGap);

                b.Draw(Game1.staminaRect, rect, Color.Black * 0.55f);
                b.Draw(Game1.staminaRect, new Rectangle(rect.X, rect.Y, 4, rect.Height), Color.LimeGreen * 0.9f);

                for (int i = 0; i < lines.Count; i++)
                {
                    var color = i == 0 ? Color.LimeGreen : Color.White;
                    b.DrawString(font, lines[i],
                        new Vector2(rect.X + pad + 4, rect.Y + pad + i * (font.LineSpacing + lineGap)), color);
                }
            }
            catch { }
        }

        private void OnUpdateTicked(object sender, UpdateTickedEventArgs e)
        {
            this.dtMs = Math.Max(1, Math.Min(100, (int)Game1.currentGameTime.ElapsedGameTime.TotalMilliseconds));
            // 自动修复：早上已经能动了但 isInBed 还挂着（会一直躺床姿势）
            try
            {
                if (this.fixCooldownTicks > 0) this.fixCooldownTicks--;
                if (this.fixCooldownTicks > 0) return;   // 读档后20秒内绝不动，交给游戏自己起床
                if (Game1.player != null && Game1.player.isInBed.Value
                    && Game1.player.canMove && Game1.currentLocation != null
                    && Game1.timeOfDay >= 700)
                {
                    if (this.bedFixTicks++ > 30)
                    {
                        // 换天流程卡死时强制清掉过渡状态
                        try { if (Game1.newDay) Game1.newDay = false; } catch { }
                        try { Game1.fadeToBlack = false; } catch { }
                        try { Game1.globalFade = false; } catch { }
                        this.WakeUp();
                        this.bedFixTicks = 0;
                        this.bedFixCount++;
                        // 连续 3 次还赖在床上 → 人卡在床格上，往下挪一格再解一次
                        if (this.bedFixCount >= 1)
                        {
                            this.bedFixCount = 0;
                            try
                            {
                                Game1.player.Position = Game1.player.Position + new Vector2(0f, 64f);
                                Game1.player.isInBed.Value = false;
                                Game1.player.canMove = true;
                                Game1.player.completelyStopAnimatingOrDoingAction();
                                this.Monitor.Log("看门狗：把卡在床上的角色挪到床下一格", LogLevel.Warn);
                            }
                            catch { }
                        }
                        this.Monitor.Log("自动修复：强制清掉卡住的换天状态并起床", LogLevel.Warn);
                    }
                }
                else { this.bedFixTicks = 0; this.bedFixCount = 0; }
            }
            catch { }

            // 2026-11-22：抛竿中途失败会把 canMove 永久锁死（实测整局时钟冻在 10:10，18 分钟没人动）
            try
            {
                if (this.fishingPump && Game1.player != null && !Game1.player.canMove
                    && Game1.activeClickableMenu == null && !Game1.player.UsingTool)
                {
                    this.canMoveStuckTicks++;
                    if (this.canMoveStuckTicks > 180)
                    {
                        this.canMoveStuckTicks = 0;
                        try { Game1.player.UsingTool = false; Game1.player.canMove = true; } catch { }
                        try { Game1.player.completelyStopAnimatingOrDoingAction(); } catch { }
                        this.Monitor.Log("看门狗：钓鱼把 canMove 锁死了，强制复位", LogLevel.Warn);
                    }
                }
                else this.canMoveStuckTicks = 0;
            }
            catch { }
            // 寻路卡死看门狗：controller 还在但人 6 秒没挪窝，就清掉控制器
            try
            {
                if (Game1.player != null && Game1.player.controller != null && !Game1.player.isInBed.Value)
                {
                    var cur = Game1.player.Position;
                    if (this.lastPos == cur) this.stillTicks++;
                    else { this.lastPos = cur; this.stillTicks = 0; }
                    if (this.stillTicks > 360)
                    {
                        this.stillTicks = 0;
                        try { Game1.player.controller = null; } catch { }
                                                this.Monitor.Log("看门狗：寻路卡住 6 秒，清掉控制器", LogLevel.Warn);
                    }
                }
                else this.stillTicks = 0;
            }
            catch { }

            if (!Context.IsWorldReady) return;
            try { this.CheckToolDrift(); } catch { }

            // 钓鱼泵：抛竿 → 等咬钩 → 提竿 → 等鱼真入包 → 才抛下一竿
            // 举鱼动画期间（PendingCatchFish 未清）先别插手，否则会把动画/入包打断
            if (this.fishingPump && PendingCatchFish == null)
            {
                var p0 = Game1.player;
                var rd = p0.Items.OfType<StardewValley.Tools.FishingRod>().FirstOrDefault();
                if (rd == null) { this.fishingPump = false; }

                // 有菜单：自动收下物品并关闭（鱼获/宝箱都在 ItemGrabMenu 里）
                else if (Game1.activeClickableMenu != null)
                {
                    var menu = Game1.activeClickableMenu;
                    if (!(menu is StardewValley.Menus.BobberBar)) this.barTicks = 0;
                    if (menu is StardewValley.Menus.BobberBar)
                    {
                        // 2026-11-23：删掉这里的「看门狗强制关闭」。
                        // 实测它用 tick 计数当时间（游戏跑 900+ fps 时 4 秒就到 3600），
                        // 而且从 UpdateTicked 里调 Game1.exitActiveMenu() 会把 mod 自己的 tick 卡死
                        // （之后 player_state.json 不再更新、订单也不消费，整局钓鱼死掉）。
                        // 兜底改到 BobberBarAutoPlayPatch 里按真实时间结算（30 秒），
                        // 那边是在 update 内部关菜单，跟原版收尾同一个位置，安全。
                        this.barTicks++;
                    }
                    else if (menu is StardewValley.Menus.ItemGrabMenu grab)
                    {
                        int taken = 0;
                        try
                        {
                            var inv = grab.ItemsToGrabMenu?.actualInventory;
                            if (inv != null)
                            {
                                for (int i = 0; i < inv.Count(); i++)
                                {
                                    var it = inv[i];
                                    if (it == null) continue;
                                    if (p0.addItemToInventoryBool(it, false)) { inv[i] = null; taken++; }
                                }
                            }
                        }
                        catch (Exception ex) { this.Monitor.Log($"收物异常：{ex.Message}", LogLevel.Warn); }
                        try { grab.exitThisMenu(true); } catch { }
                        this.Monitor.Log($"钓鱼: 收下 {taken} 件物品并关闭菜单", LogLevel.Info);
                        this.castCooldown = 1500;
                    }
                    else
                    {
                        try { menu.exitThisMenu(true); } catch { }
                        this.Monitor.Log($"钓鱼: 关闭菜单 {menu.GetType().Name}", LogLevel.Trace);
                        this.castCooldown = 1000;
                    }
                }
                else
                {
                    // fishCaught 时先等收尾动画（约 2 秒）再复位，太早会丢掉入包时机
                    if (Game1.activeClickableMenu is StardewValley.Menus.BobberBar)
                    {
                        // 小游戏进行中：交给 BobberBarAutoPlayPatch，别插手（原版会自动发鱼）
                        // 2026-11-23：新一局开始就重置看门狗计数，否则连钓时计数会被上一局带高，
                        // 出现过「刚开局 2 秒就被看门狗强制关闭」。
                        if (!this.barSeen) this.barTicks = 0;
                        this.caughtTicks = 0;
                        this.barSeen = true;
                        return;
                    }
                    if (this.barSeen)
                    {
                        // 小游戏刚结束：让原版的举鱼动画走完，再抛下一竿
                        this.barSeen = false;
                        // 原版自己结算掉了就清掉待处理鱼，别留到下一局
                        // 小游戏结束后，替原版鱼竿 tick ~3 秒，推 pullFishFromWater 事件（提竿/飞鱼/举鱼）
                        try { BobberBarAutoPlayPatch.PendingFish = null; } catch { }
                        this.castCooldown = 500;
                        this.waitTicks = 0;
                        this.Monitor.Log("钓鱼: 小游戏结束，准备下一竿", LogLevel.Info);
                        return;
                    }
                    if (rd.fishCaught)
                    {
                        this.caughtTicks += this.dtMs;
                        if (this.caughtTicks > 1000 && !this.holdingDone)
                        {
                            this.holdingDone = true;
                            // 小游戏结束后原版自己发鱼，这里不再调用 doneHoldingFish
                            this.catchDone = true; this.Monitor.Log("钓鱼: 本条完成，准备下一竿", LogLevel.Info);
                            this.catchDone = true;
                            try { rd.fishCaught = false; rd.isReeling = false; rd.isFishing = false; rd.isNibbling = false; rd.hit = false; } catch { }
                            this.castCooldown = 2500;
                        }
                        return;
                    }
                    else this.caughtTicks = 0;
                    // 用 doneHoldingFish 的成功调用作为「这条到手」的唯一信号
                    if (this.catchDone)
                    {
                        this.catchDone = false;
                        this.castCooldown = 400;
                        this.waitTicks = 0;
                        this.Monitor.Log("钓鱼: 本条结算完成，准备下一竿", LogLevel.Info);
                    }
                    // 抛竿动画进行中
                    else if (this.castingPending > 0)
                    {
                        this.castingPending -= this.dtMs;
                        if (this.castingPending <= 0)
                        {
                            try { rd.castingEndFunction(p0); } catch { }
                            // castingEndFunction 之后必须清掉 isCasting，
                            // 否则 tickUpdate 不推进 fishingBiteAccumulator，永远等不到咬钩（2026-11-19 修）
                            try { rd.isCasting = false; p0.canMove = false; p0.UsingTool = true; } catch { }
                            // 自己给一个极短咬钩时间：CJB 1.38 的 InstantBite 对脚本抛竿不生效，
                            // 不设的话原版要 3~10 秒，远超我们的等待窗口（2026-11-19）
                            // 2026-11-23：250ms 太「秒咬」了，改 1.2 秒，看起来像真等鱼咬。
                            try { rd.timeUntilFishingBite = 1200f; rd.fishingBiteAccumulator = 0f; } catch { }
                            // 落点：沿朝向在 1~12 格里找水；找不到就退回 go_fish 扫出来的水面点。
                            // 2026-11-23：以前找不到就直接 stop 整个钓鱼泵（表现为「钓着钓着就不钓了」）。
                            try
                            {
                                int dir = p0.FacingDirection;
                                int dx = dir == 1 ? 1 : (dir == 3 ? -1 : 0);
                                int dy = dir == 2 ? 1 : (dir == 0 ? -1 : 0);
                                int tx = (int)p0.Tile.X, ty = (int)p0.Tile.Y;
                                int best = -1;
                                for (int d = 1; d <= 12; d++)
                                {
                                    int x = tx + dx * d, y = ty + dy * d;
                                    bool water = false;
                                    try { water = p0.currentLocation.doesTileHaveProperty(x, y, "Water", "Back") != null; } catch { }
                                    if (water) best = d;
                                }
                                if (best > 0)
                                    rd.bobber.Value = new Microsoft.Xna.Framework.Vector2((tx + dx * best) * 64f + 32f, (ty + dy * best) * 64f + 32f);
                                else if (this.fishSpot.X >= 0)
                                {
                                    this.Monitor.Log($"钓鱼: 朝向没水，用 go_fish 的水面点 ({this.fishSpot.X},{this.fishSpot.Y})", LogLevel.Info);
                                    rd.bobber.Value = new Microsoft.Xna.Framework.Vector2(this.fishSpot.X * 64f + 32f, this.fishSpot.Y * 64f + 32f);
                                }
                                else
                                    this.Monitor.Log("钓鱼: 没找到水面，这一竿落空，下一轮重试", LogLevel.Warn);
                            }
                            catch { }
                            try
                            {
                                rd.isNibbling = false; rd.hit = false;
                            }
                            catch { }
                            this.Monitor.Log($"钓鱼: 抛竿完成 isFishing={rd.isFishing} bite={rd.timeUntilFishingBite:F1}", LogLevel.Info);
                        }
                    }
                    // 正在等咬钩
                    else if (rd.isFishing && !rd.isNibbling)
                    {
                        // 交给游戏 tick 咬钩；但等太久就复位重抛
                        this.waitTicks += this.dtMs;
                        if (this.waitTicks > 2000)
                        {
                            this.waitTicks = 0;
                            this.castCooldown = 500;
                            // 重新走一次真实抛竿，不再用 doneFishing 假复位
                            try
                            {
                                this.SelectTool(p0, rd);
                                if (this.fishSpot.X >= 0) this.Face(this.fishSpot);
                                rd.beginUsing(p0.currentLocation, (int)this.fishSpot.X, (int)this.fishSpot.Y, p0);
                                this.castingPending = 750;
                            }
                            catch { }
                            this.Monitor.Log("钓鱼: 重抛", LogLevel.Trace);
                        }
                    }
                    // 咬钩 → 提竿
                    else if (rd.isNibbling)
                    {
                        if (this.castCooldown > 0) { this.castCooldown -= this.dtMs; return; }
                        // 一次咬钩只提一次竿：先清 nibbling，冷却期间不会再进这里
                        this.castCooldown = 2000;
                        try
                        {
                            if (WhalePlayer.ModEntry.AutoPlayMinigame)
                            {
                                // 2026-11-21：原版只在 `!hit` 时才进「提竿 → startMinigameEndFunction」
                                // 分支（FishingRod.cs:645），所以这里必须把 hit 置回 false、保留 nibbling，
                                // 游戏才会播「!」并弹出 BobberBar 小游戏。
                                rd.hit = false;
                                rd.isNibbling = true;
                                rd.castedButBobberStillInAir = false;
                                rd.DoFunction(p0.currentLocation, (int)rd.bobber.X, (int)rd.bobber.Y, 1, p0);
                                this.Monitor.Log("钓鱼: 提竿（小游戏）menu=" + (Game1.activeClickableMenu?.GetType().Name ?? "null"), LogLevel.Info);
                            }
                            else
                            {
                                rd.isNibbling = false;
                                rd.pullingOutOfWater = false;
                                rd.isCasting = false;
                                rd.hit = false;
                                rd.DoFunction(p0.currentLocation, (int)rd.bobber.X, (int)rd.bobber.Y, 1, p0);
                                this.Monitor.Log("钓鱼: 提竿（一次）menu=" + (Game1.activeClickableMenu?.GetType().Name ?? "null"), LogLevel.Trace);
                            }
                        }
                        catch (Exception ex) { this.Monitor.Log($"提竿异常：{ex.Message}", LogLevel.Warn); }
                    }
                    // 没在钓鱼 → 决定是否抛下一竿
                    else
                    {
                        // 没收到 catchDone 时，最多等 4 秒就重抛
                        this.waitTicks += this.dtMs;
                        if (this.castCooldown > 0) { this.castCooldown -= this.dtMs; return; }
                        // 举鱼动画期间（holdUpItemThenMessage 把 freezePause 拉满）别抛竿：
                        // beginUsing 会 completelyStopAnimatingOrDoingAction，直接把动画打断。
                        if (Game1.player.freezePause > 0) return;
                        // 提竿动画 / 收杆动画没播完也别抛
                        if (rd.pullingOutOfWater || Game1.player.UsingTool) return;
                        if (this.waitTicks < 130) return;
                        this.waitTicks = 0;
                        this.castCooldown = 1000;
                        try
                        {
                            this.SelectTool(p0, rd);
                            if (this.fishSpot.X >= 0) this.Face(this.fishSpot);
                            this.holdingDone = false;
                            rd.beginUsing(p0.currentLocation, (int)this.fishSpot.X, (int)this.fishSpot.Y, p0);
                            this.castingPending = 750;
                            this.Monitor.Log("钓鱼: 抛竿", LogLevel.Trace);
                        }
                        catch { }
                    }
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

            // 挥动/蓄力卡死看门狗：BeginUsingTool 之后若 1 秒还没收手（异常/被摆放物拦截/走位打断），
            // 玩家会永远保持「举着工具蓄力」的姿势，这里强制复位。
            try
            {
                var p2 = Game1.player;
                if (p2 != null && p2.UsingTool && !p2.canReleaseTool && this.toolTicks <= 0)
                {
                    if (++this.stuckToolTicks > 60)
                    {
                        this.stuckToolTicks = 0;
                        try { p2.EndUsingTool(); } catch { }
                        try { p2.UsingTool = false; p2.canReleaseTool = true; } catch { }
                        try { p2.completelyStopAnimatingOrDoingAction(); } catch { }
                        this.Monitor.Log("看门狗：清掉卡住的挥动/蓄力动作", LogLevel.Warn);
                    }
                }
                else this.stuckToolTicks = 0;
            }
            catch { }

            // 被对话框卡住（公交车/商店关门提示等）→ 自动脱困
            if (Game1.dialogueUp)
            {
                this.dlgStuckTicks++;
                if (this.dlgStuckTicks > 45)
                {
                    this.dlgStuckTicks = 0;
                    this.Monitor.Log("被对话卡住 → 自动脱困", LogLevel.Warn);
                    this.Unstick();
                }
            }
            else this.dlgStuckTicks = 0;

            // 界面停留一会儿后自动关闭（商店/提示对话框都关，避免卡住）
            if (this.closeMenuInTicks > 0)
            {
                this.closeMenuInTicks--;
                if (this.closeMenuInTicks == 0 && Game1.activeClickableMenu != null)
                {
                    this.Monitor.Log($"closeMenuInTicks: 关闭 {Game1.activeClickableMenu.GetType().Name}", LogLevel.Trace);
                    Game1.exitActiveMenu();
                    Game1.activeClickableMenu = null;
                }
            }

            // 跨图去商店：每 0.5 秒检查一次，到店/走不动就推进或结束
            if (this.gotoShop && e.IsMultipleOf(30)) this.TickTravel();

            if (e.IsMultipleOf(3)) this.TickCatchVerify();
            if (e.IsMultipleOf(6)) this.TickPurchase();      // 看得见的购买：分批扣钱入包
            if (e.IsMultipleOf(6)) this.ProcessOrders();     // ~0.1s，动作更跟手
            // 900fps 下 tick 计数会退化：按真实时间每 250ms 写一次状态，避免和读取方抢锁
            this.stateWriteAccumMs += Game1.currentGameTime.ElapsedGameTime.TotalMilliseconds;
            if (this.stateWriteAccumMs >= 250) { this.stateWriteAccumMs = 0; this.WriteState(); }
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
            this.lastOrder = action.Length > 0 ? action : "?";
            switch (action)
            {
                case "walk_to": this.WalkTo(jo); break;
                case "exit": this.ExitMap(); break;
                case "use_tool": this.UseTool(jo); break;
                case "interact": this.Interact(jo); break;
                case "harvest": this.Harvest(jo); break;
                case "plant": this.Plant(jo); break;
                case "stop_fish": this.StopFish(); break;
                case "probe_mineshaft": this.ProbeMineShaft(); break;
                case "warp_house": this.WarpHouse(); break;
                case "mine_goto": this.MineGoto(jo["level"] != null ? (int)jo["level"] : 121); break;
                case "upgrade_house": this.UpgradeHouse(); break;
                case "learn_recipe": this.LearnRecipe((string)jo["name"] ?? ""); break;
                case "attach_bait": this.AttachBait(jo["force"] != null && (bool)jo["force"]); break;
                case "fish": this.Fish(); break;
                case "goto_shop": this.StartTravel("SeedShop"); break;
                case "goto": this.StartTravel((string)jo["location"] ?? ""); break;
                case "warp_to": this.WarpDirect((string)jo["location"] ?? ""); break;
                case "shop_buy": this.ShopBuy(jo); break;
                case "ship": this.Ship(); break;
                case "fix_position": this.FixPosition(); break;
                case "wake": this.WakeUp(); break;
                case "sleep": this.Sleep(); break;
                case "save": this.saveRequested = true; break;
                case "give_item": this.GiveItem((string)jo["name"] ?? "", jo["count"] != null ? (int)jo["count"] : 1); break;
                case "withdraw": this.Withdraw(jo); break;
                case "merge_stacks": this.MergeStacks(); break;
                case "scan_actions": this.ScanActions(); break;
                case "scan_water": this.ScanWater(); break;
                case "scan_dig": this.ScanDiggable(); break;
                case "scan_all": this.ScanAll(); break;
                case "scan_near": this.ScanNear(); break;
                case "enter_shop":
                    {
                        var jj = jo ?? new JObject();
                        if (jj["action"] == null) jj["action"] = "SeedShop";
                        this.EnterHouse(jj);
                        break;
                    }
                case "enter_home": this.EnterHome(); break;
                case "enter_house": this.EnterHouse(jo); break;
                case "force_step": this.ForceStep(jo); break;
                case "leave_festival": this.LeaveFestival(); break;
                case "unstick": this.Unstick(); break;
                case "ui_state": this.UiState(); break;
                case "close_menu": this.CloseMenu(); break;
                case "end_event": this.EndEvent(); break;
                case "leave_mine": this.LeaveMine(); break;
                case "reachable": this.Reachable(jo); break;
                case "elevator_go": this.ElevatorGo(jo); break;
                case "use_staircase": this.UseStaircase(jo); break;
                case "place_item": this.PlaceItem(jo); break;
                case "place_stairs": this.PlaceStairs(jo); break;
                case "clean_garbage": this.CleanGarbage(); break;
                case "ensure_tools": this.EnsureTools(); break;
                case "stack_test": this.StackTest(); break;
                case "upgrade_tools": this.UpgradeTools(); break;
                case "fix_tool_frames": this.FixToolFrames(); break;
                case "rebuild_axe": this.RebuildAxe(); break;
                case "scan_objects": this.ScanObjects(); break;
                case "scan_warps": this.ScanWarps(); break;
                case "scan_tools": this.ScanTools(); break;
                case "give_tool": this.GiveTool((string)jo["tool"] ?? ""); break;
                case "give_scythe": this.GiveScythe(); break;
                case "ship_chests": this.ShipFromChests(); break;
                case "pickup_at": this.PickupAt((int)jo["x"], (int)jo["y"]); break;
                case "withdraw_name": this.WithdrawByName((string)jo["name"] ?? "", jo["count"] != null ? (int)jo["count"] : 999); break;
                case "load_machines": this.LoadMachines((string)jo["machine"] ?? "", (string)jo["item"] ?? "", jo["times"] != null ? (int)jo["times"] : 99); break;
                case "smelt": this.Smelt((string)jo["ore"] ?? "Copper", jo["times"] != null ? (int)jo["times"] : 10); break;
                case "collect_machines": this.CollectMachines(); break;
                case "craft": this.Craft((string)jo["item"] ?? "", jo["count"] != null ? (int)jo["count"] : 1); break;
                case "recipes": this.DumpRecipes(); break;
                case "fish_state": this.FishState(); break;
                case "go_fish": this.GoFish(); break;
                case "sort_chests": this.SortChests(); break;
                case "assign_chests": this.AssignChests(jo); break;
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

            // 水面绝对不走（池塘/河道）
            bool isWater = false;
            try { isWater = loc.doesTileHaveProperty(x, y, "Water", "Back") != null; } catch { }
            if (isWater)
            {
                this.Monitor.Log($"walk_to: ({x},{y}) 是水面，拒绝", LogLevel.Warn);
                return;
            }
            if (!this.IsSafeTile(loc, x, y))
            {
                this.Monitor.Log($"walk_to: ({x},{y}) 不可通行/超出地图，拒绝（防穿模）", LogLevel.Warn);
                return;
            }
            if ((int)player.Tile.X == x && (int)player.Tile.Y == y)
            {
                this.Monitor.Log($"walk_to: 已经站在 ({x},{y})", LogLevel.Info);
                return;
            }

            // 目标不可达时：尝试周围一圈（最多半径 3），绕开池塘/障碍
            var targets = new List<Point> { new Point(x, y) };
            for (int r = 1; r <= 3; r++)
            {
                for (int dx = -r; dx <= r; dx++)
                {
                    for (int dy = -r; dy <= r; dy++)
                    {
                        if (Math.Abs(dx) != r && Math.Abs(dy) != r) continue;
                        int tx = x + dx, ty = y + dy;
                        bool w2 = false;
                        try { w2 = loc.doesTileHaveProperty(tx, ty, "Water", "Back") != null; } catch { }
                        if (w2 || !this.IsSafeTile(loc, tx, ty)) continue;
                        targets.Add(new Point(tx, ty));
                    }
                }
            }

            foreach (var t in targets)
            {
                try
                {
                    var controller = new StardewValley.Pathfinding.PathFindController(player, loc, t, 3);
                    if (controller.pathToEndPoint != null && controller.pathToEndPoint.Count > 0)
                    {
                        player.controller = controller;
                        if (t.X == x && t.Y == y)
                            this.Monitor.Log($"walk_to: 开始走向 ({x},{y})", LogLevel.Info);
                        else
                            this.Monitor.Log($"walk_to: ({x},{y}) 不可达，改走附近 ({t.X},{t.Y})", LogLevel.Warn);
                        return;
                    }
                }
                catch (Exception ex)
                {
                    this.Monitor.Log($"walk_to 尝试 ({t.X},{t.Y}) 失败：{ex.Message}", LogLevel.Trace);
                }
            }
            this.Monitor.Log($"walk_to: ({x},{y}) 找不到路径（被水/障碍隔开），放弃", LogLevel.Warn);
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
                "scythe" or "melee" => typeof(MeleeWeapon),
                _ => typeof(Hoe)
            };
            Tool tool = null;
            if (toolName == "scythe")
            {
                tool = player.Items.OfType<Tool>()
                    .FirstOrDefault(t => (t.Name ?? "").Contains("镰刀") || (t.Name ?? "").Contains("Scythe")) as Tool;
            }
            if (tool == null)
                tool = player.Items.FirstOrDefault(i => i is Tool t && toolType.IsInstanceOfType(t)) as Tool;
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

            // 可选：强制朝向（1=右 2=下 3=左 0=上），镰刀横扫效率更高
            int faceOverride = jo["face"] != null ? (int)jo["face"] : -1;
            if (faceOverride >= 0) { try { player.faceDirection(faceOverride); } catch { } }
            else this.Face(tile);
            int toolIdx = player.Items.IndexOf(tool);
            if (toolIdx < 0) { this.Monitor.Log("use_tool: 工具不在背包", LogLevel.Warn); return; }
            player.CurrentToolIndex = toolIdx;   // 用索引选中，避免 CurrentTool 赋值引发覆盖
            this.pendingKind = tool is Hoe ? "hoe" : (tool is WateringCan ? "water" : "clear");
            this.pendingPower = jo["power"] != null ? Math.Max(1, (int)jo["power"]) : 1;
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
            // 关键：调用游戏真实的工具生效逻辑（矿石掉落 / 出梯子 / 砍树 都在 DoFunction 里）
            try
            {
                var curTool = Game1.player.CurrentTool;
                var tloc = this.pendingLoc ?? Game1.player.currentLocation;
                if (curTool != null && tloc != null)
                {
                    int px = (int)(this.pendingTile.X * 64f) + 32;
                    int py = (int)(this.pendingTile.Y * 64f) + 32;
                    // 保护：目标格上有摆放物（宝箱/熔炉/稻草人等）就绝不动手
                    try
                    {
                        if (tloc.objects.TryGetValue(this.pendingTile, out var guardObj) && guardObj != null)
                        {
                            bool isPlaced = guardObj is Chest || guardObj is Furniture
                                || (guardObj.Name ?? "").Contains("稻草人")
                                || (guardObj.Name ?? "").Contains("Scarecrow")
                                || (guardObj.Name ?? "").Contains("熔炉")
                                || (guardObj.Name ?? "").Contains("Furnace")
                                || (guardObj.Name ?? "").Contains("箱")
                                || guardObj.bigCraftable.Value;
                            if (isPlaced)
                            {
                                this.Monitor.Log($"工具保护：目标格 ({this.pendingTile.X},{this.pendingTile.Y}) 有摆放物 {guardObj.Name}，取消", LogLevel.Warn);
                                // 注意：BeginUsingTool 已经调用过了，这里必须把挥动状态收掉，
                                // 否则玩家会永远卡在「举着工具蓄力」的姿势（2026-11-17 实测 bug）
                                try { Game1.player.EndUsingTool(); } catch { }
                                try { Game1.player.UsingTool = false; } catch { }
                                this.pendingKind = "";
                                return;
                            }
                        }
                    }
                    catch { }
                    curTool.DoFunction(tloc, px, py, Math.Max(1, this.pendingPower), Game1.player);
                }
            }
            catch (Exception ex) { this.Monitor.Log($"工具生效异常：{ex.Message}", LogLevel.Warn); }
            try { Game1.player.EndUsingTool(); } catch { }
            try { Game1.player.UsingTool = false; } catch { }
            this.Monitor.Log($"工具生效：{this.pendingKind} @ ({this.pendingTile.X},{this.pendingTile.Y})", LogLevel.Info);
            this.pendingKind = "";
            // 工具可能被游戏覆盖（历史 bug），用完立刻校验
            try { this.EnsureTools(); } catch { }
        }

        /// <summary>收割成熟作物（调用原版 Crop.harvest）。</summary>
        private void Harvest(JObject jo)
        {
            int x = (int)jo["x"], y = (int)jo["y"];
            var player = Game1.player;
            var loc = player.currentLocation;
            var tile = new Vector2(x, y);
            if (Math.Abs(player.Tile.X - x) + Math.Abs(player.Tile.Y - y) > 2.0f)
            {
                this.Monitor.Log($"harvest: 离 ({x},{y}) 太远", LogLevel.Warn);
                return;
            }
            this.Face(tile);
            if (!loc.terrainFeatures.TryGetValue(tile, out var tf) || !(tf is HoeDirt dirt) || dirt.crop == null)
            {
                this.Monitor.Log($"harvest: ({x},{y}) 没有作物", LogLevel.Warn);
                return;
            }
            if (!dirt.readyForHarvest())
            {
                this.Monitor.Log($"harvest: ({x},{y}) 还没成熟（{dirt.crop.indexOfHarvest.Value}）", LogLevel.Info);
                return;
            }
            try
            {
                bool ok = false;
                try { ok = dirt.crop.harvest(x, y, dirt, null); } catch { }
                if (!ok)
                {
                    // 兜底：直接按作物产物入包（游戏 API 有时在脚本环境里返回 false 但不产出）
                    try
                    {
                        string hid = dirt.crop.indexOfHarvest.Value;
                        int n = 1;
                        var loot = ItemRegistry.Create<StardewValley.Object>("(O)" + hid, n);
                        if (loot != null && Game1.player.addItemToInventoryBool(loot, false)) ok = true;
                    }
                    catch (Exception ex2) { this.Monitor.Log("harvest 兜底异常：" + ex2.Message, LogLevel.Warn); }
                }
                this.Monitor.Log($"harvest: ({x},{y}) {dirt.crop.indexOfHarvest.Value} = {ok}", LogLevel.Info);
                // 不重复收割：非再生作物收割后清掉
                if (dirt.crop != null)
                {
                    bool regrow = false;
                    try { regrow = dirt.crop.RegrowsAfterHarvest(); } catch { }
                    if (!regrow) dirt.crop = null;
                    else dirt.crop.dayOfCurrentPhase.Value = 0;
                }
                dirt.state.Value = 0;
            }
            catch (Exception ex) { this.Monitor.Log($"harvest 异常：{ex.Message}", LogLevel.Warn); }
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
            // 农场特殊处理：默认 warps 里挑到的可能是角落（78,13）这种走不出去的格子，
            // 统一改成小屋门口 (64,15)，保证落地就能走
            if (dest == "Farm")
            {
                try
                {
                    Game1.warpFarmer("Farm", 68, 20, 0);
                    this.Monitor.Log($"goto: {cur} → Farm（落到 68,20）", LogLevel.Info);
                    return;
                }
                catch { }
            }
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
            // 皮埃尔商店不是传送点（是 Town 里的一扇门）：先到 Town，剩下的走门交给上层
            if (dest == "SeedShop") { dest = "Town"; this.Monitor.Log("goto: SeedShop 走门 → 先到 Town", LogLevel.Info); }
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
                this.hopFailTotal = 0;
                this.Monitor.Log($"goto: 已到达 {name}", LogLevel.Info);
                return;
            }
            // 2026-11-23：TickTravel 每 30 tick 才调用一次，以前这里按 tick 递减 warpCooldown=60，
            // 等于要 60 次调用 × 30 tick = 1800 tick ≈ 30 秒站着不动才继续走。
            // 改成按调用次数递减（每调用扣 30），warpCooldown=120 → 4 次 ≈ 2 秒，够等黑屏淡入。
            if (this.warpCooldown > 0) { this.warpCooldown -= 30; return; }

            // 目标是农舍：Farm 里的农舍也是「门」，不是 warp → 走到 (64,16) 再进屋
            if ((this.travelDest ?? "") == "FarmHouse" && name == "Farm")
            {
                try
                {
                    var pp = Game1.player;
                    int dhome = Math.Abs((int)pp.Tile.X - 64) + Math.Abs((int)pp.Tile.Y - 15);
                    if (dhome <= 3)
                    {
                        this.gotoShop = false;
                        this.hopAttempts.Clear();
                        this.Monitor.Log("goto: 到农舍门口，进屋", LogLevel.Info);
                        Game1.warpFarmer("FarmHouse", 9, 9, 2);
                        return;
                    }
                    if (pp.controller == null)
                    {
                        var homeTarget = new Point(64, 16);
                        Game1.player.controller = new StardewValley.Pathfinding.PathFindController(Game1.player, loc, homeTarget, 3);
                        this.Monitor.Log($"goto: 走向农舍门口 (64,16)，距门 {dhome} 格", LogLevel.Info);
                    }
                    return;
                }
                catch (Exception exHome) { this.Monitor.Log("goto 农舍异常：" + exHome.Message, LogLevel.Warn); }
            }
            // 目标是矿洞：Mountain 里的矿洞入口是「门」不是传送点，单独走门进去
            if ((this.travelDest ?? "") == "Mine" && name == "Mountain" && this.TryEnterMineDoor(loc))
            {
                return;
            }
            // 选门：按「离目标更近」的顺序列出相邻地图，逐个验证门口是否真的走得到，
            // 避免选了一个被地形隔开的门然后死磕/兜底传送（2026-11-25 修）
            var pt0 = Game1.player.Tile;
            string next = null;
            StardewValley.Warp warp = null;
            Point target = Point.Zero;
            foreach (var hop in this.HopOrder(name, this.travelDest ?? ""))
            {
                var cs = loc.warps
                    .Where(w => (w.TargetName ?? "") == hop)
                    .OrderBy(w => Math.Abs(w.X - (int)pt0.X) + Math.Abs(w.Y - (int)pt0.Y))
                    .ToList();
                if (cs.Count == 0) continue;
                foreach (var w in cs)
                {
                    var t = this.DoorTarget(loc, w, pt0);
                    int probe = this.ProbePath(loc, t);
                    if (probe <= 0)
                    {
                        // 门格本身可能是「不可落脚的门」（如农舍门），改试门口相邻格
                        var t2 = this.DoorAdjacent(loc, w, pt0);
                        if (t2 != Point.Zero)
                        {
                            int p2 = this.ProbePath(loc, t2);
                            if (p2 > 0) { t = t2; probe = p2; }
                        }
                    }
                    if (probe > 0) { next = hop; warp = w; target = t; break; }
                    this.Monitor.Log($"goto: {name} 门 {hop}({w.X},{w.Y}) 不可达（探路={probe}），换一个", LogLevel.Debug);
                }
                if (warp != null) break;
            }
            if (warp == null)
            {
                this.gotoShop = false;
                this.Monitor.Log($"goto: {name} 附近所有出口都走不到，放弃去 {this.travelDest}（交给上层换目标）", LogLevel.Warn);
                return;
            }
            int dist = Math.Abs((int)pt0.X - warp.X) + Math.Abs((int)pt0.Y - warp.Y);

            // 已经在门口：过图（读图淡入，等同玩家穿过门/地图边缘）
            bool edgeWarp = warp.X <= 2 || warp.Y <= 2
                            || warp.X >= loc.Map.Layers[0].LayerWidth - 3
                            || warp.Y >= loc.Map.Layers[0].LayerHeight - 3;
            if (dist <= 1 || (edgeWarp && dist <= 4))
            {
                int tx = warp.TargetX, ty = warp.TargetY;
                try
                {
                    var destLoc = Game1.getLocationFromName(warp.TargetName);
                    if (destLoc?.Map?.Layers != null && destLoc.Map.Layers.Count > 0)
                    {
                        int w = destLoc.Map.Layers[0].LayerWidth, h = destLoc.Map.Layers[0].LayerHeight;
                        tx = Math.Min(Math.Max(tx, 2), w - 3);   // 至少离边 2 格：贴边多半是往返回转门，会来回弹
                        ty = Math.Min(Math.Max(ty, 2), h - 3);
                    }
                }
                catch { }
                this.warpCooldown = 120;
                this.hopApproach = Point.Zero;
                this.hopAttempts.Clear();
                this.Monitor.Log($"goto: {name} 走到门口，过图 → {warp.TargetName}", LogLevel.Info);
                Game1.warpFarmer(warp.TargetName, tx, ty, false);
                return;
            }

            // 正在走路就等
            if (Game1.player.controller != null) return;


            int tries = this.hopAttempts.TryGetValue(name, out var n) ? n : 0;
            tries++;
            this.hopAttempts[name] = tries;
            if (tries > 4)
            {
                this.hopAttempts[name] = 0;
                this.gotoShop = false;
                this.Monitor.Log($"goto: {name} 走向 {warp.TargetName}({warp.X},{warp.Y}) 连续 4 次失败，放弃这次旅行（不传送，交给上层换目标）", LogLevel.Warn);
                return;
            }
            try
            {
                var ctrl = new StardewValley.Pathfinding.PathFindController(
                    Game1.player, loc, target, 3);
                int plen = ctrl != null && ctrl.pathToEndPoint != null ? ctrl.pathToEndPoint.Count : -1;
                var ptNow = Game1.player.Tile;
                this.Monitor.Log($"goto: {name} 走向门口 ({target.X},{target.Y})，距门 {dist} 格（第 {tries} 次）| 起点({ptNow.X},{ptNow.Y}) 可走={this.IsSafeTile(loc, (int)ptNow.X, (int)ptNow.Y)} 路径={plen}", LogLevel.Info);
                if (plen <= 0)
                {
                    // 诊断：起点到底能不能走？分别试 4 个方向 2 格
                    foreach (var off in new[] { new Point(0, 2), new Point(2, 0), new Point(-2, 0), new Point(0, -2) })
                    {
                        try
                        {
                            var t2 = new Point((int)ptNow.X + off.X, (int)ptNow.Y + off.Y);
                            var c2 = new StardewValley.Pathfinding.PathFindController(Game1.player, loc, t2, 3);
                            int l2 = c2 != null && c2.pathToEndPoint != null ? c2.pathToEndPoint.Count : -1;
                            this.Monitor.Log($"  diag: ({ptNow.X},{ptNow.Y})→({t2.X},{t2.Y}) 路径={l2} 目标可走={this.IsSafeTile(loc, t2.X, t2.Y)}", LogLevel.Info);
                        }
                        catch (Exception e2) { this.Monitor.Log("  diag 异常: " + e2.Message, LogLevel.Warn); }
                    }
                }
                Game1.player.controller = ctrl;
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"goto 寻路失败：{ex.Message}", LogLevel.Warn);
            }
        }

        /// <summary>按「离目标更近」的顺序列出当前地图的邻居（用于挑一条走得通的路线）。</summary>
        private List<string> HopOrder(string from, string dest)
        {
            var list = new List<string>();
            try
            {
                if (this.graph == null) this.BuildGraph();
                if (this.graph != null && this.graph.TryGetValue(from, out var nbrs))
                {
                    int d0 = this.DistTo(from, dest);
                    var strict = new List<string>();
                    var loose = new List<string>();
                    foreach (var n in nbrs)
                    {
                        if (n == dest) { strict.Insert(0, n); continue; }
                        int dn = this.DistTo(n, dest);
                        if (dn < 0) continue;
                        loose.Add(n);
                        // 只走「离目标更近一步」的邻居，避免在两张图之间来回弹（2026-11-25）
                        if (d0 < 0 || dn == d0 - 1) strict.Add(n);
                    }
                    list = strict.Count > 0 ? strict : loose;
                    list = list.OrderBy(x => x == dest ? 0 : this.DistTo(x, dest)).ToList();
                }
            }
            catch { }
            var old = this.DynamicNextHop(from, dest) ?? NextHop(from, dest);
            if (old != null && !list.Contains(old)) list.Insert(0, old);
            return list;
        }

        /// <summary>BFS 步数（走不到返回 -1）。</summary>
        private int DistTo(string from, string dest)
        {
            if (string.IsNullOrEmpty(from) || string.IsNullOrEmpty(dest)) return -1;
            if (from == dest) return 0;
            if (this.graph == null || !this.graph.ContainsKey(from) || !this.graph.ContainsKey(dest)) return -1;
            var seen = new HashSet<string> { from };
            var q = new Queue<KeyValuePair<string, int>>();
            q.Enqueue(new KeyValuePair<string, int>(from, 0));
            while (q.Count > 0)
            {
                var kv = q.Dequeue();
                foreach (var n in this.graph[kv.Key])
                {
                    if (!seen.Add(n)) continue;
                    if (n == dest) return kv.Value + 1;
                    q.Enqueue(new KeyValuePair<string, int>(n, kv.Value + 1));
                }
            }
            return -1;
        }

        /// <summary>算门口落脚点：门可走就踩门上，否则用最近的可走邻格，边缘再往内收 2 格。</summary>
        /// <summary>门口相邻的可走格（门格本身不可落脚时用）。</summary>
        private Point DoorAdjacent(GameLocation loc, StardewValley.Warp warp, Vector2 from)
        {
            var near = new[]
            {
                new Point(warp.X, warp.Y + 1), new Point(warp.X, warp.Y - 1),
                new Point(warp.X + 1, warp.Y), new Point(warp.X - 1, warp.Y),
                new Point(warp.X, warp.Y + 2), new Point(warp.X + 2, warp.Y)
            }.OrderBy(c => Math.Abs(c.X - (int)from.X) + Math.Abs(c.Y - (int)from.Y));
            foreach (var c in near) if (this.IsSafeTile(loc, c.X, c.Y)) return c;
            return Point.Zero;
        }

        private Point DoorTarget(GameLocation loc, StardewValley.Warp warp, Vector2 from)
        {
            Point target;
            if (this.IsSafeTile(loc, warp.X, warp.Y)) target = new Point(warp.X, warp.Y);
            else
            {
                var near = new[]
                {
                    new Point(warp.X, warp.Y - 1), new Point(warp.X, warp.Y + 1),
                    new Point(warp.X + 1, warp.Y), new Point(warp.X - 1, warp.Y)
                }.OrderBy(c => Math.Abs(c.X - (int)from.X) + Math.Abs(c.Y - (int)from.Y));
                Point pick = Point.Zero;
                foreach (var c in near) if (this.IsSafeTile(loc, c.X, c.Y)) { pick = c; break; }
                // 门正前方也不可走（矿洞洞口常见）：按半径 2~4 找最近的可走格
                if (pick == Point.Zero)
                {
                    for (int r = 2; r <= 4 && pick == Point.Zero; r++)
                    {
                        for (int dx = -r; dx <= r && pick == Point.Zero; dx++)
                        {
                            for (int dy = -r; dy <= r; dy++)
                            {
                                if (Math.Abs(dx) != r && Math.Abs(dy) != r) continue;
                                int tx2 = warp.X + dx, ty2 = warp.Y + dy;
                                if (this.IsSafeTile(loc, tx2, ty2)) { pick = new Point(tx2, ty2); break; }
                            }
                        }
                    }
                }
                target = pick == Point.Zero ? new Point(warp.X, warp.Y) : pick;
            }
            try
            {
                int mw = loc.Map.Layers[0].LayerWidth, mh = loc.Map.Layers[0].LayerHeight;
                int cx = Math.Min(Math.Max(target.X, Math.Min(2, mw - 2)), mw - 3);
                int cy = Math.Min(Math.Max(target.Y, Math.Min(2, mh - 2)), mh - 3);
                target = new Point(cx, cy);
            }
            catch { }
            return target;
        }

        /// <summary>探路：从当前位置到目标能否走通（返回路径长度，-1 表示走不通）。</summary>
        private int ProbePath(GameLocation loc, Point target)
        {
            try
            {
                var c = new StardewValley.Pathfinding.PathFindController(Game1.player, loc, target, 3);
                return c != null && c.pathToEndPoint != null ? c.pathToEndPoint.Count : -1;
            }
            catch { return -1; }
        }

        /// <summary>Mountain 里的矿洞入口是门（Action: Mine / EnterMine），走到门口触发。</summary>
        private bool TryEnterMineDoor(GameLocation loc)
        {
            try
            {
                if (this.mineDoor.X < 0)
                {
                    int w = loc.Map.Layers[0].LayerWidth, h = loc.Map.Layers[0].LayerHeight;
                    for (int y = 0; y < h && this.mineDoor.X < 0; y++)
                    {
                        for (int x = 0; x < w; x++)
                        {
                            string a = null;
                            foreach (var layer in new[] { "Buildings", "Back", "Front" })
                            {
                                try { a = loc.doesTileHaveProperty(x, y, "Action", layer); } catch { }
                                if (!string.IsNullOrEmpty(a) && (a.Contains("Mine") || a.Contains("mine")))
                                {
                                    this.mineDoor = new Vector2(x, y);
                                    this.Monitor.Log($"goto: 找到矿洞入口 ({x},{y}) action={a}", LogLevel.Info);
                                    break;
                                }
                            }
                            if (this.mineDoor.X >= 0) break;
                        }
                    }
                    if (this.mineDoor.X < 0)
                    {
                        this.Monitor.Log("goto: Mountain 里找不到矿洞入口", LogLevel.Warn);
                        return false;
                    }
                }
                var pl = Game1.player;
                int dist = Math.Abs((int)pl.Tile.X - (int)this.mineDoor.X) + Math.Abs((int)pl.Tile.Y - (int)this.mineDoor.Y);
                if (dist <= 1)
                {
                    bool ok = loc.checkAction(new xTile.Dimensions.Location((int)this.mineDoor.X, (int)this.mineDoor.Y), Game1.viewport, pl);
                    this.Monitor.Log($"goto: 触发矿洞入口 ({this.mineDoor.X},{this.mineDoor.Y}) = {ok}", LogLevel.Info);
                    return true;
                }
                if (pl.controller == null)
                {
                    // 走到门口旁边（门本身可能不可走）
                    Point t = new Point((int)this.mineDoor.X, (int)this.mineDoor.Y + 1);
                    if (!this.IsSafeTile(loc, t.X, t.Y)) t = new Point((int)this.mineDoor.X, (int)this.mineDoor.Y - 1);
                    Game1.player.controller = new StardewValley.Pathfinding.PathFindController(Game1.player, loc, t, 3);
                    this.Monitor.Log($"goto: 走向矿洞入口 ({t.X},{t.Y})，距门 {dist} 格", LogLevel.Info);
                }
                return true;
            }
            catch (Exception ex)
            {
                this.Monitor.Log("goto 矿洞入口异常：" + ex.Message, LogLevel.Warn);
                return false;
            }
        }

        private bool IsSafeTile(GameLocation loc, int x, int y)
        {
            try
            {
                if (loc?.Map?.Layers == null || loc.Map.Layers.Count == 0) return false;
                if (x < 0 || y < 0) return false;
                if (x >= loc.Map.Layers[0].LayerWidth || y >= loc.Map.Layers[0].LayerHeight) return false;
                // 矿洞梯子/竖井格虽然"不可通行"，但游戏允许走上去
                try
                {
                    var bl = loc.Map.GetLayer("Buildings");
                    var bt = bl?.Tiles[x, y];
                    if (bt != null && (bt.TileIndex == 173 || bt.TileIndex == 174 || bt.TileIndex == 115)) return true;
                }
                catch { }
                return loc.isTilePassable(new xTile.Dimensions.Location(x, y), Game1.viewport);
            }
            catch { return false; }
        }

        private void GiveItem(string name, int count)
        {
            var p = Game1.player;
            string full = null;
            try
            {
                foreach (var kv in Game1.objectData)
                    if ((kv.Value?.Name ?? "").Equals(name, StringComparison.OrdinalIgnoreCase)) { full = "(O)" + kv.Key; break; }
            }
            catch { }
            if (full == null)
            {
                foreach (var cand in new[] { name, "(O)" + name, "(T)" + name, "(W)" + name, "(TR)" + name, "(BC)" + name })
                {
                    try { if (ItemRegistry.Create(cand, 1, 0) != null) { full = cand; break; } } catch { }
                }
            }
            if (full == null) { this.Monitor.Log($"give_item: 找不到「{name}」", LogLevel.Warn); return; }
            try
            {
                var item = ItemRegistry.Create(full, 1, 0);
                if (item == null) { this.Monitor.Log($"give_item: {full} 创建失败", LogLevel.Warn); return; }
                item.Stack = Math.Max(1, count);
                if (p.addItemToInventoryBool(item, false))
                    this.Monitor.Log($"give_item: {item.Name} × {item.Stack}", LogLevel.Info);
                else this.Monitor.Log($"give_item: 放不下 {item.Name}", LogLevel.Warn);
            }
            catch (Exception ex) { this.Monitor.Log($"give_item 异常：{ex.Message}", LogLevel.Warn); }
        }

        /// <summary>把所有箱子/背包里的同种物品按 9999 上限合并压缩。</summary>
        /// <summary>从指定箱子取物品到背包：{x,y,kind:"seeds"|"all",count}</summary>
        private void Withdraw(JObject jo)
        {
            string kind = (string)jo["kind"] ?? "all";
            int limit = jo["count"] != null ? (int)jo["count"] : 999;
            var p = Game1.player;
            var loc = p.currentLocation;
            string roleWanted = (string)jo["role"];
            Chest chest = null;
            if (!string.IsNullOrEmpty(roleWanted))
            {
                // 按角色找箱子（种子箱/作物箱/杂物箱），坐标变了也不用改 agent
                chest = this.FindChestByRole(Game1.getFarm(), roleWanted) ?? this.FindChestByRole(loc, roleWanted);
                if (chest == null) { this.Monitor.Log($"withdraw: 没有 {roleWanted} 角色的箱子", LogLevel.Warn); return; }
            }
            else
            {
                int x = (int)jo["x"], y = (int)jo["y"];
                var tile = new Vector2(x, y);
                if (!loc.objects.TryGetValue(tile, out var o) || !(o is Chest c0))
                {
                    this.Monitor.Log($"withdraw: ({x},{y}) 不是箱子", LogLevel.Warn);
                    return;
                }
                chest = c0;
            }
            int moved = 0, slots = 0;
            for (int i = 0; i < chest.Items.Count && moved < limit; i++)
            {
                var it = chest.Items[i];
                if (it == null) continue;
                bool ok = kind == "all";
                string nameFilter = (string)jo["name"];
                if (!string.IsNullOrEmpty(nameFilter))
                {
                    ok = (it.Name ?? "").IndexOf(nameFilter, StringComparison.OrdinalIgnoreCase) >= 0
                         || (it.ItemId ?? "") == nameFilter;
                }
                else if (kind == "seeds" && it is StardewValley.Object so2
                    && (so2.Type == "Seeds" || so2.Category == StardewValley.Object.SeedsCategory || (so2.Name ?? "").Contains("种子")))
                    ok = true;
                if (!ok) continue;
                // 只搬需要的那一份：整堆搬会把 9999 个种子一次塞进背包（2026-11-18 实测）
                int want = Math.Min(it.Stack, limit - moved);
                if (want <= 0) break;
                Item piece = it.getOne();
                piece.Stack = want;
                if (p.addItemToInventoryBool(piece, false))
                {
                    if (it.Stack - want <= 0) chest.Items[i] = null;
                    else it.Stack -= want;
                    moved += want;
                    slots++;
                }
            }
            string where = string.IsNullOrEmpty(roleWanted) ? "坐标" : ("角色 " + roleWanted);
            this.Monitor.Log($"withdraw: 从{where}取出 {moved} 个（{slots} 格）", LogLevel.Info);
        }

        private void MergeStacks()
        {
            var farm = Game1.getFarm();
            int chests = 0, before = 0, after = 0;
            foreach (var key in farm.objects.Keys.ToList())
            {
                var chest = farm.objects[key] as StardewValley.Objects.Chest;
                if (chest == null) continue;
                chests++;
                var grouped = new Dictionary<string, List<Item>>();
                var order = new List<string>();
                foreach (var it in chest.Items)
                {
                    if (it == null) continue;
                    before++;
                    var k = it.QualifiedItemId + "|" + it.Quality;
                    if (!grouped.ContainsKey(k)) { grouped[k] = new List<Item>(); order.Add(k); }
                    grouped[k].Add(it);
                }
                for (int i = 0; i < chest.Items.Count; i++) chest.Items[i] = null;
                int slot = 0;
                foreach (var k in order)
                {
                    var list = grouped[k];
                    var proto = list[0];
                    int total = 0;
                    foreach (var it in list) total += it.Stack;
                    int max = proto.maximumStackSize();
                    while (total > 0 && slot < chest.Items.Count)
                    {
                        int n = Math.Min(total, max);
                        if (total == list[0].Stack && list.Count == 1)
                        {
                            proto.Stack = n;
                            chest.Items[slot++] = proto;
                        }
                        else
                        {
                            var copy = proto.getOne();
                            copy.Stack = n;
                            chest.Items[slot++] = copy;
                        }
                        total -= n;
                        after++;
                    }
                }
            }
            // 玩家背包也合并
            var p = Game1.player;
            this.Monitor.Log($"merge_stacks: {chests} 个箱子，原 {before} 堆 → 现 {after} 堆", LogLevel.Info);
        }

        /// <summary>打印当前地图附近的物体（矿洞排查用）。</summary>
        private void ScanActions()
        {
            var loc = Game1.player.currentLocation;
            var lines = new List<string>();
            try
            {
                int w = loc.Map.Layers[0].LayerWidth, h = loc.Map.Layers[0].LayerHeight;
                for (int y = 0; y < h; y++)
                {
                    for (int x = 0; x < w; x++)
                    {
                        foreach (var layer in new[] { "Back", "Buildings", "Front" })
                        {
                            string a = null;
                            try { a = loc.doesTileHaveProperty(x, y, "Action", layer); } catch { }
                            string t = null;
                            try { t = loc.doesTileHaveProperty(x, y, "TouchAction", layer); } catch { }
                            if (!string.IsNullOrEmpty(a)) lines.Add("(" + x + "," + y + ")" + layer + " Action=" + a);
                            if (!string.IsNullOrEmpty(t)) lines.Add("(" + x + "," + y + ")" + layer + " Touch=" + t);
                        }
                    }
                }
            }
            catch (Exception ex) { this.Monitor.Log("scan_actions 异常: " + ex.Message, LogLevel.Warn); }
            this.Monitor.Log("ACTIONS[" + loc.Name + "] " + lines.Count + ": " + string.Join(" | ", lines.Take(30)), LogLevel.Alert);
            var warps = new List<string>();
            foreach (var w2 in loc.warps) warps.Add(w2.TargetName + "(" + w2.X + "," + w2.Y + ")");
            this.Monitor.Log("WARPS[" + loc.Name + "] : " + string.Join(" | ", warps), LogLevel.Alert);
        }

        /// <summary>扫描整张地图的可开垦空地，输出 DIG 行（供 AI 选田）。</summary>
        /// <summary>扫描整张地图水面，输出 WATER 行。</summary>
        private void ScanWater()
        {
            var loc = Game1.player.currentLocation;
            var list = new List<string>();
            try
            {
                int w = loc.Map.Layers[0].LayerWidth, h = loc.Map.Layers[0].LayerHeight;
                for (int y = 0; y < h; y++)
                {
                    for (int x = 0; x < w; x++)
                    {
                        if (loc.doesTileHaveProperty(x, y, "Water", "Back") != null)
                            list.Add(x + "," + y);
                    }
                }
            }
            catch (Exception ex) { this.Monitor.Log("scan_water 异常: " + ex.Message, LogLevel.Warn); }
            this.Monitor.Log("WATER " + list.Count + ": " + string.Join(" ", list), LogLevel.Alert);
        }

        private void ScanDiggable()
        {
            var loc = Game1.player.currentLocation;
            var list = new List<string>();
            try
            {
                int w = loc.Map.Layers[0].LayerWidth, h = loc.Map.Layers[0].LayerHeight;
                for (int y = 0; y < h; y++)
                {
                    for (int x = 0; x < w; x++)
                    {
                        if (loc.terrainFeatures.ContainsKey(new Vector2(x, y))) continue;
                        if (loc.objects.ContainsKey(new Vector2(x, y))) continue;
                        if (loc.doesTileHaveProperty(x, y, "Diggable", "Back") == null) continue;
                        if (!loc.isTilePassable(new xTile.Dimensions.Location(x, y), Game1.viewport)) continue;
                        list.Add(x + "," + y);
                    }
                }
            }
            catch (Exception ex) { this.Monitor.Log("scan_dig 异常: " + ex.Message, LogLevel.Warn); }
            this.Monitor.Log("DIG " + list.Count + ": " + string.Join(" ", list), LogLevel.Alert);
        }

        private void ScanAll()
        {
            var loc = Game1.player.currentLocation;
            var lines = new List<string>();
            var ladders = new List<string>();
            foreach (var key in loc.objects.Keys.ToList())
            {
                var o = loc.objects[key];
                if (o == null) continue;
                lines.Add("(" + key.X + "," + key.Y + ")" + o.Name + "#" + o.ItemId);
                var nm = (o.Name ?? "") + (o.ItemId ?? "");
                if (nm.Contains("Ladder") || nm.Contains("楼梯") || nm.Contains("Shaft") || nm.Contains("洞口"))
                    ladders.Add("(" + key.X + "," + key.Y + ")" + o.Name);
            }
            this.Monitor.Log("ALL[" + loc.Name + "] 物体 " + lines.Count + ": " + string.Join(" | ", lines.Take(40)), LogLevel.Alert);
            this.Monitor.Log("LADDER[" + loc.Name + "] " + ladders.Count + ": " + string.Join(" | ", ladders), LogLevel.Alert);
        }

        private void ScanNear()
        {
            var loc = Game1.player.currentLocation;
            var p = Game1.player.Tile;
            var lines = new List<string>();
            try
            {
                foreach (var key in loc.objects.Keys.ToList())
                {
                    var o = loc.objects[key];
                    if (o == null) continue;
                    float d = Math.Abs(key.X - p.X) + Math.Abs(key.Y - p.Y);
                    if (d > 12) continue;
                    lines.Add($"({key.X},{key.Y}) {o.Name}#{o.ItemId} cat={o.Category} d={d:F0}");
                }
                lines.Sort();
            }
            catch (Exception ex) { this.Monitor.Log("scan_near 异常: " + ex.Message, LogLevel.Warn); }
            this.Monitor.Log($"NEAR[{loc.Name}_{(loc as StardewValley.Locations.MineShaft)?.mineLevel}] 玩家({p.X:F0},{p.Y:F0}) 物体 {lines.Count} 个: " + string.Join(" | ", lines.Take(20)), LogLevel.Alert);
        }

        private static string ToolSignature()
        {
            var names = new List<string>();
            foreach (var it in Game1.player.Items)
            {
                if (it is Tool t) names.Add(t.GetType().Name + ":" + t.UpgradeLevel);
            }
            return string.Join(",", names);
        }

        private string lastToolSig = null;

        /// <summary>每帧检查工具是否被异常替换/丢失，记录并自动修复。</summary>
        private void CheckToolDrift()
        {
            string sig = ToolSignature();
            if (this.lastToolSig == null) { this.lastToolSig = sig; return; }
            if (sig == this.lastToolSig) return;
            this.Monitor.Log($"工具变动: {this.lastToolSig} → {sig} | pendingKind={this.pendingKind} toolTicks={this.toolTicks}", LogLevel.Warn);
            this.lastToolSig = sig;
        }

        /// <summary>确保核心工具齐全（鱼竿/斧/锄/镐/水壶/镰刀），并去掉重复。</summary>
        /// <summary>在玩家面前放一个原版梯子（等同使用「楼梯」道具），用于下矿。</summary>
        /// <summary>使用原版「楼梯」道具（71）在当前身边创造一个下矿梯子。</summary>
        /// <summary>坐电梯到指定矿层：优先点电梯菜单按钮（可见），否则走游戏同一个入口。</summary>
        /// <summary>BFS 判断某格能否走到（站到它 2 格内），结果写日志 REACH。</summary>
        /// <summary>离开矿洞：走原版 ExitMine 路径，回到矿洞大堂。</summary>
        /// <summary>直接进入指定矿层（骷髅洞穴 121+，普通矿洞 1-120）。</summary>
        /// <summary>调试：打印 MineShaft 里和梯子/竖井有关的成员值。</summary>
        private void ProbeMineShaft()
        {
            try
            {
                var ms = Game1.currentLocation as StardewValley.Locations.MineShaft;
                if (ms == null) { this.Monitor.Log("probe_mineshaft: 当前不是 MineShaft（" + (Game1.currentLocation?.Name ?? "?") + "）", LogLevel.Warn); return; }
                var t = ms.GetType();
                foreach (var f in t.GetFields(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance))
                {
                    var n = f.Name.ToLower();
                    if (n.Contains("ladder") || n.Contains("shaft") || n.Contains("exit"))
                    {
                        object v = null;
                        try { v = f.GetValue(ms); } catch { }
                        this.Monitor.Log("probe_mineshaft field " + f.Name + " = " + (v?.ToString() ?? "null"), LogLevel.Alert);
                    }
                }
                foreach (var pr in t.GetProperties(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance))
                {
                    var n = pr.Name.ToLower();
                    if (n.Contains("ladder") || n.Contains("shaft") || n.Contains("exit"))
                    {
                        object v = null;
                        try { v = pr.GetValue(ms); } catch { }
                        this.Monitor.Log("probe_mineshaft prop " + pr.Name + " = " + (v?.ToString() ?? "null"), LogLevel.Alert);
                    }
                }
                this.Monitor.Log("probe_mineshaft: level=" + ms.mineLevel + " name=" + ms.Name, LogLevel.Alert);
            }
            catch (Exception ex) { this.Monitor.Log("probe_mineshaft 异常: " + ex.Message, LogLevel.Warn); }
        }

        /// <summary>兜底：把玩家直接送进农舍（门口被堵/卡住时用）。</summary>
        private void WarpHouse()
        {
            try
            {
                Game1.exitActiveMenu();
                Game1.warpFarmer("FarmHouse", 3, 10, 2);
                Game1.player.isInBed.Value = false;
                this.Monitor.Log("warp_house: 已送进农舍", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log("warp_house 异常: " + ex.Message, LogLevel.Warn); }
        }

        private void MineGoto(int level)
        {
            try
            {
                Game1.exitActiveMenu();
                Game1.enterMine(level);
                this.Monitor.Log("mine_goto: 进入 " + level + " 层", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log("mine_goto 异常: " + ex.Message, LogLevel.Warn); }
        }

        private void LeaveMine()
        {
            try
            {
                Game1.exitActiveMenu();
                int lvl = Game1.CurrentMineLevel;
                string target;
                int tx, ty, face;
                if (lvl == 77377) { target = "Mine"; tx = 67; ty = 10; face = 1; }
                else if (lvl > 120) { target = "SkullCave"; tx = 3; ty = 4; face = 2; }
                else { target = "Mine"; tx = 23; ty = 8; face = 0; }
                Game1.warpFarmer(target, tx, ty, face);
                this.Monitor.Log($"leave_mine: 从 {lvl} 层离开 → {target}({tx},{ty})", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log($"leave_mine 异常：{ex.Message}", LogLevel.Warn); }
        }

        private void Reachable(JObject jo)
        {
            int tx = (int)jo["x"], ty = (int)jo["y"];
            var loc = Game1.player.currentLocation;
            var start = new Vector2((int)Game1.player.Tile.X, (int)Game1.player.Tile.Y);
            var seen = new HashSet<Vector2>();
            var q = new Queue<Vector2>();
            q.Enqueue(start);
            seen.Add(start);
            bool ok = false;
            int guard = 0;
            var dirs = new[] { new Vector2(1, 0), new Vector2(-1, 0), new Vector2(0, 1), new Vector2(0, -1) };
            while (q.Count > 0 && guard++ < 8000)
            {
                var cur = q.Dequeue();
                if (Math.Abs(cur.X - tx) + Math.Abs(cur.Y - ty) <= 2) { ok = true; break; }
                foreach (var d in dirs)
                {
                    var n = cur + d;
                    if (seen.Contains(n)) continue;
                    if (!this.IsSafeTile(loc, (int)n.X, (int)n.Y)) continue;
                    seen.Add(n);
                    q.Enqueue(n);
                }
            }
            this.Monitor.Log($"REACH ({tx},{ty}) = {(ok ? "yes" : "no")}", LogLevel.Alert);
        }

        private void ElevatorGo(JObject jo)
        {
            int level = jo != null && jo["level"] != null ? (int)jo["level"] : 5;
            if (level == Game1.CurrentMineLevel)
            {
                this.Monitor.Log($"elevator_go: 已经在 {level} 层，什么都不做", LogLevel.Info);
                try { Game1.exitActiveMenu(); } catch { }
                return;
            }
            var menu = Game1.activeClickableMenu as StardewValley.Menus.MineElevatorMenu;
            if (menu != null)
            {
                foreach (var comp in menu.elevators)
                {
                    if (Convert.ToInt32(comp.name) == level)
                    {
                        menu.receiveLeftClick(comp.bounds.Center.X, comp.bounds.Center.Y);
                        this.Monitor.Log($"elevator_go: 点击电梯按钮 {level}", LogLevel.Info);
                        return;
                    }
                }
                this.Monitor.Log($"elevator_go: 菜单里没有 {level} 层（未解锁）", LogLevel.Warn);
            }
            try { Game1.exitActiveMenu(); } catch { }
            try
            {
                Game1.player.ridingMineElevator = true;
                Game1.enterMine(level);
                this.Monitor.Log($"elevator_go: 直接进入 {level} 层", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log($"elevator_go 异常：{ex.Message}", LogLevel.Warn); }
        }

        /// <summary>关掉当前菜单/对话（防止菜单卡住）。</summary>
        /// <summary>走进房屋（农场小屋等）：找到门所在格再交互。</summary>
        /// <summary>走进房屋（农场小屋等）：找到门格；在旁边就交互，否则把门坐标导出。</summary>
        /// <summary>从农场走门口进屋（用游戏的门传送）。</summary>
        private void EnterHome()
        {
            try
            {
                var p = Game1.player;
                var door = new Vector2(64, 15);
                if (Math.Abs(p.Tile.X - door.X) + Math.Abs(p.Tile.Y - door.Y) > 3)
                {
                    this.Monitor.Log($"enter_home: 还没到门口（当前 {p.Tile.X},{p.Tile.Y}）", LogLevel.Warn);
                    return;
                }
                Game1.warpFarmer("FarmHouse", 9, 9, 2);
                this.Monitor.Log("enter_home: 进屋（门口传送）", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log($"enter_home 异常：{ex.Message}", LogLevel.Warn); }
        }

        private void EnterHouse(JObject jo)
        {
            string want = jo != null && jo["action"] != null ? (string)jo["action"] : "FarmHouse";
            var loc = Game1.player.currentLocation;
            if (loc.Name == "FarmHouse") { this.Monitor.Log("enter_house: 已经在家", LogLevel.Info); return; }
            // doorTile 是全局缓存：换地图后必须重扫，否则会拿农舍的门坐标去 Town 里找 SeedShop
            if (this.doorTileLoc != loc.Name) { this.doorTile = new Vector2(-1, -1); this.doorTileLoc = loc.Name; }

            var door = jo != null && jo["x"] != null && jo["y"] != null
                ? new Vector2((int)jo["x"], (int)jo["y"]) : this.doorTile;

            if (door.X < 0)
            {
                var found = new List<Vector2>();
                try
                {
                    int w = loc.Map.Layers[0].LayerWidth, h = loc.Map.Layers[0].LayerHeight;
                    for (int y = 0; y < h; y++)
                    {
                        for (int x = 0; x < w; x++)
                        {
                            foreach (var layer in new[] { "Buildings", "Back", "Front" })
                            {
                                string a = null;
                                try { a = loc.doesTileHaveProperty(x, y, "Action", layer); } catch { }
                                if (!string.IsNullOrEmpty(a)
                                && (a.StartsWith(want) || a.Contains(want) || a.Contains("LockedDoorWarp") && a.Contains(want)))
                                found.Add(new Vector2(x, y));
                            }
                        }
                    }
                }
                catch (Exception ex) { this.Monitor.Log($"enter_house 扫描异常：{ex.Message}", LogLevel.Warn); }
                if (found.Count == 0) { this.Monitor.Log($"enter_house: 找不到 {want} 的门", LogLevel.Warn); return; }
                var p0 = Game1.player;
                found.Sort((a, b) => (Math.Abs(a.X - p0.Tile.X) + Math.Abs(a.Y - p0.Tile.Y))
                                      .CompareTo(Math.Abs(b.X - p0.Tile.X) + Math.Abs(b.Y - p0.Tile.Y)));
                door = found[0];
                this.doorTile = door;
                this.Monitor.Log($"enter_house: 门在 ({door.X},{door.Y})，先走过去", LogLevel.Info);
                return;
            }

            var pl = Game1.player;
            // 店铺门是瓦片 Warp 属性（不在 loc.warps 里），checkAction 只会响一声不会传送
            // → 站到门旁边时直接按 Warp 属性 warpFarmer
            try
            {
                if (Math.Abs(pl.Tile.X - door.X) + Math.Abs(pl.Tile.Y - door.Y) <= 1)
                {
                    string warpProp = null;
                    foreach (var layer in new[] { "Buildings", "Back", "Front", "Paths" })
                    {
                        try { warpProp = loc.doesTileHaveProperty((int)door.X, (int)door.Y, "Warp", layer); } catch { }
                        if (!string.IsNullOrEmpty(warpProp)) break;
                    }
                    if (!string.IsNullOrEmpty(warpProp))
                    {
                        var parts = warpProp.Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                        if (parts.Length >= 3 && int.TryParse(parts[0], out int wx) && int.TryParse(parts[1], out int wy))
                        {
                            this.Monitor.Log($"enter_house: 门 ({door.X},{door.Y}) 瓦片传送 → {parts[2]}({wx},{wy})", LogLevel.Info);
                            Game1.warpFarmer(parts[2], wx, wy, 2);
                            return;
                        }
                    }
                }
            }
            catch (Exception ex) { this.Monitor.Log("enter_house 传送异常：" + ex.Message, LogLevel.Warn); }
            // 站在门格本身上是推不开门的（人被门框挡住）→ 先退到门下一格再交互
            if (Math.Abs(pl.Tile.X - door.X) < 0.01f && Math.Abs(pl.Tile.Y - door.Y) < 0.01f)
            {
                try
                {
                    var spot = new Point((int)door.X, (int)door.Y + 1);
                    Game1.player.controller = new StardewValley.Pathfinding.PathFindController(Game1.player, loc, spot, 3);
                    this.Monitor.Log($"enter_house: 站在门格 ({door.X},{door.Y}) 上，退到 ({spot.X},{spot.Y}) 再推", LogLevel.Info);
                }
                catch (Exception ex) { this.Monitor.Log("enter_house 退步异常：" + ex.Message, LogLevel.Warn); }
                return;
            }
            if (Math.Abs(pl.Tile.X - door.X) + Math.Abs(pl.Tile.Y - door.Y) <= 1)
            {
                try
                {
                    string before = Game1.player.currentLocation?.Name ?? "";
                    bool ok = loc.checkAction(new xTile.Dimensions.Location((int)door.X, (int)door.Y), Game1.viewport, pl);
                    this.Monitor.Log($"enter_house: 交互门 ({door.X},{door.Y}) = {ok}", LogLevel.Info);
                    // 没开门/没进去 → 稍后自动关掉提示对话框（用 tick 计数，避免卡住）
                    this.closeMenuInTicks = 24;
                }
                catch (Exception ex) { this.Monitor.Log($"enter_house 交互异常：{ex.Message}", LogLevel.Warn); }
            }
            else
            {
                // 太远就自己走过去（之前只打日志，靠调用方走；调用方坐标写错就会永远推不开门）
                this.Monitor.Log($"enter_house: 还没到门 ({door.X},{door.Y})，当前 ({pl.Tile.X},{pl.Tile.Y})，走过去", LogLevel.Info);
                try
                {
                    if (pl.controller == null)
                    {
                        var spot = new Point((int)door.X, (int)door.Y + 1);
                        if (!this.IsSafeTile(loc, spot.X, spot.Y)) spot = new Point((int)door.X, (int)door.Y - 1);
                        pl.controller = new StardewValley.Pathfinding.PathFindController(pl, loc, spot, 3);
                    }
                }
                catch (Exception ex) { this.Monitor.Log("enter_house 走路异常：" + ex.Message, LogLevel.Warn); }
            }
        }

        /// <summary>打印当前界面状态，排查卡住原因。</summary>
        /// <summary>脱困：关掉界面/对话，并把玩家挪到旁边安全格（防止触发格反复弹对话）。</summary>
        /// <summary>强制把玩家挪到指定格（先关对话），用于踩传送格脱困。</summary>
        /// <summary>离开节日：找节日出口 warp 或直接结束活动。</summary>
        private void LeaveFestival()
        {
            try
            {
                if (!Game1.isFestival())
                {
                    this.Monitor.Log("leave_festival: 不在节日里", LogLevel.Info);
                    return;
                }
                // 节日结束的官方入口
                Game1.player.completelyStopAnimatingOrDoingAction();
                if (Game1.CurrentEvent != null && Game1.CurrentEvent.isFestival)
                {
                    Game1.CurrentEvent.forceEndFestival(Game1.player);
                    this.Monitor.Log("leave_festival: 强制结束节日", LogLevel.Info);
                    return;
                }
                Game1.exitActiveMenu();
                this.Monitor.Log("leave_festival: 关掉节日界面", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log($"leave_festival 异常：{ex.Message}", LogLevel.Warn); }
        }

        private void ForceStep(JObject jo)
        {
            int x = (int)jo["x"], y = (int)jo["y"];
            var p = Game1.player;
            try
            {
                var dlg = Game1.activeClickableMenu as DialogueBox;
                if (dlg != null) dlg.closeDialogue();
                else if (Game1.activeClickableMenu != null) Game1.exitActiveMenu();
            }
            catch { }
            try { Game1.dialogueUp = false; } catch { }
            try { p.canMove = true; } catch { }
            try { p.UsingTool = false; } catch { }
            // 卡住的 PathFindController 会让玩家一直 ctrl=True 却不动，先清掉
            try { p.controller = null; } catch { }
                        try
            {
                p.Position = new Vector2(x * 64f + 32f, y * 64f + 32f);
                p.Halt();
                this.Monitor.Log($"force_step: 挪到 ({x},{y}) 位置={p.Position}", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log($"force_step 异常：{ex.Message}", LogLevel.Warn); }
        }

        private void Unstick()
        {
            // 在床上/换天过程中绝不能动，否则会打断 NewDay 导致卡死
            try
            {
                if (Game1.player.isInBed.Value) return;
            }
            catch { }
            this.UnstickCore();
        }

        private void UnstickCore()
        {
            var p = Game1.player;
            var loc = p.currentLocation;
            try
            {
                var dlg = Game1.activeClickableMenu as DialogueBox;
                if (dlg != null) dlg.closeDialogue();
                else Game1.exitActiveMenu();
            }
            catch { }
            try { Game1.dialogueUp = false; } catch { }
            try { p.canMove = true; } catch { }
            try { p.UsingTool = false; } catch { }
            try
            {
                int tx = (int)p.Tile.X, ty = (int)p.Tile.Y;
                var dirs = new[] { new Vector2(0, 1), new Vector2(1, 0), new Vector2(0, -1), new Vector2(-1, 0),
                                   new Vector2(1, 1), new Vector2(-1, 1), new Vector2(1, -1), new Vector2(-1, -1) };
                foreach (var d in dirs)
                {
                    int nx = tx + (int)d.X, ny = ty + (int)d.Y;
                    if (!this.IsSafeTile(loc, nx, ny)) continue;
                    p.Position = new Vector2(nx * 64f + 32f, ny * 64f + 32f);
                    p.Halt();
                    this.Monitor.Log($"unstick: 已挪到 ({nx},{ny})", LogLevel.Info);
                    return;
                }
                this.Monitor.Log("unstick: 四周都不安全，只关了界面", LogLevel.Warn);
            }
            catch (Exception ex) { this.Monitor.Log($"unstick 异常：{ex.Message}", LogLevel.Warn); }
        }

        private void UiState()
        {
            try
            {
                var p = Game1.player;
                string menu = Game1.activeClickableMenu?.GetType().Name ?? "null";
                string dlg = Game1.dialogueUp ? "yes" : "no";
                this.Monitor.Log($"UI: menu={menu} dialogueUp={dlg} isQuestion={Game1.isQuestion} event={Game1.CurrentEvent?.id ?? "-"} usingTool={p.UsingTool} canMove={p.CanMove} isInBed={p.isInBed.Value} newDay={Game1.newDay} fade={Game1.fadeToBlack} gFade={Game1.globalFade} ctrl={(p.controller != null)} time={Game1.timeOfDay} loc={p.currentLocation?.Name}", LogLevel.Alert);
            }
            catch (Exception ex) { this.Monitor.Log("ui_state 异常: " + ex.Message, LogLevel.Warn); }
        }

        /// <summary>结束当前剧情/节日事件（人物被事件卡住、时间冻结时用）。</summary>
        private void EndEvent()
        {
            try
            {
                var ev = Game1.CurrentEvent;
                if (ev == null)
                {
                    this.Monitor.Log("end_event: 当前没有事件", LogLevel.Info);
                    return;
                }
                this.Monitor.Log($"end_event: 结束事件 {ev.id}（festival={ev.isFestival}）", LogLevel.Info);
                try { ev.exitEvent(); }
                catch (Exception ex1)
                {
                    this.Monitor.Log("end_event: exitEvent 失败 → " + ex1.Message, LogLevel.Warn);
                    try { ev.forceEndFestival(Game1.player); } catch { }
                }
                try { Game1.eventUp = false; } catch { }
                this.Monitor.Log($"end_event: 处理完，CurrentEvent={(Game1.CurrentEvent == null ? "null" : "still")}", LogLevel.Info);
                this.FixPosition();
            }
            catch (Exception ex) { this.Monitor.Log("end_event 异常：" + ex.Message, LogLevel.Warn); }
        }

        private void CloseMenu()
        {
            try
            {
                if (Game1.activeClickableMenu != null)
                {
                    this.Monitor.Log($"close_menu: 关闭 {Game1.activeClickableMenu.GetType().Name}", LogLevel.Info);
                    Game1.exitActiveMenu();
                }
                if (Game1.dialogueUp)
                {
                    this.Monitor.Log("close_menu: 强制结束对话(dialogueUp)", LogLevel.Info);
                    Game1.dialogueUp = false;
                    try { Game1.player.canMove = true; } catch { }
                    try { Game1.player.UsingTool = false; } catch { }
                }
            }
            catch (Exception ex) { this.Monitor.Log($"close_menu 异常：{ex.Message}", LogLevel.Warn); }
        }

        private void UseStaircase(JObject jo)
        {
            var p = Game1.player;
            var loc = p.currentLocation;
            // 确保背包里有楼梯
            StardewValley.Object stairs = null;
            for (int i = 0; i < p.Items.Count; i++)
            {
                if (p.Items[i] is StardewValley.Object o && o.ItemId == "71") { stairs = o; break; }
            }
            if (stairs == null)
            {
                var made = ItemRegistry.Create("(O)71", 999, 0);
                if (!p.addItemToInventoryBool(made, false)) { this.Monitor.Log("use_staircase: 背包放不下楼梯", LogLevel.Warn); return; }
                stairs = made as StardewValley.Object;
            }
            int dir = p.FacingDirection;
            int dx = dir == 1 ? 1 : (dir == 3 ? -1 : 0);
            int dy = dir == 2 ? 1 : (dir == 0 ? -1 : 0);
            var tile = new Vector2((int)p.Tile.X + dx, (int)p.Tile.Y + dy);
            if (jo != null && jo["x"] != null && jo["y"] != null) tile = new Vector2((int)jo["x"], (int)jo["y"]);
            try
            {
                bool ok = stairs.placementAction(loc, (int)tile.X * 64, (int)tile.Y * 64, p);
                this.Monitor.Log($"use_staircase: 放置 ({tile.X},{tile.Y}) 结果={ok}", LogLevel.Info);
                if (ok)
                {
                    stairs.Stack--;
                    if (stairs.Stack <= 0)
                    {
                        int idx = p.Items.IndexOf(stairs);
                        if (idx >= 0) p.Items[idx] = null;
                    }
                }
            }
            catch (Exception ex) { this.Monitor.Log($"use_staircase 异常：{ex.Message}", LogLevel.Warn); }
        }

        /// <summary>把背包里的某个可放置物品放到指定格（稻草人/箱子等）。</summary>
        private void PlaceItem(JObject jo)
        {
            string want = (string)jo["name"] ?? "";
            int x = (int)jo["x"], y = (int)jo["y"];
            var p = Game1.player;
            var loc = p.currentLocation;
            Item found = null;
            foreach (var it in p.Items)
            {
                if (it == null) continue;
                if ((it.Name ?? "").IndexOf(want, StringComparison.OrdinalIgnoreCase) >= 0) { found = it; break; }
            }
            if (found == null) { this.Monitor.Log($"place_item: 背包没有 {want}", LogLevel.Warn); return; }
            try
            {
                var asObj = found as StardewValley.Object;
                if (asObj == null) { this.Monitor.Log($"place_item: {want} 不是可放置物体", LogLevel.Warn); return; }
                bool ok = asObj.placementAction(loc, x * 64 + 32, y * 64 + 32, p);
                this.Monitor.Log($"place_item: {want} @ ({x},{y}) = {ok}", LogLevel.Info);
                if (ok && found.Stack > 0)
                {
                    found.Stack--;
                    if (found.Stack <= 0) { int idx = p.Items.IndexOf(found); if (idx >= 0) p.Items[idx] = null; }
                }
            }
            catch (Exception ex) { this.Monitor.Log($"place_item 异常：{ex.Message}", LogLevel.Warn); }
        }

        private void PlaceStairs(JObject jo)
        {
            var p = Game1.player;
            var loc = p.currentLocation;
            int dir = p.FacingDirection;
            int dx = dir == 1 ? 1 : (dir == 3 ? -1 : 0);
            int dy = dir == 2 ? 1 : (dir == 0 ? -1 : 0);
            var target = new Vector2((int)p.Tile.X + dx, (int)p.Tile.Y + dy);
            if (jo != null && jo["x"] != null && jo["y"] != null)
                target = new Vector2((int)jo["x"], (int)jo["y"]);
            try
            {
                // 用真正的「楼梯」道具 (O)71 走原版放置逻辑；(O)173 不是合法物品，
                // 放下去只会生成「错误物品」，不能下楼（2026-11-19 修）
                var stairs = (StardewValley.Object)ItemRegistry.Create("(O)71", 1, 0);
                bool ok = stairs.placementAction(loc, (int)target.X * 64, (int)target.Y * 64, p);
                this.Monitor.Log($"place_stairs: 真楼梯 @({target.X},{target.Y}) = {ok}", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log("place_stairs 异常：" + ex.Message, LogLevel.Warn); }
        }

        /// <summary>清理背包/箱子里的 Error Item 之类垃圾。</summary>
        private void CleanGarbage()
        {
            var p = Game1.player;
            int removed = 0;
            for (int i = 0; i < p.Items.Count; i++)
            {
                var it = p.Items[i];
                if (it == null) continue;
                string nm = it.Name ?? "";
                if (nm.Contains("Error Item") || nm.Contains("错误物品") || it.ItemId == "Error")
                {
                    p.Items[i] = null;
                    removed++;
                }
            }
            // 箱子里也清
            try
            {
                var farm = Game1.getFarm();
                foreach (var kv in this.AllChests(farm))
                {
                    for (int i = 0; i < kv.Value.Items.Count; i++)
                    {
                        var it = kv.Value.Items[i];
                        if (it == null) continue;
                        string nm = it.Name ?? "";
                        if (nm.Contains("Error Item") || it.ItemId == "Error") { kv.Value.Items[i] = null; removed++; }
                    }
                }
            }
            catch { }
            this.Monitor.Log($"clean_garbage: 清理 {removed} 个垃圾物品", LogLevel.Info);
        }

        /// <summary>安全「选中」某个工具：只改 CurrentToolIndex，不碰 Items。
        /// 2026-11-22 修：player.CurrentTool = x 的 setter 是 Items[CurrentToolIndex] = x，
        /// 会把当前选中格覆盖掉——鱼竿 slots 被写进镐子/镰刀的格子，于是「镐子没了、鱼竿变两把」。</summary>
        private void SelectTool(Farmer who, Item tool)
        {
            if (who == null || tool == null) return;
            try
            {
                int idx = who.Items.IndexOf(tool);
                if (idx >= 0) who.CurrentToolIndex = idx;
            }
            catch { }
        }
        /// <summary>记录「刚钓上来的鱼」，3 秒后校验是否真的进包（举鱼动画被打断时兜底补上）。</summary>
        public static void ArmCatchVerify(Item fish)
        {
            try
            {
                PendingCatchFish = fish;
                PendingCatchTicks = 180;
                PendingCatchBefore = CountItem(fish == null ? null : fish.ItemId);
            }
            catch { }
        }

        private static int CountItem(string id)
        {
            if (string.IsNullOrEmpty(id) || Game1.player == null) return 0;
            int n = 0;
            try
            {
                foreach (var it in Game1.player.Items)
                {
                    if (it != null && it.ItemId == id) n += Math.Max(1, it.Stack);
                }
            }
            catch { }
            return n;
        }

        private void TickCatchVerify()
        {
            if (PendingCatchFish == null) return;
            if (--PendingCatchTicks > 0) return;
            var fish = PendingCatchFish;
            PendingCatchFish = null;
            try
            {
                int now = CountItem(fish.ItemId);
                if (now > PendingCatchBefore)
                {
                    this.Monitor.Log("钓鱼: 举鱼动画正常结束，已入包 " + fish.Name, LogLevel.Trace);
                    return;
                }
                var who = Game1.player;
                bool added = false;
                try
                {
                    var obj = fish as StardewValley.Object;
                    if (obj != null) added = who.addItemToInventoryBool(obj, false);
                }
                catch { }
                if (!added) { try { Game1.createItemDebris(fish, who.Position, who.FacingDirection); } catch { } }
                try
                {
                    if (who.fishCaught != null && !who.fishCaught.ContainsKey(fish.ItemId))
                        who.fishCaught[fish.ItemId] = new int[] { 1, 0 };
                    else if (who.fishCaught != null)
                        who.fishCaught[fish.ItemId][0]++;
                }
                catch { }
                this.Monitor.Log("钓鱼: 举鱼动画被打断，兜底入包 " + fish.Name, LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log("兜底入包异常: " + ex.Message, LogLevel.Warn); }
        }
        private void EnsureTools()
        {
            var p = Game1.player;
            // 只维护这 5 类工具（水壶/剑等放箱子里，不再补造）
            var required = new (Type type, int level, string label)[]
            {
                (typeof(StardewValley.Tools.FishingRod), 4, "鱼竿"),
                (typeof(Axe), 4, "斧头"),
                (typeof(Hoe), 4, "锄头"),
                (typeof(Pickaxe), 4, "镐"),
            };
            int fixedCount = 0;
            foreach (var req in required)
            {
                var found = new List<(int idx, Tool tool)>();
                for (int i = 0; i < p.Items.Count; i++)
                {
                    if (p.Items[i] is Tool t && req.type.IsInstanceOfType(t)) found.Add((i, t));
                }
                // 去重：保留等级最高的一个
                if (found.Count > 1)
                {
                    found.Sort((a, b) => b.tool.UpgradeLevel.CompareTo(a.tool.UpgradeLevel));
                    for (int k = 1; k < found.Count; k++)
                    {
                        p.Items[found[k].idx] = null;
                        fixedCount++;
                        this.Monitor.Log($"ensure_tools: 移除多余 {req.label}", LogLevel.Info);
                    }
                }
                // 缺失则补
                if (found.Count == 0)
                {
                    Tool nt = null;
                    if (req.type == typeof(StardewValley.Tools.FishingRod)) nt = new StardewValley.Tools.FishingRod();
                    else if (req.type == typeof(Axe)) nt = new Axe();
                    else if (req.type == typeof(Hoe)) nt = new Hoe();
                    else if (req.type == typeof(Pickaxe)) nt = new Pickaxe();
                    else if (req.type == typeof(WateringCan)) nt = new WateringCan();
                    if (nt != null)
                    {
                        try { nt.UpgradeLevel = req.level; } catch { }
                        if (p.addItemToInventoryBool(nt, false))
                        {
                            fixedCount++;
                            this.Monitor.Log($"ensure_tools: 补回 {req.label}", LogLevel.Info);
                        }
                    }
                }
                else
                {
                    var t = found[0].tool;
                    if (t.UpgradeLevel < req.level)
                    {
                        try { t.UpgradeLevel = req.level; t.ResetParentSheetIndex(); fixedCount++; } catch { }
                    }
                }
            }
            // 镰刀（武器类）单独检查
            bool hasScythe = false;
            foreach (var it in p.Items)
            {
                if (it is Tool tt && ((it.Name ?? "").Contains("镰刀") || (it.Name ?? "").Contains("Scythe"))) { hasScythe = true; break; }
            }
            if (!hasScythe)
            {
                // 箱子里有就拿回来，没有就不造（避免刷物品）
                this.Monitor.Log("ensure_tools: 背包没有镰刀（请从箱子取）", LogLevel.Warn);
            }
            this.lastToolSig = ToolSignature();
            this.Monitor.Log($"ensure_tools: 修复 {fixedCount} 处，当前工具 {this.lastToolSig}", LogLevel.Info);
        }

        private void StackTest()
        {
            var bait = ItemRegistry.Create("(O)774", 1, 0);
            this.Monitor.Log($"stack_test: WildBait max={bait.maximumStackSize()} Stack={bait.Stack}", LogLevel.Alert);
            var ore = ItemRegistry.Create("(O)378", 1, 0);
            this.Monitor.Log($"stack_test: CopperOre max={ore.maximumStackSize()}", LogLevel.Alert);
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
        /// <summary>按顺序给箱子分配角色：seeds/crop/fish/mineral/treasure/misc。</summary>
        /// <summary>把箱子里所有物品按角色重新分箱（种子箱单独放种子）。</summary>
        private void SortChests()
        {
            var loc = Game1.getFarm();
            var chests = this.AllChests(loc);
            if (chests.Count == 0) { this.Monitor.Log("sort_chests: 没有箱子", LogLevel.Warn); return; }
            var byRole = new Dictionary<string, Chest>();
            foreach (var kvp in chests)
            {
                string role;
                if (kvp.Value.modData.TryGetValue("whale.role", out role) && !string.IsNullOrEmpty(role) && !byRole.ContainsKey(role))
                    byRole[role] = kvp.Value;
            }
            var pool = new List<Item>();
            foreach (var kvp in chests)
            {
                foreach (var it in kvp.Value.Items) if (it != null) pool.Add(it);
                for (int k = 0; k < kvp.Value.Items.Count; k++) kvp.Value.Items[k] = null;
            }
            var allChests = new List<Chest>(byRole.Values);
            foreach (var kvp in chests) if (!allChests.Contains(kvp.Value)) allChests.Add(kvp.Value);
            int moved = 0, failed = 0;
            foreach (var it in pool)
            {
                string role = this.RoleForItem(it);
                Chest t; byRole.TryGetValue(role, out t);
                if (t != null && t.addItem(it) == null) { moved++; continue; }
                bool ok = false;
                foreach (var c2 in allChests)
                {
                    if (c2 == t) continue;
                    if (c2.addItem(it) == null) { moved++; ok = true; break; }
                }
                if (!ok) failed++;
            }
            this.Monitor.Log($"sort_chests: 重排 {moved} 件，放不下 {failed}", LogLevel.Info);
        }

        /// <summary>农田区中心（读 .plot_rect，读不到退回小屋门口）。</summary>
        private Point PlotCenter()
        {
            try
            {
                string pf = @"D:\live\logs\.plot_rect";
                if (File.Exists(pf))
                {
                    var parts = File.ReadAllText(pf).Trim().Split(',');
                    if (parts.Length == 4)
                        return new Point((int.Parse(parts[0]) + int.Parse(parts[2])) / 2,
                                         (int.Parse(parts[1]) + int.Parse(parts[3])) / 2);
                }
            }
            catch { }
            return new Point(64, 15);
        }

        private void AssignChests(JObject jo)
        {
            var loc = Game1.getFarm();
            var chests = this.AllChests(loc);
            // 2026-11-18 新规则：只认「离农田中心最近」的 3 个箱子，其余不参与归箱
            var center = this.PlotCenter();
            chests.Sort((a, b) =>
            {
                int da = Math.Abs((int)a.Key.X - center.X) + Math.Abs((int)a.Key.Y - center.Y);
                int db = Math.Abs((int)b.Key.X - center.X) + Math.Abs((int)b.Key.Y - center.Y);
                return da.CompareTo(db);
            });
            string[] def = { "seeds", "crop", "misc" };
            var roles = new List<string>();
            if (jo != null && jo["roles"] is JArray ra)
                foreach (var x in ra) roles.Add((string)x);
            if (roles.Count == 0) roles.AddRange(def);
            foreach (var kvp in chests) { try { kvp.Value.modData.Remove("whale.role"); } catch { } }
            int i = 0;
            foreach (var kvp in chests)
            {
                if (i >= roles.Count) break;
                kvp.Value.modData["whale.role"] = roles[i];
                int d = Math.Abs((int)kvp.Key.X - center.X) + Math.Abs((int)kvp.Key.Y - center.Y);
                this.Monitor.Log($"assign_chests: ({kvp.Key.X},{kvp.Key.Y}) = {roles[i]}（离农田中心 {d} 格）", LogLevel.Info);
                i++;
            }
            if (chests.Count > roles.Count)
                this.Monitor.Log($"assign_chests: 另有 {chests.Count - roles.Count} 个箱子不参与归箱", LogLevel.Info);
        }

        /// <summary>
        /// 重新整箱（2026-11-18 规则）：先按「离农田最近 3 个」分配角色，
        /// 再把所有箱子里的东西倒出来重放：种子→种子箱、作物→作物箱、其它→杂物箱。
        /// </summary>
        private void Tidy(JObject jo)
        {
            var loc = Game1.getFarm();
            var chests = this.AllChests(loc);
            if (chests.Count < 3) { this.Monitor.Log($"tidy: 箱子只有 {chests.Count} 个（至少 3 个：种子/作物/杂物）", LogLevel.Warn); return; }

            this.AssignChests(jo);
            chests = this.AllChests(loc);
            Chest seeds = this.FindChestByRole(loc, "seeds");
            Chest crop = this.FindChestByRole(loc, "crop");
            Chest misc = this.FindChestByRole(loc, "misc");
            if (seeds == null || crop == null || misc == null)
            { this.Monitor.Log("tidy: 角色缺失（seeds/crop/misc）", LogLevel.Warn); return; }

            var all = new List<Item>();
            foreach (var kvp in chests)
            {
                foreach (var it in kvp.Value.Items) if (it != null) all.Add(it);
                for (int k = 0; k < kvp.Value.Items.Count; k++) kvp.Value.Items[k] = null;
            }

            int placed = 0, failed = 0, seedN = 0, cropN = 0, miscN = 0;
            foreach (var it in all)
            {
                string role = this.RoleForItem(it);
                if (role != "seeds" && role != "crop") role = "misc";
                Chest target = role == "seeds" ? seeds : (role == "crop" ? crop : misc);
                if (target.addItem(it) == null)
                {
                    placed++;
                    if (role == "seeds") seedN++; else if (role == "crop") cropN++; else miscN++;
                }
                else failed++;
            }
            this.Monitor.Log($"tidy: 种子箱{seedN} 作物箱{cropN} 杂物箱{miscN}，放不下 {failed}", LogLevel.Info);
        }
        private void StashAll()
        {
            var loc = Game1.getFarm();
            var p = Game1.player;
            var chests = this.AllChests(loc);
            if (chests.Count == 0) { this.Monitor.Log("stash_all: 农场没有箱子", LogLevel.Warn); return; }

            var byRole = new Dictionary<string, Chest>();
            foreach (var kvp in chests)
            {
                string role;
                if (kvp.Value.modData.TryGetValue("whale.role", out role) && !string.IsNullOrEmpty(role) && !byRole.ContainsKey(role))
                    byRole[role] = kvp.Value;
            }

            var allChestList = new List<Chest>();
            foreach (var kvp in chests) allChestList.Add(kvp.Value);

            int moved = 0, kept = 0, failed = 0;
            var roleCount = new Dictionary<string, int>();
            for (int i = 0; i < p.Items.Count; i++)
            {
                var it = p.Items[i];
                if (it == null) continue;
                // 只保留这五类工具（外加浇水壶）：锄头/鱼竿/斧子/镐子/镰刀/水壶
                // 2026-11-21：水壶以前会被收进箱子，而 ensure_tools 不补造水壶 → 之后手动浇水全废
                // 种子留在身上：收进箱子后 agent 会以为没种子，又去买一遍
                if (it is StardewValley.Object seedObj && seedObj.Category == -74) { kept++; continue; }
                if (it is Tool tl)
                {
                    bool keep = tl is Hoe || tl is StardewValley.Tools.FishingRod
                                || tl is Axe || tl is Pickaxe
                                || tl is WateringCan
                                || (tl is MeleeWeapon && ((tl.Name ?? "").Contains("镰刀")
                                                          || (tl.Name ?? "").Contains("Scythe")));
                    if (keep) { kept++; continue; }
                    // 其他工具（剑等）也放进箱子
                }
                string role = this.RoleForItem(it);
                // 只有 3 个箱子：种子 / 作物 / 其它（鱼、矿石、宝物全归 misc）
                if (role != "seeds" && role != "crop") role = "misc";
                Chest target = null;
                byRole.TryGetValue(role, out target);
                if (target != null && target.addItem(it) == null)
                {
                    p.Items[i] = null; moved++;
                    roleCount[role] = roleCount.TryGetValue(role, out var c) ? c + 1 : 1;
                    continue;
                }
                // 目标箱满/缺失 → 先找其它还有空位的箱子（保证背包清干净）
                bool placed = false;
                foreach (var c2 in allChestList)
                {
                    if (c2 == target) continue;
                    if (c2.addItem(it) == null)
                    {
                        p.Items[i] = null;
                        moved++;
                        placed = true;
                        roleCount["overflow"] = roleCount.TryGetValue("overflow", out var ov) ? ov + 1 : 1;
                        break;
                    }
                }
                if (!placed) failed++;
            }
            var detail = string.Join("，", roleCount.Select(kv => kv.Key + "箱 " + kv.Value + " 件"));
            this.Monitor.Log($"stash_all: 收进 {moved} 件（{detail}），工具/种子保留 {kept}，放不下 {failed}", LogLevel.Info);
            if (moved > 0) { try { this.MergeStacks(); } catch { } }
        }

        /// <summary>按物品类型选目标箱子角色。</summary>
        private string RoleForItem(Item it)
        {
            if (it == null) return "misc";
            if (this.IsSeedItem(it)) return "seeds";
            var o = it as StardewValley.Object;
            int cat = o?.Category ?? 0;
            if (cat == -4) return "fish";
            if (cat == -75 || cat == -79 || cat == -80 || cat == -81) return "crop";
            if (cat == -2 || cat == -12 || cat == -15) return "mineral";
            if (it is Ring || it is Boots || it is Hat || it is MeleeWeapon
                || it is StardewValley.Objects.Trinkets.Trinket) return "treasure";
            return "misc";
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

        /// <summary>把当前地图的 warp（出口/入口）打到日志里，方便找路。</summary>
        private void ScanWarps()
        {
            try
            {
                var loc = Game1.player.currentLocation;
                var lines = new List<string>();
                foreach (var w in loc.warps)
                    lines.Add($"{w.X},{w.Y}->{w.TargetName}@{w.TargetX},{w.TargetY}");
                this.Monitor.Log($"WARPS {loc.Name}: " + string.Join(" | ", lines), LogLevel.Alert);
            }
            catch (Exception ex) { this.Monitor.Log("scan_warps 异常: " + ex.Message, LogLevel.Warn); }
        }
        /// <summary>列出 Data/Tools 里所有镰刀类工具（找 id 用）。</summary>
        private void ScanTools()
        {
            try
            {
                var lines = new List<string>();
                foreach (var kv in Game1.toolData)
                {
                    string nm = "";
                    try { nm = kv.Value?.Name ?? ""; } catch { }
                    if (nm.IndexOf("scythe", StringComparison.OrdinalIgnoreCase) >= 0 || nm.Contains("镰刀"))
                        lines.Add(kv.Key + "=" + nm);
                }
                this.Monitor.Log("TOOLS: " + string.Join(" | ", lines), LogLevel.Alert);
            }
            catch (Exception ex) { this.Monitor.Log("scan_tools 异常: " + ex.Message, LogLevel.Warn); }
        }

        /// <summary>直接给一个工具（参数是 Data/Tools 的 key）。</summary>
        private void GiveTool(string id)
        {
            try
            {
                Item it = ItemRegistry.Create("(T)" + id, 1, 0);
                if (it == null) { this.Monitor.Log("give_tool: 创建失败 " + id, LogLevel.Warn); return; }
                Game1.player.addItemToInventoryBool(it, false);
                this.Monitor.Log($"give_tool: 给了 {it.Name}（(T){id}）", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log("give_tool 异常: " + ex.Message, LogLevel.Warn); }
        }
        /// <summary>补一把铱镰刀（1.6 的镰刀是 MeleeWeapon，id 66=铱、53=金、47=普通）。</summary>
        private void GiveScythe()
        {
            foreach (var id in new[] { "66", "53", "47" })
            {
                try
                {
                    var w = new MeleeWeapon(id);
                    if (Game1.player.addItemToInventoryBool(w, false))
                    {
                        this.Monitor.Log($"give_scythe: 给了 id={id} name={w.Name}", LogLevel.Info);
                        string n = (w.Name ?? "").ToLower();
                        if (n.Contains("iridium") || (w.Name ?? "").Contains("铱")) return;
                    }
                }
                catch (Exception ex) { this.Monitor.Log($"give_scythe id={id} 异常: {ex.Message}", LogLevel.Warn); }
            }
        }
        /// <summary>把**所有箱子**里的作物直接投进出货箱（2026-11-18：以前只清作物箱，overflow 的作物卖不掉）。</summary>
        private void ShipFromChests()
        {
            var farm = Game1.getFarm();
            var bin = farm?.getShippingBin(Game1.player);
            if (bin == null) { this.Monitor.Log("ship_chests: 找不到出货箱", LogLevel.Warn); return; }
            int moved = 0, chests = 0;
            foreach (var kvp in this.AllChests(farm))
            {
                var chest = kvp.Value;
                if (chest == null) continue;
                bool touched = false;
                for (int i = 0; i < chest.Items.Count; i++)
                {
                    if (!(chest.Items[i] is StardewValley.Object obj)) continue;
                    int cat = obj.Category;
                    if (cat != -75 && cat != -79 && cat != -80) continue;
                    bin.Add(obj);
                    moved += obj.Stack;
                    chest.Items[i] = null;
                    touched = true;
                }
                if (touched) chests++;
            }
            this.Monitor.Log($"ship_chests: 从 {chests} 个箱子投出 {moved} 个作物", LogLevel.Info);
        }
        /// <summary>把矿石+煤塞进熔炉（自动收集已完成的）。</summary>
        private void LoadMachines(string machineName, string itemName, int times)
        {
            var farm = Game1.getFarm();
            var p = Game1.player;
            int n = 0;
            foreach (var key in farm.objects.Keys.ToList())
            {
                if (n >= times) break;
                var m = farm.objects[key];
                if (m == null) continue;
                if (!(m.Name ?? "").Contains(machineName)) continue;
                if (m.heldObject.Value != null || m.readyForHarvest.Value) continue;
                Item src = null;
                foreach (var it in p.Items)
                {
                    var o = it as StardewValley.Object;
                    if (o == null || o.Name == null) continue;
                    bool isCrop = o.Category == -75 || o.Category == -79 || o.Category == -80;
                    bool match = string.IsNullOrEmpty(itemName) ? isCrop
                                 : o.Name.IndexOf(itemName, StringComparison.OrdinalIgnoreCase) >= 0;
                    if (match) { src = o; break; }
                }
                if (src == null) break;
                try { if (m.performObjectDropInAction(src, false, p)) n++; } catch { }
            }
            this.Monitor.Log("load_machines: " + machineName + " x" + n, LogLevel.Info);
        }

        /// <summary>把某一格的可放置物（罐头瓶/熔炉/箱子…）拾回背包。</summary>
        private void PickupAt(int x, int y)
        {
            var loc = Game1.player.currentLocation;
            var key = new Vector2(x, y);
            if (loc == null || !loc.objects.TryGetValue(key, out var obj) || obj == null)
            {
                this.Monitor.Log($"pickup_at: ({x},{y}) 没有物体", LogLevel.Warn);
                return;
            }
            var pTile = Game1.player.Tile;
            if (Math.Abs(pTile.X - x) + Math.Abs(pTile.Y - y) > 3)
            {
                this.Monitor.Log($"pickup_at: 离 ({x},{y}) 太远", LogLevel.Warn);
                return;
            }
            try
            {
                loc.objects.Remove(key);
                bool ok = Game1.player.addItemToInventoryBool(obj, false);
                this.Monitor.Log($"pickup_at: 拾起 {obj.Name} @({x},{y}) = {ok}", LogLevel.Info);
            }
            catch (Exception ex)
            {
                this.Monitor.Log("pickup_at 异常: " + ex.Message, LogLevel.Warn);
            }
        }

        private void WithdrawByName(string name, int limit)
        {
            var p = Game1.player;
            var farm = Game1.getFarm();
            int moved = 0;
            foreach (var key in farm.objects.Keys.ToList())
            {
                var chest = farm.objects[key] as Chest;
                if (chest == null) continue;
                for (int i = 0; i < chest.Items.Count && moved < limit; i++)
                {
                    var it = chest.Items[i];
                    if (it == null || it.Name == null || !it.Name.Contains(name)) continue;
                    int want = Math.Min(it.Stack, limit - moved);
                    if (want <= 0) break;
                    Item piece = it.getOne();
                    piece.Stack = want;
                    if (p.addItemToInventoryBool(piece, false))
                    {
                        if (it.Stack - want <= 0) chest.Items[i] = null;
                        else it.Stack -= want;
                        moved += want;
                    }
                }
            }
            this.Monitor.Log("withdraw_name: " + name + " x" + moved, LogLevel.Info);
        }

        private void Smelt(string oreName, int times)
        {
            var farm = Game1.getFarm();
            var p = Game1.player;
            int done = 0;
            var furnaces = new List<StardewValley.Object>();
            foreach (var key in farm.objects.Keys.ToList())
            {
                var ob = farm.objects[key];
                if (ob != null && (ob.Name ?? "").Contains("Furnace")) furnaces.Add(ob);
            }
            foreach (var f in furnaces)
            {
                if (done >= times) break;
                try
                {
                    if (f.readyForHarvest.Value) { f.checkForAction(p); done++; continue; }
                    if (f.heldObject.Value != null) continue;
                    Item ore = null;
                    foreach (var it in p.Items)
                    {
                        if (!(it is StardewValley.Object o2) || o2.Name == null) continue;
                        if (o2.Name.Contains(oreName) && o2.Name.Contains("Ore")) { ore = o2; break; }
                    }
                    if (ore == null) continue;
                    bool hasCoal = false;
                    foreach (var it in p.Items)
                    {
                        if (it is StardewValley.Object c && (c.Name ?? "").Contains("Coal")) { hasCoal = true; break; }
                    }
                    if (!hasCoal) break;
                    if (f.performObjectDropInAction(ore, false, p)) done++;
                }
                catch (Exception ex) { this.Monitor.Log("smelt error: " + ex.Message, LogLevel.Warn); }
            }
            this.Monitor.Log("smelt: " + oreName + " x" + done + " (furnaces " + furnaces.Count + ")", LogLevel.Info);
        }

        private void CollectMachines()
        {
            var farm = Game1.getFarm();
            var p = Game1.player;
            int n = 0;
            foreach (var key in farm.objects.Keys.ToList())
            {
                var ob = farm.objects[key];
                if (ob != null && ob.readyForHarvest.Value)
                {
                    try { ob.checkForAction(p); n++; } catch { }
                }
            }
            this.Monitor.Log("collect_machines: " + n, LogLevel.Info);
        }

        /// <summary>导出全部制作配方：名称/是否已学/材料够不够/产物，给 agent 决定做啥。</summary>
        private void DumpRecipes()
        {
            try
            {
                var arr = new JArray();
                var all = Game1.content.Load<Dictionary<string, string>>("Data/CraftingRecipes");
                foreach (var key in all.Keys)
                {
                    try
                    {
                        var recipe = new CraftingRecipe(key, false);
                        if (recipe == null || recipe.name == null) continue;
                        bool have = false;
                        try { have = recipe.doesFarmerHaveIngredientsInInventory(new List<Item>()); } catch { }
                        var outItem = recipe.createItem();
                        arr.Add(new JObject
                        {
                            ["name"] = key,
                            ["learned"] = Game1.player.craftingRecipes.ContainsKey(key),
                            ["have"] = have,
                            ["output"] = recipe.name,
                            ["outputId"] = outItem?.ItemId ?? "",
                            ["big"] = recipe.bigCraftable,
                            ["qty"] = outItem?.Stack ?? 1
                        });
                    }
                    catch { }
                }
                var path = Path.Combine(this.Helper.DirectoryPath, "recipes.json");
                File.WriteAllText(path, arr.ToString(Newtonsoft.Json.Formatting.Indented), new System.Text.UTF8Encoding(false));
                this.Monitor.Log($"recipes: 导出 {arr.Count} 条 → {path}", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log("recipes 异常：" + ex.Message, LogLevel.Warn); }
        }

        private void Craft(string itemName, int count)
        {
            if (string.IsNullOrWhiteSpace(itemName)) return;
            int made = 0;
            for (int i = 0; i < count; i++)
            {
                try
                {
                    var recipe = new CraftingRecipe(itemName, false);
                    if (recipe == null || recipe.name == null) break;
                    IList<Item> extra = new List<Item>();
                    if (!recipe.doesFarmerHaveIngredientsInInventory(extra)) break;
                    recipe.consumeIngredients(new List<StardewValley.Inventories.IInventory>());
                    var outItem = recipe.createItem();
                    if (outItem == null) break;
                    Game1.player.addItemToInventoryBool(outItem, false);
                    made++;
                }
                catch (Exception ex) { this.Monitor.Log("craft error: " + ex.Message, LogLevel.Warn); break; }
            }
            this.Monitor.Log("craft: " + itemName + " x" + made, LogLevel.Info);
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

        /// <summary>停止钓鱼泵（脚本退出时必调，避免空挥刷屏）。</summary>
        /// <summary>给鱼竿上饵：优先 野性鱼饵(双倍) > 高级鱼饵 > 普通鱼饵。</summary>
        /// <summary>房屋升级（1 厨房 1万 / 2 育儿室 5万 / 3 地窖 10万）。</summary>
        private void UpgradeHouse()
        {
            try
            {
                int lvl = Game1.player.HouseUpgradeLevel;
                if (lvl >= 3) { this.Monitor.Log("upgrade_house: 已经是最高级", LogLevel.Info); return; }
                int cost = lvl == 0 ? 10000 : (lvl == 1 ? 50000 : 100000);
                if (Game1.player.Money < cost)
                {
                    this.Monitor.Log($"upgrade_house: 钱不够（需要 {cost}，现有 {Game1.player.Money}）", LogLevel.Warn);
                    return;
                }
                Game1.player.Money -= cost;
                Game1.player.HouseUpgradeLevel = lvl + 1;
                try
                {
                    var home = StardewValley.Utility.getHomeOfFarmer(Game1.player);
                    home?.updateMap();
                }
                catch { }
                if (lvl + 1 >= 3)
                {
                    try
                    {
                        if (Game1.getLocationFromName("Cellar") == null)
                            Game1.locations.Add(new GameLocation("Maps/Cellar", "Cellar"));
                    }
                    catch { }
                }
                this.Monitor.Log($"upgrade_house: 升到 {lvl + 1} 级（花费 {cost}）", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log("upgrade_house 异常: " + ex.Message, LogLevel.Warn); }
        }

        /// <summary>解锁配方（合成/烹饪）。</summary>
        private void LearnRecipe(string name)
        {
            if (string.IsNullOrWhiteSpace(name)) return;
            try
            {
                if (!Game1.player.craftingRecipes.ContainsKey(name)) Game1.player.craftingRecipes[name] = 0;
                if (!Game1.player.cookingRecipes.ContainsKey(name)) Game1.player.cookingRecipes[name] = 0;
                this.Monitor.Log("learn_recipe: " + name, LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log("learn_recipe 异常: " + ex.Message, LogLevel.Warn); }
        }

        /// <summary>导出农场上所有加工设备（位置/名字/是否可收），给 agent 走路去收。</summary>
        private JArray MachinesJson()
        {
            var arr = new JArray();
            try
            {
                var farm = Game1.getFarm();
                foreach (var key in farm.objects.Keys.ToList())
                {
                    var o = farm.objects[key];
                    if (o == null) continue;
                    bool isMachine = o.bigCraftable.Value || !string.IsNullOrEmpty(o.Name) && (
                        o.Name.Contains("Jar") || o.Name.Contains("Keg") || o.Name.Contains("Furnace")
                        || o.Name.Contains("Dehydrator") || o.Name.Contains("Smoker") || o.Name.Contains("Cask")
                        || o.Name.Contains("罐头") || o.Name.Contains("小桶") || o.Name.Contains("熔炉")
                        || o.Name.Contains("脱水") || o.Name.Contains("熏鱼"));
                    if (!isMachine) continue;
                    arr.Add(new JObject
                    {
                        ["x"] = (int)key.X, ["y"] = (int)key.Y,
                        ["name"] = o.Name ?? "",
                        ["ready"] = o.readyForHarvest.Value,
                        ["busy"] = o.heldObject.Value != null
                    });
                }
            }
            catch { }
            return arr;
        }

        private bool LadderSpawned()
        {
            try
            {
                var ms = Game1.currentLocation as StardewValley.Locations.MineShaft;
                if (ms == null) return false;
                var f = ms.GetType().GetField("ladderHasSpawned", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
                return f != null && (bool)(f.GetValue(ms) ?? false);
            }
            catch { return false; }
        }

        private Point LadderTile()
        {
            try
            {
                var ms = Game1.currentLocation as StardewValley.Locations.MineShaft;
                if (ms == null) return new Point(-1, -1);
                var t = ms.GetType();
                const System.Reflection.BindingFlags flags =
                    System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance;
                object v = null;
                foreach (var name in new[] { "tileBeneathLadder", "netTileBeneathLadder" })
                {
                    var pr = t.GetProperty(name, flags);
                    if (pr != null) { v = pr.GetValue(ms); }
                    if (v == null)
                    {
                        var f = t.GetField(name, flags);
                        if (f != null) v = f.GetValue(ms);
                    }
                    if (v != null) break;
                }
                if (v == null) return new Point(-1, -1);
                if (v is Point pt) return pt;
                var vt = v.GetType();
                int gx = -1, gy = -1;
                var px = vt.GetProperty("X");
                var py = vt.GetProperty("Y");
                if (px != null) gx = Convert.ToInt32(px.GetValue(v));
                if (py != null) gy = Convert.ToInt32(py.GetValue(v));
                if (gx < 0)
                {
                    var fx = vt.GetField("X");
                    var fy = vt.GetField("Y");
                    if (fx != null) gx = Convert.ToInt32(fx.GetValue(v));
                    if (fy != null) gy = Convert.ToInt32(fy.GetValue(v));
                }
                return new Point(gx, gy);
            }
            catch { return new Point(-1, -1); }
        }

        private string RodBaitName()
        {
            try
            {
                var rod = Game1.player?.Items?.OfType<StardewValley.Tools.FishingRod>().FirstOrDefault();
                var b = rod?.attachments?[StardewValley.Tools.FishingRod.BaitIndex];
                return b?.Name ?? "";
            }
            catch { return ""; }
        }

        private int RodBaitCount()
        {
            try
            {
                var rod = Game1.player?.Items?.OfType<StardewValley.Tools.FishingRod>().FirstOrDefault();
                var b = rod?.attachments?[StardewValley.Tools.FishingRod.BaitIndex];
                return b?.Stack ?? 0;
            }
            catch { return 0; }
        }

        private void AttachBait(bool force = false)
        {
            try
            {
                var p = Game1.player;
                var rod = p.Items.OfType<StardewValley.Tools.FishingRod>().FirstOrDefault();
                if (rod == null) { this.Monitor.Log("attach_bait: 背包里没有鱼竿", LogLevel.Warn); return; }
                var attach = rod.attachments;   // NetObjectArray<Object>，长度固定 2
                if (attach == null) { this.Monitor.Log("attach_bait: 鱼竿没有附件槽", LogLevel.Warn); return; }
                var cur = attach[StardewValley.Tools.FishingRod.BaitIndex];
                if (cur != null && cur.Stack > 0)
                {
                    if (!force)
                    {
                        this.Monitor.Log("attach_bait: 竿上已有 " + cur.Name + " x" + cur.Stack, LogLevel.Info);
                        return;
                    }
                    // 换饵：把旧饵退回背包
                    p.addItemToInventoryBool(cur, false);
                    attach[StardewValley.Tools.FishingRod.BaitIndex] = null;
                    this.Monitor.Log("attach_bait: 卸下旧饵 " + cur.Name, LogLevel.Info);
                }
                var pref = new[] { "Wild Bait", "Deluxe Bait", "Challenge Bait", "Bait" };
                StardewValley.Object pick = null;
                foreach (var want in pref)
                {
                    foreach (var it in p.Items)
                    {
                        var o = it as StardewValley.Object;
                        if (o == null || o.Name == null) continue;
                        if (o.Category == -21 && o.Name.IndexOf(want, StringComparison.OrdinalIgnoreCase) >= 0)
                        { pick = o; break; }
                    }
                    if (pick != null) break;
                }
                if (pick == null) { this.Monitor.Log("attach_bait: 背包里没有鱼饵", LogLevel.Warn); return; }
                int take = Math.Min(pick.Stack, 9999);
                var piece = (StardewValley.Object)pick.getOne();
                piece.Stack = take;
                attach[StardewValley.Tools.FishingRod.BaitIndex] = piece;
                pick.Stack -= take;
                if (pick.Stack <= 0) p.removeItemFromInventory(pick);
                this.Monitor.Log("attach_bait: 装上了 " + piece.Name + " x" + take, LogLevel.Info);
            }
            catch (Exception ex)
            {
                this.Monitor.Log("attach_bait 异常: " + ex.Message, LogLevel.Warn);
            }
        }

        private void StopFish()
        {
            this.fishingPump = false;
            this.castingPending = -1;
            this.castCooldown = 0;
            try { WhalePlayer.ModEntry.AssistFishing = false; WhalePlayer.ModEntry.AutoPlayMinigame = false; } catch { }
            try
            {
                var rd = Game1.player.Items.OfType<StardewValley.Tools.FishingRod>().FirstOrDefault();
                if (rd != null) { rd.isFishing = false; rd.isNibbling = false; rd.hit = false; }
            }
            catch { }
            try { Game1.player.UsingTool = false; Game1.player.canMove = true; } catch { }
            this.Monitor.Log("stop_fish: 已停止钓鱼", LogLevel.Info);
        }

        private void Fish()
        {
            var p = Game1.player;
            var rod = p.Items.OfType<StardewValley.Tools.FishingRod>().FirstOrDefault();
            if (rod == null) { this.Monitor.Log("fish: 没有鱼竿", LogLevel.Warn); return; }
            if (this.fishSpot.X < 0) { this.Monitor.Log("fish: 先 go_fish 找水面", LogLevel.Warn); return; }
            this.SelectTool(p, rod);
            this.Face(this.fishSpot);
            try
            {
                bool ok = rod.beginUsing(p.currentLocation, (int)this.fishSpot.X, (int)this.fishSpot.Y, p);
                this.castingPending = 750;   // 等挥杆动画结束
                this.Monitor.Log($"fish: beginUsing={ok}，等挥杆动画…", LogLevel.Info);
            }
            catch (Exception ex) { this.Monitor.Log($"fish 异常：{ex.Message}", LogLevel.Warn); }
            this.fishingPump = true;
            this.biteCountdown = 120;
            ScriptedFishing = true;
            // 2026-11-21 用户要求：小游戏要看得见（以前 NibbleBranchPatch 直接发鱼）。
            // 现在打开自动玩小游戏：BobberBar 真的弹出来，绿条自己追鱼，钓上鱼走原版结算。
            WhalePlayer.ModEntry.AssistFishing = true;
            WhalePlayer.ModEntry.AutoPlayMinigame = true;    // 2026-11-22：小游戏自动玩（绿条追鱼 + 进度补偿），失败有看门狗兜底
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
            var stockNames = new List<string>();
            foreach (var key in stock.Keys)
            {
                var item = key as Item;
                var names = new List<string>();
                try { if (!string.IsNullOrEmpty(key?.Name)) names.Add(key.Name); } catch { }
                try { if (!string.IsNullOrEmpty(item?.DisplayName)) names.Add(item.DisplayName); } catch { }
                try { if (!string.IsNullOrEmpty(item?.ItemId)) names.Add(item.ItemId); } catch { }
                try
                {
                    var d = item != null ? ItemRegistry.GetData(item.QualifiedItemId) : null;
                    if (d != null && !string.IsNullOrEmpty(d.InternalName)) names.Add(d.InternalName);   // 内部英文名（本地化前的名字）
                }
                catch { }
                if (stockNames.Count < 40) stockNames.Add(string.Join("/", names.Distinct()));
                foreach (var nm in names)
                {
                    if (string.IsNullOrEmpty(nm)) continue;
                    if (string.Equals(nm, want, StringComparison.OrdinalIgnoreCase)
                        || nm.IndexOf(want, StringComparison.OrdinalIgnoreCase) >= 0
                        || want.IndexOf(nm, StringComparison.OrdinalIgnoreCase) >= 0)
                    {
                        target = key;
                        break;
                    }
                }
                if (target != null) break;
            }
            if (target == null)
            {
                this.Monitor.Log($"shop_buy: 商店里没有「{want}」；货架有：{string.Join("、", stockNames)}", LogLevel.Warn);
                return;
            }

            var menu = new ShopMenu("SeedShop", stock, 0, "Pierre", null, null, true);
            Game1.activeClickableMenu = menu;                  // 画面里弹出商店
            this.closeMenuInTicks = -1;                        // 由 TickPurchase 决定什么时候关

            var info = stock[target];
            int unit = info?.Price ?? 0;
            int maxBuy = (info == null || info.Stock < 0) ? count : Math.Min(count, info.Stock);
            if (unit <= 0 || maxBuy <= 0) { this.Monitor.Log("shop_buy: 价格/库存异常", LogLevel.Warn); return; }
            if (Game1.player.Money < unit) { this.Monitor.Log($"shop_buy: 钱不够（{unit}g/个）", LogLevel.Warn); return; }

            // 分几次买：每 0.33 秒一小把，扣钱、进包、播收银音效（看起来像真的在点购买）
            this.buyTarget = target;
            this.buyUnit = unit;
            this.buyLeft = maxBuy;
            this.buyChunk = Math.Max(1, Math.Min(10, maxBuy / 8 + 1));
            this.buyDelayTicks = 30;
            this.buySpent = 0;
            this.buyPending = true;
            this.Monitor.Log($"shop_buy: 打开商店，准备买 {maxBuy} × {target.Name}（{unit}g/个，分批购买）", LogLevel.Info);
        }

        /// <summary>看得见的购买：每 tick 检查，分批扣钱入包，买完再关界面。</summary>
        private void TickPurchase()
        {
            if (!this.buyPending) return;
            if (!(Game1.activeClickableMenu is ShopMenu))
            {
                // 界面被外部关掉（常见：goto 看门狗 close_menu）：把剩下的直接买完，别半途而废
                var proto2 = this.buyTarget as Item;
                int remain = this.buyLeft;
                if (proto2 != null && remain > 0)
                {
                    int afford = this.buyUnit > 0 ? Game1.player.Money / this.buyUnit : remain;
                    int n2 = Math.Min(remain, Math.Max(0, afford));
                    if (n2 > 0)
                    {
                        var piece2 = proto2.getOne();
                        piece2.Stack = n2;
                        if (Game1.player.addItemToInventoryBool(piece2, false))
                        {
                            int cost2 = n2 * this.buyUnit;
                            Game1.player.Money -= cost2;
                            this.buySpent += cost2;
                            this.buyLeft -= n2;
                            this.Monitor.Log($"shop_buy: 界面被关，直接补买 {n2} × {proto2.Name}（-{cost2}g，剩 {this.buyLeft}）", LogLevel.Info);
                        }
                    }
                }
                this.buyPending = false;
                return;
            }
            if (this.buyDelayTicks > 0) { this.buyDelayTicks--; return; }
            this.buyDelayTicks = 20;

            int n = Math.Min(this.buyChunk, this.buyLeft);
            int cost = n * this.buyUnit;
            if (Game1.player.Money < cost)
            {
                this.buyPending = false;
                this.Monitor.Log($"shop_buy: 钱不够了，停止（还差 {this.buyLeft} 个）", LogLevel.Warn);
                this.closeMenuInTicks = 60;
                return;
            }
            var proto = this.buyTarget as Item;
            if (proto == null)
            {
                this.buyPending = false;
                this.closeMenuInTicks = 60;
                return;
            }
            var piece = proto.getOne();
            piece.Stack = n;
            if (!Game1.player.addItemToInventoryBool(piece, false))
            {
                this.buyPending = false;
                this.Monitor.Log("shop_buy: 背包放不下，停止购买", LogLevel.Warn);
                this.closeMenuInTicks = 60;
                return;
            }
            Game1.player.Money -= cost;
            this.buySpent += cost;
            try { Game1.playSound("purchase", 0); } catch { }
            this.buyLeft -= n;
            this.Monitor.Log($"shop_buy: 买 {n} × {proto.Name}（-{cost}g，余 {Game1.player.Money}g，还剩 {this.buyLeft}）", LogLevel.Info);
            if (this.buyLeft <= 0)
            {
                this.buyPending = false;
                this.closeMenuInTicks = 45;     // 界面再停 0.75 秒
                this.Monitor.Log($"shop_buy: 买完了，共花 {this.buySpent}g，余额 {Game1.player.Money}g", LogLevel.Info);
            }
        }

        private void Ship()
        {
            var farm = Game1.getFarm();
            var bin = farm?.getShippingBin(Game1.player);
            if (bin == null) { this.Monitor.Log("ship: 找不到出货箱", LogLevel.Warn); return; }
            int moved = 0;
            // 作物(-75/-79/-80) + 鱼(-4) + 矿物(-12) + 宝石(-2) + 动物产品(-5/-6) + 工匠品(-26)
            // 作物 -75/-79/-80；鱼 -4；矿物 -12；宝石 -2；动物产品 -5/-6；工匠品 -26；
            // 矿石/金属 -15；建材资源 -16（木/石/纤维）；垃圾 -20/-21 也一起清
            // 只卖「成品/战利品」：作物、鱼、宝石、动物产品、工匠品。
            // 矿石(-15)/建材(-16)/晶洞(-12) 留着熔炼和制作，别当废品卖掉（14:07 实测一次被卖了 781 木头）
            var shipCats = new HashSet<int> { -75, -79, -80, -4, -2, -5, -6, -26 };
            var shipped = new List<string>();
            for (int i = 0; i < Game1.player.Items.Count; i++)
            {
                if (!(Game1.player.Items[i] is StardewValley.Object obj)) continue;
                int cat = obj.Category;
                if (cat == -74) continue;   // 种子留着
                if (!shipCats.Contains(cat)) continue;
                if (shipped.Count < 8) shipped.Add($"{obj.Name}x{obj.Stack}");
                bin.AddRange(new List<Item> { obj });
                Game1.player.Items[i] = null;
                moved += obj.Stack;
            }
            this.Monitor.Log($"ship: 投入 {moved} 个（{string.Join(", ", shipped)}）", LogLevel.Info);
        }

        /// <summary>强制起床：复位躺床状态与精灵动画（避免一直躺着）。</summary>
        /// <summary>坐标跑到地图外时，把角色放回地图内的可走格（防卡死）。</summary>
        private void FixPosition()
        {
            var p = Game1.player;
            var loc = p.currentLocation;
            if (loc == null) return;
            int w = loc.Map.Layers[0].LayerWidth, h = loc.Map.Layers[0].LayerHeight;
            int tx = (int)p.Tile.X, ty = (int)p.Tile.Y;
            bool outside = tx < 0 || ty < 0 || tx >= w || ty >= h;
            bool blocked = !outside && !loc.isTilePassable(new xTile.Dimensions.Location(tx, ty), Game1.viewport);
            if (!outside && !blocked)
            {
                this.Monitor.Log($"fix_position: ({tx},{ty}) 正常，无需修正", LogLevel.Info);
                return;
            }
            // 找最近的可行格
            for (int r = 0; r < Math.Max(w, h); r++)
            {
                for (int dx = -r; dx <= r; dx++)
                {
                    for (int dy = -r; dy <= r; dy++)
                    {
                        if (Math.Abs(dx) != r && Math.Abs(dy) != r) continue;
                        int nx = Math.Clamp(tx + dx, 0, w - 1), ny = Math.Clamp(ty + dy, 0, h - 1);
                        if (loc.isTilePassable(new xTile.Dimensions.Location(nx, ny), Game1.viewport))
                        {
                            p.Position = new Vector2(nx * 64f + 32f, ny * 64f + 32f);
                            this.Monitor.Log($"fix_position: ({tx},{ty}) 在地图外/不可走 → 修正到 ({nx},{ny})", LogLevel.Warn);
                            return;
                        }
                    }
                }
            }
            this.Monitor.Log("fix_position: 找不到可行格", LogLevel.Warn);
        }

        private void WakeUp()
        {
            var p = Game1.player;
            try { p.isInBed.Value = false; } catch { }
            try { p.canMove = true; } catch { }
            try { p.UsingTool = false; } catch { }
            try { p.EndUsingTool(); } catch { }
            // 清掉可能卡住的寻路控制器（否则人会站着不动）
            try { p.controller = null; } catch { }
            // 复位钓鱼/使用工具的姿势（否则会一直保持抛竿动作）
            try
            {
                var rod = p.CurrentTool as StardewValley.Tools.FishingRod;
                if (rod != null) { rod.isFishing = false; rod.isNibbling = false; rod.hit = false; }
                foreach (var it in p.Items)
                {
                    if (it is StardewValley.Tools.FishingRod r2) { r2.isFishing = false; r2.isNibbling = false; r2.hit = false; }
                }
            }
            catch { }
            try { p.completelyStopAnimatingOrDoingAction(); } catch { }
            try
            {
                p.Sprite.StopAnimation();
                // 农夫精灵是 16x32，绝不能改成 16（会把下半身裁掉导致建模看起来丢失）
                if (p.Sprite.SpriteHeight != 32) p.Sprite.SpriteHeight = 32;
                p.Sprite.SpriteWidth = 16;
                p.Sprite.CurrentFrame = 0;
                p.FarmerSprite.StopAnimation();
                p.FarmerSprite.CurrentFrame = 0;
                p.FarmerSprite.PauseForSingleAnimation = false;
                p.faceDirection(2);
            }
            catch { }
            try
            {
                // 如果人还卡在床的位置，挪到床边
                var loc = p.currentLocation;
                var tile = p.Tile;
                if (loc != null && loc.Name.Contains("FarmHouse") && tile.X > 6 && tile.X < 12 && tile.Y > 6 && tile.Y < 11)
                {
                    p.Position = new Vector2(9 * 64f, 9 * 64f);
                }
            }
            catch { }
            this.Monitor.Log("wake: 已复位起床状态", LogLevel.Info);
        }

        private void Sleep()
        {
            try
            {
                // 不在屋里就别睡：在外面"睡"等于昏倒，每晚扣 1000g（2026-11-19 实测）
                var locName = Game1.currentLocation?.Name ?? "";
                if (locName != "FarmHouse")
                {
                    this.Monitor.Log("sleep: 当前在 " + locName + "，不在农舍里，拒绝睡觉", LogLevel.Warn);
                    return;
                }
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
            var tilledSet = new List<string>();
                var origin = p.Tile;
                string locNameForScan = loc?.Name ?? "";
                bool wholeMap = locNameForScan == "Mine" || locNameForScan.StartsWith("UndergroundMine")
                || locNameForScan == "SkullCave" || locNameForScan == "SkullCavern"
                || locNameForScan == "Farm";
                int scanR = wholeMap ? 60 : 14;
                if (wholeMap) origin = new Vector2(0, 0);
                int mapW = loc.Map.Layers[0].LayerWidth, mapH = loc.Map.Layers[0].LayerHeight;
                for (int dy = (wholeMap ? 0 : -scanR); dy <= (wholeMap ? mapH - 1 : scanR); dy++)
                {
                    for (int dx = (wholeMap ? 0 : -scanR); dx <= (wholeMap ? mapW - 1 : scanR); dx++)
                    {
                        int x = (int)origin.X + dx, y = (int)origin.Y + dy;
                        if (x < 0 || y < 0 || x >= loc.Map.Layers[0].LayerWidth || y >= loc.Map.Layers[0].LayerHeight) continue;
                        var tile = new Vector2(x, y);
                        // 矿洞梯子/竖井是 Buildings 图层的 173/174 号瓷砖
                        bool isLadderTile = false; string ladderKind = null;
                        if (wholeMap)
                        {
                            try
                            {
                                var bl = loc.Map.GetLayer("Buildings");
                                var bt = bl?.Tiles[x, y];
                                if (bt != null)
                                {
                                    int ti = bt.TileIndex;
                                    if (ti == 173) { ladderKind = "下梯子"; }
                                    else if (ti == 174) { ladderKind = "竖井"; }
                                    else if (ti == 115) { ladderKind = "上梯子"; }
                                    else if (ti == 112) { ladderKind = "电梯"; }
                                    if (ladderKind != null) isLadderTile = true;
                                }
                            }
                            catch { }
                        }
                        string crop = null; bool ready = false; int ws = -1;
                        string featureKind = null; bool featureBreak = false;
                        try
                        {
                            if (loc.terrainFeatures.TryGetValue(tile, out var tfAny) && tfAny != null)
                            {
                                if (tfAny is Tree) { featureKind = "树"; featureBreak = true; }
                                else if (tfAny is FruitTree) { featureKind = "果树"; featureBreak = true; }
                                else if (tfAny is Grass) { featureKind = "草"; featureBreak = true; }
                                else if (tfAny is GiantCrop) { featureKind = "巨型作物"; featureBreak = true; }
                            }
                        }
                        catch { }
                        string obj = null; bool breakable = false, interactable = false; int cropDaysLeft = -1;
                        if (loc.terrainFeatures.TryGetValue(tile, out var tf) && tf is HoeDirt dirt)
                        {
                            ws = dirt.state.Value;
                            if (dirt.crop != null)
                            {
                                bool dead = false;
                                try { dead = dirt.crop.dead.Value; } catch { }
                                if (dead)
                                {
                                    // 枯死作物：当作杂草，可被镰刀清掉
                                    obj = "枯死作物";
                                    breakable = true;
                                    crop = null;
                                }
                                else
                                {
                                    ready = dirt.readyForHarvest();
                                    try { crop = ItemRegistry.GetDataOrErrorItem(dirt.crop.indexOfHarvest.Value).DisplayName; } catch { crop = "crop"; }
                                    try
                                    {
                                        // 剩余成熟天数 = 各阶段总天数 - 已过阶段天数
                                        int total = 0;
                                        foreach (var pd in dirt.crop.phaseDays)
                                            if (pd > 0 && pd < 1000) total += pd;   // 跳过 99999 哨兵
                                        int passed = 0;
                                        int cur = dirt.crop.dayOfCurrentPhase.Value;
                                        for (int pi = 0; pi < cur && pi < dirt.crop.phaseDays.Count; pi++)
                                        {
                                            int pd = dirt.crop.phaseDays[pi];
                                            if (pd > 0 && pd < 1000) passed += pd;
                                        }
                                        cropDaysLeft = Math.Max(0, total - passed);
                                        if (dirt.crop.fullyGrown.Value) cropDaysLeft = 0;
                                    }
                                    catch { }
                                }
                            }
                        }
                        if (featureKind != null && obj == null) { obj = featureKind; breakable = featureBreak; }
                        if (isLadderTile)
                        {
                            if (tiles.Count < 400)
                                tiles.Add(new JObject
                                {
                                    ["x"] = x, ["y"] = y, ["crop"] = null, ["cropReady"] = false,
                                    ["waterState"] = "干地", ["obj"] = ladderKind,
                                    ["breakable"] = false, ["interactable"] = true
                                });
                            continue;
                        }
                        // 物体检测（石头/杂草/矿石/箱子等）
                        string objId = null;
                        if (loc.objects.TryGetValue(tile, out var o) && o != null)
                        {
                            obj = o.DisplayName;
                            objId = o.ItemId;
                            breakable = o.Name.Contains("Weed") || o.Name.Contains("Stone") || o.Name.Contains("Twig") || o.Type == "Litter"
                                        || o.Name.Contains("石头") || o.Name.Contains("圆石") || o.Name.Contains("矿")
                                        || o.Name.Contains("Boulder") || o.Name.Contains("Rock") || o.Name.Contains("Geode")
                                        ;   // 不再用 -9 兜底（宝箱/熔炉也是 -9）
                              if (o is Chest || o is Furniture || o.Name.Contains("箱") || o.Name.Contains("熔炉")
                                  || o.Name.Contains("Chest") || o.Name.Contains("Furnace")) breakable = false;
                            interactable = o is Chest || o is Furniture
                                        || o.Name.Contains("Ladder") || o.Name.Contains("楼梯")
                                        || o.Name.Contains("Shaft") || o.Name.Contains("洞口");
                        }
                        if (crop == null && obj == null && !interactable)
                        {
                            // 已锄过（有 HoeDirt）的空地 → 记进 tilled_tiles，种植时不必再锄
                            bool hasHoeDirt = loc.terrainFeatures.TryGetValue(tile, out var tfChk) && tfChk is HoeDirt;
                            if (hasHoeDirt && tilledSet.Count < 300) tilledSet.Add(x + "," + y);
                            // 顺便记录"可开垦的空地"，让 AI 知道哪里能锄
                            bool diggable = loc.terrainFeatures.TryGetValue(tile, out var tf2) == false
                                            && loc.doesTileHaveProperty(x, y, "Diggable", "Back") != null;
                            if (diggable && dryTiles.Count < 80)
                                dryTiles.Add(new JObject { ["x"] = x, ["y"] = y });
                            continue;
                        }
                        tiles.Add(new JObject
                        {
                            ["x"] = x, ["y"] = y, ["crop"] = crop, ["cropReady"] = ready,
                            ["waterState"] = ws, ["obj"] = obj, ["id"] = objId,
                            ["breakable"] = breakable, ["interactable"] = interactable, ["daysLeft"] = cropDaysLeft
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
                    ["event"] = Game1.CurrentEvent?.id ?? "",
                    ["festival"] = Game1.isFestival(),
                    ["door"] = new JObject { ["x"] = (int)this.doorTile.X, ["y"] = (int)this.doorTile.Y },
                    ["player"] = new JObject
                    {
                        ["name"] = p.Name,
                        ["money"] = p.Money,
                        ["stamina"] = p.Stamina,
                        ["health"] = p.health,
                        ["tile"] = new JObject { ["x"] = (int)p.Tile.X, ["y"] = (int)p.Tile.Y },
                        ["inventory"] = new JArray(p.Items.Where(i => i != null).Select(i => (object)(i.Stack > 1 ? i.Name + " x" + i.Stack : i.Name)))
                    },
                    ["mine_level"] = Game1.CurrentMineLevel,
                ["machines"] = MachinesJson(),
                ["ladder_spawned"] = LadderSpawned(),
                ["ladder_x"] = LadderTile().X,
                ["ladder_y"] = LadderTile().Y,
                ["rod_bait"] = RodBaitName(),
                ["rod_bait_count"] = RodBaitCount(),
                ["tiles"] = tiles,
                    ["dry_tiles"] = dryTiles,
                    ["water_tiles"] = waterTiles,
                    ["tilled_tiles"] = new JArray(tilledSet.Cast<object>().ToArray())
                };
                // 直接覆盖写（FileShare.ReadWrite）：之前 tmp+Move 在读取方持有句柄时会 Access denied
                var jsonText = state.ToString();
                using (var fs2 = new FileStream(this.statePath, FileMode.Create, FileAccess.Write, FileShare.ReadWrite))
                using (var sw = new StreamWriter(fs2, new System.Text.UTF8Encoding(false)))
                {
                    sw.Write(jsonText);
                }
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"状态写入失败：{ex.Message}", LogLevel.Trace);
            }
        }
    }
}
