#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""农场循环：买种子 → 回农场 → 取种子 → 种植 → 收割 → 出货 → 存档。

用法：python stardew_farmcycle.py [--buy 防风草:10] [--plant 8] [--harvest]
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stardew_agent as A  # noqa: E402

LOG = r"D:\live\logs\farmcycle.log"
_fh = None
SEED_CHEST = (58, 17)
# 种子名称 → 物品 id（商店按 id 匹配最稳）
SEED_IDS = {
    "防风草": "472", "parsnip": "472",
    "花椰菜": "474", "cauliflower": "474",
    "土豆": "475", "potato": "475",
    "青豆": "473", "bean": "473",
    "草莓": "745", "strawberry": "745",
    "蓝莓": "481", "blueberry": "481",
    "玉米": "487", "corn": "487",
    "小麦": "483", "wheat": "483",
    "郁金香": "427", "tulip": "427",
    "甘蓝": "477", "kale": "477",
}


def seed_id(name):
    key = str(name or "").strip()
    return SEED_IDS.get(key, SEED_IDS.get(key.lower(), key))
# 固定农田：小屋门口正下方一排
PLOT = (65, 18, 74, 23)   # 门口下方第3行起 10列×6行


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


def go(x, y, timeout=25):
    s = state()
    px, py = pos(s)
    if abs(px - x) + abs(py - y) <= 1:
        return True
    A.send_order({"actionType": "walk_to", "x": x, "y": y})
    return A.wait_pos((x, y), timeout=min(timeout, 6 + (abs(px - x) + abs(py - y)) * 0.6))


def goto(loc, timeout=300):
    if str(state().get("location")) == loc:
        return True
    log("  → 前往 %s" % loc)
    A.send_order({"actionType": "goto", "location": loc})
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(3)
        if str(state().get("location")) == loc:
            log("  到达 %s（%ds）" % (loc, int(time.time() - t0)))
            return True
    log("  没到 %s（现在 %s）" % (loc, state().get("location")))
    return False


def _mins(t):
    try:
        h, m = str(t).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def goto_shop(timeout=300):
    """走到皮埃尔商店（9:00-17:00 营业，周三/节日关门）。"""
    if not A.shop_state()[0]:
        log("  [商店] %s" % "、".join(A.shop_state()[1]))
        if not A.wait_for_shop(log_fn=log):
            log("  [商店] 现在买不了，跳过购买")
            return False
    if str(state().get("location")) == "SeedShop":
        return True
    if not goto("Town", timeout=timeout):
        return False
    for attempt in range(6):
        A.send_order({"actionType": "enter_shop"})
        time.sleep(2)
        A.send_order({"actionType": "close_menu"})   # 商店没开门会弹提示，关掉
        time.sleep(0.5)
        s = state()
        if str(s.get("location")) == "SeedShop":
            log("  进店成功（%s）" % s.get("time"))
            return True
        d = s.get("door") or {}
        dx, dy = int(d.get("x", -1)), int(d.get("y", -1))
        if dx > 0:
            log("  店门 (%d,%d)，走过去" % (dx, dy))
            go(dx, dy + 1, 20)
            A.send_order({"actionType": "enter_shop", "x": dx, "y": dy})
            time.sleep(3)
            if str(state().get("location")) == "SeedShop":
                log("  进店成功")
                return True
        # 可能没开门 → 等到 9 点
        now = state()
        if _mins(now.get("time")) < 540:
            log("  商店还没开门（%s），等到 9:00" % now.get("time"))
            t0 = time.time()
            while time.time() - t0 < 180:
                time.sleep(4)
                if _mins(state().get("time")) >= 540:
                    break
            go(dx, dy + 1, 20) if dx > 0 else None
            time.sleep(1)
    log("  进不去商店")
    return False


def buy(item, count):
    sid = seed_id(item)
    log("  → 买 %s（id=%s）×%d" % (item, sid, count))
    A.send_order({"actionType": "shop_buy", "item": sid, "count": count})
    time.sleep(2)
    A.send_order({"actionType": "close_menu"})
    time.sleep(1)
    s = state()
    log("  钱 %s，背包: %s" % (s.get("money"), s.get("inventory")))


