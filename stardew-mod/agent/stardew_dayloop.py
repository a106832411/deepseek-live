#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按游戏天循环：早上农场 → 白天挖矿 → 下午钓鱼 → 晚上回家睡觉。

用法：python stardew_dayloop.py --days 3 --mine-until 13:00 --fish-until 22:30 --home-at 23:00
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stardew_agent as A  # noqa: E402

LOG = r"D:\live\logs\dayloop.log"
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


def gm(t):
    return A._mins(t)


def run_script(name, args, timeout=2400):
    import subprocess
    path = os.path.join(HERE, name)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    log("  $ python %s %s" % (name, " ".join(args)))
    try:
        r = subprocess.run([sys.executable, path] + args, cwd=HERE, capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8", errors="replace", env=env)
        for line in (r.stdout or "").strip().splitlines()[-25:]:
            log("    | " + line)
        for line in (r.stderr or "").strip().splitlines()[-6:]:
            log("    ! " + line)
        log("  ← %s 结束 code=%s" % (name, r.returncode))
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        log("  ← %s 超时" % name)
        return False
    except Exception as e:
        log("  ← %s 异常 %s" % (name, e))
        return False


def morning_farm():
    log("[早上] 农场：整理箱子 + 收割 + 补种")
    A.send_order({"actionType": "close_menu"})
    time.sleep(1)
    A.handle_midnight(log_fn=log)
    # 出门前整理背包：除了 5 件工具，其它（含种子、作物、矿石）都收进箱子
    try:
        A.go_out_tidy(log_fn=log)
    except Exception as e:
        log("  出门整理异常: %s" % e)
    A.tidy_chests(log_fn=log)
    s = state()
    if str(s.get("location")) != "Farm":
        A.travel_to("Farm", hop_timeout=200, log_fn=log)
    # 换季第一天：先清掉杂草/过期作物/石头/树
    try:
        A.season_day_one_cleanup(log_fn=log)
    except Exception as e:
        log("  换季清理异常: %s" % e)
    # 季末（只剩 1~2 天）先抢收，换季会把没熟的也一起清掉
    try:
        A.season_end_harvest(log_fn=log)
    except Exception as e:
        log("  季末抢收异常: %s" % e)
    # 收完立刻续种（同一块固定区域补满）
    got, planted = A.harvest_and_replant(log_fn=log)
    A.send_order({"actionType": "save"})
    time.sleep(5)
    log("  收割 %d，补种 %d" % (got, planted))


def mine_phase(until):
    log("[白天] 挖矿，到 %s 收工" % until)
    s = state()
    t_before = s.get("time")
    ok = run_script("stardew_mine2.py", ["--rounds", "12", "--levels", "45,50,55", "--until", until], timeout=3000)
    s2 = state()
    log("  挖矿结束: %s → %s （位置 %s）" % (t_before, s2.get("time"), s2.get("location")))
    # 如果还在矿里，先离开
    if str(s2.get("location") or "").startswith("UndergroundMine") or str(s2.get("location")) == "Mine":
        A.send_order({"actionType": "leave_mine"})
        time.sleep(4)
        log("  离开矿洞 → %s" % state().get("location"))
    return ok


def fish_phase(until):
    log("[下午] 钓鱼，到 %s 收工" % until)
    s = state()
    t_before = s.get("time")
    ok = run_script("stardew_fishsession.py", ["--minutes", "200", "--until", until], timeout=3600)
    A.send_order({"actionType": "stop_fish"})
    time.sleep(1)
    s2 = state()
    log("  钓鱼结束: %s → %s （位置 %s）" % (t_before, s2.get("time"), s2.get("location")))
    return ok


def go_home(home_at):
    log("[晚上] 目标 %s 前回家睡觉" % home_at)
    s = state()
    t0 = s.get("time")
    loc0 = s.get("location")
    ok = A.go_home_sleep(log_fn=log)
    s2 = state()
    log("  回家: %s(%s) → %s(%s)，用时约 %d 游戏分钟 | 现在 天%s %s"
        % (loc0, t0, s2.get("location"), s2.get("time"), max(0, gm(s2.get("time")) - gm(t0)) % 1440,
           s2.get("day"), s2.get("time")))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--activity-until", type=str, default="21:00", help="当天主活动收工时间")
    ap.add_argument("--home-at", type=str, default="01:00")
    ap.add_argument("--start-with", type=str, default="mine", choices=["mine", "fish"])
    args = ap.parse_args()
    log("===== 按天循环（%d 天，一天挖矿一天钓鱼）activity→%s home→%s =====" % (
        args.days, args.activity_until, args.home_at))

    # 主活动轮换：mine / fish 交替
    order = ["mine", "fish"] if args.start_with == "mine" else ["fish", "mine"]

    for d in range(1, args.days + 1):
        act = order[(d - 1) % 2]
        s = state()
        log("——— 第 %d 轮：%s（天%s %s %s 钱%s）———" % (
            d, "挖矿" if act == "mine" else "钓鱼", s.get("day"), s.get("season"), s.get("time"), s.get("money")))
        # 每轮开始先修正可能错乱的坐标（防止卡在门外坐标）
        try:
            A.send_order({"actionType": "fix_position"})
            time.sleep(1.5)
        except Exception:
            pass
        try:
            morning_farm()
        except Exception as e:
            log("  农场异常: %s" % e)

        s = state()
        if gm(s.get("time")) < gm(args.activity_until):
            # 农活干完还有时间才去钓鱼/挖矿（按天气和时段自动选）
            try:
                A.spare_time_activity(until=args.activity_until, log_fn=log)
            except Exception as e:
                log("  余时活动异常: %s" % e)
        else:
            log("  时间已过 %s，跳过钓鱼/挖矿" % args.activity_until)

        # 时间太晚则立即回家（保证睡觉前到家）
        try:
            if A.must_go_home("22:00", log_fn=log):
                log("  时间晚了，直接回家")
        except Exception:
            pass
        go_home(args.home_at)

    s = state()
    log("结束: 天%s %s 钱%s" % (s.get("day"), s.get("time"), s.get("money")))
    log("===== 按天循环结束 =====")


if __name__ == "__main__":
    main()
