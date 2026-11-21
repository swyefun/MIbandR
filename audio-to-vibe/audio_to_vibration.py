#!/usr/bin/env python3
"""Convert an audio file to a vibration pattern for the Band 11 haptics API.

Pure Python stdlib (wave/array/math) so it runs on phone (Termux) as well as PC.

Pipeline
--------
WAV (16-bit mono) -> RMS envelope per frame -> threshold gate -> run-length
encode ON/gap durations -> quantize to 10 ms -> split into <=16-item patterns
-> emit C arrays + JSON + ASCII preview.

Why envelope only: the Band 11 LRA is a narrow-band (~256 Hz) resonator whose
drive frequency is firmware-locked. It cannot reproduce pitch or waveform; only
rhythm and loudness envelope survive. Amplitude is not exposed by the module
haptics API either (vibrate_pattern takes only ON/gap durations), so loudness is
encoded as pulse duration (longer ON = perceived stronger).

Usage
-----
    python audio_to_vibration.py input.wav -o out/
    python audio_to_vibration.py input.wav --threshold 0.12 --frame-ms 20

Output files in -o directory:
    vibration_pattern.h   C arrays of uint16_t, ready to paste into a module
    vibration_pattern.json machine-readable pattern + metadata
    preview.txt           ASCII plot of envelope and resulting pattern
"""
import argparse
import array
import json
import math
import os
import struct
import sys
import wave

# Canopus haptics API hard limits (sdk/c/canopus_services.h)
PATTERN_MAX = 16          # CANOPUS_HAPTIC_PATTERN_MAX
PULSE_MAX_MS = 5000       # CANOPUS_HAPTIC_MAX_MS
QUANTUM_MS = 10           # API has 10 ms resolution


def read_wav(path):
    """Return (sample_rate, mono float samples in [-1,1])."""
    with wave.open(path, 'rb') as w:
        if w.getsampwidth() != 2:
            raise SystemExit('only 16-bit PCM WAV supported')
        sr = w.getframerate()
        ch = w.getnchannels()
        raw = w.readframes(w.getnframes())
    pcm = array.array('h')
    pcm.frombytes(raw)
    if sys.byteorder != 'little':
        pcm.byteswap()
    if ch == 1:
        samples = pcm
    else:
        # downmix by averaging channels
        samples = array.array('h', [sum(pcm[i:i + ch]) // ch
                                    for i in range(0, len(pcm), ch)])
    return sr, [s / 32768.0 for s in samples]


