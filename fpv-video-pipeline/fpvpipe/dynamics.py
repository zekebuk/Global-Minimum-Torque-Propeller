"""Time-varying ("transient") grade analysis: when does the light change enough to warrant a keyframe?

Model: final grade = static base grade (colorsci.solve over the whole clip) + a slow, piecewise residual.
The residual is only allowed to follow LIGHT, never CONTENT:
  * exposure signal  = median log luma of the "ground" (sky excluded: bright + blue-dominant pixels), so pitching
                       up into the sky doesn't read as a light change
  * illuminant signal= log-chromaticity of the per-frame neutral cluster (same physics as colorsci.find_neutrals)
Both are smoothed over ~1 s (human adaptation and AE settle on that scale), then segmented with hysteresis:
a new section starts only if the smoothed signal departs from the current section by more than a threshold for
at least `min_hold_s`. Exposure corrections are partial (default 60%): a dive into a dark gully should still feel
darker, just not murky; WB corrections are full but capped. Transitions are eased over `ramp_s`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .colorsci import find_neutrals, luma

LOG_STOP = 0.07  # log units per stop for GoPro/DJI-style log curves (~0.06-0.09); used only to report stops


@dataclass
class Section:
    start_s: float
    end_s: float
    exposure_log: float      # section's ground exposure relative to the clip reference (log units)
    wb: np.ndarray           # section's neutral (R-G, B-G) relative to the clip reference
    correction: np.ndarray   # CDL offset (R, G, B) to ADD to the base BAL node for this section


def sky_mask(px: np.ndarray, neutral_d: np.ndarray, warm_margin: float = 0.02) -> np.ndarray:
    """Sky = more than ~1 stop brighter than the ground AND not warmer than the scene's neutrals.
    Brightness is the primary cue on purpose: in shade the illuminant IS skylight, so shaded neutrals turn exactly as
    blue as the sky and any "bluer than neutrals" rule fails there. Percentile rules ("brightest 30 %") fail when the
    sky fills most of the frame. Starts from the 20th percentile (valid while >= ~20 % of the frame is ground) and
    refines twice, so the ground estimate isn't pulled up by sky pixels."""
    y = luma(px)
    not_warm = (px[:, 0] - px[:, 1]) - neutral_d[0] < warm_margin
    ground_med = float(np.quantile(y, 0.2))  # start low: the plain median IS sky once sky fills > half the frame
    sky = np.zeros(len(px), bool)
    for _ in range(2):
        sky = not_warm & (y > ground_med + LOG_STOP)
        if (~sky).sum() < 50:
            return np.zeros(len(px), bool)
        ground_med = float(np.median(y[~sky]))
    return sky


def ground_exposure(frame_log: np.ndarray, neutral_d: np.ndarray | None = None) -> float:
    px = frame_log.reshape(-1, 3)
    y = luma(px)
    sky = sky_mask(px, np.zeros(2) if neutral_d is None else neutral_d)
    g = y[~sky] if (~sky).sum() > 50 else y
    return float(np.median(g))


def signals(frames_log: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    exp, wb, conf = [], [], []
    for f in frames_log:
        nz = find_neutrals(f.reshape(-1, 3))
        ok = nz["spread"] >= 0.05
        wb.append(nz["d"])
        conf.append(nz["confidence"] if ok else 0.0)
        exp.append(ground_exposure(f, nz["d"] if ok else None))
    return np.array(exp), np.array(wb), np.array(conf)


def smooth(x: np.ndarray, n: int) -> np.ndarray:
    """Running median (robust to single odd frames: a flash of sun, a prop in frame)."""
    if n <= 1 or len(x) < 3:
        return x.copy()
    h = n // 2
    pad = np.concatenate([np.repeat(x[:1], h, 0), x, np.repeat(x[-1:], h, 0)])
    return np.array([np.median(pad[i:i + n], axis=0) for i in range(len(x))])


def segment(t: np.ndarray, exp: np.ndarray, wb: np.ndarray, conf: np.ndarray, fps: float,
            exp_thresh: float = 0.35 * LOG_STOP, wb_thresh: float = 0.02, min_hold_s: float = 1.0,
            exposure_amount: float = 0.6, max_wb: float = 0.08) -> list[Section]:
    n = max(1, int(round(fps)))  # ~1 s smoothing window
    e = smooth(exp, n)
    w = smooth(np.where(conf[:, None] > 0.15, wb, np.nan), n)
    # frames without a confident neutral cluster inherit the last known illuminant
    for i in range(len(w)):
        if np.isnan(w[i]).any():
            w[i] = w[i - 1] if i else np.zeros(2)
    w = np.nan_to_num(w)
    ref_e, ref_w = float(np.median(e)), np.median(w, axis=0)
    hold = max(1, int(round(min_hold_s * fps)))

    bounds, cur_e, cur_w, start, i = [0], e[0], w[0], 0, 0
    while i < len(t):
        out = (abs(e[i] - cur_e) > exp_thresh) or (np.hypot(*(w[i] - cur_w)) > wb_thresh)
        if out and i + hold <= len(t):
            win_e, win_w = e[i:i + hold], w[i:i + hold]
            # sustained: the whole hold window stays away from the current section
            if np.all(np.abs(win_e - cur_e) > exp_thresh * 0.7) or np.all(np.hypot(*(win_w - cur_w).T) > wb_thresh * 0.7):
                bounds.append(i)
                cur_e, cur_w, start = float(np.median(win_e)), np.median(win_w, axis=0), i
                i += hold
                continue
        i += 1
    bounds.append(len(t))

    secs = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        se, sw = float(np.median(e[a:b])), np.median(w[a:b], axis=0)
        de, dw = se - ref_e, sw - ref_w
        dw = np.clip(dw, -max_wb, max_wb)
        g = -exposure_amount * de                    # partial exposure correction (keep some of the real change)
        corr = np.array([g - dw[0], g, g - dw[1]])   # cancel the illuminant shift relative to the clip reference
        secs.append(Section(float(t[a]), float(t[b - 1] + 1 / fps), de, dw, corr))
    return secs


def needs_keyframes(secs: list[Section], exp_thresh: float = 0.35 * LOG_STOP, wb_thresh: float = 0.02) -> bool:
    return len(secs) > 1 and any(abs(s.exposure_log) > exp_thresh or np.hypot(*s.wb) > wb_thresh for s in secs)
