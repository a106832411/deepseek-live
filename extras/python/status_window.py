"""小状态窗：实时显示星露谷 AI 现在在干什么。

运行（无控制台）：
    pythonw status_window.py
关闭：点窗口右上角 ×
"""
import json
import os
import time
import tkinter as tk
from tkinter import font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge\bridge_data.json"
GUARD_LOG = os.path.join(HERE, "logs", "guard.log")
PID_FILE = os.path.join(HERE, "logs", "guard.pid")

BG = "#121216"
FG = "#e8e6df"
GOLD = "#d8b25a"
DIM = "#8a857a"
OK = "#6fcf7f"
BAD = "#e06666"


def read_bridge():
    try:
        with open(BRIDGE, encoding="utf-8") as f:
            return json.load(f), os.path.getmtime(BRIDGE)
    except Exception:
        return None, 0


def guard_alive():
    try:
        pid = int(open(PID_FILE, encoding="utf-8").read().strip())
    except Exception:
        return False
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    except Exception:
        return False


def tail_guard(n=4):
    try:
        with open(GUARD_LOG, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return [l.rstrip("\n") for l in lines[-n:]]
    except Exception:
        return []


class Window:
    def __init__(self, root):
        root.title("鲸live · 星露谷 AI 状态")
        root.geometry("460x260+40+40")
        root.configure(bg=BG)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.94)
        except Exception:
            pass

        title_font = tkfont.Font(family="Microsoft YaHei UI", size=11, weight="bold")
        big_font = tkfont.Font(family="Microsoft YaHei UI", size=13, weight="bold")
        mono = tkfont.Font(family="Microsoft YaHei UI", size=9)

        self.head = tk.Label(root, text="鲸live · 星露谷 AI", bg=BG, fg=GOLD, font=title_font, anchor="w")
        self.head.pack(fill="x", padx=12, pady=(8, 0))

        self.status = tk.Label(root, text="连接中…", bg=BG, fg=FG, font=big_font, anchor="w")
        self.status.pack(fill="x", padx=12)

        self.info = tk.Label(root, text="", bg=BG, fg=FG, font=mono, anchor="w", justify="left")
        self.info.pack(fill="x", padx=12, pady=2)

        self.task = tk.Label(root, text="", bg=BG, fg=GOLD, font=mono, anchor="w", justify="left", wraplength=430)
        self.task.pack(fill="x", padx=12, pady=(4, 2))

        self.logs = tk.Label(root, text="", bg=BG, fg=DIM, font=mono, anchor="w", justify="left", wraplength=430)
        self.logs.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        self.tick()

    def tick(self):
        b, mtime = read_bridge()
        alive = guard_alive()
        age = time.time() - mtime if mtime else 999

        if not b:
            self.status.config(text="游戏未连接", fg=BAD)
            self.info.config(text="等待游戏启动…")
        else:
            loc = b.get("location", "?")
            day = b.get("day", "?")
            t = b.get("time", 0)
            hh, mm = (t // 100) % 24, t % 100
            money = (b.get("player") or {}).get("money", "?")
            tile = ((b.get("player") or {}).get("tile") or {})
            fresh = age < 30
            self.status.config(text=("● 运行中" if fresh else "● 数据不更新"), fg=(OK if fresh else BAD))
            self.info.config(
                text=f"位置 {loc}  ({tile.get('x')},{tile.get('y')})\n"
                     f"第 {day} 天  {hh:02d}:{mm:02d}   金币 {money}\n"
                     f"守护进程 {'在跑' if alive else '未运行'} · 数据 {age:.0f}s 前")

        logs = tail_guard(4)
        task = ""
        for line in reversed(logs):
            if "自动干活" in line or "挖矿" in line or "钓鱼" in line or "农场" in line or "跨天" in line:
                task = line.split("] ", 1)[-1]
                break
        self.task.config(text="现在：" + (task or "空闲/待命"))
        self.logs.config(text="\n".join(l.split("] ", 1)[-1] for l in logs))

        self.root_after = self.status.after(1000, self.tick)


def main():
    root = tk.Tk()
    Window(root)
    root.mainloop()


if __name__ == "__main__":
    main()
