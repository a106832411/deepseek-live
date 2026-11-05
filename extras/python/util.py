"""窗口抓帧 + 鼠标点击 + 鲸语播报 基础工具。"""
import ctypes
import json
import os
import time

from PIL import Image
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------- 抓帧 ----------------
def _window_client_rect(window_title):
    """返回 (left, top, width, height) 客户区屏幕坐标。"""
    from ctypes import wintypes

    u32 = ctypes.windll.user32
    hwnd = u32.FindWindowW(None, window_title)
    if not hwnd:
        return None
    rect = wintypes.RECT()
    u32.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wintypes.POINT(0, 0)
    u32.ClientToScreen(hwnd, ctypes.byref(pt))
    return (pt.x, pt.y, rect.right - rect.left, rect.bottom - rect.top)


def capture_frame(window_title):
    """抓整个显示器再裁到游戏客户区（老游戏按窗口抓帧是黑的，显示器抓帧正常）。"""
    from windows_capture import WindowsCapture, Frame, InternalCaptureControl

    holder = {}

    cap = WindowsCapture(cursor_capture=False, draw_border=False, monitor_index=1)

    @cap.event
    def on_frame_arrived(frame: Frame, control: InternalCaptureControl):
        buf = np.asarray(frame.frame_buffer)
        holder["img"] = Image.fromarray(buf[:, :, [2, 1, 0]].copy())
        control.stop()

    @cap.event
    def on_closed():
        pass

    try:
        cap.start()
    except Exception:
        return None
    img = holder.get("img")
    if img is None:
        return None
    rc = _window_client_rect(window_title)
    if not rc:
        return None
    x, y, w, h = rc
    return img.crop((max(0, x), max(0, y), min(img.width, x + w), min(img.height, y + h)))


def _is_black(img, ratio=0.995):
    g = np.asarray(img.convert("L"))
    return float((g < 8).mean()) > ratio


def capture_game(window_title, tries=6):
    """多次抓帧，取第一张不是全黑的（WinRT 偶尔会回黑帧）。"""
    last = None
    for _ in range(tries):
        img = capture_frame(window_title)
        if img is None:
            continue
        last = img
        if not _is_black(img):
            return img
        time.sleep(0.15)
    return last


def capture_window(window_title):
    """兼容旧名字：等同 capture_game。"""
    return capture_game(window_title)



def save_shot(img, name="shot"):
    os.makedirs(os.path.join(BASE_DIR, "shots"), exist_ok=True)
    path = os.path.join(BASE_DIR, "shots", name + "_" + time.strftime("%H%M%S") + ".png")
    img.save(path)
    return path


# ---------------- 鼠标 ----------------
_u32 = ctypes.windll.user32
_u32.SetProcessDPIAware()


def click(x, y, delay=0.08):
    _u32.SetCursorPos(int(x), int(y))
    time.sleep(delay)
    _u32.mouse_event(0x0002, 0, 0, 0, 0)  # left down
    time.sleep(0.03)
    _u32.mouse_event(0x0004, 0, 0, 0, 0)  # left up


# ---------------- 鲸语播报 ----------------
def say(text, timeout=10):
    import urllib.request

    body = json.dumps({"text": text}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8787/api/say",
        data=body,
        headers={"Content-Type": "application/json", "X-BVB": "1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def load_config():
    with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def app_ai_config():
    """从鲸语 config 里取 DeepSeek 的 key。"""
    try:
        p = os.path.join(BASE_DIR, "..", "WhaleTalk", "config.json")
        with open(p, encoding="utf-8") as f:
            c = json.load(f)
        return c["ai"]["openai_key"], c["ai"].get("openai_base", "https://api.deepseek.com/v1")
    except Exception:
        return None, None
