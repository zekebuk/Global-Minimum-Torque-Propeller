"""Speed ramps: a shot's speed as a curve over SOURCE time, eased between control points.

Cut list:  "ramp": [[t_src, speed], ...]   e.g. [[58.5, 1.8], [60.4, 0.5], [61.0, 0.5], [62.2, 1.4]]
Speed holds before the first point and after the last; between points it eases (smootherstep: speed AND its rate of
change are continuous), so there is never a kink in the motion. Cinematic ramps stay near 1x with long eases and no
holds; a hard slow-down reads as a stutter, two in a row read as the edit dragging. The shot's timeline length is the integral of 1/speed over [in_s, out_s]; `fit_out` picks the
out-point that keeps a shot on its beat when a ramp is added. The same curve drives the ffmpeg preview (setpts
expression + motion interpolation where the source has too few frames) and Resolve (TimeStretcher keyframes in the
clip's Fusion comp, over a constant Edit-page retime at the shot's average speed).
"""
from __future__ import annotations

import numpy as np

GRID_HZ = 480.0           # integration grid: 2 ms, a tenth of a 240 fps frame


def speed_at(points: list, t: np.ndarray | float) -> np.ndarray:
    """v(t): eased between control points, held outside them."""
    t = np.asarray(t, dtype=float)
    pts = sorted((float(a), float(b)) for a, b in points)
    if not pts:
        return np.ones_like(t)
    ts, vs = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
    if len(pts) == 1:
        return np.full_like(t, vs[0])
    i = np.clip(np.searchsorted(ts, t, side="right") - 1, 0, len(ts) - 2)
    t0, t1, v0, v1 = ts[i], ts[i + 1], vs[i], vs[i + 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        p = np.where(t1 > t0, (t - t0) / np.where(t1 > t0, t1 - t0, 1), 1.0)
    p = np.clip(p, 0, 1)
    e = p * p * p * (p * (6 * p - 15) + 10)          # smootherstep: no acceleration kink at either end
    return np.where(t < ts[0], vs[0], np.where(t >= ts[-1], vs[-1], v0 + (v1 - v0) * e))


def _grid(points: list, in_s: float, out_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Source times and the timeline time reached at each (cumulative 1/v), from in_s."""
    n = max(2, int(np.ceil((out_s - in_s) * GRID_HZ)) + 1)
    t = np.linspace(in_s, out_s, n)
    inv = 1.0 / np.maximum(speed_at(points, t), 1e-3)
    tau = np.concatenate([[0.0], np.cumsum((inv[1:] + inv[:-1]) / 2 * np.diff(t))])
    return t, tau


def duration(points: list, in_s: float, out_s: float) -> float:
    """Timeline seconds the source range [in_s, out_s] lasts under the ramp."""
    return float(_grid(points, in_s, out_s)[1][-1])


def source_time(points: list, in_s: float, out_s: float, tau: np.ndarray | float, pad_s: float = 0.0) -> np.ndarray:
    """Timeline time (from the shot's start) -> source time. pad_s extends the mapping pad_s of TIMELINE time before
    the in-point and after the out-point (transition handles), at the speeds the ramp holds there."""
    if pad_s > 0:
        v0, v1 = float(speed_at(points, in_s)), float(speed_at(points, out_s))
        t, tt = _grid(points, in_s - pad_s * v0 * 1.05, out_s + pad_s * v1 * 1.05)
        tt = tt - np.interp(in_s, t, tt)                 # tau = 0 at the in-point
    else:
        t, tt = _grid(points, in_s, out_s)
    return np.interp(np.asarray(tau, dtype=float), tt, t)


def fit_out(points: list, in_s: float, target_dur: float, limit_s: float | None = None) -> float:
    """The out-point that makes the ramped shot last target_dur on the timeline (so the cut stays on its beats)."""
    vmax = max(float(v) for _, v in points) if points else 1.0
    far = in_s + target_dur * vmax * 1.05 + 1.0
    if limit_s is not None:
        far = min(far, limit_s)
    t, tau = _grid(points, in_s, far)
    if tau[-1] < target_dur:
        raise ValueError(f"source ends at {far:.2f}s before the ramp reaches {target_dur:.2f}s on the timeline")
    return float(np.interp(target_dur, tau, t))


def fit_shape(in_s: float, out_s: float, target_dur: float, make, limit_s: float | None = None,
              iters: int = 20) -> tuple[list, float]:
    """A ramp whose SHAPE spans the whole shot: make(out_s) -> points is re-evaluated as the out-point moves to keep
    target_dur, until the two agree (fixed point; a few iterations). Returns (points, out_s)."""
    for _ in range(iters):
        pts = make(out_s)
        new = fit_out(pts, in_s, target_dur, limit_s)
        if abs(new - out_s) < 1e-3:
            return make(new), new
        out_s = new
    return make(out_s), out_s


def min_speed(points: list, in_s: float, out_s: float) -> float:
    t = np.linspace(in_s, out_s, 256)
    return float(speed_at(points, t).min())


def setpts_expr(points: list, in_s: float, out_s: float, knots: int = 24) -> str:
    """ffmpeg setpts expression: input time T (seconds from in_s, as `-ss` before `-i` restarts timestamps at 0) ->
    output time, as a piecewise-linear fit of the ramp's integral with a knot at every control point."""
    t, tau = _grid(points, in_s, out_s)
    ks = np.unique(np.concatenate([np.linspace(in_s, out_s, knots),
                                   [float(a) for a, _ in points if in_s < float(a) < out_s]]))
    kt = np.interp(ks, t, tau)
    expr = f"({kt[-2]:.6f}+({kt[-1] - kt[-2]:.6f})*(T-{ks[-2] - in_s:.6f})/{ks[-1] - ks[-2]:.6f})/TB"
    for i in range(len(ks) - 3, -1, -1):
        a, b = ks[i] - in_s, ks[i + 1] - in_s
        seg = f"({kt[i]:.6f}+({kt[i + 1] - kt[i]:.6f})*(T-{a:.6f})/{b - a:.6f})/TB"
        expr = f"if(lt(T\\,{b:.6f})\\,{seg}\\,{expr})"
    return expr


def base_speed(avg_speed: float, src_fps: float, fps: float) -> float:
    """The constant Edit-page speed for a ramped clip in Resolve: the smallest one that (a) covers the whole source
    span (p * dur >= span, i.e. p >= avg) and (b) makes the retime sample WHOLE comp frames (p * src_fps / fps an
    integer), so Resolve picks frames instead of alternating 1-2-1-2 comp frames, which juddered (jitter 0.55 vs
    0.2 in the ffmpeg preview). 60p on a 30p timeline: p in {0.5, 1, 1.5, 2, ...}."""
    step = fps / src_fps
    return float(np.ceil(avg_speed / step - 1e-6) * step)


def fusion_keyframes(points: list, in_s: float, out_s: float, fps: float, base_speed: float,
                     src_fps: float, pad_s: float = 0.0) -> dict[float, float]:
    """Resolve: a clip's Fusion comp runs in SOURCE frames (src_fps, un-retimed, 0 = the in-point); the Edit-page
    retime at the constant `base_speed` (span / dur) is applied to the comp's output afterwards, so timeline frame k
    samples comp time k * base_speed * src_fps / fps (measured 2026-11-03: keys placed at timeline frames froze the
    back half of every ramped shot). TimeStretcher.SourceTime at that comp time must be the MediaIn frame holding
    the ramp's source time for timeline time k / fps. One key per timeline frame, linear between."""
    n = int(round(duration(points, in_s, out_s) * fps))
    m = int(np.ceil(pad_s * fps))                  # transition handles: keep the curve going past both ends
    ks = np.arange(-m, n + m + 1)
    src = source_time(points, in_s, out_s, ks / fps, pad_s)
    step = base_speed * src_fps / fps
    return {round(k * step, 4): float((s_ - in_s) * src_fps) for k, s_ in zip(ks, src)}


def hero_time(src, in_s: float, out_s: float, hz: float = 10.0) -> float:
    """The moment of most motion in [in_s, out_s] (mean frame-to-frame change, smoothed over ~0.5 s): where an FPV
    shot wants its slow point - the pass, the gap, the dive's bottom. Returns a source time."""
    from . import grading
    n = max(4, int((out_s - in_s) * hz))
    frames = grading.sample_frames(src, in_s, out_s - in_s, n=n, width=160)
    g = np.array([f.astype(np.float32).mean(axis=2) for f in frames])
    e = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    k = max(1, int(hz * 0.5))
    e = np.convolve(e, np.ones(k) / k, mode="same")
    i = int(np.argmax(e))
    return in_s + (i + 1) / n * (out_s - in_s)


def three_point(in_s: float, out_s: float, hero_s: float, fast: float = 1.5, slow: float = 0.5,
                hold_s: float = 0.4) -> list:
    """The FPV staple: come in fast, ease down to `slow` through the hero moment (held hold_s), ease back up."""
    a, b = max(in_s, hero_s - hold_s / 2), min(out_s, hero_s + hold_s / 2)
    pts = [[in_s, fast], [a, slow], [b, slow], [out_s, fast]]
    return [p for i, p in enumerate(pts) if i == 0 or p[0] > pts[i - 1][0] + 1e-3]


def describe(points: list) -> str:
    return " -> ".join(f"{v:g}x@{t:.2f}" for t, v in sorted((float(a), float(b)) for a, b in points))
