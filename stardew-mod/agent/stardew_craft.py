"""制作：刷新配方表，按优先级把材料够的做出来。用法：--times 1"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, r"D:\live\deepseek-live\stardew-mod\agent")
import stardew_agent as A  # noqa: E402

RECIPES = r"D:\steam\steamapps\common\Stardew Valley\Mods\WhalePlayer\recipes.json"

# 有用的制作优先级（名字是游戏内部配方名）
PRIORITY = [
    "Furnace", "Chest", "Scarecrow", "Sprinkler", "Quality Sprinkler",
    "Tapper", "Preserves Jar", "Keg", "Bee House", "Lightning Rod",
    "Charcoal Kiln", "Basic Fertilizer", "Quality Fertilizer", "Torch",
    "Stone Floor", "Wood Floor", "Garden Pot",
]
# 没米下锅时也别做的：纯装饰/低价值
SKIP = {"Wood Fence", "Stone Fence", "Hardwood Fence", "Gate", "Wood Sign",
        "Stone Sign", "Text Sign", "Wood Path", "Gravel Path", "Cobblestone Path",
        "Field Snack"}


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + str(msg), flush=True)


def refresh():
    A.send_order({"actionType": "recipes"})
    time.sleep(2.0)


def load():
    with open(RECIPES, encoding="utf-8-sig") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--craft", default="3", help="每个配方最多做几个")
    a = ap.parse_args()
    max_each = int(a.craft)
    log("===== 制作开始 =====")
    refresh()
    rows = load()
    by = {r["name"]: r for r in rows}
    made = 0
    for name in PRIORITY:
        r = by.get(name)
        if not r:
            continue
        if not r.get("learned"):
            # 没学过但有材料 → 先学（用户要的「学配方」）
            if r.get("have"):
                A.send_order({"actionType": "learn_recipe", "name": name})
                time.sleep(1.0)
                log(f"  学了配方：{name}")
            else:
                log(f"  {name}: 没学会且材料不够，跳过")
                continue
        if not r.get("have"):
            continue
        n = 0
        for _ in range(max_each):
            A.send_order({"actionType": "craft", "item": name, "count": 1})
            time.sleep(1.0)
            n += 1
            if not wait_craft(name):
                break
        if n:
            made += n
            log(f"  做了 {n} × {name}")
    log(f"===== 制作结束，共 {made} 件 =====")
    s = A.summarize(A.read_state())
    log(f"  背包 {len(s.get('inventory') or [])} 格")


def wait_craft(name):
    """简单确认：材料是否还够（不够了说明做成功了）。"""
    try:
        A.send_order({"actionType": "recipes"})
        time.sleep(1.2)
        rows = load()
        r = next((x for x in rows if x["name"] == name), None)
        return bool(r and r.get("have"))
    except Exception:
        return True


if __name__ == "__main__":
    main()
