"""每日综合循环：种菜收菜 + 下矿 + 钓鱼 + 卖货 + 睡觉。

时段（游戏时间）：
  06:00-09:00 农场（收货/播种/卖作物）
  09:00-15:00 矿洞（下 1-2 层、挖矿）
  15:00-19:00 钓鱼（海滩码头）
  19:00-21:00 农场收尾 + 卖资源
  21:00+     回家睡觉
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import boxes
import fallback
import farm
import gameio
import mine_real
import nav2
import sell
import walk_fish

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "mixed_autoplay.log")
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 14400
START = time.time()
FIXED = {"n": 0}


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')} +{int((time.time()-START)//60)}m] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def rd():
    return nav2.rd() or {}


def st():
    b = rd()
    p = b.get("player") or {}
    return {"loc": b.get("location"), "season": b.get("season"), "day": b.get("day"), "time": b.get("time"),
            "money": p.get("money"), "stamina": p.get("stamina"), "hp": p.get("health")}


def fix_sprite():
    """恢复玩家姿态：先走游戏官方的 reset_rod（停动画/解除施法姿势），再校正帧。"""
    try:
        nav2.send({"actionType": "reset_rod"}, 0.5)
        nav2.send({"actionType": "fix_sprite"}, 0.4)
    except Exception:
        pass


def go_sleep():
    d0 = rd().get("day")
    fix_sprite()
    log("  回家睡觉")
    fallback.goto_or_warp("FarmHouse")
    nav2.send({"actionType": "player_sleep_bed"}, 2.0)
    for _ in range(15):
        time.sleep(2)
        if rd().get("day") != d0:
            log("  睡觉成功，跨天")
            return True
    nav2.send({"actionType": "player_force_new_day"}, 2.0)
    time.sleep(6)
    nav2.send({"actionType": "force_unpause"}, 0.5)
    log(f"  强制跨天 -> day={rd().get('day')}")
    return True


def do_farm_phase(max_plant=12):
    log("  农场：收菜/播种/卖作物")
    fallback.goto_or_warp("Farm")
    nav2.set_timeout(600)
    try:
        farm.do_farm(max_plant)
    except TimeoutError:
        log("  农场超时")
    finally:
        nav2.clear_timeout()
    fix_sprite()


def do_mine_phase():
    log("  矿洞：下去挖矿")
    fallback.goto_or_warp("Mountain")
    nav2.set_timeout(900)
    try:
        mine_real.enter_mine_walk()
        f0 = mine_real.floor_num()
        mine_real.descend_via_ladder_v2()
        f1 = mine_real.floor_num()
        mine_real.mine_rocks(12)
        log(f"  矿洞 {f0} -> {f1} 层")
    except TimeoutError:
        log("  矿洞超时")
    except Exception as ex:
        log(f"  矿洞异常: {ex}")
    finally:
        nav2.clear_timeout()
    fix_sprite()


def do_fish_phase():
    log("  钓鱼：去海滩")
    nav2.set_timeout(600)
    try:
        h, r = gameio.game_window()
        if not h:
            log("  没有游戏窗口，跳过钓鱼")
            return
        fallback.goto_or_warp("Beach")
        got = walk_fish.fish_from_pier(h, 2)
        log(f"  钓鱼结束: {got}")
    except TimeoutError:
        log("  钓鱼超时")
    except Exception as ex:
        log(f"  钓鱼异常: {ex}")
    finally:
        nav2.clear_timeout()
    fix_sprite()


def main():
    log(f"=== 综合循环开始（计划 {DURATION//3600} 小时）===")
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
            s = st()
            log(f"{s['loc']} {s['season']} day={s['day']} {s['time']} 金={s['money']} 体力={s['stamina']} 血={s['hp']}")
            if FIXED["n"] % 3 == 0:
                fix_sprite()
            FIXED["n"] += 1
            try:
                if boxes.slots_used() >= 11:
                    boxes.deposit_junk()
            except Exception:
                pass
            try:
                if _total("石头") > 1400 or _total("木材") > 1400:
                    sell.sell_surplus()
            except Exception as ex:
                log(f"  卖资源失败: {ex}")
            t = s["time"] or 0
            stamina = s["stamina"] if isinstance(s["stamina"], (int, float)) else 270
            if t >= 2100 or stamina < 20:
                go_sleep()
                continue
            if t < 900:
                do_farm_phase()
            elif t < 1500:
                do_mine_phase()
            elif t < 1800:
                do_fish_phase()
            else:
                do_farm_phase()
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
        time.sleep(5)
    log(f"=== 结束: {st()} ===")


def _total(name):
    b = rd()
    p = b.get("player") or {}
    n = sum(int(i.get("stack", 1)) for i in p.get("inventory") or [] if i and name in str(i.get("name")))
    c = sum(int(e.get("stack", 1)) for e in b.get("chestTotals") or [] if name in str(e.get("name")))
    return n + c


if __name__ == "__main__":
    main()
