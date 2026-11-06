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


def send(a, wait=0.5):
    global SEQ
    SEQ += 1
    os.makedirs(ACTION_DIR, exist_ok=True)
    name = f"{int(time.time()*1000):013d}-x{SEQ:03d}.json"
    tmp = os.path.join(ACTION_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(ACTION_DIR, name))
    time.sleep(wait)


def live():
    send({"actionType": "set_focus_pause", "disabled": True}, 0.12)
    send({"actionType": "reset_state"}, 0.12)
    send({"actionType": "close_menu"}, 0.12)


def money():
    return (rd() or {}).get("player", {}).get("money")


def tile_of(b):
    t = b["player"]["tile"]
    return int(t["x"]), int(t["y"])


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


def walk_until(tx, ty, timeout=8):
    send({"actionType": "player_walk_to", "x": tx, "y": ty}, 0.25)
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.25)
        b = rd()
        if b and tile_of(b) == (tx, ty):
            return True
    return False


def mine_phase(max_mined=20):
    for lvl in range(1, 9):
        live()
        send({"actionType": "enter_mine", "level": lvl}, 2.0)
        b = rd()
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        if any(t.get("Breakable") for t in tiles):
            break
    mined = 0
    for _ in range(max_mined * 3):
        if mined >= max_mined:
            break
        b = rd()
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        cands = [t for t in tiles if t.get("Breakable") and (t["X"], t["Y"]) not in set()]
        if not cands:
            break
        s = cands[0]
        send({"actionType": "player_mine_at", "x": s["X"], "y": s["Y"]}, 0.8)
        send({"actionType": "collect_debris"}, 0.3)
        mined += 1
        tiles = None
    log(f"  挖矿 {mined} 个，金币 {money()}")


def fish_phase(h, attempts=4):
    send({"actionType": "player_warp", "location": "Beach", "x": 30, "y": 44}, 2.0)
    send({"actionType": "stand_by_water"}, 2.0)
    b = rd()
    waters = [t for t in (b.get("surroundings") or {}).get("Tiles") or [] if t.get("IsWater")]
    if not waters:
        send({"actionType": "player_warp", "location": "Beach", "x": 38, "y": 10}, 1.5)
        send({"actionType": "stand_by_water"}, 1.5)
        b = rd()
        waters = [t for t in (b.get("surroundings") or {}).get("Tiles") or [] if t.get("IsWater")]
    send({"actionType": "player_select_tool", "tool": "fishingrod"}, 0.3)
    caught = 0
    for i in range(attempts):
        live()
        b = rd()
        vp = (b.get("player") or {}).get("viewport") or {}
        t = b["player"]["tile"]
        ox, oy = client_origin(h)
        px = ox + int(t["x"]) * 64 + 32 - int(vp.get("x") or 0)
        py = oy + int(t["y"]) * 64 + 32 - int(vp.get("y") or 0)
        cast = False
        for dy in (-192, -160, -224, 0, 128):
            click_screen(px, py + dy)
            time.sleep(0.8)
            st = (rd().get("player") or {}).get("fishing") or {}
            if st.get("inUse") or st.get("minigame"):
                cast = True
                break
        if not cast:
            continue
        deadline = time.time() + 30
        hooked = False
        while time.time() < deadline:
            st = (rd().get("player") or {}).get("fishing") or {}
            if st.get("minigame"):
                break
            if st.get("nibbling") and not hooked:
                click_screen(px, py - 192)
                hooked = True
                time.sleep(0.5)
            time.sleep(0.12)
        deadline = time.time() + 18
        while time.time() < deadline:
            st = (rd().get("player") or {}).get("fishing") or {}
            if not st.get("minigame"):
                break
            time.sleep(0.3)
        caught += 1
        send({"actionType": "close_menu"}, 0.3)
    log(f"  钓鱼 {caught} 次，金币 {money()}")


