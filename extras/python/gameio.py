import ctypes
import json
import os
import time
from ctypes import wintypes

u = ctypes.windll.user32
BRIDGE_FILE = r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge\bridge_data.json"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def game_window():
    wins = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, l):
        n = u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if b.value.startswith("Stardew Valley"):
                if u.IsIconic(h):
                    u.ShowWindow(h, 9)
                    time.sleep(0.2)
                r = wintypes.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                if r.right - r.left > 600 and r.bottom - r.top > 400:
                    wins.append((h, r))
        return True

    u.EnumWindows(cb, 0)
    return wins[0] if wins else (None, None)


def focus(h):
    u.ShowWindow(h, 9)
    u.keybd_event(0x12, 0, 0, 0)
    u.SetForegroundWindow(h)
    u.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.5)


def click(h, r, rx, ry):
    focus(h)
    u.SetCursorPos(r.left + rx, r.top + ry)
    time.sleep(0.15)
    u.mouse_event(0x02, 0, 0, 0, 0)
    time.sleep(0.12)
    u.mouse_event(0x04, 0, 0, 0, 0)
    time.sleep(0.4)


def bridge_mtime():
    try:
        return os.path.getmtime(BRIDGE_FILE)
    except Exception:
        return 0


def load_game(timeout_attempts=6):
    """在标题画面点「加载」→ 选第一个存档，并用 bridge 更新时间校验是否真的进游戏。"""
    for attempt in range(timeout_attempts):
        h, r = game_window()
        if not h:
            log("找不到游戏窗口")
            return False
        before = bridge_mtime()
        click(h, r, 520, 630)   # 加载
        time.sleep(1.0)
        click(h, r, 500, 150)   # 第一个存档
        t0 = time.time()
        while time.time() - t0 < 25:
            time.sleep(1.5)
            if bridge_mtime() > before + 0.5:
                b = json.load(open(BRIDGE_FILE, encoding="utf-8"))
                loc = b.get("location")
                t = b.get("player", {}).get("tile", {})
                if loc and loc not in ("titleScreen",):
                    log(f"进游戏成功: {loc} tile={t.get('x')},{t.get('y')} day={b.get('day')}")
                    return True
        log(f"第 {attempt+1} 次加载没成功，重试")
        time.sleep(2)
    return False


def walk_until(tx, ty, timeout=12):
    import subprocess
    send = {"x": tx, "y": ty}
    return send


if __name__ == "__main__":
    ok = load_game()
    print("load_game:", ok)
