"""Per-unit physics for the manufacturing simulation: as-built motor parameters and test outcomes.

Each simulated motor carries its own as-built state:

* **Tolerances** (``manufacturing.TOLERANCES``): airgap, magnet thickness, stack length, stator OD,
  Br lot variation, wire/termination resistance. Their effect on Kv and R comes from a finite-
  difference Jacobian of the analytical motor model (``design.model.analyze``) at the nominal design,
  so the simulation inherits the real sensitivities.
* **Turn-count errors** per phase (hand winding miscounts, CNC restarts after a jam). Phase
  resistance scales with turns (R_i ~ N_i), so line-to-line pairs are R_ij = R_i + R_j. Back-EMF
  phasors are E_i ~ N_i at 120 deg, so the line-to-line amplitude is ~ sqrt(N_i^2 + N_j^2 + N_i N_j)
  and the measured Kv follows the largest pair.
* **Rotor unbalance** from magnet-mass scatter (14 random vectors at the magnet radius) plus bell
  eccentricity. It is Rayleigh distributed, and each balancing run leaves a random residual fraction.

Tests apply the acceptance specs of DWG-003 / spec sheet with instrument noise:
R_ll each pair within ±6 %, pair imbalance <= 2 %, Kv within ±3σ of the tolerance scatter (≈ ±5.5 %),
unbalance <= 11 mg·mm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from bldc_motor.design import manufacturing, model

TOL_KEYS = ("airgap_mm", "magnet_mm", "stack_mm", "stator_od_mm")


@dataclass(frozen=True)
class TestSpec:
    r_tol: float = 0.06  # each R_ll pair within ±6 % of nominal
    imbalance_tol: float = 0.02  # (max - min) / mean of the three pairs
    kv_tol: float = 0.03
    unbalance_limit_mg_mm: float = 11.0  # ISO 21940-11 G2.5 at 36,000 rpm for the 16.6 g rotor
    sigma_r: float = 0.004  # 0.1 % instrument + ~1 K temperature-correction error (0.39 %)
    sigma_kv: float = 0.005  # supply + tachometer
    hipot_detect: float = 0.98


@dataclass
class Sensitivity:
    """d ln(Kv)/dx and d ln(R)/dx for each tolerance input, plus nominal values."""

    kv0: float
    r0: float
    dlnkv: dict = field(default_factory=dict)
    dlnr: dict = field(default_factory=dict)
    turns_per_phase: int = 24
    unbalance_sigma_mg_mm: float = 200.0


def sensitivity(design: dict) -> Sensitivity:
    base = model.analyze(design)
    kv0, r0 = base.motor.kv_rpm_per_v, base.motor.r_ll_ohm
    s = Sensitivity(kv0=kv0, r0=r0, turns_per_phase=base.internals["n_series_turns_per_phase"])
    for key in TOL_KEYS:
        h = 0.01
        hi = model.analyze({**design, key: design[key] + h}).motor
        lo = model.analyze({**design, key: design[key] - h}).motor
        s.dlnkv[key] = (math.log(hi.kv_rpm_per_v) - math.log(lo.kv_rpm_per_v)) / (2 * h)
        s.dlnr[key] = (math.log(hi.r_ll_ohm) - math.log(lo.r_ll_ohm)) / (2 * h)
    # Rotor unbalance: 1 % magnet-mass scatter at the magnet mid-radius, plus 5 µm bell eccentricity.
    i, g = base.internals, base.geometry
    poles = design["slot_pole"][1]
    m_mag_mg = i["mass_magnets_g"] / poles * 1000.0
    r_mag_mm = (g.r_m_in + g.r_m_out) / 2 * 1000.0
    per_magnet = 0.01 * m_mag_mg * r_mag_mm
    magnets = per_magnet * math.sqrt(poles / 2.0)
    eccentric = i["rotor_mass_g"] * 1000.0 * 0.005
    s.unbalance_sigma_mg_mm = math.hypot(magnets, eccentric)
    return s


def sample_tolerances(rng: np.random.Generator) -> dict:
    t = manufacturing.TOLERANCES
    return {
        **{k: rng.normal(0.0, t[k]) for k in TOL_KEYS},
        "br": rng.normal(0.0, t["br_fraction"]),
        "r": rng.normal(0.0, t["resistance_fraction"]),
    }


def line_pairs(turns: tuple[int, int, int]) -> list[tuple[int, int]]:
    a, b, c = turns
    return [(a, b), (b, c), (c, a)]


def true_r_pairs(s: Sensitivity, turns: tuple[int, int, int], tol: dict, solder_extra: float = 0.0) -> list[float]:
    """Line-to-line resistances [ohm] for actual turns per phase and tolerances."""

    n0 = s.turns_per_phase
    scale = math.exp(sum(s.dlnr[k] * tol[k] for k in TOL_KEYS)) * (1.0 + tol["r"])
    r_phase0 = s.r0 / 2.0
    return [r_phase0 * scale * (ni + nj) / n0 + solder_extra for ni, nj in line_pairs(turns)]


def true_kv(s: Sensitivity, turns: tuple[int, int, int], tol: dict) -> float:
    n0 = s.turns_per_phase
    amp = max(math.sqrt(ni * ni + nj * nj + ni * nj) for ni, nj in line_pairs(turns)) / (math.sqrt(3.0) * n0)
    flux = math.exp(sum(s.dlnkv[k] * tol[k] for k in TOL_KEYS)) / (1.0 + tol["br"])
    return s.kv0 * flux / amp


def r_test(s: Sensitivity, r_pairs: list[float], spec: TestSpec, rng: np.random.Generator) -> bool:
    measured = [r * (1.0 + rng.normal(0.0, spec.sigma_r)) for r in r_pairs]
    if any(abs(m / s.r0 - 1.0) > spec.r_tol for m in measured):
        return False
    mean = sum(measured) / 3.0
    return (max(measured) - min(measured)) / mean <= spec.imbalance_tol


def kv_test(s: Sensitivity, kv: float, spec: TestSpec, rng: np.random.Generator) -> bool:
    return abs(kv * (1.0 + rng.normal(0.0, spec.sigma_kv)) / s.kv0 - 1.0) <= spec.kv_tol


def r_test_fail_probability(s: Sensitivity, r_pairs: list[float], spec: TestSpec) -> float:
    """Exact P(imbalance test fails) for given true pair resistances (independent normal readings).

    P(range <= t) = sum_i  integral f_i(u) prod_{j != i} [F_j(u + t) - F_j(u)] du  (i = the minimum),
    with t = imbalance_tol * mean of the true pairs. The ±6 % band is ignored (it is far wider).
    """

    from math import erf, sqrt

    mus = [r / s.r0 for r in r_pairs]
    sig = [m * spec.sigma_r for m in mus]
    t = spec.imbalance_tol * sum(mus) / 3.0
    lo, hi = min(mus) - 8 * max(sig), max(mus) + 8 * max(sig)
    u = np.linspace(lo, hi, 4001)
    du = u[1] - u[0]

    def cdf(x, m, sd):
        return 0.5 * (1.0 + np.vectorize(erf)((x - m) / (sd * sqrt(2.0))))

    total = 0.0
    for i in range(3):
        pdf_i = np.exp(-0.5 * ((u - mus[i]) / sig[i]) ** 2) / (sig[i] * sqrt(2 * math.pi))
        prod = np.ones_like(u)
        for j in range(3):
            if j != i:
                prod *= cdf(u + t, mus[j], sig[j]) - cdf(u, mus[j], sig[j])
        total += float(np.sum(pdf_i * prod) * du)
    return 1.0 - total


def kv_sigma(s: Sensitivity) -> float:
    """1-sigma relative Kv scatter from the production tolerances (linearised)."""

    t = manufacturing.TOLERANCES
    var = sum((s.dlnkv[k] * t[k]) ** 2 for k in TOL_KEYS) + t["br_fraction"] ** 2
    return math.sqrt(var)


def balance(s: Sensitivity, spec: TestSpec, rng: np.random.Generator, max_runs: int = 4) -> tuple[int, float, bool]:
    """Balancing runs needed: Rayleigh initial unbalance, residual fraction U(0.03, 0.15) per run, 2 mg·mm floor."""

    u = s.unbalance_sigma_mg_mm * math.sqrt(-2.0 * math.log(max(rng.random(), 1e-12)))
    runs = 0
    while runs < max_runs:
        runs += 1
        u = u * rng.uniform(0.03, 0.15) + abs(rng.normal(0.0, 2.0))
        if u <= spec.unbalance_limit_mg_mm:
            return runs, u, True
    return runs, u, False
