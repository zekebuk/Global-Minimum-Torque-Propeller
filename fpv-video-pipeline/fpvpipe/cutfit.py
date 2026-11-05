"""Fit in-points to the music: slide each shot a little so its motion peak lands on a beat, its cuts avoid a roll in progress,
and the direction of travel carries across the cut. The slots (beats) stay as they are; only what shows in them moves.

Scores, all from the picture at HZ:
  on the beat  how hard the slot's motion peak hits (against the shot's median), times how close it is to a beat
  drive        mean motion through the slot, so a slide cannot win by parking on stillness
  roll         penalty per cut made during a roll faster than ROLL_OK deg/s
  carry        reward when travel out of one shot matches travel into the next, penalty when it opposes
The sequence is solved with Viterbi over a grid of in-points, so a shot gives way when its neighbour needs the match more.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import cutlist as cl
from . import ramp as _ramp

HZ, W, H = 15, 160, 120
ROLL_OK = 45.0                 # deg/s; banking through a turn is fine, a barrel roll in progress is not
BEAT_SIGMA = 0.10              # seconds: how close to a beat still counts as on it
ANGLES = 180


def _polar_index() -> tuple[np.ndarray, np.ndarray]:
    r = np.arange(22, 56, 2)[:, None]
    a = np.linspace(0, 2 * np.pi, ANGLES, endpoint=False)[None, :]
    return (H / 2 + r * np.sin(a)).astype(int), (W / 2 + r * np.cos(a)).astype(int)


_PY, _PX = _polar_index()


def rotation(a: np.ndarray, b: np.ndarray) -> float:
    """Degrees b is rotated from a about the centre (positive = clockwise on screen), by correlating around circles."""
    pa, pb = a[_PY, _PX], b[_PY, _PX]
    pa = pa - pa.mean(axis=1, keepdims=True)
    pb = pb - pb.mean(axis=1, keepdims=True)
    c = np.fft.irfft((np.fft.rfft(pb, axis=1) * np.conj(np.fft.rfft(pa, axis=1))).sum(axis=0), n=ANGLES)
    k = int(np.argmax(c))
    return float((k - ANGLES if k > ANGLES / 2 else k) * 360.0 / ANGLES)


def shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Pixels the picture moved from a to b, by phase correlation."""
    f = np.fft.fft2(b - b.mean()) * np.conj(np.fft.fft2(a - a.mean()))
    c = np.fft.ifft2(f / (np.abs(f) + 1e-6)).real
    y, x = np.unravel_index(np.argmax(c), c.shape)
    return float(x - W if x > W / 2 else x), float(y - H if y > H / 2 else y)


@dataclass
class Profile:
    t0: float
    motion: np.ndarray         # mean abs change per frame, smoothed
    roll: np.ndarray           # deg/s, smoothed
    dx: np.ndarray             # px per frame, smoothed
    dy: np.ndarray

    def at(self, arr: np.ndarray, t) -> np.ndarray:
        i = np.clip(((np.asarray(t, dtype=float) - self.t0) * HZ).astype(int), 0, len(arr) - 1)
        return arr[i]


def _smooth(x: np.ndarray, n: int) -> np.ndarray:
    return np.convolve(x, np.ones(n) / n, mode="same")


def profile(src: str, start: float, dur: float, cache: Path | None = None) -> Profile:
    start = max(0.0, start)
    file = None
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
        file = cache / f"{Path(src).stem.replace(' ', '_')}_{start:.2f}_{dur:.2f}.json"
        if file.exists():
            d = json.loads(file.read_text())
            return Profile(d["t0"], *[np.array(d[k]) for k in ("motion", "roll", "dx", "dy")])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src, "-vf",
                          f"fps={HZ},scale={W}:{H},format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)
    if len(a) < 4:
        raise ValueError(f"no frames from {src} at {start}")
    e = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))
    rot = np.array([rotation(a[i], a[i + 1]) for i in range(len(a) - 1)]) * HZ
    sh = np.array([shift(a[i], a[i + 1]) for i in range(len(a) - 1)])
    k = max(1, int(HZ * 0.3))
    p = Profile(start, _smooth(e, k), _smooth(np.abs(rot), k), _smooth(sh[:, 0], k), _smooth(sh[:, 1], k))
    if file:
        file.write_text(json.dumps({"t0": p.t0, "motion": p.motion.round(2).tolist(), "roll": p.roll.round(1).tolist(),
                                    "dx": p.dx.round(2).tolist(), "dy": p.dy.round(2).tolist()}))
    return p


