"""独立状态服务：给 OBS / 鲸live 页面提供 /stardew 与 /api/stardew。

用法：
    python status_server.py            # 监听 127.0.0.1:8790
OBS 浏览器源 URL：
    http://127.0.0.1:8790/stardew
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # deepseek-live 仓库根
PUBLIC = os.path.join(ROOT, "public")
BRIDGE = r"E:\steam\steamapps\common\Stardew Valley\Mods\StardewMCPBridge\bridge_data.json"
GUARD_LOG = os.path.join(HERE, "logs", "guard.log")
PORT = int(os.environ.get("STARDEW_STATUS_PORT", "8790"))


def stardew_status():
    try:
        st = os.stat(BRIDGE)
        with open(BRIDGE, encoding="utf-8") as f:
            b = json.load(f)
        age = time.time() - st.st_mtime
        guard_lines, guard_fresh = [], False
        try:
            gs = os.stat(GUARD_LOG)
            guard_fresh = (time.time() - gs.st_mtime) < 7 * 60
            with open(GUARD_LOG, encoding="utf-8", errors="ignore") as f:
                lines = f.read().strip().splitlines()[-5:]
            guard_lines = [l.split("] ", 1)[-1] for l in lines]
        except Exception:
            pass
        p = b.get("player") or {}
        return {
            "ok": True, "age": age, "guardFresh": guard_fresh, "guardLines": guard_lines,
            "location": b.get("location"), "day": b.get("day"), "time": b.get("time"),
            "money": p.get("money"), "tile": p.get("tile"), "tool": p.get("currentTool"),
            "menu": ((p.get("fishing") or {}).get("activeMenu")),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/stardew":
            try:
                with open(os.path.join(PUBLIC, "stardew.html"), encoding="utf-8") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception as e:
                self._send(500, f"stardew.html 读取失败: {e}", "text/plain; charset=utf-8")
        elif path == "/api/stardew":
            self._send(200, json.dumps(stardew_status(), ensure_ascii=False))
        elif path == "/health":
            self._send(200, json.dumps({"ok": True}))
        else:
            self._send(404, json.dumps({"ok": False, "error": "not found"}))


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"状态服务已启动: http://127.0.0.1:{PORT}/stardew", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
