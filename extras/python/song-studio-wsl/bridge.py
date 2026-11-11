#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""唱歌工坊 → WSL GPU 桥

dsh-song-studio（唱歌工坊）用 Node 的 spawn(pythonPath, [cover.py, ...]) 直接跑 python，
且传给 cover.py 的都是 Windows 路径。本桥把这一调用转给 WSL 里的 ROCm python 去跑：

    song-studio-wsl.exe <cover.py> --input <win> --voice <名> --config <win json> ...

做的事：
1. 读插件生成的 cover-config.json，把里面的 ffmpeg / voices_file / out_dir / work_dir 换成 /mnt/x/... 路径，
   另存到临时文件；
2. --input / --config 等参数里的 Windows 路径也做同样转换；
3. 调 `wsl.exe -d <发行版> -u <用户> -- env PYTHONPATH=<compat> <py> <cover.py> ...`，stdout/stderr 原样透传
   （插件靠 out_dir 里新出现的 mp3 判断成品，路径走 /mnt/d 所以 Windows 这边能直接看到文件）；
4. 退出码透传。

环境变量（都可选）：
  SONG_WSL_DISTRO   默认 Ubuntu-22.04
  SONG_WSL_USER     默认 root
  SONG_WSL_PYTHON   默认 /opt/rvc/bin/python
  SONG_WSL_COMPAT   默认 /opt/rvc/compat
  SONG_WSL_COVER    默认 /mnt/d/文档/deepseek-harness/default-workspace/deepseek-live/extras/python/song-cover/cover.py
  SONG_WSL_PRINT_ONLY=1  只打印将要执行的命令，不真跑（自检用）
"""
import json
import os
import re
import subprocess
import sys
import tempfile

DEFAULT_DISTRO = "Ubuntu-22.04"
DEFAULT_USER = "root"
DEFAULT_PY = "/opt/rvc/bin/python"
DEFAULT_COMPAT = "/opt/rvc/compat"
DEFAULT_COVER = "/mnt/d/文档/deepseek-harness/default-workspace/deepseek-live/extras/python/song-cover/cover.py"

WIN_PATH_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$")


def to_wsl(p):
    """Windows 路径 → /mnt/x/...；已经是 Linux 路径就原样返回。"""
    if not p:
        return p
    m = WIN_PATH_RE.match(p)
    if not m:
        return p
    return "/mnt/" + m.group(1).lower() + "/" + m.group(2).replace("\\", "/")


def log(msg):
    print("[song-studio-wsl] " + msg, flush=True)


def main(argv):
    distro = os.environ.get("SONG_WSL_DISTRO", DEFAULT_DISTRO)
    user = os.environ.get("SONG_WSL_USER", DEFAULT_USER)
    py = os.environ.get("SONG_WSL_PYTHON", DEFAULT_PY)
    compat = os.environ.get("SONG_WSL_COMPAT", DEFAULT_COMPAT)
    cover = os.environ.get("SONG_WSL_COVER", DEFAULT_COVER)
    print_only = os.environ.get("SONG_WSL_PRINT_ONLY") == "1"

    # argv[0] 是插件传的 cover.py（Windows 路径），我们改用它旁边的 cover.py？不：统一用 WSL 里那份
    args = list(argv[1:])
    if args and (args[0].lower().endswith("cover.py")):
        plugin_cover = args[0]
        log("插件 cover.py: " + plugin_cover + " → 使用 WSL: " + cover)
        args = args[1:]

    # 提取参数
    def get(flag):
        if flag in args:
            i = args.index(flag)
            if i + 1 < len(args):
                return args[i + 1]
        return None

    inp = get("--input")
    voice = get("--voice")
    cfg_path = get("--config")
    if not cfg_path:
        log("错误：插件没传 --config")
        return 2

    # 读插件配置并转路径
    try:
        with open(cfg_path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except Exception as e:
        log("错误：读不了插件配置 %s: %s" % (cfg_path, e))
        return 2
    cfg["ffmpeg"] = "/usr/bin/ffmpeg"
    for k in ("voices_file", "out_dir", "work_dir"):
        if cfg.get(k):
            cfg[k] = to_wsl(cfg[k])
    # voices.json 里的 pth/index 也是 Windows 路径：生成一份 /mnt 化副本
    voices = cfg.get("voices_file")
    if voices:
        try:
            with open(voices, encoding="utf-8-sig") as f:
                voices_data = json.load(f)
            fixed = {}
            for name, v in (voices_data or {}).items():
                v = v or {}
                fixed[name] = {"pth": to_wsl(v.get("pth", "")), "index": to_wsl(v.get("index", ""))}
            vfd, vtmp = tempfile.mkstemp(prefix="song-studio-wsl-voices-", suffix=".json")
            os.close(vfd)
            with open(vtmp, "w", encoding="utf-8") as f:
                json.dump(fixed, f, ensure_ascii=False, indent=2)
            cfg["voices_file"] = to_wsl(vtmp)
            tmp_voices = vtmp
            log("声线表已转 /mnt 路径: " + vtmp)
        except Exception as e:
            log("警告：转 voices.json 失败（按原路径试）: " + str(e))
    # 保底：cuda 设备
    if not cfg.get("device"):
        cfg["device"] = "cuda:0"

    fd, tmp_cfg = tempfile.mkstemp(prefix="song-studio-wsl-", suffix=".json")
    os.close(fd)
    tmp_voices = None
    with open(tmp_cfg, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    # 重建命令行：保留插件传来的业务参数（--voice/--f0/--clip-seconds/--name 等），只换路径
    passthrough = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--input", "--config", "--voice"):
            i += 2
            continue
        passthrough.append(a)
        i += 1

    cmd_args = [cover,
                "--input", to_wsl(inp),
                "--voice", voice or "",
                "--config", to_wsl(tmp_cfg)]
    cmd_args += passthrough

    wsl_cmd = ["wsl.exe", "-d", distro, "-u", user, "--", "env"]
    if os.environ.get("SONG_WSL_DXG", "1") != "0":
        wsl_cmd.append("HSA_ENABLE_DXG_DETECTION=1")
    ld_preload = os.environ.get("SONG_WSL_LD_PRELOAD", "/opt/rocm/lib/libhsa-runtime64.so.1")
    if ld_preload:
        wsl_cmd.append("LD_PRELOAD=" + ld_preload)
    wsl_cmd += ["PYTHONPATH=" + compat, py] + cmd_args

    log("输入: " + str(inp) + "  → " + to_wsl(inp))
    log("声线: " + str(voice))
    log("设备: " + str(cfg.get("device")))
    log("输出目录: " + str(cfg.get("out_dir")))
    if print_only:
        log("PRINT_ONLY 命令: " + " ".join(wsl_cmd))
        try:
            os.unlink(tmp_cfg)
        except OSError:
            pass
        return 0

    try:
        p = subprocess.Popen(wsl_cmd)
        rc = p.wait()
    except Exception as e:
        log("错误：启动 wsl 失败: " + str(e))
        return 3
    finally:
        try:
            os.unlink(tmp_cfg)
        except OSError:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
