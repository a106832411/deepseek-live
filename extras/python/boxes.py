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

CHEST_SPOTS = [(61, 16), (62, 16), (63, 16)]
# 优先存仓库的杂物（不存工具/武器/食物）
# 注意：石头不存！矿洞里合成楼梯（99 石头）和熔炉（25 石头）都只能用背包里的，
# 而 mod 的 chest_take_named 一次只搬 1 个，从箱子里取石头太慢（99 个要 99 次）。
JUNK = ["木材", "晶球", "史莱姆泥", "苔藓", "树液", "纤维", "松果", "粘土", "硬木"]


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


def find_chests():
    """当前地图扫描范围内所有箱子（家门口可能不止一个）。"""
    out = []
    for t in _tiles():
        nm = str(t.get("ObjectName") or "")
        if "箱" in nm or "Chest" in nm:
            out.append(t)
    return out


def find_chest():
    cs = find_chests()
    return cs[0] if cs else None


def _stand(b, tx, ty):
    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        for t in (b.get("surroundings") or {}).get("Tiles") or []:
            if t["X"] == tx + dx and t["Y"] == ty + dy and t.get("Passable"):
                return t
    return None


def ensure_chest():
    """保证房子门口有一个箱子；没有就用 50 木材做一个放固定位。"""
    b = rd() or {}
    if str(b.get("location")) != "Farm":
        fallback.goto_or_warp("Farm")
    # 先走到房子门口，保证箱子在扫描范围
    nav2.walk_until(64, 17, 10)
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
    if str(b.get("location")) != "Farm":
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





def take_from_chest(name, qty=10):
    """从家门口箱子取出指定物品（用于取铜矿去烧锭、取石头合楼梯）。

    家门口可能有多个箱子（历史上放重复过），逐个开，直到拿够数量。
    """
    b = rd() or {}
    if str(b.get("location")) != "Farm":
        fallback.goto_or_warp("Farm")
    before = count(name)
    target = before + max(1, int(qty))
    chests = find_chests()
    if not chests:
        ch = ensure_chest()
        chests = [ch] if ch else []
    for chest in chests:
        if count(name) >= target:
            break
        open_chest(chest)
        for _ in range(8):
            if count(name) >= target:
                break
            nav2.send({"actionType": "chest_take_named", "x": chest["X"], "y": chest["Y"],
                       "name": name, "count": min(999, target - count(name))}, 1.2)
            time.sleep(0.3)
        nav2.send({"actionType": "exit_menu"}, 0.6)
    log(f"  取箱子：{name} {count(name) - before} 个（目标 {qty}，箱子 {len(chests)} 个）")
    return count(name) - before
