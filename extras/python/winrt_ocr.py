"""用 Windows 自带 OCR（WinRT）识别阳光数字，不依赖游戏字体模版。"""
import asyncio

import numpy as np
from PIL import Image

try:
    from winrt.windows.graphics.imaging import (
        BitmapAlphaMode,
        BitmapPixelFormat,
        SoftwareBitmap,
    )
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
    HAS_WINRT = True
except Exception:  # pragma: no cover
    HAS_WINRT = False

_engine = None


def _get_engine():
    global _engine
    if _engine is None and HAS_WINRT:
        try:
            _engine = OcrEngine.try_create_from_user_profile_languages()
        except Exception:
            _engine = None
    return _engine


def _to_software_bitmap(img):
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size
    data = np.asarray(img, dtype=np.uint8).tobytes()
    writer = DataWriter()
    writer.write_bytes(data)
    buf = writer.detach_buffer()
    return SoftwareBitmap.create_copy_from_buffer(
        buf, BitmapPixelFormat.RGBA8, w, h, BitmapAlphaMode.PREMULTIPLIED
    )


async def _recognize(img):
    engine = _get_engine()
    if engine is None:
        return ""
    bmp = _to_software_bitmap(img)
    result = await engine.recognize_async(bmp)
    return result.text or ""


def ocr_text(img):
    if not HAS_WINRT:
        return ""
    try:
        return asyncio.run(_recognize(img))
    except Exception:
        return ""


def read_sun_winrt(img, region, scale=4):
    crop = img.crop((region["x"], region["y"], region["x"] + region["w"], region["y"] + region["h"]))
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    text = ocr_text(crop)
    digits = "".join(ch for ch in text if ch.isdigit())
    if 1 <= len(digits) <= 6:
        return int(digits)
    return None


if __name__ == "__main__":
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from util import capture_game, load_config

    cfg = load_config()
    r = cfg["ui"]["sun_region"]
    from PIL import Image as I

    base = os.path.dirname(os.path.abspath(__file__))
    for name in ("after_plant_140517.png", "raw_140447.png"):
        p = os.path.join(base, "shots", name)
        if os.path.exists(p):
            print(name, "->", read_sun_winrt(I.open(p), r))
    img = capture_game(cfg["window_title"])
    if img:
        print("live ->", read_sun_winrt(img, r))

async def _ocr_lines(img):
    engine = _get_engine()
    if engine is None:
        return []
    bmp = _to_software_bitmap(img)
    result = await engine.recognize_async(bmp)
    out = []
    for line in result.lines:
        for w in line.words:
            r = w.bounding_rect
            out.append((w.text, int(r.x), int(r.y), int(r.width), int(r.height)))
    return out


def ocr_lines(img, scale=2):
    """返回 (文字, x, y, w, h) 列表（按图片像素坐标，已还原缩放）。"""
    if not HAS_WINRT:
        return []
    try:
        big = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
        items = asyncio.run(_ocr_lines(big))
        return [(t, x // scale, y // scale, w // scale, h // scale) for (t, x, y, w, h) in items]
    except Exception:
        return []

