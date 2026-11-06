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
    name = f"{int(time.time()*1000):013d}-e{SEQ:03d}.json"
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


def live(h):
    send({"actionType": "set_focus_pause", "disabled": True}, 0.2)
    send({"actionType": "reset_state"}, 0.2)
    u.ShowWindow(h, 9)
    u.SetForegroundWindow(h)


def money():
    return (rd() or {}).get("player", {}).get("money")


def fish_at_beach(h, attempts=6):
    send({"actionType": "player_warp", "location": "Beach", "x": 30, "y": 44}, 2.5)
    send({"actionType": "stand_by_water"}, 2.5)
    send({"actionType": "player_select_tool", "tool": "fishingrod"}, 0.4)
    caught = 0
    for i in range(attempts):
        live(h)
        b = rd()
        vp = (b.get("player") or {}).get("viewport") or {}
        t = b["player"]["tile"]
        ox, oy = client_origin(h)
        px = ox + int(t["x"]) * 64 + 32 - int(vp.get("x") or 0)
        py = oy + int(t["y"]) * 64 + 32 - int(vp.get("y") or 0)
        st = (b.get("player") or {}).get("fishing") or {}
        if st.get("minigame"):
            send({"actionType": "close_menu"}, 0.3)
        cast = False
        for dy in (-192, -160, -224, -128):
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
                click_screen(px, py + dy)
                hooked = True
                time.sleep(0.5)
            time.sleep(0.12)
        deadline = time.time() + 20
        while time.time() < deadline:
            st = (rd().get("player") or {}).get("fishing") or {}
            if not st.get("minigame"):
                break
            time.sleep(0.35)
        caught += 1
        send({"actionType": "close_menu"}, 0.3)
    log(f"  钓鱼 {caught} 次完成，金额={money()}")
    return caught


def ship():
    send({"actionType": "player_ship_all"}, 0.8)
    b = rd()
    inv = [f"{x['name']}x{x['stack']}" for x in (b["player"].get("inventory") or []) if x]
    log(f"  已装船，剩余: {inv}")


def go_sleep(h):
    send({"actionType": "player_warp", "location": "FarmHouse", "x": 9, "y": 9}, 2.0)
    for _ in range(150):
        b = rd()
        if b and b.get("time", 0) >= 1800:
            break
        live(h)
        time.sleep(3.5)
    send({"actionType": "player_sleep_bed"}, 2)
    time.sleep(16)
    b = rd()
    log(f"  醒来: day={b.get('day')} time={b.get('time')} money={b.get('player', {}).get('money')}")
    return b


def main(days=2):
    h = find_game()
    if not h:
        log("no game window")
        return
    live(h)
    for d in range(days):
        b = rd()
        log(f"=== 第{d+1}天 开始 day={b.get('day')} time={b.get('time')} money={b.get('player', {}).get('money')}")
        fish_at_beach(h, 6)
        ship()
        go_sleep(h)
        b = rd()
        log(f"=== 第{d+1}天 结束 day={b.get('day')} money={b.get('player', {}).get('money')}")


if __name__ == "__main__":
    main(2)
