#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通宵循环：种植 → 挖矿/钓鱼 → 回家整理 → 睡觉 → 下一天。

时间分配（每游戏日）：
  06:00-09:00  出门整理背包 → 农场（1 号清障）→ 收割 → 补种
  09:00-15:00  有剩余时间 → 挖矿（晴天） / 钓鱼（雨天）
  15:00-21:00  继续钓鱼 / 挖矿
  21:00-23:00  回家 → 整理背包 → 睡觉（不等 2:00）
出错只记录并自愈（unstick / wake / close_menu），不中断整夜。
"""
import sys
import time
import traceback

sys.path.insert(0, r'D:\live\deepseek-live\stardew-mod\agent')
import stardew_agent as A  # noqa: E402

LOG = r'D:\live\logs\overnight.log'
fh = open(LOG, 'a', encoding='utf-8')
STATS = {'days': 0, 'harvested': 0, 'planted': 0, 'errors': 0, 'fished': 0, 'mined': 0}


def log(m):
    line = time.strftime('%m-%d %H:%M:%S ') + str(m)
    # 控制台是 GBK，子进程吐出的 UTF-8 替换字符会让 print 抛异常，
    # 这里吞掉异常，日志文件照写不误（2026-11-18 修：以前会误报“活动异常”）
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        fh.write(line + '\n')
        fh.flush()
    except Exception:
        pass


def state():
    return A.read_state()


def snap(tag=""):
    s = state()
    pl = s.get('player') or {}
    tiles = [t for t in (s.get('tiles') or []) if t.get('crop')]
    log("  [%s] %s季第%s天 %s | %s | 作物 %d（熟 %d）| 钱 %s | 背包 %d 格 | 体力 %s"
        % (tag, s.get('season'), s.get('day'), s.get('time'), s.get('location'),
           len(tiles), A.count_ready(s), pl.get('money'), len(pl.get('inventory') or []),
           pl.get('stamina')))
    return s


def recover(why):
    log("  !! 自愈（%s）" % why)
    STATS['errors'] += 1
    for order in ({'actionType': 'close_menu'}, {'actionType': 'unstick'},
                  {'actionType': 'wake'}, {'actionType': 'close_menu'}):
        try:
            A.send_order(order)
            time.sleep(1.2)
        except Exception:
            pass


def morning():
    s = snap('起床')
    A.set_activity('出门前整理背包')
    # 先收机器产物（果酱/果酒/金属锭）
    try:
        A.send_order({"actionType": "collect_machines"})
        time.sleep(2)
    except Exception:
        pass
    # 工具兜底：镰刀丢了就补一把（1.6 id 66 = 铱镰刀）
    try:
        inv = [str(x) for x in ((state().get('player') or {}).get('inventory') or [])]
        if not any('Scythe' in x or '镰刀' in x for x in inv):
            log("  !! 背包里没有镰刀，补一把")
            A.send_order({'actionType': 'give_scythe'})
            time.sleep(2)
    except Exception as e:
        log("  !! 检查镰刀异常 %s" % e)
    try:
        A.go_out_tidy(log_fn=log)          # 出门整理：非工具收进箱子，只留 5 件工具
    except Exception as e:
        log("  !! 整理背包异常 %s" % e)
    # 换季第 1 天：补三季收益最高的种子，并清理农田
    try:
        season, day = A.current_season_day()
        if int(day) == 1:
            A.give_top_seeds(log_fn=log)
    except Exception as e:
        log("  !! 补种子异常 %s" % e)
    try:
        A.season_day_one_cleanup(log_fn=log)
    except Exception as e:
        log("  !! 换季清理异常 %s" % e)
    # 农田：按「记忆」决定今天要不要查（没到成熟日就不去折腾）
    due, wait = A.farm_due(log_fn=log)
    if not due:
        log("  今天不查田（还要等 %d 天）" % wait)
    else:
        A.set_activity('收割与补种')
        try:
            got, planted = A.harvest_and_replant(log_fn=log)
            STATS['harvested'] += got
            STATS['planted'] += planted
            # 记下下次该几天后再查：新种下按成熟天数；没新种（连收）按连收间隔，但最多 3 天，
            # 避免估错导致熟了的作物干等好几天
            season, day = A.current_season_day()
            s = A.read_state()
            crops = [t.get('crop') for t in (s.get('tiles') or []) if t.get('crop')]
            label = crops[0] if crops else "空地"
            if planted > 0 and crops:
                days, regrow = A.crop_meta(label, season)
                step = max(1, int(days))
            else:
                days, regrow = A.crop_meta(label, season) if crops else (1, 0)
                step = max(1, int(regrow or days))
                step = min(step, 3)
            nxt = min(28, int(day) + step)
            A.save_memory(season=season, next_check_day=nxt, crop=label)
            log("  [农田记忆] %s → 第 %d 天再来（%d 天后）" % (label, nxt, step))
        except Exception as e:
            log("  !! 收割/补种异常 %s" % e)
            traceback.print_exc()


def afternoon(until="20:00"):
    try:
        s = state()
        if A._mins(s.get('time')) >= A._mins(until):
            log("  [白天] 时间已过 %s，直接回家" % until)
            return
        A.set_activity('钓鱼/挖矿')
        before = len([x for x in ((s.get('player') or {}).get('inventory') or [])])
        A.spare_time_activity(until=until, log_fn=log)
        s2 = state()
        pl2 = s2.get('player') or {}
        if str(s2.get('location')) == 'Beach':
            STATS['fished'] += 1
        else:
            STATS['mined'] += 1
        log("  [白天] 活动结束：%s %s | 背包 %d 格 | 钱 %s"
            % (s2.get('location'), s2.get('time'), len(pl2.get('inventory') or []), pl2.get('money')))
    except Exception as e:
        log("  !! 白天活动异常 %s" % e)


def night():
    A.set_activity('回家整理睡觉')
    # 挖矿后可能还在地下，先出矿再回家（否则 travel_to 会在矿里卡死）
    try:
        A.return_from_mine(log_fn=log)
    except Exception as e:
        log("  !! 出矿异常 %s" % e)
    # 把作物送进出货箱（隔夜结算，第二天早上钱才涨）
    try:
        A.ship_surplus(log_fn=log)
    except Exception as e:
        log("  !! 出货异常 %s" % e)
    try:
        A.go_out_tidy(log_fn=log)
    except Exception:
        pass
    s0 = state()
    d0 = s0.get('day')
    ok = A.go_home_sleep(log_fn=log)
    for _ in range(30):
        time.sleep(2)
        s = state()
        if s.get('day') != d0:
            break
    s = state()
    if s.get('day') == d0:
        recover('睡觉没换天')
        A.go_home_sleep(log_fn=log)
        time.sleep(15)
    s = state()
    STATS['days'] += 1
    log("  [过夜] 第 %s 天完成 → 现在 %s季第%s天 %s | 累计收 %d 种 %d | 错误 %d"
        % (d0, s.get('season'), s.get('day'), s.get('time'),
           STATS['harvested'], STATS['planted'], STATS['errors']))


def main():
    max_days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    log("========== 通宵循环启动（最多 %d 天）==========" % max_days)
    s = snap('开始')
    for i in range(max_days):
        try:
            morning()
            afternoon(until="20:00")
            night()
        except Exception as e:
            log("  !! 第 %d 轮顶层异常 %s" % (i + 1, e))
            traceback.print_exc()
            recover('顶层异常')
            time.sleep(3)
    log("========== 通宵循环结束：%s ==========" % STATS)


if __name__ == '__main__':
    main()
