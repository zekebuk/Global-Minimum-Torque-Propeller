"""fpvpipe/titles.py, the titles that live in the scene: the ground tracker, the spire occlusion and the fly-through."""
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import titles  # noqa: E402

FONT = Path("C:/Windows/Fonts/arial.ttf")


def test_shift_finds_a_known_translation():
    rng = np.random.default_rng(1)
    a = np.asarray(Image.fromarray((rng.random((120, 160)) * 255).astype(np.uint8)).resize((320, 240)), np.float32)
    b = np.roll(np.roll(a, 7, axis=1), -4, axis=0)
    dx, dy = titles._shift(a, b)
    assert abs(dx - 7) < 0.5 and abs(dy + 4) < 0.5


def test_fly_scale_creeps_then_accelerates_past_the_lens():
    s = [titles.fly_scale(t, 5.0, 1.3, 6.0) for t in np.linspace(0, 5, 51)]
    assert all(b >= a for a, b in zip(s, s[1:]))
    assert abs(s[0] - 0.92) < 1e-6 and abs(s[-1] - 6.0) < 1e-6
    assert titles.fly_scale(3.7, 5.0) - titles.fly_scale(0.0, 5.0) < 0.14     # the hold barely moves


def test_letters_rise_one_after_another():
    assert titles.emerge(0.0, 0) == 0.0 and titles.emerge(2.0, 3) == 1.0
    assert titles.emerge(0.3, 0) > titles.emerge(0.3, 1) > titles.emerge(0.3, 2)


def scene():
    """Hazy grey frame with a broad orange wall on the left and a narrow orange spire standing up on the right."""
    f = np.full((100, 200, 3), 170, np.uint8)
    f[60:, :90] = (200, 110, 40)          # wall
    f[60:, 150:] = (200, 110, 40)
    f[20:, 120:130] = (200, 110, 40)      # spire
    return f


def test_occlusion_spires_only_spares_the_wall():
    text = np.zeros((100, 200), bool)
    text[40:70, 10:190] = True
    full = titles.occlusion_mask(scene(), text, (200, 100))
    spire = titles.occlusion_mask(scene(), text, (200, 100), spires_only=True)
    assert full[65, 40] > 0.5                                 # plain mode: the wall hides the letters
    assert spire[65, 40] < 0.1 and spire[50, 125] > 0.5       # spires only: just the spire does
    assert spire[10, 125] == 0                                # nothing outside the word


@pytest.mark.skipif(not FONT.exists(), reason="needs a system font")
def test_flythrough_writes_a_sequence_that_leaves_the_frame(tmp_path):
    t = {"text": "AB", "font": str(FONT), "size": 0.2, "tracking": 0.3, "dur": 1.6, "fly_out": 0.4, "anchor": [0.5, 0.4]}
    n = 9
    path = np.column_stack([np.zeros(n), np.linspace(0, 0.02, n), np.linspace(1, 1.1, n)])
    pattern, count = titles.render_flythrough(t, 270, 236, path, tmp_path, 5.0)
    assert count == n and pattern.endswith("f_%04d.png")
    alpha = [np.asarray(Image.open(tmp_path / f"f_{k:04d}.png"))[..., 3] for k in range(n)]
    assert alpha[0].max() == 0                                # nothing has arrived on the first frame
    assert alpha[5].max() > 200                               # solid during the hold
    assert alpha[-1].max() == 0                               # gone on the cut


@pytest.mark.skipif(not FONT.exists(), reason="needs a system font")
def test_seated_stays_put_and_fades_in_place(tmp_path):
    t = {"text": "AB", "font": str(FONT), "size": 0.2, "tracking": 0.3, "dur": 1.6, "seated": True, "fade_in": 0.4,
         "fade_out": 0.4, "anchor": [0.5, 0.4]}
    n = 9
    path = np.column_stack([np.zeros(n), np.zeros(n), np.ones(n)])     # a still shot: the word must not move at all
    titles.render_flythrough(t, 270, 236, path, tmp_path, 5.0)
    alpha = [np.asarray(Image.open(tmp_path / f"f_{k:04d}.png"))[..., 3].astype(float) for k in range(n)]
    assert alpha[0].max() == 0 and alpha[-1].max() == 0
    assert np.abs(alpha[3] - alpha[5]).max() < 2                        # solid and motionless through the hold
