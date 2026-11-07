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


def go_to_bed():
    """在农舍里：走到床边再交互上床。"""
    b = rd()
    if b.get("location") != "FarmHouse":
        return False
    tiles = (b.get("surroundings") or {}).get("Tiles") or []
    bed = next((t for t in tiles if "床" in str(t.get("ObjectName")) or "Bed" in str(t.get("ObjectName"))), None)
    if not bed:
        return False
    me = nav2.tile(rd())
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        st = next((t for t in tiles if t["X"] == bed["X"] + dx and t["Y"] == bed["Y"] + dy and t.get("Passable")), None)
        if st and nav2.walk_until(st["X"], st["Y"], 12):
            nav2.send({"actionType": "player_interact_at", "x": bed["X"], "y": bed["Y"]}, 2.0)
            time.sleep(3)
            return True
    return False


def go_sleep():
    log("  晚上了，回家睡觉")
    day0 = rd().get("day")
    b = rd()
    if b.get("location") != "FarmHouse":
        if not fallback.goto_or_warp("FarmHouse"):
            log("  走不回去，直接强制跨天（避免晕倒扣钱）")
            nav2.send({"actionType": "player_force_new_day"}, 2.0)
            time.sleep(8)
            nav2.send({"actionType": "force_unpause"}, 0.5)
            log(f"  跨天后：day={rd().get('day')} time={rd().get('time')} money={money()}")
            return
    # 睡觉：先正常上床，没跨天再换动作
    for attempt in range(3):
        go_to_bed()
        nav2.send({"actionType": "player_sleep_bed"}, 2.0)
        for _ in range(20):
            time.sleep(2)
            b = rd()
            if b.get("day") != day0:
                log(f"  醒来：day={b.get('day')} time={b.get('time')} money={money()}")
                return
        log(f"  第 {attempt+1} 次睡觉没跨天，换方式")
        nav2.send({"actionType": "player_sleep"}, 2.0)
        time.sleep(8)
        if rd().get("day") != day0:
            log(f"  醒来(备用)：day={rd().get('day')} time={rd().get('time')}")
            return
    # 兜底：强制跨天（等价于睡觉，避免 2 点晕倒扣钱）
    log("  床睡不了，用强制跨天兜底")
    nav2.send({"actionType": "player_force_new_day"}, 2.0)
    time.sleep(8)
    b = rd()
    log(f"  兜底后：day={b.get('day')} time={b.get('time')} money={money()}")
    nav2.send({"actionType": "force_unpause"}, 0.5)


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
            if not fallback.ensure_ready(tries=5):
                log("  游戏没就绪，等 30 秒再试")
                time.sleep(30)
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
            if t >= 2130:
                go_sleep()
                continue

            # 白天先试矿洞，挖不到再清农场
            tnow = rd().get("time", 0)
            did_work = False
            if 600 <= tnow <= 2000:
                log("  计划：白天先去矿洞")
                nav2.set_timeout(420)
                try:
                    if "Mine" in str(rd().get("location")):
                        nav2.send({"actionType": "leave_mine"}, 2.0)
                        time.sleep(2)
                    ok = nav2.goto_via(["BusStop", "Town", "Mountain"])
                    if ok and mine_real.enter_mine_walk():
                        n = mine_real.mine_rocks(6)
                        log(f"  挖到 {n} 块")
                        did_work = n > 0
                        nav2.send({"actionType": "leave_mine"}, 2.0)
                        time.sleep(2)
                except TimeoutError:
                    log("  去矿洞超时")
                finally:
                    nav2.clear_timeout()
            if not did_work:
                log("  农场清障")
                nav2.set_timeout(300)
                try:
                    if rd().get("location") != "Farm":
                        fallback.goto_or_warp("Farm")
                    anchors = [(64, 15), (40, 10), (75, 20), (55, 40), (70, 45), (30, 30), (20, 60), (60, 60)]
                    for ax, ay in anchors:
                        if rd().get("location") != "Farm":
                            fallback.goto_or_warp("Farm")
                        nav2.break_obstacles(ax, ay, 6)
                        nav2.cut_grass(2)
                        if rd().get("time", 0) >= 2130:
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
        time.sleep(5)
    log("=== 挂机结束 ===")


if __name__ == "__main__":
    main()
