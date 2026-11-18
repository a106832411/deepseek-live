#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四季播种测试：冬天 → 春天 → 夏天 → 秋天，每季各跑一次 AI 播种。

自然过季（睡觉推进），每季记录：
  - 进季时/播种后的作物数、成熟数、枯死数、钱、背包种子
  - plant_best_crops 的返回值和日志
问题会以 !! 开头写进日志。
"""
import sys
import time

sys.path.insert(0, r'D:\live\deepseek-live\stardew-mod\agent')
import stardew_agent as A  # noqa: E402

LOG = r'D:\live\logs\season_run.log'
fh = open(LOG, 'a', encoding='utf-8')


def log(m):
    line = time.strftime('[%H:%M:%S] ') + str(m)
    print(line, flush=True)
    fh.write(line + '\n')
    fh.flush()


def st():
    return A.read_state()


def season_key():
    """当前季节的英文键（spring/summer/fall/winter）。"""
    return A.current_season_day()[0]


def wait_for_season(target, max_sleeps=34):
    for i in range(max_sleeps):
        s = st()
        if season_key() == target:
            log('  已到 %s 季 第 %s 天' % (s.get('season'), s.get('day')))
            return True
        if i == 0:
            log('  当前 %s 季第 %s 天 → 开始睡到 %s' % (s.get('season'), s.get('day'), target))
        try:
            A.go_home_sleep(log_fn=None)
        except Exception as e:
            log('  !! 睡觉异常 %s' % e)
        time.sleep(1)
        if i % 7 == 6:
            s = st()
            log('  …%s 季第 %s 天 %s' % (s.get('season'), s.get('day'), s.get('time')))
    return season_key() == target


def report(tag):
    s = st()
    pl = s.get('player') or {}
    tiles = [t for t in (s.get('tiles') or [])]
    crops = [t for t in tiles if t.get('crop')]
    dead = [t for t in crops if str(t.get('obj') or '').find('枯死') >= 0]
    ready = [t for t in crops if t.get('cropReady')]
    seeds = [x for x in (pl.get('inventory') or []) if 'Seed' in str(x) or '种子' in str(x)]
    log('  [%s] %s季第%s天 %s | 钱 %s | 作物 %d（熟 %d / 枯 %d）| 背包种子 %s'
        % (tag, s.get('season'), s.get('day'), s.get('time'), pl.get('money'),
           len(crops), len(ready), len(dead), seeds))
    return len(crops)


def run_season(target):
    log('===== 目标：%s 季 =====' % target)
    if not wait_for_season(target):
        log('  !! 等不到 %s 季，跳过' % target)
        return
    try:
        A.send_order({'actionType': 'close_menu'})
        time.sleep(1)
        A.send_order({'actionType': 'warp_to', 'location': 'Farm'})
        time.sleep(5)
    except Exception as e:
        log('  !! 去农田异常 %s' % e)
    report(target + ' 播种前')
    n = -1
    try:
        n = A.plant_best_crops(20, log_fn=log)
    except Exception as e:
        log('  !! 播种异常：%s' % e)
    report(target + ' 播种后')
    log('  结果：%s 季播种 %s 株' % (target, n))
    try:
        A.send_order({'actionType': 'save'})
        time.sleep(5)
    except Exception:
        pass


def main():
    log('########## 四季播种测试开始 ##########')
    for t in ('spring', 'summer', 'fall', 'winter'):
        run_season(t)
    log('########## 四季播种测试结束 ##########')


if __name__ == '__main__':
    main()
