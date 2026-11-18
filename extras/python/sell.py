"""卖东西换金币：打开商店 → 真实鼠标点击背包物品格 → 成交。

已验证：Robin(木匠) 收石头/木头；点击后金币立即到账。
用法：
    import sell
    sell.sell_surplus("石头", keep=999)
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gameio
import nav2

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "sell.log")
SLOTS = r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge\shop_slots.txt"
# 各商店收什么：shopId -> 可卖物品
SHOPS = {
    "Carpenter": ["石头", "木材", "粘土", "硬木"],
    "Blacksmith": ["铜矿石", "铁矿石", "煤炭"],
    "SeedShop": ["防风草", "土豆", "花椰菜", "蓝莓", "小麦", "甜瓜", "西红柿", "辣椒", "萝卜", "虞美人"],
}


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def rd():
    return nav2.rd() or {}


def inv():
    return [i for i in (rd().get("player") or {}).get("inventory") or [] if i]


def count(name):
    return sum(int(i.get("stack", 1)) for i in inv() if name in str(i.get("name")))


def chest_count(name):
    return sum(int(e.get("stack", 0)) for e in rd().get("chestTotals") or [] if name in str(e.get("name")))


def _click_slots(h, r, lines):
    """按 shop_slots.txt 的格子坐标真实点击卖出。返回卖出后金额。"""
    money0 = (rd().get("player") or {}).get("money", 0)
    sold = 0
    for ln in lines:
        m = re.search(r"\{X:(\d+) Y:(\d+) Width:(\d+) Height:(\d+)\}", ln)
        if not m:
            continue
        x, y, w, ht = map(int, m.groups())
        gameio.click(h, r, x + w // 2, y + ht // 2)
        time.sleep(0.7)
        money1 = (rd().get("player") or {}).get("money", 0)
        if money1 > money0:
            sold += 1
            money0 = money1
        else:
            break
    return sold


def sell_item(name, keep=0):
    """卖掉超出 keep 的部分（按整叠卖，留 1 叠 ≈ keep）。"""
    backed = 0
    # 背包不够就把箱子里的取出来
    if count(name) <= keep and chest_count(name) > keep:
        try:
            import boxes
            need = min(999, chest_count(name) - keep)
            backed = boxes.take_from_chest(name, need)
            log(f"  从箱子取 {name} {backed}")
        except Exception as ex:
            log(f"  取货失败: {ex}")
    total = count(name)
    if total <= keep:
        log(f"  {name} 不够卖（背包 {total}）")
        return 0
    # 找能收这件东西的商店
    shop = None
    for sid, items in SHOPS.items():
        if any(i in name or name in i for i in items):
            shop = sid
            break
    if shop is None:
        log(f"  {name} 没有对应的商店")
        return 0
    # 打开商店并生成槽位文件
    nav2.send({"actionType": "auto_sell", "shopId": shop, "name": name, "keep": 999999}, 2.2)
    time.sleep(0.4)
    if not os.path.exists(SLOTS):
        log(f"  {name}: 没生成槽位文件")
        return 0
    lines = [ln.strip() for ln in open(SLOTS, encoding="utf-8").read().splitlines() if ln.strip()]
    if not lines:
        log(f"  {name}: 商店不收或没有可卖格子")
        return 0
    if keep > 0 and len(lines) > 1:
        # 留第一叠（≈999），其余全卖
        lines = lines[1:]
    h, r = gameio.game_window()
    if not h:
        log("  没有游戏窗口")
        return 0
    sold = _click_slots(h, r, lines)
    nav2.send({"actionType": "exit_menu"}, 0.6)
    money = (rd().get("player") or {}).get("money", 0)
    log(f"  卖 {name}: 点掉 {sold} 叠，金币 -> {money}")
    return sold


def sell_surplus(stone_keep=999, wood_keep=999):
    """卖石头/木材，各留 ~900（一叠 999）。"""
    total = 0
    total += sell_item("石头", stone_keep)
    total += sell_item("木材", wood_keep)
    return total


if __name__ == "__main__":
    log("=== 手动卖东西测试 ===")
    print("stone", count("石头"), "wood", count("木材"), "chest stone", chest_count("石头"))
    print("sold", sell_surplus())
    print("money", (rd().get("player") or {}).get("money"))
