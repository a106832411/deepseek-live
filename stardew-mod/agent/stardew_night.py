#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""夜间 6 小时循环：挖矿 / 钓鱼 / 种地 轮换，带自愈（游戏崩溃重开、菜单卡死、工具丢失）。

用法（后台常驻，不受外层终端影响）：
    python stardew_night.py --hours 6

日志：D:\\live\\logs\\night\\night.log
报告：D:\\live\\logs\\night\\NIGHT-REPORT.md（每轮更新，早上看这个）
"""
import argparse
import os
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stardew_agent as A  # noqa: E402

GAME_DIR = r"D:\steam\steamapps\common\Stardew Valley"
SMAPI = os.path.join(GAME_DIR, "StardewModdingAPI.exe")
STATE = os.path.join(GAME_DIR, r"Mods\WhalePlayer\player_state.json")
PY = r"D:\live\.tools\python310\python.exe"
OUT_DIR = r"D:\live\logs\night"
LOG = os.path.join(OUT_DIR, "night.log")
REPORT = os.path.join(OUT_DIR, "NIGHT-REPORT.md")

_fh = None


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    global _fh
    if _fh is None:
        os.makedirs(OUT_DIR, exist_ok=True)
        _fh = open(LOG, "a", encoding="utf-8")
    _fh.write(line + "\n")
    _fh.flush()


def state():
    return A.summarize(A.read_state())


def game_alive():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq StardewModdingAPI.exe"],
                             capture_output=True, text=True, timeout=20)
        return "StardewModdingAPI" in (out.stdout or "")
    except Exception:
        return False


def state_fresh(max_age=30):
    try:
        return (time.time() - os.path.getmtime(STATE)) < max_age
    except Exception:
        return False


def ensure_game(timeout=300):
    """确保游戏在跑且状态文件新鲜。"""
    if game_alive() and state_fresh(60):
        return True
    log("  [自愈] 游戏未运行或状态过期 → 启动/重启")
    try:
        subprocess.run(["taskkill", "/F", "/IM", "StardewModdingAPI.exe"], capture_output=True, timeout=20)
    except Exception:
        pass
    time.sleep(5)
    try:
        subprocess.Popen([SMAPI], cwd=GAME_DIR, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=0x00000008)
    except Exception as e:
        log("  启动失败: %s" % e)
        return False
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(10)
        if game_alive() and state_fresh(40):
            log("  [自愈] 游戏已就绪（%ds）" % int(time.time() - t0))
            return True
    log("  [自愈] 游戏仍未就绪")
    return False


def run_script(name, args, timeout):
    """跑一个子脚本，输出写进日志。"""
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        log("  脚本不存在: %s" % name)
        return False
    log("  $ python %s %s" % (name, " ".join(args)))
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        r = subprocess.run([PY, path] + args, cwd=HERE, capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8", errors="replace",
                           env=env)
        for line in (r.stdout or "").strip().splitlines()[-40:]:
            log("    | " + line)
        for line in (r.stderr or "").strip().splitlines()[-10:]:
            log("    ! " + line)
        log("  ← %s 结束 code=%s" % (name, r.returncode))
        if r.returncode == 0:
            return True
        # 编码/末尾打印导致的非零退出：若输出里有正常进度就当作成功
        out = (r.stdout or "") + (r.stderr or "")
        if ("Unexpected" not in out) and ("Traceback" not in out.split("\n")[-3:][0] if out else True):
            if any(k in out for k in ("种下", "收割", "下去后", "收下", "时间", "背包")):
                log("  （非零退出但已产出进度，按成功处理）")
                return True
        return False
    except subprocess.TimeoutExpired:
        log("  ← %s 超时（%ds），继续" % (name, timeout))
        return False
    except Exception as e:
        log("  ← %s 异常: %s" % (name, e))
        return False


def soft_recover():
    """菜单/工具/位置的小修小补。"""
    try:
        A.send_order({"actionType": "stop_fish"})
        time.sleep(0.5)
        A.send_order({"actionType": "close_menu"})
        time.sleep(0.8)
        A.send_order({"actionType": "ensure_tools"})
        time.sleep(0.8)
        A.handle_midnight(log_fn=log)
        A.handle_late_night(log_fn=log)
    except Exception:
        pass


def activity_mine():
    log("[活动] 挖矿 → 回家")
    ok1 = run_script("stardew_mine2.py", ["--rounds", "6", "--levels", "45,50,55"], timeout=2400)
    soft_recover()
    ok2 = run_script("stardew_home.py", ["--sleep"], timeout=1500)
    return ok1 and ok2


def activity_farm():
    log("[活动] 农场（收割/补种/出货/睡觉）")
    return run_script("stardew_farmdaily.py", ["--days", "1"], timeout=1800)


def activity_fish():
    log("[活动] 钓鱼")
    return run_script("stardew_fishsession.py", ["--minutes", "15"], timeout=1500)


ACTIVITIES = [("mine", activity_mine), ("fish", activity_fish), ("farm", activity_farm)]


def write_report(cycles, deadline, history):
    try:
        s = state()
    except Exception:
        s = {}
    inv = s.get("inventory") or []
    lines = []
    lines.append("# 星露谷夜间循环报告\n")
    lines.append("- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("- 计划结束：%s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(deadline)))
    lines.append("- 已完成轮次：%d" % cycles)
    lines.append("")
    lines.append("## 当前状态\n")
    lines.append("| 项目 | 值 |")
    lines.append("|---|---|")
    lines.append("| 位置 | %s %s |" % (s.get("location"), s.get("tile")))
    lines.append("| 游戏时间 | 第 %s 天 %s（%s） |" % (s.get("day"), s.get("time"), s.get("season")))
    lines.append("| 金币 | %s G |" % s.get("money"))
    lines.append("| 背包 | %s |" % ", ".join(inv[:14]))
    lines.append("")
    lines.append("## 活动历史\n")
    lines.append("| 轮次 | 活动 | 结果 | 时间 |")
    lines.append("|---|---|---|---|")
    for h in history[-40:]:
        lines.append("| %s | %s | %s | %s |" % h)
    lines.append("")
    lines.append("## 日志\n")
    lines.append("- 详细日志：`D:\\live\\logs\\night\\night.log`")
    lines.append("- 挖矿：`D:\\live\\logs\\mine2.log`　钓鱼：`D:\\live\\logs\\fishsession.log`　农场：`D:\\live\\logs\\farmdaily.log`")
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        try:
            log("write_report 失败: %s" % e)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    log("========== 夜间循环开始（%.1f 小时）==========" % args.hours)
    deadline = time.time() + args.hours * 3600
    cycles = 0
    fails = 0
    history = []
    idx = 0

    if not ensure_game():
        log("游戏起不来，退出")
        return

    while time.time() < deadline:
        cycles += 1
        name, fn = ACTIVITIES[idx % len(ACTIVITIES)]
        idx += 1
        log("---- 第 %d 轮：%s（剩余 %.0f 分钟）----" % (cycles, name, (deadline - time.time()) / 60))
        soft_recover()
        try:
            ok = fn()
        except Exception:
            ok = False
            log("  活动异常:\n" + traceback.format_exc())
        history.append((cycles, name, "OK" if ok else "失败", time.strftime("%H:%M:%S")))
        log("---- 第 %d 轮 %s：%s ----" % (cycles, name, "成功" if ok else "失败"))

        if not ok:
            fails += 1
            soft_recover()
            if fails >= 4:
                log("  连续失败 %d 次 → 重启游戏" % fails)
                ensure_game()
                fails = 0
        else:
            fails = 0
            try:
                A.send_order({"actionType": "save"})
                time.sleep(3)
            except Exception:
                pass

        write_report(cycles, deadline, history)
        time.sleep(5)

    log("========== 夜间循环结束 ==========")
    write_report(cycles, deadline, history)
    log("报告已写入: %s" % REPORT)


if __name__ == "__main__":
    main()
