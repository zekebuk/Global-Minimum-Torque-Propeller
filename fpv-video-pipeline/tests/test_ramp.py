"""fpvpipe/ramp.py: eased speed curves, their timeline length, beat-keeping out-points, and the two renderings
(ffmpeg setpts expression, Fusion TimeStretcher keys) agreeing with each other."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import cutlist as cl, ramp  # noqa: E402

PTS = [[10.0, 1.5], [11.0, 0.5], [11.4, 0.5], [12.4, 1.5]]


def test_speed_holds_outside_and_eases_between():
    v = ramp.speed_at(PTS, np.array([9.0, 10.0, 10.5, 11.2, 12.4, 20.0]))
    assert v[0] == 1.5 and v[1] == 1.5 and v[4] == 1.5 and v[5] == 1.5
    assert abs(v[2] - 1.0) < 1e-9 and v[3] == 0.5                  # eased midpoint, flat hold
    q = ramp.speed_at([[0.0, 1.0], [1.0, 2.0]], np.array([0.01, 0.1, 0.9, 0.99]))
    assert q[1] - q[0] < 0.02 and q[3] - q[2] < 0.02                # smootherstep: nearly flat at both ends


def test_duration_is_the_integral_and_constant_ramp_matches_speed():
    assert abs(ramp.duration([[0.0, 2.0]], 0.0, 4.0) - 2.0) < 1e-6
    d = ramp.duration(PTS, 10.0, 12.4)
    assert 2.4 / 1.5 < d < 2.4 / 0.5                                 # between all-fast and all-slow


def test_fit_out_keeps_the_beat():
    c = cl.CutList("T", 30.0, [cl.Shot("x.mp4", 10.0, 12.4)])
    before = c.shots[0].dur
    out = c.set_ramp(1, PTS)
    assert abs(c.shots[0].dur - before) < 1e-3 and abs(out - 12.4) > 0.05   # the out-point moved to keep the length
    assert abs(c.shots[0].avg_speed - (out - 10.0) / before) < 1e-6
    assert abs(ramp.fit_out([[0.0, 2.0]], 0.0, 3.0) - 6.0) < 1e-6              # constant 2x: twice the source
    c.set_ramp(1, [])
    assert c.shots[0].ramp == [] and abs(c.shots[0].dur - (out - 10.0)) < 1e-9


def test_setpts_and_fusion_keys_agree_with_source_time():
    in_s, out_s, fps = 10.0, ramp.fit_out(PTS, 10.0, 2.4), 30.0
    expr = ramp.setpts_expr(PTS, in_s, out_s)
    assert expr.startswith("if(lt(T") and "/TB" in expr and "'" not in expr
    # evaluate the expression the way ffmpeg would (TB = 1) and compare with the exact integral
    py = expr.replace("\\,", ",").replace("/TB", "").replace("if(", "IF(")
    t, tau = ramp._grid(PTS, in_s, out_s)
    for T in np.linspace(0, out_s - in_s, 9):
        got = eval(py, {"IF": lambda c, a, b: a if c else b, "lt": lambda a, b: a < b, "T": T})  # noqa: S307
        assert abs(got - np.interp(in_s + T, t, tau)) < 0.004
    base = (out_s - in_s) / 2.4
    keys = ramp.fusion_keyframes(PTS, in_s, out_s, fps, base, 60.0)
    ks = sorted(keys)
    assert ks[0] == 0.0 and keys[0.0] == 0.0 and len(ks) == 73
    assert abs(ks[-1] - 72 * base * 2) < 1e-3                          # comp time runs 2x faster than the timeline (60p)
    assert abs(keys[ks[-1]] - (out_s - in_s) * 60.0) < 0.05            # last key = last source frame
    assert all(keys[b] > keys[a] for a, b in zip(ks, ks[1:]))         # monotonic: never plays backwards
    # the slow hold: 0.5x on a 60p source = one source frame per timeline frame
    mid = [keys[b] - keys[a] for a, b in zip(ks, ks[1:])][len(ks) // 2]
    assert abs(mid - 1.0) < 0.05


def test_base_speed_is_a_whole_comp_frame_step_that_covers_the_span():
    assert ramp.base_speed(0.82, 59.94, 29.97) == 1.0
    assert ramp.base_speed(1.0, 59.94, 29.97) == 1.0
    assert ramp.base_speed(1.07, 59.94, 29.97) == 1.5
    assert ramp.base_speed(2.4, 59.94, 29.97) == 2.5
    assert ramp.base_speed(0.9, 29.97, 29.97) == 1.0          # 30p source: whole timeline frames


def test_fit_shape_spans_the_moved_out_point():
    pts, out = ramp.fit_shape(10.0, 12.4, 2.4, lambda o: ramp.three_point(10.0, o, 11.0, fast=1.8, slow=0.6))
    assert pts[-1][0] == out and abs(ramp.duration(pts, 10.0, out) - 2.4) < 3e-3   # ramps back up by the cut


def test_three_point_shape():
    pts = ramp.three_point(0.0, 3.0, 1.5, fast=1.6, slow=0.4, hold_s=0.4)
    assert pts == [[0.0, 1.6], [1.3, 0.4], [1.7, 0.4], [3.0, 1.6]]
    assert ramp.three_point(0.0, 3.0, 0.0)[0] == [0.0, 1.5]        # hero at the head: no zero-length segments
    assert ramp.describe(pts) == "1.6x@0.00 -> 0.4x@1.30 -> 0.4x@1.70 -> 1.6x@3.00"
