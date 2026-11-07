"""游戏守护进程：定期检查游戏是否还活着/是否卡住，必要时自动重启并加载存档。

用法：
    python guard.py            # 常驻，默认每 300 秒检查一次
    python guard.py --once     # 只检查一次（测试用）
"""
import os
import sys
import time

import fallback
import gameio
import nav2

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "guard.log")
LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "guard.pid")


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def single_instance():
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    try:
        if os.path.exists(LOCK_PATH):
            old = open(LOCK_PATH, encoding="utf-8").read().strip()
            if old and old.isdigit() and int(old) != os.getpid():
                # 检查旧进程是否还活着
                try:
                    os.kill(int(old), 0)
                    return False
                except OSError:
                    pass
        open(LOCK_PATH, "w", encoding="utf-8").write(str(os.getpid()))
    except Exception:
        pass
    return True


def check_once():
    """一次健康检查 + 自动修复。返回 True 表示当前游戏可用。"""
    h, _ = gameio.game_window()
    if not h:
        log("健康检查：没有游戏窗口 → 自动重启并加载")
        ok = fallback.relaunch_game()
        log(f"重启结果: {ok}")
        return ok

    age = fallback.bridge_age()
    if age > 60:
        log(f"健康检查：桥接数据 {age:.0f}s 没更新（可能卡在标题页）→ 尝试加载存档")
        ok = gameio.load_game()
        log(f"加载结果: {ok}")
        return ok

    b = nav2.rd()
    if not b:
        log("健康检查：读不到桥接数据，稍后重试")
        return False

    t = nav2.tile(b)
    if abs(t[0]) > 150 or abs(t[1]) > 150:
        sx, sy = nav2.SAFE.get(b.get("location"), (20, 20))
        log(f"健康检查：位置异常 {t} → 放回 {sx},{sy}")
        nav2.send({"actionType": "set_tile", "x": sx, "y": sy}, 0.8)
        return True

    # 时间是否在走
    t0 = b.get("time", 0)
    time.sleep(3)
    b2 = nav2.rd() or {}
    if b2.get("time", 0) == t0:
        log("健康检查：游戏时间不走（被暂停/卡菜单）→ reset_state + 关菜单")
        nav2.send({"actionType": "set_focus_pause", "disabled": True}, 0.3)
        nav2.send({"actionType": "close_menu"}, 0.3)
        nav2.send({"actionType": "reset_state"}, 0.3)
        return True

    return True


# ============================
# 自动干活（接在守护进程里）
# ============================

def _send(a, wait=0.4):
    nav2.send(a, wait)


def _money():
    return ((nav2.rd() or {}).get("player") or {}).get("money")


def _day():
    return (nav2.rd() or {}).get("day")


def mine_batch(max_mined=8):
    """去矿洞挖一批（先走路，失败再兜底传送/进矿）。"""
    try:
        b = nav2.rd() or {}
        if b.get("location") != "Mountain":
            fallback.goto_or_warp("Mountain")
        nav2.live()
        # 走到矿洞入口附近再进矿（不要在远处凭空进）
        b = nav2.rd() or {}
        if b.get("location") == "Mountain":
            t = nav2.tile(b)
            if abs(t[0] - 51) + abs(t[1] - 7) > 3:
                nav2.walk_until(51, 7, 25)
            _send({"actionType": "enter_mine", "level": 1}, 2.0)
            time.sleep(4)
        b = nav2.rd() or {}
        if "Mine" not in str(b.get("location")):
            log(f"  挖矿：进矿失败（loc={b.get('location')}）")
            return 0
        mined = 0
        for _ in range(max_mined * 3):
            if mined >= max_mined:
                break
            b = nav2.rd() or {}
            tiles = (b.get("surroundings") or {}).get("Tiles") or []
            cands = [t for t in tiles if t.get("Breakable")]
            if not cands:
                break
            s = cands[0]
            _send({"actionType": "player_mine_at", "x": s["X"], "y": s["Y"]}, 0.7)
            _send({"actionType": "collect_debris"}, 0.3)
            mined += 1
        log(f"  挖矿：挖了 {mined} 个，金币 {_money()}")
        return mined
    except Exception as ex:
        log(f"  挖矿异常: {ex}")
        return 0


def fish_batch(attempts=2):
    try:
        import walk_fish
        fallback.goto_or_warp("Beach")
        h, _ = gameio.game_window()
        if not h:
            return 0
        n = walk_fish.fish_here(h, attempts)
        log(f"  钓鱼：尝试 {n} 次，金币 {_money()}")
        return n
    except Exception as ex:
        log(f"  钓鱼异常: {ex}")
        return 0


def farm_batch():
    try:
        import mixed_loop
        fallback.goto_or_warp("Farm")
        mixed_loop.farm_phase(3)
        log(f"  农场：干完活，金币 {_money()}")
        return 1
    except Exception as ex:
        log(f"  农场异常: {ex}")
        return 0


def ship_and_sleep_if_late():
    """时间晚了就装船+睡觉跨天（快速跨天用 set_clock 兜底）。"""
    b = nav2.rd() or {}
    if b.get("time", 0) < 1800:
        return False
    try:
        fallback.goto_or_warp("Farm")
        _send({"actionType": "player_ship_all"}, 0.6)
        _send({"actionType": "player_sell_instant"}, 0.6)
        m0 = _money()
        fallback.goto_or_warp("FarmHouse")
        d0 = _day()
        _send({"actionType": "set_clock", "time": 2590}, 0.8)
        for _ in range(40):
            time.sleep(1.5)
            if _day() != d0:
                break
        time.sleep(2)
        _send({"actionType": "close_menu"}, 0.4)
        log(f"  跨天：day {d0} -> {_day()}，金币 {m0} -> {_money()}")
        return True
    except Exception as ex:
        log(f"  跨天异常: {ex}")
        return False


def work_step():
    """一次干活轮次：按天数轮换 挖矿/钓鱼/农场，晚了就装船睡觉。"""
    b = fallback.ensure_ready()
    b = nav2.rd() or {}
    day = b.get("day", 0)
    mode = day % 3
    log(f"  自动干活：day={day} time={b.get('time')} money={_money()} mode={'挖矿' if mode == 0 else ('钓鱼' if mode == 1 else '农场')}")
    if mode == 0:
        mine_batch(8)
    elif mode == 1:
        fish_batch(2)
    else:
        farm_batch()
    ship_and_sleep_if_late()


def main():
    once = "--once" in sys.argv
    demo = "--demo" in sys.argv
    if not once and not single_instance():
        log("已有守护进程在跑，退出")
        return
    log("守护进程启动（含自动干活）")
    if once:
        check_once()
        work_step()
        return
    if demo:
        for i in range(4):
            log(f"===== 演示轮次 {i+1}/4 =====")
            try:
                check_once()
                work_step()
            except Exception as ex:
                log(f"演示异常: {ex}")
            time.sleep(10)
        return
    while True:
        try:
            ok = check_once()
            log(f"健康检查: {'正常' if ok else '异常（已尝试修复）'} | " +
                f"loc={(nav2.rd() or {}).get('location')} " +
                f"day={(nav2.rd() or {}).get('day')}")
            work_step()
        except Exception as ex:
            log(f"健康检查异常: {ex}")
        time.sleep(300)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
