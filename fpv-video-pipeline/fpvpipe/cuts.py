"""Cut detection and per-shot look statistics for reference reels.

Cuts: ffmpeg's scene-change score (0..1, how different a frame is from the previous one). FPV footage moves fast,
so plain thresholds over-detect; we require score >= threshold AND a minimum shot length, which removes the
"whip pan looks like a cut" false positives without missing real cuts (real cuts are near 1.0 on most reels).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

_PTS = re.compile(r"pts_time:([0-9.]+)")
_SCORE = re.compile(r"lavfi\.scene_score=([0-9.]+)")


def detect_cuts(path: Path, threshold: float = 0.35, min_shot_s: float = 0.12) -> list[float]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an",
         "-vf", f"scale=320:-2,select='gt(scene,{threshold})',metadata=print", "-f", "null", "-"],
        capture_output=True, text=True)
    times, last = [], -1e9
    for line in proc.stderr.splitlines():
        m = _PTS.search(line)
        if m:
            t = float(m.group(1))
            if t - last >= min_shot_s:
                times.append(round(t, 3))
                last = t
    return times


def shot_stats(cuts: list[float], duration: float) -> dict:
    bounds = [0.0] + cuts + [duration]
    lengths = np.diff(bounds)
    lengths = lengths[lengths > 0]
    if not len(lengths):
        return {}
    return {
        "shots": int(len(lengths)),
        "cuts_per_10s": round(len(cuts) / duration * 10, 2) if duration else 0,
        "shot_len_median_s": round(float(np.median(lengths)), 2),
        "shot_len_p10_s": round(float(np.percentile(lengths, 10)), 2),
        "shot_len_p90_s": round(float(np.percentile(lengths, 90)), 2),
        "first_cut_s": round(cuts[0], 2) if cuts else round(duration, 2),
        # How the pace changes: shot length in the first vs. last third of the reel.
        "pace_first_third_s": _mean_len(bounds, 0, duration / 3),
        "pace_last_third_s": _mean_len(bounds, 2 * duration / 3, duration),
    }


def _mean_len(bounds, a, b):
    ls = [e - s for s, e in zip(bounds[:-1], bounds[1:]) if s >= a - 1e-9 and s < b]
    return round(float(np.mean(ls)), 2) if ls else None


def beat_alignment(cuts: list[float], beats: list[float], fps: float = 30.0) -> dict:
    """Share of cuts within one frame of a beat. Random cutting at 120 BPM lands within +-1 frame ~13% of the time."""
    if not cuts or len(beats) < 2:
        return {}
    b = np.asarray(beats)
    err = np.array([np.min(np.abs(b - c)) for c in cuts])
    period = float(np.median(np.diff(b)))
    tol = 1.0 / fps
    return {
        "cuts_on_beat_frac": round(float(np.mean(err <= tol)), 2),
        "chance_level_frac": round(min(1.0, 2 * tol / period), 2),
        "median_offset_ms": round(float(np.median(err)) * 1000, 1),
        "cut_every_beats_median": round(float(np.median(np.diff(cuts)) / period), 2) if len(cuts) > 1 else None,
    }


def look_stats(path: Path, times: list[float]) -> dict:
    """Contrast, black level and saturation sampled mid-shot: describes the grade, not the content."""
    ys, ss = [], []
    for t in times:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
             "-vf", "scale=160:-2", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True).stdout
        if raw:
            px = np.frombuffer(raw, np.uint8).reshape(-1, 3) / 255.0
            ys.append(px @ [0.2126, 0.7152, 0.0722])
            mx, mn = px.max(1), px.min(1)
            ss.append(np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0))
    if not ys:
        return {}
    y, s = np.concatenate(ys), np.concatenate(ss)
    p2, p50, p98 = np.percentile(y, [2, 50, 98])
    return {"black_p2": round(float(p2), 3), "mid_p50": round(float(p50), 3), "white_p98": round(float(p98), 3),
            "contrast_range": round(float(p98 - p2), 3), "saturation_mean": round(float(s.mean()), 3)}
