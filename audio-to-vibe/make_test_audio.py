#!/usr/bin/env python3
"""Generate a short WAV test tone with a clear rhythm envelope.

Pure stdlib. Produces a 16-bit mono PCM WAV suitable for audio_to_vibration.py.

Usage:
    python make_test_audio.py [output.wav]

Default pattern (rhythm that maps well to a narrow-band LRA):
    0.00-0.15s  strong beat  (amp 0.9)
    0.20-0.30s  weak beat    (amp 0.4)
    0.40-0.55s  strong beat
    0.60-0.70s  weak beat
    0.80-1.10s  long sustained note with fade
    1.15-1.25s  final click
"""
import math
import struct
import sys
import wave

SAMPLE_RATE = 16000
DURATION_S = 1.4
CARRIER_HZ = 256.0  # same as the band motor f0, so the tone itself is meaningful

# (start, end, amplitude) segments; silence in between
SEGMENTS = [
    (0.00, 0.15, 0.90),
    (0.20, 0.30, 0.40),
    (0.40, 0.55, 0.90),
    (0.60, 0.70, 0.40),
    (0.80, 1.10, 0.70),
    (1.15, 1.25, 0.85),
]


def envelope_at(t):
    for start, end, amp in SEGMENTS:
        if start <= t < end:
            # short fade-in/out to avoid clicks, but keep the envelope flat-ish
            edge = min(0.01, (end - start) / 4)
            if t - start < edge:
                return amp * (t - start) / edge
            if end - t < edge:
                return amp * (end - t) / edge
            return amp
    return 0.0


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'out/test_rhythm.wav'
    n = int(SAMPLE_RATE * DURATION_S)
    frames = bytearray()
    for i in range(n):
        t = i / SAMPLE_RATE
        env = envelope_at(t)
        # a bit of 2nd harmonic so it is not a pure sine
        sample = env * (0.8 * math.sin(2 * math.pi * CARRIER_HZ * t)
                        + 0.2 * math.sin(2 * math.pi * 2 * CARRIER_HZ * t))
        pcm = max(-32768, min(32767, int(sample * 32767)))
        frames.extend(struct.pack('<h', pcm))

    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(frames))
    print('wrote %s (%d frames, %.2fs @ %d Hz)'
          % (out, n, DURATION_S, SAMPLE_RATE))


if __name__ == '__main__':
    main()
