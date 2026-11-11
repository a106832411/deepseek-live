#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人声 + 伴奏混音（自动重采样、配平、限幅）

用法:
    python rvc_mix.py vocals.wav instrumental.wav out.wav
"""
import math
import sys

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


def main() -> int:
    if len(sys.argv) != 4:
        print("用法: python rvc_mix.py <vocals.wav> <instrumental.wav> <out.wav>", file=sys.stderr)
        return 2
    voc_path, inst_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]

    voc, vsr = sf.read(voc_path, dtype="float64")
    inst, isr = sf.read(inst_path, dtype="float64")
    print("vocals: %s @ %s   inst: %s @ %s" % (voc.shape, vsr, inst.shape, isr), flush=True)

    g = math.gcd(vsr, isr)
    voc = resample_poly(voc, isr // g, vsr // g)

    if voc.ndim == 1 and inst.ndim == 2:
        voc = np.stack([voc, voc], axis=1)
    elif voc.ndim == 2 and inst.ndim == 1:
        inst = np.stack([inst, inst], axis=1)

    n = min(len(voc), len(inst))
    voc, inst = voc[:n], inst[:n]

    def rms(x):
        return float(np.sqrt(np.mean(x ** 2)) + 1e-12)

    voc_rms, inst_rms = rms(voc), rms(inst)
    gain_voc = 1.05 * inst_rms / voc_rms  # 人声略高于伴奏
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
