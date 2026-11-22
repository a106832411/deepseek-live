#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""骷髅洞穴（沙漠矿洞）自动下潜：找洞/梯子 → 挖矿找梯子 → 没有就放楼梯。

用法：python stardew_skull.py --until 21:30 --target 30
"""
import argparse
import os
import sys
import time

sys.path.insert(0, r'D:\live\deepseek-live\stardew-mod\agent')
import stardew_agent as A  # noqa: E402

LOG = r'D:\live\logs\skull.log'
fh = open(LOG, 'a', encoding='utf-8')


def log(m):
    line = time.strftime('[%H:%M:%S] ') + str(m)
    try:
        print(line, flush=True)
    except Exception:
        pass
    fh.write(line + '\n')
    fh.flush()


def st():
    return A.read_state()


def pos():
    t = (st().get('player') or {}).get('tile') or {}
    return t.get('x', -99), t.get('y', -99)


def lvl():
    return int(st().get('mine_level') or 0)


def go_near(x, y, tries=3):
    """走到目标旁边一格。

    2026-11-22 修：梯子/竖井那一格本身是不可走的（mod 的 walk_to 会直接拒绝
    「不可通行、防穿模」），所以不能直接 walk_to 梯子格——要依次试四邻格。
    """
    px, py = pos()
    if abs(px - x) + abs(py - y) <= 1:
        return True
    for (tx, ty) in ((x, y + 1), (x, y - 1), (x + 1, y), (x - 1, y), (x, y)):
        A.send_order({'actionType': 'walk_to', 'x': tx, 'y': ty})
        for _ in range(tries):
            time.sleep(0.9)
            px, py = pos()
            if abs(px - x) + abs(py - y) <= 1:
                return True
    return False


def down_candidates(s):
    out = []
    for t in (s.get('tiles') or []):
        if not t.get('interactable'):
            continue
        o = str(t.get('obj') or '')
        if '上梯' in o:
            continue
        if any(k in o for k in ('梯', '洞', '竖井', 'Ladder', 'Shaft')):
            out.append(t)
    return out


def mine_some(s, n=6):
    px, py = pos()
    tiles = [t for t in (s.get('tiles') or []) if t.get('breakable')]
    tiles.sort(key=lambda t: abs(t['x'] - px) + abs(t['y'] - py))
    hit = 0
    for t in tiles[:n]:
        if go_near(t['x'], t['y']):
            A.send_order({'actionType': 'use_tool', 'tool': 'pickaxe', 'x': t['x'], 'y': t['y']})
            time.sleep(0.7)
            hit += 1
    return hit


def descend():
    """真实的下一层：先找本层已出现的梯子/竖井（走真实格子），没有就挖石头把梯子挖出来。

    2026-11-22 重写：原来全程走 `mine_goto`（= Game1.enterMine 直接传送），
    日志里每一层都是「下楼 → N 层」，既没有挖矿也没有走梯子，等于没在玩；
    而且跳层之后地上的矿物全丢了。现在改成只认真实梯子/竖井，
    实在挖不出来就返回 False，让上层决定换层或收工（不再偷偷传送）。
    """
    cur = lvl()
    failed = set()
    for round_no in range(1, 6):
        s = st()
        cands = [c for c in down_candidates(s) if (c['x'], c['y']) not in failed]
        if cands:
            # 竖井优先：一次能下 3~15 层
            cands.sort(key=lambda t: 0 if ('竖井' in str(t.get('obj')) or 'Shaft' in str(t.get('obj'))) else 1)
            c = cands[0]
            if go_near(c['x'], c['y'], tries=4):
                A.send_order({'actionType': 'interact', 'x': c['x'], 'y': c['y']})
                time.sleep(2.5)
                if lvl() != cur:
                    log('  走%s (%d,%d) → %d 层' % (c.get('obj'), c['x'], c['y'], lvl()))
                    return True
                log('  交互了%s但没下去，换个目标' % c.get('obj'))
                failed.add((c['x'], c['y']))
            else:
                failed.add((c['x'], c['y']))
                log('  %s (%d,%d) 走不到，换目标' % (c.get('obj'), c['x'], c['y']))

        # 本层没梯子（或够不到）→ 挖石头，原版挖够就会冒梯子
        hit = mine_some(st(), 8)
        if hit == 0:
            log('  本层没有能挖的石头了，下不去')
            break
        log('  第 %d 轮：挖了 %d 处石头找梯子' % (round_no, hit))
        time.sleep(0.5)
    return False


def enter_cavern():
    """走到沙漠里真正的骷髅洞穴洞口交互进洞。返回是否进去了。"""
    try:
        s = st()
        if str(s.get('location')) != 'Desert':
            return False
        cands = [t for t in (s.get('tiles') or []) if t.get('interactable')]
        cands = [t for t in cands
                 if any(k in str(t.get('obj') or '') for k in ('洞', 'Cave', 'Skull', 'Entrance', '井'))]
        for c in cands[:3]:
            if go_near(c['x'], c['y'], tries=4):
                log('  走到洞口 %s (%d,%d)，交互' % (c.get('obj'), c['x'], c['y']))
                A.send_order({'actionType': 'interact', 'x': c['x'], 'y': c['y']})
                time.sleep(3)
                if lvl() >= 121 or str(st().get('location')) == 'SkullCave':
                    return True
        log('  沙漠里没找到可交互的洞口')
    except Exception as e:
        log('  进洞口异常：%s' % e)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--until', default='21:30')
    ap.add_argument('--target', type=int, default=30)
    args = ap.parse_args()

    s = st()
    log('===== 骷髅洞穴开始 until=%s target=%d 当前 %s %s 层%d ====='
        % (args.until, args.target, s.get('location'), s.get('time'), lvl()))

    if lvl() < 121:
        # ① 先走到沙漠（巴士 10:00 才开，早到就等）
        # 2026-11-21 修：以前是无上限死等，菜单/对话把时钟卡住就永远进不了洞
        waited = 0
        while A._mins(st().get('time')) < A._mins('10:00'):
            log('  等巴士开门（现在 %s）' % st().get('time'))
            A.send_order({'actionType': 'close_menu'})
            time.sleep(20)
            waited += 1
            if waited > 45:
                log('  等巴士超过 15 分钟（时钟可能被卡住），直接传送兜底')
                A.send_order({'actionType': 'warp_to', 'location': 'Desert'})
                time.sleep(6)
                break
        log('走路去沙漠：农场 → BusStop → 巴士')
        if not A.go_skull_walk(log_fn=log):
            log('走路到沙漠失败，传送兜底')
            A.send_order({'actionType': 'warp_to', 'location': 'Desert'})
            time.sleep(6)
        # ② 进洞：优先走真实洞口，进不去才用「楼梯」兜底
        if lvl() < 121:
            if enter_cavern():
                log('  从真实洞口进洞成功')
            else:
                log('  洞口进不去 → 用「楼梯」兜底进第 1 层（这不是走路，日志标明）')
                A.send_order({'actionType': 'mine_goto', 'level': 121})
                time.sleep(8)
    s = st()
    if lvl() < 121:
        log('进洞失败（%s 层%d），退回沙漠不硬耗' % (s.get('location'), lvl()))
        if str(s.get('location')) == 'SkullCave':
            A.send_order({'actionType': 'walk_to', 'x': 7, 'y': 9})
            time.sleep(5)
        A.send_order({'actionType': 'warp_to', 'location': 'Desert'})
        time.sleep(6)
        return 0
    start = lvl()
    log('进入骷髅洞穴：第 1 层（mine_level=%d）' % start)

    stuck = 0
    while True:
        s = st()
        if lvl() < 121:
            log('已经不在洞里，结束')
            break
        if A._mins(s.get('time')) >= A._mins(args.until):
            log('到点 %s，收工' % s.get('time'))
            break
        depth = lvl() - start + 1
        if depth >= args.target:
            log('到达目标深度 %d 层，收工' % depth)
            break
        if descend():
            stuck = 0
        else:
            stuck += 1
            if stuck >= 4:
                log('连续 4 次下不去，收工')
                break

    A.send_order({'actionType': 'leave_mine'})
    time.sleep(3)
    A.send_order({'actionType': 'warp_to', 'location': 'Desert'})
    time.sleep(5)
    s = st()
    log('===== 骷髅洞穴结束：%s %s 层%d 钱%s ====='
        % (s.get('location'), s.get('time'), lvl(), (s.get('player') or {}).get('money')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
