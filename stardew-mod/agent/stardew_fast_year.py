"""快速走完一个游戏年：每天 收菜 → 补种 → 睡觉，记录每天数据。

用法：
  python stardew_fast_year.py            # 跑到下一个 春1 就停
  python stardew_fast_year.py --days 20  # 只跑 20 天
"""
import argparse
import csv
import os
import sys
import time

sys.path.insert(0, r"D:\live\deepseek-live\stardew-mod\agent")
import stardew_agent as A  # noqa: E402

OUT = r"D:\live\logs\year-fast.csv"
LOG = r"D:\live\logs\year-fast.log"
TOOL_NAMES = ("Fishing Rod", "Pickaxe", "Hoe", "Axe", "Iridium Scythe", "Scythe")
_fh = None


def log(m):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), m)
    print(line, flush=True)
    global _fh
    if _fh is None:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        _fh = open(LOG, "a", encoding="utf-8")
    _fh.write(line + "\n")
    _fh.flush()


def st():
    return A.read_state()


def tile_of(s):
    t = s.get("tile") or (s.get("player") or {}).get("tile") or {}
    return int(t.get("x", -1)), int(t.get("y", -1))


def inv(s):
    return (s.get("player") or {}).get("inventory") or []


def seeds(s):
    return [x for x in inv(s) if "Seed" in x or "Starter" in x]


def ready(s):
    return [t for t in (s.get("tiles") or []) if t.get("cropReady")]


def tilled(s):
    out = []
    for tok in (s.get("tilled_tiles") or []):
        try:
            a, b = str(tok).split(",")[:2]
            out.append((int(float(a)), int(float(b))))
        except Exception:
            pass
    return out


def do_day(s):
    """一天的农活：镰刀收菜 + 补种。返回统计。"""
    day = s.get("day")
    r = ready(s)
    harvested = 0
    for t in r[:8]:
        A.send_order({"actionType": "walk_to", "x": t["x"], "y": t["y"] + 1})
        time.sleep(0.9)
        A.send_order({"actionType": "use_tool", "tool": "scythe", "x": t["x"], "y": t["y"]})
        time.sleep(1.0)
        harvested += 1
    s2 = st()
    sd = seeds(s2)
    planted = 0
    if sd:
        seed_name = sd[0].split(" x")[0]
        for (x, y) in tilled(s2)[:10]:
            A.send_order({"actionType": "plant", "x": x, "y": y, "seed": seed_name})
            time.sleep(0.5)
            planted += 1
    return harvested, planted, len(r), len(sd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="0=跑到下一个春1")
    a = ap.parse_args()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    new = not os.path.exists(OUT)
    fh = open(OUT, "a", encoding="utf-8", newline="")
    w = csv.writer(fh)
    if new:
        w.writerow(["season", "day", "money", "crops", "ready", "harvested", "planted", "seeds_left", "at"])

    log("===== 快速走年开始 =====")
    A.send_order({"actionType": "wake"})
    time.sleep(1.5)
    A.send_order({"actionType": "close_menu"})
    time.sleep(1)

    n = 0
    start = st()
    log("起点：%s 第%s天 钱=%s" % (start.get("season"), start.get("day"), (start.get("player") or {}).get("money")))

    while True:
        s = st()
        season, day = str(s.get("season")), s.get("day")
        crops = len([t for t in (s.get("tiles") or []) if t.get("crop")])
        rdy = len(ready(s))
        if str(s.get("location")) == "FarmHouse":
            A.send_order({"actionType": "wake"})
            time.sleep(0.8)
            A.send_order({"actionType": "close_menu"})
            time.sleep(0.5)
        harv, plan, rn, sd = do_day(s)
        money = (st().get("player") or {}).get("money")
        w.writerow([season, day, money, crops, rdy, harv, plan, sd, time.strftime("%H:%M:%S")])
        fh.flush()
        log("%s第%s天 钱=%s 作物=%s 收=%s 种=%s 剩种子=%s" % (season, day, money, crops, harv, plan, sd))

        # 睡觉进下一天
        s3 = st()
        if str(s3.get("location")) != "FarmHouse":
            A.travel_to("FarmHouse", hop_timeout=90, log_fn=lambda m: None)
        A.send_order({"actionType": "sleep"})
        t0 = time.time()
        while time.time() - t0 < 60:
            time.sleep(1)
            s4 = st()
            if str(s4.get("day")) != str(day) or str(s4.get("season")) != season:
                break
        n += 1
        s5 = st()
        if a.days and n >= a.days:
            log("已跑 %d 天，结束" % n)
            break
        if a.days == 0 and str(s5.get("season")) in ("spring", "春") and str(s5.get("day")) == "1":
            log("到达下一个春 1，整年跑完")
            break

    log("===== 快速走年结束，共 %d 天 =====" % n)
    fh.close()


if __name__ == "__main__":
    main()
