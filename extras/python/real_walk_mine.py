import ctypes
import json
import os
import time
from ctypes import wintypes

BRIDGE = r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge"
BRIDGE_FILE = os.path.join(BRIDGE, "bridge_data.json")
ACTION_DIR = os.path.join(BRIDGE, "actions")
user32 = ctypes.windll.user32
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
    name = f"{int(time.time()*1000):013d}-m{SEQ:03d}.json"
    tmp = os.path.join(ACTION_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(ACTION_DIR, name))


def find_win():
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, l):
        n = user32.GetWindowTextLengthW(h)
        b = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, b, n + 1)
        if "Stardew Valley" in b.value:
            found.append(h)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def focus(h):
    user32.ShowWindow(h, 9)
    user32.keybd_event(0x12, 0, 0, 0)
    user32.SetForegroundWindow(h)
    user32.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.4)


def key_press(vk, hold=0.08):
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(hold)
    user32.keybd_event(vk, 0, 2, 0)


def tile_of(b):
    t = b["player"]["tile"]
    return int(t["x"]), int(t["y"])


def walk_check(tx, ty, timeout=10):
    """真实寻路走过去，并验证是否真的到了。"""
    send({"actionType": "player_walk_to", "x": tx, "y": ty})
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.4)
        b = rd()
        if not b:
            continue
        if tile_of(b) == (tx, ty):
            return True
    return False



_win_origin = [0, 0]


def client_origin(h):
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(h, ctypes.byref(pt))
    return pt.x, pt.y


def click_tile(h, tx, ty):
    b = rd()
    vp = (b.get("player") or {}).get("viewport") or {}
    ox, oy = client_origin(h)
    sx = ox + (tx * 64 + 32 - int(vp.get("x") or 0))
    sy = oy + (ty * 64 + 32 - int(vp.get("y") or 0))
    user32.SetCursorPos(int(sx), int(sy))
    time.sleep(0.08)
    user32.mouse_event(0x02, 0, 0, 0, 0)
    time.sleep(0.08)
    user32.mouse_event(0x04, 0, 0, 0, 0)
    time.sleep(0.15)


def main():
    h = find_win()
    focus(h)
    b = rd()
    log(f"开始: {b.get('location')} {tile_of(b)} xp={b['player'].get('xp')}")

    if b.get("location") != "Farm":
        send({"actionType": "player_warp", "location": "Farm", "x": 64, "y": 15})
        time.sleep(3)
        b = rd()
    log(f"在农场: {tile_of(b)}")

    mined = 0
    tried = set()
    for _ in range(40):
        b = rd()
        if not b:
            continue
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        # 找可破坏目标 + 可站邻格
        candidates = []
        for s in tiles:
            if not s.get("Breakable"):
                continue
            key = (s["X"], s["Y"])
            if key in tried:
                continue
            for dx, dy, face in ((0, -1, 2), (1, 0, 1), (0, 1, 0), (-1, 0, 3)):
                c = next((t for t in tiles if t["X"] == s["X"] + dx and t["Y"] == s["Y"] + dy), None)
                if c and c.get("Passable") and not c.get("IsWater"):
                    candidates.append((s, c, face))
                    break
        if not candidates:
            log("周围没有可达目标")
            break
        s, stand, face = candidates[0]
        tried.add((s["X"], s["Y"]))
        log(f"目标 {s['X']},{s['Y']} 站位 {stand['X']},{stand['Y']}")
        ok = walk_check(stand["X"], stand["Y"], timeout=8)
        b = rd()
        log(f"  走到: {tile_of(b)} ok={ok} type={s.get('ObjectType')}")
        if not ok:
            continue
        ot = str(s.get("ObjectType") or "").lower()
        toolname = "axe" if ot in ("weed", "twig", "tree", "stump", "log") else "pickaxe"
        send({"actionType": "player_select_tool", "tool": toolname})
        time.sleep(0.4)
        send({"actionType": "player_face_tile", "x": s["X"], "y": s["Y"]})
        time.sleep(0.3)
        xp0 = list(b["player"].get("xp") or [])
        hwnd = find_win()
        for _ in range(4):
            click_tile(hwnd, s["X"], s["Y"])  # 真实鼠标点击石头
            time.sleep(0.9)
        b = rd()
        log(f"  挥镐后 xp={b['player'].get('xp')} (前 {xp0})")
        still = any(t["X"] == s["X"] and t["Y"] == s["Y"] and t.get("Breakable")
                    for t in (b.get("surroundings") or {}).get("Tiles") or [])
        log(f"  目标还在={still}")
        send({"actionType": "collect_debris"})
        time.sleep(0.8)
        mined += 1
        if mined >= 3:
            break

    b = rd()
    log("最终背包: " + " | ".join(f"{i['name']}x{i['stack']}" for i in (b['player'].get("inventory") or []) if i))


if __name__ == "__main__":
    main()
