"""The 5-node CST grade: exact transforms, scope targets hit, and every lesson in COLOR_LESSONS.md as a check."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import cst, grade5 as g5  # noqa: E402


def scene(seed=0, under=1.0):
    rng = np.random.default_rng(seed)
    rock = np.array([1.0, 0.6, 0.4]) * 0.06 * under * rng.lognormal(0, 0.9, (30000, 1))
    sky = np.array([0.55, 0.75, 1.0]) * 0.12 * under * rng.lognormal(0, 0.1, (8000, 1))
    grey = 0.05 * under * rng.lognormal(0, 0.6, (6000, 1)) * np.ones(3)
    return cst.linear_to_gplog(np.concatenate([rock, sky, grey]))


def test_transforms_are_exact_and_neutral():
    x = np.geomspace(1e-4, 1, 50)
    assert np.abs(cst.di_to_linear(cst.linear_to_di(x)) - x).max() < 1e-9
    assert np.abs(cst.REC709_TO_DWG @ cst.DWG_TO_REC709 - np.eye(3)).max() < 1e-4
    grey = np.repeat(cst.linear_to_gplog(x)[:, None], 3, 1)
    out = cst.cst_out(cst.cst_in(grey))
    assert np.abs(out - out[:, :1]).max() < 1e-4                      # nodes 1-2 add no cast
    assert abs(out[-1, 0] - 1.0) < 1e-4                               # scene white -> display white


def metered_mid(g, v):
    """The median node 3 aims at: the picture without its brightest 10% (sky, sun)."""
    y0 = g5._ycc(g.apply(v, upto=2))[0]
    return float(np.median(g5._ycc(g.apply(v))[0][y0 < np.percentile(y0, 90)]))


def test_node3_hits_waveform_targets():
    v = scene()
    g = g5.solve(v)
    f = g.stats["final"]
    assert abs(f["black"] - g5.TARGETS["black"]) < 0.03
    assert abs(metered_mid(g, v) - g5.TARGETS["mid"]) < 0.03
    assert f["white"] >= 0.8


def test_underexposed_footage_gets_one_exposure_not_two():
    """-1.5 stops in camera: node 3 must bring it back with gain (one decision), mids on target, not a dark
    exposure 'fixed' by a huge gamma."""
    v = scene(under=0.35)
    g, g0 = g5.solve(v), g5.solve(scene())
    assert g.prim.gain.mean() > 1.3 * g0.prim.gain.mean()               # the gain took the lost stops back
    assert abs(1 / g.prim.power - 1 / g0.prim.power) < 0.05              # the gamma did not
    assert abs(metered_mid(g, v) - g5.TARGETS["mid"]) < 0.03


def test_red_rock_is_not_white_balanced():
    g = g5.solve(scene())
    assert any("WB limited" in n for n in g.notes)
    assert np.abs(g.prim.gain - g.prim.gain.mean()).max() < 1e-6


def test_every_node_is_monotonic_even_with_strong_settings():
    ramp = np.repeat(np.linspace(0, 1, 2001)[:, None], 3, 1)
    for over in ({}, {"s_curve": 0.45, "sky_lift": 0.8, "vibrance": 1.0}, {"black": 0.08, "floor_level": 0.12}):
        g = g5.solve(scene(), over)
        for k in range(1, 6):
            y = g5._ycc(g.apply(ramp, upto=k))[0] if k >= 2 else g.apply(ramp, upto=k).mean(1)
            assert np.diff(y).min() > -1e-6, (over, k)


def test_over_range_rolls_off_in_the_shoulder_not_a_clip():
    g = g5.solve(scene(under=0.35))
    bright = np.repeat(cst.linear_to_gplog(np.array([0.6, 0.8, 1.0]))[:, None], 3, 1)
    y = g5._ycc(g.apply(bright))[0]
    assert y[0] < y[1] < y[2] <= g5.TARGETS["ceiling"] + 1e-6         # still ordered, under the ceiling


def test_resolve_wheel_values_are_reported():
    d = g5.solve(scene()).to_dict()
    assert set(d["3_primary"]) == {"lift", "gain", "gamma", "offset"}
    assert d["2_cst_out"]["tone_mapping"] == "none"
