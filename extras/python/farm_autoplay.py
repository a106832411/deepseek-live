"""种地专用自动循环：锄地→播种→浇水→收获→卖作物，晚上睡觉。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import boxes
import fallback
import farm
import nav2
import sell

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "farm_autoplay.log")
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 14400
START = time.time()
LAST = [time.time()]


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')} +{int((time.time()-START)//60)}m] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    LAST[0] = time.time()


def rd():
    return nav2.rd() or {}


def state():
    b = rd()
    p = b.get("player") or {}
    inv = p.get("inventory") or []
    return {
        "loc": b.get("location"), "season": b.get("season"), "day": b.get("day"), "time": b.get("time"),
        "money": p.get("money"), "stamina": p.get("stamina"), "hp": p.get("health"),
        "inv": [i["name"] for i in inv if i],
    }


def go_sleep():
    day0 = rd().get("day")
    log("  回家睡觉")
    fallback.goto_or_warp("FarmHouse")
    nav2.send({"actionType": "player_sleep_bed"}, 2.0)
    for _ in range(12):
        time.sleep(2)
        if rd().get("day") != day0:
            log("  睡觉成功，跨天")
            return True
    nav2.send({"actionType": "player_force_new_day"}, 2.0)
    time.sleep(6)
    nav2.send({"actionType": "force_unpause"}, 0.5)
    log(f"  强制跨天 -> day={rd().get('day')}")
    return rd().get("day") != day0


def main():
    log(f"=== 种地循环开始（计划 {DURATION//3600} 小时）===")
    try:
        import shutil
        saves = os.path.join(os.environ.get("APPDATA", ""), "StardewValley", "Saves")
        dst = os.path.join(os.path.dirname(LOG), "save_backup_" + time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(dst, exist_ok=True)
        for d in os.listdir(saves):
            src = os.path.join(saves, d)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(dst, d), dirs_exist_ok=True)
        log(f"  已备份存档 -> {dst}")
    except Exception as ex:
        log(f"  备份失败: {ex}")

    while time.time() - START < DURATION:
        try:
            if not fallback.ensure_ready(tries=5):
                log("  游戏没就绪，等 30 秒")
                time.sleep(30)
                continue
            fallback.check_rollback()
            fallback.write_marker()
            st = state()
            log(f"{st['loc']} {st['season']} day={st['day']} {st['time']} 金={st['money']} 体力={st['stamina']} 血={st['hp']}")
            # 背包整理
            try:
                if boxes.slots_used() >= 11:
                    boxes.deposit_junk()
            except Exception as ex:
                log(f"  存箱子失败: {ex}")
            # 卖石头/木材（各留约999）
            try:
                if _total("石头") > 1400 or _total("木材") > 1400:
                    sell.sell_surplus()
            except Exception as ex:
                log(f"  卖资源失败: {ex}")
            t = st["time"] or 0
            stamina = st["stamina"] if isinstance(st["stamina"], (int, float)) else 270
            if t >= 2100 or stamina < 25:
                go_sleep()
                continue
            # 白天：种地
            log("  去农场干活")
            fallback.goto_or_warp("Farm")
            nav2.set_timeout(600)
            try:
                farm.do_farm(10)
            except TimeoutError:
                log("  种地本轮超时")
            finally:
                nav2.clear_timeout()
            time.sleep(15)
        except TimeoutError:
            log("  本轮超时")
            try:
                nav2.clear_timeout()
            except Exception:
                pass
        except Exception as ex:
            log(f"  异常: {ex}")
            try:
                fallback.panic_recover()
            except Exception:
                pass
        time.sleep(3)
    log(f"=== 结束: {state()} ===")


def _total(name):
    b = rd()
    p = b.get("player") or {}
    n = sum(int(i.get("stack", 1)) for i in p.get("inventory") or [] if i and name in str(i.get("name")))
    c = sum(int(e.get("stack", 1)) for e in b.get("chestTotals") or [] if name in str(e.get("name")))
    return n + c


if __name__ == "__main__":
    main()
