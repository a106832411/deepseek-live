#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""矿洞多层级挖矿：电梯换层 + 敲石头 + 找下梯子(173)下一层。

用法：python stardew_mine2.py --rounds 12 --levels 40,45,50
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stardew_agent as A  # noqa: E402

LOG = r"D:\live\logs\mine2.log"
_fh = None


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    global _fh
    if _fh is None:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        _fh = open(LOG, "a", encoding="utf-8")
    _fh.write(line + "\n")
    _fh.flush()


def state():
    return A.summarize(A.read_state())


def pos(s):
    t = s.get("tile") or {}
    return int(t.get("x", -99)), int(t.get("y", -99))


def in_mine(loc):
    loc = str(loc or "")
    return loc == "Mine" or loc.startswith("UndergroundMine")


def go(x, y, timeout=20):
    s = state()
    px, py = pos(s)
    if abs(px - x) + abs(py - y) <= 1:
        return True
    A.send_order({"actionType": "walk_to", "x": x, "y": y})
    return A.wait_pos((x, y), timeout=min(timeout, 4 + (abs(px - x) + abs(py - y)) * 0.5))


def stand_next(x, y):
    # 先 4 邻，再 4 斜角（斜角也能敲）
    spots = [(1, 0), (-1, 0), (0, 1), (0, -1),
             (1, 1), (-1, 1), (1, -1), (-1, -1)]
    for dx, dy in spots:
        if go(x + dx, y + dy, 5):
            return True
    return False


def clear_blockers(rounds=2, radius=2):
    """走不过去时，先把身边的石头/矿石敲掉（矿洞里被石头围住最常见）。"""
    swings = 0
    for _ in range(rounds):
        s = state()
        px, py = pos(s)
        blockers = []
        for t in s.get("tiles") or []:
            if not t.get("breakable") or t.get("interactable"):
                continue
            if abs(t["x"] - px) > radius or abs(t["y"] - py) > radius:
                continue
            name = str(t.get("obj") or "")
            if any(k in name for k in ("石头", "圆石", "矿", "Geode", "Stone", "Boulder", "Rock")):
                blockers.append(t)
        if not blockers:
            break
        t = blockers[0]
        # 距离 2 格敲不到（镐只能打面前 1 格），先走到它旁边
        s2 = state()
        px2, py2 = pos(s2)
        if abs(t["x"] - px2) + abs(t["y"] - py2) > 1:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if go(t["x"] + dx, t["y"] + dy, 4):
                    break
        log("    [开路] 镐敲 (%d,%d) %s" % (t["x"], t["y"], t.get("obj")))
        A.send_order({"actionType": "use_tool", "tool": "pickaxe", "x": t["x"], "y": t["y"]})
        time.sleep(0.6)
        swings += 1
    return swings


def find_tile(s, kinds):
    out = []
    for t in s.get("tiles") or []:
        o = str(t.get("obj") or "")
        if t.get("interactable") and any(k in o for k in kinds):
            out.append(t)
    return out



# 矿石节点（1.6 原版 id）
ORE_IDS = {
    "751",   # Copper Node 铜矿
    "290",   # Iron Node 铁矿
    "764",   # Gold Node 金矿
    "765",   # Iridium Node 铱矿
    "819",   # Geode Node 晶洞
}
# 名称里出现这些才算矿石（注意 "Iron Stone" 是石头，不能算）
ORE_NAME_HINTS = (" Node", "Node", "矿床", "矿石节点", "晶洞", "Geode")


def is_ore(t):
    oid = str(t.get("id") or "")
    name = str(t.get("obj") or "")
    if oid in ORE_IDS:
        return True
    return any(k in name for k in ORE_NAME_HINTS)


def rock_priority(t):
    """矿石优先：0=矿石节点，1=石头杂草。"""
    return 0 if is_ore(t) else 1



_LOG_PATH = None


def log_tail(n=40):
    """读 mod 的控制台日志（优先实时重定向的 smapi_console.log，SMAPI 输出是 UTF-16）。"""
    global _LOG_PATH
    if _LOG_PATH is None:
        cands = [
            r"D:\live\logs\smapi_console.log",
            os.path.join(os.environ.get("APPDATA", ""), r"StardewValley\ErrorLogs\SMAPI-latest.txt"),
        ]
        for c in cands:
            if os.path.isfile(c):
                _LOG_PATH = c
                break
    if not _LOG_PATH:
        return []
    try:
        raw = open(_LOG_PATH, "rb").read()
        # 先试 UTF-16（SMAPI 重定向输出），失败再按 UTF-8
        try:
            txt = raw.decode("utf-16-le")
        except Exception:
            txt = raw.decode("utf-8", "replace")
        return txt.splitlines()[-n:]
    except Exception:
        return []


