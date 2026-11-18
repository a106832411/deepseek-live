"""截图识别矿洞洞口/梯子：模板匹配 → 换算成格子坐标。

用法：
    import vision_ladder
    vision_ladder.locate_holes()   # -> [(tileX, tileY, score, kind), ...]
"""
import json
import os
import time

import numpy as np
from PIL import Image, ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
BRIDGE = r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge\bridge_data.json"
LOG = os.path.join(HERE, "logs", "vision.log")


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def capture():
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    rows = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, l):
        n = u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if b.value.startswith("Stardew Valley"):
                r = wintypes.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                rows.append((h, r))
        return True

    u.EnumWindows(cb, 0)
    if not rows:
        return None
    h, r = rows[0]
    u.ShowWindow(h, 9)
    u.SetForegroundWindow(h)
    time.sleep(0.5)
    return ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))


def _bridge():
    with open(BRIDGE, encoding="utf-8") as f:
        return json.load(f)


def _match(screen_arr, tpl_arr, threshold=0.6, max_hits=5):
    """用 OpenCV 模板匹配（归一化相关）。"""
    import cv2
    res = cv2.matchTemplate(screen_arr, tpl_arr, cv2.TM_CCOEFF_NORMED)
    hits = []
    sc = res.copy()
    th, tw = tpl_arr.shape
    for _ in range(max_hits):
        _, val, _, loc = cv2.minMaxLoc(sc)
        if val < threshold:
            break
        hits.append((int(loc[0]), int(loc[1]), float(val)))
        x0, y0 = loc
        sc[max(0, y0 - th // 2):y0 + th // 2, max(0, x0 - tw // 2):x0 + tw // 2] = -1
    return hits


def locate_holes(threshold=0.75, save_debug=False):
    """返回 [(tile_x, tile_y, score, kind)]，kind: hole/ladder。"""
    b = _bridge()
    vp = b["player"]["viewport"]
    img = capture()
    if img is None:
        return []
    screen = np.asarray(img.convert("L"), dtype=np.float32)
    out = []
    for kind, name in (("hole", "hole_tpl.png"), ("ladder", "ladder_tpl.png")):
        p = os.path.join(ASSETS, name)
        if not os.path.exists(p):
            continue
        tpl = np.asarray(Image.open(p).convert("L"), dtype=np.float32)
        # 窗口中心对齐：加上模板一半
        for (x, y, score) in _match(screen, tpl, threshold):
            cx = x + tpl.shape[1] / 2
            cy = y + tpl.shape[0] / 2
            tx = round((cx - 8 - 32 + int(vp["x"])) / 64)
            ty = round((cy - 8 - 32 + int(vp["y"])) / 64)
            out.append((tx, ty, score, kind))
    if save_debug:
        img.save(os.path.join(HERE, "logs", "vision_debug.png"))
    out.sort(key=lambda z: -z[2])
    if out:
        log(f"识别到 {len(out)} 个候选: {out[:6]}")
    return out


if __name__ == "__main__":
    for h in locate_holes(save_debug=True):
        print(h)
