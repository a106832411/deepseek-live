"""矿洞挂机：warp 进矿洞 → 大堂下梯 → 逐层敲石头找梯子。用法：--minutes 15"""
import argparse
import sys
import time

sys.path.insert(0, r"D:\live\deepseek-live\stardew-mod\agent")
import stardew_agent as A  # noqa: E402


def game_minutes(s):
    """把 "23:50" / 2350 统一成分钟数；顺便处理 24:xx。"""
    t = s.get("time")
    try:
        if isinstance(t, int):
            return t
        ts = str(t or "")
        if ":" in ts:
            h, m = ts.split(":")[:2]
            return int(h) * 100 + int(m)
        return int("".join(c for c in ts if c.isdigit()) or 0)
    except Exception:
        return 0


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + str(msg), flush=True)


def state():
    return A.read_state()


def pos(s):
    # 原始 state 把坐标放在 player.tile 里；summarize 过的放在顶层 tile
    t = s.get("tile") or (s.get("player") or {}).get("tile") or {}
    return int(t.get("x", 0)), int(t.get("y", 0))


def in_mine(loc):
    loc = str(loc or "")
    return loc.startswith("UndergroundMine")


def enter_mine():
    s = state()
    if in_mine(s.get("location")):
        return True
    # 先按用户要求走路过去；走不到再传送兜底（矿洞在山里，步行路线可能被地形隔断）
    A.send_order({"actionType": "goto", "location": "Mine"})
    for _ in range(24):
        time.sleep(2.5)
        s = state()
        if str(s.get("location")) in ("Mine",) or in_mine(s.get("location")):
            break
    s = state()
    s = state()
    if str(s.get("location")) != "Mine":
        log(f"  传送进矿洞失败：{s.get('location')}")
        return False
    A.send_order({"actionType": "walk_to", "x": 22, "y": 9})
    time.sleep(4)
    A.send_order({"actionType": "interact", "x": 23, "y": 9})
    time.sleep(4)
    s = state()
    ok = in_mine(s.get("location"))
    log(f"  下矿口交互 → {s.get('location')} {'成功' if ok else '失败'}")
    return ok


mine_bad = set()   # 敲不动/走不过去的石头，记下来别再试


def mine_some(n=8):
    """一棵一棵来：走到石头正旁边（四向）→ 站定 → 挥镐 → 验证。站不到就 force_step 贴身。"""
    ok = 0
    total = 0
    for _ in range(n):
        s = state()
        brk = [t for t in (s.get("tiles") or []) if t.get("breakable") and (t["x"], t["y"]) not in mine_bad]
        total = len(brk)
        if not brk:
            break
        p = pos(s)
        brk.sort(key=lambda t: abs(t["x"] - p[0]) + abs(t["y"] - p[1]))
        t = brk[0]
        stood = False
        for cx, cy in ((t["x"] + 1, t["y"]), (t["x"] - 1, t["y"]), (t["x"], t["y"] + 1), (t["x"], t["y"] - 1)):
            A.send_order({"actionType": "walk_to", "x": cx, "y": cy})
            time.sleep(1.4)
            pp = pos(state())
            if abs(pp[0] - t["x"]) + abs(pp[1] - t["y"]) == 1:
                stood = True
                break
        if not stood:
            # 走不过去就别硬来：force_step 会把人物塞进石头里（之后就再也走不动）
            mine_bad.add((t["x"], t["y"]))
            continue
        # 站定再挥（还在走的时候挥镐会被打断）
        last = None
        for _w in range(8):
            pp = pos(state())
            if pp == last:
                break
            last = pp
            time.sleep(0.25)
        A.send_order({"actionType": "use_tool", "tool": "pickaxe", "x": t["x"], "y": t["y"]})
        time.sleep(1.4)
        s2 = state()
        still = any(z.get("x") == t["x"] and z.get("y") == t["y"] and z.get("breakable")
                    for z in (s2.get("tiles") or []))
        if still:
            mine_bad.add((t["x"], t["y"]))
        else:
            ok += 1
    return ok, total


def find_ladder_down():
    """只认「下梯子」：电梯/上梯子都不能算，否则会误点电梯原地打转。"""
    s = state()
    for t in (s.get("tiles") or []):
        if not t.get("interactable"):
            continue
        if "下梯" in str(t.get("obj")) or "洞口" in str(t.get("obj")):
            return t
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=15)
    a = ap.parse_args()
    end = time.time() + a.minutes * 60
    log(f"===== 挖矿开始（{a.minutes} 分钟）=====")
    if not enter_mine():
        return
    floors = 0
    while time.time() < end:
        s = state()
        if game_minutes(s) >= 2130:
            log("  到了 21:30，收工回农场")
            A.send_order({"actionType": "warp_to", "location": "Farm"})
            time.sleep(2)
            break
        if not in_mine(s.get("location")):
            log(f"  不在矿里（{s.get('location')}），重新进")
            if not enter_mine():
                time.sleep(5)
                continue
            continue
        ladder = find_ladder_down()
        n, total = mine_some(8)
        log(f"  [{s.get('location')}] 敲 {n}/{total} 块石头，体力 {s.get('stamina')}")
        ladder = ladder or find_ladder_down()
        if ladder:
            lx, ly = ladder["x"], ladder["y"]
            for cx, cy in ((lx - 1, ly), (lx + 1, ly), (lx, ly + 1), (lx, ly - 1)):
                A.send_order({"actionType": "walk_to", "x": cx, "y": cy})
                time.sleep(1.4)
                t = (state().get("player") or {}).get("tile") or {}
                if (t.get("x"), t.get("y")) == (cx, cy):
                    break
            A.send_order({"actionType": "interact", "x": lx, "y": ly})
            time.sleep(4)
            s2 = state()
            if str(s2.get("location")) != str(s.get("location")):
                floors += 1
                log(f"  下到 {s2.get('location')}（第 {floors} 层）")
            else:
                log("  梯子交互没下去，继续敲石头")
        else:
            log("  没看到梯子，继续敲")
        time.sleep(0.5)
    log("===== 挖矿结束，回农场 =====")
    A.send_order({"actionType": "goto", "location": "Farm"})
    time.sleep(10)
    # 挖完就地熔炼，别让矿石堆在背包里等下一轮
    A.send_order({"actionType": "smelt", "ore": "Copper", "times": 10})
    time.sleep(6)
    A.send_order({"actionType": "smelt", "ore": "Iron", "times": 10})
    time.sleep(6)
    A.send_order({"actionType": "collect_machines"})
    time.sleep(2)


if __name__ == "__main__":
    main()
