"""目标：挖到 20 铜矿 + 25 石头 → 做熔炉 → 烧铜锭。

矿洞下楼方式（已验证）：合成楼梯(99 石头) → 放在矿洞里 → 对楼梯格交互就下一层。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fallback
import mine_real
import nav2

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "furnace_progress.log")


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


def count(part):
    return sum(int(i.get("stack", 1)) for i in (rd().get("player", {}).get("inventory") or []) if i and part in str(i.get("name")))


def free_slots():
    used = len([x for x in rd().get("player", {}).get("inventory") or [] if x])
    while used >= 11:
        for nm in ("松果", "树液", "纤维", "晶球", "小麦种子"):
            if count(nm) > 0:
                nav2.send({"actionType": "throw_item", "name": nm}, 0.4)
                break
        else:
            break
        used = len([x for x in rd().get("player", {}).get("inventory") or [] if x])
    return used


def use_staircase():
    """合成楼梯 → 放在脚边 → 对着楼梯格交互下一层。"""
    st = count("石头")
    if st < 99:
        return False
    free_slots()
    nav2.send({"actionType": "learn_recipe", "name": "Staircase"}, 0.6)
    nav2.send({"actionType": "craft_item", "item": "Staircase"}, 1.5)
    if count("楼梯") == 0:
        log(f"  楼梯没做出来（石头={count('石头')}）")
        return False
    me = nav2.tile(rd())
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        tx, ty = me[0] + dx, me[1] + dy
        f0 = mine_real.floor_num(rd().get("location"))
        nav2.send({"actionType": "place_item_here", "name": "楼梯", "x": tx, "y": ty}, 1.2)
        if count("楼梯") == 0:
            nav2.send({"actionType": "player_interact_at", "x": tx, "y": ty}, 1.2)
            time.sleep(1.5)
            b = rd()
            if mine_real.floor_num(b.get("location")) > f0:
                log(f"  下到 {b.get('location')}（{f0} -> {mine_real.floor_num(b.get('location'))}）")
                return True
    return False


def go_deeper():
    b = rd()
    stairs = b.get("stairs") or []
    if stairs:
        return mine_real.go_down()
    # 找对象里的梯子（挖石头后可能掉梯子）
    tiles = (b.get("surroundings") or {}).get("Tiles") or []
    lads = [t for t in tiles if str(t.get("ObjectName") or "").find("梯") >= 0 or str(t.get("ObjectType") or "") == "ladder"]
    if lads:
        me = nav2.tile(b)
        lad = min(lads, key=lambda t: abs(t["X"] - me[0]) + abs(t["Y"] - me[1]))
        log(f"  发现梯子 ({lad['X']},{lad['Y']})")
        nav2.walk_until(lad["X"], lad["Y"], 15)
        nav2.send({"actionType": "player_interact_at", "x": lad["X"], "y": lad["Y"]}, 1.2)
        time.sleep(1.5)
        if "Mine" in str(rd().get("location")):
            log(f"  下到 {rd().get('location')}")
            return True
    if use_staircase():
        return True
    log("  这层找不到/去不了下一层")
    return False


def run_once():
    log("=== 目标：熔炉（20 铜 + 25 石）===")
    fallback.ensure_ready()
    nav2.live()
    if "Mine" not in str(rd().get("location")):
        if not mine_real.enter_mine_walk():
            log("进不了矿，退出")
            return
    for floor_try in range(12):
        log(f"[第 {floor_try+1} 层/轮] {rd().get('location')} 石头={count('石头')} 铜={count('铜矿石')}")
        mine_real.mine_rocks(40)
        log(f"  挖完：石头={count('石头')} 铜={count('铜矿石')}")
        if count("铜矿石") >= 20 and count("石头") >= 25:
            break
        if not go_deeper():
            log("  下不去，先回地面攒石头")
            nav2.send({"actionType": "leave_mine"}, 1.5)
            time.sleep(3)
            fallback.goto_or_warp("Farm")
            # 农场敲石头
            for _ in range(6):
                nav2.break_obstacles(64, 15, 8)
                if count("石头") >= 99:
                    break
            if count("石头") < 99 and count("铜矿石") < 20:
                log("  石头也不够，结束")
                break
            if not mine_real.enter_mine_walk():
                break
    log(f"材料：铜={count('铜矿石')} 石头={count('石头')} 木材={count('木材')}")
    # 做熔炉
    if count("铜矿石") >= 5 and count("石头") >= 25:
        fallback.goto_or_warp("Farm")
        free_slots()
        nav2.send({"actionType": "learn_recipe", "name": "Furnace"}, 0.8)
        nav2.send({"actionType": "craft_item", "item": "Furnace"}, 1.5)
        log(f"  熔炉数量={count('熔炉')} 石头={count('石头')}")
        me = nav2.tile(rd())
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nav2.send({"actionType": "place_item_here", "name": "熔炉", "x": me[0] + dx, "y": me[1] + dy}, 1.3)
            if count("熔炉") == 0:
                break
        b = rd()
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        f = next((t for t in tiles if "熔炉" in str(t.get("ObjectName"))), None)
        if not f:
            # 放在脚边找一遍
            me = nav2.tile(rd())
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nav2.send({"actionType": "place_item_here", "name": "熔炉", "x": me[0] + dx, "y": me[1] + dy}, 1.2)
                b2 = rd()
                tiles2 = (b2.get("surroundings") or {}).get("Tiles") or []
                f = next((t for t in tiles2 if "熔炉" in str(t.get("ObjectName"))), None)
                if f:
                    break
        if f:
            log(f"  熔炉在 ({f['X']},{f['Y']})")
            rounds = 0
            while count("铜矿石") >= 5 and rounds < 8:
                rounds += 1
                for _ in range(5):
                    nav2.send({"actionType": "machine_drop_in", "x": f["X"], "y": f["Y"], "item": "铜矿石"}, 0.7)
                log(f"  第 {rounds} 炉：投 5 铜矿，等 60 秒…")
                time.sleep(60)
                nav2.send({"actionType": "machine_collect", "x": f["X"], "y": f["Y"]}, 1.5)
                time.sleep(1)
                log(f"  第 {rounds} 炉收完：铜锭={count('铜锭')} 剩余铜矿={count('铜矿石')}")
            log(f"  熔炼结束：铜锭={count('铜锭')}")
    log("=== 结束 ===")


def sleep_new_day():
    day0 = rd().get("day")
    log("  石头不够做楼梯，先睡一天再挖")
    fallback.goto_or_warp("FarmHouse")
    nav2.send({"actionType": "player_sleep_bed"}, 2.0)
    for _ in range(15):
        time.sleep(2)
        if rd().get("day") != day0:
            return True
    nav2.send({"actionType": "player_force_new_day"}, 2.0)
    time.sleep(8)
    nav2.send({"actionType": "force_unpause"}, 0.5)
    return rd().get("day") != day0


def main():
    log("=== 目标：熔炉（自动多天）===")
    for _day in range(4):
        run_once()
        if count("铜矿石") >= 20:
            break
        if count("石头") < 99:
            if not sleep_new_day():
                break
    log(f"最终：铜={count('铜矿石')} 石头={count('石头')}")


if __name__ == "__main__":
    main()
