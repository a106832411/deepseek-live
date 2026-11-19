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
    px, py = pos()
    if abs(px - x) + abs(py - y) <= 1:
        return True
    A.send_order({'actionType': 'walk_to', 'x': x, 'y': y})
    for _ in range(tries):
        time.sleep(1.0)
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
    """优先走真实梯子：梯子没出现就先挖矿，还没有才用楼梯道具。"""
    s = st()
    cur = lvl()
    lx = int(s.get('ladder_x') or -1)
    ly = int(s.get('ladder_y') or -1)
    spawned = bool(s.get('ladder_spawned'))

    if spawned and lx >= 0:
        if go_near(lx, ly):
            A.send_order({'actionType': 'interact', 'x': lx, 'y': ly})
            time.sleep(2.2)
            if lvl() != cur:
                log('  走梯子 → %d 层' % lvl())
                return True

    # 梯子没出现：挖附近的石头/矿（原版规则：挖够就会冒梯子）
    for _ in range(3):
        mine_some(st(), 6)
        s2 = st()
        if s2.get('ladder_spawned'):
            lx = int(s2.get('ladder_x') or -1)
            ly = int(s2.get('ladder_y') or -1)
            if lx >= 0 and go_near(lx, ly):
                A.send_order({'actionType': 'interact', 'x': lx, 'y': ly})
                time.sleep(2.2)
                if lvl() != cur:
                    log('  挖出梯子 → %d 层' % lvl())
                    return True
        if A._mins(s2.get('time')) >= A._mins('21:15'):
            return False

    # 实在没有：在「梯子应出现的位置」放真楼梯（会触发原版梯子生成）
    if lx < 0:
        px, py = pos()
        lx, ly = px + 1, py
    go_near(lx, ly, tries=4)
    A.send_order({'actionType': 'place_stairs', 'x': lx, 'y': ly})
    time.sleep(1.8)
    A.send_order({'actionType': 'interact', 'x': lx, 'y': ly})
    time.sleep(2.2)
    if lvl() != cur:
        log('  放楼梯 → %d 层' % lvl())
        return True
    # 最后兜底：原版下层接口（等价于玩家用楼梯道具下潜）
    A.send_order({'actionType': 'mine_goto', 'level': cur + 1})
    time.sleep(3.5)
    if lvl() != cur:
        log('  下楼 → %d 层' % lvl())
        return True
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
        A.send_order({'actionType': 'mine_goto', 'level': 121})
        time.sleep(8)
    s = st()
    if lvl() < 121:
        log('进洞失败（%s 层%d）' % (s.get('location'), lvl()))
        return 1
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
