#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""农场管家（确定性 + 看门狗）：清杂物 → 浇水 → 收获 → 播种 → 出货 → 钓鱼。

不依赖大模型，动作全部走 WhalePlayer 的可见操作。
看门狗：游戏时间/坐标停滞超过 120 秒 → 自动重启游戏并继续。
日志：D:\\live\\logs\\supervisor.log
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stardew_agent as A  # noqa: E402

LOG = r"D:\live\logs\supervisor.log"
GAME = r"D:\steam\steamapps\common\Stardew Valley"
_fh = None


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    global _fh
    if _fh is None:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        _fh = open(LOG, "a", encoding="utf-8")
    _fh.write(line + "\n")
    _fh.flush()


def state():
    return A.summarize(A.read_state())


def pos(s):
    t = s.get("tile") or {}
    return int(t.get("x", -99)), int(t.get("y", -99))


# ---------------- 看门狗 ----------------
_watch = {"time": None, "pos": None, "since": time.time()}


def restart_game():
    log("  ⚠ 看门狗：游戏软锁，重启游戏")
    try:
        subprocess.run(["taskkill", "/F", "/IM", "StardewModdingAPI.exe"],
                       capture_output=True, text=True, errors="replace")
    except Exception:
        pass
    time.sleep(6)
    try:
        subprocess.Popen([os.path.join(GAME, "StardewModdingAPI.exe")], cwd=GAME)
    except Exception as e:
        log(f"  重启失败：{e}")
        return False
    t0 = time.time()
    while time.time() - t0 < 180:
        time.sleep(5)
        try:
            s = state()
            if pos(s)[0] != -99 and s.get("location"):
                log("  ✅ 游戏已恢复")
                _watch.update({"time": s.get("time"), "pos": pos(s), "since": time.time()})
                return True
        except Exception:
            pass
    log("  ❌ 重启后仍未恢复")
    return False


def watchdog():
    """游戏时间+坐标都停滞 >120s 就重启。"""
    try:
        s = state()
    except Exception:
        return True
    p = pos(s)
    now = time.time()
    if p[0] == -99:
        if now - _watch["since"] > 30:
            log("  ⚠ 看门狗：玩家坐标无效（软锁）")
            _watch["since"] = now
            return restart_game()
        return True
    if p == _watch["pos"] and s.get("time") == _watch["time"]:
        if now - _watch["since"] > 120:
            log(f"  ⚠ 看门狗：{p} 时间 {s.get('time')} 停滞 {int(now - _watch['since'])}s")
            _watch["since"] = now
            return restart_game()
    else:
        _watch["time"] = s.get("time")
        _watch["pos"] = p
        _watch["since"] = now
    return True


# ---------------- 动作 ----------------
def go(x, y, timeout=20.0):
    s = state()
    px, py = pos(s)
    if abs(px - x) + abs(py - y) <= 1:
        return True
    A.send_order({"actionType": "walk_to", "x": x, "y": y})
    ok = A.wait_pos((x, y), timeout=min(timeout, 2.0 + (abs(px - x) + abs(py - y)) * 0.35))
    if not ok:
        A.send_order({"actionType": "walk_to", "x": x, "y": y})
        ok = A.wait_pos((x, y), timeout=6.0)
    return ok


def use(tool, x, y):
    A.send_order({"actionType": "use_tool", "tool": tool, "x": x, "y": y})
    time.sleep(0.8)


def tile_at(s, x, y):
    for t in s.get("tiles") or []:
        if t["x"] == x and t["y"] == y:
            return t
    return None


def tool_for(t):
    name = str(t.get("obj") or "")
    if any(k in name for k in ("Stone", "石头", "矿", "Geode")):
        return "pickaxe"
    return "axe"


def stand_next_to(x, y):
    """站到目标的四个正交相邻格之一。"""
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        if go(x + dx, y + dy, 8):
            return True
    return False


def clear_one(s, t, tries=3):
    x, y = t["x"], t["y"]
    tool = tool_for(t)
    for _ in range(tries):
        if not go(x, y, 12) and not stand_next_to(x, y):
            return False
        use(tool, x, y)
        s2 = state()
        after = tile_at(s2, x, y)
        if after is None or not after.get("breakable"):
            return True
    return False


def water_one(s, t):
    x, y = t["x"], t["y"]
    if not go(x, y, 15) and not stand_next_to(x, y):
        return False
    use("wateringcan", x, y)
    after = tile_at(state(), x, y)
    return bool(after and str(after.get("waterState")) == "已浇")


def harvest_one(s, t):
    x, y = t["x"], t["y"]
    if not go(x, y, 15) and not stand_next_to(x, y):
        return False
    A.send_order({"actionType": "interact", "x": x, "y": y})
    time.sleep(0.6)
    after = tile_at(state(), x, y)
    return after is None or not after.get("cropReady")


