"""储物箱：固定在房子门口，背包快满时把杂物存进去。

用法：
    import boxes
    boxes.ensure_chest()
    boxes.deposit_junk()   # 背包 >= 11 格时调用
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fallback
import nav2

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "boxes.log")

CHEST_SPOTS = [(62, 16), (63, 16), (64, 16)]
# 优先存仓库的杂物（不存工具/武器/食物）
JUNK = ["石头", "木材", "铜矿石", "煤炭", "晶球", "史莱姆泥", "苔藓", "石英", "树液", "纤维", "松果", "粘土", "硬木"]


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


def inv():
    return [i for i in (rd().get("player") or {}).get("inventory") or [] if i]


def slots_used():
    return len(inv())


def count(name):
    return sum(int(i.get("stack", 1)) for i in inv() if name in str(i.get("name")))


def _tiles():
    return (rd().get("surroundings") or {}).get("Tiles") or []


def find_chest():
    for t in _tiles():
        nm = str(t.get("ObjectName") or "")
        if "箱" in nm or "Chest" in nm:
            return t
    return None


def _stand(b, tx, ty):
    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        for t in (b.get("surroundings") or {}).get("Tiles") or []:
            if t["X"] == tx + dx and t["Y"] == ty + dy and t.get("Passable"):
                return t
    return None


def ensure_chest():
    """保证房子门口有一个箱子；没有就用 50 木材做一个放固定位。"""
    b = rd() or {}
    if "Farm" not in str(b.get("location")):
        fallback.goto_or_warp("Farm")
    ch = find_chest()
    if ch:
        return ch
    log("  家门口没有箱子，做一个")
    if count("木材") < 50:
        log(f"  木材不够（{count('木材')}）")
        return None
    # 背包里可能已经有箱子（宝箱）
    have = count("宝箱") + count("Chest") + count("箱子")
    if have == 0:
        nav2.send({"actionType": "learn_recipe", "name": "Chest"}, 0.6)
        nav2.send({"actionType": "craft_item", "item": "Chest"}, 1.2)
        have = count("宝箱") + count("Chest") + count("箱子")
    if have == 0:
        log("  箱子没做出来")
        return None
    for spot in CHEST_SPOTS:
        t = next((x for x in _tiles() if x["X"] == spot[0] and x["Y"] == spot[1]), None)
        if t and not t.get("ObjectName"):
            for nm in ("宝箱", "Chest", "箱子"):
                nav2.send({"actionType": "place_item_here", "name": nm, "x": spot[0], "y": spot[1]}, 1.0)
                ch = find_chest()
                if ch:
                    log(f"  箱子放在 ({ch['X']},{ch['Y']})")
                    return ch
    ch = find_chest()
    return ch


def open_chest(chest):
    b = rd() or {}
    st = _stand(b, chest["X"], chest["Y"])
    if st:
        nav2.walk_until(st["X"], st["Y"], 10)
    nav2.send({"actionType": "player_interact_at", "x": chest["X"], "y": chest["Y"]}, 1.2)
    time.sleep(0.6)
    return True


def deposit_junk(keep=9):
    """背包 >= 11 格时，把杂物存进家门口箱子，直到 <= keep 格。"""
    if slots_used() < 11:
        return True
    b = rd() or {}
    if "Farm" not in str(b.get("location")):
        fallback.goto_or_warp("Farm")
    chest = ensure_chest()
    if not chest:
        return False
    open_chest(chest)
    moved = 0
    for _ in range(6):
        if slots_used() <= keep:
            break
        before = slots_used()
        nav2.send({"actionType": "chest_store_named", "x": chest["X"], "y": chest["Y"],
                   "names": JUNK}, 1.6)
        time.sleep(0.5)
        moved += 1
        if slots_used() >= before:
            break
    nav2.send({"actionType": "exit_menu"}, 0.6)
    log(f"  存箱子：{moved} 批，格子 {slots_used()}")
    return slots_used() <= 11



