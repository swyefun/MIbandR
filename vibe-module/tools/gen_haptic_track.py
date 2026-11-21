#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_haptic_track.py — Android Music-Haptics-style haptic track generator.

Replicates the essence of the AOSP HapticGenerator DSP chain (the algorithm
behind Android's music haptics, where a generated haptic waveform rides in
an audio channel of an OGG marked ANDROID_HAPTIC=1 and the player renders it
through the vibration HAL) and encodes its output into the Band 11 segment
ABI ({duration_ms, amplitude} per segment, sequential playback).

Pipeline:
  1. band-split the song with biquad IIRs:
       bass  lowpass 110 Hz   (kick / bassline -> the PULSE carrier)
       mid   bandpass 200-2000 Hz (vocals/synth -> texture)
       high  highpass 3000 Hz (percussion transients -> sharp clicks)
  2. per-band RMS envelope in 10 ms frames, each normalized to its own
     90th percentile,
  3. haptic amplitude = weighted mix, bass-driven (0.65*bass + 0.35*mid)
     shaped by loud^0.8, THEN multiplied by the full-band envelope so the
     vibration follows the song's overall dynamics,
  4. percussion accents: high-band onset (energy jump ratio > 2.2, level
     > 0.15) -> 20 ms FULL-amplitude click, min 90 ms apart,
  5. quantize to 32 levels, merge runs, fold micro-gaps < 10 ms,
     trim leading/trailing silence.

Output arrays appended to vibe-module/src/vibe_music.h:
  s_TRACK_MAN2_FULL  - whole track (10 ms frames)
  s_TRACK_MAN2_CLIP  - 20..56 s chorus at 8 ms frames
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from wav_to_vibe import read_wav_mono  # noqa: E402

FRAME_MS = 15
FRAME_MS_CLIP = 10
FULL_LEVELS = 18
CLIP_LEVELS = 20
CLIP_START_S = 20.0
CLIP_END_S = 56.0
GAMMA = 0.8
SILENCE = 0.05


# ---------------- biquad IIR (RBJ cookbook, direct form 1) ----------------

def biquad_coeffs(kind, fs, f0, q=0.707):
    w0 = 2.0 * math.pi * f0 / fs
    alpha = math.sin(w0) / (2.0 * q)
    cw = math.cos(w0)
    if kind == "lp":
        b0 = (1 - cw) / 2; b1 = 1 - cw; b2 = (1 - cw) / 2
    elif kind == "hp":
        b0 = (1 + cw) / 2; b1 = -(1 + cw); b2 = (1 + cw) / 2
    elif kind == "bp":
        b0 = alpha; b1 = 0; b2 = -alpha
    else:
        raise ValueError(kind)
    a0 = 1 + alpha; a1 = -2 * cw; a2 = 1 - alpha
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def biquad(x, c):
    b0, b1, b2, a1, a2 = c
    y = [0.0] * len(x)
    x1 = x2 = y1 = y2 = 0.0
    for i, v in enumerate(x):
        yi = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2 = x1; x1 = v; y2 = y1; y1 = yi
        y[i] = yi
    return y


def bandpass(x, fs, f_lo, f_hi):
    y = biquad(x, biquad_coeffs("hp", fs, f_lo, 0.8))
    return biquad(y, biquad_coeffs("lp", fs, f_hi, 0.8))


# ---------------- envelope ----------------

def rms_frames(x, frame_n):
    env = []
    peak = 1e-9
    for i in range(0, len(x) - frame_n + 1, frame_n):
        s = 0.0
        for v in x[i:i + frame_n]:
            s += v * v
        r = math.sqrt(s / frame_n)
        env.append(r)
        if r > peak:
            peak = r
    return [e / peak for e in env]


def pnorm(env, pct=0.90):
    es = sorted(v for v in env if v > SILENCE)
    if not es:
        return env
    ref = es[int(len(es) * pct)]
    return [min(1.0, v / ref) for v in env]


def combine(bass, mid, high, full):
    """Bass-driven haptic amplitude per frame (Android-style: bass carries
    the pulse, mids add texture, full-band dynamics gate the output)."""
    out = []
    n = min(len(bass), len(mid), len(full))
    for i in range(n):
        pulse = 0.65 * bass[i] ** GAMMA + 0.35 * mid[i] ** GAMMA
        body = 0.35 + 0.65 * full[i] ** GAMMA
        a = pulse * body
        out.append(min(1.0, a))
    return out


# ---------------- segment encoding ----------------

def detect_clicks(high_e, frame_ms):
    """Percussion transients from the HIGH band: large frame-to-frame energy
    jump. Returns [(frame_idx, amp, n_frames), ...] with a 90 ms spacing. A
    click is 2 frames (full amplitude) so it stays a sharp hit."""
    min_gap = max(1, round(90.0 / frame_ms))
    clicks = []
    last = -10 ** 9
    for i in range(1, len(high_e)):
        if (high_e[i] - high_e[i - 1] > 0.22 and high_e[i] > 0.18
                and i - last >= min_gap):
            clicks.append((i, 255, 2))
            last = i
    return clicks


