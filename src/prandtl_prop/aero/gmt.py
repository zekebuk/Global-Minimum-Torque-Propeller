"""Global-minimum-torque (GMT) twist design under BEMT, and the Prandtl-bell family.

First principles. With the chord, airfoils, rpm, and free stream fixed, BEMT treats every annulus
independently: station i's thrust and torque per unit span, T'_i and Q'_i (= r F_tangential), depend
on its own twist beta_i only. Minimising the shaft torque at a required thrust,

    min  Q = sum_i Q'_i(beta_i) dr_i   subject to   T = sum_i T'_i(beta_i) dr_i = T_req,

has the Lagrangian sum_i dr_i (Q'_i - lambda T'_i), which separates: for a given multiplier
lambda each station minimises Q'_i - lambda T'_i over its own twist. That 1-D problem is solved
globally by scanning the twist (then refining), so the result is the global minimum over all twist
distributions, not a local optimum of a parametrised shape. lambda (m) is the marginal torque per
newton of thrust, and at the optimum every station has the same dQ'/dT' = lambda.

- With a drag-free polar this is the Betz/MIL condition. Profile drag, tip loss, and stall limits
  move the optimum away from MIL; that difference is what "GMT" captures exactly here.
- Adding mu times the root flap bending moment, M = sum_i T'_i (r_i - r_root) dr_i / B, to the
  objective gives each station an effective multiplier lambda - mu (r_i - r_root) / B. The tip
  unloads and the load moves inboard: the rotor analogue of Prandtl's 1933 bell spanload (minimum
  induced drag for a given root bending moment). mu = 0 is the unconstrained global minimum torque.
- Stall margin: candidate twists whose design-point Cl exceeds ``cl_limit`` are infeasible.
- Chord too (``design_with_chord``). Station i's loads depend on its own chord and twist only, so
  the same Lagrangian separates over (c_i, beta_i): each station minimises Q'_i - lambda T'_i over a
  chord x twist grid within manufacturing bounds. That is the global minimum torque over planform and
  twist together at the given rpm. With profile drag cd = cd0 + k Cl^2, the optimum runs each loaded
  section near the best lift-to-drag Cl* = sqrt(cd0 / k) (torque per thrust ~ cd / Cl), with the
  chord set by the circulation the annulus must carry; the bounds and the Cl limit cut in at the
  root and the tip.

Assumptions: steady axial flow (hover or climb), no swirl, Prandtl tip loss, positive loading at the
design point. The vectorised station solver handles analytic polars; table polars fall back to the
scalar ``solve_station``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from scipy.optimize import brentq

from prandtl_prop.aero.bemt import BemtResult, BladeStations, OperatingPoint, solve_rotor, solve_station, trim_to_thrust
from prandtl_prop.aero.polars import AnalyticPolar, PolarPoint


@dataclass(frozen=True)
class StationTables:
    """Station loads over a twist grid: arrays (stations, candidates)."""

    beta_deg: np.ndarray
    thrust_per_span_N_m: np.ndarray
    torque_per_span_N: np.ndarray  # r * tangential force per span (N m / m)
    cl: np.ndarray
    alpha_deg: np.ndarray
    phi_deg: np.ndarray
    positive: np.ndarray  # positively loaded solution exists


@dataclass(frozen=True)
class GmtSolution:
    twist_deg: np.ndarray
    lambda_m: float
    bending_weight: float
    thrust_N: float
    torque_Nm: float
    root_moment_Nm: float  # flap moment per blade about r_root
    cl: np.ndarray
    alpha_deg: np.ndarray
    phi_deg: np.ndarray
    thrust_per_span_N_m: np.ndarray  # all blades
    torque_per_span_N: np.ndarray
    candidate_index: np.ndarray | None = None  # per station: the chosen column of the tables


class InviscidPolar:
    """A polar with its drag removed (Betz/MIL design reference)."""

    def __init__(self, polar):
        self.polar = polar

    def evaluate(self, airfoil: str, alpha_deg: float, reynolds: float) -> PolarPoint:
        point = self.polar.evaluate(airfoil, alpha_deg, reynolds)
        return replace(point, cd=0.0)

    def fingerprint(self) -> str:
        return "inviscid:" + self.polar.fingerprint()


def _drag_free(polar):
    return replace(polar, cd0=0.0, k=0.0) if isinstance(polar, AnalyticPolar) else InviscidPolar(polar)


def _tip_loss(blade_count: int, r_R: float, phi: np.ndarray) -> np.ndarray:
    if r_R >= 1.0:
        return np.zeros_like(phi)
    exponent = 0.5 * blade_count * (1.0 - r_R) / (r_R * np.maximum(np.abs(np.sin(phi)), 1.0e-12))
    return np.where(exponent > 50.0, 1.0, (2.0 / math.pi) * np.arccos(np.exp(-np.minimum(exponent, 50.0))))


def _station_vectorised(r_m: float, chord_m: float, betas: np.ndarray, op: OperatingPoint, polar: AnalyticPolar, tip_loss: bool):
    omega_r = op.omega_rad_s * r_m
    r_R = r_m / op.radius_m

    def loads(vi: np.ndarray):
        va = op.v_inf_m_s + vi
        phi = np.arctan2(va, omega_r)
        w2 = omega_r**2 + va**2
        alpha = betas - np.degrees(phi)
        cl = np.clip(polar.cl_alpha_per_rad * np.radians(alpha - polar.alpha0_deg), polar.cl_min, polar.cl_max)
        cd = polar.cd0 + polar.k * cl**2
        q_c = 0.5 * op.rho_kg_m3 * w2 * chord_m
        thrust = op.blade_count * q_c * (cl * np.cos(phi) - cd * np.sin(phi))
        tangential = op.blade_count * q_c * (cl * np.sin(phi) + cd * np.cos(phi))
        factor = _tip_loss(op.blade_count, r_R, phi) if tip_loss else np.ones_like(phi)
        residual = thrust - 4.0 * math.pi * op.rho_kg_m3 * r_m * factor * va * vi
        return residual, thrust, tangential, cl, alpha, phi

    zero = np.zeros_like(betas)
    positive = loads(zero)[0] > 0.0
    upper = np.maximum(omega_r * np.tan(np.radians(np.clip(betas, 1.0, 80.0))), 1.0e-3)
    for _ in range(40):
        still = loads(upper)[0] > 0.0
        if not still.any():
            break
        upper = np.where(still, 2.0 * upper, upper)
    low, high = zero.copy(), upper
    for _ in range(80):
        mid = 0.5 * (low + high)
        above = loads(mid)[0] > 0.0
        low, high = np.where(above, mid, low), np.where(above, high, mid)
    _, thrust, tangential, cl, alpha, phi = loads(0.5 * (low + high))
    return thrust, tangential * r_m, cl, alpha, np.degrees(phi), positive


def station_tables(stations: BladeStations, op: OperatingPoint, polar, beta_deg: np.ndarray, *, tip_loss: bool = True) -> StationTables:
    """Thrust and torque per span at every station for each candidate twist (``beta_deg``: 1-D, or per station)."""

    betas = np.broadcast_to(np.asarray(beta_deg, dtype=float), (len(stations.r_m), np.shape(beta_deg)[-1])).copy()
    shape = betas.shape
    out = {key: np.zeros(shape) for key in ("T", "Q", "cl", "alpha", "phi")}
    positive = np.zeros(shape, dtype=bool)
    for i, (r, c) in enumerate(zip(stations.r_m, stations.chord_m, strict=True)):
        if isinstance(polar, AnalyticPolar):
            T, Q, cl, alpha, phi, pos = _station_vectorised(float(r), float(c), betas[i], op, polar, tip_loss)
        else:
            rows = [
                solve_station(
                    r_m=float(r), chord_m=float(c), beta_deg=float(b), airfoil=stations.airfoil[i], op=op, polar=polar, tip_loss=tip_loss
                )
                for b in betas[i]
            ]
            T = np.array([s.thrust_per_span_N_m for s in rows])
            Q = np.array([s.tangential_per_span_N_m for s in rows]) * float(r)
            cl = np.array([s.cl for s in rows])
            alpha = np.array([s.alpha_deg for s in rows])
            phi = np.degrees([s.phi_rad for s in rows])
            pos = np.array([not s.negative_loading and s.converged for s in rows])
        out["T"][i], out["Q"][i], out["cl"][i], out["alpha"][i], out["phi"][i], positive[i] = T, Q, cl, alpha, phi, pos
    return StationTables(betas, out["T"], out["Q"], out["cl"], out["alpha"], out["phi"], positive)


def select_twist(
    tables: StationTables,
    stations: BladeStations,
    target_thrust_N: float,
    *,
    blade_count: int,
    cl_limit: float,
    bending_weight: float = 0.0,
    r_root_m: float | None = None,
) -> GmtSolution:
    """Solve the separable Lagrangian on the tables: bisection on lambda until the thrust target is met."""

    r, dr = stations.r_m, stations.dr_m
    r_root = float(r[0]) if r_root_m is None else r_root_m
    arm = (r - r_root) / blade_count
    feasible = tables.positive & (tables.cl <= cl_limit)
    # The tip station (F = 0) carries no thrust; any positively loaded twist is admissible there.
    if not feasible.any(axis=1).all():
        raise ValueError(f"No feasible twist at stations {np.where(~feasible.any(axis=1))[0].tolist()} (Cl limit {cl_limit})")
    rows = np.arange(len(r))

    def choose(lam: float) -> np.ndarray:
        effective = lam - bending_weight * arm
        q = tables.torque_per_span_N - effective[:, None] * tables.thrust_per_span_N_m
        return np.argmin(np.where(feasible, q, np.inf), axis=1)

    def thrust(lam: float) -> float:
        return float((tables.thrust_per_span_N_m[rows, choose(lam)] * dr).sum())

    low, high = 0.0, 0.01
    while thrust(high) < target_thrust_N:
        high *= 2.0
        if high > 1.0e4:
            raise ValueError(f"Thrust target {target_thrust_N:.3f} N is beyond the stall-limited maximum {thrust(high):.3f} N")
    for _ in range(100):
        mid = 0.5 * (low + high)
        low, high = (mid, high) if thrust(mid) < target_thrust_N else (low, mid)
    j = choose(high)
    T = tables.thrust_per_span_N_m[rows, j]
    return GmtSolution(
        twist_deg=tables.beta_deg[rows, j],
        lambda_m=high,
        bending_weight=bending_weight,
        thrust_N=float((T * dr).sum()),
        torque_Nm=float((tables.torque_per_span_N[rows, j] * dr).sum()),
        root_moment_Nm=float((T * dr * arm).sum()),
        cl=tables.cl[rows, j],
        alpha_deg=tables.alpha_deg[rows, j],
        phi_deg=tables.phi_deg[rows, j],
        thrust_per_span_N_m=T,
        torque_per_span_N=tables.torque_per_span_N[rows, j],
        candidate_index=j,
    )


def design(
    stations: BladeStations,
    op: OperatingPoint,
    polar,
    target_thrust_N: float,
    *,
    cl_limit: float,
    bending_weight: float = 0.0,
    tip_loss: bool = True,
    coarse_step_deg: float = 0.5,
    fine_step_deg: float = 0.02,
    beta_range_deg: tuple[float, float] = (-8.0, 60.0),
) -> GmtSolution:
    """Two-pass global search: a coarse twist grid over ``beta_range_deg``, then a fine grid +-1.5 coarse steps."""

    coarse = np.arange(beta_range_deg[0], beta_range_deg[1] + 1e-9, coarse_step_deg)
    first = select_twist(
        station_tables(stations, op, polar, coarse, tip_loss=tip_loss),
        stations,
        target_thrust_N,
        blade_count=op.blade_count,
        cl_limit=cl_limit,
        bending_weight=bending_weight,
    )
    offsets = np.arange(-1.5 * coarse_step_deg, 1.5 * coarse_step_deg + 1e-9, fine_step_deg)
    fine = first.twist_deg[:, None] + offsets[None, :]
    return select_twist(
        station_tables(stations, op, polar, fine, tip_loss=tip_loss),
        stations,
        target_thrust_N,
        blade_count=op.blade_count,
        cl_limit=cl_limit,
        bending_weight=bending_weight,
    )


def design_with_chord(
    stations: BladeStations,
    op: OperatingPoint,
    polar,
    target_thrust_N: float,
    *,
    cl_limit: float,
    chord_bounds_m: tuple[float, float],
    chord_count: int = 24,
    bending_weight: float = 0.0,
    tip_loss: bool = True,
    coarse_step_deg: float = 0.5,
    fine_step_deg: float = 0.05,
    beta_range_deg: tuple[float, float] = (-8.0, 60.0),
) -> tuple[BladeStations, GmtSolution]:
    """Global minimum torque over chord and twist (separable per station). Returns the blade and the solution."""

    def solve(chords: np.ndarray, betas: np.ndarray) -> tuple[GmtSolution, np.ndarray, np.ndarray]:
        """chords (stations, n_c), betas (stations, n_b): every pairing per station as one candidate axis."""

        parts = []
        for k in range(chords.shape[1]):
            trial = replace(stations, chord_m=chords[:, k].copy())
            parts.append(station_tables(trial, op, polar, betas, tip_loss=tip_loss))
        merged = StationTables(*(np.concatenate([getattr(t, f) for t in parts], axis=1) for f in StationTables.__dataclass_fields__))
        chord_of = np.concatenate([np.repeat(chords[:, k : k + 1], betas.shape[1], axis=1) for k in range(chords.shape[1])], axis=1)
        sol = select_twist(merged, stations, target_thrust_N, blade_count=op.blade_count, cl_limit=cl_limit, bending_weight=bending_weight)
        rows = np.arange(len(stations.r_m))
        return sol, chord_of[rows, sol.candidate_index], merged.beta_deg[rows, sol.candidate_index]

    n = len(stations.r_m)
    grid = np.geomspace(chord_bounds_m[0], chord_bounds_m[1], chord_count)
    betas = np.tile(np.arange(beta_range_deg[0], beta_range_deg[1] + 1e-9, coarse_step_deg), (n, 1))
    _, chord, beta = solve(np.tile(grid, (n, 1)), betas)
    ratio = grid[1] / grid[0]
    chords = np.clip(chord[:, None] * ratio ** np.linspace(-1.0, 1.0, 9)[None, :], chord_bounds_m[0], chord_bounds_m[1])
    betas = beta[:, None] + np.arange(-1.5 * coarse_step_deg, 1.5 * coarse_step_deg + 1e-9, fine_step_deg)[None, :]
    sol, chord, beta = solve(chords, betas)
    return replace(stations, chord_m=chord, twist_deg=beta), sol


def twist_grid(step_deg: float = 0.1, beta_range_deg: tuple[float, float] = (-8.0, 60.0)) -> np.ndarray:
    return np.arange(beta_range_deg[0], beta_range_deg[1] + 1e-9, step_deg)


def with_twist(stations: BladeStations, twist_deg: np.ndarray) -> BladeStations:
    return replace(stations, twist_deg=np.asarray(twist_deg, dtype=float))


def evaluate(stations: BladeStations, op: OperatingPoint, polar, target_thrust_N: float, *, tip_loss: bool = True) -> BemtResult:
    """The pipeline's scalar BEMT, trimmed in collective to the exact thrust (removes grid residue)."""

    return trim_to_thrust(stations, op, polar, target_thrust_N, tip_loss=tip_loss, bracket_deg=(-3.0, 3.0))