def withdraw_seeds(count=99):
    log("  → 取种子")
    go(SEED_CHEST[0], SEED_CHEST[1] + 1, 30)
    A.send_order({"actionType": "withdraw", "x": SEED_CHEST[0], "y": SEED_CHEST[1],
                  "kind": "seeds", "count": count})
    time.sleep(1.5)
    s = state()
    seeds = [i for i in s["inventory"] if "种子" in i or "Seed" in i]
    log("  种子: %s" % (seeds or "无"))
    return seeds


def tile_at(x, y):
    s = state()
    return next((z for z in (s.get("tiles") or []) if z["x"] == x and z["y"] == y), None)


def plant_loop(max_n=60):
    """固定农田 10×6：割草 → 锄地 → 播种（蛇形顺序）。"""
    x1, y1, x2, y2 = PLOT
    log("  固定农田 (%d,%d)-(%d,%d) = %d 列 × %d 行" % (x1, y1, x2, y2, x2 - x1 + 1, y2 - y1 + 1))
    planted = 0
    tried = 0
    for row, y in enumerate(range(y1, y2 + 1)):
        xs = list(range(x1, x2 + 1))
        if row % 2 == 1:
            xs.reverse()          # 蛇形
        for x in xs:
            if planted >= max_n:
                break
            if not go(x, y, 10):
                continue
            t = tile_at(x, y)
            tried += 1
            if t and t.get("crop"):
                continue          # 已有作物
            if t and t.get("obj") and not t.get("breakable"):
                log("    (%d,%d) 有障碍 %s，跳过" % (x, y, t.get("obj")))
                continue
            # 有杂草/树枝 → 先割
            if t and t.get("obj"):
                A.send_order({"actionType": "use_tool", "tool": "scythe", "x": x, "y": y})
                time.sleep(1.0)
            A.send_order({"actionType": "use_tool", "tool": "hoe", "x": x, "y": y})
            time.sleep(1.1)
            A.send_order({"actionType": "plant", "x": x, "y": y, "seed": ""})
            time.sleep(0.9)
            t2 = tile_at(x, y)
            if t2 and t2.get("crop"):
                planted += 1
                log("    种下 (%d,%d) %s" % (x, y, t2.get("crop")))
            else:
                log("    (%d,%d) 没种上（%s）" % (x, y, (t2 or {}).get("obj")))
    log("  本次种下 %d 株（处理 %d 格）" % (planted, tried))
    return planted


def harvest():
    """镰刀扫荡收割。"""
    try:
        return A.harvest_with_scythe(log_fn=log)
    except Exception as e:
        log("  收割异常: %s" % e)
        return 0


def ship():
    log("  → 出货")
    A.send_order({"actionType": "ship"})
    time.sleep(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--buy", type=str, default="", help="种子名:数量")
    ap.add_argument("--plant", type=int, default=0, help="最多种几株")
    ap.add_argument("--harvest", action="store_true")
    args = ap.parse_args()
    log("===== 农场循环开始 =====")
    s = state()
    log("起点: %s %s 钱%s 时间%s" % (s.get("location"), pos(s), s.get("money"), s.get("time")))

    if args.buy:
        name, _, cnt = args.buy.partition(":")
        cnt = int(cnt or 10)
        if goto_shop():
            buy(name, cnt)
        goto("Farm", 320)

    if str(state().get("location")) == "FarmHouse":
        goto("Farm", 60)

    if args.harvest:
        harvest()

    if args.plant:
        A.plant_best_crops(args.plant, log_fn=log)

    if args.harvest:
        ship()

    A.send_order({"actionType": "ensure_tools"})
    time.sleep(1)
    A.send_order({"actionType": "save"})
    time.sleep(6)
    s = state()
    log("结束: 位置%s 钱%s 时间%s" % (s.get("location"), s.get("money"), s.get("time")))
    log("背包: %s" % s.get("inventory"))
    log("===== 农场循环结束 =====")


if __name__ == "__main__":
    main()
