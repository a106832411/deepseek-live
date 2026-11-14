#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""星露谷 AI 代理（玩家本体 · 全可见操作版）

配套 mod：Mods/WhalePlayer（不是同伴那套 bridge）
  - 状态：Mods/WhalePlayer/player_state.json
  - 订单：Mods/WhalePlayer/orders/*.json

用法：
  python stardew_agent.py --dry                 # 只读状态 + 出计划
  python stardew_agent.py --once                # 执行一轮
  python stardew_agent.py --loop --rounds 20
  python stardew_agent.py --goal "把这片地清出来"

环境变量：
  STARDEW_LLM_BASE / _MODEL / _KEY / _THINKING
  STARDEW_PLAYER_DIR  默认 D:\\steam\\steamapps\\common\\Stardew Valley\\Mods\\WhalePlayer
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

DEFAULT_PLAYER_DIR = r"D:\steam\steamapps\common\Stardew Valley\Mods\WhalePlayer"


def env(name, default):
    v = os.environ.get(name)
    return v if v else default


PLAYER_DIR = env("STARDEW_PLAYER_DIR", DEFAULT_PLAYER_DIR)
STATE_FILE = os.path.join(PLAYER_DIR, "player_state.json")
ORDER_DIR = os.path.join(PLAYER_DIR, "orders")
LLM_BASE = env("STARDEW_LLM_BASE", "http://127.0.0.1:11434/v1")
LLM_MODEL = env("STARDEW_LLM_MODEL", "qwen25-7b-local")
LLM_KEY = env("STARDEW_LLM_KEY", "ollama")
TOKEN_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stardew_tokens.jsonl")

_tokens = {"calls": 0, "prompt": 0, "completion": 0, "total": 0}
_seq = 0


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------- 与 mod 通信 ----------------
def read_state():
    if not os.path.exists(STATE_FILE):
        raise SystemExit(f"找不到 {STATE_FILE}（游戏没开 / WhalePlayer 没加载？）")
    for _ in range(5):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return normalize(json.load(f))
        except (json.JSONDecodeError, OSError):
            time.sleep(0.2)
    raise SystemExit("player_state.json 读取失败")


def send_order(action):
    global _seq
    os.makedirs(ORDER_DIR, exist_ok=True)
    _seq += 1
    name = f"{int(time.time() * 1000):013d}-{_seq:03d}.json"
    tmp = os.path.join(ORDER_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(action, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(ORDER_DIR, name))
    return action


# ---------------- LLM ----------------
def llm_chat(messages, temperature=0.3, max_tokens=600):
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    # DeepSeek 默认开思考模式（按输出计费、还会吃光 max_tokens）→ 自动关
    thinking = env("STARDEW_LLM_THINKING", "auto").lower()
    if thinking == "auto":
        thinking = "disabled" if "deepseek" in LLM_BASE.lower() else ""
    if thinking in ("disabled", "enabled"):
        payload["thinking"] = {"type": thinking}
    req = urllib.request.Request(
        LLM_BASE.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LLM_KEY}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read().decode("utf-8"))
    usage = data.get("usage") or {}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    _tokens["calls"] += 1
    _tokens["prompt"] += pt
    _tokens["completion"] += ct
    _tokens["total"] += pt + ct
    try:
        with open(TOKEN_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "model": LLM_MODEL, "prompt": pt,
                                "completion": ct, "total": pt + ct}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return data["choices"][0]["message"]["content"]


# ---------------- 归一化 ----------------
SEASON_KEY = {"春": "spring", "夏": "summer", "秋": "fall"}
SEASON_CN = {"spring": "春", "summer": "夏", "fall": "秋", "winter": "冬",
             "春": "春", "夏": "夏", "秋": "秋", "冬": "冬"}
WATER_TEXT = {-1: "干地", 0: "未浇", 1: "已浇"}

CROP_EN = {
    "防风草": "Parsnip", "大蒜": "Garlic", "土豆": "Potato", "甘蓝菜": "Kale", "花椰菜": "Cauliflower",
    "绿豆": "Green Bean", "蓝爵": "Blue Jazz", "郁金香": "Tulip", "草莓": "Strawberry", "大黄": "Rhubarb",
    "蓝莓": "Blueberry", "甜瓜": "Melon", "杨桃": "Starfruit", "红叶卷心菜": "Red Cabbage",
    "啤酒花": "Hops", "辣椒": "Hot Pepper", "西红柿": "Tomato", "小萝卜": "Radish", "虞美人": "Poppy",
    "小麦": "Wheat", "玉米": "Corn", "蔓越莓": "Cranberry", "南瓜": "Pumpkin", "洋蓟": "Artichoke",
    "甜菜": "Beet", "茄子": "Eggplant", "葡萄": "Grape", "苋菜": "Amaranth", "山药": "Yam",
    "小白菜": "Bok Choy", "玫瑰仙子": "Fairy Rose",
}


def normalize(raw):
    s = dict(raw or {})
    s["season"] = SEASON_CN.get(str(s.get("season") or "").lower(), str(s.get("season") or ""))
    t = s.get("time")
    if not isinstance(t, str):
        try:
            n = int(t)
            s["time"] = f"{n // 100:02d}:{n % 100:02d}"
        except Exception:
            s["time"] = str(t)
    p = dict(s.get("player") or {})
    p["inventory"] = [str(x) for x in (p.get("inventory") or [])]
    s["player"] = p
    tiles = []
    for t in s.get("tiles") or []:
        t = dict(t)
        ws = t.get("waterState")
        if isinstance(ws, int):
            t["waterState"] = WATER_TEXT.get(ws, "干地")
        tiles.append(t)
    s["tiles"] = tiles
    return s


def summarize(s):
    p = s.get("player") or {}
    return {
        "time": s.get("time"), "day": s.get("day"), "season": s.get("season"),
        "weather": s.get("weather"), "location": s.get("location"),
        "money": p.get("money"), "stamina": p.get("stamina"),
        "inventory": p.get("inventory") or [],
        "tile": p.get("tile") or {},
        "tiles": (s.get("tiles") or [])[:40],
        "dry_tiles": (s.get("dry_tiles") or [])[:24],
    }


# ---------------- 作物利润表 ----------------
CROPS = {
    "spring": [
        ("防风草", 20, 4, 35, 0, 3.8), ("大蒜", 40, 4, 60, 0, 5.0), ("土豆", 50, 6, 80, 0, 5.0),
        ("甘蓝菜", 70, 6, 110, 0, 6.7), ("花椰菜", 80, 12, 175, 0, 7.9), ("绿豆", 60, 10, 40, 3, 7.9),
        ("蓝爵", 30, 7, 50, 0, 2.9), ("郁金香", 20, 6, 30, 0, 1.7), ("草莓", 100, 8, 120, 4, 20.8),
        ("大黄", 100, 13, 220, 0, 9.2),
    ],
    "summer": [
        ("蓝莓", 80, 13, 50, 4, 20.8), ("甜瓜", 80, 12, 250, 0, 14.2), ("杨桃", 400, 13, 750, 0, 26.9),
        ("红叶卷心菜", 100, 9, 300, 0, 22.2), ("啤酒花", 60, 11, 25, 1, 13.0), ("辣椒", 40, 5, 40, 3, 10.0),
        ("西红柿", 50, 11, 60, 4, 9.0), ("小萝卜", 40, 6, 90, 0, 8.3), ("虞美人", 100, 7, 140, 0, 5.7),
        ("小麦", 10, 4, 25, 0, 3.8), ("玉米", 150, 14, 50, 4, 3.5),
    ],
    "fall": [
        ("蔓越莓", 240, 7, 75, 5, 18.9), ("南瓜", 100, 13, 320, 0, 16.9), ("洋蓟", 30, 8, 160, 0, 16.3),
        ("甜菜", 20, 6, 100, 0, 13.3), ("茄子", 20, 5, 60, 5, 13.0), ("葡萄", 60, 10, 80, 3, 11.7),
        ("苋菜", 70, 7, 150, 0, 11.4), ("山药", 60, 10, 160, 0, 10.0), ("小白菜", 50, 4, 80, 0, 7.5),
        ("玫瑰仙子", 200, 12, 290, 0, 7.5),
    ],
}


def crop_table_text():
    lines = ["作物利润表（种子价/成熟天/售价/再收间隔/约g每天，1.6 基础价）："]
    for season, cn in (("spring", "春"), ("summer", "夏"), ("fall", "秋")):
        rows = "；".join(
            f"{n} {s}g/{d}天/{p}g" + (f" 每{r}天再收" if r else "") + f" ≈{g}g/d"
            for (n, s, d, p, r, g) in CROPS[season]
        )
        lines.append(f"[{cn}季] {rows}")
    lines.append("季末规则：
0. 钓鱼只在海边（goto Beach 后 fish）；不要在农场池塘钓鱼。剩余天数 < 成熟天数就别种了。")
    return "\n".join(lines)


SYSTEM_PROMPT = """你是星露谷物语的 AI 农场管家，操控**玩家角色本人**。只输出 JSON：
{"goal": "一句话目标", "actions": [ {"type": "...", ...} ]}

可用动作（最多 8 条，按顺序执行）：
- {"type":"walk_to","x":12,"y":8}                                  走路过去（画面可见）
- {"type":"exit"}                                                  走到门口出门（在屋里时用）
- {"type":"use_tool","tool":"hoe|wateringcan|axe|pickaxe","x":12,"y":8}  用工具（会挥动再生效）
- {"type":"interact","x":12,"y":8}                                 交互（收获/开箱/捡东西）
- {"type":"plant","x":12,"y":8,"seed":"Bok Choy Seeds"}            把背包里的种子种进已锄好的地
  （dry_tiles 是当前可开垦的空地坐标，hoe 请用这里的）
- {"type":"goto_shop"}                                             从农场一路走进皮埃尔商店（跨地图走路）
- {"type":"goto","location":"Mine"}                                 跨地图走过去（Mine/Beach/Town/Mountain/Farm）
- {"type":"fish"}                                                   抛竿钓鱼（需要鱼竿，站在水边）
- {"type":"shop_buy","item":"Parsnip Seeds","count":5}              在店里买种子（会弹出商店界面）
- {"type":"ship"}                                                  把背包里的作物放进出货箱
- {"type":"sleep"}                                                 睡觉进下一天（存档）

""" + crop_table_text() + """

规则：
1. 人在屋里（location=FarmHouse）→ 先 exit，再走路去农场。
2. 做事前必须 walk_to 到目标格相邻（1 格内），否则工具不生效。
3. 收成优先：tiles 里 cropReady=true 的 → interact；收完 ship 卖作物。
4. 没浇水的作物（crop 有值、waterState=未浇）→ use_tool wateringcan。
5. 空地想种：hoе 开地 → plant? 不支持 plant 时用 interact 播种；开地只挑没有 crop/obj 的格子。
6. 石头用 pickaxe，杂草/树用 axe；有作物的格子只能浇水/收获。
7. 体力 <40 不开新地，<20 回屋休息；18:00 后不开新地，22:00 前 sleep。
8. 坐标必须来自 tiles 或角色当前格附近，别瞎编。
"""

_day_plan = {"day": None, "season": None, "goal": "", "crop": "", "seeds": 0, "tasks": []}
_bought = set()


def plan_day_rules(state):
    season_key = SEASON_KEY.get(str(state.get("season")))
    money = int(state.get("money") or 0)
    day = int(state.get("day") or 1)
    remaining = 28 - day + 1
    budget = max(0, money - 200)
    best = None
    for row in CROPS.get(season_key, []):
        name, seed, days, price, regrow, gday = row
        if days + 1 > remaining or seed > budget:
            continue
        if best is None or gday > best[0]:
            best = (gday, name, seed, days + 1)
    if not best:
        return {"goal": "钱不够或季末太近，今天只做收获/浇水/清理", "crop": "", "seeds": 0, "gday": 0,
                "tasks": ["收获成熟作物", "给未浇水的作物浇水", "清理杂物"]}
    gday, name, seed, need = best
    n = min(int(budget // seed), 20)
    return {"goal": f"种 {n} 株{name}（{need}天成熟，约{gday}g/格/天）", "crop": name, "seeds": n, "gday": gday,
            "tasks": ["收获成熟作物", "给未浇水作物浇水", f"买 {n} 包{name}种子并种下", "清理杂物"]}


def _gday(season_key, name):
    for row in CROPS.get(season_key, []):
        if row[0] == name:
            return row[5]
    return 0.0


def ensure_day_plan(state):
    day, season = state.get("day"), state.get("season")
    if _day_plan["day"] == day and _day_plan["season"] == season:
        return _day_plan
    season_key = SEASON_KEY.get(str(season))
    money = state.get("money") or 0
    rows = "；".join(f"{n} 种子{s}g 成熟{d}天 约{g}g/天" for (n, s, d, p, r, g) in CROPS.get(season_key, []))
    user = (f"现在是{season}第{day}天，有{money}g，体力{state.get('stamina')}，位置{state.get('location')}。\n"
            f"当季作物：{rows}\n"
            '给出今天的计划，只输出 JSON：{"goal":"一句话","crop":"作物名","seeds":数字,"tasks":["..."]}')
    plan = {}
    # 默认用规则计划（0 延迟、0 token）；要模型参与就设 STARDEW_LLM_PLAN=1
    if env("STARDEW_LLM_PLAN", "0") == "1":
        try:
            plan = _extract_json(llm_chat([{"role": "system", "content": SYSTEM_PROMPT},
                                           {"role": "user", "content": user}],
                                          temperature=0.2, max_tokens=400)) or {}
        except Exception as e:
            log(f"模型规划失败（规则兜底）：{e}")
    rule = plan_day_rules(state)
    mcrop = str(plan.get("crop") or "")
    mgday = _gday(season_key, mcrop)
    mdays = 0
    mseed = 0
    for row in CROPS.get(season_key, []):
        if row[0] == mcrop:
            mseed, mdays = row[1], row[2]
    remaining = 28 - int(day or 1) + 1
    if not plan.get("crop") or not mgday or (mdays + 1 > remaining) or (mseed > max(0, int(money) - 200)) \
            or mgday < (rule.get("gday") or 0) * 0.7:
        log(f"采用规则计划：{rule['goal']}")
        _day_plan.update({"day": day, "season": season, **rule})
        return _day_plan
    _day_plan.update({"day": day, "season": season, "goal": str(plan.get("goal") or ""), "crop": mcrop,
                      "seeds": plan.get("seeds") or 0, "gday": mgday,
                      "tasks": [str(x) for x in (plan.get("tasks") or [])][:5]})
    log(f"今日计划：{_day_plan['goal']} | 作物={mcrop} 种子={_day_plan['seeds']}")
    return _day_plan


# ---------------- 提示 / 计划 / 校验 ----------------
def make_hints(state):
    tiles = state.get("tiles") or []
    inv = state.get("inventory") or []
    ready = [t for t in tiles if t.get("crop") and t.get("cropReady")]
    unwatered = [t for t in tiles if t.get("crop") and not t.get("cropReady")
                 and str(t.get("waterState")) != "已浇"]
    dry = state.get("dry_tiles") or []
    clutter = [t for t in tiles if t.get("breakable")]
    hints = []
    if str(state.get("location") or "").lower() == "farmhouse":
        hints.append("人在屋里 → 先 exit 出门")
    if ready:
        hints.append(f"{len(ready)} 株成熟（如 {ready[0]['x']},{ready[0]['y']}）→ walk_to 后 interact")
    if unwatered:
        hints.append(f"{len(unwatered)} 株没浇水（如 {unwatered[0]['x']},{unwatered[0]['y']}）→ wateringcan")
    crop = (_day_plan.get("crop") or "").strip()
    if crop:
        en = CROP_EN.get(crop, crop)
        have = any(crop in str(x) or en in str(x) for x in inv)
        if not have and (state.get("day"), crop) not in _bought:
            hints.append(f"没有{crop}种子 → 先 goto_shop 进店，再 shop_buy {en} Seeds")
        elif have:
            if dry:
                d = dry[:2]
                hints.append("种子有了，开地：" + "、".join(f"hoe ({t['x']},{t['y']})" for t in d))
    if not dry and clutter:
        c = clutter[:2]
        hints.append("没有干净空地：先清杂物 " + "、".join(f"({t['x']},{t['y']})" for t in c))
    if (state.get("money") or 0) < 200 and inv:
        hints.append("钱少 → 先收获再 ship 卖作物")
    if str(state.get("time") or "") >= "22:00":
        hints.append("22:00 后 → sleep 睡觉存档")
    return hints[:5]


def make_plan(state, goal=None):
    dp = _day_plan if _day_plan.get("day") == state.get("day") else {}
    goal_line = (f"人工目标：{goal}" if goal else
                 (f"今日计划：{dp.get('goal')}（作物 {dp.get('crop') or '-'}，种子 {dp.get('seeds') or 0}）"
                  if dp.get("goal") else "今天的目标：收货→浇水→按利润表种当季作物"))
    hints = make_hints(state)
    user = ("当前状态：\n" + json.dumps(state, ensure_ascii=False)
            + ("\n\n参考建议：\n- " + "\n- ".join(hints) if hints else "")
            + f"\n\n{goal_line}\n只输出 JSON；能一次做完就一次给 5~8 个动作（按顺序执行）。")
    last = None
    for attempt in range(3):
        text = llm_chat([{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": user}],
                        temperature=0.2 if attempt else 0.3, max_tokens=1200)
        plan = _extract_json(text)
        if isinstance(plan, dict) and isinstance(plan.get("actions"), list):
            plan.setdefault("goal", goal or "")
            return plan
        last = text[:200]
    raise ValueError("模型没返回合法 JSON：" + str(last))


def _extract_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        return None
    frag = text[s:e + 1]
    try:
        return json.loads(frag)
    except Exception:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", frag.replace("“", '"').replace("”", '"')))
        except Exception:
            return None


def normalize_action(a):
    t = a.get("type")
    if t in ("walk_to", "exit", "use_tool", "interact", "shop_buy", "ship", "sleep", "plant",
             "goto_shop", "goto", "fish"):
        a = dict(a)
        a["actionType"] = t
        a.pop("type", None)
        if t == "use_tool" and not a.get("tool"):
            a["tool"] = "hoe"
        if t == "shop_buy":
            item = str(a.get("item") or "")
            a["item"] = CROP_EN.get(item, item)
        return a
    return None


def validate_actions(state, actions):
    tiles = {(t["x"], t["y"]): t for t in (state.get("tiles") or [])}
    dry = [(d["x"], d["y"]) for d in (state.get("dry_tiles") or [])]
    tilled = [(t["x"], t["y"]) for t in (state.get("tiles") or []) if t.get("tilled")]
    pt = state.get("tile") or {}
    px, py = int(pt.get("x", 0)), int(pt.get("y", 0))

    def nearest(cands, maxd=6):
        if not cands:
            return None
        best = min(cands, key=lambda c: abs(c[0] - px) + abs(c[1] - py))
        return best if abs(best[0] - px) + abs(best[1] - py) <= maxd else None

    keep = []
    indoor = not (state.get("tiles") or [])
    for a in actions:
        t = a.get("type")
        if indoor and t in ("walk_to", "use_tool", "interact", "plant"):
            log(f"  丢弃（人在屋里，先出门）：{a}")
            continue
        if t in ("walk_to", "exit", "shop_buy", "ship", "sleep", "goto_shop", "goto", "fish"):
            if t == "walk_to" and a.get("x") is not None:
                tx, ty = a.get("x"), a.get("y")
                if (tx, ty) not in tiles and (tx, ty) not in dry and abs(tx - px) + abs(ty - py) > 12:
                    log(f"  丢弃（太远的无效坐标）：{a}")
                    continue
            keep.append(a)
            continue
        tile = tiles.get((a.get("x"), a.get("y")))
        # 锄地/播种：模型乱给坐标时，自动吸附到最近的可开垦/已开垦地
        if tile is None and t == "use_tool" and str(a.get("tool") or "").lower() == "hoe":
            snap = nearest(dry)
            if snap:
                log(f"  锄地坐标吸附：({a.get('x')},{a.get('y')}) → {snap}")
                a = dict(a, x=snap[0], y=snap[1])
                tile = {"x": snap[0], "y": snap[1], "crop": None, "obj": None}
        if tile is None and t == "plant":
            snap = nearest(tilled)
            if snap:
                log(f"  播种坐标吸附：({a.get('x')},{a.get('y')}) → {snap}")
                a = dict(a, x=snap[0], y=snap[1])
                tile = {"x": snap[0], "y": snap[1], "crop": None, "tilled": True}
            else:
                log(f"  丢弃（没有已锄好的地可播）：{a}")
                continue
        if t == "plant":
            inv = state.get("inventory") or []
            seed = str(a.get("seed") or "")
            if seed and not any((seed in str(x)) or (seed.replace(" Seeds", "") in str(x)) for x in inv):
                log(f"  丢弃（背包里没有 {seed} 种子）：{a}")
                continue
            keep.append(a)
            continue
        if tile is None:
            log(f"  丢弃（坐标不在附近）：{a}")
            continue
        if t == "use_tool":
            tool = str(a.get("tool") or "").lower()
            if tool == "hoe" and (tile.get("crop") or tile.get("obj") or tile.get("interactable")):
                log(f"  丢弃（不能锄这里）：{a}")
                continue
            if tool in ("wateringcan", "watering_can") and (not tile.get("crop") or tile.get("cropReady")):
                log(f"  丢弃（浇水目标不对）：{a}")
                continue
            if tool in ("axe", "pickaxe") and not (tile.get("breakable") or tile.get("obj")):
                log(f"  丢弃（这里没东西可砍/挖）：{a}")
                continue
            keep.append(a)
            continue
        if t == "interact":
            if not (tile.get("interactable") or tile.get("cropReady")):
                log(f"  丢弃（这里没什么可交互）：{a}")
                continue
            keep.append(a)
            continue
        keep.append(a)
    return keep


# ---------------- 执行 ----------------
def wait_pos(target, timeout=None, tol=1, cur=None):
    """等角色走到目标附近。超时按距离估算，紧轮询（0.15s）。"""
    if cur is None:
        try:
            cur = summarize(read_state())["tile"]
        except Exception:
            cur = {}
    if timeout is None:
        try:
            dist = abs(int(cur.get("x", target[0])) - target[0]) + abs(int(cur.get("y", target[1])) - target[1])
        except Exception:
            dist = 8
        timeout = min(25.0, 2.0 + dist * 0.35)
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            p = summarize(read_state())["tile"]
            if abs(int(p.get("x", -99)) - target[0]) + abs(int(p.get("y", -99)) - target[1]) <= tol:
                return True
        except Exception:
            pass
        time.sleep(0.15)
    return False


def ensure_near(state, x, y):
    """确保角色站在目标格旁边（1 格内），必要时先走过去。"""
    p = state.get("tile") or {}
    px, py = int(p.get("x", 0)), int(p.get("y", 0))
    if abs(px - x) + abs(py - y) <= 1:
        return True
    if abs(px - x) + abs(py - y) > 20:
        log(f"  目标 ({x},{y}) 太远（{abs(px-x)+abs(py-y)} 格），不追")
        return False
    for cand in ((x - 1, y), (x + 1, y), (x, y + 1), (x, y - 1))[:2]:
        log(f"  先走到 ({cand[0]},{cand[1]}) 旁边")
        send_order({"actionType": "walk_to", "x": cand[0], "y": cand[1]})
        if wait_pos(cand, timeout=min(8.0, 2.0 + (abs(px - cand[0]) + abs(py - cand[1])) * 0.3)):
            return True
    return False


def execute(plan, state=None, dry=False):
    actions = (plan.get("actions") or [])[:8]
    if state is not None:
        actions = validate_actions(state, actions)
    log(f"计划：{plan.get('goal')}（{len(actions)} 步）")
    for i, raw in enumerate(actions, 1):
        act = normalize_action(raw)
        if not act:
            log(f"  跳过：{raw}")
            continue
        if dry:
            log(f"  [dry] {json.dumps(act, ensure_ascii=False)}")
            continue
        at = act["actionType"]
        if at == "walk_to":
            tx, ty = act["x"], act["y"]
            tinfo = next((t for t in (state or {}).get("tiles", []) if (t["x"], t["y"]) == (tx, ty)), None)
            blocked = bool(tinfo and (tinfo.get("obj") or tinfo.get("breakable") or tinfo.get("crop")
                                      or tinfo.get("interactable")))
            ok = False
            if blocked:
                ok = ensure_near(state or summarize(read_state()), tx, ty)
            else:
                send_order(act)
                ok = wait_pos((tx, ty))
                if not ok:   # 直接走不到就试着站旁边
                    ok = ensure_near(state or summarize(read_state()), tx, ty)
            log(f"  {i}/{len(actions)} walk_to ({tx},{ty}) → {'到达' if ok else '走不到'}")
        elif at in ("use_tool", "interact", "plant"):
            if not ensure_near(state or summarize(read_state()), act["x"], act["y"]):
                log(f"  {i}/{len(actions)} 走不到 ({act['x']},{act['y']}) 旁边，跳过")
                continue
            send_order(act)
            time.sleep(0.55 if at == "use_tool" else 0.45)
            log(f"  {i}/{len(actions)} {at} ({act['x']},{act['y']})")
        elif at == "exit":
            send_order(act)
            t0 = time.time()
            while time.time() - t0 < 10:
                time.sleep(0.3)
                if str(summarize(read_state()).get("location") or "").lower() != "farmhouse":
                    break
            log(f"  {i}/{len(actions)} exit → {summarize(read_state())['location']}")
        elif at == "goto_shop":
            send_order(act)
            t0 = time.time()
            while time.time() - t0 < 120:
                time.sleep(0.8)
                if str(summarize(read_state()).get("location") or "") == "SeedShop":
                    break
            loc = summarize(read_state()).get("location")
            log(f"  {i}/{len(actions)} goto_shop → {loc}")
        elif at == "goto":
            send_order(act)
            want = act.get("location")
            t0 = time.time()
            while time.time() - t0 < 180:
                time.sleep(0.8)
                if str(summarize(read_state()).get("location") or "") == want:
                    break
            log(f"  {i}/{len(actions)} goto {want} → {summarize(read_state()).get('location')}")
        elif at == "fish":
            send_order(act)
            time.sleep(3.0)
            log(f"  {i}/{len(actions)} fish（抛竿）")
        elif at == "shop_buy":
            send_order(act)
            time.sleep(1.7)
            log(f"  {i}/{len(actions)} shop_buy {act.get('item')} × {act.get('count')}")
        elif at == "ship":
            send_order(act)
            time.sleep(0.8)
            log(f"  {i}/{len(actions)} ship")
        elif at == "sleep":
            log(f"  {i}/{len(actions)} sleep（会进下一天并存档）")
            before = summarize(read_state())
            send_order(act)
            t0 = time.time()
            while time.time() - t0 < 40:
                time.sleep(0.5)
                now = summarize(read_state())
                if now.get("day") != before.get("day") or now.get("season") != before.get("season"):
                    log(f"   已进入 {now.get('season')}第{now.get('day')}天")
                    break
        else:
            send_order(act)
            time.sleep(1)
            log(f"  {i}/{len(actions)} {at}")
    if dry:
        return plan
    time.sleep(1.0)
    s = summarize(read_state())
    log(f"  执行后：{s['season']}第{s['day']}天 {s['time']} {s['location']} 钱={s['money']}")
    return s



def ensure_seeds(state, crop, count):
    """开播前备货：没种子就自动去商店买（进不去店就远程开商店兜底）。"""
    if not crop:
        return False
    inv = state.get("inventory") or []
    en = CROP_EN.get(crop, crop)
    if any((crop in str(x)) or (en in str(x)) for x in inv):
        return True
    if (state.get("money") or 0) < 200:
        log(f"钱不够买{crop}种子（{state.get('money')}g）")
        return False
    log(f"背包没有{en} Seeds → 自动去商店买 {count} 包")
    send_order({"actionType": "goto_shop"})
    t0 = time.time()
    loc = str(summarize(read_state()).get("location"))
    while time.time() - t0 < 150:
        time.sleep(1.0)
        loc = str(summarize(read_state()).get("location"))
        if loc == "SeedShop":
            break
    order = {"actionType": "shop_buy", "item": f"{en} Seeds", "count": int(count)}
    if loc != "SeedShop":
        log(f"  goto_shop 没成功（当前 {loc}）→ 远程开商店兜底")
        order["remote"] = True
    send_order(order)
    time.sleep(2.0)
    inv = summarize(read_state()).get("inventory") or []
    ok = any((crop in str(x)) or (en in str(x)) for x in inv)
    log(f"  买种子{'成功' if ok else '失败'}：{en}")
    return ok

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--goal", default=None)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--interval", type=int, default=15)
    args = ap.parse_args()

    log(f"LLM: {LLM_BASE} model={LLM_MODEL}")
    rounds = 1 if (args.once or args.dry) else (args.rounds if args.loop else 1)
    for r in range(1, rounds + 1):
        try:
            state = summarize(read_state())
            ensure_day_plan(state)
            if _day_plan.get("crop"):
                ensure_seeds(state, _day_plan.get("crop"), _day_plan.get("seeds") or 5)
                state = summarize(read_state())
            log(f"第 {r} 轮：{state['season']} 第{state['day']}天 {state['time']} "
                f"{state['location']} 钱={state['money']} 体力={state['stamina']} 格子={len(state['tiles'])}")
            plan = make_plan(state, args.goal)
            execute(plan, state, dry=args.dry)
            if args.dry:
                break
            log(f"  累计 token：{_tokens['calls']} 次 输入{_tokens['prompt']} 输出{_tokens['completion']}")
            if not args.loop:
                break
            time.sleep(args.interval)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f"出错：{e}")
            if not args.loop:
                raise
            time.sleep(5)


if __name__ == "__main__":
    main()
