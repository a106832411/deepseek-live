"""矿洞挂机：warp 进矿洞 → 大堂下梯 → 逐层敲石头找梯子。用法：--minutes 15"""
import argparse
import sys
import time

sys.path.insert(0, r"D:\live\deepseek-live\stardew-mod\agent")
import stardew_agent as A  # noqa: E402


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + str(msg), flush=True)


def state():
    return A.summarize(A.read_state())


def pos(s):
    t = s.get("player") or {}
    return t.get("x", 0), t.get("y", 0)


def in_mine(loc):
    loc = str(loc or "")
    return loc.startswith("UndergroundMine")


def enter_mine():
    s = state()
    if in_mine(s.get("location")):
        return True
    A.send_order({"actionType": "warp_to", "location": "Mine"})
    time.sleep(4)
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


def mine_some(n=8):
    s = state()
    brk = [t for t in (s.get("tiles") or []) if t.get("breakable")]
    brk.sort(key=lambda t: abs(t["x"] - pos(s)[0]) + abs(t["y"] - pos(s)[1]))
    done = 0
    for t in brk[:n]:
        A.send_order({"actionType": "walk_to", "x": t["x"] + 1, "y": t["y"]})
        time.sleep(0.6)
        A.send_order({"actionType": "use_tool", "tool": "pickaxe", "x": t["x"], "y": t["y"]})
        time.sleep(0.8)
        done += 1
    return done, len(brk)


def find_ladder_down():
    s = state()
    for t in (s.get("tiles") or []):
        if not t.get("interactable"):
            continue
        if "上梯" in str(t.get("obj")):
            continue
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
            A.send_order({"actionType": "walk_to", "x": lx, "y": ly + 1})
            time.sleep(1.5)
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
    A.send_order({"actionType": "warp_to", "location": "Farm"})
    time.sleep(3)


if __name__ == "__main__":
    main()