def plant_one(s, x, y):
    if not go(x, y, 15) and not stand_next_to(x, y):
        return False
    A.send_order({"actionType": "plant", "x": x, "y": y, "seed": "Bok Choy Seeds"})
    time.sleep(0.7)
    after = tile_at(state(), x, y)
    return bool(after and after.get("crop"))


def ensure_farm():
    s = state()
    if str(s.get("location")) == "Farm":
        return True
    A.send_order({"actionType": "goto", "location": "Farm"})
    for _ in range(120):
        time.sleep(1)
        s = state()
        if str(s.get("location")) == "Farm":
            return True
    A.send_order({"actionType": "exit"})
    time.sleep(5)
    return str(state().get("location")) == "Farm"


def farm_round():
    if not ensure_farm():
        log(f"  回农场失败：{state().get('location')}")
        return 0
    n = 0
    s = state()
    me = pos(s)
    clutter = sorted([t for t in (s.get("tiles") or []) if t.get("breakable")],
                     key=lambda t: abs(t["x"] - me[0]) + abs(t["y"] - me[1]))
    for t in clutter[:15]:
        ok = clear_one(s, t)
        log(f"  清杂物 ({t['x']},{t['y']}) {t.get('obj')} → {'成功' if ok else '失败'}")
        n += 1
    s = state()
    for t in [t for t in (s.get("tiles") or []) if t.get("crop") and str(t.get("waterState")) != "已浇"][:8]:
        ok = water_one(s, t)
        log(f"  浇水 ({t['x']},{t['y']}) → {'成功' if ok else '失败'}")
        n += 1
    s = state()
    for t in [t for t in (s.get("tiles") or []) if t.get("cropReady")][:8]:
        ok = harvest_one(s, t)
        log(f"  收获 ({t['x']},{t['y']}) → {'成功' if ok else '失败'}")
        n += 1
    s = state()
    tilled = [t for t in (s.get("tiles") or []) if t.get("tilled")]
    if any("Bok Choy" in x for x in (s.get("inventory") or [])) and tilled:
        for t in tilled[:6]:
            ok = plant_one(s, t["x"], t["y"])
            log(f"  播种 ({t['x']},{t['y']}) → {'成功' if ok else '失败'}")
            n += 1
    s = state()
    if any(k in " ".join(s.get("inventory") or []) for k in ("Parsnip", "Bok Choy", "Potato", "Melon", "Wheat")):
        A.send_order({"actionType": "ship"})
        time.sleep(1)
        log("  出货：作物投进出货箱")
        n += 1
    return n


def trip(dest, rounds=3):
    log(f"  → 前往 {dest}")
    A.send_order({"actionType": "goto", "location": dest})
    for _ in range(150):
        time.sleep(1)
        if str(state().get("location")) == dest:
            break
    if str(state().get("location")) != dest:
        log(f"  没走到 {dest}（现在 {state().get('location')}）")
        return
    for _ in range(rounds):
        s = state()
        if dest == "Beach":
            A.send_order({"actionType": "fish"})
            time.sleep(3)
            log("  钓鱼：抛竿一次")
        else:
            rocks = sorted([t for t in (s.get("tiles") or []) if t.get("breakable")],
                           key=lambda t: abs(t["x"] - pos(s)[0]) + abs(t["y"] - pos(s)[1]))
            if rocks:
                ok = clear_one(s, rocks[0])
                log(f"  挖矿：敲 ({rocks[0]['x']},{rocks[0]['y']}) → {'成功' if ok else '失败'}")
            else:
                log("  挖矿：附近没石头")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=120)
    ap.add_argument("--pattern", default="farm,farm,farm,fish")
    a = ap.parse_args()
    end = time.time() + a.minutes * 60
    pattern = [x.strip() for x in a.pattern.split(",") if x.strip()]
    idx = 0
    log(f"===== 管家开始，计划 {a.minutes} 分钟，节奏 {pattern} =====")
    while time.time() < end:
        if not watchdog():
            time.sleep(10)
            continue
        step = pattern[idx % len(pattern)]
        idx += 1
        s0 = state()
        clutter = len([t for t in (s0.get("tiles") or []) if t.get("breakable")])
        log(f"[{idx}] 阶段={step} 位置={s0.get('location')} 时间={s0.get('time')} 钱={s0.get('money')} "
            f"体力={s0.get('stamina')} 杂物={clutter} 成熟={len([t for t in (s0.get('tiles') or []) if t.get('cropReady')])}")
        try:
            if step == "farm":
                n = farm_round()
                log(f"  农场轮完成，动作 {n} 个")
            elif step == "fish":
                trip("Beach")
            elif step == "mine":
                trip("Mine")
        except Exception as e:
            log(f"  阶段异常：{e}")
        time.sleep(1)
    log("===== 管家结束 =====")


if __name__ == "__main__":
    main()
