import json
import os
import re
import time

import gameio
import nav2

LOG_FILE = os.path.join(os.environ["APPDATA"], "StardewValley", "ErrorLogs", "SMAPI-latest.txt")


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def send(a, wait=0.5):
    nav2.send(a, wait)


def rd():
    return nav2.rd()


def tile_of(b):
    t = b["player"]["tile"]
    return int(t["x"]), int(t["y"])


def find_action_tiles(needle="Mine", loc="Mountain"):
    send({"actionType": "find_action", "needle": needle}, 0.8)
    time.sleep(1.0)
    try:
        lines = open(LOG_FILE, encoding="utf-8", errors="ignore").read().splitlines()
    except Exception:
        return []
    last = None
    for line in reversed(lines):
        if "FINDACTION" in line and f"FINDACTION {loc}" in line:
            last = line
            break
    if not last:
        return []
    m = re.search(r"FINDACTION [^:]+: (.*)", last)
    if not m:
        return []
    out = []
    for part in m.group(1).split("|"):
        part = part.strip()
        mm = re.match(r"(-?\d+),(-?\d+)=(.*)", part)
        if mm:
            out.append((int(mm.group(1)), int(mm.group(2)), mm.group(3)))
    return out


def walk_to(tx, ty, timeout=15):
    send({"actionType": "player_walk_to", "x": tx, "y": ty}, 0.3)
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.3)
        b = rd()
        if b and tile_of(b) == (tx, ty):
            return True
    return False


def go_mine(level=1):
    nav2.live()
    b = rd()
    log(f"起点 {b.get('location')} {tile_of(b)}")
    if b.get("location") != "Mountain":
        if not nav2.goto("Mountain"):
            log("到不了山上")
            return False
    # 矿洞入口在 Mountain 的 warp (54,4) 附近；走到旁边（不要踩上去），再交互进矿
    b = rd()
    log(f"山上位置 {tile(b)}，走向矿洞入口附近")
    if not walk_to(51, 7, 30):
        for dx, dy in ((52, 8), (50, 7), (52, 6), (50, 8)):
            if walk_to(dx, dy, 12):
                break
    b = rd()
    log(f"到入口附近 {tile(b)}，调用游戏进矿")
    send({"actionType": "enter_mine", "level": 1}, 2.0)
    time.sleep(5)
    b = rd()
    loc = b.get("location")
    log(f"进矿后 loc={loc} tile={tile(b)} pos={b.get('player', {}).get('position')}")
    return "Mine" in str(loc)


if __name__ == "__main__":
    import os
    fresh = (time.time() - os.path.getmtime(nav2.BRIDGE_FILE)) < 8 if os.path.exists(nav2.BRIDGE_FILE) else False
    if not fresh:
        gameio.load_game()
    ok = go_mine()
    b = rd()
    log(f"结果 {ok} loc={b.get('location')} tile={tile_of(b)}")
