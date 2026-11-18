"""养到收成 → 收割（含补漏）。用 skip_days 批量跳天，不再一天一条命令。"""
import sys
import time

sys.path.insert(0, r'D:\live\deepseek-live\stardew-mod\agent')
import stardew_agent as A  # noqa: E402

LOG = r'D:\live\logs\grow_harvest.log'
fh = open(LOG, 'a', encoding='utf-8')


def log(m):
    line = time.strftime('[%H:%M:%S] ') + str(m)
    print(line, flush=True)
    fh.write(line + '\n')
    fh.flush()


def report(tag):
    s = A.read_state()
    pl = s.get('player') or {}
    t = [x for x in (s.get('tiles') or []) if x.get('crop')]
    log('  [%s] %s季第%s天 %s | 作物 %d（熟 %d）| 钱 %s | 背包 %d 格'
        % (tag, s.get('season'), s.get('day'), s.get('time'), len(t),
           A.count_ready(s), pl.get('money'), len(pl.get('inventory') or [])))


def main():
    log('===== 养到收成开始 =====')
    if str(A.read_state().get('location')) != 'Farm':
        A.send_order({'actionType': 'warp_to', 'location': 'Farm'})
        time.sleep(6)
    report('起始')
    log('  排期建议: %s' % A.plant_window_advice())

    t0 = time.time()
    jumped = A.skip_until_ready(max_days=16, log_fn=log)
    log('  跳天 %d 天，用时 %.0f 秒' % (jumped, time.time() - t0))
    report('跳天后')

    got = A.harvest_with_scythe(log_fn=log)
    log('  收割 %d 株' % got)
    report('收割后')

    A.send_order({'actionType': 'save'})
    time.sleep(6)
    log('===== 已存档，结束 =====')


if __name__ == '__main__':
    main()
