import time

import nav2

log = nav2.log
rd = nav2.rd
tile = nav2.tile
send = nav2.send
walk_until = nav2.walk_until


def nearest_stand(tiles, s, cur):
    best = None
    bd = 999
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        st = next((t for t in tiles if t["X"] == s["X"] + dx and t["Y"] == s["Y"] + dy and t.get("Passable")), None)
        if st:
            d = abs(st["X"] - cur[0]) + abs(st["Y"] - cur[1])
            if d < bd:
                bd = d
                best = st
    return best


def break_nearby(tx, ty, max_break=8):
    """清掉玩家附近、朝目标方向最近的障碍（优先清贴着玩家的）。"""
    broken = 0
    bad = set()
    for _ in range(max_break):
        b = nav2.safe_check()
        cur = tile(b)
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        npc_tiles = {(int(n.get("X", -99)), int(n.get("Y", -99))) for n in (b.get("surroundings") or {}).get("Npcs") or []}
        cands = []
        for s in tiles:
            if not (s.get("Breakable") or s.get("Terrain") in ("tree", "bush")):
                continue
            if (s["X"], s["Y"]) in npc_tiles or (s["X"], s["Y"]) in bad:
                continue
            pd = abs(s["X"] - cur[0]) + abs(s["Y"] - cur[1])
            if pd > 4:
                continue
            st = nearest_stand(tiles, s, cur)
            if not st or (st["X"], st["Y"]) in bad:
                continue
            d = abs(s["X"] - tx) + abs(s["Y"] - ty) + pd * 0.5
            cands.append((d, pd, s, st))
        if not cands:
            return broken
        cands.sort(key=lambda c: (c[1], c[0]))
        d, _, s, st = cands[0]
        pd = abs(s["X"] - cur[0]) + abs(s["Y"] - cur[1])
        if pd > 1:
            if not walk_until(st["X"], st["Y"], 8):
                bad.add((s["X"], s["Y"]))
                bad.add((st["X"], st["Y"]))
                continue
        tool = nav2._tool_for(s.get("ObjectType"), s.get("Terrain"))
        send({"actionType": "player_select_tool", "tool": tool}, 0.25)
        send({"actionType": "player_face_tile", "x": s["X"], "y": s["Y"]}, 0.25)
        for _ in range(8):
            nav2.click_tile(s["X"], s["Y"], 0.12)
            time.sleep(0.4)
        send({"actionType": "collect_debris"}, 0.35)
        broken += 1
        log(f"  清 {s['X']},{s['Y']} ({s.get('ObjectType') or s.get('Terrain')})")
    return broken


def clear_corridor(tx, ty, rounds=80):
    """一边走一边清障，直到走到 (tx,ty) 附近。"""
    for _ in range(rounds):
        nav2.live()
        b = nav2.safe_check()
        if not b:
            continue
        cur = tile(b)
        dist = abs(cur[0] - tx) + abs(cur[1] - ty)
        if dist <= 1:
            log(f"  到目标 ({tx},{ty})，从 {cur}")
            return True
        s = nav2.step_walk(tx, ty, max_steps=6)
        b = rd()
        if s and abs(tile(b)[0] - tx) + abs(tile(b)[1] - ty) > dist - 1:
            pass
        b = rd()
        if abs(tile(b)[0] - tx) + abs(tile(b)[1] - ty) < dist:
            continue
        nav2.avoid_npc()
        broke = break_nearby(tx, ty, 5)
        if broke == 0:
            log(f"  卡在 {tile(b)}，附近没可清的障碍")
            return False
    return False


def ensure_farm():
    b = rd()
    if b.get("location") == "Farm":
        return True
    return nav2.goto("Farm")


def go_busstop():
    nav2.live()
    b = rd()
    log(f"起点 {b.get('location')} {tile(b)}")
    if not ensure_farm():
        log("到不了农场")
        return False
    # 农场东出口
    ok = clear_corridor(78, 15, 120)
    b = rd()
    log(f"走廊清理后 {tile(b)} {b.get('location')}")
    if not ok:
        return False
    send({"actionType": "enter_exit", "target": "BusStop"}, 1.0)
    time.sleep(3)
    b = rd()
    log(f"过图后 {b.get('location')} {tile(b)}")
    return b.get("location") == "BusStop"


def go_backwoods():
    nav2.live()
    b = rd()
    log(f"起点 {b.get('location')} {tile(b)}")
    if not ensure_farm():
        return False
    ok = clear_corridor(40, 1, 150)
    b = rd()
    log(f"清完走廊 {tile(b)} {b.get('location')}")
    if not ok:
        return False
    send({"actionType": "enter_exit", "target": "Backwoods"}, 1.0)
    time.sleep(3)
    b = rd()
    log(f"过图后 {b.get('location')} {tile(b)}")
    return b.get("location") == "Backwoods"


if __name__ == "__main__":
    import gameio, os
    fresh = (time.time() - os.path.getmtime(nav2.BRIDGE_FILE)) < 8 if os.path.exists(nav2.BRIDGE_FILE) else False
    if not fresh:
        gameio.load_game()
    print("go_busstop:", go_busstop())
