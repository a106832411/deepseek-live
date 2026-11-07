"""兜底逻辑：不追求完美，先保证不会卡死、能继续跑。

用法：
    import fallback
    fallback.ensure_ready()
    fallback.goto_or_warp("Mountain")
"""
import os
import time

import gameio
import nav2

log = nav2.log

SAFE = nav2.SAFE


def bridge_age():
    try:
        return time.time() - os.path.getmtime(nav2.BRIDGE_FILE)
    except Exception:
        return 999


def ensure_ready(auto_load=True, tries=3):
    """确保在游戏里、没暂停、没菜单、位置合法。全部失败再重载存档。"""
    for _ in range(tries):
        if bridge_age() > 6:
            if auto_load:
                h, _ = _win()
                if not h:
                    relaunch_game()
                    break
                log("兜底：桥接数据过期（可能在标题页），重新加载存档")
                gameio.load_game()
                continue
            return False
        nav2.live()
        nav2.send({"actionType": "force_unpause"}, 0.5)
        # 踩在出口格上容易卡在过图状态：主动触发一次过图
        try:
            bb = nav2.rd() or {}
            tt = nav2.tile(bb)
            for e in (bb.get("exits") or []):
                if abs(int(e.get("x", -99)) - tt[0]) + abs(int(e.get("y", -99)) - tt[1]) <= 1:
                    nav2.send({"actionType": "enter_exit", "target": e.get("target")}, 1.2)
                    time.sleep(2)
                    break
        except Exception:
            pass
        b = nav2.rd()
        if not b:
            time.sleep(2)
            continue
        loc = b.get("location")
        t = nav2.tile(b)
        if abs(t[0]) > 150 or abs(t[1]) > 150:
            sx, sy = SAFE.get(loc, (20, 20))
            log(f"兜底：位置异常 {t}@{loc}，放回 {sx},{sy}")
            nav2.send({"actionType": "set_tile", "x": sx, "y": sy}, 0.8)
            continue
        # 时间是否在走：冻结就强力恢复，两次还不行就重载游戏
        t0 = b.get("time", 0)
        time.sleep(3)
        b2 = nav2.rd() or {}
        if b2.get("time", 0) == t0:
            nav2.send({"actionType": "force_unpause"}, 0.5)
            nav2.send({"actionType": "reset_state"}, 0.5)
            time.sleep(3)
            b3 = nav2.rd() or {}
            if b3.get("time", 0) == t0:
                log("兜底：时间不走，尝试脱困（走一步/过图/传送）")
                if unfreeze():
                    log("  脱困成功，时间恢复")
                    return True
                log("兜底：脱困失败，重载游戏")
                gameio.load_game()
                time.sleep(5)
            continue
        return True
    return False


def keep_awake():
    """阻止 Windows 睡眠/息屏，免得挂机时游戏被休眠掉。"""
    try:
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    except Exception:
        pass


def save_crash_logs():
    """崩溃时把 SMAPI 日志留一份，方便查原因。"""
    try:
        import shutil
        src = os.path.join(os.environ.get("APPDATA", ""), "StardewValley", "ErrorLogs", "SMAPI-latest.txt")
        if not os.path.exists(src):
            return None
        outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(outdir, exist_ok=True)
        dst = os.path.join(outdir, "crash_" + time.strftime("%Y%m%d_%H%M%S") + ".txt")
        shutil.copy2(src, dst)
        return dst
    except Exception:
        return None


def relaunch_game():
    """游戏窗口没了 → 重启 SMAPI 并加载存档（兜底）。

    注意：只清理“确认无游戏窗口”时的残留进程，正常情况下绝不杀游戏。
    """
    import subprocess
    game = r"E:\steam\steamapps\common\Stardew Valley"
    exe = os.path.join(game, "StardewModdingAPI.exe")
    if not os.path.exists(exe):
        log("兜底：找不到 SMAPI 启动器")
        return False
    h, _ = _win()
    if h:
        log("兜底：已有游戏窗口，不重启")
        return gameio.load_game()
    log("兜底：重启游戏")
    import subprocess as _sp
    subprocess.Popen([exe], cwd=game,
                     stdin=_sp.DEVNULL, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                     creationflags=getattr(_sp, "DETACHED_PROCESS", 0))
    h = None
    for _ in range(50):
        time.sleep(3)
        h, r = _win()
        if h:
            break
    if not h:
        log("兜底：等待游戏窗口超时")
        return False
    return gameio.load_game()


def _win():
    try:
        import gameio as gi
        return gi.game_window()
    except Exception:
        return (None, None)


def unfreeze():
    """时间冻结/卡在过图状态时：走一步、触发过图，最后才传送兜底。"""
    try:
        nav2.send({"actionType": "force_unpause"}, 0.5)
        nav2.send({"actionType": "player_wake"}, 0.8)
        b = nav2.rd() or {}
        me = nav2.tile(b)
        nav2.send({"actionType": "player_walk_to", "x": me[0] + 1, "y": me[1]}, 0.8)
        time.sleep(1.5)
        t0 = (nav2.rd() or {}).get("time", 0)
        time.sleep(2.5)
        if (nav2.rd() or {}).get("time", 0) != t0:
            return True
        b = nav2.rd() or {}
        ex = (b.get("exits") or [])
        if ex:
            nav2.send({"actionType": "enter_exit", "target": ex[0].get("target")}, 1.5)
            time.sleep(3)
        t1 = (nav2.rd() or {}).get("time", 0)
        time.sleep(2.5)
        b2 = nav2.rd() or {}
        if b2.get("time", 0) != t1:
            return True
        loc = b2.get("location")
        sx, sy = SAFE.get(loc, (20, 20))
        nav2.send({"actionType": "player_warp", "location": loc, "x": sx, "y": sy}, 1.5)
        time.sleep(3)
        t2 = (nav2.rd() or {}).get("time", 0)
        time.sleep(2.5)
        return (nav2.rd() or {}).get("time", 0) != t2
    except Exception as ex:
        log(f"unfreeze 异常: {ex}")
        return False


def goto_or_warp(target, walk_timeout=None):
    """只走路，不再传送（穿墙被用户禁掉了）。走不过去就清障重试，再不行就跳过。"""
    for attempt in range(2):
        try:
            if nav2.goto(target):
                return "walk"
        except Exception as ex:
            log(f"走路异常：{ex}")
        try:
            b = nav2.rd() or {}
            nav2.unstick()
            sx, sy = SAFE.get(target, (20, 20))
            nav2.break_obstacles(sx, sy)
        except Exception:
            pass
    log(f"兜底：走到 {target} 失败，本轮跳过（不传送）")
    return None


def panic_recover():
    """任何异常后调用：关菜单、清暂停、修位置。"""
    nav2.live()
    nav2.safe_check()
    b = nav2.rd() or {}
    return b


if __name__ == "__main__":
    ensure_ready()
    print("goto Mountain:", goto_or_warp("Mountain"))
    b = nav2.rd()
    log(f"现在 {b.get('location')} {nav2.tile(b)}")
