#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性验证：走去皮埃尔商店买种子（看得见走路 + 进店 + 买）。

用法：python stardew_buyseed_test.py [种子英文名] [数量]
日志：D:\\live\\logs\\buyseed_test.log
"""
import sys
import time

sys.path.insert(0, r"D:\live\deepseek-live\stardew-mod\agent")
import stardew_agent as A  # noqa: E402

LOG = r"D:\live\logs\buyseed_test.log"
fh = open(LOG, "a", encoding="utf-8")


def log(m):
    line = time.strftime("%m-%d %H:%M:%S ") + str(m)
    print(line, flush=True)
    fh.write(line + "\n")
    fh.flush()


def main():
    seed = sys.argv[1] if len(sys.argv) > 1 else "Red Cabbage Seeds"
    need = int(sys.argv[2]) if len(sys.argv) > 2 else 80
    log("===== 买种子实测开始：%s × %d =====" % (seed, need))
    s = A.summarize(A.read_state())
    log("现状：%s第%s天 %s %s｜钱 %s｜背包 %s"
        % (s.get("season"), s.get("day"), s.get("time"), s.get("location"),
           s.get("money"), s.get("inventory")))
    ok_shop, reasons, wd = A.shop_state()
    log("商店状态：可买=%s %s（%s）" % (ok_shop, reasons, wd))
    got = A.ensure_seeds_buy(seed, need, log_fn=log)
    log("结果：买完后背包里 %s 有 %d 个（需要 %d）" % (seed, got, need))
    s = A.summarize(A.read_state())
    log("结束：%s %s｜钱 %s｜背包 %s"
        % (s.get("time"), s.get("location"), s.get("money"), s.get("inventory")))
    log("===== 买种子实测结束 =====")


if __name__ == "__main__":
    main()
