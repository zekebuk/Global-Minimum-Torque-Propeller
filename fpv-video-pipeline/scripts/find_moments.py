#!/usr/bin/env python3
"""Measure the motion through candidate moments, so a cut can put a shot's peak on a beat and match direction of travel.

    python scripts/find_moments.py candidates.json --out moments.json

candidates.json: [{"id": 11, "label": "...", "src": path, "from": seconds, "to": seconds}, ...]
For each range: motion per frame at 10 Hz (mean change at 160 px), smoothed over 0.3 s, and the direction the picture travels
(phase correlation). Writes the peak (source seconds), how hard it hits against the range's median, and the travel
direction at the peak, in degrees (0 = picture moves right, 90 = picture moves down).
"""
from __future__ import annotations

import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

HZ, W, H = 10, 160, 120


def frames(src: str, start: float, dur: float) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src,
                          "-vf", f"fps={HZ},scale={W}:{H},format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)


def shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Translation from a to b in pixels, by phase correlation."""
    f = np.fft.fft2(a - a.mean()) * np.conj(np.fft.fft2(b - b.mean()))
    c = np.fft.ifft2(f / (np.abs(f) + 1e-6)).real
    y, x = np.unravel_index(np.argmax(c), c.shape)
    return float(x - W if x > W / 2 else x), float(y - H if y > H / 2 else y)


def measure(c: dict) -> dict:
    a = frames(c["src"], c["from"], c["to"] - c["from"])
    if len(a) < 4:
        return {**c, "error": "no frames"}
    e = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))
    k = max(1, int(HZ * 0.3))
    s = np.convolve(e, np.ones(k) / k, mode="same")
    i = int(np.argmax(s))
    lo, hi = max(0, i - 2), min(len(a) - 1, i + 2)
    moves = [shift(a[j], a[j + 1]) for j in range(lo, hi)]
    dx, dy = np.mean(moves, axis=0)
    return {**c, "peak_s": round(c["from"] + (i + 1) / HZ, 2), "peak": round(float(s[i]), 2), "median": round(float(np.median(e)), 2),
            "punch": round(float(s[i] / max(np.median(e), .1)), 2), "dir": round(float(np.degrees(np.arctan2(dy, dx))) % 360),
            "curve": [round(float(v), 1) for v in s[::2]]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(measure, json.loads(a.candidates.read_text())))
    a.out.write_text(json.dumps(res, indent=1))
    for r in sorted((r for r in res if "peak" in r), key=lambda r: -r["peak"]):
        print(f"{str(r['id']):>4} {r['label'][:30]:30} peak {r['peak_s']:7.2f}s  motion {r['peak']:5.1f}  punch x{r['punch']:4.1f}  dir {r['dir']:3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
