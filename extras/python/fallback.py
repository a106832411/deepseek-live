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
        # 时间是否在走
        t0 = b.get("time", 0)
        time.sleep(2.5)
        b2 = nav2.rd() or {}
        if b2.get("time", 0) == t0:
            nav2.send({"actionType": "reset_state"}, 0.5)
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


def goto_or_warp(target, walk_timeout=None):
    """先走路；走路失败就用传送兜底（会记日志）。"""
    try:
        if nav2.goto(target):
            return "walk"
    except Exception as ex:
        log(f"走路异常：{ex}")
    log(f"兜底：走路到 {target} 失败，改用传送")
    sx, sy = SAFE.get(target, (20, 20))
    nav2.send({"actionType": "player_warp", "location": target, "x": sx, "y": sy}, 2.0)
    time.sleep(2)
    b = nav2.rd() or {}
    ok = b.get("location") == target
    log(f"兜底传送结果：{ok} loc={b.get('location')}")
    return "warp" if ok else None


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
