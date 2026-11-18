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
    name = f"{int(time.time()*1000):013d}-r{SEQ:03d}.json"
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



def walk_steps(tx, ty, max_steps=25):
    """分步走路（每步 1-2 格），避免长距离寻路失败。"""
    for _ in range(max_steps):
        b = rd()
        t = b["player"]["tile"]
        cx, cy = int(t["x"]), int(t["y"])
        if abs(cx - tx) + abs(cy - ty) <= 1:
            return True
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        cur = f"{cx},{cy}"
        best = None
        bestd = 10 ** 9
        for tt in tiles:
            if not tt.get("Passable") or tt.get("IsWater"):
                continue
            d = abs(tt["X"] - tx) + abs(tt["Y"] - ty)
            step = abs(tt["X"] - cx) + abs(tt["Y"] - cy)
            if step > 2 or step == 0:
                continue
            if d < bestd:
                bestd = d
                best = tt
        if best is None:
            return False
        send({"actionType": "player_walk_to", "x": best["X"], "y": best["Y"]})
        time.sleep(1.1)
    b = rd()
    t = b["player"]["tile"]
    return abs(int(t["x"]) - tx) + abs(int(t["y"]) - ty) <= 1


def main():
    h = find_win()
    focus(h)
    b = rd()
    log(f"开始: loc={b.get('location')} tile={b['player']['tile']} xp={b['player'].get('xp')}")

    b = None
    for lvl in range(1, 7):
        send({"actionType": "enter_mine", "level": lvl})
        time.sleep(6)
        b = rd()
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        if any(t.get("Breakable") for t in tiles):
            log(f"进矿 {lvl} 层: loc={b.get('location')} 可破坏={sum(1 for t in tiles if t.get('Breakable'))}")
            break
        log(f"第 {lvl} 层没有可挖的，换层")

    tiles = b.get("surroundings", {}).get("Tiles") or []
    # 找一个有可通行邻格的石头
    target = None
    stand = None
    for s in tiles:
        if not s.get("Breakable"):
            continue
        for dx, dy, face in ((0, -1, 2), (1, 0, 1), (0, 1, 0), (-1, 0, 3)):
            c = next((t for t in tiles if t["X"] == s["X"] + dx and t["Y"] == s["Y"] + dy), None)
            if c and c.get("Passable") and not c.get("IsWater"):
                target, stand, face = s, c, face
                break
        if target:
            break
    if not target:
        log("找不到可挖的石头")
        return
    log(f"目标石头 {target['X']},{target['Y']} 站位 {stand['X']},{stand['Y']} 朝向 {face}")

    # 真实走路到石头旁边
    ok = walk_steps(stand["X"], stand["Y"])
    b = rd()
    t = b["player"]["tile"]
    log(f"走到位: {t} ok={ok}")

    # 选镐、面朝石头、真实按键 C 使用工具
    send({"actionType": "player_select_tool", "tool": "pickaxe"})
    time.sleep(0.5)
    send({"actionType": "player_face_tile", "x": target["X"], "y": target["Y"]})
    time.sleep(0.5)
    xp0 = list(b["player"].get("xp") or [])
    for i in range(6):
        key_press(0x43)  # C = use tool
        time.sleep(0.9)
    time.sleep(1)
    b = rd()
    log(f"挥镐后 xp={b['player'].get('xp')} (前后 {xp0}->{b['player'].get('xp')})")
    log(f"背包: " + " | ".join(f"{i['name']}x{i['stack']}" for i in (b['player'].get("inventory") or []) if i))
    send({"actionType": "collect_debris"})
    time.sleep(1)
    b = rd()
    log(f"拾取后背包: " + " | ".join(f"{i['name']}x{i['stack']}" for i in (b['player'].get("inventory") or []) if i))


if __name__ == "__main__":
    main()
