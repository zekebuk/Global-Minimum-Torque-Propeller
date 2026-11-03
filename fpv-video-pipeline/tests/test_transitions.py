"""fpvpipe/transitions.py: the rule that picks a cut's transition from the music and the motion, the xfade names,
and the cut list / ramp plumbing that keeps the beat grid when transitions overlap the cuts."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import cutlist as cl, ramp, transitions as tr  # noqa: E402


def info(**kw):
    base = dict(t=3.0, onset=0.2, jump_db=0.0, motion_out=0.3, motion_in=0.3, direction="none")
    base.update(kw)
    return tr.CutInfo(**base)


def test_hard_beats_are_hard_cuts():
    assert tr.choose(info(onset=0.9))["type"] == "cut"
    assert tr.choose(info(jump_db=4.0))["type"] == "cut"


def test_hard_cuts_are_the_default_soft_moments_barely_soften():
    assert tr.choose(info(onset=0.5, jump_db=2.5))["type"] == "cut"            # a loudness jump is a hit
    assert tr.choose(info(onset=0.5, motion_out=0.8, motion_in=0.7)) == {"type": "blur", "dur": 0.2}
    assert tr.choose(info(onset=0.5, motion_out=0.1, motion_in=0.6)) == {"type": "dissolve", "dur": 0.12}
    assert tr.choose(info(onset=0.5, motion_out=0.4, motion_in=0.4))["type"] == "cut"   # in between: still a cut
    assert tr.choose(info(onset=0.5, motion_out=0.9, motion_in=0.9, direction="down"))["type"] == "blur"


def test_xfade_specs():
    assert tr.xfade_name({"type": "flash"}) == "custom"
    spec = tr.xfade_spec({"type": "flash", "strength": 0.5})
    assert spec.startswith("transition=custom:expr='min(255\\,A*P+B*(1-P)+128*") and "eq(PLANE\\,0)" in spec
    assert tr.xfade_spec({"type": "flow", "dir": "up"}) == "transition=smoothup"
    assert tr.xfade_spec({"type": "blur"}) == "transition=hblur"
    assert tr.xfade_spec({"type": "dissolve"}) == "transition=fade"


def test_trans_dur_and_cut():
    s = cl.Shot("x.mp4", 1.0, 3.0, trans_in={"type": "flow", "dur": 0.33, "dir": "left"})
    assert s.trans_dur == 0.33
    assert cl.Shot("x.mp4", 1.0, 3.0, trans_in={"type": "cut", "dur": 0.5}).trans_dur == 0.0
    assert cl.Shot("x.mp4", 1.0, 3.0).trans_dur == 0.0


def test_motion_direction_from_a_shift():
    # a picture that slides right between frames = camera panning left: travel direction "right"
    rng = np.random.default_rng(0)
    base = rng.random((84, 96)).astype(np.float32) * 255
    frames = [np.repeat(np.roll(base, k * 3, axis=1)[:, :, None], 3, axis=2) for k in range(6)]
    import fpvpipe.grading as grading
    orig = grading.sample_frames
    grading.sample_frames = lambda *a, **k: frames
    try:
        e, d = tr.motion(Path("x.mp4"), 0.0, 0.5)
    finally:
        grading.sample_frames = orig
    assert e > 0 and d == "right"


def test_ramp_keys_extend_through_the_handles():
    pts = [[10.0, 1.5], [12.0, 0.8]]
    keys = ramp.fusion_keyframes(pts, 10.0, 12.0, 30.0, 1.0, 60.0, pad_s=0.5)
    ks = sorted(keys)
    assert ks[0] < 0 and keys[ks[0]] < 0                  # keys before the in-point, reaching into source before it
    assert abs(keys[0.0]) < 1e-6                           # still exactly the in-point at the clip's first frame
    assert all(keys[b] > keys[a] for a, b in zip(ks, ks[1:]))
