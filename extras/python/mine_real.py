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
                    log("  ⚠ 到不了山上，进不了矿")
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


def descend_via_ladder():
    """找天然楼梯：楼梯藏在某块石头下面 → 敲开它 → 楼梯出现 → 交互下一层。"""
    b = nav2.rd() or {}
    f0 = floor_num(b.get("location"))
    lad = _ladder_tile(b)
    if not lad:
        return False
    lx, ly = lad
    log(f"  本层楼梯位置 ({lx},{ly}) spawned={b.get('ladderSpawned')}")
    for _ in range(25):
        b = nav2.rd() or {}
        if floor_num(b.get("location")) > f0:
            log(f"  下到 {b.get('location')}")
            return True
        if b.get("ladderSpawned"):
            # 楼梯/洞口出现在附近某一格：对自身和四邻交互，直到楼层变大
            cands = [(lx, ly), (lx, ly + 1), (lx, ly - 1), (lx + 1, ly), (lx - 1, ly)]
            tiles0 = _tiles(b)
            # 优先试「不可通行且没有物体」的格子（洞口就是这样）
            holes = [t for t in tiles0 if not t.get("Passable") and not t.get("ObjectName")
                     and not t.get("IsWater") and abs(t["X"] - lx) + abs(t["Y"] - ly) <= 2]
            for h in holes:
                cands.insert(0, (h["X"], h["Y"]))
            seen = set()
            for (cx, cy) in cands:
                if (cx, cy) in seen:
                    continue
                seen.add((cx, cy))
                bb = nav2.rd() or {}
                me_now = nav2.tile(bb)
                # 必须站在洞口旁边才能交互
                if abs(me_now[0] - cx) + abs(me_now[1] - cy) > 1:
                    st2 = _stand(bb, cx, cy)
                    if st2:
                        nav2.walk_until(st2["X"], st2["Y"], 10)
                        time.sleep(0.3)
                nav2.send({"actionType": "player_face_tile", "x": cx, "y": cy}, 0.25)
                nav2.send({"actionType": "player_interact_at", "x": cx, "y": cy}, 1.0)
                time.sleep(1.0)
                bb = nav2.rd() or {}
                if floor_num(bb.get("location")) > f0:
                    log(f"  下到 {bb.get('location')}（交互 {cx},{cy}）")
                    return True
            return False
        # 楼梯还没出现：敲它上面那块石头
        tiles = _tiles(b)
        rock = _find(b, lx, ly)
        if rock and rock.get("Breakable"):
            st = _stand(b, lx, ly)
            if not st:
                # 石头被围住 → 先敲挡路的
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    n = _find(b, lx + dx, ly + dy)
                    if n and n.get("Breakable") and _stand(b, n["X"], n["Y"]):
                        rock, st = n, _stand(b, n["X"], n["Y"])
                        break
            if st and nav2.walk_until(st["X"], st["Y"], 12):
                nav2.send({"actionType": "player_face_tile", "x": rock["X"], "y": rock["Y"]}, 0.2)
                nav2.send({"actionType": "select_item", "name": "十字镐"}, 0.25)
                nav2.send({"actionType": "player_use_tool", "tool": "pickaxe", "x": rock["X"], "y": rock["Y"]}, 0.8)
                time.sleep(1.0)
                nav2.send({"actionType": "collect_debris", "radius": 4}, 0.5)
                continue
            return False
        # 那块石头已经没了但楼梯没出现 → 敲附近石头碰概率
        rocks = [t for t in tiles if t.get("Breakable") and not t.get("Passable")
                 and t.get("ObjectType") not in ("clump_axe", "clump_pick")]
        if not rocks:
            return False
        me = nav2.tile(b)
        rocks.sort(key=lambda t: abs(t["X"] - me[0]) + abs(t["Y"] - me[1]))
        r0 = rocks[0]
        st = _stand(b, r0["X"], r0["Y"])
        if st and nav2.walk_until(st["X"], st["Y"], 12):
            nav2.send({"actionType": "player_face_tile", "x": r0["X"], "y": r0["Y"]}, 0.2)
            nav2.send({"actionType": "select_item", "name": "十字镐"}, 0.25)
            nav2.send({"actionType": "player_use_tool", "tool": "pickaxe", "x": r0["X"], "y": r0["Y"]}, 0.8)
            time.sleep(1.0)
            nav2.send({"actionType": "collect_debris", "radius": 4}, 0.5)
    return False


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
    time.sleep(1.5)
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


def mine_rocks(max_rocks=5):
    """真实走过去、挥镐敲石头，直到敲碎。"""
    h, _ = gameio.game_window()
    mined = 0
    _skip = set()
    for _ in range(max_rocks * 12):
        if mined >= max_rocks:
            break
        nav2.live()
        b = nav2.rd() or {}
        if "Mine" not in str(b.get("location")):
            log("  不在矿里，停止挖矿")
            break
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
            log(f"  {target['X']},{target['Y']} 还没碎，再敲")
    log(f"  挖矿完成：{mined} 块，金币 {((nav2.rd() or {}).get('player') or {}).get('money')}")
    return mined


if __name__ == "__main__":
    nav2.live()
    if enter_mine_walk():
        mine_rocks(3)
