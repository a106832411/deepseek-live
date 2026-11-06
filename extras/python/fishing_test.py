import ctypes
import json
import os
import time
from ctypes import wintypes

BRIDGE = r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge"
BRIDGE_FILE = os.path.join(BRIDGE, "bridge_data.json")
ACTION_DIR = os.path.join(BRIDGE, "actions")
SEQ = 0
user32 = ctypes.windll.user32


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read_bridge():
    for _ in range(10):
        try:
            with open(BRIDGE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            time.sleep(0.2)
    return None


def send(action):
    global SEQ
    SEQ += 1
    os.makedirs(ACTION_DIR, exist_ok=True)
    name = f"{int(time.time()*1000):013d}-f{SEQ:03d}.json"
    tmp = os.path.join(ACTION_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(action, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(ACTION_DIR, name))


def find_window():
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


def click(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(0x02, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(0x04, 0, 0, 0, 0)
    time.sleep(0.15)


def fish_state(b):
    return (b.get("player") or {}).get("fishing") or {}


def main():
    h = find_window()
    if not h:
        log("no game window")
        return
    focus(h)
    send({"actionType": "player_warp", "location": "Beach", "x": 30, "y": 40})
    time.sleep(3)
    send({"actionType": "stand_by_water"})
    for _ in range(8):
        time.sleep(1)
        b = read_bridge()
        st = fish_state(b)
        log(f"  tick fishing={st}")
    b = read_bridge()
    log(f"state: {fish_state(b)}")

    # 玩家在屏幕中央附近；朝四个方向点一次，直到抛竿成功
    cx, cy = 640, 360
    offsets = [(0, 180), (180, 0), (0, -180), (-180, 0)]
    cast = False
    for ox, oy in offsets:
        click(cx + ox, cy + oy)
        time.sleep(1.2)
        b = read_bridge()
        st = fish_state(b)
        log(f"click {ox},{oy} -> inUse={st.get('inUse')} nibbling={st.get('nibbling')} rod={st.get('rod')}")
        if st.get("inUse") or st.get("minigame"):
            cast = True
            break
    if not cast:
        log("抛竿失败")
        return

    # 等咬钩 → 真实点击钩鱼 → 小游戏（mod 自动把进度拉满）
    deadline = time.time() + 40
    hooked = False
    while time.time() < deadline:
        b = read_bridge()
        st = fish_state(b)
        if st.get("minigame"):
            log("钓鱼小游戏已打开")
            break
        if st.get("nibbling") and not hooked:
            log("咬钩！点击钩鱼")
            click(cx + offsets[0][0], cy + offsets[0][1])
            hooked = True
            time.sleep(1.0)
        time.sleep(0.25)
    for _ in range(8):
        time.sleep(1)
        b = read_bridge()
        st = fish_state(b)
        log(f"  tick fishing={st}")
    b = read_bridge()
    inv = [f"{i['name']}x{i['stack']}" for i in (b.get("player") or {}).get("inventory") or [] if i]
    log(f"结束: 背包={inv} fishing={fish_state(b)}")


if __name__ == "__main__":
    main()
