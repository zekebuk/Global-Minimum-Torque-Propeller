"""Transient grade: reacts to light changes (AE lag, shade), ignores content changes (pitching into the sky)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fpvpipe import dynamics as dy  # noqa: E402
from test_color import log_encode  # noqa: E402

FPS = 4.0


def frame(sky_frac, light=1.0, cast=(1.0, 1.0, 1.0), seed=0, n=4000):
    rng = np.random.default_rng(seed)
    k = int(n * sky_frac)
    sky = 0.6 * np.array([0.6, 0.8, 1.2]) * rng.lognormal(0, 0.05, (k, 1))        # sky: lit by itself, not by "light"
    ground = np.concatenate([0.18 * np.ones(3) * rng.lognormal(0, 0.6, ((n - k) // 2, 1)),     # rock, many stops
                             0.10 * np.array([0.5, 1.0, 0.4]) * rng.lognormal(0, 0.2, ((n - k) - (n - k) // 2, 1))])
    ground = ground * light * np.array(cast)
    return log_encode(np.concatenate([sky, ground])).reshape(-1, 1, 3)


def run(frames):
    t = np.arange(len(frames)) / FPS
    exp, wb, conf = dy.signals(frames)
    return dy.segment(t, exp, wb, conf, FPS)


def test_pitching_into_sky_is_not_a_light_change():
    frames = [frame(0.15 + 0.5 * (0.5 + 0.5 * np.sin(i / 3)), seed=i) for i in range(40)]  # sky share 15-65 %
    secs = run(frames)
    assert not dy.needs_keyframes(secs), [(s.start_s, s.exposure_log) for s in secs]


def test_ae_lag_dip_and_shade_shift_detected():
    frames = []
    for i in range(48):                       # 12 s
        tt = i / FPS
        light = 0.45 if 3.0 <= tt < 5.0 else 1.0                     # ~1.2 stop dip for 2 s (AE lag into a gully)
        cast = (0.85, 1.0, 1.2) if tt >= 8.0 else (1.0, 1.0, 1.0)    # into blue shade at 8 s
        frames.append(frame(0.3, light, cast, seed=i))
    secs = run(frames)
    assert dy.needs_keyframes(secs)
    starts = [round(s.start_s) for s in secs]
    assert any(abs(s - 3) <= 1 for s in starts) and any(abs(s - 8) <= 1 for s in starts), starts
    dip = min(secs, key=lambda s: s.exposure_log)
    assert dip.correction[1] > 0                                    # brighten the dip ...
    assert dip.correction[1] < abs(dip.exposure_log)                # ... only partially (keep some of the mood)
    shade = [s for s in secs if s.start_s >= 7.5][0]
    assert shade.correction[2] < shade.correction[0]                # warm it back up: less blue, more red
