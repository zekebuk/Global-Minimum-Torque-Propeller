"""Scope-driven grading on a synthetic scene with a known log curve, so the right answer is known exactly."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import colorsci as cs  # noqa: E402

A, B = 0.07, 0.42   # toy log: v = A*log2(x/0.18) + B  (18% grey at 0.42, ~14 stops in 0..1)


def log_encode(lin):
    return np.clip(A * np.log2(np.maximum(lin, 1e-6) / 0.18) + B, 0, 1)


def to_display(v):
    lin = 0.18 * 2 ** ((v - B) / A)
    return np.where(lin < 0.018, 4.5 * lin, 1.099 * np.clip(lin, 0, None) ** 0.45 - 0.099)  # Rec.709 OETF


def scene(n=40000, seed=0):
    """Linear-light scene: grey/white rocks & clouds (neutrals), blue sky, green grass, dark shadows, a highlight."""
    rng = np.random.default_rng(seed)
    parts = [
        (0.18, [1, 1, 1], 0.25), (0.05, [1, 1, 1], 0.10), (0.55, [1, 1, 1], 0.10),   # neutrals
        (0.30, [0.55, 0.75, 1.0], 0.25),                                             # sky
        (0.10, [0.45, 0.9, 0.35], 0.20),                                             # grass
        (0.01, [0.9, 0.9, 1.0], 0.096), (1.2, [1, 1, 1], 0.004),                      # shadow, rare specular
    ]
    px = []
    for lum, col, w in parts:
        k = int(n * w)
        c = np.array(col) / (np.array(col) @ [cs.KR, cs.KG, cs.KB])
        px.append(lum * c * rng.lognormal(0, 0.15, (k, 1)))
    return np.concatenate(px)


def make_lut(tmp_path):
    p = tmp_path / "toylog_to_709.cube"
    cs.write_cube(p, to_display, n=33)
    return cs.load_cube(p)


def test_lut_roundtrip_matches_analytic(tmp_path):
    lut = make_lut(tmp_path)
    v = np.random.default_rng(1).random((5000, 3))
    err = np.abs(cs.apply_lut(lut, v) - np.clip(to_display(v), 0, 1))
    assert err.max() < 0.02 and err.mean() < 0.002   # trilinear on a 33^3 grid


import pytest  # noqa: E402


@pytest.mark.parametrize("order", cs.ORDERS)
def test_recovers_exposure_and_white_balance(tmp_path, order):
    lut = make_lut(tmp_path)
    lin = scene()
    bad = lin * 0.5 * np.array([1.35, 1.0, 0.7])        # 1 stop under + strong warm (tungsten-ish) cast
    g = cs.solve(log_encode(bad), lut, "youtube", order=order)
    assert g.before["neutral_cast"] > 0.03                # the cast is visible before
    assert g.after["neutral_cast"] < 0.006                # neutrals land on the vectorscope centre
    assert abs(g.after["mid_p50"] - cs.LOOKS["youtube"]["mid"]) < 0.02
    assert abs(g.after["white_p99_5"] - cs.LOOKS["youtube"]["white"]) < 0.03
    assert abs(g.after["black_p0_5"] - cs.LOOKS["youtube"]["black"]) < 0.02
    # The log-space fix should be close to the physical truth: +1 stop = A*1 offset, WB = A*log2(1/gain) per channel
    truth = A * np.log2(np.array([1 / (0.5 * 1.35), 1 / 0.5, 1 / (0.5 * 0.7)]))
    rel = g.bal.offset - g.bal.offset[1]
    assert np.allclose(rel, truth - truth[1], atol=0.012)


@pytest.mark.parametrize("order", cs.ORDERS)
def test_shot_matching_converges_two_cameras(tmp_path, order):
    lut = make_lut(tmp_path)
    lin = scene(seed=2)
    a = cs.solve(log_encode(lin * 1.4 * np.array([0.9, 1, 1.15])), lut, "reel", order=order)    # bright, cool
    b = cs.solve(log_encode(lin * 0.6 * np.array([1.2, 1, 0.85])), lut, "reel", match=a.after, order=order)
    for k in ("mid_p50", "white_p99_5", "black_p0_5"):
        assert abs(a.after[k] - b.after[k]) < 0.025, k
    assert abs(a.after["sat_mean"] - b.after["sat_mean"]) < 0.01


def test_resolve_cdl_map_format():
    m = cs.CDL(slope=np.array([1.1, 1, 0.9]), sat=1.2).resolve_map(3)
    assert m["NodeIndex"] == "3" and m["Slope"] == "1.10000 1.00000 0.90000" and m["Saturation"] == "1.20000"


def test_no_neutrals_is_cautious(tmp_path):
    """Forest/sunset: nothing is grey. Must not force the dominant colour to grey; says so instead."""
    lut = make_lut(tmp_path)
    rng = np.random.default_rng(5)
    def col(c):
        c = np.array(c, float)
        return c / (c @ [cs.KR, cs.KG, cs.KB])
    lin = np.concatenate([0.12 * col([0.45, 0.9, 0.35]) * rng.lognormal(0, 0.1, (30000, 1)),
                          0.30 * col([1.0, 0.6, 0.3]) * rng.lognormal(0, 0.1, (10000, 1))])
    g = cs.solve(log_encode(lin), lut, "youtube")
    assert any("half-applied" in n for n in g.notes)
    assert abs(g.bal.offset[0] - g.bal.offset[1]) < 0.06 and abs(g.bal.offset[2] - g.bal.offset[1]) < 0.06
    assert g.after["sat_mean"] > 0.5 * g.before["sat_mean"]   # greens/oranges survive


def test_flat_scene_keeps_mid_and_warns(tmp_path):
    lut = make_lut(tmp_path)
    lin = scene() * 0 + 0.18 * np.random.default_rng(6).lognormal(0, 0.25, (40000, 1)) * np.ones(3)  # ~1.5 stops
    g = cs.solve(log_encode(lin), lut, "reel")
    assert abs(g.after["mid_p50"] - cs.LOOKS["reel"]["mid"]) < 0.02
    assert any("scene range too small" in n for n in g.notes)


def test_highlight_saturation_capped_for_skies(tmp_path):
    """A bright, very saturated sky must not be pushed further (it bands/blocks after YouTube/Instagram encoding)."""
    lut = make_lut(tmp_path)
    rng = np.random.default_rng(7)
    def col(c):
        c = np.array(c, float)
        return c / (c @ [cs.KR, cs.KG, cs.KB])
    lin = np.concatenate([0.9 * col([0.35, 0.65, 1.0]) * rng.lognormal(0, 0.05, (20000, 1)),   # bright saturated sky
                          0.18 * rng.lognormal(0, 0.4, (20000, 1)) * np.ones(3)])               # neutral ground
    g = cs.solve(log_encode(lin), lut, "reel")
    assert g.after["hi_sat_p95"] <= cs.LOOKS["reel"]["hi_sat_max"] + 0.01
    assert g.cont.sat < cs.LOOKS["reel"]["sat_mult"]          # held back from the reel's usual boost
