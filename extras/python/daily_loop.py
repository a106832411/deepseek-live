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


def send(a, wait=0.55):
    global SEQ
    SEQ += 1
    os.makedirs(ACTION_DIR, exist_ok=True)
    name = f"{int(time.time()*1000):013d}-d{SEQ:03d}.json"
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
    time.sleep(0.08)
    u.mouse_event(0x02, 0, 0, 0, 0)
    time.sleep(0.1)
    u.mouse_event(0x04, 0, 0, 0, 0)


def tile_of(b):
    t = b["player"]["tile"]
    return int(t["x"]), int(t["y"])


def ensure_live(h):
    send({"actionType": "set_focus_pause", "disabled": True}, 0.25)
    send({"actionType": "reset_state"}, 0.25)
    u.ShowWindow(h, 9)
    u.SetForegroundWindow(h)


def walk_until(tx, ty, timeout=10):
    send({"actionType": "player_walk_to", "x": tx, "y": ty}, 0.3)
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.3)
        b = rd()
        if b and tile_of(b) == (tx, ty):
            return True
    return False


def work_phase(max_targets=15):
    done = 0
    tried = set()
    for _ in range(max_targets * 4):
        if done >= max_targets:
            break
        b = rd()
        if not b:
            continue
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        me = tile_of(b)
        cands = []
        for s in tiles:
            if not s.get("Breakable"):
                continue
            key = (s["X"], s["Y"])
            if key in tried:
                continue
            for dx, dy, face in ((1, 0, 1), (-1, 0, 3), (0, 1, 0), (0, -1, 2)):
                st = next((t for t in tiles if t["X"] == s["X"] + dx and t["Y"] == s["Y"] + dy), None)
                if st and st.get("Passable") and not st.get("IsWater"):
                    dist = abs(s["X"] - me[0]) + abs(s["Y"] - me[1])
                    cands.append((dist, s, st))
                    break
        if not cands:
            log("  附近没目标了")
            break
        cands.sort(key=lambda c: c[0])
        dist, s, st = cands[0]
        tried.add((s["X"], s["Y"]))
        if not walk_until(st["X"], st["Y"], 8):
            continue
        ot = str(s.get("ObjectType") or "").lower()
        tool = "axe" if ot in ("weed", "twig", "tree", "stump", "log") else "pickaxe"
        send({"actionType": "player_face_tile", "x": s["X"], "y": s["Y"]}, 0.3)
        for _ in range(2):
            send({"actionType": "player_use_tool", "tool": tool, "x": s["X"], "y": s["Y"]}, 0.55)
        send({"actionType": "collect_debris"}, 0.5)
        done += 1
    log(f"  干活完成 {done} 个目标")


def fish_phase(h, max_catch=3, seconds_per=55):
    b = rd()
    vp = (b.get("player") or {}).get("viewport") or {}
    send({"actionType": "player_select_tool", "tool": "fishingrod"}, 0.3)
    for i in range(max_catch):
        b = rd()
        pt = b["player"]["tile"]
        ox, oy = client_origin(h)
        px = ox + int(pt["x"]) * 64 + 32 - int(vp.get("x") or 0)
        py = oy + int(pt["y"]) * 64 + 32 - int(vp.get("y") or 0)
        cast = False
        for dy in (-192, -160, -224):
            click_screen(px, py + dy)
            time.sleep(0.9)
            st = (rd().get("player") or {}).get("fishing") or {}
            if st.get("inUse") or st.get("minigame"):
                cast = True
                break
        if not cast:
            log(f"  第{i+1}次抛竿失败")
            continue
        # 等咬钩 -> 点击钩鱼
        deadline = time.time() + 35
        hooked = False
        while time.time() < deadline:
            st = (rd().get("player") or {}).get("fishing") or {}
            if st.get("minigame"):
                break
            if st.get("nibbling") and not hooked:
                click_screen(px, py + dy)
                hooked = True
                time.sleep(0.5)
            time.sleep(0.15)
        # 等小游戏结束（补丁会自动捕获入包）
        deadline = time.time() + 25
        while time.time() < deadline:
            st = (rd().get("player") or {}).get("fishing") or {}
            if not st.get("minigame"):
                break
            time.sleep(0.4)
        send({"actionType": "close_menu"}, 0.4)
        time.sleep(1)
        inv = [x["name"] for x in (rd()["player"].get("inventory") or []) if x]
        log(f"  第{i+1}次钓鱼后背包: {inv}")
    send({"actionType": "close_menu"}, 0.3)


def ship_phase():
    send({"actionType": "player_ship_all"}, 0.8)
    b = rd()
    inv = [f"{x['name']}x{x['stack']}" for x in (b["player"].get("inventory") or []) if x]
    log(f"  已出货，剩余背包: {inv}")


def go_home_and_sleep(h):
    b = rd()
    if b.get("location") != "FarmHouse":
        send({"actionType": "enter_location", "name": "FarmHouse"}, 1.5)
        b = rd()
        if b.get("location") != "FarmHouse":
            send({"actionType": "player_warp", "location": "FarmHouse", "x": 9, "y": 9}, 1.5)
    # 等晚上
    for _ in range(120):
        b = rd()
        if b and b.get("time", 0) >= 1800:
            break
        ensure_live(h)
        time.sleep(4)
    send({"actionType": "player_sleep_bed"}, 2)
    time.sleep(18)
    b = rd()
    log(f"  睡觉后: day={b.get('day')} time={b.get('time')} money={b['player'].get('money')}")


def main(days=2):
    h = find_game()
    if not h:
        log("no game window")
        return
    ensure_live(h)
    for day in range(days):
        b = rd()
        log(f"=== 第{day+1}天开始: {b.get('location')} day={b.get('day')} time={b.get('time')} money={b['player'].get('money')}")
        if b.get("location") != "Farm":
            send({"actionType": "enter_location", "name": "Farm"}, 1.5)
            b = rd()
            if b.get("location") != "Farm":
                send({"actionType": "player_warp", "location": "Farm", "x": 64, "y": 15}, 1.5)
        ensure_live(h)
        log("-- 干活")
        work_phase(15)
        log("-- 钓鱼")
        fish_phase(h, 3)
        log("-- 出货")
        ship_phase()
        log("-- 回家睡觉")
        go_home_and_sleep(h)
        b = rd()
        log(f"=== 第{day+1}天结束: day={b.get('day')} money={b['player'].get('money')}")


if __name__ == "__main__":
    main(2)
