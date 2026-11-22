#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""星露谷挂机守卫：只负责「发现卡住 → 自己救回来」，不做农场操作。

规则：
  1. 循环进程没了 → 重新拉起 stardew_overnight.py
  2. player_state.json 超过 180 秒没更新 → 游戏软锁 → 重启游戏 + 循环
  3. 人物坐标长时间不变但游戏时间在走 → 先 unstick，再 warp 回农场，最后重启循环
日志：D:\\live\\logs\\guard.log
"""
import json
import os
import subprocess
import sys
import time

HERE = r"D:\live\deepseek-live\stardew-mod\agent"
sys.path.insert(0, HERE)
import stardew_agent as A  # noqa: E402

LOG = r"D:\live\logs\guard.log"
STATE = r"D:\steam\steamapps\common\Stardew Valley\Mods\WhalePlayer\player_state.json"
GAME = r"D:\steam\steamapps\common\Stardew Valley"
LAUNCH = r"D:\live\.tools\launch_stardew.ps1"
PY = r"D:\live\.tools\python310\python.exe"

_fh = open(LOG, "a", encoding="utf-8")


def log(m):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), m)
    try:
        print(line, flush=True)
    except Exception:
        pass
    _fh.write(line + "\n")
    _fh.flush()


def loop_running():
    """用 PowerShell 查进程命令行，稳一点（wmic 在中文环境容易查不到）。"""
    try:
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'stardew_overnight' } | Measure-Object | Select-Object -ExpandProperty Count")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, errors="replace",
                             timeout=20).stdout.strip()
        return int(out or 0) > 0
    except Exception:
        # 2026-11-21 修：查询失败/超时以前一律当「没在跑」，会每 45 秒再拉一个循环，多个循环抢人物。
        # 现在查不出来就当没变化，宁可不动作。
        return True


def fishing_now():
    """当前是不是「本来就不该动」的状态：钓鱼、进店买东西、整理背包。

    这类场景人物本来就站着，不能当卡住处理（2026-11-21：钓鱼和买种子都被误 unstick 过）。
    """
    try:
        with open(r"D:\live\logs\ai_status.json", encoding="utf-8") as f:
            st = json.load(f)
        txt = (str(st.get("activity") or "") + " " + str(st.get("order") or "")).lower()
        if ("钓" in txt) or ("fish" in txt) or ("买" in txt) or ("shop" in txt) or ("整理" in txt):
            return True
    except Exception:
        pass
    try:
        loc = str(A.summarize(A.read_state()).get("location") or "")
        if loc in ("SeedShop", "Shed", "FarmHouse"):
            return True
    except Exception:
        pass
    return False


def start_loop():
    log("  拉起通宵循环")
    env = dict(os.environ)
    env["STARDEW_LLM_PLAN"] = "1"          # 2026-11-22：让本地 qwen 参与「今天种什么」
    subprocess.Popen([PY, "-u", "stardew_overnight.py", "70"], cwd=HERE,
                     stdout=open(r"D:\live\logs\overnight_stdout.log", "a", encoding="utf-8"),
                     stderr=subprocess.STDOUT, creationflags=0x00000008, env=env)


def restart_game():
    log("  ⚠ 重启游戏")
    for exe in ("StardewModdingAPI.exe", "Stardew Valley.exe"):
        try:
            subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True, text=True, errors="replace")
        except Exception:
            pass
    time.sleep(6)
    try:
        subprocess.Popen(["powershell", "-ExecutionPolicy", "Bypass", "-File", LAUNCH],
                         creationflags=0x00000008)
    except Exception as e:
        log(f"  重启失败：{e}")


def main():
    log("===== 守卫启动 =====")
    last_state_m = 0
    still = 0
    last_pos = None
    last_time = None
    last_restart = 0.0
    frozen = 0
    while True:
        try:
            # ① 循环进程
            if not loop_running():
                start_loop()

            # ② 状态文件新鲜度（重启后给 5 分钟宽限，别反复重启把游戏吊死在启动阶段）
            m = os.path.getmtime(STATE) if os.path.exists(STATE) else 0
            stale = m and last_state_m and (time.time() - m) > 180
            if stale and (time.time() - last_restart) > 300:
                log("  ⚠ 状态文件 180 秒没更新，判定软锁")
                restart_game()
                last_restart = time.time()
                # 等新存档真的读进来，最多 180 秒；读到就继续，读不到下一轮再来
                t0 = time.time()
                while time.time() - t0 < 180:
                    time.sleep(5)
                    if os.path.exists(STATE) and os.path.getmtime(STATE) > m:
                        log("  游戏已重新读档（状态文件更新了）")
                        break
                if not loop_running():
                    start_loop()
                last_state_m = os.path.getmtime(STATE) if os.path.exists(STATE) else 0
                continue
            last_state_m = m

            # ③ 坐标停滞（钓鱼时本来就不动，直接跳过）
            s = A.summarize(A.read_state())
            t = s.get("tile") or {}
            p = (int(t.get("x", -99)), int(t.get("y", -99)))
            now_t = str(s.get("time"))

            # ③.5 游戏时钟停滞：人物卡在 canMove=false（钓鱼状态坏掉）时，坐标和时间都不动，
            # 上面两条都抓不到。实测 05:41-05:57 卡在 Beach 10:10，只能重启游戏。
            if now_t == last_time:
                frozen += 1
            else:
                frozen = 0
            if frozen == 6:            # 6 × 45s ≈ 4.5 分钟
                log("  ⚠ 游戏时间停在 %s 超过 4 分钟，先强行复位钓鱼/动作状态" % now_t)
                for order in ({"actionType": "stop_fish"}, {"actionType": "unstick"},
                              {"actionType": "wake"}, {"actionType": "close_menu"}):
                    try:
                        A.send_order(order)
                    except Exception:
                        pass
                    time.sleep(1.2)
            elif frozen >= 12 and (time.time() - last_restart) > 300:
                log("  ⚠ 复位无效，时钟还是 %s → 重启游戏" % now_t)
                restart_game()
                last_restart = time.time()
                time.sleep(20)
                if not loop_running():
                    start_loop()
                frozen = 0
                continue

            if fishing_now():
                still = 0
            elif p == last_pos and now_t != last_time:
                still += 1
            else:
                still = 0
            last_pos, last_time = p, now_t

            if still == 4:
                log("  ⚠ 位置 %s 3 分钟没动（游戏时间在走），unstick" % (p,))
                A.send_order({"actionType": "unstick"})
            elif still == 8:
                # 2026-11-22：unstick 无效时先问本地模型（从校验过的候选里挑一步），不行再走老兜底
                log("  ⚠ unstick 无效，先让本地模型出主意")
                try:
                    picked = A.llm_rescue(log_fn=log, reason="位置 %s 连续 6 分钟没动" % (p,))
                except Exception as e:
                    picked = None
                    log("  LLM 救援异常：%s" % e)
                if not picked:
                    log("  ⚠ 模型没给建议，传送回农场")
                    A.send_order({"actionType": "warp_to", "location": "Farm"})
            elif still >= 14:
                log("  ⚠ 还是卡着，重启循环")
                try:
                    subprocess.run(["wmic", "process", "where", "name='python.exe' and CommandLine like '%stardew_overnight%'", "call", "terminate"],
                                   capture_output=True, text=True, errors="replace")
                except Exception:
                    pass
                time.sleep(3)
                start_loop()
                still = 0
        except Exception as e:
            log("守卫异常: %s" % e)
        time.sleep(45)


if __name__ == "__main__":
    main()
