#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星露谷 · 真实操作版 worker（v4）
- 用走路（PathFindController）代替传送（除地图切换）
- 用真实工具挥动（player_use_tool）、真实使用种子（player_use_seed_at）
- 用真实床睡觉（player_sleep_bed，走路到床边 → 游戏自己的 startSleep）
不再使用「直接加钱 / 直接种 / 强制跨天」等作弊动作。

用法：python stardew_worker4.py --days 1
"""
import argparse
import json
import os
import time

BRIDGE = os.environ.get("STARDEW_BRIDGE_DIR", r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge")
BRIDGE_FILE = os.path.join(BRIDGE, "bridge_data.json")
ACTION_DIR = os.path.join(BRIDGE, "actions")
SEQ = 0

GROW_SPOT = (60, 15)   # 农场耕作区


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def bridge():
    for _ in range(10):
        try:
            with open(BRIDGE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            time.sleep(0.2)
    return None


def send(a):
    global SEQ
    SEQ += 1
    os.makedirs(ACTION_DIR, exist_ok=True)
    name = f"{int(time.time()*1000):013d}-w4{SEQ:03d}.json"
    tmp = os.path.join(ACTION_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(ACTION_DIR, name))


def tile(b):
    p = (b.get("player") or {})
    t = p.get("tile") or {}
    return int(t.get("x") or 0), int(t.get("y") or 0)


def walk_to(x, y, timeout=8):
    send({"actionType": "player_walk_to", "x": x, "y": y})
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.6)
        b = bridge()
        if not b:
            continue
        tx, ty = tile(b)
        if tx == x and ty == y:
            return True
    return False


def wait_ready():
    b = bridge()
    if not b:
        return None
    send({"actionType": "set_focus_pause", "disabled": True})
    send({"actionType": "set_auto_dismiss", "enabled": True})
    time.sleep(0.5)
    return bridge()


def farm_day():
    b = wait_ready()
    log(f"开始：{b.get('season')} 第{b.get('day')}天 loc={b.get('location')} 钱={(b.get('player') or {}).get('money')}")

    # 地图切换（游戏里对应进门/出门，瞬间完成）
    if b.get("location") != "Farm":
        send({"actionType": "player_warp", "location": "Farm", "x": 64, "y": 15})
        time.sleep(3)
        b = bridge()

    x0, y0 = GROW_SPOT
    log(f"走到农田 ({x0},{y0})")
    walk_to(x0, y0)

    # 1) 真实挥锄头开垦 3 格
    spots = [(x0, y0), (x0 + 1, y0), (x0, y0 + 1)]
    for (tx, ty) in spots:
        walk_to(tx, ty, timeout=6)
        send({"actionType": "player_use_tool", "tool": "hoe", "x": tx, "y": ty})
        time.sleep(1.0)
        log(f"锄头开垦 {tx},{ty}")

    # 2) 真实使用种子播种
    for (tx, ty) in spots:
        walk_to(tx, ty, timeout=6)
        send({"actionType": "player_use_seed_at", "seed": "Wheat", "x": tx, "y": ty})
        time.sleep(1.0)
        log(f"播种 {tx},{ty}")

    # 3) 真实浇水（下雨天跳过）
    weather = (bridge() or {}).get("weather") or ""
    if weather == "rain":
        log("今天下雨，跳过浇水")
        spots_water = []
    else:
        spots_water = spots
    for (tx, ty) in spots_water:
        walk_to(tx, ty, timeout=6)
        send({"actionType": "player_use_tool", "tool": "wateringcan", "x": tx, "y": ty})
        time.sleep(0.8)
        log(f"浇水 {tx},{ty}")

    b = bridge()
    log(f"农活完成：钱={(b.get('player') or {}).get('money')} crops={(b.get('player') or {}).get('crops')}")

    # 4) 真实睡觉：走回农舍床边（地图切换用 warp 进农舍），然后走路到床
    send({"actionType": "player_warp", "location": "FarmHouse", "x": 9, "y": 9})
    time.sleep(3)
    log("回农舍，走向床边")
    send({"actionType": "player_sleep_bed"})
    time.sleep(4)
    send({"actionType": "player_sleep_bed"})
    for _ in range(15):
        time.sleep(2)
        b = bridge()
        if b and b.get("day") != (wait_ready() or {}).get("day"):
            pass
    b = bridge()
    log(f"结束：day={b.get('day')} time={b.get('time')} 钱={(b.get('player') or {}).get('money')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1)
    args = ap.parse_args()
    for _ in range(args.days):
        farm_day()


if __name__ == "__main__":
    main()