def _moved(s: cl.Shot, new_in: float, dur: float) -> tuple[list, float]:
    """The shot's ramp and out-point if it started at new_in and kept its timeline length."""
    d = new_in - s.in_s
    if s.ramp:
        pts = [[a + d, v] for a, v in s.ramp]
        return pts, _ramp.fit_out(pts, new_in, dur)
    return [], new_in + dur * (s.speed or 1.0)


@dataclass
class Option:
    in_s: float
    score: float
    peak_off: float            # seconds from the slot's motion peak to the nearest beat
    punch: float
    roll_in: float
    roll_out: float
    v_in: tuple[float, float]
    v_out: tuple[float, float]


def option(s: cl.Shot, p: Profile, new_in: float, dur: float, beats: np.ndarray) -> Option:
    """Score the shot started at new_in. beats = beat times from the shot's start on the timeline."""
    pts, out = _moved(s, new_in, dur)
    tau = np.arange(0, dur, 1.0 / HZ)
    src = _ramp.source_time(pts, new_in, out, tau) if pts else new_in + tau * (s.speed or 1.0)
    m = p.at(p.motion, src)
    base = max(float(np.median(p.motion)), 0.5)
    guard = m[int(HZ * .15):] if len(m) > int(HZ * .3) else m       # a peak on the first frames belongs to the last shot's cut
    i = int(np.argmax(guard)) + (len(m) - len(guard))
    off = float(np.min(np.abs(beats - tau[i]))) if len(beats) else 1.0
    punch = float(m[i] / base)
    roll_in, roll_out = float(p.at(p.roll, np.array([src[0]]))[0]), float(p.at(p.roll, np.array([src[-1]]))[0])
    penalty = sum(min(2.0, max(0.0, r / ROLL_OK - 1.0)) for r in (roll_in, roll_out))
    score = punch * np.exp(-(off / BEAT_SIGMA) ** 2) + 0.5 * float(m.mean() / base) - penalty
    v = lambda t: (float(p.at(p.dx, np.array([t]))[0]), float(p.at(p.dy, np.array([t]))[0]))
    return Option(new_in, float(score), off, punch, roll_in, roll_out, v(src[min(2, len(src) - 1)]), v(src[max(0, len(src) - 3)]))


def carry(a: Option, b: Option) -> float:
    """+0.5 for travel continuing through the cut, -0.5 for it reversing; nothing when either side is barely moving."""
    u, v = np.array(a.v_out), np.array(b.v_in)
    nu, nv = np.linalg.norm(u), np.linalg.norm(v)
    return 0.0 if nu < 0.5 or nv < 0.5 else 0.5 * float(u @ v / (nu * nv))


def fit(c: cl.CutList, window: float = 1.2, lock: set[int] | None = None, step: float = 0.1, cache: Path | None = None) -> list[dict]:
    """Slide every shot not in lock (1-based) by up to +-window and apply the best sequence. Returns a report per shot."""
    lock = lock or set()
    all_beats = np.array(c.beats_s)
    starts, opts = c.starts(), []
    for n, (s, st) in enumerate(zip(c.shots, starts), 1):
        dur = s.dur
        w = 0.0 if n in lock else window
        v = max((a[1] for a in s.ramp), default=s.speed or 1.0)
        p = profile(str(s.src), s.in_s - w - .2, 2 * w + dur * v + 1.5, cache)
        beats = all_beats[(all_beats > st + .05) & (all_beats < st + dur - .05)] - st
        grid = [s.in_s] if w == 0 else [x for x in np.arange(s.in_s - w, s.in_s + w + 1e-6, step) if x >= 0]
        opts.append([option(s, p, float(x), dur, beats) for x in grid])
    best = [np.array([o.score for o in opts[0]])]
    back = []
    for prev, cur in zip(opts, opts[1:]):
        tr = np.array([[carry(a, b) for a in prev] for b in cur])
        tot = best[-1][None, :] + tr
        back.append(tot.argmax(axis=1))
        best.append(np.array([o.score for o in cur]) + tot.max(axis=1))
    j = int(best[-1].argmax())
    path = [j]
    for b in reversed(back):
        j = int(b[j])
        path.append(j)
    path.reverse()
    report, last = [], None
    for n, (s, os_, k) in enumerate(zip(c.shots, opts, path), 1):
        o, dur = os_[k], s.dur
        moved = o.in_s - s.in_s
        s.ramp, s.out_s = _moved(s, o.in_s, dur)
        s.in_s = round(o.in_s, 3)
        s.out_s = round(s.out_s, 3)
        s.ramp = [[round(a, 3), v] for a, v in s.ramp]
        report.append({"shot": n, "moved": round(moved, 2), "peak_off": round(o.peak_off, 2), "punch": round(o.punch, 2),
                       "roll_in": round(o.roll_in), "roll_out": round(o.roll_out), "carry": None if last is None else round(carry(last, o), 2)})
        last = o
    return report
