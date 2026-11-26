"""沙漠矿洞（Skull Cavern）挂机：走到巴士站 → 坐车去沙漠 → 走 (8,5) 进洞 → 逐层敲石头找梯子/洞口。

用法：python stardew_skull_session.py --minutes 15
"""
import argparse
import sys
import time

sys.path.insert(0, r"D:\live\deepseek-live\stardew-mod\agent")
import stardew_agent as A  # noqa: E402
import stardew_mine_session as MS  # noqa: E402

SKULL_ENTRANCE = (8, 5)      # Desert 里进 SkullCave 的传送格
SKULL_DOOR = (3, 3)          # SkullCave 里 Action=SkullDoor，交互就能下到第 1 层


def log(m):
    print(time.strftime("[%H:%M:%S] ") + str(m), flush=True)


def state():
    return A.read_state()


def pos(s):
    t = s.get("player") or {}
    return int(t.get("x", -99)), int(t.get("y", -99))


def in_skull(loc):
    loc = str(loc or "")
    return loc == "SkullCave" or loc.startswith("UndergroundMine")


def go_desert():
    """按用户要求：能走就走；矿洞里先回农场，再走到巴士站坐车去沙漠。"""
    s = state()
    cur = str(s.get("location") or "")
    if cur == "Desert":
        return True
    if in_skull(cur):
        A.send_order({"actionType": "leave_mine"})
        time.sleep(4)
        s = state()
        cur = str(s.get("location") or "")
    # 从矿洞/山里出来：先回农场（走路优先，走不通才传送兜底）
    if cur not in ("Farm", "BusStop"):
        if not A.travel_to("Farm", hop_timeout=180, log_fn=log):
            log("  走不回农场 → 传送兜底")
            A.send_order({"actionType": "warp_to", "location": "Farm"})
            time.sleep(4)
    if not A.travel_to("BusStop", hop_timeout=180, log_fn=log):
        log("  没走到巴士站 → 传送兜底")
        A.send_order({"actionType": "warp_to", "location": "BusStop"})
        time.sleep(4)
    log("  到巴士站，坐车去沙漠")
    A.send_order({"actionType": "warp_to", "location": "Desert"})
    time.sleep(5)
    s = state()
    ok = str(s.get("location")) == "Desert"
    log(f"  沙漠 → {'到了' if ok else '没到（' + str(s.get('location')) + '）'}")
    return ok


def enter_skull():
    s = state()
    if in_skull(s.get("location")):
        return True
    for _ in range(3):
        A.send_order({"actionType": "walk_to", "x": SKULL_ENTRANCE[0], "y": SKULL_ENTRANCE[1]})
        time.sleep(4)
        s = state()
        if in_skull(s.get("location")):
            log(f"  进入沙漠矿洞 → {s.get('location')}")
            return True
        A.send_order({"actionType": "force_step", "x": SKULL_ENTRANCE[0], "y": SKULL_ENTRANCE[1]})
        time.sleep(2)
        s = state()
        if in_skull(s.get("location")):
            log(f"  进入沙漠矿洞 → {s.get('location')}")
            return True
    log(f"  进洞失败：{s.get('location')}")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=15)
    a = ap.parse_args()
    end = time.time() + a.minutes * 60
    log(f"===== 沙漠矿洞开始（{a.minutes} 分钟）=====")
    if not go_desert():
        return
    if not enter_skull():
        A.travel_to("Farm", hop_timeout=240, log_fn=log)
        return
    # 进洞后先在大厅点 SkullDoor 下到第 1 层（不是传送格，是 Action）
    for _ in range(4):
        s0 = state()
        if str(s0.get("location")) != "SkullCave":
            break
        for cx, cy in ((SKULL_DOOR[0] + 1, SKULL_DOOR[1]), (SKULL_DOOR[0], SKULL_DOOR[1] + 1),
                       (SKULL_DOOR[0] - 1, SKULL_DOOR[1]), (SKULL_DOOR[0] + 2, SKULL_DOOR[1])):
            A.send_order({"actionType": "walk_to", "x": cx, "y": cy})
            time.sleep(1.6)
            t = (state().get("player") or {}).get("tile") or {}
            if abs(t.get("x", 0) - SKULL_DOOR[0]) + abs(t.get("y", 0) - SKULL_DOOR[1]) <= 2:
                break
        A.send_order({"actionType": "interact", "x": SKULL_DOOR[0], "y": SKULL_DOOR[1]})
        time.sleep(4)
        s1 = state()
        log(f"  SkullDoor 交互 → {s1.get('location')}")
        if str(s1.get("location")) != "SkullCave":
            break
    floors = 0
    while time.time() < end:
        s = state()
        if MS.game_minutes(s) >= 2200:
            log("  到 22:00 收工")
            break
        if not in_skull(s.get("location")):
            log(f"  不在洞里（{s.get('location')}），收工")
            break
        ladder = MS.find_ladder_down()
        n, total = MS.mine_some(8)
        log(f"  [{s.get('location')}] 敲 {n}/{total}")
        if total == 0:
            # 这一层没石头（或刚下来），先找找梯子/洞口，没有就走两步再看
            time.sleep(1.5)
        ladder = ladder or MS.find_ladder_down()
        if ladder:
            lx, ly = ladder["x"], ladder["y"]
            for cx, cy in ((lx - 1, ly), (lx + 1, ly), (lx, ly + 1), (lx, ly - 1)):
                A.send_order({"actionType": "walk_to", "x": cx, "y": cy})
                time.sleep(1.2)
                t = (state().get("player") or {}).get("tile") or {}
                if (t.get("x"), t.get("y")) == (cx, cy):
                    break
            A.send_order({"actionType": "interact", "x": lx, "y": ly})
            time.sleep(4)
            s2 = state()
            if str(s2.get("location")) != str(s.get("location")):
                floors += 1
                log(f"  下到 {s2.get('location')}（第 {floors} 层）")
        time.sleep(0.5)
    log("===== 沙漠矿洞结束，走回农场 =====")
    A.travel_to("Farm", hop_timeout=300, log_fn=log)


if __name__ == "__main__":
    main()
