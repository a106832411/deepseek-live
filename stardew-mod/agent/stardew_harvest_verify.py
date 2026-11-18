#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""蔓越莓收割验证（默认只检查，不动游戏）。

用法：
  python stardew_harvest_verify.py --check          # 只报告：熟没熟、还差几天
  python stardew_harvest_verify.py --wait 6         # 连续睡 6 天（真改存档进度）
  python stardew_harvest_verify.py --harvest        # 镰刀扫荡收割并报告收益

背景（2026-11-17 实测）：蔓越莓真实生长 6 天，秋 Y4 第 5 天时 dayOfCurrentPhase=0，
所以第 11 天才能收；在那之前 --harvest 会直接返回 0。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stardew_agent as A  # noqa: E402

STATE = os.path.join(A.PLAYER_DIR, "player_state.json")
SAVE = os.path.join(os.environ["APPDATA"], r"StardewValley\Saves\MoonFarm_453567724\MoonFarm_453567724")


def log(m):
    print(time.strftime("[%H:%M:%S] ") + str(m), flush=True)


def state():
    return A.read_state()


def crop_tiles(st):
    return [t for t in (st.get("tiles") or []) if t.get("crop")]


def report():
    st = state()
    pl = st.get("player") or {}
    tiles = crop_tiles(st)
    ready = [t for t in tiles if t.get("cropReady")]
    log("位置 %s | 时间 %s | 第 %s 天 %s | 钱 %s" % (
        st.get("location"), st.get("time"), st.get("day"), st.get("season"), pl.get("money")))
    log("作物格 %d，可收 %d" % (len(tiles), len(ready)))
    if tiles:
        from collections import Counter
        log("作物: %s" % Counter(t.get("crop") for t in tiles).most_common(4))
        log("daysLeft(注意：当前仓库版本对 99999 哨兵未过滤，值是垃圾): %s"
            % Counter(t.get("daysLeft") for t in tiles).most_common(4))
    return len(ready)


def save_truth():
    """离线读存档，给出真实剩余天数（排除 99999 哨兵）。"""
    import re
    if not os.path.exists(SAVE):
        log("找不到存档文件，跳过离线核对")
        return None
    s = open(SAVE, encoding="utf-8", errors="replace").read()
    day = int(re.search(r"<dayOfMonthForSaveGame>(\d+)", s).group(1))
    season_n = int(re.search(r"<seasonForSaveGame>(\d+)", s).group(1))
    pat = re.compile(
        r"<value><TerrainFeature xsi:type=\"HoeDirt\">(.*?)</TerrainFeature>", re.S)
    lefts = {}
    for m in pat.finditer(s):
        blk = m.group(1)
        if "<crop>" not in blk or "<indexOfHarvest>282</indexOfHarvest>" not in blk:
            continue
        phases = [int(v) for v in re.findall(r"<int>(\d+)</int>", blk)]
        phases = [p for p in phases if p < 1000]
        m_cur = re.search(r"<dayOfCurrentPhase>(\d+)</dayOfCurrentPhase>", blk)
        cur = int(m_cur.group(1)) if m_cur else 0
        full = "<fullGrown>true</fullGrown>" in blk or "<fullyGrown>true</fullyGrown>" in blk
        left = 0 if full else max(0, sum(phases) - sum(phases[:cur]))
        lefts[left] = lefts.get(left, 0) + 1
    log("存档（第 %d 天, season=%d）：蔓越莓剩余天数分布 %s" % (day, season_n, lefts))
    return lefts


def wait_days(n):
    for i in range(n):
        st = state()
        d0 = st.get("day")
        log("第 %d/%d 次睡觉（当前第 %s 天 %s）" % (i + 1, n, d0, st.get("time")))
        ok = A.go_home_sleep(log_fn=log)
        for _ in range(30):
            time.sleep(2)
            st2 = state()
            if st2.get("day") != d0:
                break
        log("→ sleep=%s，现在第 %s 天 %s" % (ok, st2.get("day"), st2.get("time")))
    return True


def harvest():
    st = state()
    pl = st.get("player") or {}
    money0 = pl.get("money")
    inv0 = [str(x) for x in (pl.get("inventory") or [])]
    got = A.harvest_with_scythe(log_fn=log)
    st2 = state()
    pl2 = st2.get("player") or {}
    log("收割 %d 株；钱 %s → %s" % (got, money0, pl2.get("money")))
    inv1 = [str(x) for x in (pl2.get("inventory") or [])]
    log("背包新增: %s" % ([x for x in inv1 if x not in inv0],))
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--wait", type=int, default=0)
    ap.add_argument("--harvest", action="store_true")
    args = ap.parse_args()
    if not (args.check or args.wait or args.harvest):
        args.check = True

    if args.wait:
        wait_days(args.wait)
    if args.check or args.wait:
        report()
        save_truth()
    if args.harvest:
        n = report()
        if n == 0:
            log("没有成熟作物，收割跳过（先 --wait 到第 11 天）")
            return 0
        harvest()
    return 0


if __name__ == "__main__":
    sys.exit(main())
