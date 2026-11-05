#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星露谷 AI 代理（本地模型版，不花 token）

依赖：
  - 游戏已用 SMAPI 启动，且安装 StardewMCPBridge 模组
  - 本机 Ollama 已加载模型（默认 qwen25-7b-local）
  - 模组目录下 bridge_data.json 存在（进游戏后自动生成）

用法：
  python stardew_agent.py --dry                # 只读状态 + 让模型出计划，不执行
  python stardew_agent.py --once               # 执行一轮
  python stardew_agent.py --loop               # 循环（默认 30 轮）
  python stardew_agent.py --goal "把农场里的石头清掉"

环境变量（全部可选）：
  STARDEW_LLM_BASE   默认 http://127.0.0.1:11434/v1   （本地 Ollama，OpenAI 兼容）
  STARDEW_LLM_MODEL  默认 qwen25-7b-local
  STARDEW_LLM_KEY    默认 ollama
  STARDEW_BRIDGE_DIR 默认 E:/steam/steamapps/common/Stardew Valley/Mods/StardewMCPBridge
  STARDEW_COMPANION  默认 Companion1
"""
import argparse
import json
import os
import sys
import time
import urllib.request

DEFAULT_BRIDGE = r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge"


def env(name, default):
    v = os.environ.get(name)
    return v if v else default


BRIDGE = env("STARDEW_BRIDGE_DIR", DEFAULT_BRIDGE)
LLM_BASE = env("STARDEW_LLM_BASE", "http://127.0.0.1:11434/v1")
LLM_MODEL = env("STARDEW_LLM_MODEL", "qwen25-7b-local")
LLM_KEY = env("STARDEW_LLM_KEY", "ollama")
COMPANION = env("STARDEW_COMPANION", "Companion1")

BRIDGE_FILE = os.path.join(BRIDGE, "bridge_data.json")
ACTION_DIR = os.path.join(BRIDGE, "actions")

_seq = 0


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read_bridge():
    if not os.path.exists(BRIDGE_FILE):
        raise SystemExit(f"找不到 {BRIDGE_FILE}，确认游戏已启动且模组已加载")
    for _ in range(5):
        try:
            with open(BRIDGE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            time.sleep(0.2)
    raise SystemExit("bridge_data.json 读取失败（可能正在写入，稍后重试）")


def send_action(action):
    global _seq
    os.makedirs(ACTION_DIR, exist_ok=True)
    _seq += 1
    name = f"{int(time.time() * 1000):013d}-{_seq:03d}.json"
    tmp = os.path.join(ACTION_DIR, name + ".tmp")
    final = os.path.join(ACTION_DIR, name)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(action, f, ensure_ascii=False)
    os.replace(tmp, final)
    return action


def llm_chat(messages, temperature=0.3, max_tokens=600):
    body = json.dumps(
        {
            "model": LLM_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        LLM_BASE.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LLM_KEY}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def companion_of(bridge, name=COMPANION):
    for c in bridge.get("companions") or []:
        if c.get("name") == name:
            return c
    return None


def summarize(bridge):
    comp = companion_of(bridge)
    player = bridge.get("player") or {}
    tiles = []
    if comp:
        for t in (comp.get("surroundings") or {}).get("tiles") or []:
            if t.get("crop") or t.get("obj") or t.get("breakable") or t.get("interactable"):
                tiles.append(
                    {
                        "x": t["x"],
                        "y": t["y"],
                        "crop": t.get("crop"),
                        "cropReady": t.get("cropReady"),
                        "waterState": t.get("waterState"),
                        "obj": t.get("obj"),
                        "breakable": t.get("breakable"),
                        "interactable": t.get("interactable"),
                    }
                )
    return {
        "time": bridge.get("time"),
        "day": bridge.get("day"),
        "season": bridge.get("season"),
        "weather": bridge.get("weather"),
        "location": bridge.get("location"),
        "money": player.get("money"),
        "stamina": player.get("stamina"),
        "companion": {
            "name": COMPANION,
            "mode": comp.get("mode") if comp else None,
            "tile": (comp.get("tile") if comp else None),
            "location": comp.get("location") if comp else None,
            "inventory": [i.get("name") for i in (comp.get("inventory") or [])] if comp else [],
            "stamina": comp.get("stamina") if comp else None,
        },
        "interesting_tiles": tiles[:40],
    }


SYSTEM_PROMPT = """你是星露谷物语的 AI 农场管家。你要根据游戏状态，输出一个可以逐条执行的行动列表。
只输出 JSON，不要解释，格式：
{"goal": "一句话目标", "actions": [ {"type": "...", ...} ]}

