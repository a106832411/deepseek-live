#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星露谷「完整流程」机器人：买种 → 耕地 → 播种 → 浇水 → 收获 → 卖货 → 睡觉跨天

规则驱动，不调 LLM（0 token）。依赖 mod 动作：
  player_warp / player_buy_seed / player_till_all / player_plant
  player_water_all / player_harvest_all / player_sell_all / player_eat / player_sleep

用法：
  python stardew_worker2.py --days 4          # 跑 4 个游戏天
  python stardew_worker2.py --days 1 --no-sleep
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
SEED_BUDGET = int(os.environ.get("STARDEW_SEED_BUDGET", "200"))


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
    name = f"{int(time.time()*1000):013d}-w2{SEQ:03d}.json"
    tmp = os.path.join(ACTION_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(action, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(ACTION_DIR, name))


def inv_count(bridge, keyword=None):
    items = (bridge.get("player") or {}).get("inventory") or []
    n = 0
    for it in items:
        if not it:
            continue
        if keyword and keyword.lower() not in str(it.get("name", "")).lower():
            continue
        n += int(it.get("stack") or 1)
    return n


def has_seeds(bridge):
    return inv_count(bridge, "种子") > 0 or inv_count(bridge, "Seeds") > 0


def day_label(b):
    return f"{b.get('season')} 第{b.get('day')}天 {b.get('time')}"


def run_day():
    """跑一个游戏天，返回是否成功跨天。"""
    b = read_bridge()
    if not b:
        log("读不到 bridge")
        return False
    start_day = (b.get("season"), b.get("day"))
    log(f"新的一天：{day_label(b)} 钱={((b.get('player') or {}).get('money'))}")

    # 1) 传送到农场
    send({"actionType": "player_warp", "location": "Farm", "x": 64, "y": 15})
    time.sleep(2.5)
    b = read_bridge()

    # 2) 没种子且没作物才买
    if not has_seeds(b) and int((b.get("player") or {}).get("crops") or 0) == 0:
        money = int((b.get("player") or {}).get("money") or 0)
        count = max(1, min(20, money // 20))
        log(f"买 {count} 个 {CROP}（预算 {SEED_BUDGET}）")
        send({"actionType": "player_buy_seed", "seed": CROP, "count": count})
        time.sleep(1.5)
        b = read_bridge()

    # 3) 耕地（已有作物就不耕）
    crops = int((b.get("player") or {}).get("crops") or 0)
    if crops == 0:
        log("耕地")
        send({"actionType": "player_till_all"})
        time.sleep(2.5)

    # 4) 播种
    if has_seeds(read_bridge()):
        log("播种")
        send({"actionType": "player_plant"})
        time.sleep(2.5)

    # 5) 浇水
    log("浇水")
    send({"actionType": "player_water_all"})
    time.sleep(1.5)

    # 6) 收获 + 卖
    log("收获/卖货")
    send({"actionType": "player_harvest_all"})
    time.sleep(1.5)
    send({"actionType": "player_sell_all"})
    time.sleep(1.5)

    # 7) 体力低就吃东西
    b = read_bridge()
    stamina = int((b.get("player") or {}).get("stamina") or 0)
    if stamina < 80:
        log("吃东西回体力")
        send({"actionType": "player_eat"})
        time.sleep(1)

    # 8) 睡觉跨天
    log("睡觉")
    send({"actionType": "player_sleep"})
    time.sleep(3)
    send({"actionType": "player_sleep"})
    for _ in range(10):
        time.sleep(2)
        b = read_bridge()
        if not b:
            continue
        if (b.get("season"), b.get("day")) != start_day:
            log(f"跨天成功 -> {day_label(b)} 钱={((b.get('player') or {}).get('money'))}")
            return True
    log("睡觉没生效，强制 NewDay 兜底")
    send({"actionType": "player_force_new_day"})
    for _ in range(10):
        time.sleep(2)
        b = read_bridge()
        if not b:
            continue
        if (b.get("season"), b.get("day")) != start_day:
            log(f"强制跨天成功 -> {day_label(b)} 钱={((b.get('player') or {}).get('money'))}")
            return True
    log("强制跨天也失败")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--no-sleep", action="store_true")
    args = ap.parse_args()
    for d in range(args.days):
        log(f"===== 第 {d+1}/{args.days} 天 =====")
        ok = run_day()
        if not ok:
            break


if __name__ == "__main__":
    main()
