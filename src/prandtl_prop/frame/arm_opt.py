"""Arm cross-section / taper optimisation (mixed integer: wall loops and skin layers are integers).

minimise   m_arm + m_download_equivalent          (grams per arm)
subject to every margin in arm_model.evaluate()['margins'] >= 0

Solved with scipy differential evolution (global, derivative-free, handles the integer print
variables through `integrality`), seeded for reproducibility. Constraint handling uses DE's
feasibility rules (Lampinen), so the result is feasible whenever any feasible point was found.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import NonlinearConstraint, differential_evolution

from .arm_model import ArmContext, evaluate

MARGIN_KEYS = [
    "f_vert", "f_lat", "f_tor", "static_strength", "interlaminar", "fatigue_goodman",
    "tip_slope", "joint_crash", "dovetail_fits", "channel_w", "channel_h", "tip_height",
]


def bounds_from_cfg(cfg: dict):
    a = cfg["arm"]
    return [
        tuple(a["width_bounds"]), tuple(a["root_height_bounds"]), tuple(a["taper_ratio_bounds"]),
        tuple(a["wall_loop_bounds"]), tuple(a["skin_layer_bounds"]),
    ]


class _Problem:
    """Picklable objective/constraint pair sharing one model evaluation per candidate."""

    def __init__(self, ctx: ArmContext):
        self.ctx = ctx
        self.key = None
        self.val = None

    def _eval(self, x):
        k = tuple(np.round(x, 9))
        if k != self.key:
            self.key, self.val = k, evaluate(x, self.ctx)
        return self.val

    def obj(self, x):
        r = self._eval(x)
        return r["objective_g"] if r["valid"] else 1e6

    def cons(self, x):
        r = self._eval(x)
        if not r["valid"]:
            return -np.ones(len(MARGIN_KEYS))
        return np.array([r["margins"][k] for k in MARGIN_KEYS])


def optimise_arm(ctx: ArmContext, maxiter: int = 120, popsize: int = 20, seed: int = 7, workers: int = 1) -> dict:
    prob = _Problem(ctx)
    nlc = NonlinearConstraint(prob.cons, 0.0, np.inf)
    sol = differential_evolution(
        prob.obj, bounds_from_cfg(ctx.cfg), constraints=(nlc,), integrality=[False, False, False, True, True],
        maxiter=maxiter, popsize=popsize, seed=seed, tol=1e-8, polish=False, updating="deferred", workers=workers,
    )
    best = evaluate(sol.x, ctx)
    feasible = best["valid"] and min(best["margins"].values()) >= -1e-6
    active = sorted(best["margins"].items(), key=lambda kv: kv[1])[:3] if best["valid"] else []
    return {"x": sol.x.tolist(), "result": best, "feasible": feasible, "active_constraints": active,
            "nfev": int(sol.nfev), "message": sol.message}
