#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""sing_tts.py —— deepseek-live「清唱」音频管线（逐句 TTS -> 清唱化 -> 串接成一首 wav）

后端（core.cjs）按固定契约调用本脚本，输出：
  1) 一个 单声道 32kHz PCM16 wav：开头 0.3s 留白 + 逐句音频（句间 gap）+ 结尾 0.4s 留白
  2) 一个 timing json：每句在 wav 里的 start（秒，发歌词事件用）

两种唱法：
  --mode read     纯念唱：逐句 TTS 原样串接，只做轻响度归一，不改音高（最稳的兜底）
  --mode melody   清唱：pyworld(WORLD) 分解每句，仅替换 voiced 帧的 f0 成旋律音高，
                  unvoiced 帧保持 0（辅音/气声保留），带 40ms 滑音、5.5Hz 轻颤音、
                  微音高抖动，目标音高整体落在句中位音高 ±5 半音内，每句响度归一。
                  旋律：原创五声音阶 folk1（seed 决定走向）/ 公版《小星星》/《生日快乐》。
                  自然度参数（2026-11 改版）：--blend 目标旋律权重（<1 时与原始自然 f0
                  加权融合，保留语气起伏）、--quantize-ms 0 关闭硬量化、--vibrato <1 弱化
                  颤音、--tail-stretch/--tail-fall 句尾轻拖长/下滑。

用法示例：
  python extras/python/sing_tts.py \
    --lines-file _singtry/lines.json --out _singtry/melody_demo.wav \
    --timing-out _singtry/melody_demo.timing.json \
    --tts-url http://127.0.0.1:9881 \
    --ref-audio "C:/Users/49820/Desktop/deepseek-live/assets/voice_ref/ref_小奶音_7s.wav" \
    --prompt-text "" --speed 0.95 \
    --mode melody --melody auto --bpm 92 --vibrato 1 --gap-ms 320 --seed 123

