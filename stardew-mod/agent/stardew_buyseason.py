#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""换季买种：睡觉到新季第1天 → 等到9点 → 去商店买当季种子 → 回农场种植。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stardew_agent as A  # noqa: E402

LOG = r"D:\live\logs\buyseeds.log"
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


def mins(t):
    try:
        h, m = str(t).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def go_home_and_sleep():
    log("  → 回家睡觉换季")
    s = state()
    if str(s.get("location")) != "Farm":
        A.travel_to("Farm", hop_timeout=200, log_fn=log)
    # 门口进屋
    for _ in range(4):
        A.send_order({"actionType": "walk_to", "x": 64, "y": 16})
        time.sleep(3)
        A.send_order({"actionType": "enter_home"})
        time.sleep(3)
        if str(state().get("location")) == "FarmHouse":
            break
    log("  睡觉…")
    A.send_order({"actionType": "sleep"})
    time.sleep(12)
    for _ in range(5):
        A.send_order({"actionType": "close_menu"})
        time.sleep(1.2)
    s = state()
    log("  新一天: 天%s %s季 %s | 钱 %s" % (s.get("day"), s.get("season"), s.get("time"), s.get("money")))
    return s


def wait_until_shop_open():
    log("  → 等商店开门（9:00）")
    t0 = time.time()
    while time.time() - t0 < 600:
        s = state()
        if mins(s.get("time")) >= 540:
            log("  现在 %s，可以去了" % s.get("time"))
            return True
        time.sleep(5)
    log("  等太久了，直接去")
    return True


def goto_shop():
    log("  → 用安全路线去镇上商店")
    if not A.travel_to("Town", hop_timeout=200, log_fn=log):
        log("  到不了镇上")
        return False
    for attempt in range(6):
        A.send_order({"actionType": "enter_shop"})
        time.sleep(2)
        A.send_order({"actionType": "close_menu"})
        time.sleep(0.5)
        s = state()
        if str(s.get("location")) == "SeedShop":
            log("  ✅ 进店成功（%s）" % s.get("time"))
            return True
        d = s.get("door") or {}
        dx, dy = int(d.get("x", -1)), int(d.get("y", -1))
        if dx > 0:
            A.send_order({"actionType": "walk_to", "x": dx, "y": dy + 1})
            time.sleep(6)
            A.send_order({"actionType": "enter_shop", "x": dx, "y": dy})
            time.sleep(3)
            if str(state().get("location")) == "SeedShop":
                log("  ✅ 进店成功")
                return True
    return False


def buy_seeds():
    season, day = A.current_season_day()
    picks = A.pick_crops(season, day, top=5)
    log("  %s季第%d天候选: %s" % (A.SEASON_CN.get(season, season), day,
                                  [(n, en) for (n, en, d, r, g) in picks]))
    bought = []
    for (name, en, days, regrow, profit) in picks:
        log("  → 买 %s（%s）" % (name, en))
        A.send_order({"actionType": "shop_buy", "item": en, "count": 200})
        time.sleep(3)
        A.send_order({"actionType": "close_menu"})
        time.sleep(1.2)
        n = A.count_seeds(en)
        if n > 0:
            bought.append((name, en, n))
            log("    到手 %d 个 %s" % (n, en))
            if n >= 100:
                break           # 第一种够用就不折腾了
    log("  买到: %s" % bought)
    return bought


def main():
    log("===== 换季买种开始 =====")
    s = state()
    log("起点: %s 天%s %s季 %s 钱%s" % (s.get("location"), s.get("day"), s.get("season"), s.get("time"), s.get("money")))

    # 如果还在旧季节末尾，先睡到新季节
    if int(s.get("day") or 1) >= 28:
        go_home_and_sleep()
    # 按真实营业规则等待（9-17点、周三/节日不开门）
    if not A.wait_for_shop(log_fn=log):
        log("  商店现在买不了（周三/节日/时间不对）")
    else:
        wait_until_shop_open()

    if goto_shop():
        buy_seeds()
    else:
        log("  进不去商店")

    log("  → 回农场种植")
    A.send_order({"actionType": "close_menu"})
    time.sleep(1)
    A.travel_to("Farm", hop_timeout=220, log_fn=log)
    A.tidy_chests(log_fn=log)
    planted = A.plant_best_crops(200, log_fn=log)
    log("  种下 %d 株" % planted)
    A.send_order({"actionType": "save"})
    time.sleep(8)
    s = state()
    log("结束: 天%s %s季 %s 钱%s" % (s.get("day"), s.get("season"), s.get("time"), s.get("money")))
    log("===== 换季买种结束 =====")


if __name__ == "__main__":
    main()
