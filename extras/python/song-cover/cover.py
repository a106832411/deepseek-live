#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""song-cover-kit 主流程

一首歌 → ffmpeg 转 wav → demucs 拆人声/伴奏 → RVC 换声 → 混音
→ 完整版 mp3 + 试听片段 mp3。

用法:
    python cover.py --input song.mp3 --voice my_voice
    python cover.py --input song.mp3 --voice my_voice --config config.json --f0 12

模型请自备，见 README 与 docs/MODELS-AND-LICENSES.md。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

import numpy as np
import soundfile as sf

ROOT = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG = {
    "ffmpeg": "ffmpeg",
    "voices_file": "voices.json",
    "demucs_model": "htdemucs",
    "work_dir": "work",
    "out_dir": "out",
    "device": "cpu:0",
    "hf_endpoint": "https://hf-mirror.com",
}


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def die(msg: str) -> None:
    print("错误: " + msg, file=sys.stderr, flush=True)
    sys.exit(1)


def run(cmd: list, timeout: int = 1800, env=None):
    """跑命令，返回 (returncode, stdout+stderr)。"""
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (p.stdout or b"") + (p.stderr or b"")
        return p.returncode, out.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "timeout after %ss" % timeout
    except FileNotFoundError as e:
        return 127, str(e)


def load_config(path: str) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            cfg.update(json.load(f))
    return cfg


def resolve(base: str, p: str) -> str:
    if not p:
        return p
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))


def find_ffmpeg(cfg: dict, base: str) -> str:
    ff = str(cfg.get("ffmpeg") or "ffmpeg")
    if os.path.sep in ff or "/" in ff:
        ff = resolve(base, ff)
        if not os.path.exists(ff):
            die("找不到 ffmpeg: " + ff)
        return ff
    found = shutil.which(ff)
    if not found:
        die("PATH 里找不到 %r，请安装 ffmpeg 或在 config.json 里写绝对路径" % ff)
    return found


