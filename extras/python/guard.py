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


def main():
    once = "--once" in sys.argv
    if not once and not single_instance():
        log("已有守护进程在跑，退出")
        return
    log("守护进程启动")
    if once:
        check_once()
        return
    while True:
        try:
            ok = check_once()
            log(f"健康检查: {'正常' if ok else '异常（已尝试修复）'} | " +
                f"loc={(nav2.rd() or {}).get('location')} " +
                f"day={(nav2.rd() or {}).get('day')}")
        except Exception as ex:
            log(f"健康检查异常: {ex}")
        time.sleep(300)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
