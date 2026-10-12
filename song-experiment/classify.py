"""Classify a song by its intro against the stored signatures.

Usage:  python3 classify.py <audio file or YouTube URL> [...]
Needs:  ffmpeg, numpy (pip install numpy), yt-dlp for URLs.

Signatures are Haitsma-Kalker binary fingerprints: one uint32 per 11.6 ms frame,
each bit = sign of the change in energy difference between adjacent bands over time.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
RATE, FRAME, HOP = 5512, 2048, 64
BANDS = np.geomspace(300, 2000, 34)
SEARCH_S = 15


def decode(src, seconds=None):
    cmd = ["ffmpeg", "-v", "error", "-i", "-" if src.startswith("http") else src]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    if src.startswith("http"):
        dl = subprocess.Popen(["yt-dlp", "-q", "-f", "ba/b", "-o", "-", src], stdout=subprocess.PIPE)
        raw = subprocess.run(cmd, stdin=dl.stdout, capture_output=True).stdout
        dl.kill()
    else:
        raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768


def fingerprint(x):
    frames = np.lib.stride_tricks.sliding_window_view(x, FRAME)[::HOP] * np.hanning(FRAME)
    power = np.abs(np.fft.rfft(frames)) ** 2
    freqs = np.fft.rfftfreq(FRAME, 1 / RATE)
    idx = np.searchsorted(freqs, BANDS)
    energy = np.add.reduceat(power, idx[:-1], axis=1)[:, :33]
    d = energy[:, :-1] - energy[:, 1:]
    bits = (d[1:] - d[:-1]) > 0
    return (bits.astype(np.uint64) << np.arange(32, dtype=np.uint64)).sum(1).astype(np.uint32)


def best_match(query, sig):
    """Lowest bit error rate of sig slid across query, and the offset in seconds."""
    n = len(sig)
    if len(query) < n:
        return 1.0, 0.0
    windows = np.lib.stride_tricks.sliding_window_view(query, n)
    ber = np.bitwise_count(windows ^ sig).sum(1) / (32 * n)
    i = int(np.argmin(ber))
    return float(ber[i]), i * HOP / RATE


def classify(src):
    data = json.loads((HERE / "signatures.json").read_text())
    query = fingerprint(decode(src, SEARCH_S))
    results = []
    for c in data["clusters"]:
        ber, at = best_match(query, np.array(c["fingerprint"], dtype=np.uint32))
        results.append((ber, at, c))
    ber, at, c = min(results, key=lambda r: r[0])
    label = c["id"] if ber <= data["max_bit_error_rate"] else "none"
    scores = "  ".join(f"{c['id']}={b:.3f}" for b, _, c in results)
    return label, at, scores


if __name__ == "__main__":
    for src in sys.argv[1:]:
        label, at, scores = classify(src)
        where = f" (intro at {at:.2f}s)" if label != "none" else ""
        print(f"{label}{where}  [{scores}]  {src}")
