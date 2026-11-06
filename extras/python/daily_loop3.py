import json
import os
import time

BRIDGE = r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge"
BRIDGE_FILE = os.path.join(BRIDGE, "bridge_data.json")
ACTION_DIR = os.path.join(BRIDGE, "actions")
SEQ = 0


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def rd():
    for _ in range(20):
        try:
            with open(BRIDGE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            time.sleep(0.08)
    return None


def send(a, wait=0.5):
    global SEQ
    SEQ += 1
    os.makedirs(ACTION_DIR, exist_ok=True)
    name = f"{int(time.time()*1000):013d}-m{SEQ:03d}.json"
    tmp = os.path.join(ACTION_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(ACTION_DIR, name))
    time.sleep(wait)


def live():
    send({"actionType": "set_focus_pause", "disabled": True}, 0.2)
    send({"actionType": "reset_state"}, 0.2)
    send({"actionType": "close_menu"}, 0.2)


def money():
    return (rd() or {}).get("player", {}).get("money")


def mine_phase(max_mined=20):
    b = None
    for lvl in range(1, 9):
        live()
        send({"actionType": "enter_mine", "level": lvl}, 2.2)
        b = rd()
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        if any(t.get("Breakable") for t in tiles):
            log(f"  进矿 {lvl} 层，可挖 {sum(1 for t in tiles if t.get('Breakable'))}")
            break
    mined = 0
    tried = set()
    for _ in range(max_mined * 3):
        if mined >= max_mined:
            break
        b = rd()
        if not b:
            continue
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        cands = []
        for s in tiles:
            if not s.get("Breakable"):
                continue
            if (s["X"], s["Y"]) in tried:
                continue
            cands.append(s)
        if not cands:
            log("  本层没目标，换层")
            break
        s = cands[0]
        tried.add((s["X"], s["Y"]))
        send({"actionType": "player_mine_at", "x": s["X"], "y": s["Y"]}, 0.9)
        send({"actionType": "collect_debris"}, 0.4)
        mined += 1
    log(f"  挖了 {mined} 个目标，金额={money()}")
    return mined


def sell_now():
    before = money()
    send({"actionType": "player_sell_instant"}, 1.0)
    after = money()
    if after and before:
        log(f"  直接卖出，金币 {before} -> {after}")
    return after


def sleep_until_next_day():
    """等到 18:00 后睡床；如果没跨天就关菜单再试。"""
    for _ in range(240):
        b = rd()
        if b and b.get("time", 0) >= 1800:
            break
        send({"actionType": "set_focus_pause", "disabled": True}, 0.12)
        send({"actionType": "reset_state"}, 0.12)
        time.sleep(3)
    b = rd()
    log(f"  准备睡觉 day={b.get('day')} time={b.get('time')}")
    for attempt in range(4):
        send({"actionType": "player_sleep"}, 2.0)
        d0 = (rd() or {}).get("day")
        for _ in range(20):
            time.sleep(3)
            b = rd()
            if b and b.get("day") != d0:
                log(f"  跨天成功 day={b.get('day')} time={b.get('time')} money={b.get('player', {}).get('money')}")
                send({"actionType": "close_menu"}, 0.4)
                return b
        send({"actionType": "close_menu"}, 0.4)
    b = rd()
    log(f"  ⚠ 睡觉失败 day={b.get('day')} time={b.get('time')}")
    return b


def ship_and_sleep():
    send({"actionType": "player_warp", "location": "Farm", "x": 64, "y": 15}, 1.2)
    send({"actionType": "player_ship_all"}, 0.7)
    sell_now()
    send({"actionType": "player_warp", "location": "FarmHouse", "x": 9, "y": 11}, 1.0)
    return sleep_until_next_day()


def main(days=2):
    for d in range(days):
        live()
        b = rd()
        log(f"=== 第{d+1}天 day={b.get('day')} time={b.get('time')} money={b.get('player', {}).get('money')}")
        mine_phase(45)
        ship_and_sleep()
        b = rd()
        log(f"=== 第{d+1}天结束 day={b.get('day')} money={b.get('player', {}).get('money')}")


if __name__ == "__main__":
    main(2)
