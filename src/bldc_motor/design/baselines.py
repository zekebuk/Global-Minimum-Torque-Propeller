"""Non-RL optimisers on the same objective and evaluation budget, plus local refinement.

These exist to keep the RL result honest: a single fixed mission is a static black-box
optimisation problem, where population methods such as differential evolution are strong.
The pipeline runs all of them and keeps the best design regardless of which method found it.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import differential_evolution

from bldc_motor.design import geometry
from bldc_motor.design import mission as mission_mod
from bldc_motor.design.env import DesignArchive
from bldc_motor.design.model import Calibration


def _objective(mission, calibration, archive: DesignArchive):
    def score(u: np.ndarray) -> float:
        design = geometry.snap(geometry.decode(u))
        ev = mission_mod.evaluate(design, mission, calibration)
        archive.offer(ev)
        return ev.score

    return score


def random_search(mission, budget: int, seed: int = 0, calibration: Calibration | None = None) -> DesignArchive:
    archive = DesignArchive()
    score = _objective(mission, calibration, archive)
    rng = np.random.default_rng(seed)
    for _ in range(budget):
        score(rng.random(geometry.DIMENSION))
    return archive


def differential_evolution_search(
    mission, budget: int, seed: int = 0, popsize: int = 15, calibration: Calibration | None = None
) -> DesignArchive:
    archive = DesignArchive()
    score = _objective(mission, calibration, archive)
    dim = geometry.DIMENSION
    maxiter = max(int(budget / (popsize * dim)) - 1, 1)
    differential_evolution(
        lambda u: -score(u),
        bounds=[(0.0, 1.0)] * dim,
        maxiter=maxiter,
        popsize=popsize,
        seed=seed,
        polish=False,
        init="latinhypercube",
        mutation=(0.5, 1.0),
        recombination=0.7,
        tol=0.0,
        atol=0.0,
    )
    return archive


def local_refine(
    design: dict, mission, calibration: Calibration | None = None, max_passes: int = 6
) -> tuple[dict, list[str]]:
    """Greedy coordinate search on the manufacturable grid (one snap step / one discrete step).

    This mirrors a final engineering clean-up pass: nudge each dimension to its neighbouring
    manufacturable value and keep the change if the objective improves.
    """

    best = geometry.snap(dict(design))
    best_score = mission_mod.evaluate(best, mission, calibration).score
    changes: list[str] = []
    for _ in range(max_passes):
        improved = False
        for param in geometry.PARAMETERS:
            for candidate_value in _neighbours(param, best[param.name]):
                trial = dict(best)
                trial[param.name] = candidate_value
                trial_score = mission_mod.evaluate(trial, mission, calibration).score
                if trial_score > best_score + 1e-9:
                    changes.append(
                        f"{param.name}: {best[param.name]} -> {candidate_value} ({best_score:+.4f} -> {trial_score:+.4f})"
                    )
                    best, best_score, improved = trial, trial_score, True
        if not improved:
            break
    return best, changes


def _neighbours(param: geometry.Parameter, value):
    if param.discrete:
        index = param.choices.index(value)
        return [param.choices[i] for i in (index - 1, index + 1) if 0 <= i < len(param.choices)]
    step = param.snap or (param.high - param.low) / 100.0
    out = []
    for delta in (-step, step):
        candidate = round((value + delta) / step) * step
        if param.low - 1e-12 <= candidate <= param.high + 1e-12:
            out.append(float(candidate))
    return out
