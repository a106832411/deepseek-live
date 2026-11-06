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
            time.sleep(0.1)
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
    time.sleep(0.04)
    user32.mouse_event(0x02, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(0x04, 0, 0, 0, 0)
    time.sleep(0.15)


def hold_mouse(down):
    user32.mouse_event(0x02 if down else 0x04, 0, 0, 0, 0)


def fish_state(b):
    return (b.get("player") or {}).get("fishing") or {}


def play_minigame(max_seconds=30):
    """先标定：按住/松开让 bar 向哪边走，再跟随鱼。"""
    b = read_bridge()
    st0 = fish_state(b)
    if not st0.get("minigame"):
        return st0
    bar0 = float(st0.get("barPos") or 0)
    hold_mouse(True)
    time.sleep(0.5)
    st1 = fish_state(read_bridge() or {})
    bar1 = float(st1.get("barPos") or 0)
    hold_mouse(False)
    time.sleep(0.5)
    st2 = fish_state(read_bridge() or {})
    bar2 = float(st2.get("barPos") or 0)
    log(f"calibrate: bar0={bar0:.1f} hold->{bar1:.1f} release->{bar2:.1f}")
    raise_move = bar1 < bar0   # 按住时 bar 数值减小 = 向上
    held = False
    t0 = time.time()
    last_log = 0
    while time.time() - t0 < max_seconds:
        b = read_bridge()
        if not b:
            continue
        st = fish_state(b)
        if not st.get("minigame"):
            if held:
                hold_mouse(False)
            return st
        bar = float(st.get("barPos") or 0)
        fish = float(st.get("fishPos") or 0)
        # raise_move: 按住让 bar 上移（数值减小）
        want = (fish < bar - 2) if raise_move else (fish > bar + 2)
        if want != held:
            hold_mouse(want)
            held = want
        if time.time() - last_log > 0.5:
            last_log = time.time()
            log(f"  minigame bar={bar:.1f} fish={fish:.1f} dist={float(st.get('distance') or 0):.2f} hold={held}")
        time.sleep(0.03)
    if held:
        hold_mouse(False)
    return fish_state(read_bridge() or {})


def main():
    h = find_window()
    if not h:
        log("no game window")
        return
    focus(h)
    send({"actionType": "player_warp", "location": "Beach", "x": 30, "y": 40})
    time.sleep(3)
    send({"actionType": "stand_by_water"})
    time.sleep(3)
    cx, cy = 640, 360
    offsets = [(0, 180), (180, 0), (0, -180), (-180, 0)]
    cast = False
    for ox, oy in offsets:
        click(cx + ox, cy + oy)
        time.sleep(1.2)
        st = fish_state(read_bridge())
        log(f"cast click {ox},{oy} -> inUse={st.get('inUse')}")
        if st.get("inUse") or st.get("minigame"):
            cast = True
            break
    if not cast:
        log("抛竿失败")
        return

    deadline = time.time() + 40
    hooked = False
    while time.time() < deadline:
        st = fish_state(read_bridge())
        if st.get("minigame"):
            log("小游戏打开，开始跟鱼")
            play_minigame()
            break
        if st.get("nibbling") and not hooked:
            log("咬钩，点击钩鱼")
            click(cx, cy + 180)
            hooked = True
            time.sleep(0.6)
        time.sleep(0.1)

    time.sleep(2)
    b = read_bridge()
    inv = [f"{i['name']}x{i['stack']}" for i in (b.get("player") or {}).get("inventory") or [] if i]
    log(f"结束: 背包={inv} fishing={fish_state(b)}")


if __name__ == "__main__":
    main()
