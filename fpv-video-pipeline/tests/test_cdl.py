"""Node 1 as an ASC CDL must reproduce LogPrimary exactly (the shadow lift moves to node 2). No Resolve needed."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import grade5  # noqa: E402


def cdl_apply(v: dict, x):
    y = x * np.array(v["slope"]) + np.array(v["offset"])
    return np.power(np.maximum(y, 0), np.array(v["power"]))


def test_cdl_matches_primary_and_shadow_moves_to_node2():
    rng = np.random.default_rng(1)
    x = rng.uniform(0, 1, (5000, 3))
    p = grade5.LogPrimary(lift=np.array([-0.05, -0.04, -0.06]), gain=np.array([1.08, 1.0, 0.95]), power=0.9,
                          offset=0.03, lift_stops=1.5, lift_at=0.3)
    v = p.cdl()
    np.testing.assert_allclose(cdl_apply(v, x), grade5.Primary.apply(p, x), atol=1e-5)
    np.testing.assert_allclose(p.shadow(cdl_apply(v, x)), p.apply(x), atol=1e-5)
    assert v["saturation"] == 1.0
