"""cinelook node tree: the output transform's anchor points and that exposure solving lands where it says."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import cinelook as cl  # noqa: E402


def gplog_encode(x):
    return np.log(np.asarray(x) * 399 + 1) / np.log(400)


def test_grey_lands_on_grey_out_and_curve_is_monotonic():
    look = cl.Look(sky_sat=1.0, sky_dens=0.0, cool_lo=0.0, warm_hi=0.0)
    grey = np.full((1, 3), gplog_encode(0.18))
    assert abs(cl.render(grey, look, cl.ShotNodes())[0, 0] - look.grey_out) < 0.005
    ramp = np.repeat(np.linspace(0, 1, 200)[:, None], 3, 1)
    y = cl.render(ramp, look, cl.ShotNodes())[:, 1]
    assert np.all(np.diff(y) >= -1e-9) and y[0] < 0.02 and y[-1] > 0.9     # dense blacks, bright but rolled-off whites


def test_neutral_stays_neutral():
    v = np.repeat(gplog_encode(np.geomspace(0.002, 2, 50))[:, None], 3, 1)
    out = cl.render(v, cl.Look(cool_lo=0.0, warm_hi=0.0), cl.ShotNodes())
    assert np.abs(out - out[:, 1:2]).max() < 1e-6                           # sky node must not touch greys


def test_exposure_solve_hits_target():
    rng = np.random.default_rng(0)
    lin = 0.05 * rng.lognormal(0, 0.8, (20000, 1)) * np.ones(3)              # ~2 stops under
    look = cl.Look()
    ev = cl.solve_exposure(gplog_encode(lin), look, target_mid=0.44)
    out = cl.render(gplog_encode(lin), look, cl.ShotNodes(ev=ev))
    assert 1.0 < ev < 3.0 and abs(np.median(out @ [cl.KR, cl.KG, cl.KB]) - 0.44) < 0.01
