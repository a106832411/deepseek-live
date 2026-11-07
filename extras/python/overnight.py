"""挂机主循环：一直玩、卡住自动恢复；跑 2 小时后自动停。

用法：
    python overnight.py            # 默认跑 7200 秒
    python overnight.py 3600       # 自定义秒数
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fallback
import guard
import mine_real
import nav2

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "overnight.log")
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 7200


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def rd():
    return nav2.rd() or {}


def money():
    return (rd().get("player") or {}).get("money")


def inventory_count(part):
    return sum(int(i.get("stack", 1)) for i in (rd().get("player", {}).get("inventory") or []) if i and part in str(i.get("name")))


def go_sleep():
    log("  晚上了，回家睡觉")
    b = rd()
    if b.get("location") != "FarmHouse":
        if not fallback.goto_or_warp("FarmHouse"):
            log("  走不回去，兜底传送回家（仅夜间紧急）")
            nav2.send({"actionType": "player_warp", "location": "FarmHouse", "x": 9, "y": 9}, 2.0)
            time.sleep(2)
    nav2.send({"actionType": "player_sleep_bed"}, 2.0)
    for _ in range(30):
        time.sleep(2)
        b = rd()
        if b.get("time", 0) < 1200:
            break
    log(f"  醒来：day={rd().get('day')} time={rd().get('time')} money={money()}")


def main():
    log(f"=== 挂机开始，计划跑 {DURATION} 秒 ===")
    t0 = time.time()
    last = None
    stuck = 0
    cycles = 0
    while time.time() - t0 < DURATION:
        cycles += 1
        try:
            fallback.keep_awake()
            if not fallback.ensure_ready():
                log("  游戏没就绪，等 60 秒再试")
                time.sleep(60)
                continue
            b = rd()
            loc, t, day, m = b.get("location"), b.get("time", 0), b.get("day"), money()
            # 离开矿洞：矿里没出口时会卡死
            if "Mine" in str(loc) and (b.get("time", 0) >= 2000):
                log("  准备离开矿洞")
                nav2.send({"actionType": "leave_mine"}, 2.0)
                time.sleep(3)
            log(f"[第 {cycles} 轮] {loc} day={day} time={t} money={m} 木材={inventory_count('木材')} 石头={inventory_count('石头')} 铜={inventory_count('铜矿石')}")

            # 卡住检测：位置+时间都没变化
            cur = (loc, t, day)
            if cur == last:
                stuck += 1
                log(f"  疑似卡住（{stuck}/3）")
                if stuck >= 3:
                    log("  连续卡住 → reset + 修位置")
                    fallback.panic_recover()
                    if not fallback.ensure_ready():
                        fallback.relaunch_game()
                    stuck = 0
            else:
                stuck = 0
            last = cur

            # 晚上回家睡觉
            if t >= 2350:
                go_sleep()
                continue

            # 稳定模式：农场清障/砍树/敲石头（矿洞路线还不稳，先不跑）
            log("  计划：农场清障（砍树/敲石头/割草）")
            nav2.set_timeout(300)
            try:
                fallback.goto_or_warp("Farm")
                anchors = [(64, 15), (40, 10), (75, 20), (55, 40), (70, 45), (30, 30)]
                for ax, ay in anchors:
                    if rd().get("location") != "Farm":
                        fallback.goto_or_warp("Farm")
                    nav2.break_obstacles(ax, ay, 6)
                    nav2.cut_grass(3)
                    b2 = rd()
                    if b2.get("time", 0) >= 2350:
                        break
            except TimeoutError:
                log("  农场本轮超时")
            finally:
                nav2.clear_timeout()

            # 每轮结束清理状态，避免卡住
            nav2.send({"actionType": "force_unpause"}, 0.4)
            nav2.send({"actionType": "close_menu"}, 0.3)
            # 卖菜（矿石不卖）
            nav2.send({"actionType": "sell_crops"}, 0.8)
            bnow = rd()
            if cycles % 5 == 0 and bnow.get("time", 0) != (last or (None, None, None))[1]:
                nav2.send({"actionType": "player_save"}, 0.5)
        except Exception as ex:
            log(f"  异常：{ex}")
            try:
                fallback.panic_recover()
            except Exception:
                pass
            time.sleep(5)
        time.sleep(3)
    log("=== 挂机结束 ===")


if __name__ == "__main__":
    main()
