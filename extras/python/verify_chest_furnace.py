"""实测：矿石存箱子 + 熔炉烧铜锭（真实流程，不瞬移、不作弊）。

跑法：
    python verify_chest_furnace.py
日志直接输出，也会写 logs/verify_chest_furnace.log
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fallback
import mine_real
import nav2

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "verify_chest_furnace.log")


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def inv():
    b = nav2.rd() or {}
    return [i for i in (b.get("player", {}).get("inventory") or []) if i]


def count(name_part):
    return sum(int(i.get("stack", 1)) for i in inv() if name_part in str(i.get("name")))


def money():
    return ((nav2.rd() or {}).get("player") or {}).get("money")


def chops_for_wood(need=50):
    """砍农场附近的树攒木材。"""
    for _ in range(12):
        if count("木材") >= need:
            return True
        b = nav2.rd() or {}
        if b.get("location") != "Farm":
            if not fallback.goto_or_warp("Farm"):
                return False
        nav2.break_obstacles(40, 6, 12)
        log(f"  木材={count('木材')} 石头={count('石头')}")
    return count("木材") >= need


def make_chest():
    if count("木材") < 50:
        return False
    nav2.send({"actionType": "learn_recipe", "name": "Chest"}, 0.8)
    nav2.send({"actionType": "craft_item", "item": "Chest"}, 1.5)
    log(f"  做箱子后 木材={count('木材')} 背包={[i['name'] for i in inv()]}")
    return count("箱子") > 0 or count("Chest") > 0


def place_chest_nearby():
    b = nav2.rd() or {}
    me = nav2.tile(b)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nav2.send({"actionType": "place_item_here", "x": me[0] + dx, "y": me[1] + dy, "item": "箱子"}, 1.0)
        b2 = nav2.rd() or {}
        tiles = (b2.get("surroundings") or {}).get("Tiles") or []
        t = next((s for s in tiles if s["X"] == me[0] + dx and s["Y"] == me[1] + dy and s.get("ObjectType") == "chest"), None)
        if t:
            log(f"  箱子放在 ({t['X']},{t['Y']})")
            return t
    log("  箱子没放下")
    return None


def mine_until(stone_need=25, ore_need=20, budget=900):
    t0 = time.time()
    if not mine_real.enter_mine_walk():
        log("  进不了矿")
        return False
    while time.time() - t0 < budget:
        if count("石头") >= stone_need and count("铜矿石") >= ore_need:
            return True
        mine_real.mine_rocks(3)
        log(f"  石头={count('石头')} 铜矿={count('铜矿石')} 金币={money()}")
    return count("石头") >= stone_need and count("铜矿石") >= ore_need


def make_furnace():
    if count("石头") < 25 or count("铜矿石") < 20:
        log("  材料不够做熔炉")
        return False
    nav2.send({"actionType": "learn_recipe", "name": "Furnace"}, 0.8)
    nav2.send({"actionType": "craft_item", "item": "Furnace"}, 1.5)
    log(f"  做熔炉后 石头={count('石头')} 铜矿={count('铜矿石')} 背包={[i['name'] for i in inv()]}")
    return True


def smelt():
    b = nav2.rd() or {}
    tiles = (b.get("surroundings") or {}).get("Tiles") or []
    f = next((t for t in tiles if "熔炉" in str(t.get("ObjectName")) or "Furnace" in str(t.get("ObjectName"))), None)
    if not f:
        # 放在脚边
        me = nav2.tile(b)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nav2.send({"actionType": "place_item_here", "x": me[0] + dx, "y": me[1] + dy, "item": "熔炉"}, 1.2)
            b2 = nav2.rd() or {}
            tiles2 = (b2.get("surroundings") or {}).get("Tiles") or []
            f = next((t for t in tiles2 if "熔炉" in str(t.get("ObjectName")) or "Furnace" in str(t.get("ObjectName"))), None)
            if f:
                break
    if not f:
        log("  没找到熔炉")
        return False
    log(f"  熔炉在 ({f['X']},{f['Y']})，投 5 个铜矿")
    for _ in range(5):
        nav2.send({"actionType": "machine_drop_in", "x": f["X"], "y": f["Y"], "item": "铜矿石"}, 0.8)
    log("  等 60 秒熔炼…")
    time.sleep(60)
    nav2.send({"actionType": "machine_collect", "x": f["X"], "y": f["Y"]}, 1.2)
    log(f"  收熔炉后 铜锭={count('铜锭')} 背包={[i['name'] for i in inv()]}")
    return count("铜锭") > 0


def main():
    fallback.ensure_ready()
    nav2.live()
    log("=== 验证：存矿石 / 熔炼 ===")
    log(f"起始 金币={money()} 背包={[i['name'] for i in inv()]}")

    # 1) 攒木头做箱子
    chops_for_wood(50)
    if make_chest():
        chest = place_chest_nearby()
        if chest:
            nav2.send({"actionType": "chest_store_ores", "x": chest["X"], "y": chest["Y"]}, 1.0)
            log(f"  存矿后 背包={[i['name'] for i in inv()]}")
    else:
        log("  木头不够，箱子先不做")

    # 2) 下矿攒材料
    mine_until(25, 20, 900)

    # 3) 做熔炉 + 烧
    make_furnace()
    if count("铜矿石") >= 5:
        smelt()
    log("=== 验证结束 ===")
    log(f"结束 金币={money()} 背包={[i['name'] for i in inv()]}")


if __name__ == "__main__":
    main()
