#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查看 / 设置固定农田矩形（.plot_rect）。

播种、清障、收割三处都读同一份 .plot_rect，所以改这里就够了。

用法：
  python stardew_plot_rect.py                 # 查看当前区域
  python stardew_plot_rect.py --set 50,20,62,32   # 设置成 (50,20)-(62,32)
  python stardew_plot_rect.py --set 50,20,62,32 --dry   # 只看会写成什么
"""
import argparse
import sys

sys.path.insert(0, r'D:\live\deepseek-live\stardew-mod\agent')
import stardew_agent as A  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=None, help="x1,y1,x2,y2")
    ap.add_argument("--dry", action="store_true", help="只打印，不写入")
    args = ap.parse_args()

    cur = A.plot_rect()
    if args.set:
        parts = [p.strip() for p in args.set.replace("，", ",").split(",") if p.strip()]
        if len(parts) != 4:
            print("格式不对，要 x1,y1,x2,y2")
            return 2
        x1, y1, x2, y2 = (int(p) for p in parts)
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)
        w, h = x2 - x1 + 1, y2 - y1 + 1
        print("将设置为 (%d,%d)-(%d,%d)，%d×%d = %d 格" % (x1, y1, x2, y2, w, h, w * h))
        if not args.dry:
            A.remember_plot_rect(x1, y1, x2, y2, log_fn=print)
            print("已写入 %s" % A.PLOT_RECT_FILE)
        return 0

    if cur:
        x1, y1, x2, y2 = cur
        print("当前区域 (%d,%d)-(%d,%d)，%d×%d = %d 格"
              % (x1, y1, x2, y2, x2 - x1 + 1, y2 - y1 + 1,
                 (x2 - x1 + 1) * (y2 - y1 + 1)))
    else:
        print("还没设置（会退回「角色周围 10×20」）")
    print("文件：%s" % A.PLOT_RECT_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
