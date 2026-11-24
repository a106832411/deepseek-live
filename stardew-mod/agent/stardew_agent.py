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
# 跨图移动方式：walk=只走路 / warp=连相邻地图也传送 / auto=两段以上路程直接传送（默认）
TRAVEL_MODE = env("STARDEW_TRAVEL", "auto").lower()
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
ACTION_HUMAN = {
    "fish": "在钓鱼", "go_fish": "去钓鱼", "stop_fish": "收竿",
    "use_tool": "在用工具", "harvest": "在收菜", "plant": "在播种",
    "walk_to": "在赶路", "warp_to": "传送中", "sleep": "去睡觉",
    "save": "存档", "close_menu": "关菜单", "ensure_tools": "整理工具",
    "stash_all": "归箱", "ship": "出货", "unstick": "脱困",
    "fix_position": "校正坐标", "tidy": "整理", "scan_all": "扫描",
    "interact": "在互动",
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
        _activity["text"] = ACTION_HUMAN.get(a, label)
        _activity["at"] = time.time()
        _write_activity()
    except Exception:
        pass
    return action


# ---------------- LLM ----------------
class LlmUnavailable(Exception):
    """本地/远程模型连不上：调用方走规则兜底，不要每轮刷错误。"""

_llm_dead = False


def llm_chat(messages, temperature=0.3, max_tokens=600):
    global _llm_dead
    if _llm_dead:
        return ""   # 已确认连不上：直接返回空，调用方走规则兜底
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
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        _llm_dead = True
        log("LLM 不可用（" + str(e) + "），改用规则")
        return ""
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
        if len(keep) >= cap:      # 2026-11-21：作物那一轮也要受 cap 限制，否则整张地图都塞进 prompt
            break
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
        # 2026-11-21 补：这几个字段 mod 一直在导出，但 summarize 以前没透传，
        # 导致「收机器 / 鱼饵判断 / 矿洞层数 / 已锄地」四块功能全是空转（读到的永远是空）。
        "machines": s.get("machines") or [],
        "mine_level": s.get("mine_level"),
        "rod_bait": s.get("rod_bait"),
        "rod_bait_count": s.get("rod_bait_count"),
        "tilled_tiles": (s.get("tilled_tiles") or [])[:300],
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
        # 皮埃尔不卖的种子（杨桃/上古/仙人掌…）：背包里有才种，没有就别选（否则买了也买不到）
        if en in NOT_IN_PIERRE and count_seeds(en) <= 0:
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


SHOP_DOOR_TOWN = (44, 56)     # 皮埃尔商店大门在 Town 地图的格子（2026-11-21 用 enter_shop 扫出来）
SHOP_STAND_TOWN = (44, 57)    # 门口站人的格子（门那格不可走，walk_to 会被 mod 拒绝）


def _loc_and_tile():
    s = summarize(read_state())
    t = s.get("tile") or {}
    return str(s.get("location") or ""), int(t.get("x", -99)), int(t.get("y", -99))


def go_to_shop(log_fn=None, timeout=420):
    """走着去皮埃尔商店（逐图走路 → 走到店门 → 推门进去），返回当前地点名。

    2026-11-21 用户要求：买种子要**看得见走过去**，不能站在农场凭空弹出商店菜单。
    注意：进店不是踩 warp，而是 Town 地图上 (44,56) 那格的门（Action=LockedDoorWarp/SeedShop），
    所以先走到门口，再发 enter_shop 让 mod 调 checkAction 推门。
    """
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        loc, _, _ = _loc_and_tile()
        if loc == "SeedShop":
            return loc
        _log("  [买种] 出发去皮埃尔商店（Farm → Backwoods → Mountain → Town → 店门）")
        send_order({"actionType": "goto_shop"})
        t0 = time.time()
        last = loc
        while time.time() - t0 < timeout:
            time.sleep(2)
            loc, _, _ = _loc_and_tile()
            if loc != last:
                _log("    现在在 %s" % loc)
                last = loc
            if loc in ("Town", "SeedShop"):
                break

        if loc == "Town":
            dx, dy = SHOP_STAND_TOWN
            _log("  [买种] 到镇上了，走去皮埃尔店门口 (%d,%d)" % (dx, dy))
            for attempt in range(1, 4):
                send_order({"actionType": "walk_to", "x": dx, "y": dy})
                t1 = time.time()
                while time.time() - t1 < 90:
                    time.sleep(2)
                    loc, tx, ty = _loc_and_tile()
                    if loc == "SeedShop":
                        break
                    if abs(tx - dx) + abs(ty - dy) <= 1:   # mod 的 EnterHouse 只有 ≤1 格才会真的推门
                        break
                loc, tx, ty = _loc_and_tile()
                if loc == "SeedShop":
                    break
                _log("  [买种] 第 %d 次推门（人在 %s %d,%d）" % (attempt, loc, tx, ty))
                send_order({"actionType": "enter_shop", "action": "SeedShop"})
                time.sleep(2.5)
                loc, _, _ = _loc_and_tile()
                if loc == "SeedShop":
                    break
                send_order({"actionType": "unstick"})
                time.sleep(1.2)

        loc, _, _ = _loc_and_tile()
        if loc == "SeedShop":
            _log("  [买种] 已进屋，走到柜台前")
            time.sleep(1.2)
        else:
            _log("  [买种] 没能进店，当前在 %s" % loc)
        return loc
    except Exception as e:
        _log("  [买种] 去商店异常：%s" % e)
        return "?"


def ensure_seeds_buy(seed_en, need, log_fn=None):
    """确保背包里有 need 个种子；不够就**走去店里**买。返回实际可种数量。"""
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
    _log("  [买种] %s 现有 %d，需要 %d → 走去店里买 %d" % (seed_en, have, need, buy_n))
    loc = go_to_shop(log_fn=log_fn)
    order = {"actionType": "shop_buy", "item": seed_en, "count": buy_n}
    if loc != "SeedShop":
        _log("  [买种] 没进到店里（当前 %s）→ 远程开商店兜底" % loc)
        order["remote"] = True
    send_order(order)
    # 2026-11-21：mod 改成「分批购买 + 收银音效」，固定 3 秒会被自己的 close_menu 打断，
    # 这里改成轮询背包数量，买够/数量不再涨了再关界面。
    got = wait_purchase(seed_en, have + buy_n, log_fn=log_fn)
    send_order({"actionType": "close_menu"})
    time.sleep(1.0)
    return got


def wait_purchase(seed_en, want, log_fn=None, timeout=60):
    """等商店分批买完：背包里该种子达到 want，或连续几轮不再增长就返回当前数量。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    t0 = time.time()
    last = -1
    stable = 0
    while time.time() - t0 < timeout:
        time.sleep(1.2)
        n = count_seeds(seed_en)
        if n >= want:
            _log("  [买种] 已买到 %d 个" % n)
            return n
        if n == last:
            stable += 1
            if stable >= 4:      # 2026-11-22：买不动（n=0）也要提前收工，否则白等 60 秒
                break
        else:
            stable = 0
            last = n
    n = count_seeds(seed_en)
    if n < want:
        _log("  [买种] 只买到 %d / %d（可能背包满或钱不够）" % (n, want))
    return n


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
    """农田区域：①固定矩形 > ②记住的中心点 > ③兜底常量 PLOT。

    2026-11-21 修：这里以前会调 remember_plot_center()，而它又调 summarize()→_select_tiles()
    →current_plot()，形成无限递归；而且会把「当时站的坐标」（比如镇上 44,57）写进 .plot_center，
    之后清障/种植就跑到镇上去挥锄头。现在这里只读文件，绝不回读游戏状态。
    """
    rect = plot_rect()
    if rect is not None:
        return rect
    c = _load_plot_center()
    if c is None:
        return PLOT
    cx, cy = c
    x1 = cx - width // 2
    y1 = cy - height // 2
    return (x1, y1, x1 + width - 1, y1 + height - 1)


def _in_plot(x, y, plot=None):
    x1, y1, x2, y2 = plot or current_plot()
    return x1 <= x <= x2 and y1 <= y <= y2


def hard_unstick(log_fn=None):
    """彻底脱困：先进农舍、再从门口走出来。

    2026-11-22 实测：人卡在农场 (49,13) 时，walk_to / unstick / warp_to Farm 全都无效
    （warp_to Farm 在已经在 Farm 时会直接 return），但 warp_house 能进农舍、再出门就恢复了。
    """
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        _log("  [脱困] 走路失效 → 先进农舍再出来")
        send_order({"actionType": "close_menu"})
        time.sleep(0.6)
        send_order({"actionType": "warp_house"})
        time.sleep(4)
        st = summarize(read_state())
        if str(st.get("location")) != "FarmHouse":
            _log("  [脱困] warp_house 没成功（当前 %s）" % st.get("location"))
            return False
        send_order({"actionType": "close_menu"})
        time.sleep(0.5)
        ok = travel_to("Farm", hop_timeout=120, log_fn=log_fn)
        _log("  [脱困] 出门%s（当前 %s）" % ("成功" if ok else "失败", summarize(read_state()).get("location")))
        return ok
    except Exception as e:
        _log("  [脱困] 异常：%s" % e)
        return False


def _go_near(x, y, log_fn=None, tries=8, wait=1.2):
    """走到目标附近（相邻即可）。tries/wait 可调，种植用更短的等待。

    2026-11-22 修：原来只要 tries×wait 到点就认定「走不过去」并调 hard_unstick
    （传送进农舍再出门），而 harvest_click 只给 1.2 秒、种植只给 3.6 秒，
    正常赶路 7 格以上就被误判成卡死，人被反复传送回农舍，远处的作物永远收不到。
    现在改成：**连续两次采样距离都没变小**才算卡住，而且必须整轮真的没走到才 unstick。
    """
    def _pos():
        st = summarize(read_state())
        t = st.get("tile") or {}
        return st, int(t.get("x", -99)), int(t.get("y", -99))

    try:
        st, px, py = _pos()
    except Exception:
        return False
    if abs(px - x) + abs(py - y) <= 2:
        return True
    send_order({"actionType": "walk_to", "x": x, "y": y})
    best = abs(px - x) + abs(py - y)
    no_progress = 0
    for _ in range(tries):
        time.sleep(wait)
        st, px, py = _pos()
        d = abs(px - x) + abs(py - y)
        if d <= 2:
            return True
        if d < best:
            best, no_progress = d, 0
        else:
            no_progress += 1
    # 只有「距离一次都没缩小」才认为真卡住，才允许强制脱困
    if no_progress >= max(2, tries - 1) and hard_unstick(log_fn=log_fn):
        send_order({"actionType": "walk_to", "x": x, "y": y})
        for _ in range(tries):
            time.sleep(wait)
            st, px, py = _pos()
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
    # 2026-11-21：果树/巨型作物绝对不能砍——树苗 850~4250g、28 天成材，砍了不可恢复
    SKIP = ("宝箱", "箱", "熔炉", "木桶", "床", "桌", "果树", "巨型作物")
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
        if "树" in name or "嫩枝" in name or "树枝" in name or "Twig" in name:
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



def seeds_need_planting(log_fn=None):
    """农田还有空地、时间还够 → 现在就该去补种。

    2026-11-21 加：`farm_due` 只按「还有几天成熟」排期，于是刚买的 80 包种子
    要等到下次查田（8 天后）才下地，地块白白空着。这里补一条「有空地就种」的判据。
    种子在背包还是在种子箱都无所谓——种植流程自己会先从箱子里取。
    """
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        s = summarize(read_state())
        # 必须真的站在农场看农田：在农舍/别处时 state 里的 tiles 是当前地图的，
        # 会把「空地 132 格」误判成整片空（2026-11-21 实测踩到）
        if str(s.get("location")) != "Farm":
            return False
        if _mins(s.get("time")) >= _mins("20:00"):
            return False
        rect = plot_rect()
        if not rect:
            return False
        x0, y0, x1, y1 = rect
        area = (x1 - x0 + 1) * (y1 - y0 + 1)
        crops = [t for t in (s.get("tiles") or [])
                 if t.get("crop") and x0 <= t["x"] <= x1 and y0 <= t["y"] <= y1]
        empty = area - len(crops)
        if empty < 5:
            return False
        _log("  [补种] 农田还有 %d 格空地（种了 %d/%d）→ 现在去补种"
             % (empty, len(crops), area))
        return True
    except Exception as e:
        _log("  [补种] 检查异常：%s" % e)
        return False


def has_scythe():
    """背包里有没有镰刀（1.6 叫 Iridium Scythe / 铱镰刀）。"""
    try:
        inv = summarize(read_state()).get("inventory") or []
    except Exception:
        return False
    return any(("Scythe" in str(x)) or ("镰刀" in str(x)) for x in inv)


def harvest_click(log_fn=None, max_tries=3):
    """没有镰刀时的兜底收割：走到每株旁边发 harvest 订单（mod 直接调 Crop.harvest）。

    2026-11-21 加：镰刀被地图清理送进「失物招领」后，镰刀扫荡一刀都收不到，
    11-20 因此白跑一天（346 刀 0 株）。这里改成逐株收，慢但真能收进背包。
    """
    def _log(m):
        if log_fn:
            log_fn(m)

    def _ready():
        st = summarize(read_state())
        return {(t["x"], t["y"]) for t in (st.get("tiles") or [])
                if t.get("cropReady") and t.get("crop")}

    ready = _ready()
    if not ready:
        _log("  [手割] 没有成熟作物")
        return 0
    _log("  [手割] 背包没有镰刀 → 逐株收割 %d 株" % len(ready))
    got = 0
    for rnd in range(1, max_tries + 1):
        left = _ready()
        if not left:
            break
        pos = summarize(read_state()).get("tile") or {}
        cur = (pos.get("x", -99), pos.get("y", -99))
        progressed = False
        for (x, y) in sorted(left, key=lambda p: abs(p[0] - cur[0]) + abs(p[1] - cur[1])):
            if (x, y) not in _ready():
                continue
            if not _go_near(x, y, log_fn, tries=3, wait=0.4):
                continue
            before = len(_ready())
            send_order({"actionType": "harvest", "x": x, "y": y})
            time.sleep(0.32)
            after = len(_ready())
            if after < before:
                got += before - after
                progressed = True
        _log("  [手割] 第%d轮：累计收 %d 株，剩 %d 株" % (rnd, got, len(_ready())))
        if not progressed:
            _log("  [手割] 本轮一株没收（作物不可收 / 走不到位），提前收工")
            break
    _log("  [手割] 共收 %d 株（剩 %d 株）" % (got, len(_ready())))
    return got


def restore_scythe(log_fn=None):
    """镰刀彻底没了就补一把（用户 2026-11-21 同意用 give_scythe 补回）。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    if has_scythe():
        return True
    _log("  [镰刀] 背包/箱子都没有 → 用 give_scythe 补一把（会被鱼竿覆盖导致丢失，见 protect_scythe_from_fish）")
    send_order({"actionType": "give_scythe"})
    time.sleep(1.5)
    ok = has_scythe()
    _log("  [镰刀] 补回%s" % ("成功" if ok else "失败"))
    return ok


def protect_scythe_from_fish(log_fn=None):
    """钓鱼前把「当前选中格」从镰刀挪到镐子，避免被鱼竿覆盖掉。

    2026-11-21 查到的根因：
      mod 的 Fish() / 钓鱼泵抛竿时会 `player.CurrentTool = rod`，
      而 SDV 的 Farmer.CurrentTool setter 是 `Items[CurrentToolIndex] = value`
      —— 直接把**当前选中的那一格**写成鱼竿。
      白天最后用的是镰刀（收割/清障），于是钓鱼第一竿就把镰刀顶成第二根鱼竿。
      （11-20 10:23 背包还有 Iridium Scythe，10:33 钓鱼收工就变成两根鱼竿、镰刀没了。）
      镐子被顶掉没事：ensure_tools 会自动补回；镰刀它不会补。

    做法：朝附近水面挥一下镐子（水里挖不出东西，纯占位），选中格就落到镐子上。
    """
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        st = summarize(read_state())
        inv = st.get("inventory") or []
        if not any(("Scythe" in str(x)) or ("镰刀" in str(x)) for x in inv):
            return False
        pos = st.get("tile") or {}
        px, py = pos.get("x", -99), pos.get("y", -99)
        target = None
        # 2026-11-22 修：summarize 不透传 water_tiles（mod 侧也一直是空数组），
        # 原来这里永远拿不到水面 → 兜底朝自己脚下挥镐，可能把脚下耕地/作物挖掉。
        # 改成用 water_cache()（扫描结果），找不到水面就直接去「最近的可走空地」占位；
        # 连空地都没有就返回 False，不发这一刀。
        try:
            water = water_cache(log_fn=log_fn) or set()
        except Exception:
            water = set()
        for (wx, wy) in sorted(water, key=lambda c: abs(c[0] - px) + abs(c[1] - py)):
            if abs(wx - px) + abs(wy - py) <= 2:
                target = (wx, wy)
                break
        if target is None:
            for z in (st.get("tiles") or []):
                if z.get("crop") or z.get("obj"):
                    continue
                if abs(z["x"] - px) + abs(z["y"] - py) <= 2:
                    target = (z["x"], z["y"])
                    break
        if target is None:
            _log("  [护镰刀] 附近没有水格/空地可占位，这一刀跳过（不朝脚下挖）")
            return False
        send_order({"actionType": "use_tool", "tool": "pickaxe",
                    "x": target[0], "y": target[1]})
        time.sleep(1.2)
        _log("  [护镰刀] 选中格已挪到镐子（朝 %d,%d 挥了一下）" % target)
        return True
    except Exception as e:
        _log("  [护镰刀] 异常：%s" % e)
        return False


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

    # 没有镰刀就别挥了（2026-11-21）：改逐株手割，否则会白挥一天
    if not has_scythe():
        _log("  [收割] 背包没有镰刀 → 改逐株手割")
        return harvest_click(log_fn=log_fn)

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


def _walk_near(x, y, tries=3, log_fn=None):
    """走到 (x,y) 旁边一格（采集/收机器都要人先站过去）。

    2026-11-21 修：采集器/树上的东西挂在不可走的格子上，直接 walk_to(x,y) 会被 mod
    以「不可通行、防穿模」拒绝 → 现在改成按相邻四格依次试，站到旁边就算成功。
    """
    def _pos():
        t = (summarize(read_state()).get("tile") or {})
        return int(t.get("x", -99)), int(t.get("y", -99))

    px, py = _pos()
    if abs(px - x) + abs(py - y) <= 1:
        return True
    for _ in range(tries):
        for (ax, ay) in ((x, y + 1), (x + 1, y), (x - 1, y), (x, y - 1)):
            send_order({"actionType": "walk_to", "x": ax, "y": ay})
            time.sleep(0.9)
            px, py = _pos()
            if abs(px - x) + abs(py - y) <= 1:
                return True
    px, py = _pos()
    return abs(px - x) + abs(py - y) <= 2


def farm_maintenance(log_fn=None):
    """每天早上顺手做：挂采集器（只挂成熟树）、补机器、整理。全部用现有材料，不生成。"""
    def _log(m):
        if log_fn:
            log_fn(m)

    def st():
        return summarize(read_state())

    def inv_count(name):
        n = 0
        for x in [str(i) for i in (st().get("inventory") or [])]:
            if x.startswith(name):
                n += int(x.split(" x")[-1]) if " x" in x else 1
        return n

    # ① 采集器：背包里有就找最近的、还没挂的树试挂（树苗会失败，成熟树才成功）
    tappers = inv_count("Tapper")
    if tappers > 0:
        s = st()
        tiles = s.get("tiles") or []
        trees = [(t["x"], t["y"]) for t in tiles if str(t.get("obj") or "") == "树"]
        tapped = {(t["x"], t["y"]) for t in tiles if "采集器" in str(t.get("obj") or "")}
        p = (s.get("tile") or {})
        px, py = int(p.get("x", -99)), int(p.get("y", -99))
        cand = sorted([q for q in trees if q not in tapped],
                      key=lambda q: abs(q[0] - px) + abs(q[1] - py))
        done = 0
        for (x, y) in cand[:20]:
            if done >= tappers:
                break
            if inv_count("Tapper") <= 0:      # 2026-11-22：背包没采集器了就别再跑
                break
            send_order({"actionType": "walk_to", "x": x, "y": y})
            time.sleep(1.2)
            send_order({"actionType": "place_item", "name": "Tapper", "x": x, "y": y})
            time.sleep(0.8)
            done += 1                          # 原来这里没累加，done>=tappers 永远不成立，每天白跑 20 棵树
        _log("  [维护] 采集器检查完毕（本轮试挂 %d 台，背包还剩 %d）" % (done, inv_count("Tapper")))

    # ② 脱水机：材料够就补（30 木 + 2 粘土 + 1 火石英）
    if inv_count("Dehydrator") == 0 and inv_count("Wood") >= 30 * 3 and inv_count("Clay") >= 6 and inv_count("Fire Quartz") >= 3:
        send_order({"actionType": "craft", "item": "Dehydrator", "count": 3})
        time.sleep(3)
        _log("  [维护] 补了 3 台脱水机")

    # ③ 腌菜桶：材料够就补到 20 个（50 木 + 40 石 + 8 煤）
    if inv_count("Preserves Jar") < 20 and inv_count("Wood") >= 50 and inv_count("Stone") >= 40 and inv_count("Coal") >= 8:
        send_order({"actionType": "craft", "item": "Preserves Jar", "count": 2})
        time.sleep(3)
        _log("  [维护] 补腌菜桶")


def collect_machines_walk(log_fn=None):
    """走到每台成熟的机器旁边手动收（不再远程一键收）。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        st = summarize(read_state())
        ms = st.get("machines") or []
    except Exception:
        return 0
    # 2026-11-22：机器全在农场，人不在农场时 walk_to 必然失败（每台白等十几秒）
    if str(st.get("location")) != "Farm":
        ready = [m for m in ms if m.get("ready")]
        if ready:
            _log("  [收机器] %d 台成熟，但人不在农场（%s），先跳过" % (len(ready), st.get("location")))
        return 0
    ready = [m for m in ms if m.get("ready")]
    if not ready:
        return 0
    _log("  [收机器] %d 台成熟，走过去收" % len(ready))
    n = 0
    for m in ready:
        x, y = int(m["x"]), int(m["y"])
        if not _walk_near(x, y, log_fn=log_fn):
            _log("    (%d,%d) 走不过去，跳过" % (x, y))
            continue
        send_order({"actionType": "interact", "x": x, "y": y})
        time.sleep(1.0)
        n += 1
    _log("  [收机器] 收了 %d 台" % n)
    return n


def load_machines_walk(machine, item="", log_fn=None):
    """走到每台空机器旁边再放料。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        ms = summarize(read_state()).get("machines") or []
    except Exception:
        return 0
    idle = [m for m in ms if machine in str(m.get("name") or "") and not m.get("busy") and not m.get("ready")]
    n = 0
    for m in idle:
        x, y = int(m["x"]), int(m["y"])
        if not _walk_near(x, y, log_fn=log_fn):
            continue
        send_order({"actionType": "load_machines", "machine": machine, "item": item, "times": 1})
        time.sleep(0.9)
        n += 1
    if n:
        _log("  [装机器] %s 装了 %d 台" % (machine, n))
    return n


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
    # 2026-11-22 修：以前这里发 warp_to SkullCave —— 人已经在 SkullCave 时
    # WarpDirect 直接 return（cur==dest），永远下不去；而且成功判据只认 location=="SkullCave"，
    # 真进洞后 location 是 UndergroundMine121+，反而判失败。现在改成走真实梯子 + 只看 mine_level。
    if loc() == "SkullCave" and int(st().get("mine_level") or 0) < 121:
        _log("  [骷髅洞穴] 在洞口小房间，找真实梯子下第 1 层")
        got = False
        for _ in range(3):
            s = st()
            cands = [z for z in (s.get("tiles") or [])
                     if z.get("interactable")
                     and any(k in str(z.get("obj") or "") for k in ("梯", "竖井", "Ladder", "Shaft"))]
            for c in cands[:2]:
                # 梯子格本身不可走：走到四邻格再交互
                for (tx, ty) in ((c["x"], c["y"] + 1), (c["x"], c["y"] - 1), (c["x"] + 1, c["y"]), (c["x"] - 1, c["y"])):
                    send_order({"actionType": "walk_to", "x": tx, "y": ty})
                    time.sleep(2.5)
                    send_order({"actionType": "interact", "x": c["x"], "y": c["y"]})
                    time.sleep(2.5)
                    if int(st().get("mine_level") or 0) >= 121:
                        got = True
                        break
                if got:
                    break
            if got:
                break
        if not got:
            _log("  [骷髅洞穴] 房间里没梯子，用「楼梯」兜底（mine_goto 121）")
            send_order({"actionType": "mine_goto", "level": 121})
            time.sleep(8)
    s2 = st()
    lvl2 = int(s2.get("mine_level") or 0)
    ok = lvl2 >= 121 or str(s2.get("location") or "").startswith("UndergroundMine")
    _log("  [骷髅洞穴] 进洞结果：%s 层 %s（%s）" % (s2.get("location"), lvl2, "成功" if ok else "失败"))
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
    act = next_activity(default="mine")
    # 先翻牌再执行：即使今天活动没跑成，明天也轮到另一种（用户 2026-11-18 要求）
    mark_activity_done(act)
    go_fish = (act == "fish")
    script = "stardew_fishsession.py" if go_fish else "stardew_mine2.py"
    _log("  [排班] 今天轮到 %s" % ("钓鱼" if go_fish else "挖矿"))
    # 收工时间要按「回家路程」倒推：海滩→农场 4 段路约 2.3 游戏小时，
    # 矿洞→农场 3 段路约 1.7 小时。算晚了会踩 2:00 强制昏倒，每晚扣 1000g（2026-11-19 修）
    # 每 5 天安排一次沙漠矿洞（骷髅洞穴），收益远高于普通矿洞
    # 用户 2026-11-20 指定：以后挖矿只去沙漠矿洞（骷髅洞穴），普通矿洞不再去
    skull = not go_fish
    if go_fish:
        # 2026-11-21 修：海滩回农场实测要走 4 个多游戏小时，21:30 收工会踩 2:00 昏倒扣 1000g
        # （19:42 那次就是：21:30 收工 → 25:50 才到农场）。改成 19:30 收工。
        act_until = "19:30"
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
        # 2026-11-21 修：沙漠回农场要「巴士/走路 + 四段图」，实测 21:30 收工会走到 2:00 昏倒扣 1000g
        # （19:13 那次直接从第 3 天跳到第 5 天）。改成 19:30 收工，留足 4 小时回家。
        act_until = "19:30"
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
        # 2026-11-22 修：① 子脚本输出直接写文件（用 PIPE 又从不读，4KB 缓冲写满就死锁）；
        # ② 掐脚本前先 stop_fish / leave_mine，否则 mod 的钓鱼泵会自己抛竿到第二天
        #   （抛竿会 player.CurrentTool=rod → 覆盖选中格，就是丢镐子/镰刀那条路）。
        sub_log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "logs",
                               ("fishsession_run.log" if go_fish else "mine_run.log"))
        sub_log = os.path.abspath(sub_log)
        os.makedirs(os.path.dirname(sub_log), exist_ok=True)
        logf = open(sub_log, "a", encoding="utf-8")
        logf.write("\n===== %s 启动 %s =====\n" % (script, time.strftime("%m-%d %H:%M:%S")))
        logf.flush()
        p = subprocess.Popen([sys.executable, "-u", os.path.join(os.path.dirname(os.path.abspath(__file__)), script)] + args,
                             cwd=os.path.dirname(os.path.abspath(__file__)),
                             stdout=logf, stderr=subprocess.STDOUT)
        deadline = _mins(act_until) + 15          # 到点后再宽限 15 游戏分钟
        t_start = time.time()

        def _stop_child(reason):
            _log("  [收工] %s → 先复位游戏侧状态（stop_fish / leave_mine），再掐脚本" % reason)
            try:
                send_order({"actionType": "stop_fish" if go_fish else "leave_mine"})
            except Exception:
                pass
            time.sleep(2.0)
            try:
                p.terminate()
            except Exception:
                pass
            try:
                p.wait(timeout=10)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

        while p.poll() is None:
            try:
                cur = _mins(summarize(read_state()).get("time"))
            except Exception:
                cur = None
            if cur is not None and cur >= deadline:
                _stop_child("游戏时间 %s 已过 %s" % (summarize(read_state()).get("time"), act_until))
                break
            if time.time() - t_start > 3000:
                _stop_child("活动超时 50 分钟")
                break
            time.sleep(10)
        try:
            p.wait(timeout=10)
        except Exception:
            pass
        try:
            logf.flush()
            logf.close()
        except Exception:
            pass
        try:
            with open(sub_log, encoding="utf-8", errors="replace") as f:
                tail = f.read().splitlines()[-8:]
        except Exception:
            tail = []
        for line in tail:
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
        pt = s.get("tile") or {}          # 2026-11-21 修：summarize 的玩家坐标在顶层 tile，不是 s["player"]["tile"]
        px, py = pt.get("x", -99), pt.get("y", -99)
        if px == -99 or py == -99:
            _log("  [开路] 拿不到玩家坐标，跳过")
            break
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
        # 2026-11-21：stash_all 会把水壶收进箱子（mod 新版已改成保留，旧存档里水壶还在箱子里时取回来）
        _ensure_watering_can(log_fn=log_fn)
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
    # 2026-11-22：STARDEW_LLM_PLAN=1 时让本地模型参与「今天种什么」。
    # 模型只用来在**已经算好、已经校验过**的候选里排序；名单外的作物一律忽略（规则表兜底）。
    try:
        if env("STARDEW_LLM_PLAN", "0") == "1" and llm_available():
            _dp = ensure_day_plan(summarize(read_state()))
            _mcrop = str(_dp.get("crop") or "")
            if _mcrop:
                _names = [p[0] for p in picks]
                if _mcrop in _names:
                    picks.sort(key=lambda p: 0 if p[0] == _mcrop else 1)
                    _log("  [种植] 模型参与选种：%s（候选 %d 个里优先）" % (_mcrop, len(picks)))
                else:
                    _log("  [种植] 模型给的 %s 不在本季可行列表，按规则表" % _mcrop)
    except Exception as e:
        _log("  [种植] 模型选种失败（规则兜底）：%s" % e)
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
        # 2026-11-21 用户要求：一次买够、一次种完，不要「先买 40 种 40，过几天再补 12」。
        # 所以这里按「区域空位数」买足（不再有 40 的上限）。
        want = max(5, free or max_n)
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
    # 2026-11-21 修：不在农场时 state 里的 tiles 是当前地图的（在店里就是「整片田都空」），
    # 必须先走回农场再算空位，否则会对着 132 格空地跑到店里/镇上去种。
    if str(summarize(read_state()).get("location")) != "Farm":
        _log("  [种植] 当前不在农场 → 先走回农场再种")
        travel_to("Farm", hop_timeout=200, log_fn=log_fn)
    _log("  [种植] 农田区域 (%d,%d)-(%d,%d)（来自 .plot_rect，%d×%d）"
         % (x1, y1, x2, y2, x2 - x1 + 1, y2 - y1 + 1))
    planted = 0
    # 2026-11-21 用户要求：整片一次种完，不要分批次。上限按「区域空位」算，不再受 max_n 限制。
    limit = min(free, have) if free else min(max_n, have)
    _log("  [种植] 本次一口气种 %d 株（区域空位 %d，手里种子 %d）" % (limit, free or 0, have))
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
    # 2026-11-21 修：边界差一天。最后一天种下时 day+days 会到 29，换季被清掉。
    can = days > 0 and (day + days) <= 28
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
        # 皮埃尔不卖的种子（杨桃在沙漠绿洲）：背包里没有就别规划，否则买不到
        en = SEED_EN.get(name)
        if en in NOT_IN_PIERRE and count_seeds(en, state) <= 0:
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
        try:
            log("  [LLM计划] 模型给的是：crop=%s seeds=%s" % (plan.get("crop"), plan.get("seeds")))
        except Exception:
            pass
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