def stream_to_segments(amps, frame_ms):
    segs = []
    for a in amps:
        if segs and segs[-1][1] == a:
            segs[-1][0] += frame_ms
        else:
            segs.append([frame_ms, a])
    return segs


def env_to_stream(env, clicks, frame_ms, levels):
    """Frame-level amplitude stream: quantized haptic envelope with full-amp
    click frames overlaid at percussion onsets."""
    step = 255.0 / levels
    amps = []
    for e in env:
        if e < SILENCE:
            q = 0
        else:
            q = int(max(1, min(255, round(255.0 * (e ** GAMMA) / step) * step)))
        amps.append(q)
    for (j, amp, n) in clicks:
        for k in range(n):
            if j + k < len(amps):
                amps[j + k] = max(amps[j + k], amp)
    return amps


def trim(segs):
    while segs and segs[-1][1] == 0:
        segs.pop()
    while segs and segs[0][1] == 0:
        segs.pop(0)
    return segs


def emit(f, macro, segs, name):
    total = sum(s[0] for s in segs)
    print("%s: %d segments, %.1f s, %d bytes" %
          (macro, len(segs), total / 1000.0, len(segs) * 6))
    f.write("/* %s - %d segments, %.1f s (Android-Haptics-style, "
            "tools/gen_haptic_track.py) */\n" % (name, len(segs), total / 1000.0))
    f.write("static const struct vibe_segment s_TRACK_%s[] = {\n" % macro)
    for d, a in segs:
        f.write("    {0u, %du, 0u, %du},\n" % (d, a))
    f.write("};\n")
    f.write("#define TRACK_%s_COUNT (sizeof(s_TRACK_%s) / sizeof(s_TRACK_%s[0]))\n\n"
            % (macro, macro, macro))


def build(wav_path):
    samples, rate = read_wav_mono(wav_path)
    print("read %d samples @ %d Hz (%.1f s)" %
          (len(samples), rate, len(samples) / rate))
    print("band-splitting (pure python IIR, may take ~1 min)...")
    bass = bandpass(samples, rate, 25, 110)
    mid = bandpass(samples, rate, 200, 2000)
    high = bandpass(samples, rate, 3000, rate // 2 - 500)
    print("envelopes...")
    fb = max(1, round(rate * FRAME_MS / 1000.0))
    bass_e = pnorm(rms_frames(bass, fb))
    mid_e = pnorm(rms_frames(mid, fb))
    high_e = pnorm(rms_frames(high, fb))
    full_e = pnorm(rms_frames(samples, fb))
    haptic = combine(bass_e, mid_e, high_e, full_e)

    hdr = os.path.normpath(os.path.join(HERE, "..", "src", "vibe_music.h"))
    text = open(hdr, encoding="utf-8").read()
    marker = "#endif /* VIBE_MUSIC_H */"
    head = text[:text.index(marker)].rstrip("\n")
    if "s_TRACK_MAN2_FULL" in head:
        sys.exit("MAN2 tracks already present; regenerate vibe_music.h cleanly first.")

    def seg_env(env, high, frame_ms, levels):
        clicks = detect_clicks(high, frame_ms)
        print("  clicks: %d" % len(clicks))
        amps = env_to_stream(env, clicks, frame_ms, levels)
        return trim(stream_to_segments(amps, frame_ms))

    with open(hdr, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + "\n\n")
        print("full track:")
        segs = seg_env(haptic, high_e, FRAME_MS, FULL_LEVELS)
        emit(f, "MAN2_FULL", segs, "Man full, haptic DSP")

        i0 = int(CLIP_START_S * rate)
        i1 = min(len(samples), int(CLIP_END_S * rate))
        fr_c = max(1, round(rate * FRAME_MS_CLIP / 1000.0))
        b2 = pnorm(rms_frames(bass[i0:i1], fr_c))
        m2 = pnorm(rms_frames(mid[i0:i1], fr_c))
        f2 = pnorm(rms_frames(samples[i0:i1], fr_c))
        h2 = combine(b2, m2, [0.0] * len(b2), f2)
        c2 = pnorm(rms_frames(high[i0:i1], fr_c))
        print("clip 20-56s:")
        segs2 = seg_env(h2, c2, FRAME_MS_CLIP, CLIP_LEVELS)
        emit(f, "MAN2_CLIP", segs2, "Man chorus 20-56s, haptic DSP")
        f.write(marker + "\n")
    print("header updated:", hdr)


if __name__ == "__main__":
    wav = os.path.normpath(os.path.join(HERE, "..", "..", "man_mono.wav"))
    build(wav)
