#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星露谷「干活」智能体（规则优先，LLM 只在制定每日目标时调用一次）

相比 stardew_agent.py：
- 不再让模型瞎点格子，改为规则选择：先收割/浇水，再清杂草/树枝/石头，再开垦草地
- 强制同伴先传送到 Farm
- 每天只调 1 次本地模型来定「今天干什么」（token 降低 90%+）
- 到 24:00 自动让主角睡觉，跨天保存

用法：
  python stardew_worker.py --minutes 10        # 干 10 分钟
  python stardew_worker.py --until-sleep       # 一直干到睡觉跨天
"""
import argparse
import json
import os
import time
import urllib.request

BRIDGE = os.environ.get("STARDEW_BRIDGE_DIR", r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge")
LLM_BASE = os.environ.get("STARDEW_LLM_BASE", "http://127.0.0.1:11434/v1")
LLM_MODEL = os.environ.get("STARDEW_LLM_MODEL", "qwen25-7b-local")
LLM_KEY = os.environ.get("STARDEW_LLM_KEY", "ollama")
COMPANION = os.environ.get("STARDEW_COMPANION", "Companion1")

BRIDGE_FILE = os.path.join(BRIDGE, "bridge_data.json")
ACTION_DIR = os.path.join(BRIDGE, "actions")
TOKEN_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stardew_tokens.jsonl")

SEQ = 0
TOKENS = {"calls": 0, "prompt": 0, "completion": 0}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def focus_game():
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(h, l):
            n = user32.GetWindowTextLengthW(h)
            b = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, b, n + 1)
            if 'Stardew Valley' in b.value:
                found.append(h)
            return True

        user32.EnumWindows(cb, 0)
        if found:
            user32.ShowWindow(found[0], 9)
            user32.SetForegroundWindow(found[0])
    except Exception:
        pass


def read_bridge():
    for _ in range(10):
        try:
            with open(BRIDGE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            time.sleep(0.2)
    return None


def send(action):
    global SEQ
    SEQ += 1
    os.makedirs(ACTION_DIR, exist_ok=True)
    name = f"{int(time.time()*1000):013d}-w{SEQ:03d}.json"
    tmp = os.path.join(ACTION_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(action, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(ACTION_DIR, name))


def llm(messages, max_tokens=300):
    body = json.dumps({"model": LLM_MODEL, "messages": messages, "temperature": 0.3,
                       "max_tokens": max_tokens, "stream": False}).encode()
    req = urllib.request.Request(LLM_BASE.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + LLM_KEY}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    u = d.get("usage") or {}
    TOKENS["calls"] += 1
    TOKENS["prompt"] += int(u.get("prompt_tokens") or 0)
    TOKENS["completion"] += int(u.get("completion_tokens") or 0)
    try:
        with open(TOKEN_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "model": LLM_MODEL, "prompt": u.get("prompt_tokens", 0),
                                "completion": u.get("completion_tokens", 0)}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return (d.get("choices") or [{}])[0].get("message", {}).get("content", "")


def comp_of(bridge):
    for c in bridge.get("companions") or []:
        if c.get("name") == COMPANION:
            return c
    return None


def ensure_ready():
    b = read_bridge()
    c = comp_of(b)
    if not c:
        log("同伴未生成，spawn")
        send({"actionType": "spawn"})
        time.sleep(3)
        b = read_bridge()
        c = comp_of(b)
    if c and c.get("mode") != "player":
        log("切换同伴到 player 模式")
        send({"actionType": "set_mode", "target": COMPANION, "mode": "player"})
        time.sleep(2)
        b = read_bridge()
        c = comp_of(b)
    if c and c.get("location") != "Farm":
        log("传送到农场")
        send({"actionType": "warp", "location": "Farm", "x": 64, "y": 15})
        time.sleep(3)
    return read_bridge()


def dist(a, b):
    return abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])


def choose_actions(bridge):
    """规则决定这一轮干什么。"""
    c = comp_of(bridge)
    if not c:
        return [{"actionType": "spawn"}]
    tiles = (c.get("surroundings") or {}).get("tiles") or []
    pos = c.get("tile") or {"x": 64, "y": 15}

    crops = [t for t in tiles if t.get("crop")]
    ready = [t for t in crops if t.get("cropReady")]
    dry = [t for t in crops if t.get("crop") and t.get("waterState") == 0]

    actions = []
    if crops:
        if ready:
            actions.append({"actionType": "harvest_all"})
        if dry:
            actions.append({"actionType": "water_all"})
        if actions:
            return actions

    # 清理：杂草/树枝/石头/树桩
    breakables = [t for t in tiles if t.get("breakable")]
    breakables.sort(key=lambda t: dist(t, pos))
    for t in breakables[:3]:
        tool = "pickaxe"
        ot = (t.get("objType") or "").lower()
        if ot in ("weed", "twig", "tree", "stump", "log"):
            tool = "axe"
        if ot in ("stone", "rock"):
            tool = "pickaxe"
        actions.append({"actionType": "move_to", "companion": COMPANION, "x": t["x"], "y": t["y"]})
        actions.append({"actionType": "use_tool", "companion": COMPANION, "tool": tool, "x": t["x"], "y": t["y"]})
    if actions:
        return actions

    # 开垦草地
    soil = [t for t in tiles if t.get("passable") and not t.get("obj") and not t.get("crop")
            and (t.get("terrain") in (None, "grass"))]
    soil.sort(key=lambda t: dist(t, pos))
    for t in soil[:3]:
        actions.append({"actionType": "move_to", "companion": COMPANION, "x": t["x"], "y": t["y"]})
        actions.append({"actionType": "use_tool", "companion": COMPANION, "tool": "hoe", "x": t["x"], "y": t["y"]})
    return actions


def day_goal(bridge):
    """每天只调一次 LLM，定今天的优先级。"""
    c = comp_of(bridge)
    p = bridge.get("player") or {}
    state = {
        "season": bridge.get("season"), "day": bridge.get("day"), "time": bridge.get("time"),
        "money": p.get("money"), "stamina": p.get("stamina"),
        "location": bridge.get("location"),
        "companion": {"loc": c.get("location"), "tile": c.get("tile")} if c else None,
    }
    msg = [
        {"role": "system", "content": "你是星露谷农场管家。根据状态给一句话的今日优先级（不超过20字），"
                                      "例如：先清农场左上角的杂草和石头，再开垦耕地。"},
        {"role": "user", "content": json.dumps(state, ensure_ascii=False)},
    ]
    try:
        return (llm(msg, max_tokens=80) or "").strip().replace("\n", " ")[:40]
    except Exception as e:
        log("LLM 失败: " + str(e))
        return ""


def run(minutes=10, until_sleep=False):
    start = time.time()
    bridge = ensure_ready()
    if not bridge:
        log("读不到 bridge_data.json，确认游戏已进档")
        return
    goal = ""
    goal_done = False
    last_day = bridge.get("day")
    log(f"开始干活 | {bridge.get('season')} 第{bridge.get('day')}天 {bridge.get('time')} 钱={(bridge.get('player') or {}).get('money')}")
    rounds = 0
    while True:
        if minutes and time.time() - start > minutes * 60:
            log("时间到，收工")
            break
        focus_game()
        bridge = read_bridge()
        if not bridge:
            break
        if bridge.get("day") != last_day:
            log(f"跨天 -> {bridge.get('season')} 第{bridge.get('day')}天")
            last_day = bridge.get("day")
            goal = ""
            goal_done = False
        if bridge.get("time", 0) >= 2400:
            log("到 24:00，让主角睡觉")
            send({"actionType": "player_sleep"})
            time.sleep(3)
            if until_sleep:
                break
        if not goal_done:
            goal_done = True
            goal = day_goal(bridge)
            log(f"今日目标：{goal or '（无）'}")
        acts = choose_actions(bridge)
        if not acts:
            log("没有可干的活，等一会")
            time.sleep(3)
            continue
        rounds += 1
        for a in acts:
            send(a)
            time.sleep(0.55)
        log(f"第{rounds}轮：{len(acts)} 个动作 | time={bridge.get('time')} 钱={(bridge.get('player') or {}).get('money')}")
        time.sleep(0.8)
        if TOKENS["calls"]:
            log(f"token：调用{TOKENS['calls']} 输入{TOKENS['prompt']} 输出{TOKENS['completion']}")
        if until_sleep and bridge.get("time", 0) >= 2400 and last_day != bridge.get("day"):
            break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=10)
    ap.add_argument("--until-sleep", action="store_true")
    args = ap.parse_args()
    run(minutes=args.minutes, until_sleep=args.until_sleep)


if __name__ == "__main__":
    main()
