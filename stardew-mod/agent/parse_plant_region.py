#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从存档文件离线解析当前农田播种情况（不需要启动游戏）。

输出：
  - 播种格子列表 + 作物种类
  - 每株作物剩余成熟天数
  - 保存固定播种区域到 D:\\live\\logs\\.plant_region.json
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

SAVE = os.path.join(os.environ["APPDATA"], r"StardewValley\Saves\MoonFarm_453567724\MoonFarm_453567724")
OUT_REGION = r"D:\live\logs\.plant_region.json"
OUT_REPORT = r"D:\live\logs\plant_region_report.md"

# 作物产出物品 id → 名称（常见的）
HARVEST_NAMES = {
    24: "防风草", 190: "花椰菜", 192: "土豆", 188: "绿豆", 250: "甘蓝菜",
    400: "草莓", 252: "大黄", 256: "西红柿", 257: "羊角豆", 258: "蓝莓",
    254: "甜瓜", 268: "杨桃", 266: "红叶卷心菜", 282: "蔓越莓", 276: "南瓜",
    264: "小萝卜", 262: "小麦", 260: "辣椒", 270: "玉米", 272: "茄子",
    300: "苋菜", 274: "洋蓟", 284: "甜菜", 278: "小白菜", 280: "山药",
    398: "葡萄", 304: "啤酒花", 302: "虞美人", 595: "玫瑰仙子", 421: "向日葵",
    591: "郁金香", 597: "蓝爵", 376: "罂粟", 90: "仙人掌果", 889: "菠萝",
    831: "芋头", 271: "未知道具",
}

# 作物成熟所需天数（基础，不含加速肥）
CROP_DAYS = {
    "防风草": 4, "大蒜": 4, "土豆": 6, "甘蓝菜": 6, "花椰菜": 12, "绿豆": 10,
    "蓝爵": 7, "郁金香": 6, "草莓": 8, "大黄": 13,
    "蓝莓": 13, "甜瓜": 12, "杨桃": 13, "红叶卷心菜": 9, "啤酒花": 11,
    "辣椒": 5, "西红柿": 11, "小萝卜": 6, "虞美人": 7, "小麦": 4, "玉米": 14,
    "蔓越莓": 7, "南瓜": 13, "洋蓟": 8, "甜菜": 6, "茄子": 5, "葡萄": 10,
    "苋菜": 7, "山药": 10, "小白菜": 4, "玫瑰仙子": 12,
}


