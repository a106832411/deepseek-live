#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鲸live · 星露谷完整玩法机器人 v3
每天：买种 → 耕地 → 播种 → 浇水 → 收获 → 卖 → 钓鱼 → 收鱼 → 卖鱼 → 吃饭 → 睡觉跨天

依赖 mod 动作：
  player_warp / player_buy_seed / player_till_all / player_plant / player_water_all
  player_harvest_all / player_sell_instant / player_eat / player_sleep / player_force_new_day
  spawn / set_mode / warp / fish / companion_collect / set_auto_dismiss

用法：
  python stardew_worker3.py --days 3
"""
import argparse
import json
import os
import time

BRIDGE = os.environ.get("STARDEW_BRIDGE_DIR", r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge")
BRIDGE_FILE = os.path.join(BRIDGE, "bridge_data.json")
ACTION_DIR = os.path.join(BRIDGE, "actions")
SEQ = 0
CROP = os.environ.get("STARDEW_CROP", "Wheat Seeds")
FISH_SECONDS = int(os.environ.get("STARDEW_FISH_SECONDS", "45"))


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read_bridge():
    for _ in range(10):
        try:
            with open(BRIDGE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            time.sleep(0.2)
    return None


def send(action):
    global SEQ
    SEQ += 1
    os.makedirs(ACTION_DIR, exist_ok=True)
    name = f"{int(time.time()*1000):013d}-w3{SEQ:03d}.json"
    tmp = os.path.join(ACTION_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(action, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(ACTION_DIR, name))


def money(b):
    return int((b.get("player") or {}).get("money") or 0)


def has_seeds(b):
    for it in (b.get("player") or {}).get("inventory") or []:
        if not it:
            continue
        nm = str(it.get("name") or "")
        if "种子" in nm or "Seeds" in nm:
            if int(it.get("stack") or 0) > 0:
                return True
    return False


def ensure_companions():
    b = read_bridge()
    comps = b.get("companions") or [] if b else []
    if not comps:
        log("生成同伴")
        send({"actionType": "spawn"})
        time.sleep(3)
        send({"actionType": "set_mode", "target": "Companion1", "mode": "player"})
        time.sleep(1)


def farm_morning():
    b = read_bridge()
    log(f"上午农活：{b.get('season')} 第{b.get('day')}天 钱={money(b)}")
    send({"actionType": "player_warp", "location": "Farm", "x": 64, "y": 15})
    time.sleep(2.5)
    b = read_bridge()

    if not has_seeds(b) and int((b.get("player") or {}).get("crops") or 0) == 0:
        cnt = max(1, min(20, money(b) // 20))
        log(f"买 {cnt} 个 {CROP}")
        send({"actionType": "player_buy_seed", "seed": CROP, "count": cnt})
        time.sleep(1.5)
        b = read_bridge()

    if int((b.get("player") or {}).get("crops") or 0) == 0:
        log("耕地")
        send({"actionType": "player_till_all"})
        time.sleep(2.5)

    if has_seeds(read_bridge()):
        log("播种")
        send({"actionType": "player_plant"})
        time.sleep(2.5)

    log("浇水")
    send({"actionType": "player_water_all"})
    time.sleep(1.5)

    log("收获")
    send({"actionType": "player_harvest_all"})
    time.sleep(1.5)


def fishing():
    ensure_companions()
    b = read_bridge()
    if not b.get("companions"):
        log("没有同伴，跳过钓鱼")
        return
    log(f"去海滩钓鱼 {FISH_SECONDS}s")
    send({"actionType": "warp", "location": "Beach", "x": 30, "y": 48})
    time.sleep(3)
    send({"actionType": "fish"})
    time.sleep(FISH_SECONDS)
    send({"actionType": "companion_collect"})
    time.sleep(2)
    send({"actionType": "player_sell_instant"})
    time.sleep(1.5)


def evening():
    b = read_bridge()
    stamina = int((b.get("player") or {}).get("stamina") or 0)
    if stamina < 80:
        log("吃东西回体力")
        send({"actionType": "player_eat"})
        time.sleep(1.2)


def sleep_and_next_day(start_day):
    log("睡觉")
    send({"actionType": "player_sleep"})
    time.sleep(3)
    send({"actionType": "player_sleep"})
    for _ in range(8):
        time.sleep(2)
        b = read_bridge()
        if b and (b.get("season"), b.get("day")) != start_day:
            return True
    log("睡觉没生效，强制 NewDay")
    send({"actionType": "player_force_new_day"})
    for _ in range(8):
        time.sleep(2)
        b = read_bridge()
        if b and (b.get("season"), b.get("day")) != start_day:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1)
    args = ap.parse_args()
    send({"actionType": "set_auto_dismiss", "enabled": True})
    time.sleep(1)
    for d in range(args.days):
        b = read_bridge()
        if not b:
            log("读不到 bridge")
            return
        start = (b.get("season"), b.get("day"))
        m0 = money(b)
        log(f"===== 第 {d+1}/{args.days} 天（钱 {m0}）=====")
        farm_morning()
        fishing()
        evening()
        ok = sleep_and_next_day(start)
        b = read_bridge()
        m1 = money(b) if b else 0
        log(f"===== 第 {d+1} 天结束：钱 {m0} -> {m1}（{'跨天成功' if ok else '跨天失败'}）=====")
        if not ok:
            break


if __name__ == "__main__":
    main()
