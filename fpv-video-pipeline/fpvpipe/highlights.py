"""Score FPV moments from GoPro gyro + accelerometer telemetry.

Assumption (first principles): what reads as "epic" in FPV is mostly physical and is visible in the IMU.
  * flips / rolls / power loops -> large integrated rotation on one axis in a short window
  * aggressive, snappy flying   -> high peak angular rate
  * dives, hang time, throttle cuts -> the camera is near free fall, so |accel| drops well below 1 g
Composition (a gap, a building, a sunset) is NOT visible here; treat the output as a shortlist to review,
not a final selection.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

G = 9.80665


@dataclass
class Highlight:
    start_s: float
    end_s: float
    score: float
    rotation_deg: float
    peak_rate_dps: float
    freefall_frac: float
    label: str


def _window_bounds(t: np.ndarray, start: float, end: float) -> slice:
    return slice(int(np.searchsorted(t, start)), int(np.searchsorted(t, end)))


def score_windows(t_gyro, gyro, t_acc, acc, window_s=3.0, step_s=0.5) -> list[Highlight]:
    if len(t_gyro) < 2:
        return []
    rate_dps = np.degrees(gyro)
    dt = np.diff(t_gyro, append=t_gyro[-1] + np.median(np.diff(t_gyro)))
    acc_g = np.linalg.norm(acc, axis=1) / G if len(acc) else np.ones(0)

    out = []
    duration = float(t_gyro[-1])
    for start in np.arange(0.0, max(duration - window_s, 0.0) + 1e-9, step_s):
        end = start + window_s
        gs = _window_bounds(t_gyro, start, end)
        if gs.stop - gs.start < 4:
            continue
        r = rate_dps[gs]
        # Largest single-axis net rotation over any sub-span of the window ~ flip/roll size.
        cum = np.cumsum(r * dt[gs, None], axis=0)
        rotation = float(np.max(cum.max(axis=0) - cum.min(axis=0)))
        peak = float(np.percentile(np.linalg.norm(r, axis=1), 95))
        if len(acc_g):
            a = acc_g[_window_bounds(t_acc, start, end)]
            freefall = float(np.mean(a < 0.4)) if len(a) else 0.0
        else:
            freefall = 0.0
        score = min(rotation / 360.0, 2.0) + 0.7 * min(peak / 720.0, 2.0) + 1.0 * freefall
        if rotation >= 300:
            label = "flip/roll"
        elif freefall >= 0.3:
            label = "dive/hang"
        elif peak >= 400:
            label = "aggressive"
        else:
            label = "cruise"
        out.append(Highlight(float(start), float(end), round(score, 3), round(rotation, 1),
                             round(peak, 1), round(freefall, 3), label))
    return out


def pick(windows: list[Highlight], top_k=6, min_gap_s=4.0) -> list[Highlight]:
    """Greedy non-max suppression: best windows first, no two closer than min_gap_s."""
    kept: list[Highlight] = []
    for w in sorted(windows, key=lambda w: w.score, reverse=True):
        if w.label == "cruise":
            continue
        if all(abs(w.start_s - k.start_s) >= min_gap_s for k in kept):
            kept.append(w)
        if len(kept) >= top_k:
            break
    return sorted(kept, key=lambda w: w.start_s)


def smooth_sections(windows: list[Highlight], min_len_s=8.0) -> list[tuple[float, float]]:
    """For YouTube: long stretches of 'cruise' flying (smooth lines) that let a shot breathe."""
    spans, cur = [], None
    for w in windows:
        if w.label == "cruise" and w.peak_rate_dps < 250:
            cur = (cur[0], w.end_s) if cur else (w.start_s, w.end_s)
        elif cur:
            spans.append(cur)
            cur = None
    if cur:
        spans.append(cur)
    return [s for s in spans if s[1] - s[0] >= min_len_s]
