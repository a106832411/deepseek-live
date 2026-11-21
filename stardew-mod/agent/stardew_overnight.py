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
DAY_START = {'season': None, 'day': None}


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
    # 先收机器产物（果酱/果酒/金属锭）：走过去一台台收，不做远程一键收
    try:
        A.collect_machines_walk(log_fn=log)
    except Exception as e:
        log("  !! 收机器异常 %s" % e)
    # 镰刀检查（2026-11-21）：镰刀被地图清理送进「失物招领」，箱子/背包都没有。
    # 不再凭空生成（用户要求），也不假装能收——收割环节会自动改「逐株手割」。
    try:
        inv = [str(x) for x in ((state().get('player') or {}).get('inventory') or [])]
        if not any('Scythe' in x or '镰刀' in x for x in inv):
            log("  !! 背包里没有镰刀，先从箱子找（不凭空生成）")
            A.send_order({'actionType': 'withdraw_name', 'name': 'Scythe', 'count': 1})
            time.sleep(2)
            inv2 = [str(x) for x in ((state().get('player') or {}).get('inventory') or [])]
            if any('Scythe' in x or '镰刀' in x for x in inv2):
                log("  镰刀已从箱子取回")
            else:
                log("  箱子里也没有镰刀 → 收割改「逐株手割」兜底，同时补一把（用户已同意）")
                A.restore_scythe(log_fn=log)
    except Exception as e:
        log("  !! 检查镰刀异常 %s" % e)
    try:
        A.go_out_tidy(log_fn=log)          # 出门整理：非工具收进箱子，只留 5 件工具
    except Exception as e:
        log("  !! 整理背包异常 %s" % e)
    # 农场维护：挂采集器 / 补机器（只用现有材料）
    try:
        A.farm_maintenance(log_fn=log)
    except Exception as e:
        log("  !! 农场维护异常 %s" % e)
    # 换季第 1 天：不再凭空生成种子（用户要求），缺种子由种植流程去商店买
    try:
        season, day = A.current_season_day()
        if int(day) == 1:
            log("  [换季] 第 1 天：不生成种子，缺的走商店购买")
    except Exception as e:
        log("  !! 换季检查异常 %s" % e)
    try:
        A.season_day_one_cleanup(log_fn=log)
    except Exception as e:
        log("  !! 换季清理异常 %s" % e)
    # 农田：按「记忆」决定今天要不要查（没到成熟日就不去折腾）；
    # 但背包里有种子 + 田里还有空地时，应该现在就去种（2026-11-21 加）
    # 干农活前先确保人站在农场（在农舍时 state 里的 tiles 只含农舍，会把整片田误判成空地）
    try:
        _st = A.summarize(A.read_state())
        _loc = str(_st.get('location'))
        if _loc == 'FarmHouse':
            log("  先在农舍，出门去农场再干活")
            A.travel_to('Farm', log_fn=log)
        elif _loc not in ('Farm', 'FarmHouse'):
            log("  现在在 %s，先回农场再干活" % _loc)
            A.travel_to('Farm', log_fn=log)
    except Exception as e:
        log("  !! 回农场异常 %s" % e)
    due, wait = A.farm_due(log_fn=log)
    if not due:
        try:
            if A.seeds_need_planting(log_fn=log):
                due = True
        except Exception as e:
            log("  !! 补种判断异常 %s" % e)
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
            crops = [t for t in (s.get('tiles') or []) if t.get('crop')]
            # 2026-11-21 修：以前只看 crops[0]（扫描顺序里的第一株），混种时按错了成熟天数，
            # 连收作物会被压在地里好几天。现在取「最快到期」的那株。
            def _days_left(t):
                try:
                    return int(t.get('daysLeft') or 99)
                except Exception:
                    return 99
            best = min(crops, key=_days_left) if crops else None
            label = str((best or {}).get('crop') or "空地")
            if planted > 0 and crops:
                days, regrow = A.crop_meta(label, season)
                step = max(1, int(days))
            elif best is not None and _days_left(best) < 99:
                step = max(1, min(3, _days_left(best)))
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


