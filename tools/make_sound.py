"""Synthesizes ded.m4a, the DedNotch welcome chime (original sound, no samples).

A soft bell-like arpeggio (Cmaj9: C5 E5 G5 B5 D6) over a warm pad, with a light
echo tail. Requires numpy and ffmpeg.

usage: python3 tools/make_sound.py
"""
import os
import subprocess
import tempfile
import wave

import numpy as np

OUT = os.path.join(os.path.dirname(__file__), "..", "dednotch", "assets", "ded.m4a")

RATE = 44100
LENGTH = 3.2  # seconds


def note_freq(semitones_from_a4):
    return 440.0 * 2 ** (semitones_from_a4 / 12.0)


def bell(freq, dur, t0, buf):
    """Additive bell: inharmonic partials with individual exponential decays."""
    n = int(dur * RATE)
    t = np.arange(n) / RATE
    partials = [(1.0, 1.0, 2.2), (2.0, 0.35, 3.5), (3.01, 0.12, 5.0), (4.2, 0.05, 7.0)]
    tone = np.zeros(n)
    for ratio, amp, decay in partials:
        tone += amp * np.sin(2 * np.pi * freq * ratio * t) * np.exp(-decay * t)
    attack = np.minimum(1.0, t / 0.008)
    start = int(t0 * RATE)
    end = min(len(buf), start + n)
    buf[start:end] += (tone * attack)[: end - start]


def pad(freqs, buf):
    t = np.arange(len(buf)) / RATE
    env = np.minimum(1.0, t / 0.6) * np.exp(-1.1 * np.maximum(0.0, t - 0.6))
    for f in freqs:
        buf += 0.06 * env * (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * f * 2.003 * t))


def echo(buf, delay=0.19, feedback=0.32, taps=4):
    out = buf.copy()
    d = int(delay * RATE)
    for i in range(1, taps + 1):
        out[d * i:] += buf[: len(buf) - d * i] * feedback ** i
    return out


def main():
    buf = np.zeros(int(LENGTH * RATE))
    # C5, E5, G5, B5, D6 relative to A4
    for i, semi in enumerate([3, 7, 10, 14, 17]):
        bell(note_freq(semi), 2.4, 0.03 + i * 0.085, buf)
    pad([note_freq(-21), note_freq(-14)], buf)  # C3 + G3
    buf = echo(buf)
    fade = np.ones_like(buf)
    fl = int(0.5 * RATE)
    fade[-fl:] = np.linspace(1.0, 0.0, fl)
    buf *= fade
    buf = 0.85 * buf / np.max(np.abs(buf))
    stereo = np.stack([buf, np.roll(buf, int(0.012 * RATE)) * 0.97], axis=1)
    pcm = (stereo * 32767).astype("<i2")

    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "ded.wav")
        with wave.open(wav, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm.tobytes())
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-c:a", "aac", "-b:a", "128k",
                        "-map_metadata", "-1", OUT], check=True)
    print("wrote", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
