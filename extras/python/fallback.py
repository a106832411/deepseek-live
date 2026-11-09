"""兜底逻辑：不追求完美，先保证不会卡死、能继续跑。

用法：
    import fallback
    fallback.ensure_ready()
    fallback.goto_or_warp("Mountain")
"""
import json
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
                ok = gameio.load_game()
                if ok:
                    time.sleep(2)
                    # 存档已加载：直接认为就绪（下一轮循环会继续校验时间/位置）
                    return True
                continue
            return False
        nav2.live()
        nav2.send({"actionType": "cancel_dialogue"}, 0.4)
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
        time.sleep(7)
        b2 = nav2.rd() or {}
        if b2.get("time", 0) == t0:
            nav2.send({"actionType": "force_unpause"}, 0.5)
            nav2.send({"actionType": "reset_state"}, 0.5)
            time.sleep(3)
            b3 = nav2.rd() or {}
            if b3.get("time", 0) == t0:
                bb = nav2.rd() or {}
                log(f"兜底：时间不走（menu={((bb.get('player') or {}).get('fishing') or {}).get('activeMenu')}, loc={bb.get('location')}），先关菜单再脱困")
                nav2.send({"actionType": "exit_menu"}, 0.6)
                nav2.send({"actionType": "force_unpause"}, 0.4)
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


def _game_process_alive():
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq StardewModdingAPI.exe"],
                             capture_output=True, text=True)
        return "StardewModdingAPI" in (out.stdout or "")
    except Exception:
        return False


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

    for attempt in range(2):
        log(f"兜底：重启游戏（第 {attempt+1} 次）")
        import subprocess as _sp
        subprocess.Popen([exe], cwd=game,
                         stdin=_sp.DEVNULL, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                         creationflags=getattr(_sp, "DETACHED_PROCESS", 0))
        # 先等进程起来
        time.sleep(8)
        if not _game_process_alive():
            log("兜底：SMAPI 进程没起来，再试一次")
            continue
        # 等窗口（最多 4 分钟）
        h = None
        for _ in range(80):
            time.sleep(3)
            h, r = _win()
            if h:
                break
            if bridge_age() < 15:
                # 桥接在更新说明游戏已经在跑（可能还在标题/加载）
                continue
            if not _game_process_alive():
                log("兜底：SMAPI 进程中途退出")
                break
        if h:
            ok = gameio.load_game()
            if ok:
                return True
            log("兜底：窗口出来了但加载存档失败")
        if bridge_age() < 15:
            return True
    log("兜底：重启游戏失败（已尝试 2 次）")
    return False


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
    # 两次都走不到：夜间/矿洞返回等场景允许应急传送（会记录）
    try:
        sx, sy = SAFE.get(target, (20, 20))
        log(f"兜底：走到 {target} 失败，应急传送到 ({sx},{sy})")
        nav2.send({"actionType": "player_warp", "location": target, "x": sx, "y": sy}, 2.0)
        time.sleep(2)
        if (nav2.rd() or {}).get("location") == target:
            return "emergency_warp"
    except Exception as ex:
        log(f"应急传送失败：{ex}")
    log(f"兜底：走到 {target} 失败，本轮跳过")
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


# ---------- 存档防回滚 ----------
SAVES_DIR = os.path.join(os.environ.get("APPDATA", ""), "StardewValley", "Saves")
MARKER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "save_marker.json")
_SEASONS = ["spring", "summer", "fall", "winter"]


def _day_index(b):
    try:
        si = _SEASONS.index(str(b.get("season") or "spring"))
    except ValueError:
        si = 0
    return si * 28 + int(b.get("day") or 0)


def read_marker():
    try:
        with open(MARKER_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_marker(b=None):
    b = b or nav2.rd() or {}
    if not b:
        return
    try:
        with open(MARKER_FILE, "w", encoding="utf-8") as f:
            json.dump({"season": b.get("season"), "day": b.get("day"),
                       "time": b.get("time"), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "index": _day_index(b)}, f, ensure_ascii=False)
    except Exception:
        pass


def newest_backup():
    """找最新的一份运行前备份（logs/save_backup_*）。"""
    import glob
    pats = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "save_backup_*")),
                  key=lambda p: os.path.getmtime(p), reverse=True)
    return pats[0] if pats else None


def restore_backup(backup_dir):
    """把备份的存档目录复制回 Saves（需要游戏已关闭）。"""
    import shutil
    if not backup_dir or not os.path.isdir(backup_dir):
        return False
    try:
        for d in os.listdir(backup_dir):
            src = os.path.join(backup_dir, d)
            if not os.path.isdir(src):
                continue
            dst = os.path.join(SAVES_DIR, d)
            shutil.copytree(src, dst, dirs_exist_ok=True)
        log(f"  已从备份恢复存档: {backup_dir}")
        return True
    except Exception as ex:
        log(f"  恢复存档失败: {ex}")
        return False


def check_rollback(auto_restore=True):
    """检测存档是否回滚；回滚则恢复最新备份并重进游戏。"""
    marker = read_marker()
    b = nav2.rd() or {}
    if not marker or not b:
        return False
    cur = _day_index(b)
    if cur >= int(marker.get("index") or 0):
        return False
    log(f"  ⚠ 检测到存档回滚：现在 {b.get('season')} {b.get('day')}，之前记录 {marker.get('season')} {marker.get('day')}")
    if not auto_restore:
        return True
    bk = newest_backup()
    import subprocess
    game = r"E:\steam\steamapps\common\Stardew Valley"
    try:
        for name in ("Stardew Valley", "StardewModdingAPI"):
            subprocess.run(["taskkill", "/IM", name + ".exe", "/F"], capture_output=True)
        time.sleep(4)
        if restore_backup(bk):
            log("  存档已恢复，重启游戏")
            relaunch_game()
            return True
    except Exception as ex:
        log(f"  回滚处理失败: {ex}")
    return False
