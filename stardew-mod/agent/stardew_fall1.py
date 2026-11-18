"""秋季第 1 天：清掉坏掉的菜 + 石头树杂草 → 在这块固定区域继续种。

流程：
  1. 睡到秋季第 1 天
  2. 回农场，清障（枯死作物/杂草/石头/树，都在 .plot_rect 区域内）
  3. 按季节选种并播种
  4. 存档 + 汇报
"""
import sys
import time

sys.path.insert(0, r'D:\live\deepseek-live\stardew-mod\agent')
import stardew_agent as A  # noqa: E402

LOG = r'D:\live\logs\fall1_run.log'
fh = open(LOG, 'a', encoding='utf-8')


def log(m):
    line = time.strftime('[%H:%M:%S] ') + str(m)
    print(line, flush=True)
    fh.write(line + '\n')
    fh.flush()


def st():
    return A.read_state()


def report(tag):
    s = st()
    pl = s.get('player') or {}
    t = [x for x in (s.get('tiles') or []) if x.get('crop')]
    dead = [x for x in t if '枯死' in str(x.get('obj') or '')]
    seeds = [x for x in (pl.get('inventory') or []) if 'Seed' in str(x)]
    log('  [%s] %s季第%s天 %s | 钱 %s | 作物 %d（熟 %d / 枯 %d）| 种子 %s'
        % (tag, s.get('season'), s.get('day'), s.get('time'), pl.get('money'),
           len(t), len([x for x in t if x.get('cropReady')]), len(dead), seeds))


def main():
    log('===== 秋 1 准备开始 =====')
    report('起始')
    log('固定农田区: %s' % (A.plot_rect(),))

    # 1) 睡到秋天
    for i in range(4):
        season, day = A.current_season_day()
        if season == 'fall':
            break
        log('  睡觉（当前 %s 第 %s 天）' % (A.SEASON_CN.get(season, season), day))
        A.go_home_sleep(log_fn=None)
        time.sleep(1)
    season, day = A.current_season_day()
    log('  现在 %s季第%s天' % (A.SEASON_CN.get(season, season), day))
    report('秋 1')

    # 2) 回农场清障
    if str(st().get('location')) != 'Farm':
        A.send_order({'actionType': 'warp_to', 'location': 'Farm'})
        time.sleep(6)
    log('--- 清障 ---')
    swings = A.clear_plot(log_fn=log)
    log('清障完成：%d 刀' % swings)
    report('清障后')

    # 3) 播种
    log('--- 播种 ---')
    n = A.plant_best_crops(132, log_fn=log)
    log('播种完成：%s 株' % n)
    report('播种后')

    A.send_order({'actionType': 'save'})
    time.sleep(6)
    log('已存档')


if __name__ == '__main__':
    main()
