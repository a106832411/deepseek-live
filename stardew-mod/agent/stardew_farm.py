#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动种菜：取种子 → 锄地 → 播种（浇水由 CJB 自动浇水处理）。

用法：python stardew_farm.py --rows 2 --cols 3
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stardew_agent as A  # noqa: E402

LOG = r"D:\live\logs\farm.log"
_fh = None
SEED_CHEST = (58, 17)


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


def go(x, y, timeout=25):
    s = state()
    px, py = pos(s)
    if abs(px - x) + abs(py - y) <= 1:
        return True
    A.send_order({"actionType": "walk_to", "x": x, "y": y})
    return A.wait_pos((x, y), timeout=min(timeout, 5 + (abs(px - x) + abs(py - y)) * 0.6))


def ensure_farm():
    s = state()
    if str(s.get("location")) != "Farm":
        log("→ 走到 Farm")
        A.send_order({"actionType": "goto", "location": "Farm"})
        t0 = time.time()
        while time.time() - t0 < 120:
            time.sleep(2)
            if str(state().get("location")) == "Farm":
                break
    return str(state().get("location")) == "Farm"


def withdraw_seeds(count=60):
    log("→ 去种子箱 (%d,%d) 取种子" % SEED_CHEST)
    go(SEED_CHEST[0], SEED_CHEST[1] + 1, 30)
    A.send_order({"actionType": "withdraw", "x": SEED_CHEST[0], "y": SEED_CHEST[1],
                  "kind": "seeds", "count": count})
    time.sleep(1.5)
    s = state()
    seeds = [i for i in s["inventory"] if "种子" in i or "Seed" in i]
    log("  背包种子: %s" % (seeds or "无"))
    return bool(seeds)


def till(x, y):
    A.send_order({"actionType": "use_tool", "tool": "hoe", "x": x, "y": y})
    time.sleep(1.2)


def plant(x, y):
    A.send_order({"actionType": "plant", "x": x, "y": y, "seed": ""})
    time.sleep(1.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=2)
    ap.add_argument("--cols", type=int, default=3)
    a = ap.parse_args()
    log("===== 种菜开始 =====")
    if not ensure_farm():
        log("不在农场，结束")
        return
    A.send_order({"actionType": "ensure_tools"})
    time.sleep(1)
    if not withdraw_seeds():
        log("没有种子可用，结束")
        return

    s = state()
    cand = [(t["x"], t["y"]) for t in (s.get("dry_tiles") or [])]
    # 优先选离种子箱近、并且彼此靠近的地块（方便连续种植）
    cx, cy = SEED_CHEST
    cand.sort(key=lambda t: abs(t[0] - cx) + abs(t[1] - cy))
    log("可用空地 %d 块，最近的: %s" % (len(cand), cand[:10]))
    planted = 0
    for (x, y) in cand[: a.rows * a.cols]:
        reached = go(x, y, 20)
        if not reached:
            for dx, dy in ((0, 1), (1, 0), (-1, 0), (0, -1)):
                if go(x + dx, y + dy, 10):
                    reached = True
                    break
        if not reached:
            log("  走不到 (%d,%d)，跳过" % (x, y))
            continue
        till(x, y)
        time.sleep(0.5)
        plant(x, y)
        s2 = state()
        t = next((z for z in s2.get("tiles") or [] if z["x"] == x and z["y"] == y), None)
        if t and t.get("crop"):
            planted += 1
            log("  种下 (%d,%d) → %s" % (x, y, t.get("crop")))
        else:
            log("  (%d,%d) 没种上（%s）" % (x, y, t.get("obj") if t else "无"))
    log("本次种下 %d 株" % planted)
    A.send_order({"actionType": "save"})
    time.sleep(5)
    log("===== 种菜结束 =====")


if __name__ == "__main__":
    main()
