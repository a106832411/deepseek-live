"""拓展玩法：采集 / 砍树 / 机器 / 深层挖矿。用法：--activity forage|chop|machines|deep_mine --minutes 10 [--level 20]"""
import argparse
import sys
import time

sys.path.insert(0, r"D:\live\deepseek-live\stardew-mod\agent")
import stardew_agent as A  # noqa: E402
import stardew_mine_session as MS  # noqa: E402


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + str(msg), flush=True)


def state():
    # 必须用原始扫描：summarize 只保留十几格，树/石头会「看不见」
    raw = A.read_state()
    try:
        import json as _json
        return _json.loads(_json.dumps(raw))
    except Exception:
        return raw


def pos(s):
    t = s.get("player") or {}
    return t.get("x", 0), t.get("y", 0)


def dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


OBSTACLE = ("草", "树", "嫩枝", "石头", "罐头瓶", "宝箱", "熔炉", "木箱", "树桩", "大树")


def stand_then_interact(x, y):
    """站到梯子四周任一格再交互（只试 y+1 经常站不过去）。"""
    for cx, cy in ((x - 1, y), (x + 1, y), (x, y + 1), (x, y - 1), (x - 2, y), (x + 2, y)):
        A.send_order({"actionType": "walk_to", "x": cx, "y": cy})
        time.sleep(1.6)
        t = (state().get("player") or {}).get("tile") or {}
        if (t.get("x"), t.get("y")) == (cx, cy):
            break
    A.send_order({"actionType": "interact", "x": x, "y": y})
    time.sleep(3.5)


def foragables(s):
    out = []
    for t in (s.get("tiles") or []):
        nm = str(t.get("obj") or "")
        if not nm or nm in OBSTACLE:
            continue
        if t.get("interactable") or t.get("breakable"):
            out.append(t)
    return out


def trees(s):
    return [t for t in (s.get("tiles") or []) if str(t.get("obj") or "") == "树"]


def act_forage(minutes=8):
    end = time.time() + minutes * 60
    got = 0
    while time.time() < end:
        s = state()
        cands = foragables(s)
        px, py = pos(s)
        cands.sort(key=lambda t: dist((t["x"], t["y"]), (px, py)))
        if not cands:
            log("  没看到采集物，换个地方")
            A.send_order({"actionType": "walk_to", "x": px + 6, "y": py + 4})
            time.sleep(3)
            continue
        for t in cands[:6]:
            x, y = t["x"], t["y"]
            A.send_order({"actionType": "walk_to", "x": x, "y": y + 1})
            time.sleep(0.8)
            A.send_order({"actionType": "interact", "x": x, "y": y})
            time.sleep(0.6)
            got += 1
        log(f"  采了 {got} 个（还剩 {len(cands)}）")
    return got


def act_chop(minutes=8):
    end = time.time() + minutes * 60
    n = 0
    while time.time() < end:
        s = state()
        ts = trees(s)
        if not ts:
            log("  附近没树了")
            break
        px, py = pos(s)
        ts.sort(key=lambda t: dist((t["x"], t["y"]), (px, py)))
        for t in ts[:6]:
            x, y = t["x"], t["y"]
            A.send_order({"actionType": "walk_to", "x": x + 1, "y": y})
            time.sleep(0.7)
            A.send_order({"actionType": "use_tool", "tool": "axe", "x": x, "y": y})
            time.sleep(0.7)
            A.send_order({"actionType": "walk_to", "x": x, "y": y})
            time.sleep(0.5)
            A.send_order({"actionType": "pickup_at", "x": x, "y": y})
            time.sleep(0.4)
            n += 1
        log(f"  砍了 {n} 棵")
    return n


def act_machines():
    s = state()
    inv = (s.get("player") or {}).get("inventory") or []
    A.send_order({"actionType": "collect_machines"})
    time.sleep(2)
    ore = next((x for x in inv if "Ore" in x), None)
    log(f"  矿石: {ore}")
    A.send_order({"actionType": "smelt", "ore": "Copper", "times": 10})
    time.sleep(6)
    A.send_order({"actionType": "smelt", "ore": "Iron", "times": 10})
    time.sleep(6)
    A.send_order({"actionType": "collect_machines"})
    time.sleep(2)
    s2 = state()
    inv2 = (s2.get("player") or {}).get("inventory") or []
    log(f"  背包: {[x for x in inv2 if 'Bar' in x or 'Ore' in x or 'Coal' in x]}")


def act_deep_mine(level=20, minutes=8):
    A.send_order({"actionType": "mine_goto", "level": level})
    time.sleep(4)
    s = state()
    log(f"  mine_goto {level} → {s.get('location')}")
    if not MS.in_mine(s.get("location")):
        return
    # mine_goto 只是落到该层入口（电梯/上梯/下梯三件套），要再点「下梯子」才进真正的矿层
    for _ in range(3):
        s = state()
        lad = MS.find_ladder_down()
        if lad and "下梯" in str(lad.get("obj")):
            stand_then_interact(lad["x"], lad["y"])
            s = state()
            log(f"  进入矿层 → {s.get('location')} tiles={len(s.get('tiles') or [])}")
            if len(s.get("tiles") or []) > 6:
                break
        else:
            break
    end = time.time() + minutes * 60
    while time.time() < end:
        s = state()
        if MS.game_minutes(s) >= 2130:
            log("  到 21:30 了，收工")
            break
        if not MS.in_mine(s.get("location")):
            log(f"  不在矿里了（{s.get('location')}），收工")
            break
        n, total = MS.mine_some(8)
        ladder = MS.find_ladder_down()
        log(f"  [{s.get('location')}] 敲 {n}/{total}")
        if ladder and "下梯" in str(ladder.get("obj")):
            stand_then_interact(ladder["x"], ladder["y"])
        elif total == 0:
            # 这一层没石头（或只是入口），回电梯换个层
            A.send_order({"actionType": "warp_to", "location": "Farm"})
            time.sleep(2)
            break
    A.send_order({"actionType": "warp_to", "location": "Farm"})
    time.sleep(2)
    A.send_order({"actionType": "smelt", "ore": "Copper", "times": 10})
    time.sleep(5)
    A.send_order({"actionType": "smelt", "ore": "Iron", "times": 10})
    time.sleep(5)
    A.send_order({"actionType": "collect_machines"})
    time.sleep(2)
    log("  回农场（并熔炼）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity", required=True)
    ap.add_argument("--minutes", type=float, default=8)
    ap.add_argument("--level", type=int, default=20)
    a = ap.parse_args()
    log(f"===== {a.activity} 开始 =====")
    if a.activity == "forage":
        act_forage(a.minutes)
    elif a.activity == "chop":
        act_chop(a.minutes)
    elif a.activity == "machines":
        act_machines()
    elif a.activity == "deep_mine":
        act_deep_mine(a.level, a.minutes)
    else:
        log("未知活动")
    s = state()
    log(f"===== 结束：{s.get('location')} 钱={(s.get('player') or {}).get('money')} =====")


if __name__ == "__main__":
    main()