可靠性：单句 TTS 失败重试 1 次，仍失败用 1.0s 静音占位（timing 里标 failed:true）；
pyworld 异常自动回退 read（timing.fallback == "read"）；全部句子都失败才 exit 2。
"""

import argparse
import io
import json
import math
import os
import random
import re
import sys
import time
import urllib.request
import wave

import numpy as np

# Windows 控制台默认 GBK，日志里都是中文；统一输出 UTF-8（后端按 utf8 读 pipe 也不会乱码）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---------------------------------------------------------------- 常量 / 默认值

SR = 32000                     # 输出采样率（契约）
LEAD_SEC = 0.30                # 开头留白（契约）
TAIL_SEC = 0.40                # 结尾留白（契约）
PLACEHOLDER_SEC = 1.00         # 单句失败时的静音占位

DEFAULT_TTS_URL = "http://127.0.0.1:9881"
DEFAULT_REF_AUDIO = "C:/Users/49820/Desktop/deepseek-live/assets/voice_ref/ref_小奶音_7s.wav"
# 参考音对应的真实文本（Whisper+FunASR 一致）。传错/占位符会让 GPT-SoVITS 输出又短又平。
DEFAULT_PROMPT_TEXT = "梁山在上，今天大白饭也要变得美味呀。"
DEFAULT_TOP_K = 25            # 2026-11 调参：更自然（旧值 20）
DEFAULT_TEMPERATURE = 1.15    # 旧值 1.0
DEFAULT_REP_PENALTY = 1.15    # 旧值 1.35

WORLD_FRAME_MS = 5.0           # WORLD 帧移（ms）
VIBRATO_HZ = 5.5               # 颤音频率（Hz，契约）
VIBRATO_CENTS = 20.0           # 颤音幅度（音分，契约 15~25）
GLIDE_MS = 40.0                # 音程间滑音（ms，契约 30~50）

TARGET_RMS_DB_MELODY = -18.0   # 每句 RMS（dBFS，契约）
TARGET_RMS_DB_READ = -20.0
PEAK_DB = -1.0                 # 峰值上限（dBFS，契约）

# 五声音阶（相对主音的半音偏移：1 2 3 5 6）
_MELODY_XX = [                 # 公版《小星星》（C 大调：1 1 5 5 6 6 5 / 4 4 3 3 2 2 1 / ...）
    [0, 0, 7, 7, 9, 9, 7],
    [5, 5, 4, 4, 2, 2, 0],
    [7, 7, 5, 5, 4, 4, 2],
    [7, 7, 5, 5, 4, 4, 2],
]
_MELODY_SR = [                 # 公版《生日快乐》（相对 G 调：G G A G C B / G G A G D C / ...）
    [0, 0, 2, 0, 5, 4],
    [0, 0, 2, 0, 7, 5],
    [0, 0, 0, 9, 5, 4],
    [10, 10, 9, 5, 7, 5],
]
_FOLK_TEMPLATES = [            # 原创五声音阶走向（seed 选模板 + 旋转，音高再按主播音域自动定调）
    [[9, 7, 5, 4, 2, 4, 5], [7, 5, 4, 2, 0, 2, 4], [9, 7, 9, 7, 5, 4, 2], [4, 2, 0, 2, 0, 0, 0]],
    [[0, 2, 4, 7, 9, 7, 4], [9, 7, 5, 4, 2, 0, 0], [7, 9, 7, 5, 4, 5, 7], [4, 2, 0, 0, 0, 0, 0]],
    [[7, 7, 9, 7, 5, 4, 2], [5, 5, 7, 5, 4, 2, 0], [9, 9, 7, 5, 7, 9, 7], [4, 4, 2, 2, 0, 0, 0]],
]

_SYL_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7afA-Za-z0-9]")


def _eprint(*a):
    print(*a, file=sys.stderr, flush=True)


def _hz_to_midi(hz):
    return 69.0 + 12.0 * math.log2(float(hz) / 440.0)


def _midi_to_hz(m):
    return 440.0 * (2.0 ** ((np.asarray(m, dtype=np.float64) - 69.0) / 12.0))


def count_syllables(text):
    """中文一字一音节；英文/数字按字符近似。标点/空白/emoji 不计。"""
    return len(_SYL_RE.findall(text or ""))


# ---------------------------------------------------------------- 输入 / 输出

def load_lines(path):
    """读歌词 json：["句1","句2"] 或 [{"text":"句1"}, ...]（也兼容 {"lines":[...]}）。"""
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("lines")
    if not isinstance(data, list) or not data:
        raise ValueError("lines 文件必须是非空 JSON 数组")
    out = []
    for i, item in enumerate(data):
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = item.get("text") or item.get("line") or item.get("lyric") or ""
        else:
            text = ""
        text = str(text).strip()
        if not text:
            raise ValueError("第 %d 句为空" % i)
        out.append(text)
    return out


def write_timing(path, payload):
    try:
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _eprint("[sing_tts] 写 timing 失败：%r" % (e,))


# ---------------------------------------------------------------- 音频基础工具

def _resample_lin(y, sr_from, sr_to):
    """线性插值重采样（语音够用，避免额外依赖）。"""
    if sr_from == sr_to or len(y) == 0:
        return y
    n = int(round(len(y) * sr_to / sr_from))
    src = np.arange(len(y), dtype=np.float64)
    dst = np.linspace(0, len(y) - 1, n)
    return np.interp(dst, src, y).astype(np.float64)


def _decode_wav(raw):
    """解析 TTS 返回的 wav bytes -> (mono float64, sr)。优先 wave 模块，兜底 soundfile。"""
    try:
        with wave.open(io.BytesIO(raw)) as w:
            sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
            n = w.getnframes()
            frames = w.readframes(n)
    except Exception:
        import soundfile as sf  # 兜底（例如 float wav）
        y, sr = sf.read(io.BytesIO(raw), dtype="float64", always_2d=True)
        return y.mean(axis=1), int(sr)
    if sw == 2:
        y = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
    elif sw == 4:
        y = np.frombuffer(frames, dtype="<i4").astype(np.float64) / 2147483648.0
    elif sw == 1:
        y = (np.frombuffer(frames, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    else:
        raise RuntimeError("不支持的采样位宽 %d" % sw)
    if ch > 1:
        y = y.reshape(-1, ch).mean(axis=1)
    return y, int(sr)


def trim_silence(y, top_db=35.0, keep_head_ms=40.0, keep_tail_ms=100.0):
    """剪掉句首句尾静音（保守保留辅音起音），让 timing 的 start 贴合实际出声点。"""
    if len(y) == 0:
        return y
    hop = int(0.01 * SR)
    n = len(y) // hop
    if n < 2:
        return y
    frames = y[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return y
    thr = peak * (10.0 ** (-top_db / 20.0))
    idx = np.flatnonzero(rms > thr)
    if len(idx) == 0:
        return y
    start = max(0, idx[0] * hop - int(keep_head_ms * SR / 1000.0))
    end = min(len(y), (idx[-1] + 1) * hop + int(keep_tail_ms * SR / 1000.0))
    return y[start:end]


def _active_rms(y):
    """只统计"有声帧"的 RMS，避免句中停顿把电平拉低。"""
    hop = int(0.025 * SR)
    n = len(y) // hop
    if n < 2:
        return float(np.sqrt(np.mean(np.asarray(y, dtype=np.float64) ** 2))) if len(y) else 0.0
    blocks = y[: n * hop].reshape(n, hop).astype(np.float64)
    rms = np.sqrt(np.mean(blocks ** 2, axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return 0.0
    act = rms[rms > peak * 0.05]
    if len(act) == 0:
        act = rms
    return float(np.sqrt(np.mean(act ** 2)))


def normalize_loudness(y, target_db=TARGET_RMS_DB_MELODY, peak_db=PEAK_DB,
                       gain_lo=0.25, gain_hi=4.0):
    """把每句拉到目标 RMS，并按峰值上限压住；增益限制范围防止把噪声/瑕疵放大。"""
    y = np.asarray(y, dtype=np.float64)
    rms = _active_rms(y)
    if rms <= 1e-8:
        return y
    gain = 10.0 ** ((target_db - 20.0 * math.log10(rms)) / 20.0)
    gain = float(min(gain_hi, max(gain_lo, gain)))
    y = y * gain
    limit = 10.0 ** (peak_db / 20.0)
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > limit:
        y = y * (limit / peak)
    return y


def fade_edges(y, fade_in_ms=6.0, fade_out_ms=15.0):
    y = np.asarray(y, dtype=np.float64).copy()
    n = len(y)
    ni = min(n, int(fade_in_ms * SR / 1000.0))
    no = min(n, int(fade_out_ms * SR / 1000.0))
    if ni > 1:
        y[:ni] *= np.linspace(0.0, 1.0, ni)
    if no > 1:
        y[-no:] *= np.linspace(1.0, 0.0, no)
    return y


def _smooth_1d(x, win):
    """Hann 窗滑动平均（win 为奇数帧数）；用于滑音与微漂移。"""
    x = np.asarray(x, dtype=np.float64)
    win = int(win)
    if win <= 1 or len(x) == 0:
        return x.copy()
    if win % 2 == 0:
        win += 1
    win = min(win, len(x) if len(x) % 2 == 1 else max(1, len(x) - 1))
    if win <= 1:
        return x.copy()
    pad = win // 2
    xp = np.pad(x, pad, mode="edge")
    k = np.hanning(win)
    k /= k.sum()
    return np.convolve(xp, k, mode="valid")


# ---------------------------------------------------------------- TTS

def _tts_request(text, args, opener):
    body = {
        "text": text,
        "text_lang": args.text_lang,
        "ref_audio_path": os.path.abspath(args.ref_audio),
        "prompt_text": args.prompt_text,
        "prompt_lang": args.prompt_lang,
        "text_split_method": "cut0",   # 整句单次解码（代理也会强制 cut0）
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": False,
        "speed_factor": float(args.speed),
        "temperature": float(args.temperature),
        "top_k": int(args.top_k),
        "top_p": 1.0,
        "sample_steps": int(args.sample_steps),
        "repetition_penalty": float(args.repetition_penalty),
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        args.tts_url.rstrip("/") + "/tts",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with opener.open(req, timeout=float(args.timeout)) as r:
        raw = r.read()
        status = getattr(r, "status", 200)
    if status != 200:
        raise RuntimeError("TTS HTTP %s" % status)
    if not raw:
        raise RuntimeError("TTS 返回空数据")
    return raw


def _cache_path(args, text):
    """可选 TTS 缓存（--cache-dir）：同一句/同一参数复用同一段 TTS，方便 read/melody A/B。"""
    if not getattr(args, "cache_dir", None):
        return None
    import hashlib
    key = json.dumps({
        "text": text, "url": args.tts_url, "ref": os.path.abspath(args.ref_audio),
        "prompt": args.prompt_text, "speed": args.speed, "steps": args.sample_steps,
        "top_k": args.top_k, "temp": args.temperature, "rep": args.repetition_penalty,
        "lang": args.text_lang, "plang": args.prompt_lang,
    }, ensure_ascii=False, sort_keys=True)
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return os.path.join(args.cache_dir, h + ".wav")


def synth_line(text, args, opener):
    """合成一句；失败重试 1 次。返回 (audio|None, err|None)。audio 已 trim + 重采样到 SR。"""
    last = None
    cache = _cache_path(args, text)
    for attempt in range(2):
        try:
            if cache and os.path.exists(cache):
                with open(cache, "rb") as f:
                    raw = f.read()
                y, sr = _decode_wav(raw)
            else:
                raw = _tts_request(text, args, opener)
                y, sr = _decode_wav(raw)
                if cache:
                    try:
                        os.makedirs(os.path.dirname(cache), exist_ok=True)
                        tmp = cache + ".tmp"
                        with open(tmp, "wb") as f:
                            f.write(raw)
                        os.replace(tmp, cache)
                    except Exception:
                        pass
            if sr != SR:
                y = _resample_lin(y, sr, SR)
            y = trim_silence(np.asarray(y, dtype=np.float64))
            if len(y) < int(0.05 * SR):
                raise RuntimeError("合成结果过短（%d 采样）" % len(y))
            return y, None
        except Exception as e:
            last = e
            try:
                if cache and os.path.exists(cache):
                    os.remove(cache)   # 缓存坏了就删掉重来
            except Exception:
                pass
            if attempt == 0:
                time.sleep(0.4)
    return None, str(last)


# ---------------------------------------------------------------- 旋律生成

def _choose_melody(name, seed):
    if name != "auto":
        return name
    rng = random.Random(int(seed))
    return rng.choice(["folk1", "folk1", "xiaoxingxing", "shengri"])


def melody_phrases(name, seed):
    """返回 list[list[int]]：每个 phrase 是若干音符（相对主音的半音偏移）。"""
    if name == "xiaoxingxing":
        return [list(p) for p in _MELODY_XX]
    if name == "shengri":
        return [list(p) for p in _MELODY_SR]
    # folk1 / folk2：同一套原创五声音阶模板，用 seed 偏移选不同走向（保证两档听感不同）
    offset = 2 if name == "folk2" else 0
    tpl = [list(p) for p in _FOLK_TEMPLATES[(int(seed) + offset) % len(_FOLK_TEMPLATES)]]
    rot = (int(seed) // 7 + (1 if name == "folk2" else 0)) % len(tpl)
    return tpl[rot:] + tpl[:rot]


def notes_for_line(phrase, n_syll):
    """按音节数从 phrase 取音符；不够就循环补。"""
    n = max(1, int(n_syll))
    ph = phrase if phrase else [0]
    return [ph[k % len(ph)] for k in range(n)]


# ---------------------------------------------------------------- 念唱 / 清唱处理

def _read_process(y):
    """read 模式：轻响度归一 + 淡入淡出，不动音高。"""
    y = normalize_loudness(y, TARGET_RMS_DB_READ, PEAK_DB, gain_lo=0.5, gain_hi=2.0)
    return fade_edges(y, 4.0, 12.0)


def _calc_note_bounds(y, f0, tpos, n_note, args):
    """把一句的 voiced 帧按音符均分，边界往能量谷底吸附，可选节拍轻量化。返回秒边界数组。"""
    hop = WORLD_FRAME_MS / 1000.0
    dur = len(y) / float(SR)
    n_note = max(1, int(n_note))
    frame_len = int(round(SR * hop))
    need = len(f0) * frame_len
    yp = np.pad(y, (0, need - len(y))) if need > len(y) else y[:need]
    frame_rms = np.sqrt(np.mean(yp.reshape(len(f0), frame_len) ** 2, axis=1))
    vf = np.flatnonzero(f0 > 0)
    M = len(vf)
    if M == 0:
        return np.array([0.0, dur], dtype=np.float64)
    bounds = [0.0]
    for k in range(1, n_note):
        fi = int(round(k * M / n_note))
        fi = min(M - 1, max(0, fi))
        bounds.append(float(tpos[vf[fi]]))
    bounds.append(dur)

    for k in range(1, n_note):
        b = bounds[k]
        lo = max(1, int((b - 0.08) / hop))
        hi = min(len(frame_rms), int((b + 0.08) / hop) + 1)
        if hi > lo:
            j = lo + int(np.argmin(frame_rms[lo:hi]))
            if j < len(tpos):
                bounds[k] = float(tpos[j])

    # 轻量化到节拍网格（bpm 用在这里；只在 ≤quantize_ms 的偏差内吸附；0 = 关闭）
    beat = 60.0 / float(args.bpm) if args.bpm and args.bpm > 0 else 0.65
    tol = float(args.quantize_ms) / 1000.0
    if tol > 0:
        for k in range(1, n_note):
            b = bounds[k]
            g = round(b / beat) * beat
            if abs(g - b) <= tol and g > bounds[k - 1] + 0.05 and g < bounds[-1] - 0.05:
                bounds[k] = g
    # 单调 + 每段至少 30ms
    for k in range(1, n_note):
        bounds[k] = max(bounds[k], bounds[k - 1] + 0.03)
        bounds[k] = min(bounds[k], bounds[-1] - 0.03 * (n_note - k))
        bounds[k] = max(bounds[k], 0.0)
    bounds = np.array(bounds, dtype=np.float64)
    bounds[0] = 0.0
    bounds[-1] = max(dur, bounds[-2] + 0.03)
    return bounds


def _apply_tail(f0_new, sp, ap, bounds, args):
    """句尾轻拖音：对最后一段做 WORLD 帧域时间拉伸 + 末尾轻微下滑。返回 (f0, sp, ap, bounds)。"""
    stretch = float(getattr(args, "tail_stretch", 1.0) or 1.0)
    fall = float(getattr(args, "tail_fall", 0.0) or 0.0)
    if stretch <= 1.0001 and abs(fall) < 0.01:
        return f0_new, sp, ap, bounds
    hop = WORLD_FRAME_MS / 1000.0
    a = int(round(float(bounds[-2]) / hop))
    a = max(0, min(a, len(f0_new) - 3))
    b = len(f0_new)
    n_old = b - a
    if n_old < 3:
        return f0_new, sp, ap, bounds
    if stretch > 1.0001:
        n_new = max(n_old + 2, int(round(n_old * stretch)))
        idx = np.linspace(a, b - 1, n_new)
        src = np.arange(a, b, dtype=np.float64)
        f0_tail = np.interp(idx, src, f0_new[a:b])
        sp_tail = np.empty((n_new, sp.shape[1]), dtype=sp.dtype)
        ap_tail = np.empty((n_new, ap.shape[1]), dtype=ap.dtype)
        for j in range(sp.shape[1]):
            sp_tail[:, j] = np.interp(idx, src, sp[a:b, j])
            ap_tail[:, j] = np.interp(idx, src, ap[a:b, j])
        f0_new = np.concatenate([f0_new[:a], f0_tail])
        sp = np.vstack([sp[:a], sp_tail])
        ap = np.vstack([ap[:a], ap_tail])
        bounds = np.array(bounds, dtype=np.float64)
        bounds[-1] = len(f0_new) * hop
    if abs(fall) >= 0.01:
        v = np.flatnonzero(f0_new > 0)
        if len(v) >= 3:
            end = int(v[-1])
            n = max(3, int(round(0.22 / hop)))
            start = max(0, end - n + 1)
            ramp = np.linspace(0.0, 1.0, end - start + 1)
            f0_new[start:end + 1] = f0_new[start:end + 1] * (2.0 ** (-fall * ramp / 12.0))
    return f0_new, sp, ap, bounds


def _stretch_frames(f0, sp, ap, factor):
    """整句 WORLD 帧域均匀拉伸（简单变速对照用；factor>1 变慢）。"""
    factor = float(factor)
    T = len(f0)
    if factor <= 1.0001 or T < 3:
        return f0, sp, ap
    T2 = max(T + 2, int(round(T * factor)))
    idx = np.linspace(0, T - 1, T2)
    src = np.arange(T, dtype=np.float64)
    f0n = np.interp(idx, src, f0)
    spn = np.empty((T2, sp.shape[1]), dtype=np.float64)
    apn = np.empty((T2, ap.shape[1]), dtype=np.float64)
    for j in range(sp.shape[1]):
        spn[:, j] = np.interp(idx, src, sp[:, j])
        apn[:, j] = np.interp(idx, src, ap[:, j])
    return f0n, spn, apn


def _sing_line_v2(y, notes_midi, global_midi, args, seed):
    """唱腔化 v2：按 bpm 音节时值重排 WORLD 帧 —— 拉伸元音、辅音保持、legato 拼接。

    与 _sing_line 的区别：不再保留 TTS 的原句时值，而是把每个音节的 voiced 元音
    帧拉伸/压缩到目标拍长（句尾音更长），未 voiced 辅音帧 ≤90ms，句内停顿 ≤40ms，
    形成"一个字唱够时值、字连着"的唱腔；长音符自动有颤音（沿用 vibrato 参数）。
    """
    import pyworld as pw

    rng = random.Random(int(seed))
    np_rng = np.random.default_rng(int(seed))
    y = np.ascontiguousarray(np.asarray(y, dtype=np.float64))
    hop = WORLD_FRAME_MS / 1000.0
    f0, tpos = pw.harvest(y, SR, f0_floor=float(args.f0_floor),
                          f0_ceil=float(args.f0_ceil), frame_period=WORLD_FRAME_MS)
    f0 = np.ascontiguousarray(f0, dtype=np.float64)
    tpos = np.asarray(tpos, dtype=np.float64)
    if len(f0) < 6:
        return None, None
    sp = pw.cheaptrick(y, f0, tpos, SR)
    ap = pw.d4c(y, f0, tpos, SR)
    if int((f0 > 0).sum()) < 8:
        return None, None

    n_note = max(1, len(notes_midi))
    bounds = _calc_note_bounds(y, f0, tpos, n_note, args)
    bf = [max(0, min(int(round(b / hop)), len(f0))) for b in bounds]

    beat = 60.0 / float(args.bpm) if args.bpm and args.bpm > 0 else 0.65
    scale = max(0.2, min(2.0, float(getattr(args, "beat_scale", 0.75) or 0.75)))
    tail_beats = max(1.0, float(getattr(args, "tail_beats", 1.8) or 1.8))
    durs = [beat * scale] * n_note
    durs[-1] = beat * scale * tail_beats
    uv_cap_start = max(2, int(round(0.09 / hop)))   # 句首辅音最多 90ms
    uv_cap_mid = max(2, int(round(0.04 / hop)))     # 字间停顿最多 40ms（legato）

    out_sp, out_ap, out_voiced, out_note, out_orig = [], [], [], [], []
    for k in range(n_note):
        a, b = bf[k], bf[k + 1]
        if b <= a:
            a = max(0, min(a, len(f0) - 2))
            b = min(len(f0), a + 2)
        seg_f0 = f0[a:b]
        seg_sp = sp[a:b]
        seg_ap = ap[a:b]
        target = max(3, int(round(durs[k] / hop)))
        runs = []
        start = 0
        cur = bool(seg_f0[0] > 0)
        for i in range(1, len(seg_f0)):
            v = bool(seg_f0[i] > 0)
            if v != cur:
                runs.append((start, i, cur))
                start = i
                cur = v
        runs.append((start, len(seg_f0), cur))
        uv_keep = sum(min(e - s, uv_cap_start if s == 0 else uv_cap_mid)
                      for s, e, v in runs if not v)
        v_total = sum(e - s for s, e, v in runs if v)
        v_new_total = max(2, target - uv_keep)
        for s, e, v in runs:
            L = e - s
            if not v:
                keep = min(L, uv_cap_start if s == 0 else uv_cap_mid)
                out_sp.append(seg_sp[s:s + keep])
                out_ap.append(seg_ap[s:s + keep])
                out_voiced.append(np.zeros(keep, dtype=bool))
                out_note.append(np.full(keep, k, dtype=np.int32))
                out_orig.append(np.zeros(keep, dtype=np.float64))
            else:
                newL = max(2, int(round(v_new_total * L / max(1, v_total))))
                idx = np.linspace(s, e - 1, newL)
                src = np.arange(s, e, dtype=np.float64)
                spn = np.empty((newL, sp.shape[1]), dtype=np.float64)
                apn = np.empty((newL, ap.shape[1]), dtype=np.float64)
                for j in range(sp.shape[1]):
                    spn[:, j] = np.interp(idx, src, seg_sp[s:e, j])
                    apn[:, j] = np.interp(idx, src, seg_ap[s:e, j])
                out_sp.append(spn)
                out_ap.append(apn)
                out_voiced.append(np.ones(newL, dtype=bool))
                out_note.append(np.full(newL, k, dtype=np.int32))
                out_orig.append(np.interp(idx, src, seg_f0[s:e]))
    new_sp = np.vstack(out_sp)
    new_ap = np.vstack(out_ap)
    new_voiced = np.concatenate(out_voiced)
    new_note = np.concatenate(out_note)
    new_orig = np.concatenate(out_orig)
    T2 = len(new_sp)
    if T2 < 8 or int(new_voiced.sum()) < 8:
        return None, None
    t2 = np.arange(T2) * hop

    # 新音节边界（秒）
    note_starts = np.zeros(n_note + 1, dtype=np.float64)
    for k in range(n_note):
        idx = np.flatnonzero(new_note == k)
        if len(idx):
            note_starts[k] = idx[0] * hop
            note_starts[k + 1] = (idx[-1] + 1) * hop
    for k in range(1, n_note + 1):
        if note_starts[k] <= note_starts[k - 1]:
            note_starts[k] = note_starts[k - 1] + hop
    note_starts[-1] = max(note_starts[-1], note_starts[-2] + hop)

    # 目标音高：音符音级 + 原始自然起伏（blend 参数同旧版）
    blend = max(0.0, min(1.0, float(getattr(args, "blend", 1.0))))
    mode = getattr(args, "blend_mode", "global")
    vo = new_orig > 0
    orig_midi = np.zeros(T2, dtype=np.float64)
    orig_midi[vo] = 69.0 + 12.0 * np.log2(np.maximum(new_orig[vo], 1e-9) / 440.0)
    line_orig_med = float(np.median(orig_midi[vo])) if np.any(vo) else float(global_midi)
    line_tgt_med = float(np.median(np.asarray(notes_midi, dtype=np.float64)))
    tgt = np.zeros(T2, dtype=np.float64)
    for k in range(n_note):
        sel = (new_note == k)
        if not np.any(sel):
            continue
        base = float(notes_midi[k]) + rng.uniform(-0.04, 0.04)
        vs = sel & vo
        if not np.any(vs):
            tgt[sel] = base
            continue
        med = float(np.median(orig_midi[vs]))
        resid = orig_midi[vs] - med
        if blend >= 0.999:
            tgt[vs] = base + 0.18 * np.clip(resid, -0.4, 0.4)
        elif mode == "note":
            tgt[vs] = base + (1.0 - blend) * np.clip(resid, -1.5, 1.5)
        else:
            tgt[vs] = blend * base + (1.0 - blend) * (orig_midi[vs] - line_orig_med + line_tgt_med)
        tgt[sel & ~vo] = base

    # 滑音（连续 voiced 段内平滑）+ 颤音（新时值下自动长音更明显）+ 微漂移
    vf2 = np.flatnonzero(new_voiced)
    runs2 = []
    st = 0
    for i in range(1, len(vf2)):
        if vf2[i] != vf2[i - 1] + 1:
            runs2.append((st, i))
            st = i
    runs2.append((st, len(vf2)))
    base_v = tgt[vf2].copy()
    win = max(3, int(round(float(args.glide_ms) / WORLD_FRAME_MS)))
    for a2, b2 in runs2:
        if b2 - a2 >= 3:
            base_v[a2:b2] = _smooth_1d(base_v[a2:b2], win)

    vib_amp = max(0.0, min(2.0, float(args.vibrato))) * (VIBRATO_CENTS / 100.0)
    if vib_amp > 0:
        vt = t2[vf2]
        kk = np.clip(np.searchsorted(note_starts[1:-1], vt, side="right"), 0, n_note - 1)
        ns_t = note_starts[:-1][kk]
        ne_t = note_starts[1:][kk]
        note_len = np.maximum(ne_t - ns_t, 1e-3)
        ramp_in = np.minimum(0.18, 0.35 * note_len)
        ramp_out = np.minimum(0.15, 0.30 * note_len)
        ramp = np.clip((vt - ns_t) / ramp_in, 0.0, 1.0) * \
            np.clip((ne_t - vt) / ramp_out, 0.0, 1.0)
        vib = vib_amp * np.sin(2.0 * math.pi * VIBRATO_HZ * vt + rng.uniform(0, 2 * math.pi))
        base_v = base_v + vib * ramp
    noise = np_rng.normal(0.0, 1.0, size=len(vf2))
    drift = _smooth_1d(noise, 41)
    sd = float(np.std(drift))
    if sd > 1e-9:
        base_v = base_v + drift / sd * 0.05
    base_v = np.clip(base_v, float(global_midi) - 5.0, float(global_midi) + 5.0)
    base_v = np.where(np.isfinite(base_v), base_v, float(global_midi))

    f0_new = np.zeros(T2, dtype=np.float64)
    f0_new[vf2] = _midi_to_hz(base_v)
    f0_new, new_sp, new_ap, note_starts = _apply_tail(f0_new, new_sp, new_ap,
                                                      np.array(note_starts), args)

    y2 = pw.synthesize(f0_new, new_sp, new_ap, SR, frame_period=WORLD_FRAME_MS)
    y2 = np.asarray(y2, dtype=np.float64)
    if len(y2) == 0 or float(np.max(np.abs(y2))) <= 1e-6:
        return None, None

    # 长音符渐弱（>long_note_min 的音符最后 40% 线性降到 note_fade_db）
    fade_db = float(getattr(args, "note_fade_db", 0.0) or 0.0)
    if fade_db < -0.05:
        depth = 10.0 ** (fade_db / 20.0)
        long_min = float(getattr(args, "long_note_min", 0.5) or 0.5)
        for k in range(len(note_starts) - 1):
            d = note_starts[k + 1] - note_starts[k]
            if d < long_min:
                continue
            s0 = int(round(note_starts[k] * SR))
            s1 = min(len(y2), int(round(note_starts[k + 1] * SR)))
            if s1 - s0 < 64:
                continue
            fs = s0 + int((s1 - s0) * 0.6)
            y2[fs:s1] *= np.linspace(1.0, depth, s1 - fs)

    return y2, [float(b) for b in note_starts]


def _sing_line(y, notes_midi, global_midi, args, seed):
    """WORLD 清唱化一句：只替换 voiced 帧 f0，音高按 notes_midi 走旋律。失败抛异常。"""
    import pyworld as pw

    rng = random.Random(int(seed))
    np_rng = np.random.default_rng(int(seed))

    y = np.ascontiguousarray(np.asarray(y, dtype=np.float64))
    hop = WORLD_FRAME_MS / 1000.0
    f0, tpos = pw.harvest(y, SR, f0_floor=float(args.f0_floor),
                          f0_ceil=float(args.f0_ceil), frame_period=WORLD_FRAME_MS)
    f0 = np.ascontiguousarray(f0, dtype=np.float64)
    tpos = np.asarray(tpos, dtype=np.float64)
    if len(f0) < 4:
        return None, None
    sp = pw.cheaptrick(y, f0, tpos, SR)
    ap = pw.d4c(y, f0, tpos, SR)

    voiced = f0 > 0
    vf = np.flatnonzero(voiced)
    if len(vf) < 8:
        return None, None
    n_note = max(1, len(notes_midi))
    bounds = _calc_note_bounds(y, f0, tpos, n_note, args)

    # --- 每帧目标音高（MIDI 半音单位）
    internal = bounds[1:-1]
    note_id = np.searchsorted(internal, tpos, side="right")
    note_id = np.clip(note_id, 0, n_note - 1)

    tgt = np.asarray(notes_midi, dtype=np.float64)[note_id]
    vf_note = note_id[vf]
    orig_vf_midi = 69.0 + 12.0 * np.log2(np.maximum(f0[vf], 1e-9) / 440.0)
    blend = float(getattr(args, "blend", 1.0))
    blend_mode = getattr(args, "blend_mode", "global")
    if blend >= 0.999:
        # 旧行为：目标旋律 + 18% 原句局部起伏（±0.4 半音封顶）
        resid = np.zeros(len(vf), dtype=np.float64)
        for k in range(n_note):
            sel = vf_note == k
            if np.any(sel):
                local = orig_vf_midi[sel] - float(np.median(orig_vf_midi[sel]))
                resid[sel] = np.clip(local, -0.4, 0.4) * 0.18
        target_vf = tgt[vf] + resid
    elif blend_mode == "note":
        # 音符锚定融合：音级严格走旋律，只把原句"音符内"的自然起伏按 (1-blend) 混进来
        blend = max(0.0, min(1.0, blend))
        resid = np.zeros(len(vf), dtype=np.float64)
        for k in range(n_note):
            sel = vf_note == k
            if np.any(sel):
                local = orig_vf_midi[sel] - float(np.median(orig_vf_midi[sel]))
                resid[sel] = local
        target_vf = tgt[vf] + (1.0 - blend) * np.clip(resid, -1.5, 1.5)
    else:
        # 全轮廓融合：目标旋律与原始自然 f0 加权（先把原始轮廓按句中位对齐到旋律中位）
        blend = max(0.0, min(1.0, blend))
        orig_med = float(np.median(orig_vf_midi))
        tgt_med = float(np.median(tgt[vf]))
        target_vf = blend * tgt[vf] + (1.0 - blend) * (orig_vf_midi - orig_med + tgt_med)

    # 每个音符轻微随机音准（±4 音分）
    note_jitter = np.array([rng.uniform(-0.04, 0.04) for _ in range(n_note)])
    target_vf = target_vf + note_jitter[vf_note]

    # --- 滑音：在连续 voiced 段内对音符台阶做 40ms 平滑
    vf_time = tpos[vf]
    runs = []
    run_start = 0
    for i in range(1, len(vf)):
        if vf[i] != vf[i - 1] + 1:
            runs.append((run_start, i))
            run_start = i
    runs.append((run_start, len(vf)))
    smooth = target_vf.copy()
    win = max(3, int(round(float(args.glide_ms) / WORLD_FRAME_MS)))
    for a, b in runs:
        if b - a >= 3:
            smooth[a:b] = _smooth_1d(target_vf[a:b], win)

    # --- 颤音 5.5Hz ±20 音分（音符头尾淡入淡出，短音符按自身时长收窄，避免音头音尾跳变）
    vib_amp = max(0.0, min(2.0, float(args.vibrato))) * (VIBRATO_CENTS / 100.0)
    if vib_amp > 0:
        phase0 = rng.uniform(0.0, 2.0 * math.pi)
        vib = vib_amp * np.sin(2.0 * math.pi * VIBRATO_HZ * vf_time + phase0)
        note_start_t = bounds[:-1][vf_note]
        note_end_t = bounds[1:][vf_note]
        note_len = np.maximum(note_end_t - note_start_t, 1e-3)
        ramp_in = np.minimum(0.12, 0.30 * note_len)
        ramp_out = np.minimum(0.10, 0.25 * note_len)
        ramp = np.clip((vf_time - note_start_t) / ramp_in, 0.0, 1.0) * \
            np.clip((note_end_t - vf_time) / ramp_out, 0.0, 1.0)
        smooth = smooth + vib * ramp

    # --- 极慢微漂移（约 ±5 音分），让人声不那么像直尺
    noise = np_rng.normal(0.0, 1.0, size=len(vf))
    drift = _smooth_1d(noise, 41)
    sd = float(np.std(drift))
    if sd > 1e-9:
        drift = drift / sd * 0.05
    smooth = smooth + drift

    # --- 限制在句中位音高 ±5 半音内（契约），并避免 NaN / 越界
    smooth = np.clip(smooth, float(global_midi) - 5.0, float(global_midi) + 5.0)
    smooth = np.where(np.isfinite(smooth), smooth, float(global_midi))

    f0_new = np.zeros_like(f0)
    f0_new[vf] = _midi_to_hz(smooth)
    # 句尾轻拖音（帧域拉伸 + 末尾下滑）
    f0_new, sp, ap, bounds = _apply_tail(f0_new, sp, ap, bounds, args)
    # 简单变速对照：整句 WORLD 帧域均匀拉伸（1.0=关）
    ls = float(getattr(args, "line_stretch", 1.0) or 1.0)
    if ls > 1.0001:
        f0_new, sp, ap = _stretch_frames(f0_new, sp, ap, ls)
        bounds = np.array(bounds, dtype=np.float64) * ls
        bounds[-1] = len(f0_new) * (WORLD_FRAME_MS / 1000.0)
    y2 = pw.synthesize(f0_new, sp, ap, SR, frame_period=WORLD_FRAME_MS)
    y2 = np.asarray(y2, dtype=np.float64)
    if len(y2) == 0 or float(np.max(np.abs(y2))) <= 1e-6:
        return None, None
    return y2, [float(b) for b in bounds]


def _sing_line_librosa(y, notes_midi, global_midi, args, seed):
    """D 方案对照：不用 WORLD 重合成，按音节分段用 librosa 变调再交叉淡化拼接。"""
    import librosa
    import pyworld as pw

    hop = WORLD_FRAME_MS / 1000.0
    f0, tpos = pw.harvest(y, SR, f0_floor=float(args.f0_floor),
                          f0_ceil=float(args.f0_ceil), frame_period=WORLD_FRAME_MS)
    tpos = np.asarray(tpos, dtype=np.float64)
    n_note = max(1, len(notes_midi))
    bounds = _calc_note_bounds(y, f0, tpos, n_note, args)

    segs = []
    for k in range(n_note):
        s = int(round(float(bounds[k]) * SR))
        e = int(round(float(bounds[k + 1]) * SR))
        s = max(0, min(s, len(y) - 2))
        e = max(s + 2, min(e, len(y)))
        sel = (tpos >= bounds[k]) & (tpos < bounds[k + 1])
        vals = f0[sel]
        vals = vals[vals > 0]
        orig_m = (69.0 + 12.0 * math.log2(float(np.median(vals)) / 440.0)) if len(vals) >= 3 else global_midi
        shift = float(notes_midi[k]) - orig_m
        shift = max(-7.0, min(7.0, shift))
        seg = np.ascontiguousarray(np.asarray(y[s:e], dtype=np.float32))
        if abs(shift) >= 0.05 and len(seg) > 64:
            seg = librosa.effects.pitch_shift(seg, sr=SR, n_steps=shift).astype(np.float64)
        else:
            seg = seg.astype(np.float64)
        segs.append(seg)

    fade = max(8, int(round(0.025 * SR)))
    out = segs[0].copy()
    for seg in segs[1:]:
        nf = min(fade, len(out), len(seg))
        if nf > 2:
            w = np.linspace(0.0, 1.0, nf)
            out[-nf:] = out[-nf:] * (1.0 - w) + seg[:nf] * w
            out = np.concatenate([out, seg[nf:]])
        else:
            out = np.concatenate([out, seg])
    return out, [float(b) for b in bounds]


def _melody_pipeline(raws, lines, args):
    """返回 (processed, melody_used, debug_notes, fallback)。"""
    import pyworld as pw

    # 阶段 A：每句中位音高（加权中位数 -> 全曲人声音高中心）
    stats = []
    for y in raws:
        if y is None:
            stats.append(None)
            continue
        f0, _ = pw.harvest(y, SR, f0_floor=float(args.f0_floor),
                           f0_ceil=float(args.f0_ceil), frame_period=WORLD_FRAME_MS)
        v = f0[f0 > 0]
        stats.append((float(np.median(v)), int(len(v))) if len(v) >= 5 else None)

    valid = [s for s in stats if s is not None]
    if not valid:
        raise RuntimeError("WORLD 未能从任何句子提取到基频")
    pairs = sorted(valid, key=lambda s: s[0])
    total_w = float(sum(s[1] for s in pairs))
    acc = 0.0
    global_hz = pairs[-1][0]
    for hz, w in pairs:
        acc += w
        if acc >= total_w * 0.5:
            global_hz = hz
            break
    global_midi = _hz_to_midi(global_hz)

    melody_name = _choose_melody(args.melody, args.seed)
    phrases = melody_phrases(melody_name, args.seed)

    # 每句音符（相对主音） -> 全曲统一移调，保持调性一致
    line_notes = []
    offsets_all = []
    for i, (text, y) in enumerate(zip(lines, raws)):
        if y is None:
            line_notes.append(None)
            continue
        n_syl = count_syllables(text)
        ph = phrases[i % len(phrases)]
        offs = notes_for_line(ph, n_syl)
        line_notes.append(offs)
        offsets_all.extend(offs)
    if not offsets_all:
        raise RuntimeError("没有可处理的句子")
    shift = global_midi - float(np.median(offsets_all))

    processed = [None] * len(raws)
    debug_notes = [None] * len(raws)
    fallback = None
    for i, (y, offs) in enumerate(zip(raws, line_notes)):
        if y is None:
            processed[i] = np.zeros(int(PLACEHOLDER_SEC * SR), dtype=np.float64)
            continue
        if not offs:
            processed[i] = _read_process(y)
            fallback = "read"
            continue
        targets = [min(global_midi + 5.0, max(global_midi - 5.0, float(o) + shift)) for o in offs]
        seed_i = int(args.seed) * 7919 + i * 104729
        note_bounds = None
        try:
            if args.debug_fail_world:
                raise RuntimeError("debug: world disabled")
            if args.sing_v2:
                y2, note_bounds = _sing_line_v2(y, targets, global_midi, args, seed_i)
            elif getattr(args, "engine", "world") == "librosa":
                y2, note_bounds = _sing_line_librosa(y, targets, global_midi, args, seed_i)
            else:
                y2, note_bounds = _sing_line(y, targets, global_midi, args, seed_i)
            if y2 is None:
                raise RuntimeError("WORLD 处理后无有效音频")
        except Exception as e:
            _eprint("[sing_tts] 第 %d 句 WORLD 处理失败，本句回退 read：%r" % (i + 1, e))
            fallback = "read"
            processed[i] = _read_process(y)
            continue
        y2 = normalize_loudness(y2, TARGET_RMS_DB_MELODY, PEAK_DB, gain_lo=0.3, gain_hi=3.0)
        processed[i] = fade_edges(y2, 6.0, 15.0)
        if args.debug:
            debug_notes[i] = {
                "midi": [round(float(t), 2) for t in targets],
                "pitch_median_hz": round(float(global_hz), 2),
                "syllables": count_syllables(lines[i]),
                "blend": float(getattr(args, "blend", 1.0)),
                "blend_mode": getattr(args, "blend_mode", "global"),
                "vibrato": float(args.vibrato),
                "engine": getattr(args, "engine", "world"),
                "tail_stretch": float(getattr(args, "tail_stretch", 1.0)),
                "tail_fall": float(getattr(args, "tail_fall", 0.0)),
                "sing_v2": bool(getattr(args, "sing_v2", False)),
                "beat_scale": float(getattr(args, "beat_scale", 0.0) or 0.0),
                "line_stretch": float(getattr(args, "line_stretch", 1.0) or 1.0),
            }
            if note_bounds:
                debug_notes[i]["bounds"] = [round(float(b), 3) for b in note_bounds]
    return processed, melody_name, debug_notes, fallback


# ---------------------------------------------------------------- 主流程

def build_parser():
    p = argparse.ArgumentParser(
        prog="sing_tts.py",
        description="deepseek-live 清唱音频管线：逐句 TTS -> 清唱化(melody/read) -> 串接成一首 wav + timing json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python extras/python/sing_tts.py --lines-file _singtry/lines.json \\\n"
            "    --out _singtry/melody_demo.wav --timing-out _singtry/melody_demo.timing.json \\\n"
            "    --mode melody --melody auto --bpm 92 --vibrato 1 --gap-ms 320 --seed 123\n"
        ),
    )
    p.add_argument("--lines-file", required=True, help="歌词 JSON 数组文件（字符串或 {\"text\":..}）")
    p.add_argument("--out", required=True, help="输出 wav 路径（单声道 32kHz PCM16）")
    p.add_argument("--timing-out", required=True, help="输出 timing json 路径")
    p.add_argument("--tts-url", default=DEFAULT_TTS_URL, help="TTS 代理地址（默认 %(default)s）")
    p.add_argument("--ref-audio", default=DEFAULT_REF_AUDIO, help="参考音路径（默认内置小奶音）")
    p.add_argument("--prompt-text", nargs="?", const="", default=DEFAULT_PROMPT_TEXT,
                   help="参考音对应文本（默认=实测转写；也可只写 --prompt-text 不带值=空）")
    p.add_argument("--speed", type=float, default=0.95, help="TTS 语速（默认 0.95）")
    p.add_argument("--mode", choices=["melody", "read"], default="melody",
                   help="melody=带旋律清唱（默认）/ read=纯念唱")
    p.add_argument("--melody", choices=["auto", "xiaoxingxing", "shengri", "folk1", "folk2"], default="auto",
                   help="旋律：auto=按 seed 选（默认）/ 小星星 / 生日快乐 / 原创五声音阶 folk1、folk2")
    p.add_argument("--bpm", type=float, default=92.0, help="节拍，用于音头轻量化吸附（默认 92）")
    p.add_argument("--vibrato", type=float, default=1.0, help="颤音强度倍数（0~2，默认 1 => ±20 音分）")
    p.add_argument("--gap-ms", type=int, default=320, help="句间间隔毫秒（默认 320）")
    p.add_argument("--seed", type=int, default=123, help="随机种子（挑旋律走向/微抖动，默认 123）")
    # 进阶参数（后端一般不用传）
    p.add_argument("--blend", type=float, default=1.0, help=argparse.SUPPRESS)
    p.add_argument("--natural", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--blend-mode", choices=["global", "note"], default="global",
                   help=argparse.SUPPRESS)
    p.add_argument("--engine", choices=["world", "librosa"], default="world", help=argparse.SUPPRESS)
    p.add_argument("--tail-stretch", type=float, default=1.0, help=argparse.SUPPRESS)
    p.add_argument("--tail-fall", type=float, default=0.0, help=argparse.SUPPRESS)
    # 唱腔化 v2：按 bpm 音节时值拉伸元音 + legato + 长音颤音/渐弱
    p.add_argument("--sing-v2", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--beat-scale", type=float, default=0.75, help=argparse.SUPPRESS)
    p.add_argument("--tail-beats", type=float, default=1.8, help=argparse.SUPPRESS)
    p.add_argument("--line-stretch", type=float, default=1.0, help=argparse.SUPPRESS)
    p.add_argument("--note-fade-db", type=float, default=0.0, help=argparse.SUPPRESS)
    p.add_argument("--long-note-min", type=float, default=0.5, help=argparse.SUPPRESS)
    p.add_argument("--text-lang", default="zh", help=argparse.SUPPRESS)
    p.add_argument("--prompt-lang", default="zh", help=argparse.SUPPRESS)
    p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help=argparse.SUPPRESS)
    p.add_argument("--sample-steps", type=int, default=32, help=argparse.SUPPRESS)
    p.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE, help=argparse.SUPPRESS)
    p.add_argument("--repetition-penalty", type=float, default=DEFAULT_REP_PENALTY, help=argparse.SUPPRESS)
    p.add_argument("--timeout", type=float, default=180.0, help=argparse.SUPPRESS)
    p.add_argument("--f0-floor", type=float, default=80.0, help=argparse.SUPPRESS)
    p.add_argument("--f0-ceil", type=float, default=700.0, help=argparse.SUPPRESS)
    p.add_argument("--glide-ms", type=float, default=GLIDE_MS, help=argparse.SUPPRESS)
    p.add_argument("--quantize-ms", type=float, default=80.0, help=argparse.SUPPRESS)
    p.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--debug-fail-world", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--cache-dir", default=None, help=argparse.SUPPRESS)
    return p


def _fail(timing_path, message, started):
    payload = {
        "ok": False,
        "error": message,
        "elapsed": round(time.time() - started, 2),
        "lines": [],
    }
    write_timing(timing_path, payload)
    _eprint("[sing_tts] 失败：%s" % message)
    return 2


def main(argv=None):
    started = time.time()
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)

    # 参数兜底
    if args.natural:
        # 一键"自然清唱"预设（= audit 的 C3）：音符锚定融合、关硬量化、弱颤音、句尾轻拖音
        args.blend = 0.55
        args.blend_mode = "note"
        args.vibrato = 0.3 if float(getattr(args, "vibrato", 1.0) or 0) > 0 else 0.0
        args.quantize_ms = 0.0
        args.tail_stretch = 1.25
        args.tail_fall = 0.35
    if not (args.speed > 0):
        return _fail(args.timing_out, "--speed 必须 > 0", started)
    if not (args.bpm > 0):
        return _fail(args.timing_out, "--bpm 必须 > 0", started)
    if args.gap_ms < 0:
        return _fail(args.timing_out, "--gap-ms 不能为负", started)
    if not args.ref_audio or not os.path.exists(args.ref_audio):
        return _fail(args.timing_out, "参考音不存在：%s" % args.ref_audio, started)

    try:
        lines = load_lines(args.lines_file)
    except Exception as e:
        return _fail(args.timing_out, "读 lines 文件失败：%r" % (e,), started)

    for i, t in enumerate(lines):
        if len(t) > 24:
            _eprint("[sing_tts] 警告：第 %d 句 %d 字（>24），建议后端先拆句：%s" % (i + 1, len(t), t))

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    n = len(lines)
    raws = [None] * n
    line_errors = [None] * n
    tts_secs = 0.0

    for i, text in enumerate(lines):
        t0 = time.time()
        y, err = synth_line(text, args, opener)
        tts_secs += time.time() - t0
        raws[i] = y
        line_errors[i] = err
        if y is None:
            _eprint("[sing_tts] 第 %d/%d 句 TTS 失败（已重试 1 次），用 %.1fs 静音占位：%s"
                    % (i + 1, n, PLACEHOLDER_SEC, (err or "")[:160]))
        else:
            _eprint("[sing_tts] TTS %d/%d 完成 %.2fs" % (i + 1, n, len(y) / SR))

    if all(y is None for y in raws):
        return _fail(args.timing_out, "全部 %d 句 TTS 都失败：%s" % (n, line_errors[0]), started)

    # ---- 唱法处理
    fallback = None
    melody_used = None
    debug_notes = [None] * n
    if args.mode == "read":
        processed = [_read_process(y) if y is not None
                     else np.zeros(int(PLACEHOLDER_SEC * SR), dtype=np.float64) for y in raws]
    else:
        try:
            processed, melody_used, debug_notes, fallback = _melody_pipeline(raws, lines, args)
        except Exception as e:
            _eprint("[sing_tts] melody 管线失败，整首回退 read：%r" % (e,))
            fallback = "read"
            processed = [_read_process(y) if y is not None
                         else np.zeros(int(PLACEHOLDER_SEC * SR), dtype=np.float64) for y in raws]

    # ---- 串接（sample-accurate）
    lead_n = int(round(LEAD_SEC * SR))
    tail_n = int(round(TAIL_SEC * SR))
    gap_n = int(round(args.gap_ms / 1000.0 * SR))
    total_n = lead_n + tail_n + sum(len(p) for p in processed) + gap_n * (n - 1)
    mix = np.zeros(total_n, dtype=np.float64)
    pos = lead_n
    starts = []
    for p in processed:
        starts.append(round(pos / float(SR), 4))
        mix[pos:pos + len(p)] = p
        pos += len(p) + gap_n

    peak = float(np.max(np.abs(mix))) if len(mix) else 0.0
    if peak > 1.0:
        mix = mix / peak

    import soundfile as sf
    sf.write(args.out, np.clip(mix, -1.0, 1.0), SR, subtype="PCM_16")

    duration = round(total_n / float(SR), 3)
    timing_lines = []
    for i, p in enumerate(processed):
        item = {
            "i": i,
            "text": lines[i],
            "start": starts[i],
            "dur": round(len(p) / float(SR), 3),
        }
        if raws[i] is None:
            item["failed"] = True
        if args.debug and debug_notes[i]:
            item["notes"] = debug_notes[i]
        timing_lines.append(item)

    payload = {
        "ok": True,
        "sr": SR,
        "duration": duration,
        "tts_gen": round(tts_secs, 2),
        "fallback": fallback,
        "mode": args.mode,
        "melody": melody_used,
        "bpm": args.bpm,
        "gap_ms": args.gap_ms,
        "seed": args.seed,
        "blend": args.blend,
        "blend_mode": getattr(args, "blend_mode", "global"),
        "vibrato": args.vibrato,
        "quantize_ms": args.quantize_ms,
        "engine": getattr(args, "engine", "world"),
        "sing_v2": bool(getattr(args, "sing_v2", False)),
        "beat_scale": float(getattr(args, "beat_scale", 0.0) or 0.0),
        "line_stretch": float(getattr(args, "line_stretch", 1.0) or 1.0),
        "elapsed": round(time.time() - started, 2),
        "lines": timing_lines,
    }
    write_timing(args.timing_out, payload)
    _eprint("[sing_tts] 完成：%s  时长 %.2fs  %d 句  melody=%s fallback=%s  用时 %.2fs"
            % (args.out, duration, n, melody_used, fallback, payload["elapsed"]))
    print(json.dumps({"ok": True, "out": os.path.abspath(args.out),
                      "duration": duration, "lines": n,
                      "mode": args.mode, "melody": melody_used,
                      "fallback": fallback,
                      "elapsed": payload["elapsed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # 兜底：任何未预期异常都当作硬失败
        _eprint("[sing_tts] 未预期异常：%r" % (e,))
        import traceback
        traceback.print_exc()
        sys.exit(2)