def main():
    if not os.path.exists(SAVE):
        print("找不到存档:", SAVE)
        return 1
    s = open(SAVE, encoding="utf-8", errors="replace").read()
    print("存档 %.1f MB" % (len(s) / 1048576))

    day = int(re.search(r"<dayOfMonthForSaveGame>(\d+)", s).group(1))
    season_n = int(re.search(r"<seasonForSaveGame>(\d+)", s).group(1))
    year = int(re.search(r"<yearForSaveGame>(\d+)", s).group(1))
    season = ["spring", "summer", "fall", "winter"][season_n]
    season_cn = {"spring": "春", "summer": "夏", "fall": "秋", "winter": "冬"}[season]
    print("存档日期: 第 %d 年 %s季 第 %d 天" % (year, season_cn, day))

    # 定位 Farm
    farm_idx = s.find('xsi:type="Farm"')
    if farm_idx < 0:
        print("找不到 Farm 地图")
        return 1
    farm_seg = s[farm_idx:farm_idx + 3_000_000]

    # 解析 HoeDirt 里的作物（1.6 存档结构：key=Vector2，value=TerrainFeature xsi:type="HoeDirt"）
    pat = re.compile(
        r"<key><Vector2><X>(\d+)</X><Y>(\d+)</Y></Vector2></key>\s*"
        r"<value><TerrainFeature xsi:type=\"HoeDirt\">(.*?)</TerrainFeature>",
        re.S,
    )
    crops = []
    for m in pat.finditer(farm_seg):
        x, y, blk = int(m.group(1)), int(m.group(2)), m.group(3)
        if "<crop>" not in blk:
            continue
        harvest = re.search(r"<indexOfHarvest>(\d+)</indexOfHarvest>", blk)
        dead = re.search(r"<dead>true</dead>", blk) is not None
        fully = re.search(r"<fullGrown>true</fullGrown>", blk) is not None
        if not fully:
            fully = re.search(r"<fullyGrown>true</fullyGrown>", blk) is not None
        phases = [int(v) for v in re.findall(r"<int>(\d+)</int>", blk)]
        cur = re.search(r"<dayOfCurrentPhase>(\d+)</dayOfCurrentPhase>", blk)
        curd = int(cur.group(1)) if cur else 0
        hid = int(harvest.group(1)) if harvest else -1
        name = HARVEST_NAMES.get(hid, "id%d" % hid)
        crops.append({
            "x": x, "y": y, "crop": name, "harvestId": hid,
            "dead": dead, "fullyGrown": fully,
            "phaseDays": phases, "dayOfCurrentPhase": curd,
        })

    print("农场作物格: %d" % len(crops))

    # 剩余天数估算
    by_tile = {}
    for c in crops:
        # 存档 phaseDays 末尾有 99999 哨兵值，必须排除
        phases = [p2 for p2 in c["phaseDays"] if p2 < 99999]
        curd = c["dayOfCurrentPhase"]
        passed = sum(phases[:curd]) + curd
        total = CROP_DAYS.get(c["crop"], sum(phases))
        left = 0 if (c["fullyGrown"] or c["dead"]) else max(0, total - passed)
        c["daysToMature"] = total
        c["daysLeft"] = left
        c["passedDays"] = passed
        by_tile[(c["x"], c["y"])] = c

    alive = [c for c in crops if not c["dead"]]
    dead = [c for c in crops if c["dead"]]
    print("  存活 %d 株 | 枯死 %d 株" % (len(alive), len(dead)))
    print("  作物种类:", dict(Counter(c["crop"] for c in crops)))

    if crops:
        xs = [c["x"] for c in crops]
        ys = [c["y"] for c in crops]
        print("  范围: x %d~%d, y %d~%d" % (min(xs), max(xs), min(ys), max(ys)))

    # 剩余天数分布
    left_dist = Counter(c["daysLeft"] for c in alive)
    print("  剩余收获天数分布:", dict(sorted(left_dist.items())))

    # 保存固定区域
    region = {
        "savedAt": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "saveDay": day, "season": season, "year": year,
        "tiles": [{"x": c["x"], "y": c["y"], "crop": c["crop"]} for c in alive],
        "deadTiles": [{"x": c["x"], "y": c["y"]} for c in dead],
    }
    os.makedirs(os.path.dirname(OUT_REGION), exist_ok=True)
    with open(OUT_REGION, "w", encoding="utf-8") as f:
        json.dump(region, f, ensure_ascii=False, indent=1)
    print("固定播种区域已保存:", OUT_REGION)

    # 生成报告
    lines = ["# 农田现状（离线解析存档）", "",
             "存档日期：第%d年 %s季 第%d天" % (year, season_cn, day), "",
             "## 作物汇总", "", "| 作物 | 数量 | 成熟天数 | 剩余天数范围 |", "|---|---|---|---|"]
    g = defaultdict(list)
    for c in alive:
        g[c["crop"]].append(c["daysLeft"])
    for name, ds in sorted(g.items(), key=lambda kv: -len(kv[1])):
        lines.append("| %s | %d | %d 天 | %d~%d |" % (name, len(ds), CROP_DAYS.get(name, 0), min(ds), max(ds)))
    if dead:
        lines += ["", "## 枯死作物（%d 株，需要清掉）" % len(dead), ""]
        for c in dead[:40]:
            lines.append("- (%d,%d) %s" % (c["x"], c["y"], c["crop"]))
    lines += ["", "## 稳定播种区域（后续都在这个范围种）", ""]
    if crops:
        lines.append("x: %d ~ %d，y: %d ~ %d" % (min(xs), max(xs), min(ys), max(ys)))
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("报告已写入:", OUT_REPORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
