"""真实《植物大战僵尸》AI 接入：抓屏 → 识别 → DeepSeek 决策 → 点击操作。

用法：
  python real_ai.py --ticks 12 --dry     # 只决策，不点鼠标
  python real_ai.py --ticks 200          # 真打
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import decide, parse_action
from util import BASE_DIR, _window_client_rect, capture_game, click, load_config, say
from winrt_ocr import read_sun_winrt, ocr_lines
from sun_ocr import read_sun as read_sun_tpl

LOG = os.path.join(BASE_DIR, "real_ai.log")


def bring_to_front(title):
    """把游戏窗口置顶+激活，保证抓屏/点击落在游戏上。"""
    import ctypes

    u32 = ctypes.windll.user32
    hwnd = u32.FindWindowW(None, title)
    if not hwnd:
        return False
    u32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0040)  # HWND_TOPMOST, NOSIZE|SHOWWINDOW
    u32.SetForegroundWindow(hwnd)
    return True
TITLE = "Plants vs. Zombies"

# 当前关卡（1-3）的卡片：名字 / 阳光花费 / 卡槽序号
CARDS = [
    {"name": "豌豆射手", "cost": 100, "slot": 0},
    {"name": "向日葵", "cost": 50, "slot": 1},
    {"name": "樱桃炸弹", "cost": 150, "slot": 2},
    {"name": "坚果墙", "cost": 50, "slot": 3},
]

SUN_TOKEN_TPL = os.path.join(BASE_DIR, "templates", "sun_token2.png")


def log(msg):
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def detect_lawn(img):
    """用绿色区域自动找草坪范围，返回 (x0, y0, cell_w, cell_h, cols, rows)。"""
    hsv = np.asarray(img.convert("HSV"))
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    green = ((h > 35) & (h < 95) & (s > 90) & (v > 60)).astype(np.uint8)
    green = cv2.morphologyEx(green, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    green = cv2.morphologyEx(green, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    cnts, _ = cv2.findContours(green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        x, y, w, hh = cv2.boundingRect(c)
        if w < 600 or hh < 250 or cv2.contourArea(c) < 120000:
            continue
        if best is None or w * hh > best[2] * best[3]:
            best = (x, y, w, hh)
    if not best:
        return None
    x, y, w, hh = best
    x = max(x, 25)
    cols, rows = 9, 5
    return (x, y, w / cols, hh / rows, cols, rows)


def cell_center(lawn, row, col):
    x0, y0, cw, ch, _, _ = lawn
    return int(x0 + (col + 0.5) * cw), int(y0 + (row + 0.5) * ch)


def cell_has_plant(img, lawn, row, col):
    """格子里是否有植物（绿色饱和度高的像素比例）。"""
    cfgs = load_config()
    x0, y0, cw, ch, _, _ = lawn
    cx, cy = cell_center(lawn, row, col)
    w, h = int(cw * 0.5), int(ch * 0.5)
    crop = np.asarray(img.crop((cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2)).convert("HSV"))
    sat = crop[:, :, 1]
    bright = crop[:, :, 2]
    mask = (sat > 120) & (bright > 90)
    return float(mask.mean()) > 0.10


def slot_ready(img, slot, slot_x0=100, slot_w=53, slot_h=84, slot_y=8):
    """卡片是否可用（冷却时卡片变灰暗）。"""
    x = slot_x0 + slot * slot_w
    crop = np.asarray(img.crop((x + 4, slot_y + 4, x + slot_w - 4, slot_y + slot_h - 20)).convert("HSV"))
    sat = crop[:, :, 1]
    val = crop[:, :, 2]
    colorful = float(((sat > 60) & (val > 60)).mean())
    return colorful > 0.35


def find_sun_token(img, tpl):
    gray = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
    _, maxv, _, maxloc = cv2.minMaxLoc(res)
    if maxv < 0.62:
        return None
    th, tw = tpl.shape[:2]
    return int(maxloc[0] + tw / 2), int(maxloc[1] + th / 2)


BUTTON_TEXTS = ["再次尝试", "下一关", "继续游戏", "继续", "点击继续", "开始游戏"]


def recover(img, ox, oy):
    """OCR 找按钮并点击（游戏结束/过关/暂停都能救）。返回点到的文字。"""
    lines = ocr_lines(img, scale=2)
    if not lines:
        return None
    # OCR 常把中文拆成单字，这里按行聚合
    groups = []
    for (text, x, y, w, h) in lines:
        placed = False
        for g in groups:
            if abs(g["y"] - y) < 22:
                g["items"].append((x, y, w, h, text))
                g["x0"] = min(g["x0"], x)
                g["x1"] = max(g["x1"], x + w)
                g["y0"] = min(g["y0"], y)
                g["y1"] = max(g["y1"], y + h)
                placed = True
                break
        if not placed:
            groups.append({"y": y, "x0": x, "x1": x + w, "y0": y, "y1": y + h,
                           "items": [(x, y, w, h, text)]})
    for g in groups:
        text = "".join(it[4] for it in sorted(g["items"], key=lambda it: it[0]))
        for kw in ("再次尝试", "再次", "下一关", "继续游戏", "继续", "开始"):
            if kw in text or (kw == "再次尝试" and ("再" in text and "试" in text)):
                cx = ox + (g["x0"] + g["x1"]) // 2
                cy = oy + (g["y0"] + g["y1"]) // 2
                click(cx, cy)
                return text or kw
    # 兜底：单字命中直接点它（OCR 常把中文拆开）
    for key in ("再", "试", "继", "下", "尝"):
        for (text, x, y, w, h) in lines:
            if key in text:
                click(ox + x + w // 2, oy + y + h // 2)
                return "单字:" + text
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=10)
    ap.add_argument("--loop", action="store_true", help="一直打，遇游戏结束/过关自动点")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-say", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    rc = _window_client_rect(TITLE)
    if not rc:
        log("游戏窗口没找到")
        return
    ox, oy = rc[0], rc[1]
    log(f"开工：客户区原点 {ox},{oy}")

    tpl = None
    if os.path.exists(SUN_TOKEN_TPL):
        with open(SUN_TOKEN_TPL, "rb") as f:
            tpl = cv2.imdecode(np.frombuffer(f.read(), np.uint8), cv2.IMREAD_GRAYSCALE)

    board = set()
    t = 0
    while True:
        t += 1
        if not args.loop and t > args.ticks:
            break
        if args.loop and args.ticks and t > args.ticks:
            break
        bring_to_front(TITLE)
        img = capture_game(TITLE)
        if img is None:
            log("抓帧失败")
            time.sleep(2)
            continue
        lawn = detect_lawn(img)
        if not lawn:
            btn = recover(img, ox, oy)
            if btn:
                log(f"检测到弹窗，自动点了「{btn}」")
                time.sleep(2.5)
                img = capture_game(TITLE)
                lawn = detect_lawn(img) if img is not None else None
            if not lawn:
                log("没找到草坪（可能不在战斗界面）")
                time.sleep(2)
                continue
        sun = read_sun_winrt(img, cfg["ui"]["sun_region"])
        if sun is None:
            sun = read_sun_tpl(img, cfg["ui"]["sun_region"])
        # 收集阳光
        if tpl is not None:
            tok = find_sun_token(img, tpl)
            if tok and not args.dry:
                click(ox + tok[0], oy + tok[1])
                time.sleep(0.4)
                img = capture_game(TITLE)
                if img is None:
                    time.sleep(1.0)
                    continue
                _s = read_sun_winrt(img, cfg["ui"]["sun_region"])
                if _s is None:
                    _s = read_sun_tpl(img, cfg["ui"]["sun_region"])
                sun = _s if _s is not None else sun
        # 卡片可用性
        available = [c["name"] for c in CARDS if sun is not None and sun >= c["cost"] and slot_ready(img, c["slot"])]
        # 已种格子
        planted = [[r, c] for (r, c) in sorted(board)]
        state = {
            "sun": sun,
            "available": available,
            "planted": planted,
            "grid": {"rows": 5, "cols": 9},
            "note": "选 available 里的植物，种在没被占用的格子；向日葵优先铺经济",
        }
        log(f"tick{t} 阳光={sun} 可用={available} 已种={len(planted)} 草坪={tuple(round(v,1) for v in lawn)}")
        text, err = decide(state, cfg["brain"]["model"], cfg["brain"]["max_tokens"], cfg["brain"]["temperature"])
        if err:
            log("决策失败: " + str(err))
            time.sleep(cfg["tick_seconds"])
            continue
        act = parse_action(text)
        log("AI: " + (json.dumps(act, ensure_ascii=False) if act else str(text)))
        if act and act.get("action") == "plant":
            seed = act.get("seed")
            row, col = int(act.get("row", 0)), int(act.get("col", 0))
            card = next((c for c in CARDS if c["name"] == seed), None)
            if not card:
                log(f"没有卡片 {seed}")
            elif [row, col] in planted:
                log(f"({row},{col}) 已占用")
            elif args.dry:
                log(f"[dry] 将种 {seed} -> ({row},{col})")
            else:
                sx = ox + 100 + card["slot"] * 53 + 26
                sy = oy + 8 + 42
                click(sx, sy)
                time.sleep(0.25)
                cx, cy = cell_center(lawn, row, col)
                click(ox + cx, oy + cy)
                time.sleep(0.9)
                img2 = capture_game(TITLE)
                if img2 is not None:
                    w2, h2 = int(lawn[2] * 0.6), int(lawn[3] * 0.6)
                    before = np.asarray(img.crop((cx - w2 // 2, cy - h2 // 2, cx + w2 // 2, cy + h2 // 2)).convert("L"), dtype=np.int16)
                    after = np.asarray(img2.crop((cx - w2 // 2, cy - h2 // 2, cx + w2 // 2, cy + h2 // 2)).convert("L"), dtype=np.int16)
                    diff = float(np.abs(after - before).mean())
                    if diff > 3.0:
                        board.add((row, col))
                        log(f"种下 {seed} ({row},{col}) 成功 diff={diff:.1f}")
                    else:
                        log(f"点 ({row},{col}) 没变化 diff={diff:.1f}（可能已有植物/阳光不够）")
                else:
                    board.add((row, col))
                    log(f"种下 {seed} ({row},{col})（无法复核）")
                if not args.no_say:
                    say(f"{seed}，第{row + 1}行")
        time.sleep(cfg["tick_seconds"])


if __name__ == "__main__":
    main()
