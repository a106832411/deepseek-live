p = r"D:\文档\deepseek-harness\default-workspace\deepseek-live\extras\python\daily_loop3.py"
s = open(p, encoding="utf-8").read()
s = s.replace("    for _ in range(90):\n        b = rd()\n        if b and b.get(\"time\", 0) >= 1800:", "    for _ in range(240):\n        b = rd()\n        if b and b.get(\"time\", 0) >= 1800:")
open(p, "w", encoding="utf-8").write(s)
print("wait window extended to 12 min")