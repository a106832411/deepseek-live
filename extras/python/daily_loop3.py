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


def ship_and_sleep(max_wait=150):
    send({"actionType": "player_warp", "location": "Farm", "x": 64, "y": 15}, 1.5)
    send({"actionType": "player_ship_all"}, 0.8)
    b = rd()
    log("  已装船，背包: " + " | ".join(f"{x['name']}x{x['stack']}" for x in (b["player"].get("inventory") or []) if x))
    send({"actionType": "player_warp", "location": "FarmHouse", "x": 9, "y": 9}, 1.5)
    # 等到晚上 18:00
    for _ in range(max_wait):
        b = rd()
        if b and b.get("time", 0) >= 1800:
            break
        live()
        time.sleep(3)
    b = rd()
    log(f"  睡觉前 day={b.get('day')} time={b.get('time')}")
    send({"actionType": "close_menu"}, 0.3)
    send({"actionType": "reset_state"}, 0.3)
    send({"actionType": "player_sleep_bed"}, 2)
    time.sleep(16)
    b = rd()
    log(f"  醒来 day={b.get('day')} time={b.get('time')} money={b.get('player', {}).get('money')}")
    send({"actionType": "close_menu"}, 0.5)
    b = rd()
    return b


def main(days=2):
    for d in range(days):
        live()
        b = rd()
        log(f"=== 第{d+1}天 day={b.get('day')} time={b.get('time')} money={b.get('player', {}).get('money')}")
        mine_phase(20)
        ship_and_sleep()
        b = rd()
        log(f"=== 第{d+1}天结束 day={b.get('day')} money={b.get('player', {}).get('money')}")


if __name__ == "__main__":
    main(2)
