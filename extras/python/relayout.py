"""重新排版家门口的熔炉/箱子。

布局（房子门在 64,15）：
    箱子: (61,16) (62,16) (63,16)
    熔炉: (65,16) (66,16) (67,16)
    门口通道 (64,15)/(64,16) 留空
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fallback
import nav2

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "relayout.log")
CHEST_SPOTS = [(61, 16), (62, 16), (63, 16)]
FURNACE_SPOTS = [(65, 16), (66, 16), (67, 16)]
DOOR = (64, 15)


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def rd():
    return nav2.rd() or {}


def tiles():
    return (rd().get("surroundings") or {}).get("Tiles") or []


def inv_count(part):
    return sum(int(i.get("stack", 1)) for i in (rd().get("player") or {}).get("inventory") or [] if i and part in str(i.get("name")))


def find_machines():
    out = []
    for t in tiles():
        nm = str(t.get("ObjectName") or "")
        if "熔炉" in nm:
            out.append(("熔炉", t["X"], t["Y"]))
        elif "箱" in nm or "Chest" in nm:
            out.append(("箱子", t["X"], t["Y"]))
    return out


def free_spot(spots, kind):
    for (x, y) in spots:
        t = next((t for t in tiles() if t["X"] == x and t["Y"] == y), None)
        nm = str(t.get("ObjectName") or "") if t else ""
        if t and not nm:
            return (x, y)
    return None


def walk_near(x, y):
    st = None
    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        b = rd() or {}
        t = next((t for t in (b.get("surroundings") or {}).get("Tiles") or []
                  if t["X"] == x + dx and t["Y"] == y + dy and t.get("Passable")), None)
        if t:
            st = t
            break
    if st:
        return nav2.walk_until(st["X"], st["Y"], 12)
    return nav2.walk_until(x, y, 10)


def place(name, x, y):
    nav2.send({"actionType": "place_item_here", "name": name, "x": x, "y": y}, 1.2)
    t = next((t for t in tiles() if t["X"] == x and t["Y"] == y), None)
    return bool(t and str(t.get("ObjectName") or ""))


def main():
    log("=== 重新排版熔炉/箱子 ===")
    fallback.goto_or_warp("Farm")
    # 1) 先把背包里的熔炉放到固定位
    if inv_count("熔炉") > 0:
        spot = free_spot(FURNACE_SPOTS, "熔炉")
        if spot:
            walk_near(*spot)
            if place("熔炉", *spot):
                log(f"  背包熔炉 -> ({spot[0]},{spot[1]})")
    # 2) 扫场内机器，逐个搬到固定位
    for _ in range(6):
        machines = find_machines()
        moved = False
        for (kind, x, y) in machines:
            spots = CHEST_SPOTS if kind == "箱子" else FURNACE_SPOTS
            if (x, y) in spots:
                continue
            spot = free_spot(spots, kind)
            if not spot:
                continue
            log(f"  搬 {kind} ({x},{y}) -> ({spot[0]},{spot[1]})")
            if not walk_near(x, y):
                continue
            nav2.send({"actionType": "pick_object_at", "x": x, "y": y}, 0.9)
            time.sleep(0.5)
            if inv_count(kind) == 0:
                log("    没收起来")
                continue
            walk_near(*spot)
            if place(kind, *spot):
                moved = True
                time.sleep(0.4)
        if not moved:
            break
    ms = find_machines()
    log(f"  最终位置: {ms}")
    for (x, y) in CHEST_SPOTS + FURNACE_SPOTS:
        t = next((t for t in tiles() if t["X"] == x and t["Y"] == y), None)
        if t is None:
            log(f"  ({x},{y}) 不在扫描范围")
    log("=== 排版完成 ===")


if __name__ == "__main__":
    main()
