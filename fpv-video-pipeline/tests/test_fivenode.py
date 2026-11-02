"""5-node scope-solved grade: each node hits its waveform / vectorscope target on a synthetic GP-Log scene."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import fivenode as fn  # noqa: E402


def gplog(x):
    return np.log(np.asarray(x) * 399 + 1) / np.log(400)


def scene(seed=0):
    rng = np.random.default_rng(seed)
    rock = np.array([1.0, 0.6, 0.4]) * 0.06 * rng.lognormal(0, 0.9, (30000, 1))      # sunlit .. shaded sandstone
    sky = np.array([0.55, 0.75, 1.0]) * 0.12 * rng.lognormal(0, 0.1, (8000, 1))
    grey = 0.05 * rng.lognormal(0, 0.6, (6000, 1)) * np.ones(3)                         # neutral rocks / shade
    return gplog(np.concatenate([rock, sky, grey]))


def test_scope_targets_are_hit():
    g = fn.solve(scene(), fn.DRT())
    y = fn._ycc(g.apply(scene()))[0]
    lift_y = y  # luma after all nodes
    t = fn.TARGETS
    assert abs(np.percentile(lift_y, 99.5) - t["white"]) < 0.04
    assert np.percentile(lift_y, 1) < 0.08                                   # dense, not crushed, blacks
    assert abs(g.stats["final"]["sat_mean"] - t["sat_mean"]) < 0.02 or any("SAT" in n for n in g.notes)
    assert g.stats["final"]["white"] <= t["ceiling"] + 1e-6


def test_neutral_axis_stays_neutral_through_nodes_2_to_5():
    g = fn.solve(scene(), fn.DRT())
    ramp = np.repeat(gplog(np.geomspace(0.002, 1, 40))[:, None], 3, 1)
    out = ramp
    for k, f in g.node_fns().items():
        if k >= 2:
            out = f(out)
    assert np.abs(out - out[:, 1:2]).max() < 0.02      # node 5 trims casts, nodes 2-4 add none


def test_shadow_lift_survives_black_point():
    v = scene()
    flat = fn.solve(v, fn.DRT())
    lifted = fn.solve(v, fn.DRT(), {"shadow_lift": 1.5})
    dark = v.mean(1) < np.percentile(v.mean(1), 10)
    assert fn._ycc(lifted.apply(v[dark]))[0].mean() > fn._ycc(flat.apply(v[dark]))[0].mean() + 0.02


def test_whole_grade_never_reverses_tones():
    """A shadow lift that faded out faster than the image got brighter once turned a sky into blotches; every
    node and every strong setting must keep the grey ramp monotonic."""
    v = scene()
    ramp = np.repeat(np.linspace(0, 1, 2001)[:, None], 3, 1)
    for over in ({}, {"shadow_lift": 1.5}, {"shadow_lift": 3.0, "s_curve": 0.4}, {"sky_lift": 0.6, "vibrance": 1.0}):
        g = fn.solve(v, fn.DRT(), over)
        for k in range(1, 6):
            y = fn._ycc(g.apply(ramp, upto=k))[0]
            assert np.diff(y).min() > -1e-4, (over, k)


def test_shadow_lift_does_not_move_exposure_and_node3_cannot_redo_it():
    """Node 1's lift once raised the metered median, so exposure dropped ~1.3 stops and node 3 stretched x2.2 to
    compensate (gray, flat highlights). Exposure must ignore the lift and node 3's stretch stays bounded."""
    v = scene()
    a = fn.solve(v, fn.DRT())
    b = fn.solve(v, fn.DRT(), {"shadow_lift": 2.5, "lift_pct": 10.0})
    assert abs(a.bal.cdl.offset[1] - b.bal.cdl.offset[1]) < 1e-6
    for g in (a, b):
        c = g.contrast
        assert (c.out_white - c.out_black) / (c.white - c.black) <= fn.TARGETS["max_stretch"] + 1e-6
        assert 0.75 - 1e-6 <= c.gamma <= 1.35 + 1e-6