可用动作（最多 12 条）：
- {"type":"warp","location":"Farm","x":64,"y":15}         传送到农场
- {"type":"move_to","x":12,"y":8}                          走到指定格子
- {"type":"use_tool","tool":"hoe|wateringcan|axe|pickaxe","x":12,"y":8}  使用工具
- {"type":"interact","x":12,"y":8}                         交互（收获/开箱/捡东西）
- {"type":"water_all"}                                     给同伴附近所有未浇水作物浇水
- {"type":"harvest_all"}                                   收获附近所有成熟作物
规则：
- 坐标必须来自状态里的 interesting_tiles 或同伴当前格子附近，不要瞎编远处坐标。
- 体力低于 30 时优先回屋/休息，不要再干重活。
- 优先顺序：收获成熟作物 > 浇水 > 清理石头/木头 > 开垦新地。
"""


def make_plan(state, goal=None):
    user = (
        "当前状态：\n"
        + json.dumps(state, ensure_ascii=False)
        + f"\n\n今天的目标：{goal or '把附近能干的农活干完，别浪费体力'}\n请输出 JSON。"
    )
    text = llm_chat(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    )
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON：" + text[:300])
    plan = json.loads(text[start : end + 1])
    plan.setdefault("actions", [])
    return plan


def normalize_action(a):
    t = a.get("type")
    if t in ("move_to", "use_tool", "interact", "face_direction", "attack"):
        a = dict(a)
        a["companion"] = COMPANION
        a["actionType"] = t
        a.pop("type", None)
        return a
    if t in ("warp", "water_all", "harvest_all", "water", "harvest", "clear", "hoe"):
        a = dict(a)
        a["actionType"] = t
        a.pop("type", None)
        return a
    return None


def execute(plan, dry=False):
    actions = plan.get("actions") or []
    log(f"计划：{plan.get('goal')}（{len(actions)} 步）")
    done = 0
    for i, raw in enumerate(actions, 1):
        act = normalize_action(raw)
        if not act:
            log(f"  跳过未知动作：{raw}")
            continue
        desc = json.dumps(act, ensure_ascii=False)
        if dry:
            log(f"  [dry] {desc}")
            continue
        send_action(act)
        done += 1
        log(f"  执行 {i}/{len(actions)}: {desc}")
        time.sleep(0.8)
        # 每 5 步回读一次，检查上一批结果
        if i % 5 == 0:
            time.sleep(1.0)
            b = read_bridge()
            c = companion_of(b)
            if c and c.get("lastCommandResult"):
                log(f"    last: {c['lastCommandResult']}")
    if dry:
        return plan
    time.sleep(2.0)
    b = read_bridge()
    log(f"  执行后：time={b.get('time')} location={b.get('location')}")
    return b


def ensure_ready(bridge):
    """确保同伴已生成，并切到 player 模式（否则 follow 会把同伴拉回玩家身边）。"""
    comp = companion_of(bridge)
    if not comp:
        log("同伴未生成，发送 spawn…")
        send_action({"actionType": "spawn"})
        time.sleep(3)
        bridge = read_bridge()
        comp = companion_of(bridge)
    if comp and comp.get("mode") != "player":
        log(f"切换 {COMPANION} 到 player 模式")
        send_action({"actionType": "set_mode", "target": COMPANION, "mode": "player"})
        time.sleep(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="循环执行")
    ap.add_argument("--once", action="store_true", help="只执行一轮")
    ap.add_argument("--dry", action="store_true", help="只出计划不执行")
    ap.add_argument("--goal", default=None)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--interval", type=int, default=20, help="每轮间隔秒")
    args = ap.parse_args()

    log(f"LLM: {LLM_BASE} model={LLM_MODEL}")
    rounds = 1 if (args.once or args.dry) else (args.rounds if args.loop else 1)
    for r in range(1, rounds + 1):
        try:
            bridge = read_bridge()
            ensure_ready(bridge)
            time.sleep(0.5)
            bridge = read_bridge()
            state = summarize(bridge)
            log(
                f"第 {r} 轮：{state['season']} 第{state['day']}天 {state['time']} "
                f"{state['location']} 钱={state['money']} 体力={state['stamina']}"
            )
            plan = make_plan(state, args.goal)
            # 如果计划里包含传送，先只执行传送，等场景切换后重新观察再规划（避免用旧坐标）
            warps = [a for a in plan.get("actions", []) if a.get("type") == "warp"]
            if warps and not args.dry:
                execute({"goal": "传送", "actions": warps})
                time.sleep(3)
                state = summarize(read_bridge())
                plan = make_plan(state, args.goal)
            result = execute(plan, dry=args.dry)
            if args.dry:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                break
            if not args.loop:
                break
            time.sleep(args.interval)
        except KeyboardInterrupt:
            log("手动中断")
            break
        except Exception as e:
            log(f"出错：{e}")
            if not args.loop:
                raise
            time.sleep(5)


if __name__ == "__main__":
    main()
