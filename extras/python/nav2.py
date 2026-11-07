import json
import os
import time

BRIDGE = r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge"
BRIDGE_FILE = os.path.join(BRIDGE, "bridge_data.json")
ACTION_DIR = os.path.join(BRIDGE, "actions")
SEQ = 0

SAFE = {
    "FarmHouse": (9, 9), "Farm": (64, 15), "BusStop": (11, 23),
    "Town": (30, 50), "Mountain": (30, 20), "Backwoods": (30, 20),
    "Mine": (18, 14), "UndergroundMine1": (10, 10), "Beach": (30, 30),
    "Forest": (60, 20), "Blacksmith": (8, 10), "SeedShop": (6, 20),
}

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


def safe_check():
    """位置跑飞（走出地图/负数坐标）时，用 set_tile 放回该地图的安全格。"""
    b = rd()
    if not b:
        return b
    t = tile(b)
    if abs(t[0]) > 150 or abs(t[1]) > 150:
        loc = b.get("location")
        sx, sy = SAFE.get(loc, (20, 20))
        log(f"  ⚠ 位置异常 {t} @{loc}，放回 ({sx},{sy})")
        send({"actionType": "set_tile", "x": sx, "y": sy}, 0.8)
        b = rd()
    return b


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


def hop(target, timeout=60):
    """走到本图通往 target 的出口，走到后再触发过图（不穿墙、不越界）。"""
    for attempt in range(3):
        live()
        b = safe_check()
        cur_loc = b.get("location")
        if cur_loc == target:
            return True
        # 1) 真实走路到出口（地图内）
        send({"actionType": "walk_to_target", "target": target}, 0.4)
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(0.4)
            b = rd()
            if b and b.get("location") == target:
                return True
        b = safe_check()
        if b and b.get("location") == target:
            return True
        # 2) 人已走到出口附近，再触发过图（等价于踩上 warp 格）
        time.sleep(1.5)
        send({"actionType": "enter_exit", "target": target}, 0.8)
        t0 = time.time()
        while time.time() - t0 < 12:
            time.sleep(0.5)
            b = rd()
            if b and b.get("location") == target:
                return True
        b = safe_check()
        log(f"  过图失败（loc={b.get('location')} tile={tile(b)}），重试 {attempt+1}/3")
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
        safe_check()
    return True


if __name__ == "__main__":
    live()
    log("起点 " + str((rd() or {}).get("location")))
    ok = goto("Mountain")
    b = rd()
    log(f"goto Mountain = {ok}, 现在 {b.get('location')} {tile(b)}")
