#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""矿洞自动：进矿洞 → 敲石头/矿石 → 找梯子下一层。

用法：python stardew_mine.py --rounds 20
日志：D:\\live\\logs\\mine.log
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stardew_agent as A  # noqa: E402

LOG = r"D:\live\logs\mine.log"
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


def wait_loaded(timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            s = state()
            if pos(s)[0] > 0:
                return s
        except Exception:
            pass
        time.sleep(1)
    return state()


def go(x, y, timeout=25):
    s = state()
    px, py = pos(s)
    if abs(px - x) + abs(py - y) <= 1:
        return True
    A.send_order({"actionType": "walk_to", "x": x, "y": y})
    return A.wait_pos((x, y), timeout=min(timeout, 3 + (abs(px - x) + abs(py - y)) * 0.4))


def stand_next_to(x, y):
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        if go(x + dx, y + dy, 8):
            return True
    return False


def choose_tool(t):
    name = str(t.get("obj") or "")
    if any(k in name for k in ("石头", "Stone", "圆石", "Boulder", "矿", "Geode")):
        return "pickaxe"
    return "axe"


def mine_one(t, tries=3):
    x, y = t["x"], t["y"]
    tool = choose_tool(t)
    for _ in range(tries):
        if not go(x, y, 12) and not stand_next_to(x, y):
            return False
        A.send_order({"actionType": "use_tool", "tool": tool, "x": x, "y": y})
        time.sleep(1.0)
        s = state()
        after = next((z for z in s["tiles"] if z["x"] == x and z["y"] == y), None)
        if after is None or not after.get("breakable"):
            return True
    return False



MINE_LADDER = (23, 9)      # 矿洞大堂里「下矿」机关
MINE_ELEVATOR = (17, 3)    # 电梯


def descend():
    """大堂走 (23,9) 下矿；矿层里找梯子对象。"""
    s = state()
    loc = str(s.get("location") or "")
    if loc == "Mine":
        x, y = MINE_LADDER
        log(f"  → 大堂下矿口 ({x},{y})")
        for cand in ((x - 1, y), (x + 1, y), (x, y + 1), (x, y - 1), (x - 2, y), (x, y + 2)):
            if go(cand[0], cand[1], 15):
                break
        A.send_order({"actionType": "interact", "x": x, "y": y})
        time.sleep(4)
        s2 = state()
        log(f"  交互后: {s2.get('location')} {pos(s2)}")
        return str(s2.get("location") or "") != loc

    # 矿层：找梯子（interactable）
    ladder = next((t for t in s.get("tiles") or [] if t.get("interactable")), None)
    if not ladder:
        # 往地图中心走一点，找更多石头
        px, py = pos(s)
        A.send_order({"actionType": "walk_to", "x": px + 3, "y": py + 3})
        time.sleep(3)
        return False
    lx, ly = ladder["x"], ladder["y"]
    log(f"  发现梯子 ({lx},{ly})，走过去")
    for cand in ((lx - 1, ly), (lx + 1, ly), (lx, ly + 1), (lx, ly - 1)):
        if go(cand[0], cand[1], 15):
            break
    A.send_order({"actionType": "interact", "x": lx, "y": ly})
    time.sleep(4)
    s2 = state()
    log(f"  下梯后: {s2.get('location')} {pos(s2)}")
    return True


def in_mine(loc):
    loc = str(loc or "")
    return loc == "Mine" or loc.startswith("UndergroundMine")


def enter_mine():
    s = state()
    if in_mine(s.get("location")) and pos(s)[0] > 0:
        return True
    log("→ 走路去矿洞（约 3 分钟）")
    A.send_order({"actionType": "goto", "location": "Mine"})
    t0 = time.time()
    while time.time() - t0 < 300:
        time.sleep(3)
        s = state()
        if in_mine(s.get("location")) and pos(s)[0] > 0:
            log("到矿洞 用时 %ds（%s）" % (int(time.time() - t0), s.get("location")))
            return True
    log("没走到矿洞（现在 %s）" % state().get("location"))
    return False


def mine_round(n_tiles=6):
    """敲附近石头，然后找梯子。"""
    s = state()
    loc = s.get("location")
    rocks = [t for t in s.get("tiles") or [] if t.get("breakable")]
    rocks.sort(key=lambda t: abs(t["x"] - pos(s)[0]) + abs(t["y"] - pos(s)[1]))
    log(f"[{loc}] 附近石头 {len(rocks)} 个，体力 {s.get('stamina')}")
    done = 0
    for t in rocks[:n_tiles]:
        ok = mine_one(t)
        log(f"  敲 ({t['x']},{t['y']}) {t.get('obj')} → {'成功' if ok else '失败'}")
        if ok:
            done += 1
    # 找梯子/洞口
    s = state()
    ladder = next((t for t in s.get("tiles") or [] if t.get("interactable")), None)
    if ladder:
        log(f"  发现梯子/洞口 ({ladder['x']},{ladder['y']})，下去")
        stand_next_to(ladder["x"], ladder["y"])
        A.send_order({"actionType": "interact", "x": ladder["x"], "y": ladder["y"]})
        time.sleep(3)
    else:
        log("  没看到梯子，尝试走下矿口")
        descend()
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=10)
    a = ap.parse_args()
    log("===== 挖矿开始 =====")
    if not enter_mine():
        return
    wait_loaded()
    for i in range(1, a.rounds + 1):
        s = state()
        log(f"[{i}] 位置 {pos(s)} 体力 {s.get('stamina')} 时间 {s.get('time')}")
        # 每轮校验工具，防止工具被异常替换
        A.send_order({"actionType": "ensure_tools"})
        time.sleep(1.0)
        if (s.get("stamina") or 0) < 30:
            log("体力不足，回农场")
            A.send_order({"actionType": "goto", "location": "Farm"})
            break
        mine_round()
        time.sleep(1)
    log("===== 挖矿结束 =====")


if __name__ == "__main__":
    main()