def rms_envelope(samples, sr, frame_ms):
    """RMS per frame -> list of (timestamp_ms, rms)."""
    win = max(1, int(sr * frame_ms / 1000))
    env = []
    for i in range(0, len(samples), win):
        block = samples[i:i + win]
        if not block:
            break
        rms = math.sqrt(sum(s * s for s in block) / len(block))
        env.append((i * 1000 // sr, rms))
    return env


def gate_and_encode(env, frame_ms, threshold, min_gap_ms):
    """Threshold the envelope and run-length encode into ON/gap runs.

    Returns list of (is_on, duration_ms). Adjacent ON runs separated by a gap
    shorter than min_gap_ms are merged (keeps a rhythm steady).
    """
    steps = [rms >= threshold for _, rms in env]
    # run-length encode
    runs = []
    cur = steps[0]
    count = 1
    for s in steps[1:]:
        if s == cur:
            count += 1
        else:
            runs.append((cur, count * frame_ms))
            cur, count = s, 1
    runs.append((cur, count * frame_ms))

    # merge short gaps (< min_gap_ms) so a beat is not chopped by a tiny dip
    merged = []
    for is_on, ms in runs:
        if (not is_on and ms < min_gap_ms and merged and merged[-1][0]):
            # absorb into the preceding ON run, then let following ON merge
            prev_on, prev_ms = merged.pop()
            merged.append((True, prev_ms + ms))
        elif (not is_on and ms < min_gap_ms and merged):
            # short gap between two gaps: just drop it into previous by extending
            prev_on, prev_ms = merged[-1]
            merged[-1] = (prev_on, prev_ms + ms)
        else:
            merged.append((is_on, ms))
    # coalesce consecutive same-state runs left after merging
    coalesced = []
    for is_on, ms in merged:
        if coalesced and coalesced[-1][0] == is_on:
            coalesced[-1] = (is_on, coalesced[-1][1] + ms)
        else:
            coalesced.append((is_on, ms))
    return coalesced


def quantize(runs):
    """Round durations up to QUANTUM_MS, clamp to PULSE_MAX_MS."""
    out = []
    for is_on, ms in runs:
        q = max(QUANTUM_MS, ((ms + QUANTUM_MS - 1) // QUANTUM_MS) * QUANTUM_MS)
        q = min(PULSE_MAX_MS, q)
        out.append((is_on, q))
    return out


def split_patterns(runs, max_items=PATTERN_MAX):
    """Split the ON/gap run stream into chunks of at most max_items entries.

    Each chunk starts with an ON run (drop leading gaps). If a chunk would end
    on a gap, keep it — the API treats trailing gaps harmlessly.
    """
    # drop leading silence
    while runs and not runs[0][0]:
        runs = runs[1:]
    patterns = []
    while runs:
        chunk, runs = runs[:max_items], runs[max_items:]
        vals = [ms for _, ms in chunk]
        if not vals or sum(vals) == 0:
            break
        patterns.append(vals)
    return patterns


def ascii_preview(env, threshold, patterns, width=64):
    lines = ['RMS envelope (threshold = %.4f):' % threshold]
    if env:
        peak = max(rms for _, rms in env) or 1.0
        buckets = []
        per = max(1, len(env) // width)
        for i in range(0, len(env), per):
            block = env[i:i + per]
            buckets.append(max(rms for _, rms in block))
        for idx, rms in enumerate(buckets):
            t = env[min(idx * per, len(env) - 1)][0]
            bar = '#' * int(round(rms / peak * 40))
            lines.append('%6dms |%-40s' % (t, bar))
    lines.append('')
    lines.append('Vibration patterns (ON/gap durations in ms, %d items max):'
                 % PATTERN_MAX)
    for i, p in enumerate(patterns):
        lines.append('  pattern[%d] (%2d items): {%s}'
                     % (i, len(p), ', '.join(str(v) for v in p)))
        total = sum(p)
        lines.append('    timeline: ' + ' '.join(
            ('ON %d' if k % 2 == 0 else 'off %d') % v
            for k, v in enumerate(p)) + '  (total %d ms)' % total)
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input', help='input WAV (16-bit PCM, mono or stereo)')
    ap.add_argument('-o', '--outdir', default='out', help='output directory')
    ap.add_argument('--frame-ms', type=int, default=10,
                    help='RMS analysis frame in ms (default 10)')
    ap.add_argument('--threshold', type=float, default=None,
                    help='absolute RMS gate; default = 0.15 * peak')
    ap.add_argument('--relative', type=float, default=0.15,
                    help='threshold as fraction of peak RMS (default 0.15)')
    ap.add_argument('--min-gap-ms', type=int, default=20,
                    help='gaps shorter than this are merged into the beat')
    args = ap.parse_args()

    sr, samples = read_wav(args.input)
    env = rms_envelope(samples, sr, args.frame_ms)
    if not env:
        raise SystemExit('empty audio')
    peak = max(rms for _, rms in env)
    threshold = args.threshold if args.threshold is not None else args.relative * peak
    runs = gate_and_encode(env, args.frame_ms, threshold, args.min_gap_ms)
    runs = quantize(runs)
    patterns = split_patterns(runs)

    os.makedirs(args.outdir, exist_ok=True)
    patterns_h = ['/* Generated by audio_to_vibration.py -- paste into a Canopus module. */',
                  '/* Each uint16_t alternates ON, gap, ON, gap, ... */']
    for i, p in enumerate(patterns):
        patterns_h.append('static const uint16_t vibe_pattern_%d[] = {%s};'
                          % (i, ', '.join(str(v) for v in p)))
        patterns_h.append('#define VIBE_PATTERN_%d_LEN %d' % (i, len(p)))
    h_path = os.path.join(args.outdir, 'vibration_pattern.h')
    with open(h_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(patterns_h) + '\n')

    payload = {
        'source': os.path.basename(args.input),
        'sample_rate': sr,
        'duration_ms': int(len(samples) * 1000 / sr),
        'frame_ms': args.frame_ms,
        'peak_rms': round(peak, 6),
        'threshold': round(threshold, 6),
        'min_gap_ms': args.min_gap_ms,
        'patterns': patterns,
        'api_limits': {'max_items': PATTERN_MAX, 'max_pulse_ms': PULSE_MAX_MS,
                       'resolution_ms': QUANTUM_MS},
    }
    j_path = os.path.join(args.outdir, 'vibration_pattern.json')
    with open(j_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    preview = ascii_preview(env, threshold, patterns)
    p_path = os.path.join(args.outdir, 'preview.txt')
    with open(p_path, 'w', encoding='utf-8') as f:
        f.write(preview + '\n')

    print('peak RMS = %.4f, threshold = %.4f, %d pattern(s)'
          % (peak, threshold, len(patterns)))
    print(preview)
    print('\nwrote %s, %s, %s' % (h_path, j_path, p_path))


if __name__ == '__main__':
    main()
