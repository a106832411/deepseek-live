"""4 小时挂机总管：如果 autoplay4h 崩了/被关了，自动重新拉起；到点停止。"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
AUTOPLAY = os.path.join(HERE, "autoplay4h.py")
LOG = os.path.join(HERE, "logs", "supervisor.log")
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 14400
START = time.time()


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def running():
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'autoplay4h' } | Measure-Object | Select-Object -ExpandProperty Count"],
        capture_output=True, text=True)
    try:
        return int(out.stdout.strip()) > 0
    except Exception:
        return False


def main():
    log(f"=== 总管启动：目标 {DURATION//3600} 小时 ===")
    restarts = 0
    while time.time() - START < DURATION:
        if not running():
            restarts += 1
            left = int((DURATION - (time.time() - START)) / 60)
            log(f"autoplay 不在跑，重新拉起（第 {restarts} 次，剩余 {left} 分钟）")
            out = open(os.path.join(HERE, "logs", "autoplay_stdout.log"), "ab")
            subprocess.Popen([PY, AUTOPLAY, str(max(300, int(DURATION - (time.time() - START))))],
                             cwd=HERE, stdout=out, stderr=out,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        time.sleep(60)
    log("=== 到点，总管结束 ===")


if __name__ == "__main__":
    main()
