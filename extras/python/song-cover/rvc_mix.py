#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人声 + 伴奏混音（自动重采样、配平、限幅）

用法:
    python rvc_mix.py vocals.wav instrumental.wav out.wav
"""
import math
import os
import sys

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


def main() -> int:
    if len(sys.argv) != 4:
        print("用法: python rvc_mix.py <vocals.wav> <instrumental.wav> <out.wav>", file=sys.stderr)
        return 2
    voc_path, inst_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]

    voc, vsr = sf.read(voc_path, dtype="float32")
    inst, isr = sf.read(inst_path, dtype="float32")
    print("vocals: %s @ %s   inst: %s @ %s" % (voc.shape, vsr, inst.shape, isr), flush=True)

    if vsr != isr:
        # 40k -> 44.1k 用 resample_poly 时上下采样因子 441/400，滤波核巨大、内存爆炸；
        # 改用 FFT 重采样（整首 3~4 分钟音频几秒完成）
        try:
            from scipy.signal import resample as _resample_fft

            new_len = int(round(len(voc) * isr / vsr))
            voc = _resample_fft(voc, new_len, axis=0)
            print("resample FFT: %d samples" % new_len, flush=True)
        except Exception as e:
            print("FFT 重采样失败，回退 resample_poly: %s" % e, flush=True)
            g = math.gcd(vsr, isr)
            voc = resample_poly(voc, isr // g, vsr // g)

    if voc.ndim == 1 and inst.ndim == 2:
        voc = np.stack([voc, voc], axis=1)
    elif voc.ndim == 2 and inst.ndim == 1:
        inst = np.stack([inst, inst], axis=1)

    n = min(len(voc), len(inst))
    voc, inst = voc[:n], inst[:n]

    # 可选：给人声加高频提升（RVC 训练音频来自 32k TTS，高频偏闷，可补一点亮度）
    eq_db = float(os.environ.get("RVC_VOC_EQ", "0") or 0)
    if eq_db:
        try:
            from scipy.signal import butter, sosfilt

            nyq = isr / 2.0
            sos = butter(2, min(4000.0, nyq * 0.8) / nyq, btype="high", output="sos")
            high = sosfilt(sos, voc, axis=0)
            voc = voc + high * (10 ** (eq_db / 20.0) - 1.0)
            print("人声高频提升: %+.1f dB" % eq_db, flush=True)
        except Exception as e:
            print("人声 EQ 失败: %s" % e, flush=True)

    def rms(x):
        return float(np.sqrt(np.mean(x ** 2)) + 1e-12)

    voc_rms, inst_rms = rms(voc), rms(inst)
    voc_gain = float(os.environ.get("RVC_VOC_GAIN", "1.05") or 1.05)  # 人声音量倍数（默认略高于伴奏）
    gain_voc = voc_gain * inst_rms / voc_rms
    mix = voc * gain_voc + inst * 1.0

    peak = float(np.abs(mix).max())
    if peak > 0.98:
        mix *= 0.98 / peak

    sf.write(out_path, mix, isr, subtype="PCM_16")
    print("gain_voc=%.3f voc_rms=%.4f inst_rms=%.4f peak=%.3f" % (gain_voc, voc_rms, inst_rms, peak), flush=True)
    print("wrote %s %s @ %s" % (out_path, mix.shape, isr), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
