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
    "Farm": ["BusStop", "Backwoods", "Forest"],
    "BusStop": ["Town", "Farm"],
    "Backwoods": ["Farm", "Mountain"],
    "Town": ["Beach", "Mountain", "BusStop", "Blacksmith", "SeedShop"],
    "Mountain": ["Town", "Backwoods", "Farm"],
    "Beach": ["Town"],
    "Forest": ["Farm", "Town"],
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
        send({"actionType": "walk_to_target", "target": target}, 0.4)
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(0.4)
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


def _focus_click(h, x, y):
    try:
        if u.GetForegroundWindow() != h:
            u.ShowWindow(h, 9)
            u.keybd_event(0x12, 0, 0, 0)
            u.SetForegroundWindow(h)
            u.keybd_event(0x12, 0, 2, 0)
            time.sleep(0.25)
    except Exception:
        pass
    click_screen(x, y)


def walk_until(tx, ty, timeout=10):
    send({"actionType": "player_walk_to", "x": tx, "y": ty}, 0.3)
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.3)
        b = rd()
        if b and tile_of(b) == (tx, ty):
            return True
    return False


PIER_TILES = [(30, 21), (31, 21), (32, 21), (33, 21), (34, 22), (34, 23), (34, 24), (32, 27), (30, 28)]


def fish_from_pier(h, attempts=2):
    """写死：必须走到海滩栈桥上再钓鱼。"""
    b = rd()
    tiles = (b.get("surroundings") or {}).get("Tiles") or []
    me = tile_of(b)
    cur = next((x for x in tiles if x["X"] == me[0] and x["Y"] == me[1]), None)
    if cur and cur.get("IsWater") and cur.get("Passable"):
        log(f"  已经在栈桥上 {me}")
        return fish_here(h, attempts)
    for pt in PIER_TILES:
        t = next((x for x in tiles if x["X"] == pt[0] and x["Y"] == pt[1]), None)
        if t is not None and not t.get("Passable"):
            continue
        if walk_until(pt[0], pt[1], 20):
            log(f"  已在栈桥 {pt}")
            break
    else:
        log("  走不到栈桥")
        return 0
    return fish_here(h, attempts)


def _close_all_menus(timeout=5.0):
    for _ in range(int(timeout / 0.4)):
        st = (rd().get("player") or {}).get("fishing") or {}
        if not st.get("activeMenu") and not st.get("inUse"):
            return True
        send({"actionType": "close_menu"}, 0.4)
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
        send({"actionType": "close_menu"}, 0.4)
        _close_all_menus()
        send({"actionType": "reset_rod"}, 0.5)
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
                    # 相邻水格越多越像栈桥/码头，优先去那钓
                    water_adj = 0
                    for ax, ay in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        if any(tt["X"] == st["X"] + ax and tt["Y"] == st["Y"] + ay and tt.get("IsWater") for tt in tiles):
                            water_adj += 1
                    spots.append((abs(st["X"] - me[0]) + abs(st["Y"] - me[1]) - water_adj * 3, w, st))
                    break
        cur_dbg = next((t for t in tiles if t["X"] == me[0] and t["Y"] == me[1]), None)
        log(f"  [调试] me={me} cur_water={cur_dbg.get('IsWater') if cur_dbg else None} cur_passable={cur_dbg.get('Passable') if cur_dbg else None}")
        # 已经站在栈桥上：直接朝远处开阔水面抛竿（不要走到水里）
        cur_tile = next((t for t in tiles if t["X"] == me[0] and t["Y"] == me[1]), None)
        if cur_tile and cur_tile.get("IsWater") and cur_tile.get("Passable"):
            open_water = [t for t in tiles if t.get("IsWater") and not t.get("Passable")]
            open_water.sort(key=lambda t: abs(t["X"] - me[0]) + abs(t["Y"] - me[1]))
            if open_water:
                spots.insert(0, (-2, open_water[0], cur_tile))
        spots.sort(key=lambda x: x[0])
        if spots:
            log(f"  [调试] 首选水格 {spots[0][1]['X']},{spots[0][1]['Y']} 站位 {spots[0][2]['X']},{spots[0][2]['Y']}")
        if not spots:
            log("  附近没有可站位的水")
            return caught
        done = False
        for dist, w, st in spots[:6]:
            log(f"  试水格 {w['X']},{w['Y']} 站位 {st['X']},{st['Y']}")
            if (st["X"], st["Y"]) != tile_of(rd()):
                if not walk_until(st["X"], st["Y"], 8):
                    continue
            send({"actionType": "select_item", "name": "竹鱼竿"}, 0.35)
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
            wx = px + ux * 160
            wy = py + uy * 160
            cast = False
            # 用游戏内部抛竿；抛竿后要等 1~3 秒才会有状态，别重复抛（重复会取消）
            send({"actionType": "fish_begin", "x": w["X"], "y": w["Y"]}, 0.5)
            for _wait in range(10):
                time.sleep(0.6)
                stf = (rd().get("player") or {}).get("fishing") or {}
                if stf.get("inUse") or stf.get("minigame") or stf.get("nibbling"):
                    cast = True
                    break
            if cast:
                stf = (rd().get("player") or {}).get("fishing") or {}
                log(f"  抛竿成功 inUse={stf.get('inUse')} menu={stf.get('activeMenu')}")
                done = True
                break
        if not done:
            log("  抛竿失败")
            continue
        deadline = time.time() + 25
        hook_sent = False
        minigame = False
        while time.time() < deadline:
            stf = (rd().get("player") or {}).get("fishing") or {}
            if stf.get("minigame"):
                minigame = True
                break
            if stf.get("nibbling") and not hook_sent:
                send({"actionType": "fish_hook"}, 0.1)
                hook_sent = True
                t1 = time.time() + 1.5
                while time.time() < t1:
                    stf = (rd().get("player") or {}).get("fishing") or {}
                    if stf.get("minigame"):
                        minigame = True
                        break
                    time.sleep(0.15)
                if not minigame:
                    send({"actionType": "fish_hook2"}, 0.3)
            time.sleep(0.15)
        if not minigame:
            t2 = time.time() + 6
            while time.time() < t2:
                stf = (rd().get("player") or {}).get("fishing") or {}
                if stf.get("minigame"):
                    minigame = True
                    break
                time.sleep(0.2)
        if not minigame:
            log("  没进小游戏，重置鱼竿")
            send({"actionType": "close_menu"}, 0.25)
            send({"actionType": "reset_rod"}, 0.3)
            continue
        # 等补丁自动收鱼（进度拉满后自动关菜单）
        before = {x["name"]: x["stack"] for x in (rd().get("player") or {}).get("inventory") or [] if x}
        t3 = time.time() + 12
        while time.time() < t3:
            stf = (rd().get("player") or {}).get("fishing") or {}
            if not stf.get("minigame"):
                break
            time.sleep(0.25)
        send({"actionType": "close_menu"}, 0.4)
        time.sleep(0.5)
        after = {x["name"]: x["stack"] for x in (rd().get("player") or {}).get("inventory") or [] if x}
        gained = [k for k in after if after[k] > before.get(k, 0)]
        if gained:
            caught += 1
            log(f"  钓到：{gained}")
            send({"actionType": "collect_debris", "radius": 1}, 0.4)
        else:
            log("  小游戏开了但没收到鱼")
            send({"actionType": "reset_rod"}, 0.3)
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
