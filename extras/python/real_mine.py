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
            time.sleep(0.1)
    return None


def send(a):
    global SEQ
    SEQ += 1
    os.makedirs(ACTION_DIR, exist_ok=True)
    name = f"{int(time.time()*1000):013d}-r{SEQ:03d}.json"
    tmp = os.path.join(ACTION_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(ACTION_DIR, name))


def tile_of(b):
    t = b["player"]["tile"]
    return int(t["x"]), int(t["y"])


def walk_until(tx, ty, timeout=12):
    send({"actionType": "player_walk_to", "x": tx, "y": ty})
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.35)
        b = rd()
        if b and tile_of(b) == (tx, ty):
            return True
    return False


def main():
    b = rd()
    log(f"开始 {b.get('location')} {tile_of(b)}")

    # 进矿（地图切换），挑一个有石头的层
    entered = False
    for lvl in range(1, 8):
        send({"actionType": "enter_mine", "level": lvl})
        time.sleep(6)
        b = rd()
        if not b:
            continue
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        if any(t.get("Breakable") for t in tiles):
            log(f"进矿 {lvl} 层，可破坏 {sum(1 for t in tiles if t.get('Breakable'))}")
            entered = True
            break
    if not entered:
        log("没有找到有石头的矿层")
        return

    done = 0
    tried = set()
    for _ in range(80):
        b = rd()
        if not b:
            continue
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        me = tile_of(b)
        cands = []
        for s in tiles:
            if not s.get("Breakable"):
                continue
            if (s["X"], s["Y"]) in tried:
                continue
            for dx, dy, face in ((1, 0, 1), (-1, 0, 3), (0, 1, 0), (0, -1, 2)):
                st = next((t for t in tiles if t["X"] == s["X"] + dx and t["Y"] == s["Y"] + dy), None)
                if st and st.get("Passable") and not st.get("IsWater"):
                    dist = abs(s["X"] - me[0]) + abs(s["Y"] - me[1])
                    cands.append((dist, s, st, face))
                    break
        if not cands:
            log("附近没有可挖目标")
            break
        cands.sort(key=lambda c: c[0])
        dist, s, st, face = cands[0]
        tried.add((s["X"], s["Y"]))
        log(f"目标 {s['X']},{s['Y']}({s.get('ObjectType')}) 距离{dist} 站位{st['X']},{st['Y']}")
        if not walk_until(st["X"], st["Y"]):
            b2 = rd()
            log(f"  走不到（当前 {tile_of(b2)}），换目标")
            continue
        send({"actionType": "player_select_tool", "tool": "pickaxe"})
        time.sleep(0.3)
        send({"actionType": "player_face_tile", "x": s["X"], "y": s["Y"]})
        time.sleep(0.3)
        xp0 = list((rd()["player"].get("xp")) or [])
        for _ in range(3):
            send({"actionType": "player_use_tool", "tool": "pickaxe", "x": s["X"], "y": s["Y"]})
            time.sleep(0.8)
        b3 = rd()
        still = any(t["X"] == s["X"] and t["Y"] == s["Y"] and t.get("Breakable")
                    for t in (b3.get("surroundings") or {}).get("Tiles") or [])
        log(f"  挖后 目标还在={still} xp={xp0}->{b3['player'].get('xp')}")
        send({"actionType": "collect_debris"})
        time.sleep(0.7)
        done += 1
        if done >= 4:
            break

    b = rd()
    log("背包: " + " | ".join(f"{i['name']}x{i['stack']}" for i in (b['player'].get("inventory") or []) if i))
    log("xp: " + str(b['player'].get("xp")))


if __name__ == "__main__":
    main()
