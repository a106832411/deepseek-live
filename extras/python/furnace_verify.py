"""熔炉验证：下矿攒 25 石头 + 20 铜矿 → 学配方 → 做熔炉 → 放置 → 投矿 → 收铜锭。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fallback
import mine_real
import nav2

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "furnace_verify.log")


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def inv():
    b = nav2.rd() or {}
    return [i for i in (b.get("player", {}).get("inventory") or []) if i]


def count(part):
    return sum(int(i.get("stack", 1)) for i in inv() if part in str(i.get("name")))


def find_obj(name_part):
    b = nav2.rd() or {}
    tiles = (b.get("surroundings") or {}).get("Tiles") or []
    return next((t for t in tiles if name_part in str(t.get("ObjectName"))), None)


def ensure_materials(budget=1200):
    t0 = time.time()
    if count("铜矿石") >= 20 and count("石头") >= 25:
        return True
    if not mine_real.enter_mine_walk():
        log("进矿失败")
        return False
    while time.time() - t0 < budget:
        if count("铜矿石") >= 20 and count("石头") >= 25:
            return True
        b = nav2.rd() or {}
        if b.get("time", 0) >= 2400:
            # 太晚了先回家睡觉，别卡在 2 点晕倒
            log("  太晚了，回家睡觉")
            fallback.goto_or_warp("FarmHouse")
            nav2.send({"actionType": "player_sleep_bed"}, 2.0)
            time.sleep(6)
            continue
        n = mine_real.mine_rocks(4)
        log(f"  石头={count('石头')} 铜矿={count('铜矿石')} 金币={((nav2.rd() or {}).get('player') or {}).get('money')}")
        if n == 0:
            time.sleep(1.5)
            b3 = nav2.rd() or {}
            if "Mine" not in str(b3.get("location")):
                log("  不在矿里了，重新进矿")
                fallback.goto_or_warp("Mountain")
                mine_real.enter_mine_walk()
    return False


def main():
    fallback.ensure_ready()
    nav2.live()
    log("=== 熔炉验证开始 ===")
    log(f"起始 石头={count('石头')} 铜矿={count('铜矿石')}")

    if not ensure_materials():
        log("材料没攒够，退出")
        return

    log(f"材料够了：石头={count('石头')} 铜矿={count('铜矿石')}")

    # 回农场做熔炉（要找空地放）
    fallback.goto_or_warp("Farm")
    b = nav2.rd() or {}
    log(f"回到 {b.get('location')} {nav2.tile(b)}")

    nav2.send({"actionType": "learn_recipe", "name": "Furnace"}, 0.8)
    nav2.send({"actionType": "craft_item", "item": "Furnace"}, 1.5)
    log(f"做熔炉后 背包={[i['name'] for i in inv()]}")
    if count("熔炉") == 0:
        log("熔炉没做出来，退出（可能配方/材料问题）")
        return

    # 放在脚边
    me = nav2.tile(nav2.rd())
    placed = None
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1)):
        nav2.send({"actionType": "place_item_here", "name": "熔炉", "x": me[0] + dx, "y": me[1] + dy}, 1.2)
        f = find_obj("熔炉")
        if f:
            placed = f
            break
    if not placed:
        log("熔炉没放下，退出")
        return
    log(f"熔炉放在 ({placed['X']},{placed['Y']})")

    # 投 5 个铜矿
    for i in range(5):
        nav2.send({"actionType": "machine_drop_in", "x": placed["X"], "y": placed["Y"], "item": "铜矿石"}, 0.9)
    log(f"投矿后 铜矿={count('铜矿石')}")

    log("等 60 秒熔炼…")
    time.sleep(60)
    nav2.send({"actionType": "machine_collect", "x": placed["X"], "y": placed["Y"]}, 1.5)
    log(f"收完 铜锭={count('铜锭')} 背包={[i['name'] for i in inv()]}")
    log("=== 熔炉验证结束 ===")


if __name__ == "__main__":
    main()