def slug(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("._")
    return s[:40] or "song"


def best_start(vocals_wav: str, seconds: float) -> int:
    """在 vocals 里找最响的 N 秒，返回起始秒数。"""
    x, sr = sf.read(vocals_wav, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    win = int(seconds * sr)
    if win <= 0 or len(x) <= win:
        return 0
    step = sr
    n = len(x) // step
    rms = np.array([np.sqrt(np.mean(x[i * step:(i + 1) * step] ** 2) + 1e-12) for i in range(n)])
    w = max(1, int(round(seconds)))
    best_i, best_v = 0, -1.0
    for i in range(0, max(1, n - w + 1)):
        v = float(rms[i:i + w].mean())
        if v > best_v:
            best_v, best_i = v, i
    return best_i


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把一首歌换成指定 RVC 声线（本地运行，模型自备）"
    )
    ap.add_argument("--input", required=True, help="输入音频：mp3 / wav / m4a / flac…")
    ap.add_argument("--name", default="", help="歌名（输出文件名用，默认取输入文件名）")
    ap.add_argument("--voice", default="", help="声线名（voices.json 的键，默认第一个）")
    ap.add_argument("--config", default="", help="配置文件，默认取仓库目录下 config.json")
    ap.add_argument("--voices-file", default="", help="覆盖配置里的声线表路径")
    ap.add_argument("--outdir", default="", help="覆盖配置里的输出目录")
    ap.add_argument("--clip-seconds", type=float, default=60.0, help="试听片段长度，0 = 不切")
    ap.add_argument("--f0", type=int, default=0, help="升降调（半音），0 = 不变")
    ap.add_argument("--rms", type=float, default=0.35, help="RVC rms_mix_rate")
    ap.add_argument("--index-rate", type=float, default=0.5, help="RVC index_rate")
    ap.add_argument("--device", default="", help="torch 设备：cpu:0 / cuda:0")
    ap.add_argument("--keep-work", action="store_true", help="保留下载与中间文件")
    args = ap.parse_args()

    cfg_path = args.config or os.path.join(ROOT, "config.json")
    base = os.path.dirname(os.path.abspath(cfg_path)) if os.path.exists(cfg_path) else ROOT
    cfg = load_config(cfg_path)
    if args.voices_file:
        cfg["voices_file"] = args.voices_file
    if args.outdir:
        cfg["out_dir"] = args.outdir
    if args.device:
        cfg["device"] = args.device

    inp = os.path.abspath(args.input)
    if not os.path.exists(inp):
        die("输入文件不存在: " + inp)
    ff = find_ffmpeg(cfg, base)

    voices_file = resolve(base, cfg["voices_file"])
    if not os.path.exists(voices_file):
        die("找不到声线表 %s（参考 voices.example.json）" % voices_file)
    with open(voices_file, encoding="utf-8-sig") as f:
        voices = json.load(f)
    voice = args.voice or next(iter(voices), "")
    if not voice:
        die("声线表是空的: " + voices_file)
    if voice not in voices:
        die("声线表里没有 %r，可用: %s" % (voice, ", ".join(voices)))

    name = args.name or os.path.splitext(os.path.basename(inp))[0]
    key = "%s_%s" % (slug(name), time.strftime("%Y%m%d_%H%M%S"))
    out_dir = resolve(base, cfg["out_dir"])
    work_root = resolve(base, cfg["work_dir"])
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_root, exist_ok=True)
    work = os.path.join(work_root, key)
    os.makedirs(work, exist_ok=True)
    wav = os.path.join(work, "input.wav")

    log("输入: %s" % inp)
    log("歌名: %s    声线: %s" % (name, voice))

    # 1) 转 wav
    rc, out = run([ff, "-y", "-v", "error", "-i", inp, "-vn", "-ar", "44100", "-ac", "2", wav], timeout=300)
    if rc != 0 or not os.path.exists(wav):
        die("转 wav 失败: " + out[-500:])
    log("已转 wav")

    # 2) demucs
    log("demucs 拆人声/伴奏（首次运行会下载模型）")
    env = dict(os.environ)
    for k in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP"):
        env.pop(k, None)
    if cfg.get("hf_endpoint"):
        env.setdefault("HF_ENDPOINT", str(cfg["hf_endpoint"]))
    env["PYTHONIOENCODING"] = "utf-8"
    demucs_out = os.path.join(work, "demucs")
    rc, out = run(
        [sys.executable, "-m", "demucs", "-j", "1", "--two-stems=vocals",
         "-n", str(cfg["demucs_model"]), "-o", demucs_out, wav],
        timeout=1800,
        env=env,
    )
    stem_dir = os.path.join(demucs_out, str(cfg["demucs_model"]), "input")
    vocals = os.path.join(stem_dir, "vocals.wav")
    inst = os.path.join(stem_dir, "no_vocals.wav")
    if rc != 0 or not os.path.exists(vocals) or not os.path.exists(inst):
        die("demucs 失败: " + out[-800:])
    log("人声分离完成")

    # 3) RVC
    voc_rvc = os.path.join(work, "vocals_rvc.wav")
    log("RVC 换声（%s）" % voice)
    rc, out = run(
        [sys.executable, os.path.join(ROOT, "rvc_full.py"), vocals, voc_rvc,
         str(args.f0), str(args.rms), str(args.index_rate),
         "--model", voice, "--voices-file", voices_file, "--device", str(cfg["device"])],
        timeout=3600,
    )
    if rc != 0 or not os.path.exists(voc_rvc):
        die("RVC 失败: " + out[-800:])

    # 4) 混音
    mix_wav = os.path.join(work, "mix.wav")
    log("混音")
    rc, out = run(
        [sys.executable, os.path.join(ROOT, "rvc_mix.py"), voc_rvc, inst, mix_wav],
        timeout=600,
    )
    if rc != 0 or not os.path.exists(mix_wav):
        die("混音失败: " + out[-500:])

    # 5) 输出
    base_name = "%s_%s" % (name, voice) if voice else name
    full_mp3 = os.path.join(out_dir, base_name + "_full.mp3")
    rc, out = run(
        [ff, "-y", "-v", "error", "-i", mix_wav, "-ar", "44100", "-ac", "2",
         "-b:a", "192k", full_mp3],
        timeout=300,
    )
    if rc != 0 or not os.path.exists(full_mp3):
        die("导出完整版失败: " + out[-500:])
    log("完整版: %s" % full_mp3)

    if args.clip_seconds and args.clip_seconds > 0:
        start = best_start(vocals, args.clip_seconds)
        clip_mp3 = os.path.join(out_dir, "%s_clip%ds.mp3" % (base_name, int(args.clip_seconds)))
        rc, out = run(
            [ff, "-y", "-v", "error", "-ss", str(start), "-t", str(args.clip_seconds),
             "-i", mix_wav, "-ar", "44100", "-ac", "2", "-b:a", "192k", clip_mp3],
            timeout=300,
        )
        if rc != 0 or not os.path.exists(clip_mp3):
            die("导出片段失败: " + out[-500:])
        log("试听片段（从 %ss 起 %.0f 秒）: %s" % (start, args.clip_seconds, clip_mp3))

    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
        log("已清理中间文件（--keep-work 可保留）")

    log("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
