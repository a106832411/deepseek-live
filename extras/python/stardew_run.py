#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一小时连续跑：种地 → 挖矿 → 钓鱼 轮换，记录每个动作的结果，供事后出修复清单。

用法：python stardew_run.py --minutes 60
日志：D:\\live\\logs\\hour-run.log
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = r"D:\live\logs"
PY = sys.executable


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, "hour-run.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_agent(goal, rounds, timeout=900):
    cmd = [PY, os.path.join(HERE, "stardew_agent.py"), "--loop",
           "--rounds", str(rounds), "--interval", "3", "--goal", goal]
    log(f"▶ 阶段开始：{goal}（{rounds} 轮）")
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        for line in out.splitlines():
            if line.strip():
                log("  " + line)
        log(f"■ 阶段结束：{goal}（{time.time()-t0:.0f}s，exit={p.returncode}）")
    except subprocess.TimeoutExpired:
        log(f"⚠ 阶段超时：{goal}")
    except Exception as e:
        log(f"⚠ 阶段异常：{goal} → {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=60)
    a = ap.parse_args()
    end = time.time() + a.minutes * 60
    log(f"===== 一小时连续跑开始，计划 {a.minutes} 分钟 =====")
    phases = [
        ("farm", "在农场干活：先清杂物，再锄地播种小白菜，给所有没浇水的作物浇水，收获成熟的，最后把作物放进出货箱", 12),
        ("mine", "去矿洞挖矿：goto Mine，然后用镐子敲石头、找矿石和梯子", 10),
        ("fish", "去海滩钓鱼：goto Beach，走到水边用鱼竿钓鱼", 10),
    ]
    idx = 0
    while time.time() < end:
        name, goal, rounds = phases[idx % len(phases)]
        run_agent(goal, rounds)
        idx += 1
        if idx % 3 == 0:
            log("--- 完成一轮 种地/挖矿/钓鱼 循环 ---")
    log("===== 一小时连续跑结束 =====")


if __name__ == "__main__":
    main()