def helix_twist(stations: BladeStations, pitch_m: float) -> np.ndarray:
    """Constant geometric pitch (a conventional fixed-pitch prop): beta = atan(P / (2 pi r))."""

    return np.degrees(np.arctan(pitch_m / (2.0 * math.pi * stations.r_m)))


def helix_for_thrust(stations: BladeStations, op: OperatingPoint, polar, target_thrust_N: float, *, tip_loss: bool = True):
    """Constant-pitch prop of the same planform, pitch set for the thrust. Returns (pitch_m, result)."""

    def error(pitch: float) -> float:
        result = solve_rotor(with_twist(stations, helix_twist(stations, pitch)), op, polar, tip_loss=tip_loss)
        return float(result.summary["thrust_N"]) - target_thrust_N

    diameter = 2.0 * op.radius_m
    pitch = brentq(error, 0.05 * diameter, 2.0 * diameter, xtol=1e-9)
    twisted = with_twist(stations, helix_twist(stations, pitch))
    return pitch, solve_rotor(twisted, op, polar, tip_loss=tip_loss)


def compare(
    stations: BladeStations,
    op: OperatingPoint,
    polar,
    target_thrust_N: float,
    *,
    cl_limit: float,
    bending_weights: tuple[float, ...] = (0.0,),
    tip_loss: bool = True,
) -> dict[str, Any]:
    """GMT (and the bell family), Betz/MIL, and constant pitch, all at the same chord, rpm, and thrust."""

    out: dict[str, Any] = {"gmt": {}, "rows": []}
    for mu in bending_weights:
        solution = design(stations, op, polar, target_thrust_N, cl_limit=cl_limit, bending_weight=mu, tip_loss=tip_loss)
        result = evaluate(with_twist(stations, solution.twist_deg), op, polar, target_thrust_N, tip_loss=tip_loss)
        out["gmt"][mu] = (solution, result)
    mil = design(stations, op, _drag_free(polar), target_thrust_N, cl_limit=cl_limit, tip_loss=tip_loss)
    out["mil"] = (mil, evaluate(with_twist(stations, mil.twist_deg), op, polar, target_thrust_N, tip_loss=tip_loss))
    out["helix"] = helix_for_thrust(stations, op, polar, target_thrust_N, tip_loss=tip_loss)
    return out


def root_moment(result: BemtResult, r_root_m: float) -> float:
    """Flap bending moment per blade about r_root from a solved rotor."""

    st = result.stations
    dT = np.array([s.thrust_per_span_N_m for s in result.solutions]) * st.dr_m
    return float((dT * (st.r_m - r_root_m)).sum() / result.operating_point.blade_count)
