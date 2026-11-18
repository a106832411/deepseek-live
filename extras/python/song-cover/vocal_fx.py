#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人声效果器：变调 + 电音/自动修音（World 声码器实现）

用法:
    python vocal_fx.py in.wav out.wav [--pitch 3] [--tune 0.8] [--scale major] [--formant 1.0]

--pitch   变调半音数（+3 = 升 3 个半音，萝莉音常用 +3~+6）
--tune    自动修音强度 0~1（0 不修音，1 = 完全吸附到音阶）
--scale   chromatic/major/minor/pentatonic
--formant 共振峰缩放（1.0 不变；>1 更萝莉，<1 更成熟）
"""
import argparse
import sys

import numpy as np
import pyworld as pw
import soundfile as sf

SCALES = {
    "chromatic": list(range(12)),
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "pentatonic": [0, 2, 4, 7, 9],
}


def snap_to_scale(midi, scale):
    allowed = np.array(scale)
    octave = np.floor(midi / 12.0)
    within = midi - octave * 12.0
    # 每个音找最近的音阶音（跨八度取最近的绝对 MIDI）
    best = np.zeros_like(midi)
    bestdist = np.full_like(midi, 1e9)
    for shift in (-12, 0, 12):
        for n in allowed:
            cand = octave * 12.0 + n + shift
            d = np.abs(midi - cand)
            m = d < bestdist
            best[m] = cand[m]
            bestdist[m] = d[m]
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--pitch", type=float, default=0.0, help="变调半音")
    ap.add_argument("--tune", type=float, default=0.0, help="自动修音强度 0~1")
    ap.add_argument("--scale", default="major", choices=list(SCALES))
    ap.add_argument("--formant", type=float, default=1.0, help="共振峰缩放")
    a = ap.parse_args()

    x, fs = sf.read(a.input, dtype="float64")
    if x.ndim > 1:
        x = x.mean(axis=1)
    print("输入: %s  sr=%d  dur=%.2fs" % (a.input, fs, len(x) / fs), flush=True)

    f0, t = pw.harvest(x, fs, frame_period=5.0)
    sp = pw.cheaptrick(x, f0, t, fs)
    ap_ = pw.d4c(x, f0, t, fs)

    voiced = f0 > 0
    if a.pitch:
        f0[voiced] *= 2.0 ** (a.pitch / 12.0)

    if a.tune > 0:
        midi = np.zeros_like(f0)
        midi[voiced] = 69.0 + 12.0 * np.log2(f0[voiced] / 440.0)
        snapped = snap_to_scale(midi, SCALES[a.scale])
        target = 440.0 * 2.0 ** ((snapped - 69.0) / 12.0)
        blend = 1.0 - a.tune + a.tune * (target[voiced] / np.maximum(f0[voiced], 1e-9))
        f0[voiced] *= blend
        print("电音修音: 强度 %.2f, 音阶 %s" % (a.tune, a.scale), flush=True)

    if a.formant != 1.0:
        sp = np.exp(np.log(sp + 1e-12) * 1.0)
        # 简单共振峰缩放：按频率轴重采样
        n_bins = sp.shape[1]
        idx = np.clip(np.arange(n_bins) / a.formant, 0, n_bins - 1)
        lo = np.floor(idx).astype(int)
        hi = np.minimum(lo + 1, n_bins - 1)
        w = (idx - lo)[None, :]
        sp = sp[:, lo] * (1 - w) + sp[:, hi] * w
        print("共振峰缩放: %.2f" % a.formant, flush=True)

    y = pw.synthesize(
        np.ascontiguousarray(f0, dtype=np.float64),
        np.ascontiguousarray(sp, dtype=np.float64),
        np.ascontiguousarray(ap_, dtype=np.float64),
        fs,
        frame_period=5.0,
    )
    sf.write(a.output, y.astype(np.float32), fs)
    print("输出: %s  %.2fs" % (a.output, len(y) / fs), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
