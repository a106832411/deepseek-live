"""4 小时自动游玩：挖矿攒铜/石头 → 做熔炉；晚上睡觉；全程兜底。

用法：
    python autoplay4h.py            # 默认 14400 秒（4 小时）
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fallback
import furnace_progress as fp
import guard
import mine_real
import nav2

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "autoplay4h.log")
STUCK_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "stuck_report.jsonl")
LAST_CYCLE = [time.time()]
STUCK_WATCH = True
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 14400
START = time.time()


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')} +{int(time.time()-START)//60}m] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _dump_stuck(reason):
    import json as _json
    try:
        b = nav2.rd() or {}
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        lads = [t for t in tiles if "梯" in str(t.get("ObjectName") or "") or t.get("ObjectType") == "ladder"]
        rec = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason,
            "loc": b.get("location"),
            "tile": nav2.tile(b) if b else None,
            "day": b.get("day"), "clock": b.get("time"),
            "money": (b.get("player") or {}).get("money"),
            "ladderSpawned": b.get("ladderSpawned"),
            "ladderPosText": b.get("ladderPosText"),
            "stairs": b.get("stairs"),
            "dialogueUp": b.get("dialogueUp"),
            "nearLadders": [(t.get("X"), t.get("Y")) for t in lads][:5],
            "inv": [i["name"] for i in (b.get("player") or {}).get("inventory") or [] if i],
        }
        with open(STUCK_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _watchdog():
    while True:
        time.sleep(60)
        gap = time.time() - LAST_CYCLE[0]
        # 两个日志任一在更新都算活着（详情日志也会写动作）
        try:
            for lp in (LOG, os.path.join(os.path.dirname(LOG), "autoplay_stdout.log")):
                if os.path.exists(lp):
                    gap = min(gap, time.time() - os.path.getmtime(lp))
        except Exception:
            pass
        if gap > 900:
            _dump_stuck(f"no cycle for {int(gap)}s")
            log(f"⚠ 检测到卡住 {int(gap)} 秒，记录状态并重启自己")
            try:
                b = nav2.rd() or {}
                nav2.send({"actionType": "cancel_dialogue"}, 0.3)
                if "Mine" in str(b.get("location")):
                    # 卡在矿洞：先离开矿洞，别原地重启又进同一个坑
                    nav2.send({"actionType": "leave_mine"}, 1.5)
                    time.sleep(2)
            except Exception:
                pass
            time.sleep(2)
            os._exit(9)


def rd():
    return nav2.rd() or {}


def cnt(part):
    return sum(int(i.get("stack", 1)) for i in (rd().get("player", {}).get("inventory") or []) if i and part in str(i.get("name")))


def state():
    b = rd()
    return {
        "loc": b.get("location"), "day": b.get("day"), "time": b.get("time"),
        "money": (b.get("player") or {}).get("money"),
        "stone": cnt("石头"), "copper": cnt("铜矿石"), "wood": cnt("木材"),
        "bars": cnt("铜锭"), "furnace": cnt("熔炉"),
    }


def go_home_sleep():
    day0 = rd().get("day")
    log("  晚上回家睡觉")
    fallback.goto_or_warp("FarmHouse")
    nav2.send({"actionType": "player_sleep_bed"}, 2.0)
    for _ in range(12):
        time.sleep(2)
        if rd().get("day") != day0:
            log("  睡觉成功")
            return True
    nav2.send({"actionType": "player_force_new_day"}, 2.0)
    time.sleep(8)
    nav2.send({"actionType": "force_unpause"}, 0.5)
    log(f"  强制跨天 day={rd().get('day')}")
    return rd().get("day") != day0


def main():
    log(f"=== 4 小时自动开始（计划 {DURATION//3600} 小时）===")
    try:
        import threading
        threading.Thread(target=_watchdog, daemon=True).start()
    except Exception:
        pass
    last = None
    stuck = 0
    cycle = 0
    mine_fail = 0
    last_gain = (0, 0)
    while time.time() - START < DURATION:
        cycle += 1
        LAST_CYCLE[0] = time.time()
        try:
            fallback.keep_awake()
            if not fallback.ensure_ready(tries=5):
                log("  游戏没就绪，等 30 秒")
                time.sleep(30)
                continue
            nav2.send({"actionType": "cancel_dialogue"}, 0.3)
            st = state()
            log(f"[{cycle}] {st['loc']} day={st['day']} {st['time']} 金={st['money']} 石={st['stone']} 铜={st['copper']} 木={st['wood']} 铜锭={st['bars']} 熔炉={st['furnace']}")

            cur = (st["loc"], st["time"], st["day"])
            if cur == last:
                stuck += 1
                log(f"  疑似卡住 ({stuck}/3)")
                if stuck >= 3:
                    log("  连续卡住 → 脱困")
                    fallback.panic_recover()
                    fallback.unfreeze()
                    stuck = 0
            else:
                stuck = 0
            last = cur

            # 矿洞卡住检测：连续 3 轮没收获就离开矿洞去干农场活
            gain = (st["stone"], st["copper"])
            if "Mine" in str(st["loc"]) and gain == last_gain:
                mine_fail += 1
            else:
                mine_fail = 0
            if gain != last_gain:
                last_gain = gain
            if mine_fail >= 3:
                log("  矿洞连续没收获，先离开干农场活")
                nav2.send({"actionType": "leave_mine"}, 2.0)
                time.sleep(3)
                fallback.goto_or_warp("Farm")
                nav2.set_timeout(300)
                try:
                    nav2.break_obstacles(64, 15, 8)
                    nav2.cut_grass(2)
                except TimeoutError:
                    pass
                finally:
                    nav2.clear_timeout()
                mine_fail = 0
                continue

            t = st["time"] or 0
            if t >= 2100:
                go_home_sleep()
                continue

            # 目标：铜矿优先（够 20 做熔炉），否则攒石头做楼梯
            if st["copper"] >= 20 and st["stone"] >= 25:
                log("  材料够了，去做熔炉")
                fp.run_once()   # 内含做熔炉/烧铜锭
                continue
            if st["stone"] >= 99:
                log("  下矿找天然楼梯，挖铜")
                fp.run_once()
            else:
                log("  石头不够，先挖矿/清农场")
                if not mine_real.enter_mine_walk():
                    fallback.goto_or_warp("Farm")
                    nav2.set_timeout(300)
                    try:
                        nav2.break_obstacles(64, 15, 8)
                        nav2.cut_grass(2)
                    except TimeoutError:
                        pass
                    finally:
                        nav2.clear_timeout()
                else:
                    mine_real.mine_rocks(12)
        except TimeoutError:
            log("  本轮超时")
        except Exception as ex:
            log(f"  异常: {ex}")
            log(traceback.format_exc()[-500:])
            try:
                fallback.panic_recover()
            except Exception:
                pass
        time.sleep(4)
    log(f"=== 结束：{state()} ===")


if __name__ == "__main__":
    main()
