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
            time.sleep(0.1)
    return None


def send(a):
    global SEQ
    SEQ += 1
    os.makedirs(ACTION_DIR, exist_ok=True)
    name = f"{int(time.time()*1000):013d}-fr{SEQ:03d}.json"
    tmp = os.path.join(ACTION_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(ACTION_DIR, name))


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


def tile_screen(h, b, tx, ty):
    vp = (b.get("player") or {}).get("viewport") or {}
    ox, oy = client_origin(h)
    return ox + (tx * 64 + 32 - int(vp.get("x") or 0)), oy + (ty * 64 + 32 - int(vp.get("y") or 0))


def click(x, y, hold=0.0):
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.08)
    u.mouse_event(0x02, 0, 0, 0, 0)
    time.sleep(hold if hold else 0.08)
    u.mouse_event(0x04, 0, 0, 0, 0)


def fishing_state(b):
    return (b.get("player") or {}).get("fishing") or {}


def main():
    h = find_game()
    if not h:
        log("no game window")
        return
    u.ShowWindow(h, 9)
    u.SetForegroundWindow(h)
    time.sleep(0.5)
    send({"actionType": "close_menu"})
    time.sleep(0.5)
    send({"actionType": "player_warp", "location": "Beach", "x": 30, "y": 44})
    time.sleep(3)
    send({"actionType": "stand_by_water"})
    time.sleep(3)
    b = rd()
    st = fishing_state(b)
    log(f"stand: player={b['player']['tile']} viewport={(b['player'].get('viewport'))}")

    # 找到附近水面：用桥接周围的水格
    tiles = (b.get("surroundings") or {}).get("Tiles") or []
    waters = [t for t in tiles if t.get("IsWater")]
    if not waters:
        log("没有找到水")
        return
    # 选离玩家最近的水格
    pt = b["player"]["tile"]
    waters.sort(key=lambda t: abs(t["X"] - int(pt["x"])) + abs(t["Y"] - int(pt["y"])))
    w = waters[0]
    sx, sy = tile_screen(h, b, w["X"], w["Y"])
    send({"actionType": "player_select_tool", "tool": "fishingrod"})
    time.sleep(0.6)
    b = rd()
    log(f"当前工具: {b['player'].get('currentTool')}")
    log(f"点击水面 ({w['X']},{w['Y']}) -> screen ({sx},{sy})")
    click(sx, sy)
    time.sleep(1.0)
    b = rd()
    st = fishing_state(b)
    log(f"抛竿后 inUse={st.get('inUse')} nibbling={st.get('nibbling')}")

    # 等咬钩
    deadline = time.time() + 40
    hooked = False
    while time.time() < deadline:
        b = rd()
        st = fishing_state(b)
        if st.get("minigame"):
            break
        if st.get("nibbling") and not hooked:
            log("咬钩，点击钩鱼")
            click(sx, sy)
            hooked = True
            time.sleep(0.5)
        time.sleep(0.15)

    # 小游戏：按 bar/fish 位置控制鼠标按住
    held = False
    t0 = time.time()
    while time.time() - t0 < 45:
        b = rd()
        st = fishing_state(b)
        if not st.get("minigame"):
            break
        bar = float(st.get("barPos") or 0)
        fish = float(st.get("fishPos") or 0)
        # 先用“按住抬高绿条”的假设：bar数值变小=上移
        want = fish < bar - 2
        if want and not held:
            u.mouse_event(0x02, 0, 0, 0, 0)
            held = True
        elif not want and held:
            u.mouse_event(0x04, 0, 0, 0, 0)
            held = False
        time.sleep(0.03)
    if held:
        u.mouse_event(0x04, 0, 0, 0, 0)

    time.sleep(2)
    b = rd()
    inv = [f"{i['name']}x{i['stack']}" for i in (b["player"].get("inventory") or []) if i]
    log(f"结束: 背包={inv}")
    log(f"fishing={fishing_state(b)}")


if __name__ == "__main__":
    main()
