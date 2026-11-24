#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""钓鱼时段：走到海滩 → 固定钓点 → 钓 N 分钟。

用法：python stardew_fishsession.py --minutes 20
"""
import argparse
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stardew_agent as A  # noqa: E402

LOG = r"D:\live\logs\fishsession.log"
_fh = None
TOOLS = ("Fishing Rod", "Hoe", "Pickaxe", "Watering Can", "Axe", "Iridium Scythe", "Slingshot")


def inv_total(inv):
    """背包里非工具物品的「总数量」（不是堆数）。

    2026-11-23：以前用堆数判断有没有上鱼，鱼叠起来后堆数不变 → 自愈被误触发。
    """
    total = 0
    for it in inv or []:
        if it in TOOLS:
            continue
        m = re.match(r"(.*?)\s*x(\d+)$", it)
        total += int(m.group(2)) if m else 1
    return total


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


def go_to_beach(timeout=400):
    s = state()
    if str(s.get("location")) == "Beach":
        return True
    log("  → 按固定路线去 Beach（绕开 BusStop）")
    ok = A.travel_to("Beach", hop_timeout=220, log_fn=log)
    log("  到达 Beach" if ok else "  没到海滩")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=20)
    ap.add_argument("--until", type=str, default="", help="到点收工 HH:MM")
    args = ap.parse_args()
    log("===== 钓鱼开始（%d 分钟）=====" % args.minutes)
    try:
        A.morning_tidy(log_fn=log)
    except Exception as e:
        log("整理背包异常: %s" % e)
    try:
        if not A.wait_out_festival(log_fn=log):
            log("  节日未结束，跳过本次活动")
            return
    except Exception as e:
        log("  节日处理异常: %s" % e)

    A.send_order({"actionType": "close_menu"})
    time.sleep(1)
    A.send_order({"actionType": "ensure_tools"})
    time.sleep(1)

    if not go_to_beach():
        log("没到海滩，放弃")
        A.send_order({"actionType": "stop_fish"})
        return

    A.send_order({"actionType": "go_fish"})
    time.sleep(12)
    A.protect_scythe_from_fish(log_fn=log)   # 防鱼竿覆盖镰刀（2026-11-21）
    A.send_order({"actionType": "fish"})

    start = time.time()
    base = None
    last_count = 0
    last_change = time.time()
    try:
        while time.time() - start < args.minutes * 60:
            time.sleep(15)
            # 到点收工
            if args.until:
                cur = A._mins(st_date.get("time") if False else state().get("time"))
                if cur >= A._mins(args.until):
                    log("  到点收工（%s >= %s）" % (state().get("time"), args.until))
                    break
            # 00:00 自动回家睡觉
            try:
                if A.handle_midnight(log_fn=log):
                    # 2026-11-24：长时段验证时睡觉不等于收工——睡醒继续钓，
                    # 由循环里「不在海滩」那条路自动走回去重开一竿。
                    log("  已到 00:00，回家睡觉，睡醒继续")
                    time.sleep(10)
                    last_change = time.time()
                    continue
            except Exception as e:
                log("  00:00 检查异常: %s" % e)
            try:
                s = state()
            except Exception as e:
                log("  读状态失败: %s" % e)
                continue
            if str(s.get("location")) != "Beach":
                log("  不在海滩了（%s），重新过去" % s.get("location"))
                if not go_to_beach():
                    break
                A.send_order({"actionType": "go_fish"})
                time.sleep(10)
                A.protect_scythe_from_fish(log_fn=log)
                A.send_order({"actionType": "fish"})
                continue
            inv = s.get("inventory") or []
            cnt = inv_total(inv)
            if base is None:
                base = cnt
            if cnt != last_count:
                gained = cnt - (base or 0)
                log("  物品变化 +%d（共 %d）：%s" % (gained, cnt, inv[-3:]))
                last_count = cnt
                last_change = time.time()
            elif time.time() - last_change > 600:
                # 2026-11-23：长时间没上鱼（泵被掐 / 竿子卡住 / 镜头切走）→ 自愈一次
                log("  [自愈] 10 分钟没上鱼，重发 go_fish + fish")
                try:
                    A.send_order({"actionType": "go_fish"}); time.sleep(3)
                    A.send_order({"actionType": "fish"}); time.sleep(5)
                except Exception as e:
                    log("  [自愈] 失败: %s" % e)
                last_change = time.time()
            # 卡住自愈：位置和物品都没变化太久 → 重发 fish
    finally:
        # 2026-11-22：不管怎么退出（正常收工 / 异常 / 被父进程 terminate）都要先收竿，
        # 否则 mod 的钓鱼泵会自己抛竿到第二天，抛竿时 CurrentTool=rod 会覆盖选中格（丢镐子/镰刀）。
        try:
            A.send_order({"actionType": "stop_fish"})
            time.sleep(1.5)
        except Exception:
            pass
    s = state()
    log("收工: 钱%s 时间%s" % (s.get("money"), s.get("time")))
    log("背包: %s" % s.get("inventory"))
    A.send_order({"actionType": "stop_fish"})
    time.sleep(1)
    A.send_order({"actionType": "save"})
    time.sleep(4)
    log("===== 钓鱼结束 =====")


if __name__ == "__main__":
    main()
