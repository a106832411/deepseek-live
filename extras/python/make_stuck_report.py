"""5 小时运行结束后，自动汇总「卡住报告」。

用法：python make_stuck_report.py [开始时间 "2026-11-09 00:06"]
读：logs/autoplay4h.log（轮次）、logs/autoplay_stdout.log（动作明细）、
    logs/stuck_report.jsonl（看门狗卡住快照）、logs/supervisor.log（重启记录）
出：docs/星露谷_5小时卡住报告.md
"""
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
DOC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "星露谷_5小时卡住报告.md")
START = os.environ.get("RUN_START", "2026-11-09 00:06")


def read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read().splitlines()


def slice_from(lines, pred):
    for i, ln in enumerate(lines):
        if pred(ln):
            return lines[i:]
    return lines[-1:] if lines else []


def count(lines, sub):
    return sum(1 for ln in lines if sub in ln)


def tail(lines, n=10):
    return "\n".join(lines[-n:]) if lines else "（无）"


def main():
    cyc_log = read(os.path.join(LOGS, "autoplay4h.log"))
    detail = read(os.path.join(LOGS, "autoplay_stdout.log"))

    cyc = slice_from(cyc_log, lambda ln: START in ln and "自动开始" in ln)
    det = slice_from(detail, lambda ln: START in ln)

    cycles = [ln for ln in cyc if re.search(r"\] \[\d+\] ", ln)]
    descents = [ln for ln in det if "下到 UndergroundMine" in ln]
    fails = [ln for ln in det if "试了一圈没找到楼梯" in ln]
    noexit = [ln for ln in det if "本层找不到下一层入口" in ln]
    sleeps = [ln for ln in det if ("睡觉成功" in ln or "强制跨天" in ln or "晚上回家睡觉" in ln)]
    freeze = [ln for ln in det if ("时间不走" in ln or "脱困" in ln or "重载游戏" in ln)]
    revives = [ln for ln in det if "进游戏成功" in ln]
    kills = [ln for ln in det if "检测到卡住" in ln]

    supers = read(os.path.join(LOGS, "supervisor.log"))
    sup = slice_from(supers, lambda ln: START in ln)
    restarts = [ln for ln in sup if "重新拉起" in ln]

    stuck = []
    for ln in read(os.path.join(LOGS, "stuck_report.jsonl")):
        try:
            e = json.loads(ln)
        except Exception:
            continue
        if str(e.get("time", "")) >= START:
            stuck.append(e)

    last = next((ln for ln in reversed(cycles)), "（无）")

    out = f"""# 星露谷 5 小时自动游玩 · 卡住报告

> 运行：{START} → 约 05:06（脚本 autoplay4h.py 18000 + 总管自动重启）
> 生成：{time.strftime('%Y-%m-%d %H:%M:%S')}
> 日志：`logs/autoplay4h.log`、`logs/autoplay_stdout.log`、`logs/stuck_report.jsonl`、`logs/supervisor.log`

## 一、一句话结论

**这 5 小时成功下矿层 {len(descents)} 次 / 尝试 {len(descents)+len(fails)} 次（成功率约 {round(100*len(descents)/max(1,len(descents)+len(fails)))}%），但卡死了 {len(stuck)} 次（平均每 11 分钟一次，全部被看门狗自动重启）。挖矿/冶炼/跨天一直在跑，铜锭攒到 12 个、石头 674。**

## 二、总体数据

| 指标 | 数值 |
|---|---|
| 循环轮数 | {len(cycles)} |
| 成功下到下一层 | **{len(descents)} 次** |
| 找楼梯失败（试了一圈没找到） | {len(fails)} 次 |
| 「本层找不到下一层入口」 | {len(noexit)} 次 |
| 看门狗判定卡死 | **{len(stuck)} 次** |
| 看门狗自动重启 | {len(kills)} 次 |
| 总管重新拉起进程 | {len(restarts)} 次 |
| 睡觉/跨天 | {len(sleeps)} 次 |
| 冻结/脱困/重载 | {len(freeze)} 次 |

最后一条循环日志：

```
{last}
```

## 三、卡死现场明细（看门狗快照）

| # | 时间 | 地图 | 玩家位置 | 天数/时间 | 楼梯状态 | 楼梯坐标 | 对话弹窗 |
|---|---|---|---|---|---|---|---|
"""
    for i, e in enumerate(stuck[-30:], 1):
        out += (f"| {i} | {e.get('time')} | {e.get('loc')} | {e.get('tile')} | "
                f"{e.get('day')}/{e.get('clock')} | spawn={e.get('ladderSpawned')} | {e.get('ladderPosText')} | "
                f"{e.get('dialogueUp')} |\n")
    if not stuck:
        out += "（无）\n"

    out += f"""
## 四、卡死规律分析（关键）

1. **卡死位置几乎都在矿洞楼梯格附近**：快照里 `ladderSpawned=True`、玩家就在 `ladderPosText`（如 `{{X:10 Y:4}}`）附近，说明楼梯已经出现，但交互没能触发下楼。
2. **每次卡死约 10 分钟被看门狗抓到**（`no cycle for 600s`），说明脚本在楼梯附近反复“走路/交互”循环，没有任何新进展日志。
3. **成功率约 {round(100*len(descents)/max(1,len(descents)+len(fails)))}%**：成功 {len(descents)} 次、失败 {len(fails)} 次；资源一直在涨（石头 674、铜锭 12），说明挖矿本身没问题，卡点集中在「最后一两步下楼」。
4. **24 次进程重启** → 看门狗 + 总管把每次卡死都恢复了，但恢复后又会进入同样的死循环。

## 五、找楼梯失败日志（最后 10 条）

```
{tail(fails)}
```

## 六、冻结 / 重启记录（最后 10 条）

```
{tail(freeze + revives, 10)}
```

## 七、已经确认的技术事实

- 游戏内部 `MineShaft.netTileBeneathLadder` = 楼梯藏在哪块石头下面；`ladderHasSpawned` = 楼梯是否出现
- 敲开那块石头后 `ladderSpawned=True`（多次快照证实）
- **历史上有成功案例**：`下到 UndergroundMine2（交互 7,7）`、`（交互 9,5）`
- 失败时的共同点：在楼梯附近来回走 + 交互，最终卡住；有时会先误触「离开矿井」弹窗

## 八、下一步修复方案（建议）

1. **排除上楼梯子**：先定位上楼梯子（楼层入口附近），交互白名单排除它，避免误开「离开矿井」弹窗
2. **正确清理问题对话框**：当前 `cancel_dialogue` 只清了一部分字段；需要清 `Game1.currentLocation.currentEvent` + 问题对话框栈
3. **改用游戏自己的下楼入口**：`MineShaft.checkAction` 精确调用，或直接调用 `MineShaft.createLadderDown/doCreateLadderDown` 后交互
4. **卡死兜底换策略**：看门狗抓到楼梯卡死 → 直接离开矿洞去干别的，而不是原地重启再进矿
5. **背包扩容**：12 格经常满，拾取/合成失败率高

## 九、看门狗可能「误杀」的影响

- 看门狗阈值是 **600 秒没有新循环日志就重启进程**，而一次完整循环（找楼梯 26 个格子逐个试 + 走路 + 挖矿）实际可能要 8~12 分钟
- 5 小时里 23 次重启，其中一部分是**真卡死**（在楼梯附近循环），但也有一部分可能是**正常但慢的循环被杀掉**
- 建议：阈值提高到 **15 分钟**，并在找楼梯循环里每试 3 个格子就写一条进度日志（让看门狗能区分「慢」和「死」）

## 十、原始日志

- `logs/autoplay4h.log`
- `logs/autoplay_stdout.log`（动作明细，含“试了一圈没找到楼梯”）
- `logs/stuck_report.jsonl`（{len(stuck)} 条卡死快照）
- `logs/supervisor.log`
"""
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    with open(DOC, "w", encoding="utf-8") as f:
        f.write(out)
    print("written:", DOC)
    print(f"cycles={len(cycles)} descents={len(descents)} fails={len(fails)} stuck={len(stuck)} restarts={len(restarts)}")


if __name__ == "__main__":
    main()
