"""DeepSeek 决策：输入局面 JSON，输出一步操作 JSON。"""
import json
import urllib.request

from util import app_ai_config

SYSTEM = """你是植物大战僵尸的高手玩家，正在代打一局。每次只给一步操作。
只输出一个 JSON 对象，不要任何解释，格式：
{"action":"plant","seed":"向日葵","row":2,"col":3}
或
{"action":"collect","row":2,"col":3}
或
{"action":"wait"}
row 从 0 开始（0 是最上面一行），col 从 0 开始（0 是最左边一列）。
优先保证：阳光经济 > 防御 > 输出。阳光低于 100 时优先种向日葵；有僵尸的行优先放防御。
"""


def decide(state, model="deepseek-chat", max_tokens=120, temperature=0.3):
    key, base = app_ai_config()
    if not key:
        return None, "缺少 DeepSeek key"
    base = base or "https://api.deepseek.com/v1"
    user = "当前局面：\n" + json.dumps(state, ensure_ascii=False)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=data,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        text = j["choices"][0]["message"]["content"].strip()
        return text, None
    except Exception as e:
        return None, repr(e)


def parse_action(text):
    """从模型输出里抽出 JSON。"""
    if not text:
        return None
    s = text.strip()
    if "{" in s:
        s = s[s.index("{"):]
    if "}" in s:
        s = s[:s.rindex("}") + 1]
    try:
        d = json.loads(s)
        if isinstance(d, dict) and d.get("action"):
            return d
    except Exception:
        pass
    return None


if __name__ == "__main__":
    state = {
        "sun": 175,
        "planted": [{"seed": "向日葵", "row": 2, "col": 0}],
        "available": ["向日葵", "豌豆射手"],
        "grid": {"rows": 5, "cols": 9},
        "note": "刚开局，僵尸还没来",
    }
    text, err = decide(state)
    print("LLM:", text, err)
    print("解析:", parse_action(text))
