#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""农场每日循环：收割 → 出货 → 补种 → 存档 → 睡觉，重复 N 天。

用法：python stardew_farmdaily.py --days 4
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stardew_agent as A  # noqa: E402

LOG = r"D:\live\logs\farmdaily.log"
_fh = None
PLOT = (65, 18, 74, 23)     # x1,y1,x2,y2


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


def go(x, y, timeout=20):
    s = state()
    px, py = pos(s)
    if abs(px - x) + abs(py - y) <= 1:
        return True
    A.send_order({"actionType": "walk_to", "x": x, "y": y})
    return A.wait_pos((x, y), timeout=min(timeout, 6 + (abs(px - x) + abs(py - y)) * 0.6))


def ensure_farm():
    s = state()
    if str(s.get("location")) == "Farm":
        return True
    A.send_order({"actionType": "close_menu"})
    time.sleep(0.6)
    if A.travel_to("Farm", hop_timeout=220, log_fn=log):
        return True
    return str(state().get("location")) == "Farm"


def seeds_in_bag():
    s = state()
    return [i for i in s["inventory"] if "Seed" in i or "种子" in i or "Starter" in i]


def harvest_all():
    """镰刀扫荡收割（一次收一片）。"""
    try:
        return A.harvest_with_scythe(log_fn=log)
    except Exception as e:
        log("  收割异常: %s" % e)
        return 0


def plant_plot(max_n=50):
    """把固定农田里空着的格子种上。"""
    if not seeds_in_bag():
        log("  没有种子，跳过补种")
        return 0
    x1, y1, x2, y2 = PLOT
    planted = 0
    for row, y in enumerate(range(y1, y2 + 1)):
        xs = list(range(x1, x2 + 1))
        if row % 2 == 1:
            xs.reverse()
        for x in xs:
            if planted >= max_n:
                return planted
            if not seeds_in_bag():
                log("  种子用完了")
                return planted
            if not go(x, y, 8):
                continue
            s = state()
            t = next((z for z in (s.get("tiles") or []) if z["x"] == x and z["y"] == y), None)
            if t and t.get("crop"):
                continue
            if t and t.get("obj") and not t.get("breakable"):
                continue
            if t and t.get("obj"):
                A.send_order({"actionType": "use_tool", "tool": "scythe", "x": x, "y": y})
                time.sleep(0.9)
            A.send_order({"actionType": "use_tool", "tool": "hoe", "x": x, "y": y})
            time.sleep(1.0)
            A.send_order({"actionType": "plant", "x": x, "y": y, "seed": ""})
            time.sleep(0.8)
            s2 = state()
            t2 = next((z for z in (s2.get("tiles") or []) if z["x"] == x and z["y"] == y), None)
            if t2 and t2.get("crop"):
                planted += 1
    log("  补种 %d 株" % planted)
    return planted


def ship_and_save():
    A.send_order({"actionType": "ship"})
    time.sleep(2)
    A.send_order({"actionType": "ensure_tools"})
    time.sleep(1)
    A.send_order({"actionType": "save"})
    time.sleep(5)


def sleep_day():
    s = state()
    if str(s.get("location")) != "FarmHouse":
        if str(s.get("location")) != "Farm":
            A.travel_to("Farm", hop_timeout=220, log_fn=log)
        go(64, 16, 20)
        for _ in range(3):
            A.send_order({"actionType": "enter_home"})
            time.sleep(3)
            if str(state().get("location")) == "FarmHouse":
                break
    A.send_order({"actionType": "sleep"})
    time.sleep(10)
    # 睡后关掉出货结算等多层菜单
    for _ in range(6):
        A.send_order({"actionType": "close_menu"})
        time.sleep(1.2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4)
    args = ap.parse_args()
    log("===== 农场每日循环 开始（%d 天）=====" % args.days)
    for d in range(1, args.days + 1):
        # 每轮开头关掉残留菜单（睡觉后的出货结算会挡住一切）
        for _ in range(3):
            A.send_order({"actionType": "close_menu"}); time.sleep(0.8)
        s = state()
        log("[第 %d 轮] %s %s 天%s 时间%s 钱%s" % (d, s.get("location"), pos(s), s.get("day"), s.get("time"), s.get("money")))
        if not ensure_farm():
            log("  不在农场，跳过")
            continue
        try:
            A.morning_tidy(log_fn=log)
        except Exception as e:
            log("  整理背包异常: %s" % e)
        try:
            if not A.wait_out_festival(log_fn=log):
                log("  节日未结束，跳过本轮")
                continue
        except Exception as e:
            log("  节日处理异常: %s" % e)
        harvest_all()
        ship_and_save()
        try:
            A.plant_best_crops(60, log_fn=log)
        except Exception as e:
            log("  季节种植异常: %s" % e)
        s2 = state()
        log("  补种后: 钱%s 背包尾 %s" % (s2.get("money"), s2.get("inventory")[-4:]))
        ship_and_save()
        log("  睡觉")
        sleep_day()
        s3 = state()
        log("  → 天%s %s 钱%s" % (s3.get("day"), s3.get("time"), s3.get("money")))
    s = state()
    log("结束: 天%s 钱%s" % (s.get("day"), s.get("money")))
    log("背包: %s" % s.get("inventory"))
    log("===== 农场每日循环 结束 =====")


if __name__ == "__main__":
    main()
