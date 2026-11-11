#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RVC 单段换声

用法:
    python rvc_full.py in.wav out.wav [f0] [rms_mix] [index_rate] \
        --model NAME [--voices-file voices.json] [--device cpu:0] [--f0-method rmvpe]

声线表格式见 voices.example.json。模型请自备；本仓库不附带任何模型。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import soundfile as sf


def main() -> int:
    ap = argparse.ArgumentParser(description="RVC 单段换声")
    ap.add_argument("input", help="输入 wav")
    ap.add_argument("output", help="输出 wav")
    ap.add_argument("f0_up_key", nargs="?", type=int, default=0, help="升降调（半音）")
    ap.add_argument("rms_mix_rate", nargs="?", type=float, default=0.35, help="rms 混合比")
    ap.add_argument("index_rate", nargs="?", type=float, default=0.5, help="index 占比")
    ap.add_argument("--model", required=True, help="声线名（voices 文件里的键）")
    ap.add_argument("--voices-file", default="voices.json", help="声线表 JSON")
    ap.add_argument("--device", default="cpu:0", help="torch 设备：cpu:0 / cuda:0")
    ap.add_argument("--f0-method", default="rmvpe", help="音高提取方法")
    a = ap.parse_args()

    import rvc_python
    from rvc_python.configs.config import Config
    from rvc_python.modules.vc.modules import VC

    lib_dir = os.path.dirname(os.path.abspath(rvc_python.__file__))

    with open(a.voices_file, encoding="utf-8-sig") as f:
        voices = json.load(f)
    if a.model not in voices:
        print("未知声线: %s（可用: %s）" % (a.model, ", ".join(voices)), file=sys.stderr, flush=True)
        return 2
    MODEL = voices[a.model].get("pth", "")
    INDEX = voices[a.model].get("index", "") or ""

    if not os.path.exists(a.input):
        print("输入不存在: " + a.input, file=sys.stderr, flush=True)
        return 2
    if not MODEL or not os.path.exists(MODEL):
        print("模型文件不存在: %r（检查 %s 里 %s 的 pth 路径）" % (MODEL, a.voices_file, a.model),
              file=sys.stderr, flush=True)
        return 2

    info = sf.info(a.input)
    print("model: %s" % a.model, flush=True)
    print("input: %s sr=%d ch=%d dur=%.2fs" % (a.input, info.samplerate, info.channels, info.duration), flush=True)

    t0 = time.time()
    config = Config(lib_dir, a.device)
    vc = VC(lib_dir, config)
    vc.get_vc(MODEL, version="v2")
    print("model loaded in %.1fs" % (time.time() - t0), flush=True)

    t1 = time.time()
    audio_opt = vc.vc_single(
        sid=0,
        input_audio_path=a.input,
        f0_up_key=a.f0_up_key,
        f0_file="",
        f0_method=a.f0_method,
        file_index=INDEX,
        file_index2="",
        index_rate=a.index_rate,
        filter_radius=3,
        resample_sr=0,
        rms_mix_rate=a.rms_mix_rate,
        protect=0.33,
    )
    print("infer done in %.1fs" % (time.time() - t1), flush=True)
    if not isinstance(audio_opt, np.ndarray):
        print("FAILED: " + str(audio_opt)[:3000], file=sys.stderr, flush=True)
        return 2

    os.makedirs(os.path.dirname(os.path.abspath(a.output)) or ".", exist_ok=True)
    sf.write(a.output, audio_opt, vc.tgt_sr, subtype="PCM_16")
    print("wrote: %s sr=%d samples=%d" % (a.output, vc.tgt_sr, audio_opt.shape[0]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
