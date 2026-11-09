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
    return mine_real.go_down()


def _unused_old():
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


# 房子门口固定机器位置（别到处放）
FURNACE_SPOTS = [(65, 16), (66, 16), (67, 16)]
CHEST_SPOTS = [(61, 16), (62, 16), (63, 16)]
HOUSE_DOOR = (64, 15)  # 门口保持空


def _tile_obj(b, x, y):
    for t in (b.get("surroundings") or {}).get("Tiles") or []:
        if t["X"] == x and t["Y"] == y:
            return t
    return None


def _spot_furnace(b, spot):
    t = _tile_obj(b, spot[0], spot[1])
    if t and "熔炉" in str(t.get("ObjectName")):
        return t
    return None


def _spot_empty(b, spot):
    t = _tile_obj(b, spot[0], spot[1])
    return bool(t and t.get("Passable") and not t.get("ObjectName"))


def _stand_for(spot):
    b = nav2.rd() or {}
    return mine_real._stand(b, spot[0], spot[1])


def _pick_machine(x, y):
    st = _stand_for((x, y))
    if not st:
        return False
    nav2.walk_until(st["X"], st["Y"], 10)
    nav2.send({"actionType": "pick_object_at", "x": x, "y": y}, 0.8)
    time.sleep(0.4)
    return count("熔炉") > 0 or count("箱子") > 0


def _place_at_spot(name, spot):
    st = _stand_for(spot)
    if st:
        nav2.walk_until(st["X"], st["Y"], 10)
    nav2.send({"actionType": "place_item_here", "name": name, "x": spot[0], "y": spot[1]}, 1.2)
    b = nav2.rd() or {}
    t = _tile_obj(b, spot[0], spot[1])
    return bool(t and name in str(t.get("ObjectName") or ""))


def tidy_machines():
    """把散落的熔炉/箱子收起来，统一放到房子门口固定位置。"""
    b = nav2.rd() or {}
    if "Farm" not in str(b.get("location")):
        return
    tiles = (b.get("surroundings") or {}).get("Tiles") or []
    fixed = set(FURNACE_SPOTS) | set(CHEST_SPOTS)
    scattered = [t for t in tiles if ("熔炉" in str(t.get("ObjectName")) or "箱子" in str(t.get("ObjectName")))
                 and (t["X"], t["Y"]) not in fixed]
    for t in scattered[:4]:
        name = "熔炉" if "熔炉" in str(t.get("ObjectName")) else "箱子"
        if _pick_machine(t["X"], t["Y"]):
            log(f"  收起散落的{name} ({t['X']},{t['Y']})")
            spots = FURNACE_SPOTS if name == "熔炉" else CHEST_SPOTS
            b2 = nav2.rd() or {}
            free = [sp for sp in spots if _spot_empty(b2, sp)]
            if free:
                _place_at_spot(name, free[0])


def _scan_furnace(b=None):
    b = b or nav2.rd() or {}
    tiles = (b.get("surroundings") or {}).get("Tiles") or []
    return next((t for t in tiles if "熔炉" in str(t.get("ObjectName"))), None)


def _walk_farm_and_scan():
    """在农场走几个点找现成熔炉（家附近 + 之前放过的位置）。"""
    for (x, y) in ((64, 15), (63, 15), (70, 21), (60, 15)):
        nav2.walk_until(x, y, 10)
        f = _scan_furnace()
        if f:
            return f
    for (x, y) in ((62, 18), (66, 18), (66, 12), (62, 12)):
        nav2.walk_until(x, y, 10)
        f = _scan_furnace()
        if f:
            return f
    return None


def find_existing_furnace():
    """先看脚边和固定位置；散落的先收拢到固定位置，再使用。"""
    b = nav2.rd() or {}
    for sp in FURNACE_SPOTS:
        f0 = _spot_furnace(b, sp)
        if f0:
            return f0
    tidy_machines()
    b = nav2.rd() or {}
    for sp in FURNACE_SPOTS:
        f0 = _spot_furnace(b, sp)
        if f0:
            return f0
    f = _scan_furnace()
    if f:
        return f
    b = nav2.rd() or {}
    if "Farm" not in str(b.get("location")):
        return None
    return _walk_farm_and_scan()


def place_furnace_here():
    b = nav2.rd() or {}
    for sp in FURNACE_SPOTS:
        if _spot_empty(b, sp):
            if _place_at_spot("熔炉", sp):
                return _spot_furnace(nav2.rd() or {}, sp)
    me = nav2.tile(rd())
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (2, 0), (0, 2)):
        nav2.send({"actionType": "place_item_here", "name": "熔炉", "x": me[0] + dx, "y": me[1] + dy}, 1.2)
        b = rd()
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        f = next((t for t in tiles if "熔炉" in str(t.get("ObjectName"))), None)
        if f:
            return f
        if count("熔炉") == 0:
            break
    return _scan_furnace()


