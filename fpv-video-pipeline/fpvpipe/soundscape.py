"""Music-free sound design that follows the flight, driven by the camera's own IMU.

First principles: without music, sound has to carry speed and scale. What the viewer feels maps to physics:
  * motor/prop loudness    ~ how hard the quad is working  -> follows angular rate (stick input) magnitude
  * a whoosh               ~ air moving past fast            -> placed on sharp angular-rate peaks (rolls, flips, snaps)
  * rushing wind in a dive ~ free fall                       -> |accel| well below 1 g
  * the place itself       ~ a steady ambience bed           -> per scene, crossfaded at scene changes
All levels are in dBFS on a 48 kHz mono bus; the result is normalised and peak-limited, then written stereo.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SR = 48000
LEVELS_DB = {"bed": -22.0, "motor_min": -32.0, "motor_max": -16.0, "whoosh": -12.0, "dive": -14.0}
WHOOSH_DPS = 400.0      # smoothed angular rate that counts as a "snap"
WHOOSH_GAP_S = 1.5      # no two whooshes closer than this
MOTOR_FULL_DPS = 600.0  # angular rate at which the motor layer is at full level
FREEFALL_G = 0.4
CROSSFADE_S = 1.0


def db(x: float) -> float:
    return 10 ** (x / 20)


@dataclass
class Segment:
    start_s: float                       # where the shot sits on the timeline
    duration_s: float
    scene: str
    t: np.ndarray = field(default_factory=lambda: np.zeros(0))       # IMU times relative to the shot's first frame
    rate_dps: np.ndarray = field(default_factory=lambda: np.zeros(0))
    t_acc: np.ndarray = field(default_factory=lambda: np.zeros(0))
    acc_g: np.ndarray = field(default_factory=lambda: np.zeros(0))


def smooth(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) < 2 or n <= 1:
        return x
    return np.convolve(x, np.ones(n) / n, mode="same")


def tile(loop: np.ndarray, n: int, offset: int = 0) -> np.ndarray:
    if len(loop) == 0:
        return np.zeros(n, np.float32)
    idx = (np.arange(n) + offset) % len(loop)
    return loop[idx]


def _add(bus: np.ndarray, clip: np.ndarray, at: int, gain: float):
    if at >= len(bus) or len(clip) == 0:
        return
    if at < 0:
        clip, at = clip[-at:], 0
    end = min(len(bus), at + len(clip))
    bus[at:end] += gain * clip[: end - at]


def whoosh_times(seg: Segment) -> list[float]:
    if len(seg.t) < 4:
        return []
    fs = 1 / max(np.median(np.diff(seg.t)), 1e-4)
    r = smooth(seg.rate_dps, int(0.15 * fs))
    above = r >= WHOOSH_DPS
    out, last = [], -1e9
    for i in np.flatnonzero(above[1:] & ~above[:-1]) + 1:  # rising edges
        j = i + int(np.argmax(r[i:i + int(0.5 * fs)]))       # the peak just after crossing
        t = float(seg.t[min(j, len(seg.t) - 1)])
        if t - last >= WHOOSH_GAP_S and 0 <= t < seg.duration_s:
            out.append(t)
            last = t
    return out


def freefall_spans(seg: Segment, min_s: float = 0.4) -> list[tuple[float, float]]:
    if len(seg.t_acc) < 4:
        return []
    ff = seg.acc_g < FREEFALL_G
    spans, start = [], None
    for t, f in zip(seg.t_acc, ff):
        if f and start is None:
            start = t
        elif not f and start is not None:
            if t - start >= min_s:
                spans.append((float(start), float(t)))
            start = None
    if start is not None and seg.t_acc[-1] - start >= min_s:
        spans.append((float(start), float(seg.t_acc[-1])))
    return [s for s in spans if s[0] < seg.duration_s]


def mix(segments: list[Segment], beds: dict[str, np.ndarray], motor: np.ndarray, whoosh: np.ndarray,
        dive: np.ndarray, total_s: float | None = None) -> tuple[np.ndarray, dict]:
    total_s = total_s or max(s.start_s + s.duration_s for s in segments)
    n = int(total_s * SR)
    bus = np.zeros(n, np.float32)
    stats = {"whooshes": 0, "dives": 0, "scenes": []}

    # 1. Beds: one gain curve per scene, ramping over CROSSFADE_S where the scene changes.
    fade = int(CROSSFADE_S * SR)
    for scene in sorted({s.scene for s in segments}):
        bed = beds.get(scene)
        if bed is None:
            continue
        g = np.zeros(n, np.float32)
        for s in segments:
            if s.scene == scene:
                a, b = int(s.start_s * SR), min(n, int((s.start_s + s.duration_s) * SR))
                g[max(0, a - fade // 2):min(n, b + fade // 2)] = 1.0
        g = np.convolve(g, np.ones(fade) / fade, mode="same") if fade > 1 else g
        bus += db(LEVELS_DB["bed"]) * g * tile(bed, n)
        stats["scenes"].append(scene)

    # 2. Motor layer: level follows smoothed angular rate; constant mid level when a shot has no IMU.
    for s in segments:
        a, m = int(s.start_s * SR), int(s.duration_s * SR)
        tt = np.arange(m) / SR
        if len(s.t) >= 4:
            fs = 1 / max(np.median(np.diff(s.t)), 1e-4)
            r = smooth(s.rate_dps, int(0.3 * fs))
            x = np.clip(np.interp(tt, s.t, r) / MOTOR_FULL_DPS, 0, 1)
        else:
            x = np.full(m, 0.5)
        level_db = LEVELS_DB["motor_min"] + (LEVELS_DB["motor_max"] - LEVELS_DB["motor_min"]) * x
        seg_audio = (10 ** (level_db / 20)) * tile(motor, m, offset=a)
        _add(bus, seg_audio.astype(np.float32), a, 1.0)

        # 3. Whooshes: loudest point of the whoosh lands on the angular-rate peak.
        if len(whoosh):
            hot = int(np.argmax(np.abs(whoosh)))
            for t in whoosh_times(s):
                _add(bus, whoosh, a + int(t * SR) - hot, db(LEVELS_DB["whoosh"]))
                stats["whooshes"] += 1
        # 4. Dives: wind swell from the start of free fall.
        if len(dive):
            for t0, _ in freefall_spans(s):
                _add(bus, dive, a + int(t0 * SR), db(LEVELS_DB["dive"]))
                stats["dives"] += 1

    # Normalise to -20 dBFS RMS, then hard-limit peaks at -1 dBFS (gentle enough for a bed under camera audio).
    rms = float(np.sqrt(np.mean(bus ** 2))) if n else 0.0
    if rms > 0:
        bus *= db(-20) / rms
    peak = float(np.max(np.abs(bus))) if n else 0.0
    if peak > db(-1):
        bus = np.tanh(bus / db(-1)) * db(-1)
    return bus, stats
