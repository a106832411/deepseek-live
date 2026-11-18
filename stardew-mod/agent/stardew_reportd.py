#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报告守护：每 3 分钟把最新状态 + 各活动日志尾部写进 NIGHT-REPORT.md。"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stardew_agent as A  # noqa: E402

OUT_DIR = r"D:\live\logs\night"
REPORT = os.path.join(OUT_DIR, "NIGHT-REPORT.md")
NIGHT_LOG = os.path.join(OUT_DIR, "night.log")
SRC_LOGS = [
    ("挖矿", r"D:\live\logs\mine2.log"),
    ("钓鱼", r"D:\live\logs\fishsession.log"),
    ("农场", r"D:\live\logs\farmdaily.log"),
    ("回家", r"D:\live\logs\home.log"),
]


def tail(path, n=12):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [l.rstrip() for l in f.readlines() if l.strip()]
        return lines[-n:]
    except Exception:
        return []


def parse_history(n=60):
    rows = []
    cur = None
    try:
        with open(NIGHT_LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.search(r"---- 第 (\d+) 轮：(\w+)", line)
                if m:
                    cur = {"cycle": m.group(1), "act": m.group(2), "res": "进行中", "t": line[1:9]}
                    rows.append(cur)
                    continue
                m2 = re.search(r"---- 第 (\d+) 轮 (\w+)：(成功|失败)", line)
                if m2 and cur:
                    cur["res"] = m2.group(3)
    except Exception:
        pass
    return rows[-n:]


def build():
    lines = []
    lines.append("# 星露谷夜间循环报告\n")
    lines.append("- 更新时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        s = A.summarize(A.read_state())
    except Exception as e:
        s = {}
        lines.append("- ⚠ 读取游戏状态失败：%s（游戏可能正在重启）" % e)
    lines.append("")
    lines.append("## 当前状态\n")
    lines.append("| 项目 | 值 |")
    lines.append("|---|---|")
    lines.append("| 位置 | %s %s |" % (s.get("location"), s.get("tile")))
    lines.append("| 游戏时间 | 第 %s 天 %s（%s） |" % (s.get("day"), s.get("time"), s.get("season")))
    lines.append("| 金币 | %s G |" % s.get("money"))
    lines.append("| 体力 | %s |" % s.get("stamina"))
    inv = s.get("inventory") or []
    lines.append("| 背包 | %s |" % ", ".join(inv[:16]))
    crops = [t for t in (s.get("tiles") or []) if t.get("crop")]
    ready = [t for t in crops if t.get("cropReady")]
    lines.append("| 附近作物 | %d 株（成熟 %d） |" % (len(crops), len(ready)))
    lines.append("")

    hist = parse_history()
    lines.append("## 活动历史（最近 30 条）\n")
    lines.append("| 轮次 | 活动 | 结果 | 时间 |")
    lines.append("|---|---|---|---|")
    for h in hist[-30:]:
        lines.append("| %s | %s | %s | %s |" % (h["cycle"], h["act"], h["res"], h["t"]))
    if not hist:
        lines.append("| - | - | 尚未开始 | - |")
    lines.append("")

    lines.append("## 各活动日志尾部\n")
    for name, path in SRC_LOGS:
        t = tail(path, 8)
        if not t:
            continue
        lines.append("### %s（`%s`）\n" % (name, path))
        lines.append("```")
        lines.extend(t)
        lines.append("```")
        lines.append("")

    nl = tail(NIGHT_LOG, 10)
    if nl:
        lines.append("## 循环日志尾部（`%s`）\n" % NIGHT_LOG)
        lines.append("```")
        lines.extend(nl)
        lines.append("```")

    return "\n".join(lines)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    while True:
        try:
            txt = build()
            with open(REPORT, "w", encoding="utf-8") as f:
                f.write(txt)
        except Exception:
            pass
        time.sleep(180)


if __name__ == "__main__":
    main()