def farm_phase(max_plant=6):
    """收成熟作物；把空地锄成耕地再播种（真实走位 + 工具/种子）。"""
    for _ in range(6):
        b = rd()
        if b and b.get("location") == "Farm":
            break
        send({"actionType": "player_warp", "location": "Farm", "x": 64, "y": 15}, 1.5)
    b = rd()
    if b.get("location") != "Farm":
        log(f"  ⚠ 没到农场（{b.get('location')}）")
        return
    tiles = (b.get("surroundings") or {}).get("Tiles") or []
    harvested = 0
    for s in tiles:
        if s.get("CropReady"):
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                st = next((t for t in tiles if t["X"] == s["X"] + dx and t["Y"] == s["Y"] + dy and t.get("Passable")), None)
                if st and walk_until(st["X"], st["Y"], 5):
                    send({"actionType": "player_interact_at", "x": s["X"], "y": s["Y"]}, 0.6)
                    harvested += 1
                    break

    planted = 0
    tried = set()
    for _ in range(max_plant * 6):
        if planted >= max_plant:
            break
        b = rd()
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        me = tile_of(b)
        cand = None
        for s in tiles:
            if not s.get("Passable") or s.get("IsWater") or s.get("ObjectName"):
                continue
            key = (s["X"], s["Y"])
            if key in tried:
                continue
            if abs(s["X"] - me[0]) + abs(s["Y"] - me[1]) > 6:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                st = next((t for t in tiles if t["X"] == s["X"] + dx and t["Y"] == s["Y"] + dy and t.get("Passable")), None)
                if st:
                    cand = (s, st)
                    break
            if cand:
                break
        if not cand:
            break
        s, st = cand
        tried.add((s["X"], s["Y"]))
        if not walk_until(st["X"], st["Y"], 6):
            continue
        send({"actionType": "player_use_tool", "tool": "hoe", "x": s["X"], "y": s["Y"]}, 0.7)
        send({"actionType": "player_use_seed_at", "x": s["X"], "y": s["Y"], "seed": "Wheat"}, 0.8)
        planted += 1
    log(f"  农场：收 {harvested}，种 {planted}，金币 {money()}")


def sell_and_sleep():
    send({"actionType": "player_warp", "location": "Farm", "x": 64, "y": 15}, 1.2)
    send({"actionType": "player_ship_all"}, 0.7)
    send({"actionType": "player_sell_instant"}, 0.8)
    log(f"  卖出后金币 {money()}")
    send({"actionType": "player_warp", "location": "FarmHouse", "x": 9, "y": 11}, 1.0)
    # 直接调到傍晚（测试用调速），省去等 12 分钟
    send({"actionType": "set_clock", "time": 1780}, 1.0)
    b = rd()
    b = rd()
    log(f"  睡觉 day={b.get('day')} time={b.get('time')}")
    for _ in range(4):
        send({"actionType": "player_sleep"}, 1.8)
        d0 = (rd() or {}).get("day")
        for _ in range(18):
            time.sleep(3)
            b = rd()
            if b and b.get("day") != d0:
                log(f"  跨天 day={b.get('day')} money={b.get('player', {}).get('money')}")
                send({"actionType": "close_menu"}, 0.3)
                return
        send({"actionType": "close_menu"}, 0.3)
    b = rd()
    log(f"  ⚠ 没跨天 day={b.get('day')}")


def main(days=2):
    h = find_game()
    live()
    for d in range(days):
        b = rd()
        log(f"=== 第{d+1}天 day={b.get('day')} time={b.get('time')} money={b.get('player', {}).get('money')}")
        mode = d % 3
        if mode == 0:
            log("-- 挖矿日")
            mine_phase(25)
        elif mode == 1:
            log("-- 钓鱼日")
            fish_phase(h, 5)
        else:
            log("-- 农场日")
            farm_phase(6)
        log("-- 卖出+睡觉")
        sell_and_sleep()
        b = rd()
        log(f"=== 第{d+1}天结束 day={b.get('day')} money={b.get('player', {}).get('money')}")


if __name__ == "__main__":
    main(3)
