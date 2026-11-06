import ctypes
import json
import os
import time
from ctypes import wintypes

BRIDGE = r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge"
BRIDGE_FILE = os.path.join(BRIDGE, "bridge_data.json")
ACTION_DIR = os.path.join(BRIDGE, "actions")
u = ctypes.windll.user32
SEQ = 0

ROUTES = {
    "FarmHouse": ["Farm"],
    "Farm": ["BusStop", "Mountain"],
    "BusStop": ["Town"],
    "Town": ["Beach", "Mountain", "BusStop"],
    "Mountain": ["Town", "Farm"],
    "Beach": ["Town"],
    "Blacksmith": ["Town"],
}


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
    name = f"{int(time.time()*1000):013d}-n{SEQ:03d}.json"
    tmp = os.path.join(ACTION_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(ACTION_DIR, name))
    time.sleep(wait)


def find_game():
    res = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, l):
        n = u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if "Stardew Valley 1.6.15" in b.value:
                res.append(h)
        return True

    u.EnumWindows(cb, 0)
    return res[0] if res else None


def client_origin(h):
    pt = wintypes.POINT(0, 0)
    u.ClientToScreen(h, ctypes.byref(pt))
    return pt.x, pt.y


def click_screen(x, y):
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.07)
    u.mouse_event(0x02, 0, 0, 0, 0)
    time.sleep(0.09)
    u.mouse_event(0x04, 0, 0, 0, 0)


def live():
    send({"actionType": "set_focus_pause", "disabled": True}, 0.12)
    send({"actionType": "reset_state"}, 0.12)
    send({"actionType": "close_menu"}, 0.12)


def loc():
    b = rd()
    return (b or {}).get("location")


def tile_of(b):
    t = b["player"]["tile"]
    return int(t["x"]), int(t["y"])


def hop(target, timeout=50):
    """走到本图的出口（真实走路），直到进入 target 地图。"""
    for attempt in range(4):
        live()
        send({"actionType": "walk_to_target", "target": target}, 0.6)
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(1.5)
            b = rd()
            if b and b.get("location") == target:
                return True
        b = rd()
        log(f"    hop {target} 未到（现在 {(b or {}).get('location')}），重试 {attempt+1}/4")
    return False


def route_to(target):
    """BFS 用硬编码相邻表算路径。"""
    start = loc()
    if start == target:
        return []
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
    if loc() == target:
        return True
    path = route_to(target)
    if not path:
        log(f"  ⚠ 没有从 {loc()} 到 {target} 的路线")
        return False
    log(f"  走路路线: {loc()} -> {' -> '.join(path)}")
    for hop_target in path:
        if not hop(hop_target):
            return False
    return True


def walk_until(tx, ty, timeout=10):
    send({"actionType": "player_walk_to", "x": tx, "y": ty}, 0.3)
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.3)
        b = rd()
        if b and tile_of(b) == (tx, ty):
            return True
    return False


def fish_here(h, attempts=3):
    """找水边可站格，走过去，面朝水，真实点击抛竿/钩鱼。"""
    send({"actionType": "player_select_tool", "tool": "fishingrod"}, 0.3)
    # 如果附近没有水，往海滩中间走一段直到看到水面
    for _ in range(6):
        b = rd()
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        if any(t.get("IsWater") for t in tiles):
            break
        for wp in ((30, 45), (30, 30), (35, 25), (25, 35), (40, 45)):
            walk_until(wp[0], wp[1], 6)
            b = rd()
            tiles = (b.get("surroundings") or {}).get("Tiles") or []
            if any(t.get("IsWater") for t in tiles):
                break
    caught = 0
    for i in range(attempts):
        live()
        b = rd()
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        me = tile_of(b)
        spots = []
        for w in tiles:
            if not w.get("IsWater"):
                continue
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                st = next((t for t in tiles if t["X"] == w["X"] + dx and t["Y"] == w["Y"] + dy and t.get("Passable")), None)
                if st:
                    spots.append((abs(st["X"] - me[0]) + abs(st["Y"] - me[1]), w, st))
                    break
        spots.sort(key=lambda x: x[0])
        if not spots:
            log("  附近没有可站位的水")
            return caught
        done = False
        for dist, w, st in spots[:6]:
            log(f"  试水格 {w['X']},{w['Y']} 站位 {st['X']},{st['Y']}")
            if not walk_until(st["X"], st["Y"], 8):
                continue
            send({"actionType": "player_face_tile", "x": w["X"], "y": w["Y"]}, 0.3)
            b = rd()
            pt2 = tile_of(b)
            vp = (b.get("player") or {}).get("viewport") or {}
            ox, oy = client_origin(h)
            px = ox + pt2[0] * 64 + 32 - int(vp.get("x") or 0)
            py = oy + pt2[1] * 64 + 32 - int(vp.get("y") or 0)
            dx = w["X"] - pt2[0]
            dy = w["Y"] - pt2[1]
            n = max(1, max(abs(dx), abs(dy)))
            ux, uy = dx / n, dy / n
            cast = False
            for scale in (96, 128, 160, 192, 224):
                click_screen(px + ux * scale, py + uy * scale)
                time.sleep(0.85)
                stf = (rd().get("player") or {}).get("fishing") or {}
                if stf.get("inUse") or stf.get("minigame"):
                    cast = True
                    break
            if cast:
                done = True
                break
        if not done:
            log("  抛竿失败")
            continue
        deadline = time.time() + 30
        hooked = False
        while time.time() < deadline:
            stf = (rd().get("player") or {}).get("fishing") or {}
            if stf.get("minigame"):
                break
            if stf.get("nibbling") and not hooked:
                click_screen(wx, wy)
                hooked = True
                time.sleep(0.5)
            time.sleep(0.12)
        deadline = time.time() + 20
        while time.time() < deadline:
            stf = (rd().get("player") or {}).get("fishing") or {}
            if not stf.get("minigame"):
                break
            time.sleep(0.35)
        send({"actionType": "close_menu"}, 0.4)
        caught += 1
        b = rd()
        inv = [x["name"] for x in (b["player"].get("inventory") or []) if x]
        log(f"  第{i+1}次结束，背包={inv}")
    return caught


def main():
    h = find_game()
    live()
    log(f"开始：{loc()}")
    if not goto("Beach"):
        log("到不了海滩，退出")
        return
    log(f"到海滩：{loc()}")
    fish_here(h, 3)
    b = rd()
    inv = [f"{x['name']}x{x['stack']}" for x in (b["player"].get("inventory") or []) if x]
    log(f"最终背包={inv}")


if __name__ == "__main__":
    main()
