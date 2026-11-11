"""种地流程：割草 → 锄地 → 播种 → 浇水 → 收获 → 卖作物。

用法：
    import farm
    farm.do_farm(max_plant=8)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fallback
import nav2

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "farm.log")
# 主要种植区（房子右边的大片草地）
FIELD = [(x, y) for y in range(18, 25) for x in range(56, 73)]
KEEP_SEED_NAMES = ["防风草种子", "土豆种子", "花椰菜种子", "蓝莓种子", "小麦种子"]


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


def tiles():
    return (rd().get("surroundings") or {}).get("Tiles") or []


def inv():
    return [i for i in (rd().get("player") or {}).get("inventory") or [] if i]


def count(name):
    return sum(int(i.get("stack", 1)) for i in inv() if name in str(i.get("name")))


def tile_at(x, y):
    return next((t for t in tiles() if t["X"] == x and t["Y"] == y), None)


def stand_near(x, y):
    b = rd() or {}
    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        t = next((s for s in (b.get("surroundings") or {}).get("Tiles") or []
                  if s["X"] == x + dx and s["Y"] == y + dy and s.get("Passable")), None)
        if t:
            return t
    return None


TOOL_EN = {"锄头": "hoe", "喷壶": "watering can", "斧头": "axe", "十字镐": "pickaxe", "镰刀": "scythe"}


def use_tool_at(tool, x, y):
    st = stand_near(x, y)
    if st:
        nav2.walk_until(st["X"], st["Y"], 10)
    nav2.send({"actionType": "player_face_tile", "x": x, "y": y}, 0.2)
    nav2.send({"actionType": "player_use_tool", "tool": TOOL_EN.get(tool, tool), "x": x, "y": y}, 0.9)
    time.sleep(0.4)


def harvest():
    got = 0
    for t in tiles():
        if not t.get("CropReady"):
            continue
        st = stand_near(t["X"], t["Y"])
        if not st:
            continue
        nav2.walk_until(st["X"], st["Y"], 10)
        nav2.send({"actionType": "player_interact_at", "x": t["X"], "y": t["Y"]}, 0.8)
        time.sleep(0.3)
        got += 1
    if got:
        log(f"  收获 {got} 个作物")
    return got


def water():
    b = rd() or {}
    can = int((b.get("player") or {}).get("wateringCanLeft") or 0)
    dry = [t for t in tiles() if t.get("Terrain") == "hoeDirt" and not t.get("Watered")
           and t.get("CropName")]
    if not dry:
        return 0
    log(f"  需要浇水 {len(dry)} 格，水壶剩 {can}")
    watered = 0
    for t in dry[:40]:
        if can <= 0:
            # 去水边打水
            w = next((s for s in tiles() if s.get("IsWater")), None)
            if w and stand_near(w["X"], w["Y"]):
                use_tool_at("喷壶", w["X"], w["Y"])
                can = int((rd().get("player") or {}).get("wateringCanLeft") or 0)
                log(f"  重新打水 -> {can}")
        use_tool_at("喷壶", t["X"], t["Y"])
        watered += 1
        can -= 1
    log(f"  浇水 {watered} 格")
    return watered


def clear_grass_near(x, y):
    st = stand_near(x, y)
    if st:
        nav2.walk_until(st["X"], st["Y"], 8)
    nav2.send({"actionType": "select_item", "name": "镰刀"}, 0.2)
    nav2.send({"actionType": "player_face_tile", "x": x, "y": y}, 0.2)
    try:
        nav2.click_tile(x, y, 0.1)
    except Exception:
        nav2.send({"actionType": "player_use_tool", "tool": "scythe", "x": x, "y": y}, 0.6)
    time.sleep(0.25)


PLOT = [(x, y) for y in range(19, 23) for x in range(62, 67)]


def plant(max_plant=6):
    b = rd() or {}
    if str(b.get("location")) != "Farm":
        return 0
    seed = next((n for n in KEEP_SEED_NAMES if count(n) > 0), None)
    if not seed:
        log("  没有种子（需要买）")
        return 0
    nav2.walk_until(64, 23, 15)
    time.sleep(0.6)
    planted = 0
    for (x, y) in PLOT:
        if planted >= max_plant or count(seed) <= 0:
            break
        t = tile_at(x, y)
        if not t:
            continue
        if t.get("IsWater") or t.get("ObjectName") or t.get("CropName"):
            continue
        if not stand_near(x, y):
            continue
        if str(t.get("Terrain") or "") == "grass":
            clear_grass_near(x, y)
            time.sleep(0.8)
            t = tile_at(x, y) or t
        if str(t.get("Terrain") or "") in ("grass", ""):
            use_tool_at("锄头", x, y)
            time.sleep(1.0)
            t = tile_at(x, y)
        if not t or t.get("Terrain") != "hoeDirt":
            continue
        nav2.send({"actionType": "plant_seed", "name": seed, "x": x, "y": y}, 1.0)
        time.sleep(0.5)
        t2 = tile_at(x, y)
        if t2 and t2.get("CropName"):
            planted += 1
    if planted:
        log(f"  播种 {planted} 格（{seed}）")
    return planted


SEASON_SEEDS = {
    "spring": ["防风草种子", "土豆种子", "花椰菜种子"],
    "summer": ["小麦种子", "蓝莓种子", "甜瓜种子"],
    "fall": ["小麦种子", "南瓜种子", "玉米种子"],
}


def buy_seeds(season, want=15):
    """到 Pierre 买当季种子（buy_direct：扣钱 + 入包）。"""
    names = SEASON_SEEDS.get(str(season), [])
    if not names:
        return 0
    have = sum(count(n) for n in names)
    if have >= want:
        return have
    if (rd().get("player") or {}).get("money", 0) < 120:
        log(f"  钱不够买种子")
        return have
    nav2.send({"actionType": "auto_sell", "shopId": "SeedShop", "name": "不存在", "keep": 999999}, 2.0)
    time.sleep(0.3)
    name = names[0]
    need = want - have
    while need > 0 and (rd().get("player") or {}).get("money", 0) >= 120:
        n = min(10, need)
        nav2.send({"actionType": "buy_direct", "name": name, "count": n}, 1.8)
        time.sleep(0.3)
        if count(name) <= have:
            break
        need = want - count(name)
    nav2.send({"actionType": "exit_menu"}, 0.6)
    log(f"  买种子 {name}: 现有 {count(name)} 金币 {((rd().get('player') or {}).get('money'))}")
    return count(name)


def do_farm(max_plant=8):
    b = rd() or {}
    if str(b.get("location")) != "Farm":
        fallback.goto_or_warp("Farm")
    if str(rd().get("season")) == "winter":
        log("  冬天不种地")
        return
    buy_seeds(rd().get("season"), 15)
    harvest()
    water()
    plant(max_plant)
    sell_crops()


def sell_crops():
    try:
        import sell
        for crop in ("防风草", "土豆", "花椰菜", "蓝莓", "小麦"):
            if count(crop) >= 3:
                sell.sell_item(crop, 0)
    except Exception as ex:
        log(f"  卖作物失败: {ex}")


if __name__ == "__main__":
    log("=== 手动种地测试 ===")
    print("inv", [i["name"] for i in inv()])
    harvest()
    water()
    plant(5)
    print("done")