def can_reach(x, y):
    """让 mod 做 BFS；返回 True/False。"""
    # 直接发请求，不依赖 before 计数
    A.send_order({"actionType": "reachable", "x": x, "y": y})
    time.sleep(0.35)
    for _ in range(6):
        lines = [l for l in log_tail(400) if ("REACH (%d,%d)" % (x, y)) in l]
        if lines:
            return "= yes" in lines[-1]
        time.sleep(0.2)
    return True   # 查不到就当可达，交给 walk 判定


def choose_tool(t):
    name = str(t.get("obj") or "")
    if any(k in name for k in ("石头", "圆石", "矿", "Geode", "Stone", "Boulder", "Rock")):
        return "pickaxe"
    return "axe"


def mine_one(t, tries=3, budget=10.0):
    """走到能敲的位置再敲；走不到就快速放弃。"""
    x, y = t["x"], t["y"]
    tool = choose_tool(t)
    if not can_reach(x, y):
        log("      走不到，跳过")
        return False
    t0 = time.time()
    for _ in range(tries):
        if time.time() - t0 > budget:
            return False
        # 已经在可敲范围内？
        s = state()
        px, py = pos(s)
        if abs(px - x) + abs(py - y) > 2:
            if not go(x, y, 5) and not stand_next(x, y):
                # 走不过去：先把挡路的石头敲掉，再试一次
                if clear_blockers():
                    if not go(x, y, 5) and not stand_next(x, y):
                        continue
                else:
                    continue
        s = state()
        px, py = pos(s)
        if abs(px - x) + abs(py - y) > 2:
            continue          # 还没到位，别浪费一镐
        A.send_order({"actionType": "use_tool", "tool": tool, "x": x, "y": y})
        time.sleep(0.8)
        s = state()
        after = next((z for z in s["tiles"] if z["x"] == x and z["y"] == y), None)
        if after is None or not after.get("breakable"):
            return True
    return False


def elevator_to(level, limit=8):
    """在当前层找到电梯并前往指定层。"""
    cur = state()
    if str(level) in str(cur.get("location")):
        log("    已在 %d 层，跳过电梯" % level)
        return True
    for _ in range(limit):
        s = state()
        ele = find_tile(s, ["电梯"])
        if not ele:
            log("    本层没有电梯")
            return False
        e = ele[0]
        if not stand_next(e["x"], e["y"]):
            go(e["x"], e["y"], 12)
        A.send_order({"actionType": "interact", "x": e["x"], "y": e["y"]})
        time.sleep(2)
        A.send_order({"actionType": "elevator_go", "level": level})
        time.sleep(6)
        s2 = state()
        log("    电梯 → %s %s" % (s2.get("location"), pos(s2)))
        if str(level) in str(s2.get("location")):
            return True
    return False


def descend():
    """找下梯子(173)/竖井(174)并交互下去。"""
    s = state()
    downs = find_tile(s, ["下梯子", "竖井"])
    if not downs:
        return False
    d = downs[0]
    log("    发现 %s (%d,%d)，交互下矿" % (d["obj"], d["x"], d["y"]))
    stand_next(d["x"], d["y"])
    A.send_order({"actionType": "interact", "x": d["x"], "y": d["y"]})
    time.sleep(5)
    s2 = state()
    log("    下去后: %s %s" % (s2.get("location"), pos(s2)))
    return True


FAILED = set()
LAST_DAY = None
STUCK_SINCE = [time.time()]
LAST_PROGRESS = [time.time()]


