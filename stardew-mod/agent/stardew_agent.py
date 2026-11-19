#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""星露谷 AI 代理（玩家本体 · 全可见操作版）

配套 mod：Mods/WhalePlayer（不是同伴那套 bridge）
  - 状态：Mods/WhalePlayer/player_state.json
  - 订单：Mods/WhalePlayer/orders/*.json

用法：
  python stardew_agent.py --dry                 # 只读状态 + 出计划
  python stardew_agent.py --once                # 执行一轮
  python stardew_agent.py --loop --rounds 20
  python stardew_agent.py --goal "把这片地清出来"

环境变量：
  STARDEW_LLM_BASE / _MODEL / _KEY / _THINKING
  STARDEW_PLAYER_DIR  默认 D:\\steam\\steamapps\\common\\Stardew Valley\\Mods\\WhalePlayer
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

DEFAULT_PLAYER_DIR = r"D:\steam\steamapps\common\Stardew Valley\Mods\WhalePlayer"
MAX_STACK = 9999   # BigStackPatch 把堆叠上限提到 9999


def env(name, default):
    v = os.environ.get(name)
    return v if v else default


PLAYER_DIR = env("STARDEW_PLAYER_DIR", DEFAULT_PLAYER_DIR)
STATE_FILE = os.path.join(PLAYER_DIR, "player_state.json")
ORDER_DIR = os.path.join(PLAYER_DIR, "orders")
LLM_BASE = env("STARDEW_LLM_BASE", "http://127.0.0.1:11434/v1")
LLM_MODEL = env("STARDEW_LLM_MODEL", "qwen25-7b-local")
LLM_KEY = env("STARDEW_LLM_KEY", "ollama")
TOKEN_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stardew_tokens.jsonl")

_tokens = {"calls": 0, "prompt": 0, "completion": 0, "total": 0}
_seq = 0


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------- 与 mod 通信 ----------------
def read_state():
    if not os.path.exists(STATE_FILE):
        raise SystemExit(f"找不到 {STATE_FILE}（游戏没开 / WhalePlayer 没加载？）")
    for _ in range(5):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return normalize(json.load(f))
        except (json.JSONDecodeError, OSError):
            time.sleep(0.2)
    raise SystemExit("player_state.json 读取失败")


ACTIVITY_FILE = r"D:\live\logs\ai_status.json"
ACTION_CN = {
    "walk_to": "走路", "use_tool": "用工具", "interact": "互动", "harvest": "收割",
    "plant": "播种", "warp_to": "传送", "sleep": "睡觉", "save": "存档",
    "close_menu": "关菜单", "ensure_tools": "整理工具", "stash_all": "归箱",
    "ship": "出货", "fish": "钓鱼", "go_fish": "去钓鱼点", "stop_fish": "收竿",
    "unstick": "脱困", "fix_position": "校正坐标", "tidy": "整理", "scan_all": "扫描",
}
_activity = {"text": "待机", "order": "", "at": 0.0}


def _write_activity():
    try:
        os.makedirs(os.path.dirname(ACTIVITY_FILE), exist_ok=True)
        tmp = ACTIVITY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"activity": _activity["text"], "order": _activity["order"],
                       "at": _activity["at"]}, f, ensure_ascii=False)
        os.replace(tmp, ACTIVITY_FILE)
    except Exception:
        pass


def set_activity(text):
    """给 OBS 状态面板用的「最近在干什么」（人类可读的高层动作）。"""
    _activity["text"] = str(text)
    _activity["at"] = time.time()
    _write_activity()


def send_order(action):
    global _seq
    os.makedirs(ORDER_DIR, exist_ok=True)
    _seq += 1
    name = f"{int(time.time() * 1000):013d}-{_seq:03d}.json"
    tmp = os.path.join(ORDER_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(action, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(ORDER_DIR, name))
    # 给 OBS 面板记一条「最近指令」：动作 + 关键参数
    try:
        a = str(action.get("actionType") or "")
        label = ACTION_CN.get(a, a)
        extra = ""
        if a == "use_tool":
            extra = " %s" % (action.get("tool") or "")
        elif a in ("warp_to", "goto"):
            extra = " → %s" % (action.get("location") or "")
        elif a == "walk_to":
            extra = " (%s,%s)" % (action.get("x"), action.get("y"))
        _activity["order"] = (label + extra).strip()
        _activity["at"] = time.time()
        _write_activity()
    except Exception:
        pass
    return action


# ---------------- LLM ----------------
def llm_chat(messages, temperature=0.3, max_tokens=600):
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    # DeepSeek 默认开思考模式（按输出计费、还会吃光 max_tokens）→ 自动关
    thinking = env("STARDEW_LLM_THINKING", "auto").lower()
    if thinking == "auto":
        thinking = "disabled" if "deepseek" in LLM_BASE.lower() else ""
    if thinking in ("disabled", "enabled"):
        payload["thinking"] = {"type": thinking}
    req = urllib.request.Request(
        LLM_BASE.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LLM_KEY}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read().decode("utf-8"))
    usage = data.get("usage") or {}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    _tokens["calls"] += 1
    _tokens["prompt"] += pt
    _tokens["completion"] += ct
    _tokens["total"] += pt + ct
    try:
        with open(TOKEN_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "model": LLM_MODEL, "prompt": pt,
                                "completion": ct, "total": pt + ct}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return data["choices"][0]["message"]["content"]


# ---------------- 归一化 ----------------
SEASON_KEY = {"春": "spring", "夏": "summer", "秋": "fall", "冬": "winter"}
SEASON_CN = {"spring": "春", "summer": "夏", "fall": "秋", "winter": "冬",
             "春": "春", "夏": "夏", "秋": "秋", "冬": "冬"}
WATER_TEXT = {-1: "干地", 0: "未浇", 1: "已浇"}

CROP_EN = {
    "防风草": "Parsnip", "大蒜": "Garlic", "土豆": "Potato", "甘蓝菜": "Kale", "花椰菜": "Cauliflower",
    "绿豆": "Green Bean", "蓝爵": "Blue Jazz", "郁金香": "Tulip", "草莓": "Strawberry", "大黄": "Rhubarb",
    "蓝莓": "Blueberry", "甜瓜": "Melon", "杨桃": "Starfruit", "红叶卷心菜": "Red Cabbage",
    "啤酒花": "Hops", "辣椒": "Hot Pepper", "西红柿": "Tomato", "小萝卜": "Radish", "虞美人": "Poppy",
    "小麦": "Wheat", "玉米": "Corn", "蔓越莓": "Cranberry", "南瓜": "Pumpkin", "洋蓟": "Artichoke",
    "甜菜": "Beet", "茄子": "Eggplant", "葡萄": "Grape", "苋菜": "Amaranth", "山药": "Yam",
    "小白菜": "Bok Choy", "玫瑰仙子": "Fairy Rose",
}


def normalize(raw):
    s = dict(raw or {})
    s["season"] = SEASON_CN.get(str(s.get("season") or "").lower(), str(s.get("season") or ""))
    t = s.get("time")
    if not isinstance(t, str):
        try:
            n = int(t)
            s["time"] = f"{n // 100:02d}:{n % 100:02d}"
        except Exception:
            s["time"] = str(t)
    p = dict(s.get("player") or {})
    p["inventory"] = [str(x) for x in (p.get("inventory") or [])]
    s["player"] = p
    tiles = []
    for t in s.get("tiles") or []:
        t = dict(t)
        ws = t.get("waterState")
        if isinstance(ws, int):
            t["waterState"] = WATER_TEXT.get(ws, "干地")
        tiles.append(t)
    s["tiles"] = tiles
    return s

def _select_tiles(s, cap=1200):
    """挑出真正有用的格子。

    - 矿洞/矿井：全部保留（石头、梯子到处都有用）
    - 农场等：所有作物 + 当前农田区域内的格
    """
    all_t = s.get("tiles") or []
    loc = str(s.get("location") or "")
    if loc == "Mine" or loc.startswith("UndergroundMine"):
        return all_t[:cap]
    try:
        plot = current_plot()
    except Exception:
        plot = (0, 0, 0, 0)
    x1, y1, x2, y2 = plot

    def in_plot(t):
        return x1 <= t.get("x", -1) <= x2 and y1 <= t.get("y", -1) <= y2

    keep = []
    seen = set()
    for t in all_t:
        if t.get("crop"):
            k = (t.get("x"), t.get("y"))
            if k not in seen:
                keep.append(t)
                seen.add(k)
    for t in all_t:
        if not in_plot(t):
            continue
        k = (t.get("x"), t.get("y"))
        if k in seen:
            continue
        keep.append(t)
        seen.add(k)
        if len(keep) >= cap:
            break
    return keep



def summarize(s):
    p = s.get("player") or {}
    return {
        "door": s.get("door"),
        "event": s.get("event"),
        "festival": s.get("festival"),
        "time": s.get("time"), "day": s.get("day"), "season": s.get("season"),
        "weather": s.get("weather"), "location": s.get("location"),
        "money": p.get("money"), "stamina": p.get("stamina"),
        "inventory": p.get("inventory") or [],
        "tile": p.get("tile") or {},
        "tiles": _select_tiles(s),
        "dry_tiles": (s.get("dry_tiles") or [])[:24],
    }


# ---------------- 作物利润表 ----------------
CROPS = {
    "spring": [
        ("防风草", 20, 4, 35, 0, 3.8), ("大蒜", 40, 4, 60, 0, 5.0), ("土豆", 50, 6, 80, 0, 5.0),
        ("甘蓝菜", 70, 6, 110, 0, 6.7), ("花椰菜", 80, 12, 175, 0, 7.9), ("绿豆", 60, 10, 40, 3, 7.9),
        ("蓝爵", 30, 7, 50, 0, 2.9), ("郁金香", 20, 6, 30, 0, 1.7), ("草莓", 100, 8, 120, 4, 20.8),
        ("大黄", 100, 13, 220, 0, 9.2),
    ],
    "summer": [
        ("蓝莓", 80, 13, 50, 4, 20.8), ("甜瓜", 80, 12, 250, 0, 14.2), ("杨桃", 400, 13, 750, 0, 26.9),
        ("红叶卷心菜", 100, 9, 300, 0, 22.2), ("啤酒花", 60, 11, 25, 1, 13.0), ("辣椒", 40, 5, 40, 3, 10.0),
        ("西红柿", 50, 11, 60, 4, 9.0), ("小萝卜", 40, 6, 90, 0, 8.3), ("虞美人", 100, 7, 140, 0, 5.7),
        ("小麦", 10, 4, 25, 0, 3.8), ("玉米", 150, 14, 50, 4, 3.5),
    ],
    "fall": [
        ("蔓越莓", 240, 7, 75, 5, 18.9), ("南瓜", 100, 13, 320, 0, 16.9), ("洋蓟", 30, 8, 160, 0, 16.3),
        ("甜菜", 20, 6, 100, 0, 13.3), ("茄子", 20, 5, 60, 5, 13.0), ("葡萄", 60, 10, 80, 3, 11.7),
        ("苋菜", 70, 7, 150, 0, 11.4), ("山药", 60, 10, 160, 0, 10.0), ("小白菜", 50, 4, 80, 0, 7.5),
        ("玫瑰仙子", 200, 12, 290, 0, 7.5),
    ],
}



# ---------------- 季节作物选择（确定性逻辑） ----------------
# 中文作物名 → 英文种子名（商店按英文名买，种植也按英文名匹配）
# 皮埃尔（SeedShop）不卖的种子：杨桃(沙漠)、上古种子、仙人掌、芋头、菠萝、宝石甜莓等
NOT_IN_PIERRE = ("Starfruit Seeds", "Ancient Seeds", "Cactus Seeds", "Taro Tuber",
                 "Pineapple Seeds", "Rare Seed", "Rice Shoot")

SEED_EN = {
    "防风草": "Parsnip Seeds", "大蒜": "Garlic Seeds", "土豆": "Potato Seeds",
    "甘蓝菜": "Kale Seeds", "花椰菜": "Cauliflower Seeds", "绿豆": "Bean Starter",
    "蓝爵": "Jazz Seeds", "郁金香": "Tulip Bulb", "草莓": "Strawberry Seeds",
    "大黄": "Rhubarb Seeds",
    "蓝莓": "Blueberry Seeds", "甜瓜": "Melon Seeds", "杨桃": "Starfruit Seeds",
    "红叶卷心菜": "Red Cabbage Seeds", "啤酒花": "Hops Starter", "辣椒": "Hot Pepper Seeds",
    "西红柿": "Tomato Seeds", "小萝卜": "Radish Seeds", "虞美人": "Poppy Seeds",
    "小麦": "Wheat Seeds", "玉米": "Corn Seeds",
    "蔓越莓": "Cranberry Seeds", "南瓜": "Pumpkin Seeds", "洋蓟": "Artichoke Seeds",
    "甜菜": "Beet Seeds", "茄子": "Eggplant Seeds", "葡萄": "Grape Starter",
    "苋菜": "Amaranth Seeds", "山药": "Yam Seeds", "小白菜": "Bok Choy Seeds",
    "玫瑰仙子": "Fairy Rose Seeds",
}


def current_season_day():
    """返回 (季节键, 天数)。季节键为 spring/summer/fall/winter。"""
    st = summarize(read_state())
    season_cn = str(st.get("season") or "")
    key = SEASON_KEY.get(season_cn, season_cn.lower() if season_cn else "")
    if key not in ("spring", "summer", "fall", "winter"):
        # 兜底：按中文反查
        inv = {v: k for k, v in SEASON_KEY.items()}
        key = inv.get(season_cn, "spring")
    try:
        day = int(st.get("day") or 1)
    except Exception:
        day = 1
    return key, day


# 利润字段修正：原表第 6 列全是 0，导致选种排序失效（2026-11-18 修）
def _recompute_profit():
    for season, rows in list(CROPS.items()):
        fixed = []
        for row in rows:
            name, cost, days, price, regrow, _old = row[:6]
            days = max(1, int(days))
            # 一次收多个的作物（蔓越莓 2、蓝莓 3、咖啡 4）按产量折算
            yield_n = {"蔓越莓": 2, "蓝莓": 3, "咖啡豆": 4}.get(name, 1)
            if regrow and int(regrow) > 0:
                n = 1 + max(0, (28 - days) // max(1, int(regrow)))
                profit = (price * yield_n * n - cost) / float(days + (n - 1) * int(regrow))
            else:
                profit = (price * yield_n - cost) / float(days)
            fixed.append((name, cost, days, price, regrow, round(profit, 1)))
        CROPS[season] = fixed


_recompute_profit()


def pick_crops(season=None, day=None, top=3):
    """按季节+剩余天数挑作物。返回 [(中文名, 英文种子名, 成熟天, 再收间隔, 利润/天)]。

    规则：成熟天数 <= 本季剩余天数，按利润排序。
    """
    if season is None or day is None:
        season, day = current_season_day()
    rows = CROPS.get(season) or []
    if not rows:
        return []
    remaining = max(0, 28 - day + 1)
    out = []
    for (name, cost, days, price, regrow, profit) in rows:
        if days > remaining:
            continue
        en = SEED_EN.get(name)
        if not en:
            continue
        out.append((name, en, days, regrow, profit))
    out.sort(key=lambda r: -r[4])
    return out[:top]


def plan_season_text():
    season, day = current_season_day()
    remain = max(0, 28 - day + 1)
    picks = pick_crops(season, day)
    if not picks:
        return "本季剩余 %d 天，没有能成熟的作物" % remain
    return "第%d天（%s季，剩 %d 天）推荐：%s" % (
        day, SEASON_CN.get(season, season), remain,
        "、".join("%s(%d天,≈%sg/天)" % (n, d, g) for (n, en, d, r, g) in picks))



TREASURE_KEEP = ("Seed",)   # 背包里种子名包含这些就不收箱


def count_seeds(seed_en, st=None):
    """统计背包里某种种子的数量（inventory 形如 'Parsnip Seeds x82'）。"""
    if st is None:
        st = summarize(read_state())
    total = 0
    for item in (st.get("inventory") or []):
        name = item
        n = 1
        if " x" in item:
            name, _, num = item.rpartition(" x")
            try:
                n = int(num)
            except Exception:
                n = 1
        if seed_en.lower() in name.lower():
            total += n
    return total


def ensure_seeds_buy(seed_en, need, log_fn=None):
    """确保背包里有 need 个种子；不够就远程买。返回实际可种数量。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    have = count_seeds(seed_en)
    if have >= need:
        return have
    buy_n = need - have
    ok_shop, reasons, wd = shop_state()
    if not ok_shop:
        # 只有时间没到9点才等；周三/节日关门直接跳过（别睡掉一整天）
        if any("周三" in r or "节日" in r for r in reasons):
            _log("  [买种] 商店今天关门（%s），跳过购买，用现有种子" % "、".join(reasons))
            return have
        _log("  [买种] 商店未开门（%s），稍等…" % "、".join(reasons))
        if not wait_for_shop(log_fn=log_fn):
            _log("  [买种] 等不到营业时间，跳过购买")
            return have
    _log("  [买种] %s 现有 %d，需要 %d → 买 %d" % (seed_en, have, need, buy_n))
    send_order({"actionType": "shop_buy", "item": seed_en, "count": buy_n, "remote": True})
    time.sleep(3)
    send_order({"actionType": "close_menu"})
    time.sleep(1.5)
    return count_seeds(seed_en)


PLOT = (65, 18, 74, 37)      # 兜底
PLOT_CENTER_FILE = r"D:\live\logs\.plot_center"
WATER_CACHE_FILE = r"D:\live\logs\.water_tiles"
PLOT_RECT_FILE = r"D:\live\logs\.plot_rect"


def _load_plot_center():
    try:
        with open(PLOT_CENTER_FILE, encoding="utf-8") as f:
            a, b = f.read().strip().split(",")
            return int(a), int(b)
    except Exception:
        return None


def remember_plot_center(x=None, y=None, log_fn=None):
    """记住农田中心点（不起时用当前角色位置）。"""
    if x is None or y is None:
        st = summarize(read_state())
        t = st.get("tile") or {}
        x, y = int(t.get("x", 0)), int(t.get("y", 0))
    try:
        os.makedirs(os.path.dirname(PLOT_CENTER_FILE), exist_ok=True)
        with open(PLOT_CENTER_FILE, "w", encoding="utf-8") as f:
            f.write("%d,%d" % (x, y))
    except Exception:
        pass
    if log_fn:
        log_fn("  [农田] 中心点已记住: (%d,%d)" % (x, y))
    return x, y



def water_cache(log_fn=None, refresh=False):
    """水面格集合：调用 scan_water 扫全图，解析日志缓存到文件。只用于种地跳过水格。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    if not refresh:
        try:
            with open(WATER_CACHE_FILE, encoding="utf-8") as f:
                data = f.read().strip()
            if data:
                out = set()
                for tok in data.split():
                    if "," in tok:
                        a2, b2 = tok.split(",")
                        out.add((int(a2), int(b2)))
                return out
        except Exception:
            pass
    # 扫一次全图水面
    import glob
    logp = os.path.join(os.environ.get("APPDATA", ""), r"StardewValley\ErrorLogs\SMAPI-latest.txt")
    before = 0
    try:
        before = os.path.getsize(logp)
    except Exception:
        pass
    send_order({"actionType": "scan_water"})
    time.sleep(3)
    out = set()
    try:
        with open(logp, encoding="utf-8", errors="replace") as f:
            f.seek(before)
            for line in f:
                if "WATER " in line and ":" in line:
                    payload = line.split("WATER ", 1)[1]
                    payload = payload.split(":", 1)[1] if ":" in payload else ""
                    for tok in payload.split():
                        if "," in tok:
                            a2, b2 = tok.split(",")
                            try:
                                out.add((int(a2), int(b2)))
                            except Exception:
                                pass
    except Exception:
        pass
    if not out:
        # 退化：用 state 里的 water_tiles
        try:
            st = summarize(read_state())
            for t in (st.get("water_tiles") or []):
                out.add((int(t["x"]), int(t["y"])))
        except Exception:
            pass
    try:
        os.makedirs(os.path.dirname(WATER_CACHE_FILE), exist_ok=True)
        with open(WATER_CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(" ".join("%d,%d" % (x, y) for (x, y) in sorted(out)))
    except Exception:
        pass
    if log_fn:
        log_fn("  [水面] 缓存 %d 个水格" % len(out))
    return out

def is_water(x, y, cache=None):
    c = cache if cache is not None else water_cache()
    return (x, y) in c



def remember_plot_rect(x1, y1, x2, y2, log_fn=None):
    """记住固定播种矩形区域（后续所有播种都限定在这里）。"""
    try:
        os.makedirs(os.path.dirname(PLOT_RECT_FILE), exist_ok=True)
        with open(PLOT_RECT_FILE, "w", encoding="utf-8") as f:
            f.write("%d,%d,%d,%d" % (x1, y1, x2, y2))
    except Exception:
        pass
    if log_fn:
        log_fn("  [农田] 固定播种区域已记住: (%d,%d)-(%d,%d)" % (x1, y1, x2, y2))
    return (x1, y1, x2, y2)


def plot_rect():
    """读取固定播种矩形；没有返回 None。"""
    try:
        with open(PLOT_RECT_FILE, encoding="utf-8") as f:
            parts = f.read().strip().split(",")
            if len(parts) == 4:
                return tuple(int(v) for v in parts)
    except Exception:
        pass
    return None



def current_plot(width=30, height=20):
    """农田区域：①固定矩形 > ②记住的中心点 > ③当前角色位置。"""
    rect = plot_rect()
    if rect is not None:
        return rect
    c = _load_plot_center()
    if c is None:
        c = remember_plot_center()
    cx, cy = c
    x1 = cx - width // 2
    y1 = cy - height // 2
    return (x1, y1, x1 + width - 1, y1 + height - 1)


def _in_plot(x, y, plot=None):
    x1, y1, x2, y2 = plot or current_plot()
    return x1 <= x <= x2 and y1 <= y <= y2


def _go_near(x, y, log_fn=None, tries=8, wait=1.2):
    """走到目标附近（相邻即可）。tries/wait 可调，种植用更短的等待。"""
    try:
        st = summarize(read_state())
    except Exception:
        return False
    px, py = (st.get("tile") or {}).get("x", -99), (st.get("tile") or {}).get("y", -99)
    if abs(px - x) + abs(py - y) <= 2:
        return True
    send_order({"actionType": "walk_to", "x": x, "y": y})
    for _ in range(tries):
        time.sleep(wait)
        st = summarize(read_state())
        px, py = (st.get("tile") or {}).get("x", -99), (st.get("tile") or {}).get("y", -99)
        if abs(px - x) + abs(py - y) <= 2:
            return True
    return False


def clear_plot(log_fn=None, max_swings=400, budget_sec=600, face=1):
    """清理农田里的树/草/石头。

    2026-11-18 改：
      - 上限从 120 刀提到 400 刀 + 600 秒预算，不再「清一半就开种」；
      - 每刀都挑**离角色最近的**障碍（原来按行排序，来回横穿农田特别慢）；
      - 挥刀后只等 0.55 秒（工具 0.33 秒生效），走位用更快的参数。
    """
    def _log(m):
        if log_fn:
            log_fn(m)
    SKIP = ("宝箱", "箱", "熔炉", "木桶", "床", "桌")
    t0 = time.time()

    def _targets():
        st = summarize(read_state())
        return [t for t in (st.get("tiles") or [])
                if _in_plot(t["x"], t["y"]) and t.get("breakable")
                and not t.get("interactable")
                and not any(k in str(t.get("obj") or "") for k in SKIP)]

    def _pos():
        t = (summarize(read_state()).get("tile") or {})
        return t.get("x", -99), t.get("y", -99)

    targets = _targets()
    if not targets:
        _log("  [清障] 农田里没有障碍")
        return 0
    total0 = len(targets)
    _log("  [清障] 农田里有 %d 处障碍，开始范围清理（上限 %d 刀 / %d 秒）"
         % (total0, max_swings, budget_sec))
    swings = 0
    fails = set()
    while swings < max_swings and (time.time() - t0) < budget_sec:
        targets = [t for t in _targets() if (t["x"], t["y"]) not in fails]
        if not targets:
            break
        px, py = _pos()
        # 最近邻优先：少走冤枉路
        targets.sort(key=lambda t: abs(t["x"] - px) + abs(t["y"] - py))
        t = targets[0]
        x, y, name = t["x"], t["y"], str(t.get("obj") or "")
        if "树" in name or "果树" in name or "嫩枝" in name or "树枝" in name or "Twig" in name:
            tool, max_swing = "axe", 8
        elif ("草" in name or "杂草" in name or "Weed" in name
              or "枯死" in name or "枯萎" in name):
            tool, max_swing = "scythe", 1      # 镰刀范围清（含枯死作物），1 刀就够
        else:
            tool, max_swing = "pickaxe", 3
        if not _go_near(x, y, log_fn, tries=5, wait=0.7):
            fails.add((x, y))
            continue
        before_n = len(_targets())
        for _ in range(max_swing):
            send_order({"actionType": "use_tool", "tool": tool, "x": x, "y": y,
                        "face": face if tool == "scythe" else -1})
            time.sleep(0.55)
            swings += 1
            if not any(z["x"] == x and z["y"] == y for z in _targets()):
                break
        after_n = len(_targets())
        cleared_now = before_n - after_n
        if cleared_now > 0:
            _log("    挥 %s @(%d,%d) → 清了 %d 处（剩 %d）" % (tool, x, y, cleared_now, after_n))
        else:
            fails.add((x, y))
    left = _targets()
    cost = time.time() - t0
    _log("  [清障] 共挥 %d 刀、%.1f 秒，清掉 %d/%d 处，剩 %d 处"
         % (swings, cost, total0 - len(left), total0, len(left)))
    return swings



def harvest_with_scythe(log_fn=None, max_rounds=300, face=1, band=3):
    """铱镰刀 3x3 蛇形收割：主扫一遍 + 针对性补漏。

    走位规则：
      - 按固定农田区划 3 行一带，带内每 3 列一站，一刀覆盖 3x3；
      - 扫到右侧尽头**不回左侧**，下一条带从右往左；到左侧尽头同理反向，整体 S 形；
      - 扫完再回头**只补漏掉的那几株**（最多 3 轮，不整片重扫）。
    """
    def _log(m):
        if log_fn:
            log_fn(m)

    def _ready():
        st = summarize(read_state())
        return {(t["x"], t["y"]) for t in (st.get("tiles") or [])
                if t.get("cropReady") and t.get("crop")}

    def _pos():
        t = summarize(read_state()).get("tile") or {}
        return t.get("x", -99), t.get("y", -99)

    ready = _ready()
    if not ready:
        _log("  [收割] 没有成熟作物")
        return 0

    # 优先用固定农田区规划带，保证每一行/列都被带覆盖到
    rect = plot_rect()
    if rect:
        x0, y0, x1, y1 = rect
    else:
        xs = [p[0] for p in ready]
        ys = [p[1] for p in ready]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

    _log("  [收割] 可收 %d 株；农田 x%d~%d y%d~%d，3x3 蛇形扫一遍 + 补漏"
         % (len(ready), x0, x1, y0, y1))
    swings = 0
    for bi, by in enumerate(range(y0, y1 + 1, band)):
        left_to_right = (bi % 2 == 0)
        cols = list(range(x0, x1 + 1, band))
        if not left_to_right:
            cols.reverse()                     # 上一条带扫到右边 → 这条从右往左
        cy = min(by + band // 2, y1)
        for bx in cols:
            cx = min(bx + band // 2, x1)
            tx = min(bx + band - 1, x1) if left_to_right else bx
            direction = 1 if left_to_right else 3
            if not _go_near(cx, cy, log_fn):
                continue
            send_order({"actionType": "use_tool", "tool": "scythe",
                        "x": tx, "y": cy, "face": direction})
            swings += 1
            time.sleep(0.6)
        _log("    y%d~%d 扫完（累计 %d 刀）" % (by, min(by + band - 1, y1), swings))

    # 补漏：主扫只跑一遍，田块边缘总会漏几株；这里只针对「剩下的点」补刀，
    # 不做整片重扫（2026-11-18 用户要求加回来）。
    got = max(0, len(ready) - len(_ready()))
    for rnd in range(1, 4):
        left_pts = _ready()
        if not left_pts:
            break
        _log("  [收割] 补漏第%d轮：剩 %d 株" % (rnd, len(left_pts)))
        cur = _pos()
        for (x, y) in sorted(left_pts, key=lambda p: abs(p[0] - cur[0]) + abs(p[1] - cur[1])):
            if (x, y) not in _ready():
                continue
            if not _go_near(x, y, log_fn, tries=4, wait=0.6):
                continue
            before_n = len(_ready())
            send_order({"actionType": "use_tool", "tool": "scythe", "x": x, "y": y, "face": 1})
            swings += 1
            time.sleep(0.6)
            after_n = len(_ready())
            got += max(0, before_n - after_n)

    left = len(_ready())
    got = max(got, 0)
    _log("  [收割] 共挥 %d 刀，收 %d 株（剩 %d 株）" % (swings, got, left))
    return got



def skip_days(n=1, log_fn=None):
    """连续跳 N 天（一天一次睡觉，走完整的 newDay，作物才会真的长）。

    和「一天一天手动睡」的区别：一次调用推进 N 天，每天确认天数真的变了，
    跳完打印一次汇总，中间不刷屏。
    """
    def _log(m):
        if log_fn:
            log_fn(m)
    done = 0
    for i in range(int(n)):
        try:
            before = summarize(read_state())
        except Exception as e:
            _log("  [跳天] 读状态失败：%s" % e)
            break
        d0 = before.get("day")
        try:
            go_home_sleep(log_fn=None)
        except Exception as e:
            _log("  [跳天] 睡觉异常：%s" % e)
        ok = False
        for _ in range(25):
            time.sleep(1)
            try:
                s = summarize(read_state())
            except Exception:
                continue
            if s.get("day") != d0:
                ok = True
                break
        if not ok:
            _log("  [跳天] 第 %d 天卡住了，停止" % (i + 1))
            break
        done += 1
        if done % 5 == 0 or i == int(n) - 1:
            s = summarize(read_state())
            _log("  [跳天] %d/%d → %s季第%s天 %s"
                 % (done, n, s.get("season"), s.get("day"), s.get("time")))
    return done


def count_ready(st=None):
    """当前有多少株成熟可收（必须在农场，屋内状态里没有 tiles）。"""
    s = st or summarize(read_state())
    return len([t for t in (s.get("tiles") or []) if t.get("cropReady") and t.get("crop")])


def skip_until_ready(max_days=20, min_ready=1, log_fn=None):
    """一直跳天直到有 min_ready 株成熟（最多 max_days 天），返回实际跳了几天。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    for i in range(int(max_days)):
        try:
            cur = str(summarize(read_state()).get("location"))
            if cur != "Farm":
                # 走路过去，不闪现
                if cur.startswith("UndergroundMine") or cur == "Mine":
                    return_from_mine(log_fn=log_fn)
                else:
                    travel_to("Farm", hop_timeout=240, log_fn=log_fn)
        except Exception:
            pass
        n = count_ready()
        if n >= min_ready:
            _log("  [跳天] 已有 %d 株成熟，停止跳天（已跳 %d 天）" % (n, i))
            return i
        skip_days(1, log_fn=log_fn)
    return int(max_days)


def plant_window_advice(season=None, day=None):
    """给出「什么时候该种什么」的建议文本（用于日志和 LLM 提示）。"""
    if season is None or day is None:
        season, day = current_season_day()
    remain = max(0, 28 - day + 1)
    picks = pick_crops(season, day, top=4)
    if not picks:
        return "本季剩 %d 天，没有能在季末前成熟的作物 → 不再播种，只做收获/清理" % remain
    parts = ["本季剩 %d 天" % remain]
    for (name, en, days, regrow, profit) in picks:
        parts.append("%s(熟%d天%s,≈%sg/天)" % (name, days, "，可连收" if regrow else "", profit))
    parts.append("→ 选利润最高且成熟期够的那个")
    return "；".join(parts)


def plot_area():
    """固定农田区的格子数（没设置返回 0）。"""
    r = plot_rect()
    if not r:
        return 0
    x1, y1, x2, y2 = r
    return (x2 - x1 + 1) * (y2 - y1 + 1)


def harvest_and_replant(log_fn=None, max_n=None, clear=False):
    """收完立刻续种：先收割（含补漏），再按季节把空地补满。返回 (收割数, 播种数)。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    # 不在农场就先走过去（走路，不闪现）
    try:
        cur = str(summarize(read_state()).get("location"))
        if cur != "Farm":
            if cur.startswith("UndergroundMine") or cur == "Mine":
                return_from_mine(log_fn=log_fn)
            else:
                travel_to("Farm", hop_timeout=240, log_fn=log_fn)
    except Exception:
        pass
    got = harvest_with_scythe(log_fn=log_fn)
    # 收完先塞进加工设备（腌菜桶/小桶），别直接归箱
    try:
        for machine in ("Preserves Jar", "Keg", "Dehydrator"):
            send_order({"actionType": "load_machines", "machine": machine, "times": 99})
            time.sleep(2.5)
    except Exception:
        pass
    n = plant_best_crops(max_n or plot_area() or 60, log_fn=log_fn, clear=clear)
    _log("  [续种] 收 %d 株 → 补种 %d 株" % (got, n))
    return got, n


def season_day_one_cleanup(log_fn=None):
    """春/夏/秋 第 1 天：清理耕地区域的杂草、过期作物、石头、树。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    season, day = current_season_day()
    if int(day) != 1 or season == "winter":
        return 0
    _log("  [换季清理] %s季第1天，清理耕地区域（杂草/过期作物/石头/树）" % SEASON_CN.get(season, season))
    try:
        if str(summarize(read_state()).get("location")) != "Farm":
            travel_to("Farm", hop_timeout=200, log_fn=log_fn)
    except Exception as e:
        _log("  [换季清理] 去农场失败：%s" % e)
    return clear_plot(log_fn=log_fn)


MEMORY_FILE = r"D:\live\logs\farm_memory.json"
_memory_cache = None


def load_memory():
    """农田记忆：下次该几天后检查、种的什么、下个活动是钓鱼还是挖矿。"""
    global _memory_cache
    if _memory_cache is not None:
        return _memory_cache
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            _memory_cache = json.load(f)
    except Exception:
        _memory_cache = {}
    return _memory_cache


def save_memory(**kw):
    global _memory_cache
    m = dict(load_memory())
    m.update(kw)
    _memory_cache = m
    try:
        os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return m


def crop_meta(name, season=None):
    """从作物表取 (成熟天数, 连收间隔)。"""
    rows = []
    if season:
        rows = CROPS.get(season) or []
    if not rows:
        for s_rows in CROPS.values():
            rows = list(rows) + list(s_rows)
    for row in rows:
        if row[0] == name:
            return int(row[2]), int(row[4])
    return 4, 0


def farm_due(log_fn=None):
    """今天要不要检查农田。返回 (是否要查, 还要等几天)。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    m = load_memory()
    season, day = current_season_day()
    if m.get("season") != season:
        _log("  [农田记忆] 换季了（记忆是 %s，现在 %s），必须检查" % (m.get("season"), season))
        return True, 0
    nxt = int(m.get("next_check_day") or 0)
    if nxt and day < nxt:
        _log("  [农田记忆] %s 还有 %d 天才能收（第 %d 天再来），今天不查田"
             % (m.get("crop") or "作物", nxt - day, nxt))
        return False, nxt - day
    return True, 0


def days_left_min(season=None, day=None):
    """读 mod 导出的 daysLeft，返回田里作物最小剩余成熟天数（None=没数据）。"""
    try:
        s = summarize(read_state())
    except Exception:
        return None
    vals = []
    for t in (s.get("tiles") or []):
        if not t.get("crop"):
            continue
        d = t.get("daysLeft")
        if isinstance(d, int) and 0 <= d < 1000:
            vals.append(d)
    return min(vals) if vals else None

def compute_next_check(crops, planted_now, season=None, day=None):
    """下次查田日 = 所有作物里最早成熟的；连收作物收完按连收间隔算。"""
    from collections import Counter as _C
    if season is None or day is None:
        season, day = current_season_day()
    if not crops:
        return min(28, int(day) + 1), "空地"
    best, label = None, "作物"
    for name in set(crops):
        days, regrow = crop_meta(name, season)
        step = days if planted_now > 0 else (regrow if regrow else days)
        nxt = min(28, int(day) + max(1, int(step)))
        if best is None or nxt < best:
            best, label = nxt, name
    return best, label

def remember_farm(crop, days, season=None, day=None):
    """记下下次该哪天再来收。"""
    if season is None or day is None:
        season, day = current_season_day()
    nxt = min(28, int(day) + max(1, int(days)))
    save_memory(season=season, next_check_day=nxt, crop=crop)
    return nxt


def next_activity(default="mine"):
    """一天钓鱼一天挖矿：读记忆里的下一个活动，返回 fish / mine。"""
    m = load_memory()
    act = m.get("next_activity") or default
    if act not in ("fish", "mine"):
        act = default
    return act


def mark_activity_done(act):
    save_memory(next_activity="mine" if act == "fish" else "fish")

TOP_SEEDS = {
    "spring": "Strawberry Seeds",    # 20.8 g/天
    "summer": "Starfruit Seeds",     # 26.9 g/天
    "fall": "Cranberry Seeds",       # 18.9 g/天（可连收）
}


def give_top_seeds(count=MAX_STACK, log_fn=None):
    """已停用：用户明确要求不要再凭空生成物品（2026-11-19）。"""
    if log_fn:
        log_fn("  [种子] 已停用凭空生成，缺种子走商店购买")
    return 0


def ship_surplus(log_fn=None):
    """把作物箱里的作物掏出来 → 投进出货箱 → 剩下的归箱。

    星露谷是隔夜结算，钱要到第二天早上才涨。
    """
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        send_order({"actionType": "close_menu"})
        time.sleep(0.6)
        # 直接让 mod 扫所有箱子里的作物投进出货箱（不再只清“作物箱”）
        # 2026-11-18 用户要求：作物暂时不卖，全部留给加工；这里只做归箱
        inv = summarize(read_state()).get("inventory") or []
        _log("  [出货] 已暂停（作物不卖，留着做果酱/果酒），背包 %d 格" % len(inv))
    except Exception as e:
        _log("  [出货] 异常：%s" % e)


def go_skull_walk(log_fn=None):
    """走路去骷髅洞穴：农场(80,16) → BusStop(22,8) →  Desert → 洞口(8,5) → 第 1 层。"""
    def _log(m):
        if log_fn:
            log_fn(m)

    def st():
        return summarize(read_state())

    def loc():
        return str(st().get("location") or "")

    def step(x, y, tries=3):
        for _ in range(tries):
            send_order({"actionType": "walk_to", "x": x, "y": y})
            time.sleep(5)
            t = st().get("tile") or {}
            if abs(int(t.get("x", -99)) - x) + abs(int(t.get("y", -99)) - y) <= 1:
                return True
        send_order({"actionType": "force_step", "x": x, "y": y})
        time.sleep(4)
        return True

    # ① 农场 → BusStop（农场西口 (80,16) 是传送格）
    if loc() not in ("BusStop", "Desert", "SkullCave"):
        _log("  [骷髅洞穴] 农场西口 → BusStop")
        step(79, 16)
        send_order({"actionType": "walk_to", "x": 80, "y": 16})
        time.sleep(6)
        if loc() not in ("BusStop", "Desert", "SkullCave"):
            _log("  [骷髅洞穴] 没走到 BusStop（%s），传送" % loc())
            send_order({"actionType": "warp_to", "location": "BusStop"})
            time.sleep(6)

    # ② BusStop → 沙漠（(22,8) 是巴士传送格）
    if loc() == "BusStop":
        _log("  [骷髅洞穴] 上巴士 (22,8)")
        step(22, 9)
        send_order({"actionType": "walk_to", "x": 22, "y": 8})
        time.sleep(7)
        if loc() != "Desert":
            _log("  [骷髅洞穴] 巴士没生效（%s），传送去沙漠" % loc())
            send_order({"actionType": "warp_to", "location": "Desert"})
            time.sleep(7)

    # ③ 沙漠 → 洞口（必须先在 (8,6)，再往北迈一步）
    if loc() == "Desert":
        _log("  [骷髅洞穴] 沙漠 (8,6) → 洞口 (8,5)")
        for _ in range(4):
            step(8, 6, tries=2)
            t = st().get("tile") or {}
            if int(t.get("x", -99)) == 8 and int(t.get("y", -99)) == 6:
                break
        send_order({"actionType": "walk_to", "x": 8, "y": 5})
        time.sleep(6)
        if loc() == "SkullCave" and int(st().get("mine_level") or 0) < 121:
            # 进的是洞口小房间，先退出去，别在里面困住
            _log("  [骷髅洞穴] 进了洞口小房间（层 0），先退出来")
            send_order({"actionType": "walk_to", "x": 7, "y": 9})
            time.sleep(6)

    # ④ 洞口房间 → 第 1 层
    if loc() == "SkullCave":
        if int(st().get("mine_level") or 0) < 121:
            send_order({"actionType": "warp_to", "location": "SkullCave"})
            time.sleep(7)
    s2 = st()
    ok = str(s2.get("location")) == "SkullCave" and int(s2.get("mine_level") or 0) >= 121
    _log("  [骷髅洞穴] 进洞结果：%s 层 %s（%s）" % (s2.get("location"), s2.get("mine_level"), "成功" if ok else "失败"))
    return ok


def spare_time_activity(until="20:00", log_fn=None):
    """种完收完后还有时间就去钓鱼/挖矿（雨天或傍晚钓鱼，其余挖矿）。"""
    import subprocess
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        st = summarize(read_state())
    except Exception:
        return False
    now = _mins(st.get("time"))
    end = _mins(until)
    if now >= end - 60:                      # 不足 1 小时就不折腾了
        _log("  [余时] 离 %s 只剩不到 1 小时，休息" % until)
        return False
    hour = int(str(st.get("time", "0:0")).split(":")[0] or 0)
    wet = str(st.get("weather")) in ("rain", "storm")
    if hour >= 20:
        _log("  [余时] 已经 %s，收工回家" % st.get("time"))
        return False
    # 一天钓鱼、一天挖矿（记忆里存着下一个活动）
    act = "mine"   # 沙漠矿洞期间固定挖矿，不做钓鱼轮换
    # 先翻牌再执行：即使今天活动没跑成，明天也轮到另一种（用户 2026-11-18 要求）
    mark_activity_done(act)
    go_fish = (act == "fish")
    script = "stardew_fishsession.py" if go_fish else "stardew_mine2.py"
    _log("  [排班] 今天轮到 %s" % ("钓鱼" if go_fish else "挖矿"))
    # 收工时间要按「回家路程」倒推：海滩→农场 4 段路约 2.3 游戏小时，
    # 矿洞→农场 3 段路约 1.7 小时。算晚了会踩 2:00 强制昏倒，每晚扣 1000g（2026-11-19 修）
    # 每 5 天安排一次沙漠矿洞（骷髅洞穴），收益远高于普通矿洞
    # 用户 2026-11-19 指定：这几天每天直接去沙漠矿洞（骷髅洞穴）
    skull = not go_fish
    if go_fish:
        act_until = "21:30"
        # 竿上还有足够鱼饵就别折腾（上饵一次能装 9999 个，够用很久）
        try:
            _st = summarize(read_state())
            _have = int(_st.get("rod_bait_count") or 0)
            _name = str(_st.get("rod_bait") or "")
            if _have < 5000 or "Wild" not in _name:
                send_order({"actionType": "withdraw_name", "name": "Bait", "count": 9000})
                time.sleep(2.5)
                send_order({"actionType": "attach_bait", "force": True})
                time.sleep(2)
            else:
                _log("  [鱼饵] 竿上已有 %s x%d，不重复装" % (_name, _have))
        except Exception:
            pass
        args = ["--minutes", str(max(20, min(240, (end - now) // 2))), "--until", act_until]
    elif skull:
        act_until = "21:30"
        script = "stardew_skull.py"
        args = ["--until", act_until, "--target", "60"]
        _log("  [排班] 今天去沙漠矿洞（骷髅洞穴）")
    else:
        act_until = "22:00"
        args = ["--rounds", "40", "--levels", "80,90,100,110,120", "--until", act_until]
    _log("  [余时] 到 %s 收工（回家 + 睡觉留足 2 小时）→ 去%s"
         % (act_until, "钓鱼" if script.startswith("stardew_fish") else "挖矿"))
    try:
        # 用 Popen 自己盯游戏时间：脚本偶尔会把 --until 越过（挖矿一局太久），
        # 一过线就掐掉，否则回家太晚会在 2:00 昏倒扣 1000g（2026-11-19 修）
        p = subprocess.Popen([sys.executable, "-u", os.path.join(os.path.dirname(os.path.abspath(__file__)), script)] + args,
                             cwd=os.path.dirname(os.path.abspath(__file__)),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        deadline = _mins(act_until) + 15          # 到点后再宽限 15 游戏分钟
        t_start = time.time()
        while p.poll() is None:
            try:
                cur = _mins(summarize(read_state()).get("time"))
            except Exception:
                cur = None
            if cur is not None and cur >= deadline:
                _log("  [收工] 游戏时间 %s 已过 %s，掐掉活动脚本" % (summarize(read_state()).get("time"), act_until))
                try:
                    p.terminate()
                except Exception:
                    pass
                break
            if time.time() - t_start > 3000:
                _log("  [收工] 活动超时 50 分钟，强制结束")
                try:
                    p.terminate()
                except Exception:
                    pass
                break
            time.sleep(10)
        try:
            out = p.communicate(timeout=30)[0] or ""
        except Exception:
            out = ""
        for line in out.strip().splitlines()[-8:]:
            _log("    | " + line)
        return True
    except Exception as e:
        _log("  [余时] 活动异常：%s" % e)
        return False


def unblock_near(log_fn=None, radius=2, max_swings=10):
    """把角色周围挡路的石头/矿石用镐敲掉（楼梯口被石头堵住时用）。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    swings = 0
    for _ in range(max_swings):
        try:
            s = summarize(read_state())
        except Exception:
            break
        pt = (s.get("player") or {}).get("tile") or {}
        px, py = pt.get("x", -99), pt.get("y", -99)
        blockers = []
        for t in (s.get("tiles") or []):
            if not t.get("breakable") or t.get("interactable"):
                continue
            if abs(t["x"] - px) > radius or abs(t["y"] - py) > radius:
                continue
            name = str(t.get("obj") or "")
            if any(k in name for k in ("石", "矿", "Rock", "Ore", "圆石", "晶洞", "Geode")):
                blockers.append(t)
        if not blockers:
            break
        t = blockers[0]
        _log("  [开路] 镐敲 (%d,%d) %s" % (t["x"], t["y"], t.get("obj")))
        send_order({"actionType": "use_tool", "tool": "pickaxe", "x": t["x"], "y": t["y"]})
        swings += 1
        time.sleep(0.6)
    return swings


def return_from_mine(log_fn=None):
    """从矿洞回到农场：坐电梯/梯子上到矿口，然后**一路走回去**（不闪现）。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    for _ in range(4):
        try:
            loc = str(summarize(read_state()).get("location") or "")
        except Exception:
            return False
        if not (loc.startswith("UndergroundMine") or loc == "Mine"):
            break
        if loc.startswith("UndergroundMine"):
            _log("  [回地面] 在 %s，上到矿口" % loc)
            # 楼梯口被石头/矿石堵住时，先用镐敲开（用户 2026-11-18 要求）
            unblock_near(log_fn=log_fn)
            send_order({"actionType": "leave_mine"})
            time.sleep(5)
        else:
            _log("  [回地面] 在矿口，开始走回农场")
            break
    try:
        loc = str(summarize(read_state()).get("location") or "")
        if loc == "Mine":
            for attempt in range(3):
                _log("  [回地面] 在矿口，第 %d 次尝试走出去（先敲开挡路石头）" % (attempt + 1))
                unblock_near(log_fn=log_fn, radius=3)
                travel_to("Farm", hop_timeout=150, log_fn=log_fn)
                loc = str(summarize(read_state()).get("location") or "")
                if not (loc.startswith("UndergroundMine") or loc == "Mine"):
                    break
            if str(summarize(read_state()).get("location") or "") == "Mine":
                # 兜底：实在走不出去才用传送，避免整夜卡死在矿里
                _log("  [回地面] 走不出去，兜底传送回农场")
                send_order({"actionType": "warp_to", "location": "Farm"})
                time.sleep(6)
        loc = str(summarize(read_state()).get("location") or "")
        ok = not (loc.startswith("UndergroundMine") or loc == "Mine")
        if ok:
            _log("  [回地面] 已到 %s" % loc)
        return ok
    except Exception as e:
        _log("  [回地面] 异常：%s" % e)
        return False


def go_out_tidy(log_fn=None):
    """出门前整理背包：非工具/非种子收进箱子，只留工具和种子（2026-11-18 补）。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        st0 = summarize(read_state())
        if str(st0.get("location")) not in ("Farm", "FarmHouse"):
            _log("  [出门整理] 当前在 %s，不在农场，跳过归箱" % st0.get("location"))
            return
        send_order({"actionType": "close_menu"})
        time.sleep(0.8)
        before = summarize(read_state())
        n0 = len(before.get("inventory") or [])
        send_order({"actionType": "stash_all"})
        time.sleep(3.2)
        send_order({"actionType": "close_menu"})
        time.sleep(0.8)
        send_order({"actionType": "ensure_tools"})
        time.sleep(1.0)
        after = summarize(read_state())
        inv = after.get("inventory") or []
        _log("  [出门整理] 背包 %d 格 → %d 格：%s" % (n0, len(inv), inv))
    except Exception as e:
        _log("  [出门整理] 异常：%s" % e)


def season_end_harvest(log_fn=None, days_left=2):
    """季末抢收：本季只剩 days_left 天（含今天）时，把能收的先收了。

    换季会把地里所有作物清掉（包括还没熟的），所以最后几天能收多少收多少。
    返回收割株数（没到季末或没有可收的返回 0）。
    """
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        season, day = current_season_day()
    except Exception:
        return 0
    remain = 28 - day + 1
    if remain > days_left:
        return 0
    # 必须站在农场才能扫到作物（在屋里时状态里 tiles 是空的，收割会静默变成 0 株）
    try:
        st = summarize(read_state())
        if str(st.get("location")) != "Farm":
            _log("  [抢收] 当前在 %s，先去农场" % st.get("location"))
            travel_to("Farm", hop_timeout=200, log_fn=log_fn)
    except Exception as e:
        _log("  [抢收] 去农场失败：%s" % e)
    _log("  [抢收] %s季第%d天，本季只剩 %d 天 → 抢收成熟作物" % (SEASON_CN.get(season, season), day, remain))
    got = harvest_with_scythe(log_fn=log_fn)
    _log("  [抢收] 本季抢收 %d 株" % got)
    return got


def withdraw_chest_seeds(log_fn=None):
    """从种子箱取种子到背包。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    st = summarize(read_state())
    if str(st.get("location")) != "Farm":
        return 0
    before = sum(count_seeds(en) for en in set(SEED_EN.values()))
    send_order({"actionType": "withdraw", "x": 57, "y": 17, "kind": "seeds", "count": 999})
    time.sleep(2)
    st2 = summarize(read_state())
    after = sum(count_seeds(en, st2) for en in set(SEED_EN.values()))
    if after > before:
        _log("  [取种] 从种子箱取出 %d 个种子" % (after - before))
    return after - before


def withdraw_seeds_by_name(seed_en, need, log_fn=None, chest=(57, 17)):
    """从种子箱按名称取指定种子（只取这一种）。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    before = count_seeds(seed_en)
    if before >= need:
        return before
    send_order({"actionType": "withdraw", "role": "seeds",
                "name": seed_en, "count": max(0, need - before)})
    time.sleep(1.8)
    after = count_seeds(seed_en)
    if after > before:
        _log("  [取种] 从箱子取 %s +%d（现有 %d）" % (seed_en, after - before, after))
    return after



def plant_best_crops(max_n=60, log_fn=None, clear=True, budget_sec=480):
    """季节选种 → 优先背包种子 → 缺了按名称从种子箱取 → 最后才买 → 清障 → 锄地 → 播种。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    season, day = current_season_day()
    picks = pick_crops(season, day)
    try:
        _log("  [种植] 排期建议：" + plant_window_advice(season, day))
    except Exception:
        pass
    if not picks:
        _log("  [种植] %s季第%d天，剩余天数不足以成熟，本季不再种" % (SEASON_CN.get(season, season), day))
        return 0

    # 开种前必须先站在农场：走不过去就别买种子（否则白花钱）
    try:
        cur = str(summarize(read_state()).get("location") or "")
        if cur != "Farm":
            _log("  [种植] 当前在 %s，先走去农场" % cur)
            travel_to("Farm", hop_timeout=180, log_fn=log_fn)
            cur = str(summarize(read_state()).get("location") or "")
            if cur != "Farm":
                _log("  [种植] 到不了农场（在 %s），本轮不买种、不播种" % cur)
                return 0
    except Exception:
        pass

    # 地已经种满就别去取种子了（省得背包又被种子占满）
    free = 0
    try:
        s0 = summarize(read_state())
        x1, y1, x2, y2 = current_plot()
        # 只把「活的作物」算占用：草/杂草/枯死作物/石头都能被 clear_plot 清掉，
        # 以前把它们也算占用，导致换季后杂草长满 → 判定"已种满" → 永远不种也不收（2026-11-19 修）
        occupied = sum(1 for t in (s0.get("tiles") or [])
                       if x1 <= t["x"] <= x2 and y1 <= t["y"] <= y2
                       and t.get("crop"))
        free = plot_area() - occupied
        if free <= 0:
            _log("  [种植] 固定区域已经种满（占用 %d 格），跳过取种/播种" % occupied)
            return 0
        _log("  [种植] 区域空位 %d 格，本轮最多种 %d 株" % (free, min(free, max_n)))
    except Exception:
        pass

    if clear:
        clear_plot(log_fn=log_fn)

    # ① 背包已有 → ② 从箱子按名称取 → ③ 购买（跳过皮埃尔不卖的）
    chosen = None
    for (name, en, days, regrow, profit) in picks:
        n = count_seeds(en)
        if n >= 5:
            _log("  [种植] 背包已有 %d 个 %s（%s），直接用" % (n, en, name))
            chosen = (name, en, n)
            break
    if chosen is None and str(summarize(read_state()).get("location")) == "Farm":
        for (name, en, days, regrow, profit) in picks:
            n = withdraw_seeds_by_name(en, MAX_STACK, log_fn=log_fn)   # 一次取满上限，别抠 200
            if n >= 5:
                chosen = (name, en, n)
                break
    if chosen is None:
        want = max(5, min(free or max_n, max_n, 40))     # 一次只买够这轮种的量（上限 40），别囤
        for (name, en, days, regrow, profit) in picks:
            if en in NOT_IN_PIERRE:
                _log("  [种植] 跳过皮埃尔不卖的 %s" % en)
                continue
            n = ensure_seeds_buy(en, want, log_fn=log_fn)
            if n >= 5:
                chosen = (name, en, n)
                break
    if chosen is None:
        _log("  [种植] 没有可用种子，跳过")
        return 0

    name, seed_en, have = chosen
    # 背包种子不够就把箱子里的补满（堆叠上限 9999，别只抠一小把）
    need_total = plot_area() or max_n
    if have < need_total and str(summarize(read_state()).get("location")) == "Farm":
        more = withdraw_seeds_by_name(seed_en, MAX_STACK, log_fn=log_fn)
        if more > have:
            have = more
    _log("  [种植] %s季第%d天 → 种 %s（库存 %d，区域 %d 格）"
         % (SEASON_CN.get(season, season), day, name, have, need_total))

    plot = current_plot()
    x1, y1, x2, y2 = plot
    _log("  [种植] 农田区域 (%d,%d)-(%d,%d)（来自 .plot_rect，%d×%d）"
         % (x1, y1, x2, y2, x2 - x1 + 1, y2 - y1 + 1))
    planted = 0
    limit = min(max_n, have)
    # 先一次性读全状态，把"已有作物/有障碍"的格子排除，只对空地下手（不再逐格走路）
    st0 = summarize(read_state())
    # 换季后田里常常长满草/杂草，一格空地都没有；先清障再选点，
    # 否则「需要种的空地 0 格」直接跳过，整季都不种（2026-11-19 修）
    try:
        _blockers = [z for z in (st0.get("tiles") or [])
                     if x1 <= z["x"] <= x2 and y1 <= z["y"] <= y2
                     and z.get("obj") and z.get("breakable")]
        _live = [z for z in (st0.get("tiles") or [])
                 if x1 <= z["x"] <= x2 and y1 <= z["y"] <= y2 and z.get("crop")]
        if _blockers and not _live:
            if log_fn:
                log_fn("  [种植] 田里全是障碍（%d 处）且无作物，先清障再播" % len(_blockers))
            clear_plot(log_fn=log_fn)
            st0 = summarize(read_state())
    except Exception:
        pass
    tile_map = {(z["x"], z["y"]): z for z in (st0.get("tiles") or [])}
    dry_set = {(t["x"], t["y"]) for t in (st0.get("dry_tiles") or [])}
    tilled_set = set()
    for tok in (st0.get("tilled_tiles") or []):
        try:
            a2, b2 = str(tok).split(",")
            tilled_set.add((int(a2), int(b2)))
        except Exception:
            pass
    candidates = []
    for y in range(y1, y2 + 1):
        xs = list(range(x1, x2 + 1))
        if (y - y1) % 2 == 1:
            xs.reverse()
        for x in xs:
            z = tile_map.get((x, y))
            if z and z.get("crop"):
                continue                       # 已有作物，直接跳过，不走路
            if z and z.get("obj"):
                continue                       # 有障碍，跳过
            candidates.append((x, y))
    log_exp = lambda m: log_fn(m) if log_fn else None
    # 按离角色的距离排序，就近种植（避免长途寻路失败）
    try:
        px, py = int(st0["tile"]["x"]), int(st0["tile"]["y"])
    except Exception:
        px, py = x1, y1
    # 按行蛇形：一行一行扫，动线最短、看着也整齐
    rows_map = {}
    for (cx2, cy2) in candidates:
        rows_map.setdefault(cy2, []).append(cx2)
    candidates = []
    for idx2, ry in enumerate(sorted(rows_map.keys())):
        xs2 = sorted(rows_map[ry], reverse=(idx2 % 2 == 1))
        candidates += [(cx3, ry) for cx3 in xs2]
    if log_fn:
        log_fn("  [种植] 需要种的空地 %d 格（就近优先，跳过已有作物/障碍）" % len(candidates))
    t_start = time.time()
    pending = set(candidates)
    fails = {}
    # 水面缓存（只算一次）
    try:
        water = water_cache(log_fn=log_fn)
    except Exception:
        water = set()
    pending = {c for c in pending if c not in water}
    if log_fn:
        log_fn("  [种植] 排除水面后待种 %d 格" % len(pending))
    planted = 0
    while pending and planted < limit:
        if time.time() - t_start > budget_sec:
            if log_fn:
                log_fn("  [种植] 超过 %d 秒预算，先收工（已种 %d 株，待种 %d 格）" % (budget_sec, planted, len(pending)))
            break
        stn = summarize(read_state())
        ptn = stn.get("tile") or {}
        px2, py2 = int(ptn.get("x", x1)), int(ptn.get("y", y1))
        target = min(pending, key=lambda c: abs(c[0] - px2) + abs(c[1] - py2))
        if not _go_near(target[0], target[1], log_fn, tries=4, wait=0.9):
            fails[target] = fails.get(target, 0) + 1
            if fails[target] >= 2:
                pending.discard(target)
            continue
        stn = summarize(read_state())
        ptn = stn.get("tile") or {}
        px2, py2 = int(ptn.get("x", x1)), int(ptn.get("y", y1))
        # 站在当前位置，先把周围 3×3 用铱锄头一次锄好（power=4）
        time.sleep(0.2)
        # 再种周围 2 格内的待种格
        batch = [c for c in pending if abs(c[0] - px2) + abs(c[1] - py2) <= 2]
        batch.sort(key=lambda c: abs(c[0] - px2) + abs(c[1] - py2))
        if not batch:
            pending.discard(target)
            continue
        for (x, y) in batch:
            if planted >= limit:
                break
            pending.discard((x, y))
            if (x, y) not in tilled_set:
                send_order({"actionType": "use_tool", "tool": "hoe", "x": x, "y": y})
                time.sleep(0.7)
                tilled_set.add((x, y))
            send_order({"actionType": "plant", "x": x, "y": y, "seed": seed_en})
            time.sleep(0.7)
            planted += 1
            if planted % 25 == 0 and log_fn:
                log_fn("    已种 %d 株…" % planted)
    _log("  [种植] 共种下 %d 株 %s" % (planted, name))
    # 种完把多余的种子收回箱子：出门只带 5 件工具（鱼竿/镐/斧/镰刀/锄）
    if planted > 0:
        try:
            send_order({"actionType": "stash_all"})
            time.sleep(2.5)
            send_order({"actionType": "close_menu"})
            time.sleep(0.8)
            inv = summarize(read_state()).get("inventory") or []
            _log("  [种植] 余种收回箱子，背包剩 %d 格：%s" % (len(inv), inv))
        except Exception:
            pass
    return planted



def plant_timing(crop_name=None, season=None, day=None):
    """计算播种时间：今天/季节/剩余天数/能否成熟/预计收获日。

    返回 dict: season, day, crop, daysToMature, remainingDays, canMature, harvestDay
    """
    if season is None or day is None:
        season, day = current_season_day()
    if crop_name is None:
        picks = pick_crops(season, day)
        if not picks:
            return {"season": season, "day": day, "crop": None, "canMature": False,
                    "remainingDays": max(0, 28 - day + 1), "daysToMature": None, "harvestDay": None}
        crop_name = picks[0][0]
    days = CROP_DAYS_EXTRA.get(crop_name) or 0
    if not days:
        for row in (CROPS.get(season) or []):
            if row[0] == crop_name:
                days = row[2]
                break
    remaining = max(0, 28 - day + 1)
    can = days > 0 and days <= remaining
    return {
        "season": season, "day": day, "crop": crop_name,
        "daysToMature": days, "remainingDays": remaining,
        "canMature": can,
        "harvestDay": (day + days) if can else None,
        "latestPlantDay": (28 - days + 1) if days else None,
    }


def crop_table_text():
    lines = ["作物利润表（种子价/成熟天/售价/再收间隔/约g每天，1.6 基础价）："]
    for season, cn in (("spring", "春"), ("summer", "夏"), ("fall", "秋")):
        rows = "；".join(
            f"{n} {s}g/{d}天/{p}g" + (f" 每{r}天再收" if r else "") + f" ≈{g}g/d"
            for (n, s, d, p, r, g) in CROPS[season]
        )
        lines.append(f"[{cn}季] {rows}")
    lines.append("季末规则：剩余天数 < 成熟天数就别种了。")
    return "\n".join(lines)


SYSTEM_PROMPT = """你是星露谷物语的 AI 农场管家，操控**玩家角色本人**。只输出 JSON：
{"goal": "一句话目标", "actions": [ {"type": "...", ...} ]}

可用动作（最多 8 条，按顺序执行）：
- {"type":"walk_to","x":12,"y":8}                                  走路过去（画面可见）
- {"type":"exit"}                                                  走到门口出门（在屋里时用）
- {"type":"use_tool","tool":"hoe|wateringcan|axe|pickaxe","x":12,"y":8}  用工具（会挥动再生效）
- {"type":"interact","x":12,"y":8}                                 交互（收获/开箱/捡东西）
- {"type":"plant","x":12,"y":8,"seed":"Bok Choy Seeds"}            把背包里的种子种进已锄好的地
  （dry_tiles 是当前可开垦的空地坐标，hoe 请用这里的）
- {"type":"goto_shop"}                                             从农场一路走进皮埃尔商店（跨地图走路）
- {"type":"goto","location":"Mine"}                                 跨地图走过去（Mine/Beach/Town/Mountain/Farm）
- {"type":"fish"}                                                   抛竿钓鱼（需要鱼竿，站在水边）
- {"type":"shop_buy","item":"Parsnip Seeds","count":5}              在店里买种子（会弹出商店界面）
- {"type":"ship"}                                                  把背包里的作物放进出货箱
- {"type":"sleep"}                                                 睡觉进下一天（存档）

""" + crop_table_text() + """

规则：
0. 钓鱼只在海边（goto Beach 后 fish）；不要在农场池塘钓鱼。
1. 人在屋里（location=FarmHouse）→ 先 exit，再走路去农场。
2. 做事前必须 walk_to 到目标格相邻（1 格内），否则工具不生效。
3. 收成优先：tiles 里 cropReady=true 的 → interact；收完 ship 卖作物。
4. 没浇水的作物（crop 有值、waterState=未浇）→ use_tool wateringcan。
5. 空地想种：hoе 开地 → plant? 不支持 plant 时用 interact 播种；开地只挑没有 crop/obj 的格子。
6. 石头用 pickaxe，杂草/树用 axe；有作物的格子只能浇水/收获。
7. 体力 <40 不开新地，<20 回屋休息；18:00 后不开新地，22:00 前 sleep。
8. 坐标必须来自 tiles 或角色当前格附近，别瞎编。
"""

_day_plan = {"day": None, "season": None, "goal": "", "crop": "", "seeds": 0, "tasks": []}
_bought = set()


def plan_day_rules(state):
    season_key = SEASON_KEY.get(str(state.get("season")))
    money = int(state.get("money") or 0)
    day = int(state.get("day") or 1)
    remaining = 28 - day + 1
    budget = max(0, money - 200)
    best = None
    for row in CROPS.get(season_key, []):
        name, seed, days, price, regrow, gday = row
        if days + 1 > remaining or seed > budget:
            continue
        if best is None or gday > best[0]:
            best = (gday, name, seed, days + 1)
    if not best:
        return {"goal": "钱不够或季末太近，今天只做收获/浇水/清理", "crop": "", "seeds": 0, "gday": 0,
                "tasks": ["收获成熟作物", "给未浇水的作物浇水", "清理杂物"]}
    gday, name, seed, need = best
    n = min(int(budget // seed), 20)
    return {"goal": f"种 {n} 株{name}（{need}天成熟，约{gday}g/格/天）", "crop": name, "seeds": n, "gday": gday,
            "tasks": ["收获成熟作物", "给未浇水作物浇水", f"买 {n} 包{name}种子并种下", "清理杂物"]}


def _gday(season_key, name):
    for row in CROPS.get(season_key, []):
        if row[0] == name:
            return row[5]
    return 0.0


def ensure_day_plan(state):
    day, season = state.get("day"), state.get("season")
    if _day_plan["day"] == day and _day_plan["season"] == season:
        return _day_plan
    season_key = SEASON_KEY.get(str(season))
    money = state.get("money") or 0
    rows = "；".join(f"{n} 种子{s}g 成熟{d}天 约{g}g/天" for (n, s, d, p, r, g) in CROPS.get(season_key, []))
    user = (f"现在是{season}第{day}天，有{money}g，体力{state.get('stamina')}，位置{state.get('location')}。\n"
            f"当季作物：{rows}\n"
            '给出今天的计划，只输出 JSON：{"goal":"一句话","crop":"作物名","seeds":数字,"tasks":["..."]}')
    plan = {}
    # 默认用规则计划（0 延迟、0 token）；要模型参与就设 STARDEW_LLM_PLAN=1
    if env("STARDEW_LLM_PLAN", "0") == "1":
        try:
            plan = _extract_json(llm_chat([{"role": "system", "content": SYSTEM_PROMPT},
                                           {"role": "user", "content": user}],
                                          temperature=0.2, max_tokens=400)) or {}
        except Exception as e:
            log(f"模型规划失败（规则兜底）：{e}")
    rule = plan_day_rules(state)
    mcrop = str(plan.get("crop") or "")
    mgday = _gday(season_key, mcrop)
    mdays = 0
    mseed = 0
    for row in CROPS.get(season_key, []):
        if row[0] == mcrop:
            mseed, mdays = row[1], row[2]
    remaining = 28 - int(day or 1) + 1
    if not plan.get("crop") or not mgday or (mdays + 1 > remaining) or (mseed > max(0, int(money) - 200)) \
            or mgday < (rule.get("gday") or 0) * 0.7:
        log(f"采用规则计划：{rule['goal']}")
        _day_plan.update({"day": day, "season": season, **rule})
        return _day_plan
    _day_plan.update({"day": day, "season": season, "goal": str(plan.get("goal") or ""), "crop": mcrop,
                      "seeds": plan.get("seeds") or 0, "gday": mgday,
                      "tasks": [str(x) for x in (plan.get("tasks") or [])][:5]})
    log(f"今日计划：{_day_plan['goal']} | 作物={mcrop} 种子={_day_plan['seeds']}")
    return _day_plan


# ---------------- 提示 / 计划 / 校验 ----------------
def make_hints(state):
    tiles = state.get("tiles") or []
    inv = state.get("inventory") or []
    ready = [t for t in tiles if t.get("crop") and t.get("cropReady")]
    unwatered = [t for t in tiles if t.get("crop") and not t.get("cropReady")
                 and str(t.get("waterState")) != "已浇"]
    dry = state.get("dry_tiles") or []
    clutter = [t for t in tiles if t.get("breakable")]
    hints = []
    if str(state.get("location") or "").lower() == "farmhouse":
        hints.append("人在屋里 → 先 exit 出门")
    if ready:
        hints.append(f"{len(ready)} 株成熟（如 {ready[0]['x']},{ready[0]['y']}）→ walk_to 后 interact")
    if unwatered:
        hints.append(f"{len(unwatered)} 株没浇水（如 {unwatered[0]['x']},{unwatered[0]['y']}）→ wateringcan")
    crop = (_day_plan.get("crop") or "").strip()
    if crop:
        en = CROP_EN.get(crop, crop)
        have = any(crop in str(x) or en in str(x) for x in inv)
        if not have and (state.get("day"), crop) not in _bought:
            hints.append(f"没有{crop}种子 → 先 goto_shop 进店，再 shop_buy {en} Seeds")
        elif have:
            if dry:
                d = dry[:2]
                hints.append("种子有了，开地：" + "、".join(f"hoe ({t['x']},{t['y']})" for t in d))
    if not dry and clutter:
        c = clutter[:2]
        hints.append("没有干净空地：先清杂物 " + "、".join(f"({t['x']},{t['y']})" for t in c))
    if (state.get("money") or 0) < 200 and inv:
        hints.append("钱少 → 先收获再 ship 卖作物")
    if str(state.get("time") or "") >= "22:00":
        hints.append("22:00 后 → sleep 睡觉存档")
    return hints[:5]


def make_plan(state, goal=None):
    dp = _day_plan if _day_plan.get("day") == state.get("day") else {}
    goal_line = (f"人工目标：{goal}" if goal else
                 (f"今日计划：{dp.get('goal')}（作物 {dp.get('crop') or '-'}，种子 {dp.get('seeds') or 0}）"
                  if dp.get("goal") else "今天的目标：收货→浇水→按利润表种当季作物"))
    hints = make_hints(state)
    user = ("当前状态：\n" + json.dumps(state, ensure_ascii=False)
            + ("\n\n参考建议：\n- " + "\n- ".join(hints) if hints else "")
            + f"\n\n{goal_line}\n只输出 JSON；能一次做完就一次给 5~8 个动作（按顺序执行）。")
    last = None
    for attempt in range(3):
        text = llm_chat([{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": user}],
                        temperature=0.2 if attempt else 0.3, max_tokens=1200)
        plan = _extract_json(text)
        if isinstance(plan, dict) and isinstance(plan.get("actions"), list):
            plan.setdefault("goal", goal or "")
            return plan
        last = text[:200]
    raise ValueError("模型没返回合法 JSON：" + str(last))


def _extract_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        return None
    frag = text[s:e + 1]
    try:
        return json.loads(frag)
    except Exception:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", frag.replace("“", '"').replace("”", '"')))
        except Exception:
            return None


def normalize_action(a):
    t = a.get("type")
    if t in ("walk_to", "exit", "use_tool", "interact", "shop_buy", "ship", "sleep", "plant",
             "goto_shop", "goto", "fish"):
        a = dict(a)
        a["actionType"] = t
        a.pop("type", None)
        if t == "use_tool" and not a.get("tool"):
            a["tool"] = "hoe"
        if t == "shop_buy":
            item = str(a.get("item") or "")
            a["item"] = CROP_EN.get(item, item)
        return a
    return None


def validate_actions(state, actions):
    tiles = {(t["x"], t["y"]): t for t in (state.get("tiles") or [])}
    dry = [(d["x"], d["y"]) for d in (state.get("dry_tiles") or [])]
    tilled = [(t["x"], t["y"]) for t in (state.get("tiles") or []) if t.get("tilled")]
    pt = state.get("tile") or {}
    px, py = int(pt.get("x", 0)), int(pt.get("y", 0))

    def nearest(cands, maxd=6):
        if not cands:
            return None
        best = min(cands, key=lambda c: abs(c[0] - px) + abs(c[1] - py))
        return best if abs(best[0] - px) + abs(best[1] - py) <= maxd else None

    keep = []
    indoor = not (state.get("tiles") or [])
    for a in actions:
        t = a.get("type")
        if indoor and t in ("walk_to", "use_tool", "interact", "plant"):
            log(f"  丢弃（人在屋里，先出门）：{a}")
            continue
        if t in ("walk_to", "exit", "shop_buy", "ship", "sleep", "goto_shop", "goto", "fish"):
            if t == "walk_to" and a.get("x") is not None:
                tx, ty = a.get("x"), a.get("y")
                if (tx, ty) not in tiles and (tx, ty) not in dry and abs(tx - px) + abs(ty - py) > 12:
                    log(f"  丢弃（太远的无效坐标）：{a}")
                    continue
            keep.append(a)
            continue
        tile = tiles.get((a.get("x"), a.get("y")))
        # 锄地/播种：模型乱给坐标时，自动吸附到最近的可开垦/已开垦地
        if tile is None and t == "use_tool" and str(a.get("tool") or "").lower() == "hoe":
            snap = nearest(dry)
            if snap:
                log(f"  锄地坐标吸附：({a.get('x')},{a.get('y')}) → {snap}")
                a = dict(a, x=snap[0], y=snap[1])
                tile = {"x": snap[0], "y": snap[1], "crop": None, "obj": None}
        if tile is None and t == "plant":
            snap = nearest(tilled)
            if snap:
                log(f"  播种坐标吸附：({a.get('x')},{a.get('y')}) → {snap}")
                a = dict(a, x=snap[0], y=snap[1])
                tile = {"x": snap[0], "y": snap[1], "crop": None, "tilled": True}
            else:
                log(f"  丢弃（没有已锄好的地可播）：{a}")
                continue
        if t == "plant":
            inv = state.get("inventory") or []
            seed = str(a.get("seed") or "")
            if seed and not any((seed in str(x)) or (seed.replace(" Seeds", "") in str(x)) for x in inv):
                log(f"  丢弃（背包里没有 {seed} 种子）：{a}")
                continue
            keep.append(a)
            continue
        if tile is None:
            log(f"  丢弃（坐标不在附近）：{a}")
            continue
        if t == "use_tool":
            tool = str(a.get("tool") or "").lower()
            if tool == "hoe" and (tile.get("crop") or tile.get("obj") or tile.get("interactable")):
                log(f"  丢弃（不能锄这里）：{a}")
                continue
            if tool in ("wateringcan", "watering_can") and (not tile.get("crop") or tile.get("cropReady")):
                log(f"  丢弃（浇水目标不对）：{a}")
                continue
            if tool in ("axe", "pickaxe") and not (tile.get("breakable") or tile.get("obj")):
                log(f"  丢弃（这里没东西可砍/挖）：{a}")
                continue
            keep.append(a)
            continue
        if t == "interact":
            if not (tile.get("interactable") or tile.get("cropReady")):
                log(f"  丢弃（这里没什么可交互）：{a}")
                continue
            keep.append(a)
            continue
        keep.append(a)
    return keep


# ---------------- 执行 ----------------

# ---------------- 每天出门整理背包 ----------------
DAY_MARK = r"D:\live\logs\.last_tidy_day"


def _read_last_tidy_day():
    try:
        with open(DAY_MARK, encoding="utf-8") as f:
            return int(f.read().strip() or -1)
    except Exception:
        return -1


def _write_last_tidy_day(day):
    try:
        os.makedirs(os.path.dirname(DAY_MARK), exist_ok=True)
        with open(DAY_MARK, "w", encoding="utf-8") as f:
            f.write(str(day))
    except Exception:
        pass


def morning_tidy(force=False, log_fn=None):
    """每次出门整理：箱子角色 + 归箱 + 合并。返回是否执行。"""
    try:
        st = summarize(read_state())
    except Exception:
        return False
    loc = str(st.get("location") or "")
    if loc not in ("Farm", "FarmHouse"):
        return False
    return tidy_chests(log_fn=log_fn)


def state_event():
    try:
        st = read_state()
        return (st.get("event") or ""), bool(st.get("festival"))
    except Exception:
        return "", False


def in_festival():
    ev, fest = state_event()
    return bool(ev) or fest


def wait_out_festival(max_minutes=40, log_fn=None):
    """节日期间等待结束：先尝试离开，再轮询。返回是否已离开节日。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    if not in_festival():
        return True
    ev, _ = state_event()
    _log("  [节日] 检测到游戏内活动「%s」，尝试离开" % ev)
    send_order({"actionType": "leave_festival"})
    time.sleep(3)
    if not in_festival():
        _log("  [节日] 已离开活动")
        return True
    _log("  [节日] 无法离开，等待活动结束（最多 %d 分钟）" % max_minutes)
    t0 = time.time()
    while time.time() - t0 < max_minutes * 60:
        time.sleep(20)
        if not in_festival():
            _log("  [节日] 活动结束，继续")
            return True
        send_order({"actionType": "leave_festival"})
        time.sleep(2)
        send_order({"actionType": "close_menu"})
        time.sleep(1)
    _log("  [节日] 等待超时")
    return not in_festival()



# ---------------- 跨图路由（绕开 BusStop 公交车） ----------------
ROUTE_EDGES = {
    "FarmHouse": ["Farm"],
    "Farm": ["FarmHouse", "Backwoods"],
    "Backwoods": ["Farm", "Mountain"],
    "Mountain": ["Backwoods", "Town", "Mine"],
    "Town": ["Mountain", "Beach", "SeedShop"],
    "Beach": ["Town"],
    "SeedShop": ["Town"],
    "Mine": ["Mountain"],
    "BusStop": ["Farm", "Town"],     # 只有目标就是 BusStop 才走
}


def route_path(src, dst):
    """BFS 找路径，默认绕开 BusStop。"""
    if src == dst:
        return [src]
    from collections import deque
    q = deque([[src]])
    seen = {src}
    while q:
        path = q.popleft()
        cur = path[-1]
        for nxt in ROUTE_EDGES.get(cur, []):
            if nxt in seen:
                continue
            # 目标不是 BusStop 就不路过
            if nxt == "BusStop" and dst != "BusStop":
                continue
            newp = path + [nxt]
            if nxt == dst:
                return newp
            seen.add(nxt)
            q.append(newp)
    return None


def travel_to(dst, hop_timeout=200, log_fn=None):
    """按固定路线逐跳走到目标地图。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        st = summarize(read_state())
    except Exception:
        return False
    cur = str(st.get("location") or "")
    if cur == dst:
        return True
    path = route_path(cur, dst)
    if not path:
        # 兜底：直接 goto
        send_order({"actionType": "goto", "location": dst})
        t0 = time.time()
        while time.time() - t0 < hop_timeout:
            time.sleep(3)
            if str(summarize(read_state()).get("location")) == dst:
                return True
        return False
    _log("  路线: %s" % " → ".join(path))
    for hop in path[1:]:
        send_order({"actionType": "close_menu"})
        time.sleep(0.6)
        send_order({"actionType": "goto", "location": hop})
        t0 = time.time()
        ok = False
        while time.time() - t0 < hop_timeout:
            time.sleep(3)
            now = str(summarize(read_state()).get("location") or "")
            if now == hop:
                ok = True
                break
        _log("    → %s %s" % (hop, "OK" if ok else "失败"))
        if not ok:
            # 降级 1：关菜单 / 唤醒 / 抖开位置，再 goto 一次
            for act in ("close_menu", "wake", "unstick"):
                try:
                    send_order({"actionType": act})
                    time.sleep(1.2)
                except Exception:
                    pass
            send_order({"actionType": "goto", "location": hop})
            t1 = time.time()
            while time.time() - t1 < 90:
                time.sleep(3)
                if str(summarize(read_state()).get("location") or "") == hop:
                    ok = True
                    break
            # 降级 2：在屋里就直接出门（exit = 走门出去，不是传送）
            if not ok and str(summarize(read_state()).get("location") or "") == "FarmHouse" and hop == "Farm":
                _log("    → 卡在屋里，改用 exit 走门出去")
                send_order({"actionType": "exit"})
                for _ in range(10):
                    time.sleep(2)
                    if str(summarize(read_state()).get("location") or "") == "Farm":
                        ok = True
                        break
            if not ok:
                # 降级 3：兜底传送，只记日志不静默（避免整段计划报废）
                _log("    → %s 仍然失败，兜底传送到 %s" % (hop, hop))
                try:
                    send_order({"actionType": "warp_to", "location": hop})
                    time.sleep(6)
                    ok = str(summarize(read_state()).get("location") or "") == hop
                except Exception:
                    ok = False
            if not ok:
                _log("    → %s 兜底也失败，放弃路线" % hop)
                return False
    return str(summarize(read_state()).get("location") or "") == dst



# ---------------- 凌晨过夜保险 ----------------
def _time_minutes(t):
    try:
        h, m = str(t).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def handle_late_night(log_fn=None):
    """凌晨 2 点还在外面（晕倒状态）→ 强制过夜，避免卡死。返回是否处理过。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        st = summarize(read_state())
    except Exception:
        return False
    mins = _time_minutes(st.get("time"))
    loc = str(st.get("location") or "")
    if mins >= 24 * 60 and loc != "FarmHouse":
        _log("  [过夜] 时间已到 %s 且在外面（%s）→ 强制过夜" % (st.get("time"), loc))
        send_order({"actionType": "sleep"})
        time.sleep(12)
        for _ in range(4):
            send_order({"actionType": "close_menu"})
            time.sleep(1.2)
        st2 = summarize(read_state())
        _log("  [过夜] 现在：%s 天%s %s" % (st2.get("location"), st2.get("day"), st2.get("time")))
        return True
    return False



# ---------------- 00:00 自动回家睡觉 ----------------
def _mins(t):
    try:
        h, m = str(t).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0



# ---------------- 商店营业规则 ----------------
# 节日日期（季节索引, 日）：春13/24 夏11/28 秋16/27 冬8/25
FESTIVAL_DAYS = {(0, 13), (0, 24), (1, 11), (1, 28), (2, 16), (2, 27), (3, 8), (3, 25)}
SEASON_INDEX = {"spring": 0, "summer": 1, "fall": 2, "winter": 3,
                "春": 0, "夏": 1, "秋": 2, "冬": 3}
WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def shop_state(st=None):
    """皮埃尔商店是否可买：返回 (可买, 原因列表, 星期文本)。

    规则：9:00-17:00；周三休息（社区中心完成后才全周营业，这里保守按休息算）；
    节日当天关闭。
    """
    if st is None:
        st = summarize(read_state())
    season = SEASON_INDEX.get(str(st.get("season")), 0)
    try:
        day = int(st.get("day") or 1)
    except Exception:
        day = 1
    try:
        mins = _mins(st.get("time"))
    except Exception:
        mins = 0
    weekday = (day - 1) % 7        # 0=周一
    reasons = []
    if (season, day) in FESTIVAL_DAYS:
        reasons.append("节日(%s季%d日)" % (st.get("season"), day))
    if weekday == 2:
        reasons.append("周三皮埃尔休息")
    if mins < 540:
        reasons.append("未到9:00")
    elif mins >= 1020:
        reasons.append("已过17:00（皮埃尔离柜）")
    return (len(reasons) == 0), reasons, WEEKDAY_CN[weekday]


def wait_for_shop(log_fn=None, max_hours=6.0):
    """等到商店可买（必要时睡觉过夜）。返回是否可买。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    t0 = time.time()
    while time.time() - t0 < max_hours * 3600:
        ok, reasons, wd = shop_state()
        if ok:
            _log("  [商店] 现在可以买（%s）" % wd)
            return True
        st = summarize(read_state())
        _log("  [商店] 暂不可买：%s（%s %s %s）" % ("、".join(reasons), wd, st.get("season"), st.get("time")))
        # 时间问题就等；日期问题（周三/节日）就睡到明天
        if any("周三" in r or "节日" in r for r in reasons):
            go_home_sleep(log_fn=log_fn)
            # 睡醒后如果还是不可买（比如节日），再等一天
            ok2, reasons2, wd2 = shop_state()
            if not ok2 and any("节日" in r for r in reasons2):
                _log("  [商店] 节日当天，睡到明天")
                go_home_sleep(log_fn=log_fn)
        else:
            time.sleep(10)
    _log("  [商店] 等待超时")
    return shop_state()[0]



def sleep_verified(log_fn=None, retries=4):
    """睡觉并确认成功换天；被打断就重试（换天/存档约需数秒）。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    before = summarize(read_state())
    day0 = before.get("day")
    for i in range(retries):
        send_order({"actionType": "close_menu"})
        time.sleep(1.0)
        send_order({"actionType": "sleep"})
        # 等换天最多 20 秒
        for _ in range(10):
            time.sleep(2)
            cur = summarize(read_state())
            if str(cur.get("day")) != str(day0):
                _log("  [睡觉] 换天成功：天%s" % cur.get("day"))
                # 清理残留躺床/钓鱼姿势，确保新一天正常
                send_order({"actionType": "wake"})
                time.sleep(1.5)
                send_order({"actionType": "stop_fish"})
                time.sleep(1.0)
                # 关键：直接走出门，游戏才会真正清掉"躺床"状态
                try:
                    send_order({"actionType": "exit"})
                    time.sleep(8)
                except Exception:
                    pass
                return True
        _log("  [睡觉] 第%d次没换天（天%s %s），重试" % (i + 1, day0, summarize(read_state()).get("time")))
    _log("  [睡觉] 多次重试仍没换天")
    return False




def must_go_home(deadline="21:30", log_fn=None):
    """超过 deadline 就必须回家（保证 23:00 前到家）。返回是否应该回家。"""
    st = summarize(read_state())
    cur = _mins(st.get("time"))
    if cur >= _mins(deadline):
        if log_fn:
            log_fn("  [时间] 已经 %s（超过 %s），立即回家" % (st.get("time"), deadline))
        return True
    return False



def go_home_sleep(log_fn=None, force=False):
    """回家（走安全路线到农场 → 门口 → 进屋）并睡觉过夜。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        st = summarize(read_state())
    except Exception:
        return False
    loc = str(st.get("location") or "")
    if loc == "FarmHouse" and not force:
        _log("  [回家] 已在家，直接睡觉")
    else:
        if loc != "Farm":
            _log("  [回家] 从 %s 回农场" % loc)
            if not travel_to("Farm", hop_timeout=200, log_fn=log_fn):
                _log("  [回家] 走路回农场失败，改用传送")
                send_order({"actionType": "warp_to", "location": "Farm"})
                time.sleep(6)
                if str(summarize(read_state()).get("location")) != "Farm":
                    _log("  [回家] 传送也没到农场，今晚先不睡（避免在外过夜扣 1000）")
                    return False
        # 走到门口进门
        send_order({"actionType": "close_menu"})
        time.sleep(0.6)
        _log("  [回家] 走到小屋门口 (64,15)")
        for _ in range(3):
            send_order({"actionType": "walk_to", "x": 64, "y": 16})
            time.sleep(4)
            if str(summarize(read_state()).get("location")) == "FarmHouse":
                break
            if _mins(summarize(read_state()).get("time")) == 0:
                break
            send_order({"actionType": "enter_home"})
            time.sleep(3)
            if str(summarize(read_state()).get("location")) == "FarmHouse":
                _log("  [回家] 已进屋")
                break
    # 睡觉：必须确认已在屋里
    if str(summarize(read_state()).get("location")) != "FarmHouse":
        _log("  [回家] 人还不在屋里，先不睡")
        return False
    _log("  [回家] 睡觉")
    send_order({"actionType": "sleep"})
    time.sleep(12)
    for _ in range(4):
        send_order({"actionType": "close_menu"})
        time.sleep(1.2)
    st2 = summarize(read_state())
    _log("  [回家] 现在：%s 天%s %s" % (st2.get("location"), st2.get("day"), st2.get("time")))
    return True


def handle_midnight(log_fn=None, threshold=24 * 60):
    """到 00:00（或超过）且不在家 → 自动回家睡觉。返回是否处理过。"""
    try:
        st = summarize(read_state())
    except Exception:
        return False
    mins = _mins(st.get("time"))
    loc = str(st.get("location") or "")
    if mins >= threshold and loc != "FarmHouse":
        if log_fn:
            log_fn("  [00:00] 时间 %s 还在 %s → 自动回家睡觉" % (st.get("time"), loc))
        return go_home_sleep(log_fn=log_fn)
    return False



# ---------------- 出门整理（箱子 + 背包） ----------------
CHEST_ROLES = ["seeds", "crop", "fish", "mineral", "treasure", "misc"]


def tidy_chests(log_fn=None):
    """每次出门：分配箱子角色 → 整理箱内物品 → 背包归箱 → 合并堆叠。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        st = summarize(read_state())
    except Exception:
        return False
    loc = str(st.get("location") or "")
    if loc not in ("Farm", "FarmHouse"):
        return False
    _log("  [整理] 分配箱子角色 + 归箱（背包 %d 格）" % len(st.get("inventory") or []))
    send_order({"actionType": "close_menu"})
    time.sleep(0.6)
    if loc == "FarmHouse":
        send_order({"actionType": "goto", "location": "Farm"})
        for _ in range(20):
            time.sleep(2)
            if str(summarize(read_state()).get("location")) == "Farm":
                break
    send_order({"actionType": "ensure_tools"})
    time.sleep(1)
    send_order({"actionType": "assign_chests", "roles": CHEST_ROLES})
    time.sleep(1.5)
    send_order({"actionType": "sort_chests"})
    time.sleep(2.5)
    send_order({"actionType": "stash_all"})
    time.sleep(4)
    send_order({"actionType": "merge_stacks"})
    time.sleep(2)
    try:
        st2 = summarize(read_state())
        _log("  [整理] 完成：背包 %d 格" % len(st2.get("inventory") or []))
    except Exception:
        pass
    return True


def wait_pos(target, timeout=None, tol=1, cur=None):
    """等角色走到目标附近。超时按距离估算，紧轮询（0.15s）。"""
    if cur is None:
        try:
            cur = summarize(read_state())["tile"]
        except Exception:
            cur = {}
    if timeout is None:
        try:
            dist = abs(int(cur.get("x", target[0])) - target[0]) + abs(int(cur.get("y", target[1])) - target[1])
        except Exception:
            dist = 8
        timeout = min(25.0, 2.0 + dist * 0.35)
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            p = summarize(read_state())["tile"]
            if abs(int(p.get("x", -99)) - target[0]) + abs(int(p.get("y", -99)) - target[1]) <= tol:
                return True
        except Exception:
            pass
        time.sleep(0.15)
    return False


def ensure_near(state, x, y):
    """确保角色站在目标格旁边（1 格内），必要时先走过去。"""
    p = state.get("tile") or {}
    px, py = int(p.get("x", 0)), int(p.get("y", 0))
    if abs(px - x) + abs(py - y) <= 1:
        return True
    if abs(px - x) + abs(py - y) > 20:
        log(f"  目标 ({x},{y}) 太远（{abs(px-x)+abs(py-y)} 格），不追")
        return False
    for cand in ((x - 1, y), (x + 1, y), (x, y + 1), (x, y - 1))[:2]:
        log(f"  先走到 ({cand[0]},{cand[1]}) 旁边")
        send_order({"actionType": "walk_to", "x": cand[0], "y": cand[1]})
        if wait_pos(cand, timeout=min(8.0, 2.0 + (abs(px - cand[0]) + abs(py - cand[1])) * 0.3)):
            return True
    return False


def execute(plan, state=None, dry=False):
    actions = (plan.get("actions") or [])[:8]
    if state is not None:
        actions = validate_actions(state, actions)
    log(f"计划：{plan.get('goal')}（{len(actions)} 步）")
    for i, raw in enumerate(actions, 1):
        act = normalize_action(raw)
        if not act:
            log(f"  跳过：{raw}")
            continue
        if dry:
            log(f"  [dry] {json.dumps(act, ensure_ascii=False)}")
            continue
        at = act["actionType"]
        if at == "walk_to":
            tx, ty = act["x"], act["y"]
            tinfo = next((t for t in (state or {}).get("tiles", []) if (t["x"], t["y"]) == (tx, ty)), None)
            blocked = bool(tinfo and (tinfo.get("obj") or tinfo.get("breakable") or tinfo.get("crop")
                                      or tinfo.get("interactable")))
            ok = False
            if blocked:
                ok = ensure_near(state or summarize(read_state()), tx, ty)
            else:
                send_order(act)
                ok = wait_pos((tx, ty))
                if not ok:   # 直接走不到就试着站旁边
                    ok = ensure_near(state or summarize(read_state()), tx, ty)
            log(f"  {i}/{len(actions)} walk_to ({tx},{ty}) → {'到达' if ok else '走不到'}")
        elif at in ("use_tool", "interact", "plant"):
            if not ensure_near(state or summarize(read_state()), act["x"], act["y"]):
                log(f"  {i}/{len(actions)} 走不到 ({act['x']},{act['y']}) 旁边，跳过")
                continue
            send_order(act)
            time.sleep(0.55 if at == "use_tool" else 0.45)
            log(f"  {i}/{len(actions)} {at} ({act['x']},{act['y']})")
        elif at == "exit":
            send_order(act)
            t0 = time.time()
            while time.time() - t0 < 10:
                time.sleep(0.3)
                if str(summarize(read_state()).get("location") or "").lower() != "farmhouse":
                    break
            log(f"  {i}/{len(actions)} exit → {summarize(read_state())['location']}")
        elif at == "goto_shop":
            send_order(act)
            t0 = time.time()
            while time.time() - t0 < 120:
                time.sleep(0.8)
                if str(summarize(read_state()).get("location") or "") == "SeedShop":
                    break
            loc = summarize(read_state()).get("location")
            log(f"  {i}/{len(actions)} goto_shop → {loc}")
        elif at == "goto":
            send_order(act)
            want = act.get("location")
            t0 = time.time()
            while time.time() - t0 < 180:
                time.sleep(0.8)
                if str(summarize(read_state()).get("location") or "") == want:
                    break
            log(f"  {i}/{len(actions)} goto {want} → {summarize(read_state()).get('location')}")
        elif at == "fish":
            send_order(act)
            time.sleep(3.0)
            log(f"  {i}/{len(actions)} fish（抛竿）")
        elif at == "shop_buy":
            send_order(act)
            time.sleep(1.7)
            log(f"  {i}/{len(actions)} shop_buy {act.get('item')} × {act.get('count')}")
        elif at == "ship":
            send_order(act)
            time.sleep(0.8)
            log(f"  {i}/{len(actions)} ship")
        elif at == "sleep":
            log(f"  {i}/{len(actions)} sleep（会进下一天并存档）")
            before = summarize(read_state())
            send_order(act)
            t0 = time.time()
            while time.time() - t0 < 40:
                time.sleep(0.5)
                now = summarize(read_state())
                if now.get("day") != before.get("day") or now.get("season") != before.get("season"):
                    log(f"   已进入 {now.get('season')}第{now.get('day')}天")
                    break
        else:
            send_order(act)
            time.sleep(1)
            log(f"  {i}/{len(actions)} {at}")
    if dry:
        return plan
    time.sleep(1.0)
    s = summarize(read_state())
    log(f"  执行后：{s['season']}第{s['day']}天 {s['time']} {s['location']} 钱={s['money']}")
    return s



def ensure_seeds(state, crop, count):
    """开播前备货：没种子就自动去商店买（进不去店就远程开商店兜底）。"""
    if not crop:
        return False
    inv = state.get("inventory") or []
    en = CROP_EN.get(crop, crop)
    if any((crop in str(x)) or (en in str(x)) for x in inv):
        return True
    if (state.get("money") or 0) < 200:
        log(f"钱不够买{crop}种子（{state.get('money')}g）")
        return False
    log(f"背包没有{en} Seeds → 自动去商店买 {count} 包")
    send_order({"actionType": "goto_shop"})
    t0 = time.time()
    loc = str(summarize(read_state()).get("location"))
    while time.time() - t0 < 150:
        time.sleep(1.0)
        loc = str(summarize(read_state()).get("location"))
        if loc == "SeedShop":
            break
    order = {"actionType": "shop_buy", "item": f"{en} Seeds", "count": int(count)}
    if loc != "SeedShop":
        log(f"  goto_shop 没成功（当前 {loc}）→ 远程开商店兜底")
        order["remote"] = True
    send_order(order)
    time.sleep(2.0)
    inv = summarize(read_state()).get("inventory") or []
    ok = any((crop in str(x)) or (en in str(x)) for x in inv)
    log(f"  买种子{'成功' if ok else '失败'}：{en}")
    return ok

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--goal", default=None)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--interval", type=int, default=15)
    args = ap.parse_args()

    log(f"LLM: {LLM_BASE} model={LLM_MODEL}")
    rounds = 1 if (args.once or args.dry) else (args.rounds if args.loop else 1)
    for r in range(1, rounds + 1):
        try:
            state = summarize(read_state())
            ensure_day_plan(state)
            if _day_plan.get("crop"):
                ensure_seeds(state, _day_plan.get("crop"), _day_plan.get("seeds") or 5)
                state = summarize(read_state())
            log(f"第 {r} 轮：{state['season']} 第{state['day']}天 {state['time']} "
                f"{state['location']} 钱={state['money']} 体力={state['stamina']} 格子={len(state['tiles'])}")
            plan = make_plan(state, args.goal)
            execute(plan, state, dry=args.dry)
            if args.dry:
                break
            log(f"  累计 token：{_tokens['calls']} 次 输入{_tokens['prompt']} 输出{_tokens['completion']}")
            if not args.loop:
                break
            time.sleep(args.interval)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f"出错：{e}")
            if not args.loop:
                raise
            time.sleep(5)


if __name__ == "__main__":
    main()
