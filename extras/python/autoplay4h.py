"""4 小时自动游玩：挖矿攒铜/石头 → 做熔炉；晚上睡觉；全程兜底。

用法：
    python autoplay4h.py            # 默认 14400 秒（4 小时）
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import boxes
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
    """卡住判定 v2：位置/游戏时间长时间不变才判定卡死（避免误杀长任务）。"""
    last = None
    still = 0
    while True:
        time.sleep(45)
        try:
            b = nav2.rd() or {}
            pos = (b.get("location"), (b.get("player") or {}).get("tile", {}).get("x"),
                   (b.get("player") or {}).get("tile", {}).get("y"),
                   b.get("day"), b.get("time"))
        except Exception:
            continue
        if pos == last:
            still += 1
        else:
            still = 0
            last = pos
        # 连续 7 次（约 5 分钟）位置和游戏时间都没变 → 判定卡死
        if still >= 7:
            _dump_stuck(f"frozen {still*45}s at {pos}")
            log(f"⚠ 检测到卡住（位置/时间 {still*45} 秒没变），记录并重启")
            try:
                nav2.send({"actionType": "cancel_dialogue"}, 0.3)
                if "Mine" in str((nav2.rd() or {}).get("location")):
                    nav2.send({"actionType": "leave_mine"}, 1.5)
                    time.sleep(1.5)
            except Exception:
                pass
            time.sleep(1)
            os._exit(9)


def rd():
    return nav2.rd() or {}


def cnt(part):
    return sum(int(i.get("stack", 1)) for i in (rd().get("player", {}).get("inventory") or []) if i and part in str(i.get("name")))


def _chest_count(part):
    b = nav2.rd() or {}
    return sum(int(e.get("stack", 0)) for e in b.get("chestTotals") or []
               if part in str(e.get("name") or ""))


def state():
    b = rd()
    return {
        "loc": b.get("location"), "day": b.get("day"), "time": b.get("time"),
        "money": (b.get("player") or {}).get("money"),
        "stone": cnt("石头") + _chest_count("石头"),
        "copper": cnt("铜矿石") + _chest_count("铜矿石"),
        "wood": cnt("木材") + _chest_count("木材"),
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


def backup_save():
    """启动前把存档备份一份，避免再出现存档回滚。"""
    import shutil
    saves = os.path.join(os.environ.get("APPDATA", ""), "StardewValley", "Saves")
    dst = os.path.join(os.path.dirname(LOG), "save_backup_" + time.strftime("%Y%m%d_%H%M%S"))
    try:
        os.makedirs(dst, exist_ok=True)
        for d in os.listdir(saves):
            src = os.path.join(saves, d)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(dst, d), dirs_exist_ok=True)
        log(f"  已备份存档 -> {dst}")
        return dst
    except Exception as ex:
        log(f"  备份存档失败: {ex}")
        return None


def main():
    log(f"=== 4 小时自动开始（计划 {DURATION//3600} 小时）===")
    backup_save()
    _start_state = None
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
    same_loc_count = 0
    last_loc = None
    while time.time() - START < DURATION:
        cycle += 1
        LAST_CYCLE[0] = time.time()
        try:
            fallback.keep_awake()
            if not fallback.ensure_ready(tries=5):
                log("  游戏没就绪，等 30 秒")
                time.sleep(30)
                continue
            # 存档防回滚：检测到回滚就恢复备份并重进
            try:
                if fallback.check_rollback():
                    time.sleep(5)
                    continue
                fallback.write_marker()
            except Exception as ex:
                log(f"  存档检查失败: {ex}")
            nav2.send({"actionType": "cancel_dialogue"}, 0.3)
            # 背包快满 → 先存箱子
            try:
                if boxes.slots_used() >= 11:
                    boxes.deposit_junk()
            except Exception as ex:
                log(f"  存箱子失败: {ex}")
            st = state()
            if _start_state is None:
                _start_state = st
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

            # 走路卡死检测：连续 3 轮都在同一张图 → 应急传送到安全点
            if last_loc == st["loc"]:
                same_loc_count += 1
            else:
                same_loc_count = 0
                last_loc = st["loc"]
            if same_loc_count >= 3:
                safe = (nav2.SAFE or {}).get(st["loc"])
                if safe:
                    log(f"  连续 {same_loc_count} 轮卡在 {st['loc']}，应急传送到 {safe}")
                    nav2.send({"actionType": "player_warp", "location": st["loc"], "x": safe[0], "y": safe[1]}, 1.5)
                same_loc_count = 0

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
                # 今天矿洞起步层不可用 → 别反复进矿，改干农场活
                try:
                    if mine_real._is_no_ladder(1):
                        log("  今天第1层不可用，改清农场")
                        fallback.goto_or_warp("Farm")
                        nav2.set_timeout(240)
                        try:
                            nav2.break_obstacles(64, 15, 10)
                            nav2.cut_grass(3)
                        except TimeoutError:
                            pass
                        finally:
                            nav2.clear_timeout()
                        continue
                except Exception:
                    pass
                nav2.set_timeout(600)
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
                nav2.clear_timeout()
        except TimeoutError:
            log("  本轮超时")
            try:
                nav2.clear_timeout()
            except Exception:
                pass
        except Exception as ex:
            log(f"  异常: {ex}")
            log(traceback.format_exc()[-500:])
            try:
                fallback.panic_recover()
            except Exception:
                pass
        time.sleep(4)
    end = state()
    log(f"=== 结束：{end} ===")
    try:
        seasons = ["spring", "summer", "fall", "winter"]
        if _start_state and end.get("day") is not None:
            si, ei = seasons.index(_start_state.get("season", "spring")), seasons.index(end.get("season", "spring"))
            if (ei, end.get("day", 0)) < (si, _start_state.get("day", 0)):
                log(f"  ⚠ 警告：天数倒退了（{_start_state.get('season')} {_start_state.get('day')} -> {end.get('season')} {end.get('day')}），可能有存档回滚")
    except Exception:
        pass


if __name__ == "__main__":
    main()