def mine_level(max_tiles=45):
    """挖矿顺序：① 先挖光矿石 → ② 看下梯子 → ③ 没梯子就挖石头直到出梯子 → 下去。"""
    ores_done = 0
    stones_done = 0
    for _ in range(max_tiles):
        s = state()
        rocks = [t for t in s.get("tiles") or []
                 if t.get("breakable") and (t["x"], t["y"]) not in FAILED]
        ores = [t for t in rocks if is_ore(t)]
        ladder = find_tile(s, ["下梯子", "竖井"])

        def nearest(lst):
            lst = sorted(lst, key=lambda t: abs(t["x"] - pos(s)[0]) + abs(t["y"] - pos(s)[1]))
            return lst[0] if lst else None

        # ① 还有矿石 → 继续挖矿石
        if ores:
            t = nearest(ores)
            log("    [矿] (%d,%d) %s" % (t["x"], t["y"], t.get("obj")))
            if mine_one(t):
                ores_done += 1
                LAST_PROGRESS[0] = time.time()
            else:
                FAILED.add((t["x"], t["y"]))
                log("      敲不动，跳过")
            continue

        # ② 矿石挖光了 → 有下梯子就下去
        if ores_done > 0 or ladder:
            log("    矿石已挖完（%d 块），找下梯子" % ores_done)
        if ladder:
            log("    本层挖矿结束：矿石 %d 块、石头 %d 块，下去" % (ores_done, stones_done))
            LAST_PROGRESS[0] = time.time()
            return descend()

        # ③ 没梯子 → 挖石头直到出梯子
        t = nearest(rocks)
        if t is None:
            log("    石头也没了，且没有下梯子（死局）")
            return False
        log("    [石] (%d,%d) %s（等下梯子）" % (t["x"], t["y"], t.get("obj")))
        if mine_one(t):
            stones_done += 1
            LAST_PROGRESS[0] = time.time()
        else:
            FAILED.add((t["x"], t["y"]))
            log("      敲不动，跳过")

    s = state()
    ladder = find_tile(s, ["下梯子", "竖井"])
    if ladder:
        log("    达到上限：矿石 %d 块、石头 %d 块，下去" % (ores_done, stones_done))
        return descend()
    return False


def enter_mine():
    s = state()
    if in_mine(s.get("location")) and pos(s)[0] > 0:
        return True
    log("→ 按固定路线去矿洞（绕开 BusStop）")
    if A.travel_to("Mine", hop_timeout=220, log_fn=log):
        return in_mine(state().get("location")) and pos(state())[0] > 0
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--levels", type=str, default="40,45,50")
    ap.add_argument("--until", type=str, default="", help="到点收工 HH:MM")
    args = ap.parse_args()
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    log("===== 多层级挖矿开始 levels=%s =====" % levels)
    try:
        A.morning_tidy(log_fn=log)
    except Exception as e:
        log("整理背包异常: %s" % e)
    try:
        if not A.wait_out_festival(log_fn=log):
            log("  节日未结束，跳过本次活动")
            return
    except Exception as e:
        log("  节日处理异常: %s" % e)
    if not enter_mine():
        log("进不去矿洞")
        return
    lvl_idx = 0
    for i in range(1, args.rounds + 1):
        A.send_order({"actionType": "close_menu"})   # 防菜单卡住
        s = state()
        A.send_order({"actionType": "ensure_tools"})
        time.sleep(1)
        log("[%d] %s %s 体力%s 时间%s 天%s" % (i, s.get("location"), pos(s), s.get("stamina"), s.get("time"), s.get("day")))
        if args.until:
            cur = A._mins(s.get("time"))
            if cur >= A._mins(args.until):
                log("  到点收工（%s >= %s），回家" % (s.get("time"), args.until))
                return
        try:
            if A.handle_midnight(log_fn=log):
                log("  已到 00:00，回家睡觉，挖矿结束")
                return
        except Exception as e:
            log("  00:00 检查异常: %s" % e)
        global LAST_DAY
        if LAST_DAY is not None and s.get("day") != LAST_DAY:
            log("跨天了（可能晕倒回屋），重新进矿洞")
            if not enter_mine():
                log("进不去，结束")
                break
        LAST_DAY = s.get("day")
        if (s.get("stamina") or 0) < 40:
            log("体力不足，回农场")
            A.send_order({"actionType": "goto", "location": "Farm"})
            break
        # 每 3 轮换一层（电梯）
        if i % 3 == 1:
            target = levels[lvl_idx % len(levels)]
            lvl_idx += 1
            if "Mine" == s.get("location") or "UndergroundMine" in str(s.get("location")):
                elevator_to(target)
        try:
            ok = mine_level()
        except Exception as ex:
            log("    挖矿异常: %s" % ex)
            ok = False
        # 卡死恢复：120 秒没有任何进度 → 直接换层
        idle = time.time() - LAST_PROGRESS[0]
        if idle > 120:
            target = levels[lvl_idx % len(levels)]
            lvl_idx += 1
            log("    卡住 %.0f 秒无进度，直接跳到 %d 层" % (idle, target))
            A.send_order({"actionType": "elevator_go", "level": target})
            time.sleep(6)
            FAILED.clear()
            LAST_PROGRESS[0] = time.time()
            continue
        if not ok:
            target = levels[lvl_idx % len(levels)]
            lvl_idx += 1
            log("    本层没下成，跳到 %d 层" % target)
            A.send_order({"actionType": "elevator_go", "level": target})
            time.sleep(6)
            FAILED.clear()
    log("===== 多层级挖矿结束 =====")


if __name__ == "__main__":
    main()
