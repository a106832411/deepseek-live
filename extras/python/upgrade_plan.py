"""十字镐升级计划：卖石头凑钱 → 调 mod 的 upgrade_tool 把镐子升到 2 级。

为什么需要：巨石要 2 级镐子才能挖，AI 每层遇到巨石只能跳过，
logs/needs_upgrade.jsonl 已经刷了上千条「需要升级镐子（巨石）」。
mod 的 upgrade_tool 动作是直接改 UpgradeLevel（扣 2000 金 + 5 铜锭），
不用等克林特那 3 天，所以这里先卖石头凑钱、再升级。

用法：
    import upgrade_plan
    upgrade_plan.maybe_upgrade_pickaxe(target=2, min_marks=8)
CLI：
    python upgrade_plan.py --dry   # 只打印现状 + 决策，绝不发动作（挂机中也能安全跑）
    python upgrade_plan.py         # 执行：卖石头凑钱 → upgrade_tool
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nav2

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "upgrade_plan.log")
MARK_FILE = nav2.MARK_FILE          # logs/needs_upgrade.jsonl，和 nav2 用同一份
STONE = "石头"
COPPER_BAR = "铜锭"                 # 物品 id 334，mod 里按 (O)334 找
UPGRADE_MONEY = 2000                # mod 升级一口价
UPGRADE_BARS = 5                    # mod 升级要 5 个铜锭
PICKAXE_NAMES = ("十字镐", "pickaxe")   # 注意：「锄头」是另一种工具（Hoe），别用「镐」模糊匹配
AXE_NAMES = ("斧头", "axe")             # 「Pickaxe」也含 axe，所以斧头要排除 pickaxe

_TOOLS_CACHE = [0.0, {}]            # [读到的时间戳, {工具名: 等级}]：1 秒 TTL，别每格都读 138KB JSON


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def rd():
    return nav2.rd() or {}


def player():
    return rd().get("player") or {}


def inv():
    return [i for i in player().get("inventory") or [] if i]


def count(name):
    """背包里某物品的总数（复用 sell.py / boxes.py 的 sum(stack) 写法）。"""
    return sum(int(i.get("stack", 1)) for i in inv() if name in str(i.get("name")))


def money():
    return int(player().get("money") or 0)


def tool_levels():
    """读 bridge 快照的 player.tools，返回 {工具名: 等级}；1 秒 TTL 缓存。"""
    now = time.time()
    if _TOOLS_CACHE[1] and now - _TOOLS_CACHE[0] < 1.0:
        return _TOOLS_CACHE[1]
    levels = {}
    for t in player().get("tools") or []:
        try:
            levels[str(t.get("name"))] = int(t.get("level", 0))
        except Exception:
            pass
    if levels:                      # 读空了不缓存，下次重新读（游戏刚启动时快照可能还没写）
        _TOOLS_CACHE[0] = now
        _TOOLS_CACHE[1] = levels
    return levels


def _tool_level(cands, exclude=()):
    """按名字关键字找工具等级；找不到返回 None（bridge 读不到 / 没有这件工具）。"""
    for name, lvl in tool_levels().items():
        low = str(name).lower()
        if any(k in low for k in exclude):
            continue
        if any(k in low for k in cands):
            return lvl
    return None


def pickaxe_level():
    """十字镐等级；读不到返回 None。"""
    return _tool_level(PICKAXE_NAMES)


def axe_level():
    """斧头等级；读不到返回 None。"""
    return _tool_level(AXE_NAMES, exclude=("pickaxe",))


def _mark_counts():
    """返回 (reason 含「镐子」的记录数, 其中含「巨石」的记录数)，utf-8 读。"""
    total = boulder = 0
    try:
        with open(MARK_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    reason = str((json.loads(line) or {}).get("reason") or "")
                except Exception:
                    reason = line
                if "镐子" in reason:
                    total += 1
                    if "巨石" in reason:
                        boulder += 1
    except FileNotFoundError:
        return 0, 0
    except Exception as ex:
        log(f"  读升级记录失败: {ex}")
        return 0, 0
    return total, boulder


def boulder_marks():
    """统计 logs/needs_upgrade.jsonl 里 reason 含「镐子」的记录条数。"""
    return _mark_counts()[0]


def sell_to_reach(target_money):
    """金币不够 target 时卖石头凑钱（留 500），最多卖 3 轮。返回是否达标。"""
    if money() >= target_money:
        log(f"  金币已够（{money()} >= {target_money}），不用卖石头")
        return True
    try:
        import sell                 # 惰性导入：--dry 不碰 gameio/卖东西这套
    except Exception as ex:
        log(f"  import sell 失败，没法卖石头: {ex}")
        return False
    log(f"  卖石头凑钱：金币 {money()} -> 目标 {target_money}，背包石头 {count(STONE)}")
    for i in range(3):
        before = money()
        try:
            sell.sell_item(STONE, keep=500)
        except Exception as ex:
            log(f"  第 {i + 1} 轮卖石头出错: {ex}")
            break
        after = money()
        log(f"  第 {i + 1} 轮卖石头：金币 {before} -> {after}")
        if after >= target_money:
            return True
        # sell_item 的 keep>0 会整叠保留：石头只有一叠（<999）时第一轮卖不掉，退回全卖
        if after <= before and count(STONE) > 0:
            try:
                sell.sell_item(STONE, keep=0)
            except Exception as ex:
                log(f"  全卖石头出错: {ex}")
                break
            after = money()
            log(f"  第 {i + 1} 轮改全卖石头：金币 -> {after}")
            if after >= target_money:
                return True
    ok = money() >= target_money
    log(f"  卖石头结束：金币 {money()}/{target_money}，{'够了' if ok else '还不够'}")
    return ok


def upgrade(tool="pickaxe", target_level=2):
    """调 mod 的 upgrade_tool：扣 2000 金 + 5 铜锭，等级 +1；等 2 秒后从快照复核。"""
    cands = {"pickaxe": PICKAXE_NAMES, "axe": AXE_NAMES}.get(tool, (str(tool).lower(),))
    exclude = ("pickaxe",) if tool == "axe" else ()
    before = _tool_level(cands, exclude)
    money0, bars0 = money(), count(COPPER_BAR)
    if before is not None and before >= target_level:
        log(f"  {tool} 已经 {before} 级（目标 {target_level}），不用升")
        return False
    log(f"  升级 {tool}：等级 {before} -> {target_level}，金币 {money0}，{COPPER_BAR} {bars0}")
    if money0 < UPGRADE_MONEY:
        log(f"  前置检查失败：金币 {money0} < {UPGRADE_MONEY}")
        return False
    if bars0 < UPGRADE_BARS:
        log(f"  前置检查失败：{COPPER_BAR} {bars0} < {UPGRADE_BARS}（先烧矿）")
        return False
    nav2.send({"actionType": "upgrade_tool", "tool": tool}, 1.5)
    time.sleep(2.0)
    _TOOLS_CACHE[0] = 0.0           # 强制丢缓存，复核升级后的真实等级
    _TOOLS_CACHE[1] = {}
    after = _tool_level(cands, exclude)
    money1, bars1 = money(), count(COPPER_BAR)
    log(f"  复核：{tool} 等级 {before} -> {after}，金币 {money0} -> {money1}，"
        f"{COPPER_BAR} {bars0} -> {bars1}")
    if after is None:
        log("  读不到升级后的等级，按失败处理（看 mod 日志里的 upgrade_tool）")
        return False
    ok = (after == before + 1) if before is not None else (after >= target_level)
    log("  升级成功" if ok else "  升级失败：等级没变（看 mod 日志里的 upgrade_tool）")
    return ok


def maybe_upgrade_pickaxe(target=2, min_marks=8):
    """镐子不够级且巨石记录够多时：卖石头凑钱 → 升级。返回是否升级成功。"""
    lvl = pickaxe_level()
    marks = boulder_marks()
    if lvl is None:
        log("  读不到十字镐等级（bridge 快照异常），这次不动")
        return False
    if lvl >= target:
        log(f"  十字镐已 {lvl} 级（目标 {target}），不用升")
        return False
    if marks < min_marks:
        log(f"  升级记录只有 {marks} 条（< {min_marks}），先不升")
        return False
    bars = count(COPPER_BAR)
    log(f"  十字镐 {lvl} 级，升级记录 {marks} 条 -> 准备升到 {target}（铜锭 {bars}）")
    if bars < UPGRADE_BARS:
        log(f"  {COPPER_BAR} 只有 {bars}（需要 {UPGRADE_BARS}），先烧矿再来")
        return False
    if not sell_to_reach(UPGRADE_MONEY):
        log("  金币没凑够，放弃升级")
        return False
    return upgrade("pickaxe", target)


def _print_status():
    """打印现状 + 决策（只读，不发送任何动作）。"""
    b = rd()
    if not b:
        log("  读不到 bridge 快照（游戏没开 / 快照文件被锁），没法判断")
        return
    pl = b.get("player") or {}
    p, a = pickaxe_level(), axe_level()
    marks, boulder = _mark_counts()
    log(f"  地图 {b.get('location')}  金币 {money()}  背包格子 {len(inv())}")
    log(f"  工具等级：十字镐 {p}，斧头 {a}（目标十字镐 2）")
    log(f"  {COPPER_BAR} {count(COPPER_BAR)}（升级要 {UPGRADE_BARS}）  "
        f"{STONE} {count(STONE)}（卖时留 500）")
    log(f"  升级记录 {marks} 条（reason 含「镐子」），其中「巨石」{boulder} 条")
    if pl.get("toolUpgrading"):
        log(f"  注意：快照里 toolUpgrading=True（days={pl.get('toolUpgradeDays')}），"
            f"克林特那边可能正在升级工具，mod 直接改等级可能和它的完成流程打架")
    # 决策
    if p is None:
        log("  决策：读不到十字镐等级，跳过（不动）")
        return
    if p >= 2:
        log(f"  决策：十字镐已 {p} 级，无需升级")
        return
    if marks < 8:
        log(f"  决策：升级记录只有 {marks} 条（< 8），暂不升级")
        return
    if count(COPPER_BAR) < UPGRADE_BARS:
        log(f"  决策：{COPPER_BAR} 不足（{count(COPPER_BAR)} < {UPGRADE_BARS}），先烧矿")
        return
    gap = UPGRADE_MONEY - money()
    if gap > 0:
        log(f"  决策：需要升级；金币差 {gap}（{money()}/{UPGRADE_MONEY}），"
            f"先 sell.sell_item(\"{STONE}\", keep=500) 凑钱再 upgrade_tool")
    else:
        log(f"  决策：条件齐了（金币 {money()} >= {UPGRADE_MONEY}、"
            f"{COPPER_BAR} {count(COPPER_BAR)} >= {UPGRADE_BARS}），可直接调 upgrade_tool")
    log("  提示：--dry 只读快照，不会发送任何动作")


if __name__ == "__main__":
    dry = "--dry" in sys.argv[1:]
    if dry:
        log("=== upgrade_plan --dry（只读，挂机中也可以跑）===")
        _print_status()
    else:
        log("=== 十字镐升级 ===")
        _print_status()
        ok = maybe_upgrade_pickaxe()
        log(f"=== 结束：{'升级成功' if ok else '没有升级'} ===")
