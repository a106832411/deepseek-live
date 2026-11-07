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


def go_down():
    """找梯子下一层（真实走过去 + 交互）。"""
    b = nav2.rd() or {}
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
            log("  石头旁边没有能站的格子，停下")
            break
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