def afternoon(until="23:30"):
    try:
        s = state()
        if A._mins(s.get('time')) >= A._mins(until):
            log("  [白天] 时间已过 %s，直接回家" % until)
            return
        A.set_activity('钓鱼/挖矿')
        ran = A.spare_time_activity(until=until, log_fn=log)
        s2 = state()
        pl2 = s2.get('player') or {}
        # 2026-11-21 修：以前不看返回值，活动没跑起来（没时间/没去成）也按地点记一次，
        # 统计里的钓鱼/挖矿次数长期虚高。
        if ran:
            if str(s2.get('location')) == 'Beach':
                STATS['fished'] += 1
            else:
                STATS['mined'] += 1
        else:
            log("  [白天] 本次活动没跑起来（时间不够/没去成），不计入统计")
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
        if A._mins(state().get('time')) < A._mins("24:00"):
            A.go_out_tidy(log_fn=log)
        else:
            log("  [夜里] 已过 00:00，跳过整理直接回家")
    except Exception:
        pass
    s0 = state()
    # 如果已经过了 2:00 被强制昏倒，天数已经推进，别再睡一次（否则白白跳掉一天）
    if DAY_START['day'] is not None and s0.get('day') != DAY_START['day']:
        log("  [夜里] 已经换天（大概率 2:00 昏倒过一次），不再重复睡觉")
        STATS['days'] += 1
        return
    d0 = s0.get('day')
    _m0 = (s0.get('player') or {}).get('money')
    log("  [钱] 睡前 %s | %s | %s" % (_m0, s0.get('time'), s0.get('location')))
    ok = A.go_home_sleep(log_fn=log)
    if not ok:
        log("  !! 没进屋/没睡成（go_home_sleep 返回 False），先自愈再试")
        recover('回家睡觉失败')
    slept = False
    for _ in range(30):
        time.sleep(2)
        s = state()
        if s.get('day') != d0:
            slept = True
            break
    if not slept:
        recover('睡觉没换天')
        A.go_home_sleep(log_fn=log)
        for _ in range(15):        # 2026-11-21：第二次也要校验天数，不能固定等 15 秒就认
            time.sleep(2)
            s = state()
            if s.get('day') != d0:
                slept = True
                break
    s = state()
    _m1 = (s.get('player') or {}).get('money')
    try:
        log("  [钱] 睡后 %s（%+d）" % (_m1, int(_m1) - int(_m0)))
    except Exception:
        pass
    if not slept:
        log("  !! 今天没睡成（还是第 %s 天），不计入天数，下一轮补睡" % s.get('day'))
        STATS['errors'] += 1
        return
    STATS['days'] += 1
    log("  [过夜] 第 %s 天完成 → 现在 %s季第%s天 %s | 累计收 %d 种 %d | 错误 %d"
        % (d0, s.get('season'), s.get('day'), s.get('time'),
           STATS['harvested'], STATS['planted'], STATS['errors']))


def main():
    max_days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    log("========== 通宵循环启动（最多 %d 天）==========" % max_days)
    s = snap('开始')
    # 2026-11-21 修：DAY_START 以前从来没被赋值，night() 里「已经换天就别再睡」的守卫形同虚设，
    # 2:00 昏倒自动换天后还会再睡一次，直接白跳一整天。
    DAY_START['season'] = s.get('season')
    DAY_START['day'] = s.get('day')
    for i in range(max_days):
        try:
            # 2026-11-21 夜修：DAY_START 必须每天重新取一次。否则第一天换天后，
            # night() 的「已经换天」判断永远成立 → 再也不睡觉，循环 30 秒空转一轮（实测疯狂刷屏）。
            try:
                cur = state()
                DAY_START['season'] = cur.get('season')
                DAY_START['day'] = cur.get('day')
            except Exception:
                pass
            morning()
            afternoon(until="23:30")
            night()
        except (Exception, SystemExit) as e:
            # read_state() 在存档文件缺失时抛 SystemExit，原来抓不到 → 整个通宵循环静默退出
            log("  !! 第 %d 轮顶层异常 %s" % (i + 1, e))
            traceback.print_exc()
            recover('顶层异常')
            time.sleep(5)
    log("========== 通宵循环结束：%s ==========" % STATS)


if __name__ == '__main__':
    main()
