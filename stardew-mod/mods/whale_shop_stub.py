#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WhaleShop 模组的**测试替身**：不装游戏也能验证 Agent ↔ mod 的订单协议。

它盯着 orders 目录，看到 buy_seed 订单就假装"买成功"，写 result 文件。
用法：
  python whale_shop_stub.py --dir "D:\\live\\stardew-test\\WhaleShop\\orders"
"""
import argparse
import json
import os
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--money", type=int, default=99999)
    a = ap.parse_args()
    os.makedirs(a.dir, exist_ok=True)
    print(f"[stub] 盯着 {a.dir}，余额 {a.money}g", flush=True)
    money = a.money
    seen = set()
    while True:
        for name in sorted(os.listdir(a.dir)):
            if not name.endswith(".json") or name.endswith(".result.json") or name in seen:
                continue
            seen.add(name)
            path = os.path.join(a.dir, name)
            try:
                with open(path, encoding="utf-8") as f:
                    order = json.load(f)
            except Exception:
                continue
            item = order.get("item") or order.get("itemId") or "Unknown"
            count = int(order.get("count") or 1)
            unit = int(order.get("price") or 100)
            cost = unit * count
            if money < cost:
                res = {"ok": False, "error": "钱不够", "need": cost, "money": money}
            else:
                money -= cost
                res = {"ok": True, "itemId": "stub", "name": f"{item} Seeds",
                       "count": count, "unit": unit, "cost": cost, "money": money}
            res["ts"] = int(time.time() * 1000)
            out = os.path.join(a.dir, name[:-5] + ".result.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False)
            print(f"[stub] {name} -> {res}", flush=True)
        time.sleep(0.2)


if __name__ == "__main__":
    main()
