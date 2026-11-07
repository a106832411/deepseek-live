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


def click_tile(tx, ty, hold=0.1):
    """真实鼠标点击某个格子（用真实点击触发工具挥动动画）。"""
    import gameio, ctypes
    u = gameio.u
    b = rd()
    if not b:
        return
    vp = (b.get("player") or {}).get("viewport") or {}
    h, r = gameio.game_window()
    if not h:
        return
    gameio.focus(h)
    x = r.left + tx * 64 + 32 - int(vp.get("x") or 0)
    y = r.top + ty * 64 + 32 - int(vp.get("y") or 0)
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.06)
    u.mouse_event(0x02, 0, 0, 0, 0)
    time.sleep(hold)
    u.mouse_event(0x04, 0, 0, 0, 0)
    time.sleep(0.05)


def live():
    send({"actionType": "set_focus_pause", "disabled": True}, 0.12)
    send({"actionType": "reset_state"}, 0.12)
    send({"actionType": "close_menu"}, 0.12)


def tile(b):
    t = b["player"]["tile"]
    return int(t["x"]), int(t["y"])


def walk_until(tx, ty, timeout=10):
    send({"actionType": "player_walk_to", "x": tx, "y": ty}, 0.25)
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.3)
        b = rd()
        if b and tile(b) == (tx, ty):
            return True
    return False


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
        npc_tiles = set()
        for n in (b.get("surroundings") or {}).get("Npcs") or []:
            npc_tiles.add((int(n.get("X", -99)), int(n.get("Y", -99))))
        best = None
        bestd = abs(cur[0] - tx) + abs(cur[1] - ty)
        for s in tiles:
            if not s.get("Passable") or s.get("IsWater"):
                continue
            if (s["X"], s["Y"]) in npc_tiles:
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


def hop(target, timeout=30):
    """走到本图通往 target 的出口；一到出口立刻触发过图，不干等。"""
    for attempt in range(3):
        live()
        b = safe_check()
        cur_loc = b.get("location")
        if cur_loc == target:
            return True
        e = find_exit(b, target)
        if not e:
            log(f"  {cur_loc} 没有通往 {target} 的出口")
            return False
        ex, ey = int(e["x"]), int(e["y"])
        gx, gy = max(ex, 0), max(ey, 0)
        send({"actionType": "walk_to_target", "target": target}, 0.3)
        t0 = time.time()
        fired = False
        while time.time() - t0 < timeout:
            time.sleep(0.25)
            b = rd()
            if not b:
                continue
            if b.get("location") == target:
                return True
            t = tile(b)
            if abs(t[0] - gx) + abs(t[1] - gy) <= 1 and not fired:
                send({"actionType": "enter_exit", "target": target}, 0.6)
                fired = True
            if fired and b.get("location") == target:
                return True
        b = safe_check()
        if b.get("location") == target:
            return True
        time.sleep(1.0)
        avoid_npc()          # 先试着绕开挡路的宠物/NPC
        broke = break_obstacles(gx, gy, 18)
        log(f"  过图失败（loc={b.get('location')} tile={tile(b)}），清了 {broke} 个障碍，重试 {attempt+1}/3")
    return False



def _tool_for(obj_type=None, terrain=None):
    ot = (obj_type or "").lower()
    if terrain == "tree" or ot in ("tree", "stump", "log", "twig", "weed"):
        return "axe"
    return "pickaxe"


def avoid_npc(tries=6):
    """被宠物/NPC挡住时，往旁边挪两步再继续（不要去挤它）。"""
    for _ in range(tries):
        b = safe_check()
        if not b:
            return False
        cur = tile(b)
        npcs = [(int(n.get("X", -99)), int(n.get("Y", -99))) for n in (b.get("surroundings") or {}).get("Npcs") or []]
        near = [n for n in npcs if abs(n[0] - cur[0]) + abs(n[1] - cur[1]) <= 2]
        if not near:
            return False
        moved = False
        for dx, dy in ((0, 1), (1, 0), (0, -1), (-1, 0), (1, 1), (-1, -1)):
            for step in (2, 3):
                tx, ty = cur[0] + dx * step, cur[1] + dy * step
                if walk_until(tx, ty, 4):
                    moved = True
                    break
            if moved:
                break
        if not moved:
            time.sleep(1.5)
        log(f"  绕开宠物（在 {near[0]} 附近）")
    return True


def break_obstacles(tx, ty, max_break=25):
    """清掉通往目标方向上的石头/杂草/树枝/树（真实走位 + 挥工具）。"""
    broken = 0
    for _ in range(max_break):
        b = safe_check()
        if not b:
            return broken
        cur = tile(b)
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        target_d = abs(cur[0] - tx) + abs(cur[1] - ty)
        if target_d <= 1:
            return broken
        cands = []
        for s in tiles:
            if not (s.get("Breakable") or s.get("Terrain") in ("tree", "bush")):
                continue
            d = abs(s["X"] - tx) + abs(s["Y"] - ty)
            if d >= target_d:
                continue
            # 需要相邻可站格
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                st = next((t for t in tiles if t["X"] == s["X"] + dx and t["Y"] == s["Y"] + dy and t.get("Passable")), None)
                if st:
                    cands.append((d, abs(st["X"] - cur[0]) + abs(st["Y"] - cur[1]), s, st))
                    break
        if not cands:
            return broken
        cands.sort(key=lambda c: (c[0], c[1]))
        d, sd, s, st = cands[0]
        if not walk_until(st["X"], st["Y"], 8):
            continue
        tool = _tool_for(s.get("ObjectType"), s.get("Terrain"))
        hit_count = 10 if (s.get("Terrain") in ("tree", "bush") or str(s.get("ObjectType") or "").lower() in ("tree", "stump", "log", "twig")) else 5
        send({"actionType": "player_select_tool", "tool": tool}, 0.3)
        send({"actionType": "player_face_tile", "x": s["X"], "y": s["Y"]}, 0.3)
        # 用真实鼠标点击目标格：会有挥工具动画，也能正常砍倒树
        for rnd in range(4):
            for _ in range(hit_count):
                click_tile(s["X"], s["Y"], 0.12)
                time.sleep(0.42)
            send({"actionType": "collect_debris"}, 0.4)
            bb = rd()
            still = any(t["X"] == s["X"] and t["Y"] == s["Y"] and (t.get("Breakable") or t.get("Terrain") in ("tree", "bush"))
                        for t in (bb.get("surroundings") or {}).get("Tiles") or [])
            if not still:
                break
        broken += 1
        log(f"  清障碍 {s['X']},{s['Y']} ({s.get('ObjectType') or s.get('Terrain')})")
    return broken


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
