"""1 小时修复验证报告生成器。

用法：python make_validation_report.py [开始时间 "2026-11-09 09:15"]
读：logs/autoplay4h.log、logs/autoplay_stdout.log、logs/stuck_report.jsonl
出：docs/星露谷_1小时修复验证报告.md
"""
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
DOC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "星露谷_1小时修复验证报告.md")
START = os.environ.get("RUN_START", "2026-11-09 09:15")


def read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read().splitlines()


def chop(lines, marker):
    for i, ln in enumerate(lines):
        if marker in ln:
            return lines[i:]
    return []


def cnt(lines, sub):
    return sum(1 for ln in lines if sub in ln)


def tail(lines, n=10):
    return "\n".join(lines[-n:]) if lines else "（无）"


def main():
    cyc_all = read(os.path.join(LOGS, "autoplay4h.log"))
    det_all = read(os.path.join(LOGS, "autoplay_stdout.log"))
    cyc = chop(cyc_all, START + " +0m")
    det = chop(det_all, START)

    cycles = [ln for ln in cyc if re.search(r"\] \[\d+\] ", ln)]
    descents = [ln for ln in det if "下到 UndergroundMine" in ln]
    fails = [ln for ln in det if "试了一圈没找到楼梯" in ln]
    checkacts = [ln for ln in det if "checkAction" in ln and "下到" in ln]
    kills = [ln for ln in det if "检测到卡住" in ln]
    reloads = [ln for ln in det if "进游戏成功" in ln]
    sleeps = [ln for ln in det if "睡觉成功" in ln]
    smelt = [ln for ln in det if "第" in ln and "炉" in ln and "铜锭" in ln]

    stuck = []
    for ln in read(os.path.join(LOGS, "stuck_report.jsonl")):
        try:
            e = json.loads(ln)
        except Exception:
            continue
        if str(e.get("time", "")) >= START:
            stuck.append(e)

    first = cycles[0] if cycles else "（无）"
    last = cycles[-1] if cycles else "（无）"

    def val(patterns, text):
        m = re.search(patterns, text)
        return m.group(1) if m else "?"

    out = f"""# 星露谷 1 小时修复验证报告

> 运行：{START} → 约 10:15（修复后验证，1 小时）
> 生成：{time.strftime('%Y-%m-%d %H:%M:%S')}
> 基线：昨夜 5 小时跑（00:06–05:06，成功率 19%，卡死 23 次）

## 一、修复项（本轮验证内容）

| # | 修复 | 验证方式 |
|---|---|---|
| 1 | 找楼梯失败时改成「全图 40 格逐个 checkAction」 | 看 `下到 ...（checkAction x,y）` 记录 |
| 2 | 交互前后都清理对话框，避免「离开矿井」卡死 | 看卡死次数是否下降 |
| 3 | 看门狗从「600 秒无日志」改成「位置/游戏时间 5 分钟不变」 | 看是否还有误杀/卡死 |
| 4 | 看门狗抓到卡死先离开矿洞，不再原地重启 | 看卡死快照是否还在矿里 |
| 5 | 熔炉固定放房子门口 + 复用现成 + 冶炼循环 | 看铜锭产量 |
| 6 | 背包满/站不到石头自动跳过 | 看是否还整轮退出 |

## 二、结果对比

| 指标 | 昨夜 5 小时 | 本轮 1 小时 |
|---|---|---|
| 循环轮数 | 33 | {len(cycles)} |
| 成功下楼 | 10 次 | **{len(descents)} 次** |
| 找楼梯失败 | 43 次 | {len(fails)} 次 |
| 卡死（看门狗） | 23 次 | **{len(stuck)} 次** |
| 游戏重载 | 10 次 | {len(reloads)} 次 |
| 睡觉成功 | 10 次 | {len(sleeps)} 次 |

本轮开始状态：

```
{first}
```

本轮结束状态：

```
{last}
```

## 三、下楼记录（含 checkAction 精确交互）

```
{tail(descents, 12)}
```

其中通过 checkAction 成功的：

```
{tail(checkacts, 12)}
```

## 四、找楼梯失败记录（最后 8 条）

```
{tail(fails, 8)}
```

## 五、卡死快照（本轮，{len(stuck)} 条）

"""
    if stuck:
        out += "| 时间 | 原因 | 地图 | 位置 | 天数/时间 | 楼梯状态 | 对话 |\n|---|---|---|---|---|---|---|\n"
        for e in stuck[-15:]:
            out += (f"| {e.get('time')} | {e.get('reason')} | {e.get('loc')} | {e.get('tile')} | "
                    f"{e.get('day')}/{e.get('clock')} | spawn={e.get('ladderSpawned')} | {e.get('dialogueUp')} |\n")
    else:
        out += "（本轮没有卡死记录 🎉）\n"

    out += f"""
## 六、冶炼记录

```
{tail(smelt, 8)}
```

## 七、结论

（跑完由人工/AI 补充）

## 八、原始日志

- `logs/autoplay4h.log`
- `logs/autoplay_stdout.log`
- `logs/stuck_report.jsonl`
"""
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    with open(DOC, "w", encoding="utf-8") as f:
        f.write(out)
    print("written:", DOC)
    print(f"cycles={len(cycles)} descents={len(descents)} fails={len(fails)} stuck={len(stuck)} checkAction={len(checkacts)}")


if __name__ == "__main__":
    main()
