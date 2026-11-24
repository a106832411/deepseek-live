#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""星露谷「干活旁白」：AI 在游戏里干活时偶尔冒一句人话，走 TTS 播出来。

- 每 3 秒看一眼 /api/stardew 的当前动作
- 动作变了 20 秒后、或距上一句超过 90 秒，生成一句 <=18 字的台词
- 台词 POST 到鲸live /api/say（走它自己的 TTS 队列），同时写进 logs/ai_status.json 给状态卡显示
- 模型优先用 config.json 里配的（本地 Ollama 或 DeepSeek），失败就用内置短语兜底

用法：
  python stardew_narration.py            # 常驻
  python stardew_narration.py --once     # 只出一句（调试）
  python stardew_narration.py --dry      # 只打印不播
"""
import argparse
import json
import os
import random
import time

import urllib.request

ROOT = r"D:\live\deepseek-live"
STATE_API = "http://127.0.0.1:8790/api/stardew"
SAY_API = "http://127.0.0.1:8788/api/say"
ACTIVITY_FILE = r"D:\live\logs\ai_status.json"

MIN_GAP = 90          # 同一状态最长沉默
CHANGE_GAP = 20       # 状态变化后开头一句话的最短间隔
POLL = 3

FALLBACK = {
    "fish": ["鱼咬钩了，屏住呼吸…", "今天这条必须拿下！", "水面下好像有大货～"],
    "farm": ["先把这片地浇透，作物才长得欢。", "收成不错，今天没白干。", "这行种下去，过两天就冒芽了。"],
    "mine": ["矿洞里凉飕飕的，别被史莱姆撞到。", "再敲一块，说不定就出宝石。"],
    "travel": ["路上顺便看看风景。", "走快点，别耽误正事～"],
    "idle": ["今天想干点什么好呢…", "先看看状态，再决定去哪。"],
}


def http_json(url, payload=None, timeout=8, headers=None):
    data = None
    headers = dict(headers or {})
    headers.setdefault("Content-Type", "application/json")
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def load_ai_config():
    """读 config.json 的 ai 段；有本地 ollama 配置就优先本地。"""
    cfg = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with open(os.path.join(ROOT, "config.json"), encoding=enc) as fh:
                cfg = json.load(fh)
            break
        except Exception:
            cfg = None
    if cfg is None:
        return None
    ai = cfg.get("ai") or {}
    base = os.environ.get("DEEPLIVE_AI_BASE") or ai.get("openai_base") or ""
    key = os.environ.get("DEEPLIVE_AI_KEY") or ai.get("openai_key") or ""
    model = os.environ.get("DEEPLIVE_AI_MODEL") or ai.get("openai_model") or "deepseek-chat"
    if not base:
        return None
    return {"base": base.rstrip("/"), "key": key, "model": model}


def category(d):
    act = str(d.get("activity") or "")
    loc = str(d.get("location") or "")
    t = act + " " + loc
    if "钓" in act or "鱼" in act:
        return "fish"
    if "浇" in act or "种" in act or "收" in act or "农" in act or "干活" in act:
        return "farm"
    if "矿" in loc or "挖矿" in act:
        return "mine"
    if "走路" in act or "赶路" in act or "传送" in act:
        return "travel"
    return "idle"


def build_prompt(d, cat):
    scene = {
        "fish": "正在钓鱼",
        "farm": "在农场干活（浇水/种地/收菜）",
        "mine": "在矿洞挖矿",
        "travel": "在赶路",
        "idle": "随便逛逛",
    }.get(cat, "在玩星露谷")
    return (
        "你是正在直播玩《星露谷物语》的 AI 主播小深。"
        f"你现在{scene}，第{d.get('day')}天，{d.get('weather')}。"
        "只说一句自言自语的口语化台词：10~18 个字，像个真人边干边嘟囔，"
        "可以感叹、吐槽或给自己打气。不要引号、不要解释、不要 emoji、不要提金币数字。"
    )


def gen_line(d, cat, ai):
    if ai:
        try:
            body = {
                "model": ai["model"],
                "messages": [
                    {"role": "system", "content": "你是星露谷主播小深，说话简短、口语化、有点小幽默。"},
                    {"role": "user", "content": build_prompt(d, cat)},
                ],
                "max_tokens": 48,
                "temperature": 0.95,
            }
            hdrs = {}
            if ai.get("key"):
                hdrs["Authorization"] = "Bearer " + ai["key"]
            r = http_json(ai["base"] + "/chat/completions", body, timeout=10, headers=hdrs)
            text = (r.get("choices") or [{}])[0].get("message", {}).get("content", "")
            text = text.strip().strip('"').strip("“”").replace("\n", " ")
            if text and len(text) <= 40:
                return text
        except Exception:
            pass
    return random.choice(FALLBACK.get(cat, FALLBACK["idle"]))


def write_status(talk, cat):
    try:
        data = {}
        if os.path.isfile(ACTIVITY_FILE):
            try:
                with open(ACTIVITY_FILE, encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                data = {}
        data["talk"] = talk
        data["talkAt"] = time.time()
        data["talkKind"] = cat
        tmp = ACTIVITY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, ACTIVITY_FILE)
    except Exception:
        pass


def speak(text, dry=False):
    if dry:
        print("[dry]", text)
        return
    try:
        http_json(SAY_API, {"text": text}, timeout=45)
        print("[say]", text)
    except Exception as e:
        print("[say-fail]", e, text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    ai = load_ai_config()
    last_cat = None
    last_change = time.time()
    last_say = 0.0

    while True:
        try:
            d = http_json(STATE_API, timeout=4)
        except Exception:
            d = None
        if not d or not d.get("ok"):
            time.sleep(POLL)
            continue

        cat = category(d)
        now = time.time()
        if cat != last_cat:
            last_cat = cat
            last_change = now

        due = (now - last_change >= CHANGE_GAP and now - last_say >= CHANGE_GAP) or \
              (now - last_say >= MIN_GAP)
        if due:
            line = gen_line(d, cat, ai)
            write_status(line, cat)
            speak(line, dry=args.dry)
            last_say = now

        if args.once:
            return
        time.sleep(POLL)


if __name__ == "__main__":
    main()
