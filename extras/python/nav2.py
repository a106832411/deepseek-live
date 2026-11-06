import json
import os
import time

BRIDGE = r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge"
BRIDGE_FILE = os.path.join(BRIDGE, "bridge_data.json")
ACTION_DIR = os.path.join(BRIDGE, "actions")
SEQ = 0

ROUTES = {
    "FarmHouse": ["Farm"],
    "Farm": ["BusStop", "Backwoods", "Forest"],
    "BusStop": ["Town", "Farm"],
    "Backwoods": ["Farm", "Mountain"],
    "Town": ["Beach", "Mountain", "BusStop", "Blacksmith", "SeedShop"],
    "Mountain": ["Town", "Backwoods", "Farm", "Mine"],
    "Beach": ["Town"],
    "Forest": ["Farm", "Town"],
    "Mine": ["Mountain", "UndergroundMine1"],
    "UndergroundMine1": ["Mine"],
    "Blacksmith": ["Town"],
    "SeedShop": ["Town"],
}


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def rd():
    for _ in range(20):
        try:
            with open(BRIDGE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            time.sleep(0.06)
    return None


def send(a, wait=0.35):
    global SEQ
    SEQ += 1
    os.makedirs(ACTION_DIR, exist_ok=True)
    name = f"{int(time.time()*1000):013d}-v{SEQ:03d}.json"
    tmp = os.path.join(ACTION_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(ACTION_DIR, name))
    time.sleep(wait)


def live():
    send({"actionType": "set_focus_pause", "disabled": True}, 0.12)
    send({"actionType": "reset_state"}, 0.12)
    send({"actionType": "close_menu"}, 0.12)


def tile(b):
    t = b["player"]["tile"]
    return int(t["x"]), int(t["y"])


def step_walk(tx, ty, max_steps=260):
    """一小步一小步走过去（用扫描到的可通行格规划），带防卡死。"""
    last = None
    stuck = 0
    for _ in range(max_steps):
        b = rd()
        if not b:
            continue
        cur = tile(b)
        if abs(cur[0] - tx) + abs(cur[1] - ty) <= 1:
            return True
        if cur == last:
            stuck += 1
            if stuck > 6:
                live()
                stuck = 0
        else:
            stuck = 0
        last = cur
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        best = None
        bestd = abs(cur[0] - tx) + abs(cur[1] - ty)
        for s in tiles:
            if not s.get("Passable") or s.get("IsWater"):
                continue
            step = abs(s["X"] - cur[0]) + abs(s["Y"] - cur[1])
            if step < 1 or step > 2:
                continue
            d = abs(s["X"] - tx) + abs(s["Y"] - ty)
            if d < bestd:
                bestd = d
                best = s
        if best is None:
            return False
        send({"actionType": "player_walk_to", "x": best["X"], "y": best["Y"]}, 0.2)
        time.sleep(0.35)
    return False


def exits_of(b):
    return (b or {}).get("exits") or []


def find_exit(b, target):
    for e in exits_of(b):
        if e.get("target") and target.lower() in str(e["target"]).lower():
            return e
    return None


def hop(target, timeout=120):
    """走到当前地图通往 target 的出口；边界出口走近后用 1 格外推触发换图。"""
    for attempt in range(3):
        live()
        b = rd()
        cur_loc = b.get("location")
        if cur_loc == target:
            return True
        # 先用 mod 的 walk_to_target（对地图内的门口 warp 好用）
        send({"actionType": "walk_to_target", "target": target}, 0.4)
        t0 = time.time()
        while time.time() - t0 < 60:
            time.sleep(0.4)
            b2 = rd()
            if b2 and b2.get("location") == target:
                return True
        b = rd()
        if b.get("location") == target:
            return True
        e = find_exit(b, target)
        if not e:
            log(f"  {cur_loc} 没有通往 {target} 的出口")
            return False
        # 1) 先真实走路到出口（地图内会走过去；边界出口会走到边上）
        send({"actionType": "walk_to_target", "target": target}, 0.4)
        t0 = time.time()
        while time.time() - t0 < 45:
            time.sleep(0.4)
            b = rd()
            if b and b.get("location") == target:
                return True
        # 2) 走到出口/边上后，再触发过图（等角色走到位，避免凭空换图）
        b = rd()
        if b and b.get("location") != cur_loc:
            return b.get("location") == target
        time.sleep(2.5)
        send({"actionType": "enter_exit", "target": target}, 1.0)
        t0 = time.time()
        while time.time() - t0 < 10:
            time.sleep(0.5)
            b = rd()
            if b and b.get("location") == target:
                return True
        log(f"  过图失败（loc={(rd() or {}).get('location')}）")
    return False


def route_to(start, target):
    q = [(start, [])]
    seen = {start}
    while q:
        cur, path = q.pop(0)
        for nxt in ROUTES.get(cur, []):
            if nxt in seen:
                continue
            if nxt == target:
                return path + [nxt]
            seen.add(nxt)
            q.append((nxt, path + [nxt]))
    return None


def goto(target):
    b = rd()
    start = b.get("location")
    if start == target:
        return True
    path = route_to(start, target)
    if not path:
        log(f"  ⚠ 无路线 {start} -> {target}")
        return False
    log(f"  路线: {start} -> {' -> '.join(path)}")
    for h in path:
        if not hop(h):
            return False
    return True


if __name__ == "__main__":
    live()
    log("起点 " + str((rd() or {}).get("location")))
    ok = goto("Mountain")
    b = rd()
    log(f"goto Mountain = {ok}, 现在 {b.get('location')} {tile(b)}")
