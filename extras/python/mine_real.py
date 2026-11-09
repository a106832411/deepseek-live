"""矿洞：真实走路 + 真实挥镐（不瞬移）。

用法：
    import mine_real
    mine_real.enter_mine_walk()
    mine_real.mine_rocks(5)
"""
import time

import gameio
import nav2

log = nav2.log

import json
import os

NO_LADDER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "no_ladder_floors.json")
_DAY_SEQ = ["spring", "summer", "fall", "winter"]


def _day_key(b=None):
    b = b or nav2.rd() or {}
    try:
        si = _DAY_SEQ.index(str(b.get("season") or "spring"))
    except ValueError:
        si = 0
    return f"{si}-{b.get('day')}"


def _load_no_ladder():
    try:
        with open(NO_LADDER_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_no_ladder(keys):
    try:
        with open(NO_LADDER_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(keys), f, ensure_ascii=False)
    except Exception:
        pass


def _mark_no_ladder(floor):
    keys = _load_no_ladder()
    keys.add(f"{_day_key()}#{floor}")
    _save_no_ladder(keys)
    log(f"  记住：{_day_key()} 第 {floor} 层没楼梯")


def _is_no_ladder(floor):
    return f"{_day_key()}#{floor}" in _load_no_ladder()



import re


def floor_num(loc=None):
    """矿洞楼层号；不在矿洞返回 -1。"""
    if loc is None:
        loc = str((nav2.rd() or {}).get("location") or "")
    m = re.search(r"UndergroundMine(\d+)", str(loc))
    if m:
        return int(m.group(1))
    if "Mine" in str(loc) or "mine" in str(loc):
        return 0
    return -1


def _stand_any(b, tx, ty):
    """目标旁边有没有能站的格子（只查四方向）。"""
    return _stand(b, tx, ty)


def _tiles(b):
    return (b.get("surroundings") or {}).get("Tiles") or []


def _find(b, x, y):
    return next((t for t in _tiles(b) if t["X"] == x and t["Y"] == y), None)


def _stand(b, tx, ty, maxd=6):
    """找目标旁边一个能站、离玩家最近的格子。"""
    me = nav2.tile(b)
    best = None
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        t = _find(b, tx + dx, ty + dy)
        if not t or not t.get("Passable"):
            continue
        d = abs(t["X"] - me[0]) + abs(t["Y"] - me[1])
        if d > maxd:
            continue
        if best is None or d < best[0]:
            best = (d, t)
    return best[1] if best else None


def enter_mine_walk():
    """从山脚走到矿洞入口，再进矿（不瞬移）。"""
    b = nav2.rd() or {}
    if "Mine" in str(b.get("location")):
        return True
    if b.get("location") != "Mountain":
        if not nav2.goto("Mountain"):
            log("  直走不通，改走：镇上绕路")
            if not nav2.goto_via(["BusStop", "Town", "Mountain"]):
                if not nav2.goto_via(["Forest", "Town", "Mountain"]):
                    log("  ⚠ 到不了山上，应急传送到山脚")
                    try:
                        import fallback as _fb
                        _fb.goto_or_warp("Mountain")
                    except Exception as _ex:
                        log(f"  传送失败: {_ex}")
                    if "Mountain" in str((nav2.rd() or {}).get("location")):
                        log("  已到山脚，继续进矿")
                    return False
    # 矿洞入口在山上 (54,4) 附近的洞口
    b = nav2.rd() or {}
    t = nav2.tile(b)
    if abs(t[0] - 52) + abs(t[1] - 7) > 2:
        nav2.walk_until(52, 7, 30)
    nav2.send({"actionType": "enter_mine", "level": 1}, 2.0)
    time.sleep(3)
    b = nav2.rd() or {}
    ok = "Mine" in str(b.get("location"))
    log(f"  进矿: {ok} ({b.get('location')})")
    return ok


def find_ladder():
    b = nav2.rd() or {}
    tiles = _tiles(b)
    for t in tiles:
        otype = str(t.get("ObjectType") or "").lower()
        oname = str(t.get("ObjectName") or "")
        if otype == "ladder" or "梯子" in oname or "Ladder" in oname or "Shaft" in oname or "竖井" in oname:
            return t
    return None


def _ladder_tile(b=None):
    """从桥接数据读下一页楼梯的坐标（隐藏石头下面也会给坐标）。"""
    b = b or nav2.rd() or {}
    txt = str(b.get("ladderPosText") or "")
    m = re.search(r"X:(\d+)\s+Y:(\d+)", txt)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _interact_tile(cx, cy):
    """站到旁边（若需要）再交互，最后取消对话框。返回是否打开了对话。"""
    b = nav2.rd() or {}
    me = nav2.tile(b)
    if abs(me[0] - cx) + abs(me[1] - cy) > 1:
        st = _stand(b, cx, cy)
        if st:
            nav2.walk_until(st["X"], st["Y"], 10)
            time.sleep(0.2)
    nav2.send({"actionType": "cancel_dialogue"}, 0.25)
    nav2.send({"actionType": "player_face_tile", "x": cx, "y": cy}, 0.2)
    nav2.send({"actionType": "player_interact_at", "x": cx, "y": cy}, 0.7)
    time.sleep(0.5)
    bb = nav2.rd() or {}
    talked = bool(bb.get("dialogueUp"))
    nav2.send({"actionType": "cancel_dialogue"}, 0.3)
    return talked


def _break_rock_at(rock):
    b = nav2.rd() or {}
    st = _stand(b, rock["X"], rock["Y"])
    if not st:
        return False
    if not nav2.walk_until(st["X"], st["Y"], 12):
        return False
    nav2.send({"actionType": "player_face_tile", "x": rock["X"], "y": rock["Y"]}, 0.2)
    nav2.send({"actionType": "select_item", "name": "十字镐"}, 0.2)
    nav2.send({"actionType": "player_use_tool", "tool": "pickaxe", "x": rock["X"], "y": rock["Y"]}, 0.8)
    time.sleep(0.9)
    nav2.send({"actionType": "collect_debris", "radius": 4}, 0.4)
    return True


def descend_via_ladder():
    """找天然楼梯：先敲石头让楼梯出现，再在楼梯附近试交互，直到楼层变大。"""
    b = nav2.rd() or {}
    f0 = floor_num(b.get("location"))
    lad = _ladder_tile(b)
    if not lad:
        return False
    lx, ly = lad
    log(f"  本层楼梯位置 ({lx},{ly}) spawned={b.get('ladderSpawned')}")

    # 先试游戏自己的下楼入口（checkAction / enterMineShaft）
    if b.get("ladderSpawned"):
        for _try in range(3):
            nav2.send({"actionType": "cancel_dialogue"}, 0.3)
            nav2.send({"actionType": "descend_ladder"}, 1.2)
            time.sleep(1.5)
            b2 = nav2.rd() or {}
            if floor_num(b2.get("location")) > f0:
                log(f"  下到 {b2.get('location')}（游戏入口）")
                return True

    # 阶段 1：让楼梯出现（敲藏楼梯那块石头，不行就敲附近其他石头）
    for round_i in range(18):
        b = nav2.rd() or {}
        if floor_num(b.get("location")) > f0:
            log(f"  下到 {b.get('location')}")
            return True
        if b.get("ladderSpawned"):
            break
        tiles = _tiles(b)
        rock = _find(b, lx, ly)
        if not (rock and rock.get("Breakable")):
            rocks = [t for t in tiles if t.get("Breakable") and not t.get("Passable")
                     and t.get("ObjectType") not in ("clump_axe", "clump_pick")]
            if not rocks:
                log("  本层没有可敲的石头，放弃")
                return False
            me = nav2.tile(b)
            rocks.sort(key=lambda t: abs(t["X"] - me[0]) + abs(t["Y"] - me[1]))
            rock = rocks[0]
        if not _break_rock_at(rock):
            continue

    # 阶段 2：楼梯已出现 → 在附近所有可疑格子上试交互
    b = nav2.rd() or {}
    if not b.get("ladderSpawned"):
        log("  敲完石头楼梯仍未出现")
        return False
    tiles = _tiles(b)
    me = nav2.tile(b)
    scored = []
    for t in tiles:
        d = abs(t["X"] - lx) + abs(t["Y"] - ly)
        score = d
        if not t.get("Passable"):
            score -= 3  # 洞口/楼梯通常不可通行
        if t.get("ObjectName"):
            score += 4  # 有东西的格子不太像楼梯
        scored.append((score, t))
    scored.sort(key=lambda x: x[0])
    tried = set()
    talked_tiles = set()
    for _idx, (_score, t) in enumerate(scored[:40]):
        cx, cy = t["X"], t["Y"]
        if (cx, cy) in tried or (cx, cy) in talked_tiles:
            continue
        tried.add((cx, cy))
        if _idx % 3 == 0:
            log(f"  找楼梯进度 {_idx+1}/40（当前 {cx},{cy}）")
        # 先让 mod 精确调用 checkAction（对楼梯格最有效）
        if not t.get("ObjectName"):
            st3 = _stand(nav2.rd() or {}, cx, cy)
            if st3:
                nav2.walk_until(st3["X"], st3["Y"], 8)
            nav2.send({"actionType": "descend_ladder", "x": cx, "y": cy}, 0.9)
            time.sleep(0.6)
            b9 = nav2.rd() or {}
            if floor_num(b9.get("location")) > f0:
                log(f"  下到 {b9.get('location')}（checkAction {cx},{cy}）")
                return True
        if _interact_tile(cx, cy):
            talked_tiles.add((cx, cy))
            continue
        b2 = nav2.rd() or {}
        if floor_num(b2.get("location")) > f0:
            log(f"  下到 {b2.get('location')}（交互 {cx},{cy}）")
            return True
        # 能走的话，踩上去试试
        if t.get("Passable"):
            nav2.walk_until(cx, cy, 8)
            time.sleep(0.4)
            b3 = nav2.rd() or {}
            if floor_num(b3.get("location")) > f0:
                log(f"  下到 {b3.get('location')}（踩 {cx},{cy}）")
                return True
    log("  试了一圈没找到楼梯/洞口")
    return False


def fight_nearest(max_swings=10):
    """打最近的怪：挥武器直到怪死了/走远/血量太低。返回是否还在战斗。"""
    for i in range(max_swings):
        b = nav2.rd() or {}
        hp = int(b.get("health") or 100)
        mons = b.get("monsters") or []
        me = nav2.tile(b)
        near = [m for m in mons if abs(m.get("x", 0) - me[0]) + abs(m.get("y", 0) - me[1]) <= 3]
        if not near:
            return False
        if hp < 35:
            log(f"  血量低（{hp}），先撤退")
            return True
        # 走到怪旁边再挥
        m = near[0]
        tx, ty = int(m.get("x", 0)), int(m.get("y", 0))
        st = _stand(b, tx, ty)
        if st and nav2.tile(nav2.rd()) != (st["X"], st["Y"]):
            nav2.walk_until(st["X"], st["Y"], 10)
        nav2.send({"actionType": "attack"}, 0.55)
        time.sleep(0.25)
    return True


def descend_via_ladder_v2():
    """可靠找下一层：①敲石头让楼梯出现 ②全图巡逻交互直到下楼。"""
    b = nav2.rd() or {}
    f0 = floor_num(b.get("location"))
    if f0 < 0:
        return False
    if _is_no_ladder(f0):
        log(f"  这层今天已确认没楼梯，直接走")
        nav2.send({"actionType": "leave_mine"}, 1.5)
        time.sleep(2)
        return False
    t0 = time.time()
    log(f"  找楼梯（spawned={b.get('ladderSpawned')}）")
    # 阶段一：敲石头直到楼梯出现
    while time.time() - t0 < 90 and not b.get("ladderSpawned"):
        tiles = _tiles(b)
        rocks = [t for t in tiles if t.get("Breakable") and not t.get("Passable")
                 and t.get("ObjectType") not in ("clump_axe", "clump_pick")]
        if not rocks:
            log("  本层石头清完楼梯还没出现，换层")
            _mark_no_ladder(f0)
            return False
        lad = _ladder_tile(b)
        if lad:
            rocks.sort(key=lambda t: abs(t["X"] - lad[0]) + abs(t["Y"] - lad[1]))
        else:
            me = nav2.tile(b)
            rocks.sort(key=lambda t: abs(t["X"] - me[0]) + abs(t["Y"] - me[1]))
        if not _break_rock_at(rocks[0]):
            time.sleep(0.5)
        b = nav2.rd() or {}
        if floor_num(b.get("location")) > f0:
            return True
    if not b.get("ladderSpawned"):
        log("  90 秒没出楼梯，这层跳过")
        _mark_no_ladder(f0)
        return False
    # 阶段二：先快速试附近，再全图巡逻
    if quick_ladder_try(120):
        return True
    if sweep_floor_for_ladder(180):
        return True
    # 这层今天找不到楼梯 → 记住，别再反复进
    _mark_no_ladder(f0)
    return False


def descend_via_ladder():
    return descend_via_ladder_v2()


def go_down():
    """找天然楼梯下一层（不烧石头做楼梯）。"""
    if descend_via_ladder():
        return True
    b = nav2.rd() or {}
    stairs = b.get("stairs") or []
    if stairs:
        me = nav2.tile(b)
        stairs_sorted = sorted(stairs, key=lambda s: abs(int(s["x"]) - me[0]) + abs(int(s["y"]) - me[1]))
        for st in stairs_sorted[:4]:
            sx, sy = int(st["x"]), int(st["y"])
            log(f"  走向楼梯 ({sx},{sy})")
            f0 = floor_num(b.get("location"))
            for attempt in range(3):
                nav2.walk_until(sx, sy, 15)
                time.sleep(0.6)
                b2 = nav2.rd() or {}
                if floor_num(b2.get("location")) > f0:
                    log(f"  下到 {b2.get('location')}")
                    return True
                nav2.send({"actionType": "player_interact_at", "x": sx, "y": sy}, 1.0)
                time.sleep(1.0)
                b3 = nav2.rd() or {}
                if floor_num(b3.get("location")) > f0:
                    log(f"  下到 {b3.get('location')}")
                    return True
        log("  桥接楼梯都走不到，退回扫描")
    lad = find_ladder()
    if not lad:
        return False
    st = _stand(b, lad["X"], lad["Y"])
    if not st:
        return False
    if not nav2.walk_until(st["X"], st["Y"], 12):
        nav2.unstick()
        return False
    nav2.send({"actionType": "player_interact_at", "x": lad["X"], "y": lad["Y"]}, 1.2)
    nav2.send({"actionType": "cancel_dialogue"}, 0.4)
    time.sleep(1.0)
    b2 = nav2.rd() or {}
    moved = str(b2.get("location")) != str(b.get("location"))
    log(f"  下一层: {moved} ({b.get('location')} -> {b2.get('location')})")
    return moved


def _explore_step():
    """本层随便走几步，开图找梯子。"""
    b = nav2.rd() or {}
    me = nav2.tile(b)
    tiles = _tiles(b)
    cands = [t for t in tiles if t.get("Passable") and not t.get("IsWater")
             and abs(t["X"] - me[0]) + abs(t["Y"] - me[1]) >= 4]
    cands.sort(key=lambda t: -(abs(t["X"] - me[0]) + abs(t["Y"] - me[1])))
    for t in cands[:3]:
        if nav2.walk_until(t["X"], t["Y"], 10):
            return True
    return False


def quick_ladder_try(budget=120):
    """先快速试上楼梯子附近的格子（多数楼层楼梯就在附近）。"""
    t0 = time.time()
    b = nav2.rd() or {}
    f0 = floor_num(b.get("location"))
    lad = _ladder_tile(b)
    if not lad:
        return False
    lx, ly = lad
    tiles = _tiles(b)
    cands = []
    # 优先：Buildings 图层 170~180 = 梯子/洞口贴图
    for t in tiles:
        bi = t.get("BuildingIdx")
        if isinstance(bi, int) and 168 <= bi <= 182:
            cands.append((0, t))
    for t in tiles:
        if lx <= 1 or ly <= 1:
            return False  # 楼梯坐标异常（0,0 之类），别乱试
        d = abs(t["X"] - lx) + abs(t["Y"] - ly)
        if d > 6 or t["X"] <= 1 or t["Y"] <= 1 or t["X"] >= 18 or t["Y"] >= 18:
            continue
        if (t["X"], t["Y"]) == (lx, ly):
            continue
        if not t.get("ObjectName"):
            cands.append((d, t))
    cands.sort(key=lambda x: x[0])
    log(f"  先快速试附近 {len(cands)} 格")
    for _i, (_d, t) in enumerate(cands[:40]):
        try:
            nav2.check_timeout()
        except Exception:
            raise
        if time.time() - t0 > budget:
            break
        cx, cy = t["X"], t["Y"]
        st = _stand(nav2.rd() or {}, cx, cy)
        if t.get("Passable"):
            nav2.walk_until(cx, cy, 6)
        elif st:
            nav2.walk_until(st["X"], st["Y"], 6)
        nav2.send({"actionType": "cancel_dialogue"}, 0.2)
        nav2.send({"actionType": "descend_ladder", "x": cx, "y": cy}, 0.7)
        time.sleep(0.35)
        b2 = nav2.rd() or {}
        if floor_num(b2.get("location")) > f0:
            log(f"  下到 {b2.get('location')}（快速试 {cx},{cy}）")
            return True
    log("  附近没有，改全图巡逻")
    return False


def sweep_floor_for_ladder(budget=180):
    """全图巡逻找下一层：走到每个可走格子，对自身和四邻 callAction，直到下楼。

    这是最笨但最可靠的办法——楼梯一定在某个格子上，走过去交互必然触发。
    """
    t0 = time.time()
    b0 = nav2.rd() or {}
    f0 = floor_num(b0.get("location"))
    up_lad = _ladder_tile(b0)  # 上楼梯子，别碰（会提示离开矿井）
    excluded = set()
    if up_lad:
        excluded.add((up_lad[0], up_lad[1]))
    visited = set()
    log(f"  开始全图巡逻找楼梯…（排除上楼梯子 {up_lad}）")
    while time.time() - t0 < budget:
        nav2.check_timeout()
        b = nav2.rd() or {}
        if floor_num(b.get("location")) > f0:
            log(f"  下到 {b.get('location')}（巡逻）")
            return True
        if "Mine" not in str(b.get("location")):
            return False
        me = nav2.tile(b)
        tiles = _tiles(b)
        passables = [t for t in tiles if t.get("Passable") and not t.get("IsWater")
                     and 2 <= t["X"] <= 17 and 2 <= t["Y"] <= 17
                     and (t["X"], t["Y"]) not in excluded]
        passables.sort(key=lambda t: abs(t["X"] - me[0]) + abs(t["Y"] - me[1]))
        moved = False
        for t in passables:
            if (t["X"], t["Y"]) in visited:
                continue
            visited.add((t["X"], t["Y"]))
            if not nav2.walk_until(t["X"], t["Y"], 10):
                continue
            moved = True
            # 站上去后：先对自身交互，再对四邻交互（mod 精确 checkAction）
            for (cx, cy) in ((t["X"], t["Y"]),
                             (t["X"], t["Y"] - 1), (t["X"], t["Y"] + 1),
                             (t["X"] - 1, t["Y"]), (t["X"] + 1, t["Y"])):
                if (cx, cy) in excluded:
                    continue
                nav2.send({"actionType": "descend_ladder", "x": cx, "y": cy}, 0.6)
                time.sleep(0.2)
                b2 = nav2.rd() or {}
                if floor_num(b2.get("location")) > f0:
                    log(f"  下到 {b2.get('location')}（巡逻交互 {cx},{cy}）")
                    return True
                # 再试真实交互（历史上成功多靠这个）
                nav2.send({"actionType": "player_face_tile", "x": cx, "y": cy}, 0.2)
                nav2.send({"actionType": "player_interact_at", "x": cx, "y": cy}, 0.7)
                time.sleep(0.5)
                b3 = nav2.rd() or {}
                if floor_num(b3.get("location")) > f0:
                    log(f"  下到 {b3.get('location')}（交互 {cx},{cy}）")
                    return True
                nav2.send({"actionType": "cancel_dialogue"}, 0.2)
            nav2.send({"actionType": "cancel_dialogue"}, 0.2)
            break
        if not moved:
            # 附近都走过了，往没去过的方向走
            far = [t for t in passables if (t["X"], t["Y"]) not in visited]
            if not far:
                break
            far.sort(key=lambda t: -(abs(t["X"] - me[0]) + abs(t["Y"] - me[1])))
            for t in far[:3]:
                if nav2.walk_until(t["X"], t["Y"], 10):
                    visited.add((t["X"], t["Y"]))
                    break
    log(f"  巡逻 {int(time.time()-t0)} 秒没找到楼梯")
    return False


def mine_rocks(max_rocks=5):
    """真实走过去、挥镐敲石头，直到敲碎。"""
    h, _ = gameio.game_window()
    mined = 0
    _skip = set()
    _tries = {}
    for _ in range(max_rocks * 12):
        if mined >= max_rocks:
            break
        nav2.live()
        b = nav2.rd() or {}
        if "Mine" not in str(b.get("location")):
            log("  不在矿里，停止挖矿")
            break
        # 有怪先打
        try:
            if (b.get("monsters") or []):
                me0 = nav2.tile(b)
                near0 = [m for m in b.get("monsters") if abs(m.get("x", 0) - me0[0]) + abs(m.get("y", 0) - me0[1]) <= 3]
                if near0:
                    log(f"  有 {len(near0)} 只怪，先打")
                    fight_nearest(6)
                    b = nav2.rd() or {}
        except Exception:
            pass
        tiles = _tiles(b)
        targets = []
        for t in tiles:
            if (t["X"], t["Y"]) in _skip:
                continue
            if not (t.get("Breakable") and not t.get("Passable")):
                continue
            if nav2.needs_upgrade(t.get("ObjectType")):
                nav2.mark_needs_upgrade(t["X"], t["Y"], (b or {}).get("location") or "?",
                                        "需要升级镐子（巨石）")
                continue
            targets.append(t)
        if not targets:
            log("  本层没可挖的石头了（或只剩升级才能挖的巨石），找下一层入口")
            if go_down():
                continue
            # 在附近走一圈找入口
            found = False
            for _try in range(6):
                b3 = nav2.rd() or {}
                tiles3 = _tiles(b3)
                if any(t.get("ObjectType") == "ladder" for t in tiles3):
                    found = True
                    break
                _explore_step()
            if found and go_down():
                continue
            log("  本层找不到下一层入口，先离开")
            break
        me = nav2.tile(b)
        targets.sort(key=lambda t: abs(t["X"] - me[0]) + abs(t["Y"] - me[1]))
        target = None
        stand = None
        for t in targets:
            st = _stand(b, t["X"], t["Y"])
            if st:
                target, stand = t, st
                break
        if not target:
            # 兜底：这块石头被围住 → 先敲它旁边挡路的石头/杂物
            blocker = None
            for t in targets:
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    n = _find(b, t["X"] + dx, t["Y"] + dy)
                    if not n or not (n.get("Breakable") and not n.get("Passable")):
                        continue
                    if _stand(b, n["X"], n["Y"]):
                        blocker = n
                        break
                if blocker:
                    break
            if blocker:
                log(f"  石头被围住，先敲挡路的 {blocker['X']},{blocker['Y']}")
                targets = [blocker] + targets
                target = blocker
                stand = _stand(b, blocker["X"], blocker["Y"])
            else:
                for t in targets[:8]:
                    _skip.add((t["X"], t["Y"]))
                log("  附近石头都站不到，先跳过换别的")
                continue
        # 走过去（PathFindController 真实走路）
        if nav2.tile(nav2.rd()) != (stand["X"], stand["Y"]):
            if not nav2.walk_until(stand["X"], stand["Y"], 25):
                log(f"  走不过去 ({stand['X']},{stand['Y']})，换一块")
                # 标记这格暂时跳过：尝试下一块
                continue
        nav2.send({"actionType": "player_face_tile", "x": target["X"], "y": target["Y"]}, 0.3)
        nav2.send({"actionType": "select_item", "name": "十字镐"}, 0.35)
        nav2.send({"actionType": "player_face_tile", "x": target["X"], "y": target["Y"]}, 0.25)
        # 真实点击（带挥镐动画）
        try:
            nav2.click_tile(target["X"], target["Y"], 0.15)
        except Exception:
            nav2.send({"actionType": "player_use_tool", "tool": "pickaxe",
                       "x": target["X"], "y": target["Y"]}, 0.8)
        time.sleep(0.9)
        b2 = nav2.rd() or {}
        now = _find(b2, target["X"], target["Y"])
        if not now or now.get("Passable"):
            mined += 1
            log(f"  敲碎 {target['X']},{target['Y']}（第 {mined} 块）")
            time.sleep(1.5)
            nav2.send({"actionType": "collect_debris", "radius": 4}, 0.6)
        else:
            key = (target["X"], target["Y"])
            _tries[key] = _tries.get(key, 0) + 1
            if _tries[key] >= 3:
                _skip.add(key)
                log(f"  {target['X']},{target['Y']} 敲了3次没碎，跳过")
            else:
                log(f"  {target['X']},{target['Y']} 还没碎，再敲")
    log(f"  挖矿完成：{mined} 块，金币 {((nav2.rd() or {}).get('player') or {}).get('money')}")
    return mined


if __name__ == "__main__":
    nav2.live()
    if enter_mine_walk():
        mine_rocks(3)
