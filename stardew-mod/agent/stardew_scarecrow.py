"""补稻草人：按作物范围算覆盖，缺就取材料做出来摆上。用法：--dry 只看不下手"""
import argparse
import sys
import time

sys.path.insert(0, r"D:\live\deepseek-live\stardew-mod\agent")
import stardew_agent as A  # noqa: E402

SCARECROW = "Scarecrow"
WOOD, FIBER, COAL = 50, 20, 1
SPACING = 12          # 稻草人半径 8，隔 12 格摆一个基本无缝


def log(m):
    print(time.strftime("[%H:%M:%S] ") + str(m), flush=True)


def state():
    return A.read_state()


def inventory(s):
    return (s.get("player") or {}).get("inventory") or []


def count_inv(s, name):
    total = 0
    for x in inventory(s):
        if name.lower() in str(x).lower():
            try:
                total += int(str(x).split("x")[-1]) if "x" in str(x) else 1
            except Exception:
                total += 1
    return total


def placed_scarecrows(s):
    return [(t["x"], t["y"]) for t in (s.get("tiles") or []) if "稻草人" in str(t.get("obj"))]


def crop_bbox(s):
    crops = [t for t in (s.get("tiles") or []) if t.get("crop")]
    if not crops:
        return None
    xs = [t["x"] for t in crops]
    ys = [t["y"] for t in crops]
    return min(xs), max(xs), min(ys), max(ys), len(crops)


def target_points(bbox):
    minx, maxx, miny, maxy, _ = bbox
    pts = []
    y = miny + 2
    while y <= maxy:
        x = minx + 2
        while x <= maxx:
            pts.append((x, y))
            x += SPACING
        y += SPACING
    return pts


def free_tile_near(s, x, y):
    occupied = {(t["x"], t["y"]) for t in (s.get("tiles") or [])
                if t.get("obj") or t.get("crop") or t.get("interactable")}
    for r in range(0, 4):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                cand = (x + dx, y + dy)
                if cand not in occupied:
                    return cand
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    s = state()
    if str(s.get("location")) != "Farm":
        A.send_order({"actionType": "goto", "location": "Farm"})
        time.sleep(6)
        s = state()
    bbox = crop_bbox(s)
    if not bbox:
        log("没有作物，不用稻草人")
        return
    need_pts = target_points(bbox)
    have = placed_scarecrows(s)
    log(f"作物 {bbox[4]} 株，范围 x{bbox[0]}~{bbox[1]} y{bbox[2]}~{bbox[3]}")
    log(f"已放稻草人 {len(have)} 个：{have}；目标点位 {need_pts}")
    # 粗略覆盖判断：目标点 12 格内有稻草人算覆盖
    uncovered = [p for p in need_pts
                 if not any(abs(p[0] - q[0]) <= SPACING and abs(p[1] - q[1]) <= SPACING for q in have)]
    if not uncovered:
        log("覆盖够了")
        return
    k = len(uncovered)
    log(f"需要补 {k} 个")
    if a.dry:
        return
    # 取材料（箱子里的也算）
    inv = inventory(s)
    def have_inv(nm):
        return count_inv(s, nm)
    if have_inv("Wood") < WOOD * k:
        A.send_order({"actionType": "withdraw_name", "name": "Wood", "count": WOOD * k})
        time.sleep(1.5)
    if have_inv("Fiber") < FIBER * k:
        A.send_order({"actionType": "withdraw_name", "name": "Fiber", "count": FIBER * k})
        time.sleep(1.5)
    if have_inv("Coal") < COAL * k:
        A.send_order({"actionType": "withdraw_name", "name": "Coal", "count": COAL * k})
        time.sleep(1.5)
    for i in range(k):
        A.send_order({"actionType": "craft", "item": SCARECROW, "count": 1})
        time.sleep(1.2)
    s = state()
    log(f"背包稻草人: {count_inv(s, 'Scarecrow')}")
    for p in uncovered:
        spot = free_tile_near(s, p[0], p[1])
        if not spot:
            log(f"  {p} 附近没空地，跳过")
            continue
        A.send_order({"actionType": "walk_to", "x": spot[0], "y": spot[1] + 1})
        time.sleep(1.2)
        A.send_order({"actionType": "place_item", "name": "Scarecrow", "x": spot[0], "y": spot[1]})
        time.sleep(1.0)
        log(f"  放到 {spot}")
        s = state()
    log(f"完成，现在稻草人：{placed_scarecrows(state())}")


if __name__ == "__main__":
    main()
