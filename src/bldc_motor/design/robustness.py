"""Production-tolerance Monte Carlo: yield and spread of an as-built design.

A design that sits exactly on a constraint boundary (e.g. burst temperature = limit) is
optimal on paper and fails half the time in production. Sampling the manufacturing tolerances
in ``manufacturing.TOLERANCES`` gives the yield, i.e. the fraction of built motors that still
meet every mission constraint, and the Kv spread that the bench acceptance test must allow for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from bldc_motor.design import manufacturing
from bldc_motor.design import mission as mission_mod
from bldc_motor.design.model import Calibration


@dataclass
class YieldReport:
    samples: int
    yield_fraction: float
    score_mean: float
    score_p10: float
    kv_mean: float
    kv_cv: float  # coefficient of variation
    burst_winding_p90_c: float
    failure_modes: dict = field(default_factory=dict)


def tolerance_yield(
    design: dict, mission, n: int = 200, seed: int = 0, calibration: Calibration | None = None
) -> YieldReport:
    base = calibration or Calibration()
    rng = np.random.default_rng(seed)
    scores, kvs, temps, feasible = [], [], [], []
    failures: dict[str, int] = {}
    for built, factors in manufacturing.perturbed_designs(design, n, rng):
        cal = Calibration(
            kv_factor=base.kv_factor * factors["kv_factor"],
            resistance_factor=base.resistance_factor * factors["resistance_factor"],
            iron_loss_factor=base.iron_loss_factor,
            friction_factor=base.friction_factor,
            convection_factor=base.convection_factor,
        )
        ev = mission_mod.evaluate(built, mission, cal)
        scores.append(ev.score)
        feasible.append(ev.feasible)
        kvs.append(ev.metrics.get("kv_rpm_per_v", np.nan))
        temps.append(ev.metrics.get("burst_winding_c", np.nan))
        for key, value in ev.penalties.items():
            if value > 0:
                failures[key] = failures.get(key, 0) + 1
        for rule in ev.failed_rules:
            failures[f"dfm:{rule}"] = failures.get(f"dfm:{rule}", 0) + 1
    kv = np.array(kvs, float)
    return YieldReport(
        samples=n,
        yield_fraction=float(np.mean(feasible)),
        score_mean=float(np.mean(scores)),
        score_p10=float(np.percentile(scores, 10)),
        kv_mean=float(np.nanmean(kv)),
        kv_cv=float(np.nanstd(kv) / np.nanmean(kv)),
        burst_winding_p90_c=float(np.nanpercentile(temps, 90)),
        failure_modes=dict(sorted(failures.items(), key=lambda kv_: -kv_[1])),
    )
