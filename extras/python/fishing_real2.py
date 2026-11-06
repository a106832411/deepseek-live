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


def player_screen(h, b):
    vp = b["player"]["viewport"]
    t = b["player"]["tile"]
    ox, oy = client_origin(h)
    return ox + t["x"] * 64 + 32 - vp["x"], oy + t["y"] * 64 + 32 - vp["y"]


def click(x, y):
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.08)
    u.mouse_event(0x02, 0, 0, 0, 0)
    time.sleep(0.1)
    u.mouse_event(0x04, 0, 0, 0, 0)


def fish_state(b):
    return (b.get("player") or {}).get("fishing") or {}


def control_minigame(seconds=40):
    """读 bar/fish 位置，按住鼠标让绿条跟随鱼。"""
    held = False
    # 标定：按住 0.4s 看 bar 数值变化方向
    b = rd()
    st = fish_state(b)
    if not st.get("minigame"):
        return
    bar0 = float(st.get("barPos") or 0)
    u.mouse_event(0x02, 0, 0, 0, 0)
    held = True
    time.sleep(0.4)
    st1 = fish_state(rd() or {})
    bar1 = float(st1.get("barPos") or 0)
    u.mouse_event(0x04, 0, 0, 0, 0)
    held = False
    time.sleep(0.2)
    raise_on_hold = bar1 < bar0
    log(f"标定: bar {bar0:.1f} -> hold {bar1:.1f} (raise_on_hold={raise_on_hold})")

    t0 = time.time()
    while time.time() - t0 < seconds:
        b = rd()
        st = fish_state(b)
        if not st.get("minigame"):
            break
        bar = float(st.get("barPos") or 0)
        fish = float(st.get("fishPos") or 0)
        if raise_on_hold:
            want = fish < bar - 2
        else:
            want = fish > bar + 2
        if want and not held:
            u.mouse_event(0x02, 0, 0, 0, 0)
            held = True
        elif not want and held:
            u.mouse_event(0x04, 0, 0, 0, 0)
            held = False
        time.sleep(0.03)
    if held:
        u.mouse_event(0x04, 0, 0, 0, 0)


def main():
    h = find_game()
    u.ShowWindow(h, 9)
    u.SetForegroundWindow(h)
    time.sleep(0.6)
    send({"actionType": "close_menu"})
    time.sleep(0.4)
    send({"actionType": "player_warp", "location": "Beach", "x": 30, "y": 44})
    time.sleep(3)
    send({"actionType": "stand_by_water"})
    time.sleep(3)
    send({"actionType": "player_select_tool", "tool": "fishingrod"})
    time.sleep(0.6)
    b = rd()
    px, py = player_screen(h, b)
    log(f"player screen {px},{py} tile={b['player']['tile']}")

    # 抛竿：朝上偏移点击
    cast = False
    for dy in (-192, -160, -128, -224, -96):
        click(px, py + dy)
        time.sleep(0.9)
        st = fish_state(rd())
        log(f"cast dy={dy} inUse={st.get('inUse')} nibbling={st.get('nibbling')}")
        if st.get("inUse") or st.get("minigame"):
            cast = True
            break
    if not cast:
        log("抛竿失败")
        return

    # 等咬钩 -> 点击钩鱼
    deadline = time.time() + 40
    hooked = False
    while time.time() < deadline:
        st = fish_state(rd())
        if st.get("minigame"):
            break
        if st.get("nibbling") and not hooked:
            log("咬钩，点击钩鱼")
            click(px, py - 192)
            hooked = True
            time.sleep(0.6)
        time.sleep(0.15)

    # 小游戏控制
    log("开始小游戏控制")
    control_minigame(45)
    time.sleep(2)
    b = rd()
    inv = [f"{i['name']}x{i['stack']}" for i in (b["player"].get("inventory") or []) if i]
    log(f"结束: 背包={inv}")
    log(f"fishing={fish_state(b)}")


if __name__ == "__main__":
    main()