def _rule_plan(state, goal=None):
    """LLM 不可用时的规则动作计划：出室内→去农场→收菜/浇水/播种/除草。"""
    loc = str(state.get("location") or "")
    dp = _day_plan or {}
    crop = str(dp.get("crop") or "")
    seed_en = SEED_EN.get(crop, "")
    have = count_seeds(seed_en, state) if seed_en else 0
    tiles = state.get("tiles") or []
    ready = [t for t in tiles if t.get("cropReady")]
    dry = state.get("dry_tiles") or []
    tilled = []
    for tok in (state.get("tilled_tiles") or []):
        try:
            a2, b2 = str(tok).split(",")[:2]
            tilled.append({"x": int(float(a2)), "y": int(float(b2))})
        except Exception:
            continue
    acts = []
    if loc != "Farm":
        acts.append({"type": "goto", "location": "Farm"})
    else:
        for t in ready[:4]:
            acts.append({"type": "harvest", "x": t["x"], "y": t["y"]})
        if not acts and dry:
            for d in dry[:6]:
                acts.append({"type": "use_tool", "tool": "wateringcan", "x": d["x"], "y": d["y"]})
        if not acts and have > 0 and tilled:
            for t in tilled[:6]:
                acts.append({"type": "plant", "x": t["x"], "y": t["y"]})
        if not acts:
            # 没事干：用镰刀清一片草（看着像在干活）
            pt = state.get("tile") or {}
            px, py = int(pt.get("x", 0)), int(pt.get("y", 0))
            grass = [t for t in tiles if t.get("breakable") and t.get("obj") == "草"]
            grass.sort(key=lambda t: abs(t["x"] - px) + abs(t["y"] - py))
            for t in grass[:6]:
                acts.append({"type": "use_tool", "tool": "scythe", "x": t["x"], "y": t["y"]})
    if not acts:
        acts.append({"type": "goto", "location": "Farm"})
    return {"goal": goal or (dp.get("goal") or "规则模式：农场干活"), "actions": acts}


