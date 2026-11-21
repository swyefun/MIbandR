#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_musicvibe_track.py — faithful offline port of the MusicVibe
(https://github.com/Hong-agent/MusicVibe) audio->haptics chain, adapted to
the Band 11 segment ABI.

MusicVibe signal chain (per 10 ms block @ 48 kHz):
  mix -> lowpass 150 Hz -> FULL-WAVE RECTIFY (creates 2nd harmonic, moving
  inaudible bass into the LRA's responsive band) -> highpass 100 / lowpass
  1150 (cascade 2) -> block RMS -> asymmetric envelope (rise 1.0 / release
  0.5) -> AGC normalize (attack 0.35 / release 0.004, relative to the
  song's own peak) -> gamma 2.2 (SUPPRESS background, only beats vibrate)
  -> amplitude; zero-crossing rate -> pulse duty ("freq feel": low freq =
  sustained hum, high freq = chattery ticks).

Why our previous attempts felt bad (now fixed):
  - gamma 0.6-0.8 BOOSTED quiet sections -> constant mush. MusicVibe uses
    gamma 2.2: AGC already fills the dynamic range, so >1 gamma is what
    keeps background silent and beats punchy.
  - plain band envelopes missed the rectification trick.
  - no frequency-feel dimension.

ffmpeg does the IIR filtering (fast), numpy does the rest.

Outputs appended to vibe-module/src/vibe_music.h:
  s_TRACK_MAN3_FULL - whole track
  s_TRACK_MAN3_CLIP - 20..56 s chorus
"""
import os
import subprocess
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
FS = 48000
CHUNK = 480                      # 10 ms blocks
ENV_RISE, ENV_RELEASE = 1.0, 0.5
AGC_ATTACK, AGC_RELEASE = 0.35, 0.004
AMP_GAMMA = 2.2
MUSIC_GAIN = 1.2
FREQ_ZERO_RATE_REF = 0.25
FREQ_SMOOTH = 0.25
DUTY_WINDOW = 6                  # 60 ms modulation window for the freq feel
AMP_GATE = 10                    # below this -> silence


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("ffmpeg failed: %s\n%s" % (cmd, r.stderr[-500:]))


def read_wav(path):
    w = wave.open(path, "rb")
    assert w.getframerate() == FS and w.getnchannels() == 1
    data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    w.close()
    return data.astype(np.float64) / 32768.0


def write_wav(path, x):
    x16 = np.clip(x * 32767.0, -32768, 32767).astype(np.int16)
    w = wave.open(path, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(FS)
    w.writeframes(x16.tobytes())
    w.close()


def process(mix_wav):
    """Returns per-block (amp 0..255, duty 0..1) arrays."""
    bass = os.path.join(ROOT, "mv_bass.wav")
    rect = os.path.join(ROOT, "mv_rect.wav")
    band = os.path.join(ROOT, "mv_band.wav")

    # 1. lowpass 150 Hz (harmonic source)
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", mix_wav,
        "-af", "lowpass=f=150", "-ac", "1", "-ar", str(FS),
        "-acodec", "pcm_s16le", bass])
    y = read_wav(bass)
    print("  lowpass 150: %d samples" % len(y))

    # 2. full-wave rectification (2nd harmonic generation)
    write_wav(rect, np.abs(y))

    # 3. band-pass 100..1150 Hz, cascade 2 (remove DC, limit band)
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", rect,
        "-af", "highpass=f=100,lowpass=f=1150,highpass=f=100,lowpass=f=1150",
        "-ac", "1", "-ar", str(FS), "-acodec", "pcm_s16le", band])
    b = read_wav(band)
    print("  band-pass: %d samples" % len(b))

    n_blocks = len(b) // CHUNK
    B = b[:n_blocks * CHUNK].reshape(n_blocks, CHUNK)
    rms = np.sqrt((B ** 2).mean(axis=1))
    zcr = ((B[:, :-1] >= 0) != (B[:, 1:] >= 0)).mean(axis=1)

    # 4. asymmetric envelope (fast attack, slower release)
    env = np.zeros(n_blocks)
    e = 0.0
    for i in range(n_blocks):
        t = rms[i]
        e += (t - e) * (ENV_RISE if t > e else ENV_RELEASE)
        env[i] = e

    # 5. AGC: relative to the song's own slowly-decaying peak
    agc = np.zeros(n_blocks)
    p = 0.15
    for i in range(n_blocks):
        p += (env[i] - p) * (AGC_ATTACK if env[i] > p else AGC_RELEASE)
        p = max(p, 1e-4)
        agc[i] = p
    norm = np.clip(env / agc, 0.0, 1.0)

    # 6. gamma 2.2: keep background silent, beats punchy
    shaped = norm ** AMP_GAMMA
    amp = np.minimum(255.0, shaped * MUSIC_GAIN * 255.0)
    amp[amp < AMP_GATE] = 0.0

    # 7. zero-crossing rate -> normalized frequency -> pulse duty
    norm_freq = np.clip(zcr / FREQ_ZERO_RATE_REF, 0.0, 1.0)
    freq_sm = np.zeros(n_blocks)
    fs_m = 0.5
    for i in range(n_blocks):
        fs_m += (norm_freq[i] - fs_m) * FREQ_SMOOTH
        freq_sm[i] = min(1.0, max(0.0, fs_m))
    duty = 1.0 - (1.0 - 1.0 / DUTY_WINDOW) * freq_sm

    for pth in (bass, rect, band):
        os.remove(pth)
    return amp, duty


def render_segments(amp, duty, block_ms=10, levels=16, group=2):
    """Frame stream -> segments.

    - amplitude quantized to `levels` steps (adjacent equal blocks merge),
    - blocks pooled into `group`-sized units (max, peak-preserving) so the
      segment count stays within the firmware budget,
    - high-frequency sections are duty-chopped at the unit level inside a
      DUTY_WINDOW-unit window (sustained hum vs chattery ticks)."""
    step = 255.0 / levels
    n = len(amp)
    ng = n // group
    if ng < 1:
        return []
    A = np.round(amp[:ng * group] / step) * step
    A = A.reshape(ng, group).max(axis=1)
    D = duty[:ng * group].reshape(ng, group)[:, 0]

    W = DUTY_WINDOW
    amps = A.copy()
    for w0 in range(0, ng - W + 1, W):
        on = int(round(D[w0] * W))
        if on >= W:
            continue                      # sustained
        amps[w0 + on:w0 + W] = 0.0        # off part of the duty cycle

    segs = []
    unit = block_ms * group
    for a in amps:
        a = int(round(a))
        if segs and segs[-1][1] == a:
            segs[-1][0] += unit
        else:
            segs.append([unit, a])
    while segs and segs[-1][1] == 0:
        segs.pop()
    while segs and segs[0][1] == 0:
        segs.pop(0)
    return segs


def emit(f, macro, segs, name):
    total = sum(s[0] for s in segs)
    print("%s: %d segments, %.1f s, %d bytes" %
          (macro, len(segs), total / 1000.0, len(segs) * 6))
    f.write("/* %s - %d segments, %.1f s (MusicVibe DSP port, "
            "tools/gen_musicvibe_track.py) */\n" % (name, len(segs), total / 1000.0))
    f.write("static const struct vibe_segment s_TRACK_%s[] = {\n" % macro)
    for d, a in segs:
        f.write("    {0u, %du, 0u, %du},\n" % (d, a))
    f.write("};\n")
    f.write("#define TRACK_%s_COUNT (sizeof(s_TRACK_%s) / sizeof(s_TRACK_%s[0]))\n\n"
            % (macro, macro, macro))


def main():
    mix = os.path.join(ROOT, "man_mono.wav")
    if not os.path.exists(mix):
        # make the 48 kHz mono mix from Man.wav
        sh(["ffmpeg", "-y", "-loglevel", "error",
            "-i", os.path.join(ROOT, "Man.wav"),
            "-ac", "1", "-ar", str(FS), "-acodec", "pcm_s16le", mix])

    hdr = os.path.normpath(os.path.join(HERE, "..", "src", "vibe_music.h"))
    text = open(hdr, encoding="utf-8").read()
    marker = "#endif /* VIBE_MUSIC_H */"
    head = text[:text.index(marker)].rstrip("\n")
    if "s_TRACK_MAN3_FULL" in head:
        sys.exit("MAN3 tracks already present; regenerate vibe_music.h first.")

    CLIP_START, CLIP_END = 20.0, 56.0

    print("full track:")
    amp, duty = process(mix)
    segs = render_segments(amp, duty, block_ms=10, levels=20, group=2)

    with open(hdr, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + "\n\n")
        emit(f, "MAN3_FULL", segs, "Man full, MusicVibe DSP")

        i0 = int(CLIP_START * FS)
        i1 = int(CLIP_END * FS)
        print("clip 20-56s:")
        segs2 = render_segments(amp[i0 // CHUNK:i1 // CHUNK],
                                duty[i0 // CHUNK:i1 // CHUNK],
                                block_ms=10, levels=20, group=2)
        emit(f, "MAN3_CLIP", segs2, "Man chorus 20-56s, MusicVibe DSP")
        f.write(marker + "\n")
    print("header updated:", hdr)


if __name__ == "__main__":
    main()