def smelt_with_furnace(f):
    """投 5 矿 → 等 40 秒 → 连收 3 次，确认铜锭增加再投下一炉。"""
    # 铜矿不够就先从家门口箱子里取
    if count("铜矿石") < 5:
        try:
            import boxes
            have = sum(int(e.get("stack", 0)) for e in (rd().get("chestTotals") or []) if "铜矿石" in str(e.get("name")))
            if have >= 5:
                boxes.take_from_chest("铜矿石", min(25, have))
        except Exception as ex:
            log(f"  取铜矿失败: {ex}")
    rounds = 0
    while count("铜矿石") >= 5 and rounds < 10:
        rounds += 1
        bars0 = count("铜锭")
        for _ in range(5):
            nav2.send({"actionType": "machine_drop_in", "x": f["X"], "y": f["Y"], "item": "铜矿石"}, 0.6)
        log(f"  第 {rounds} 炉：投 5 铜矿，等 40 秒…")
        time.sleep(40)
        before = count("铜矿石")
        for _ in range(3):
            nav2.send({"actionType": "machine_collect", "x": f["X"], "y": f["Y"]}, 1.0)
            time.sleep(1.0)
            if count("铜锭") > bars0:
                break
        log(f"  第 {rounds} 炉：铜锭={count('铜锭')} 剩铜矿={count('铜矿石')}（投矿前 {before}）")
        if count("铜锭") == bars0:
            log("  ⚠ 这炉没收到铜锭，可能还在烧/被卡，先停")
            break
    log(f"  熔炼结束：铜锭={count('铜锭')}")


def run_once():
    log("=== 目标：熔炉（20 铜 + 25 石）===")
    fallback.ensure_ready()
    nav2.live()
    # 已在矿洞但该层今天确认没楼梯 → 立刻离开，别挖
    _loc = str(rd().get("location"))
    if "Mine" in _loc:
        _fl = mine_real.floor_num(_loc)
        if _fl > 0 and mine_real._is_no_ladder(_fl):
            log(f"  第 {_fl} 层今天没楼梯，直接离开")
            nav2.send({"actionType": "leave_mine"}, 1.5)
            time.sleep(2)
    if "Mine" not in str(rd().get("location")):
        if not mine_real.enter_mine_walk():
            log("进不了矿，退出")
            return
    for floor_try in range(12):
        _locnow = str(rd().get("location"))
        _flnow = mine_real.floor_num(_locnow)
        # 每轮开始都检查无梯记忆（否则会在同一层反复挖）
        if _flnow > 0 and mine_real._is_no_ladder(_flnow):
            log(f"  第 {_flnow} 层今天不可用，离开")
            nav2.send({"actionType": "leave_mine"}, 1.5)
            time.sleep(2)
            continue
        log(f"[第 {floor_try+1} 层/轮] {_locnow} 石头={count('石头')} 铜={count('铜矿石')}")
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
    # 熔炉：优先用家里现成的，没有才做
    # 背包铜矿不够先看箱子
    if count("铜矿石") < 5:
        try:
            import boxes
            chest_copper = sum(int(e.get("stack", 0)) for e in (rd().get("chestTotals") or []) if "铜矿石" in str(e.get("name")))
            if chest_copper >= 5:
                log(f"  从箱子取铜矿再烧（箱里 {chest_copper}）")
                fallback.goto_or_warp("Farm")
                boxes.take_from_chest("铜矿石", min(25, chest_copper))
        except Exception as ex:
            log(f"  取铜矿失败: {ex}")
    if count("铜矿石") >= 5:
        fallback.goto_or_warp("Farm")
        f = find_existing_furnace()
        if not f:
            if count("熔炉") > 0:
                log("  背包里有熔炉，直接放出来用")
                f = place_furnace_here()
            elif count("石头") >= 25 and count("木材") >= 1:
                log("  家里没有熔炉，做一个")
                free_slots()
                nav2.send({"actionType": "learn_recipe", "name": "Furnace"}, 0.8)
                nav2.send({"actionType": "craft_item", "item": "Furnace"}, 1.5)
                if count("熔炉") > 0:
                    f = place_furnace_here()
            else:
                log(f"  石头不够做熔炉（石头={count('石头')}）")
        if f:
            log(f"  使用熔炉 ({f['X']},{f['Y']})")
            smelt_with_furnace(f)
        else:
            log("  找不到也做不出熔炉，结束")
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
