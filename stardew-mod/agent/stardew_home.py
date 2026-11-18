#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从矿洞回家：上梯子 → 离开矿洞 → 走路回农场小屋 → 睡觉存档。

用法：python stardew_home.py [--sleep]
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stardew_agent as A  # noqa: E402

LOG = r"D:\live\logs\home.log"
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


def wait_loc(pred, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = state()
        if pred(s):
            return s
        time.sleep(1)
    return state()


def leave_mine_floor():
    """在矿层里：走到上梯子并交互，然后调用 leave_mine 回大堂。"""
    s = state()
    loc = str(s.get("location") or "")
    if loc == "Mine":
        return True
    ups = [t for t in s.get("tiles") or [] if t.get("interactable") and "上梯子" in str(t.get("obj"))]
    if ups:
        u = ups[0]
        log("  上梯子在 (%d,%d)，走过去" % (u["x"], u["y"]))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (0, 0)):
            if go(u["x"] + dx, u["y"] + dy, 8):
                break
        A.send_order({"actionType": "interact", "x": u["x"], "y": u["y"]})
        time.sleep(2)
    A.send_order({"actionType": "leave_mine"})
    s2 = wait_loc(lambda z: str(z.get("location")) == "Mine", 25)
    log("  离开矿层 → %s %s" % (s2.get("location"), pos(s2)))
    return str(s2.get("location")) == "Mine"


def walk_out():
    """矿洞大堂 → 走山路回家。"""
    s = state()
    if str(s.get("location")) == "Mine":
        log("  从矿洞走出（Mountain）")
        A.send_order({"actionType": "goto", "location": "Mountain"})
        wait_loc(lambda z: str(z.get("location")) != "Mine", 40)
        s = state()
        log("  现在 %s" % s.get("location"))
    # 已经在家就直接跳过；否则先走到 Farm 再进屋
    cur = state()
    if str(cur.get("location")) == "FarmHouse":
        log("  已经在 FarmHouse，跳过走路")
        s2 = state()
        log("  到家: %s %s 时间 %s" % (s2.get("location"), pos(s2), s2.get("time")))
        if not args.sleep:
            A.send_order({"actionType": "save"}); time.sleep(5)
        else:
            A.send_order({"actionType": "sleep"}); time.sleep(12)
            s3 = state()
            log("新的一天: 天%s %s" % (s3.get("day"), s3.get("time")))
        log("背包: %s" % state().get("inventory"))
        log("===== 回家结束 =====")
        return
    if str(cur.get("location")) != "Farm":
        log("  走向 Farm（约 2~3 分钟）")
        A.send_order({"actionType": "goto", "location": "Farm"})
        wait_loc(lambda z: str(z.get("location")) == "Farm", 260)

    log("  走到小屋门口 (64,15)")
    go(64, 16, 40)
    for attempt in range(3):
        A.send_order({"actionType": "enter_home"})
        time.sleep(3)
        if str(state().get("location")) == "FarmHouse":
            break
        go(64, 15, 15)

    s2 = state()
    log("  到家: %s %s 时间 %s" % (s2.get("location"), pos(s2), s2.get("time")))
    return str(s2.get("location")) == "FarmHouse"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sleep", action="store_true", help="回家后睡觉到下一天")
    args = ap.parse_args()
    A.send_order({"actionType": "close_menu"})
    time.sleep(1)
    log("===== 回家开始 =====")
    s = state()
    log("起点: %s %s 时间 %s" % (s.get("location"), pos(s), s.get("time")))
    if in_mine(s.get("location")):
        if not leave_mine_floor():
            log("离开矿层失败")
            return
    if not walk_out():
        log("没走回家")
        return
    A.send_order({"actionType": "ensure_tools"})
    time.sleep(1)
    if args.sleep:
        log("睡觉")
        A.send_order({"actionType": "sleep"})
        time.sleep(12)
        s2 = state()
        log("新的一天: 天%s %s" % (s2.get("day"), s2.get("time")))
    else:
        A.send_order({"actionType": "save"})
        time.sleep(6)
        log("已存档")
    s2 = state()
    log("背包: %s" % s2.get("inventory"))
    log("===== 回家结束 =====")


if __name__ == "__main__":
    main()