def make_plan(state, goal=None):
    global _llm_dead
    if not _llm_dead and not llm_available():
        _llm_dead = True
        log("LLM 不在线，本轮起走规则计划")
    if _llm_dead:
        return _rule_plan(state, goal)
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


# ---------------- 卡住时让本地模型出主意 ----------------
RESCUE_ACTIONS = {
    "walk_to": "走路到某格", "use_tool": "用工具（tool=hoe/axe/pickaxe/wateringcan/scythe）",
    "interact": "交互目标格（收作物/开箱子/捡东西）", "plant": "把背包种子种进已锄地",
    "goto": "跨地图走过去（location=Farm/Town/Beach/Mountain/Backwoods/BusStop）",
    "warp_to": "直接传送（location=Farm/FarmHouse/Desert）", "warp_house": "传送进农舍",
    "unstick": "原地抖开（解卡）", "exit": "走到门口出门", "enter_home": "进农舍门",
    "close_menu": "关掉当前界面/对话", "go_fish": "走到钓鱼点", "fish": "抛竿钓鱼",
    "sleep": "睡觉进下一天",
}
_last_rescue_at = 0.0
_rescue_used = 0


def llm_available(timeout=3):
    """本地模型在不在线（不在就完全不碰 LLM 路径）。"""
    try:
        req = urllib.request.Request(LLM_BASE.rstrip("/") + "/models",
                                     headers={"Authorization": f"Bearer {LLM_KEY}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return False
            body = json.loads(r.read().decode("utf-8") or "{}")
            models = [str(m.get("id") or "") for m in (body.get("data") or [])]
            # 服务在跑但一个模型都没有（ollama 空库）→ 当不可用
            return any(models)
    except Exception:
        return False


def llm_rescue(log_fn=None, reason="", cooldown=300):
    """卡住时让模型从白名单里挑**一个**动作，校验后发出去。返回执行的动作或 None。

    - 有冷却（默认 5 分钟最多一次），避免模型瞎指挥把局面搞更糟；
    - 动作仍要过 normalize_action + validate_actions（坐标瞎编会被丢掉）；
    - 模型不可用 / 超时 / JSON 非法 → 返回 None，调用方继续走原来的兜底。
    """
    global _last_rescue_at, _rescue_used
    def _log(m):
        if log_fn:
            log_fn(m)
    if time.time() - _last_rescue_at < cooldown:
        return None
    if not llm_available():
        return None
    _last_rescue_at = time.time()
    try:
        st = read_state()
        s = summarize(st)
        t = s.get("tile") or {}
        px, py = int(t.get("x", -99)), int(t.get("y", -99))
        loc = str(s.get("location") or "")
        # ① 本地确定性生成候选动作（坐标都来自真实状态，避免模型瞎编）
        cands = [{"type": "unstick", "why": "原地抖开"},
                 {"type": "warp_house", "why": "传送进农舍重置位置"},
                 {"type": "close_menu", "why": "关掉可能卡住的操作界面"}]
        if loc != "Farm":
            cands.append({"type": "goto", "location": "Farm", "why": "回农场"})
        passable = [z for z in (s.get("tiles") or [])
                    if abs(z["x"] - px) <= 3 and abs(z["y"] - py) <= 3
                    and not z.get("obj") and not z.get("breakable")]
        for z in passable[:3]:
            cands.append({"type": "walk_to", "x": z["x"], "y": z["y"],
                          "why": "走到旁边空格 (%d,%d)" % (z["x"], z["y"])})
        for z in [z for z in (s.get("tiles") or []) if z.get("cropReady")][:2]:
            cands.append({"type": "interact", "x": z["x"], "y": z["y"], "why": "收成熟作物"})
        if loc == "Farm":
            cands.append({"type": "walk_to", "x": 64, "y": 16, "why": "走到小屋门口"})
        cands.append({"type": "go_fish", "why": "去钓鱼点"})
        cands = cands[:9]
        menu_txt = "\n".join("%d. %s" % (i + 1, json.dumps(c, ensure_ascii=False)) for i, c in enumerate(cands))
        info = {"season": s.get("season"), "day": s.get("day"), "time": s.get("time"),
                "weather": s.get("weather"), "location": loc, "tile": t,
                "money": s.get("money"), "stamina": s.get("stamina"),
                "inventory": s.get("inventory")}
        sys_p = ("你是星露谷物语的操作手。角色卡住了，下面是几个**已经校验过坐标**的候选动作，"
                 "你只能选一个编号。只输出 {\"choice\": 编号}，不要解释、不要输出别的字。")
        user_p = ("状态：" + json.dumps(info, ensure_ascii=False)
                  + "\n卡住原因：" + (reason or "位置长时间不动")
                  + "\n候选：\n" + menu_txt
                  + "\n只输出 {\"choice\": 编号}。")
        text = llm_chat([{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
                        temperature=0.0, max_tokens=40)
        obj = _extract_json(text) or {}
        try:
            idx = int(obj.get("choice") or 0)
        except Exception:
            m = re.search(r"\d+", text or "")
            idx = int(m.group(0)) if m else 0
        if not (1 <= idx <= len(cands)):
            _log("  [LLM救援] 模型没选中有效编号（原文：%s），放弃" % str(text)[:60])
            return None
        act = dict(cands[idx - 1])
        act.pop("why", None)
        # 再次过一遍校验（防止候选之外的字段混进来）
        # 2026-11-22 修：validate_actions 只认 a["type"]，必须先用原始候选（带 type）过校验，
        # 再把通过的动作用 normalize_action 转成 actionType 发出去，否则 unstick/goto 这类会被全丢掉。
        raw = dict(act)
        kept_raw = validate_actions(s, [raw])
        kept = [normalize_action(k) or k for k in kept_raw]
        if not kept:
            _log("  [LLM救援] 校验没过：%s" % act)
            return None
        send_order(kept[0])
        _rescue_used += 1
        _log("  [LLM救援] 模型选了 #%d %s（第 %d 次）" % (idx, json.dumps(kept[0], ensure_ascii=False), _rescue_used))
        return kept[0]
    except Exception as e:
        _log("  [LLM救援] 异常：%s" % e)
        return None


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
             "goto_shop", "goto", "fish",
             # 2026-11-22：救援动作也要能过白名单（原来这几个全被当非法丢掉）
             "unstick", "warp_house", "warp_to", "close_menu", "enter_home", "go_fish", "stop_fish"):
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
    # 2026-11-21 修：tiles 里没有 "tilled" 字段（mod 只在顶层导出 tilled_tiles），
    # 以前这里恒为空 → 模型下发的 plant 动作全被当「没有已锄好的地」丢掉。
    tilled = []
    for tok in (state.get("tilled_tiles") or []):
        try:
            a2, b2 = str(tok).split(",")[:2]
            tilled.append((int(float(a2)), int(float(b2))))
        except Exception:
            continue
    tilled += [(t["x"], t["y"]) for t in (state.get("tiles") or []) if t.get("tilled")]
    pt = state.get("tile") or {}
    px, py = int(pt.get("x", 0)), int(pt.get("y", 0))

    def nearest(cands, maxd=6):
        if not cands:
            return None
        best = min(cands, key=lambda c: abs(c[0] - px) + abs(c[1] - py))
        return best if abs(best[0] - px) + abs(best[1] - py) <= maxd else None

    keep = []
    # 2026-11-22 修：tiles 为空只代表「没扫到」，Town/Beach/Desert 这些图本来就没有农田格，
    # 以前被当成「人在屋里」，把该地图上所有 walk_to/use_tool/interact/plant 全丢掉（LLM 救援也一起废）。
    _loc = str(state.get("location") or "").lower()
    indoor = _loc in ("farmhouse", "farmhouse2", "shed", "coop", "barn", "greenhouse", "cabin")
    for a in actions:
        t = a.get("type")
        if indoor and t in ("walk_to", "use_tool", "interact", "plant"):
            log(f"  丢弃（人在屋里，先出门）：{a}")
            continue
        if t in ("walk_to", "exit", "shop_buy", "ship", "sleep", "goto_shop", "goto", "fish",
                      "unstick", "warp_house", "warp_to", "close_menu", "enter_home", "go_fish", "stop_fish"):
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
        if tile is None and t == "use_tool" and str(a.get("tool") or "").lower() in ("wateringcan", "watering_can"):
            if (a.get("x"), a.get("y")) in dry:
                tile = {"x": a.get("x"), "y": a.get("y"), "crop": "?", "waterState": "干地"}
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
    # 2026-11-21 修：23:00 之后别走路了。深夜从沙漠/海滩走回农场要 4 个游戏小时，
    # 铁定踩 2:00 昏倒（今晚已经丢了 3 次 1000g）。太晚就直接传送。
    if _mins(st.get("time")) >= _mins("23:00"):
        _log("  现在 %s，太晚 → 直接传送到 %s" % (st.get("time"), dst))
        send_order({"actionType": "warp_to", "location": dst})
        time.sleep(6)
        if str(summarize(read_state()).get("location")) == dst:
            return True
    path = route_path(cur, dst)
    hops = (len(path) - 1) if path else 99
    # 2026-11-23：加传送模式。走路一段要 40~55 秒，FarmHouse→Beach 四段要 3 分半；
    # 传送落地 2 秒。auto 只对「两段以上」的跨图用传送，相邻地图（进农舍/出门）照旧走。
    want_warp = TRAVEL_MODE == "warp" or (TRAVEL_MODE == "auto" and hops >= 2)
    if want_warp:
        _log("  传送 → %s（跳过 %s 段走路）" % (dst, hops if path else "?"))
        try:
            send_order({"actionType": "warp_to", "location": dst})
            t0 = time.time()
            while time.time() - t0 < 25:
                time.sleep(2)
                if str(summarize(read_state()).get("location") or "") == dst:
                    _log("    已到 %s（%.0f 秒）" % (dst, time.time() - t0))
                    return True
            _log("  传送没生效，回退走路")
        except Exception as e:
            _log("  传送异常：%s，回退走路" % e)
    if not path:
        # 兜底：直接 goto
        send_order({"actionType": "goto", "location": dst})
        t0 = time.time()
        while time.time() - t0 < hop_timeout:
            time.sleep(1)
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
            time.sleep(1)
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


def wait_for_shop(log_fn=None, max_hours=2.0):
    """等到商店可买（当天买不到就睡到明天，最多睡两天）。返回是否可买。"""
    def _log(m):
        if log_fn:
            log_fn(m)
    t0 = time.time()
    slept = 0
    while time.time() - t0 < max_hours * 3600:
        ok, reasons, wd = shop_state()
        if ok:
            _log("  [商店] 现在可以买（%s）" % wd)
            return True
        st = summarize(read_state())
        _log("  [商店] 暂不可买：%s（%s %s %s）" % ("、".join(reasons), wd, st.get("season"), st.get("time")))
        mins = _mins(st.get("time"))
        date_issue = any(("周三" in r or "节日" in r) for r in reasons)
        # 2026-11-21 修：打烊后（17:00 之后 / 凌晨）当天不会再开，干等只会等到 2:00 昏倒扣钱
        late = mins >= _mins("17:00") or mins < _mins("06:00")
        if date_issue or late:
            if slept >= 2:
                _log("  [商店] 连着睡两天还是买不了，放弃")
                break
            _log("  [商店] 今天买不到了（%s）→ 睡到明天再来" % "、".join(reasons))
            go_home_sleep(log_fn=log_fn)
            slept += 1
            t0 = time.time()
            continue
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
    day0 = st.get("day")
    loc = str(st.get("location") or "")
    if loc == "FarmHouse" and not force:
        _log("  [回家] 已在家，直接睡觉")
    else:
        if loc != "Farm":
            _log("  [回家] 从 %s 回农场" % loc)
            # 2026-11-21 修：23:00 之后还在外面就别走路了，直接传送回农场，
            # 否则铁定踩 2:00 昏倒扣 1000g（今晚已经丢了两次）。
            late = _mins(st.get("time")) >= _mins("23:00")
            if late:
                _log("  [回家] 已经 %s，太晚 → 直接传送回农场" % st.get("time"))
                send_order({"actionType": "warp_to", "location": "Farm"})
                time.sleep(6)
            if str(summarize(read_state()).get("location")) != "Farm" and not travel_to("Farm", hop_timeout=200, log_fn=log_fn):
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
    # 睡觉：必须确认已在屋里；进不去就兜底传进农舍
    if str(summarize(read_state()).get("location")) != "FarmHouse":
        _log("  [回家] 进不了屋，兜底传进农舍")
        send_order({"actionType": "warp_house"})
        time.sleep(5)
    if str(summarize(read_state()).get("location")) != "FarmHouse":
        _log("  [回家] 还是没进屋，先不睡")
        return False
    # 2026-11-21 修：走回家的路上可能已经 2:00 昏倒并换天了（从沙漠回来要 4 个游戏小时），
    # 这时再发 sleep 会让游戏又 NewDay 一次，白跳一天（实测 19:13 从第 3 天跳到了第 5 天）。
    st_now = summarize(read_state())
    if st_now.get("day") != day0:
        _log("  [回家] 路上已经昏倒换天了（第 %s 天 → 第 %s 天），不再重复睡觉" % (day0, st_now.get("day")))
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
# 2026-11-21 修：mod 的 stash_all 只按 seeds/crop/misc 三个角色结算，
# 传 6 个角色时 fish/mineral/treasure 全被折算成 misc，箱子不够就会把鱼矿塞进种子箱。
CHEST_ROLES = ["seeds", "crop", "misc"]


def _ensure_watering_can(log_fn=None):
    """整理完把水壶取回背包（stash_all 会把水壶收进箱子，而 ensure_tools 不补造水壶）。

    2026-11-21 加：不取回来的话，之后所有手动浇水都会报「没有 WateringCan」。
    """
    def _log(m):
        if log_fn:
            log_fn(m)
    try:
        inv = [str(x) for x in (summarize(read_state()).get("inventory") or [])]
        if any("Watering Can" in x or "水壶" in x or "喷壶" in x for x in inv):
            return True
        _log("  [整理] 背包里没有水壶 → 从箱子取回")
        send_order({"actionType": "withdraw_name", "name": "Watering Can", "count": 1})
        time.sleep(2)
        inv = [str(x) for x in (summarize(read_state()).get("inventory") or [])]
        ok = any("Watering Can" in x or "水壶" in x or "喷壶" in x for x in inv)
        _log("  [整理] 水壶取回%s" % ("成功" if ok else "失败（箱子里可能没有）"))
        return ok
    except Exception as e:
        _log("  [整理] 取水壶异常：%s" % e)
        return False


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
    _ensure_watering_can(log_fn=log_fn)
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
    """确保角色站在目标格旁边（1 格内），必要时先走过去。

    2026-11-21 修：以前用计划开始时的 state 快照判断距离，前面几步走完坐标早变了，
    于是要么误判「已经在旁边」（实际很远，动作被 mod 拒绝），要么误判「太远不追」。
    现在每次都重新读一次状态。
    """
    def _pos():
        try:
            p = summarize(read_state()).get("tile") or {}
        except Exception:
            p = {}
        return int(p.get("x", -99)), int(p.get("y", -99))

    px, py = _pos()
    if abs(px - x) + abs(py - y) <= 1:
        return True
    dist0 = abs(px - x) + abs(py - y)
    if dist0 > 140:
        log(f"  目标 ({x},{y}) 太远（{dist0} 格），不追")
        return False
    # 远距离：先整段走到目标附近（mod 的寻路已经修好，能走长路），再微调到旁边
    if dist0 > 6:
        log(f"  目标较远（{dist0} 格），先走过去")
        send_order({"actionType": "walk_to", "x": x, "y": y})
        if wait_pos((x, y), timeout=min(60.0, 6.0 + dist0 * 0.5)):
            return True
    for cand in ((x - 1, y), (x + 1, y), (x, y + 1), (x, y - 1)):   # 2026-11-22：四个都试，别只试左右
        log(f"  先走到 ({cand[0]},{cand[1]}) 旁边")
        send_order({"actionType": "walk_to", "x": cand[0], "y": cand[1]})
        if wait_pos(cand, timeout=min(20.0, 3.0 + (abs(px - cand[0]) + abs(py - cand[1])) * 0.4)):
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
    # 2026-11-22 修：以前只要物品名包含作物名就算「有种子」，收获下来的果实
    # （Blueberry / 蓝莓）也满足 → 误判不用买种，后面 plant 全被 mod 以「没有种子」拒绝。
    # 现在必须名字里真的带 Seeds/种子。
    def _is_seed(nm):
        s = str(nm)
        return (("Seed" in s) or ("种子" in s)) and ((crop in s) or (en in s))

    if any(_is_seed(x) for x in inv):
        return True
    if (state.get("money") or 0) < 200:
        log(f"钱不够买{crop}种子（{state.get('money')}g）")
        return False
    log(f"背包没有{en} Seeds → 走去皮埃尔商店买 {count} 包")
    loc = go_to_shop(log_fn=log)
    order = {"actionType": "shop_buy", "item": f"{en} Seeds", "count": int(count)}
    if loc != "SeedShop":
        log(f"  没进到店里（当前 {loc}）→ 远程开商店兜底")
        order["remote"] = True
    send_order(order)
    got = wait_purchase(f"{en} Seeds", int(count), log_fn=log)
    send_order({"actionType": "close_menu"})
    time.sleep(1.0)
    inv = summarize(read_state()).get("inventory") or []
    ok = any((crop in str(x)) or (en in str(x)) for x in inv)
    log(f"  买种子{'成功' if ok else '失败'}：{en}（现有 {got}）")
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
        except LlmUnavailable:
            log("LLM 不可用（连不上模型服务），继续用规则玩")
            if not args.loop:
                raise
            time.sleep(5)
        except Exception as e:
            log(f"出错：{e}")
            if not args.loop:
                raise
            time.sleep(5)


if __name__ == "__main__":
    main()
