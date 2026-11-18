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
_RESTARTS = []
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
        log("健康检查：没有游戏窗口（崩溃/被关）→ 先留崩溃日志再重启")
        crash = fallback.save_crash_logs()
        if crash:
            log(f"  崩溃日志已保存: {os.path.basename(crash)}")
        # 崩溃循环保护：10 分钟内重启超过 3 次，暂停 5 分钟
        now = time.time()
        _RESTARTS[:] = [t for t in _RESTARTS if now - t < 600]
        if len(_RESTARTS) >= 3:
            log("  10 分钟内已重启 3 次，疑似崩溃循环，暂停 5 分钟再试")
            time.sleep(300)
        _RESTARTS.append(now)
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
    """去矿洞挖一批：真实走路 + 真实挥镐（不瞬移）。"""
    try:
        import mine_real
        if not mine_real.enter_mine_walk():
            log("  挖矿：走路进矿失败，兜底传送上山再进")
            fallback.goto_or_warp("Mountain")
            nav2.send({"actionType": "enter_mine", "level": 1}, 2.0)
            time.sleep(4)
        n = mine_real.mine_rocks(max_mined)
        log(f"  挖矿：{n} 块，金币 {_money()}")
        return n
    except Exception as ex:
        log(f"  挖矿异常: {ex}")
        return 0


def fish_batch(attempts=2):
    try:
        import walk_fish
        # 背包满了就丢杂物腾位置（否则鱼放不进背包）
        used = len([x for x in ((nav2.rd() or {}).get("player") or {}).get("inventory") or [] if x])
        if used >= 11:
            for nm in ("松果", "树液", "纤维", "晶球"):
                nav2.send({"actionType": "throw_item", "name": nm}, 0.4)
        fallback.goto_or_warp("Beach")
        h, _ = gameio.game_window()
        if not h:
            return 0
        n = walk_fish.fish_from_pier(h, attempts)
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


def _inv():
    b = nav2.rd() or {}
    return b.get("player", {}).get("inventory") or []


def _count(name_part):
    n = 0
    for it in _inv():
        if it and name_part in str(it.get("name")):
            n += it.get("stack", 1)
    return n


def store_ores():
    """有箱子就把矿石存进去（没有就留在背包，不卖）。"""
    try:
        b = nav2.rd() or {}
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        chest = next((t for t in tiles if t.get("ObjectType") == "chest"), None)
        if not chest:
            return False
        nav2.send({"actionType": "chest_store_ores", "x": chest["X"], "y": chest["Y"]}, 0.8)
        log(f"  矿石已存入箱子 ({chest['X']},{chest['Y']})")
        return True
    except Exception as ex:
        log(f"  存矿石异常: {ex}")
        return False


def try_smelt():
    """铜矿石够就烧铜锭（需要熔炉；没有就先做）。"""
    try:
        ore = _count("铜矿石")
        stone = _count("石头")
        if ore < 5:
            return False
        b = nav2.rd() or {}
        tiles = (b.get("surroundings") or {}).get("Tiles") or []
        furnace = next((t for t in tiles if t.get("ObjectType") == "machine" and "熔炉" in str(t.get("ObjectName"))), None)
        if not furnace and stone >= 25 and ore >= 20:
            nav2.send({"actionType": "learn_recipe", "name": "Furnace"}, 0.8)
            nav2.send({"actionType": "craft_item", "item": "Furnace"}, 1.2)
            log("  试着做了个熔炉")
            return False
        if furnace:
            nav2.send({"actionType": "machine_drop_in", "x": furnace["X"], "y": furnace["Y"], "item": "铜矿石"}, 1.2)
            time.sleep(30)
            nav2.send({"actionType": "machine_collect", "x": furnace["X"], "y": furnace["Y"]}, 1.0)
            log("  熔炉：收了一炉")
            return True
        return False
    except Exception as ex:
        log(f"  熔炼异常: {ex}")
        return False


def ship_and_sleep_if_late():
    """时间晚了就装船+睡觉跨天（快速跨天用 set_clock 兜底）。"""
    b = nav2.rd() or {}
    if b.get("time", 0) < 1800:
        return False
    try:
        fallback.goto_or_warp("Farm")
        # 只卖收的菜/水果/花，矿石一律不卖
        _send({"actionType": "sell_crops"}, 0.8)
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


FESTIVALS = {
    ("spring", 13): ("彩蛋节", "Town"),
    ("spring", 24): ("花舞节", "Forest"),
    ("summer", 11): ("夏威夷宴会", "Beach"),
    ("summer", 28): ("月光水母", "Beach"),
    ("fall", 16): ("星露谷集市", "Town"),
    ("fall", 27): ("幽灵节", "Town"),
    ("winter", 8): ("冰雪节", "Forest"),
    ("winter", 25): ("冬星节", "Town"),
}


def plan_today(b):
    """按季节/天气/运气给今天选活（参考星露谷冬季攻略：冬天不下地，挖矿/钓鱼为主）。"""
    season = str(b.get("season") or "")
    weather = str(b.get("weather") or "sunny")
    raining = weather in ("rain", "storm")
    luck = b.get("luck")
    t = b.get("time", 600)
    try:
        luck = float(luck)
    except Exception:
        luck = 0.0
    # 节日优先
    day_of_month = b.get("day", 0)
    fest = FESTIVALS.get((season, day_of_month))
    if fest:
        return "festival", f"今天是{fest[0]}，去{fest[1]}过节"
    if season == "winter":
        # 冬天：上午挖矿，下午钓鱼；下雪天也照常
        if t < 1200:
            return "mine", "冬天上午：挖矿攒矿石"
        return "fish", "冬天下午：钓鱼换钱"
    if t < 1100:
        if raining:
            return "mine", "下雨天：下矿"
        return "farm", "上午：农场浇水/收菜"
    if luck > 0.05:
        return "mine", "运气不错：挖矿"
    return "fish", "下午：钓鱼"


def work_step():
    """一次干活轮次：按季节/天气/运气挑活，晚了就装船睡觉。"""
    fallback.ensure_ready()
    b = nav2.rd() or {}
    mode, why = plan_today(b)
    log(f"  自动干活：day={b.get('day')} {b.get('season')} {b.get('time')} money={_money()} → {why}")
    nav2.set_timeout(150)   # 单轮活最多 150 秒，超时就下一轮（避免一直卡）
    try:
        _do_work(mode, b)
    except TimeoutError:
        log("  本轮超时（150 秒），跳过，下一轮再试")
    finally:
        nav2.clear_timeout()
    return


def _do_work(mode, b):
    if mode == "festival":
        fest = FESTIVALS.get((b.get("season"), b.get("day")))
        if fest:
            log(f"  去{fest[1]}参加{fest[0]}")
            fallback.goto_or_warp(fest[1])
            time.sleep(60)   # 到场停留，让节日开始
            b2 = nav2.rd() or {}
            if b2.get("location") != "FarmHouse" and b2.get("time", 0) < 1800:
                # 节日后继续干活
                if b2.get("time", 0) < 1200:
                    mine_batch(6)
                else:
                    fish_batch(2)
    elif mode == "mine":
        mine_batch(8)
        store_ores()
        try_smelt()
    elif mode == "fish":
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
            fallback.keep_awake()
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
