"""Calibrate BEMT against CFD station loads, and the corrections file the aero stage reads.

At each station BEMT resolves the section force through the inflow angle phi:

    dT  = B q c (Cl cos(phi) - Cd sin(phi)) dr
    dFt = B q c (Cl sin(phi) + Cd cos(phi)) dr,   dQ = r dFt

Given CFD loads (dT, dQ) for the same geometry and operating point, inverting that rotation gives
the effective (Cl, Cd) that reproduce them at BEMT's own inflow. The station factors are
``lift = Cl_eff / Cl_polar`` and ``drag = Cd_eff / Cd_polar``. Changing the loads changes the
induced velocity and hence phi, so the factors are found by a relaxed fixed-point iteration in which
BEMT re-solves the momentum balance with the current factors. At convergence the corrected BEMT
reproduces the CFD loading station by station.

Regularisation: a CFD surface mesh resolves the spanwise loading only as finely as its faces, and
binning faces into narrow BEMT strips is noisy. So by default the factors are constrained to
polynomials of degree ``degree`` in r/R, fitted by load-weighted least squares inside the fixed
point. Two scalar multipliers then make the corrected BEMT reproduce the CFD *total* thrust and
torque exactly, since that is what the outer design loop trims on. ``degree=None`` fits every
station independently.

The factors absorb everything BEMT misses relative to the CFD (3D relief, tip and root effects,
polar error, and mesh error), so they are only as good as the CFD that produced them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from prandtl_prop.aero.bemt import BladeStations, OperatingPoint, solve_rotor
from prandtl_prop.aero.corrections import read_corrections, write_corrections  # noqa: F401 - re-exported
from prandtl_prop.aero.polars import Polar

FACTOR_LIMITS = (0.2, 5.0)
MIN_COEFFICIENT = 0.02  # below this the polar value is too small to scale reliably; keep the factor


@dataclass(frozen=True)
class Calibration:
    lift_factor: np.ndarray
    drag_factor: np.ndarray
    iterations: int
    max_load_mismatch: float  # max over stations of |BEMT - CFD| / max(|CFD|), for dT and dQ
    bemt_dT_N: np.ndarray
    bemt_dQ_Nm: np.ndarray
    total_thrust_error_fraction: float
    total_torque_error_fraction: float


def calibrate(
    stations: BladeStations,
    op: OperatingPoint,
    polar: Polar,
    cfd_dT_N: np.ndarray,
    cfd_dQ_Nm: np.ndarray,
    *,
    collective_offset_deg: float,
    tip_loss: bool = True,
    relaxation: float = 0.7,
    tolerance: float = 1.0e-7,
    max_iterations: int = 200,
    degree: int | None = 3,
    match_totals: bool = True,
) -> Calibration:
    """Find station lift/drag factors so that BEMT at this geometry reproduces the CFD loads."""

    count = len(stations.r_m)
    x = stations.r_m / op.radius_m
    lift, drag = np.ones(count), np.ones(count)
    dT_scale = max(float(np.max(np.abs(cfd_dT_N))), 1.0e-12)
    dQ_scale = max(float(np.max(np.abs(cfd_dQ_Nm))), 1.0e-12)
    mismatch = math.inf
    iterations_done = 0
    for iterations_done in range(1, max_iterations + 1):  # noqa: B007 - the count is reported
        result = solve_rotor(stations, op, polar, collective_offset_deg=collective_offset_deg, tip_loss=tip_loss, corrections=(lift, drag))
        new_lift, new_drag = lift.copy(), drag.copy()
        bemt_dT = np.zeros(count)
        bemt_dQ = np.zeros(count)
        for index, solution in enumerate(result.solutions):
            r, dr, chord = float(stations.r_m[index]), float(stations.dr_m[index]), float(stations.chord_m[index])
            bemt_dT[index] = solution.thrust_per_span_N_m * dr
            bemt_dQ[index] = solution.tangential_per_span_N_m * r * dr
            scale = op.blade_count * 0.5 * op.rho_kg_m3 * solution.w_m_s**2 * chord * dr
            a, b = cfd_dT_N[index] / scale, cfd_dQ_Nm[index] / (r * scale)
            cl_eff = a * math.cos(solution.phi_rad) + b * math.sin(solution.phi_rad)
            cd_eff = -a * math.sin(solution.phi_rad) + b * math.cos(solution.phi_rad)
            cl_polar, cd_polar = solution.cl / lift[index], solution.cd / drag[index]
            if abs(cl_polar) > MIN_COEFFICIENT:
                new_lift[index] = cl_eff / cl_polar
            if abs(cd_polar) > 1.0e-4 and cd_eff > 0.0:
                new_drag[index] = cd_eff / cd_polar
        mismatch = max(float(np.max(np.abs(bemt_dT - cfd_dT_N))) / dT_scale, float(np.max(np.abs(bemt_dQ - cfd_dQ_Nm))) / dQ_scale)
        if degree is not None:
            new_lift = _smooth(x, new_lift, np.abs(cfd_dT_N), degree)
            new_drag = _smooth(x, new_drag, np.abs(cfd_dQ_Nm), degree)
        new_lift = np.clip(new_lift, *FACTOR_LIMITS)
        new_drag = np.clip(new_drag, *FACTOR_LIMITS)
        change = max(float(np.max(np.abs(new_lift - lift))), float(np.max(np.abs(new_drag - drag))))
        lift = lift + relaxation * (new_lift - lift)
        drag = drag + relaxation * (new_drag - drag)
        if change < tolerance:
            break
    if match_totals:
        lift, drag = _match_totals(
            stations, op, polar, lift, drag, float(cfd_dT_N.sum()), float(cfd_dQ_Nm.sum()), collective_offset_deg, tip_loss
        )
    final = solve_rotor(stations, op, polar, collective_offset_deg=collective_offset_deg, tip_loss=tip_loss, corrections=(lift, drag))
    rows = final.station_rows()
    bemt_dT = np.array([row["dT_N"] for row in rows])
    bemt_dQ = np.array([row["dQ_Nm"] for row in rows])
    mismatch = max(float(np.max(np.abs(bemt_dT - cfd_dT_N))) / dT_scale, float(np.max(np.abs(bemt_dQ - cfd_dQ_Nm))) / dQ_scale)
    return Calibration(
        lift,
        drag,
        iterations_done,
        mismatch,
        bemt_dT,
        bemt_dQ,
        float(bemt_dT.sum() / cfd_dT_N.sum() - 1.0),
        float(bemt_dQ.sum() / cfd_dQ_Nm.sum() - 1.0),
    )


def _smooth(x: np.ndarray, values: np.ndarray, weights: np.ndarray, degree: int) -> np.ndarray:
    """Weighted least-squares polynomial in r/R (weights are station load magnitudes)."""

    w = np.sqrt(weights / max(float(weights.max()), 1.0e-300)) + 1.0e-6
    return np.polynomial.Polynomial.fit(x, values, deg=min(degree, len(x) - 1), w=w)(x)


def _match_totals(
    stations: BladeStations,
    op: OperatingPoint,
    polar: Polar,
    lift: np.ndarray,
    drag: np.ndarray,
    thrust_target: float,
    torque_target: float,
    collective_offset_deg: float,
    tip_loss: bool,
    tolerance: float = 1.0e-10,
) -> tuple[np.ndarray, np.ndarray]:
    """Scale all lift and drag factors so total BEMT thrust and torque equal the CFD totals.

    Fixed point: lift scales by the thrust ratio. Drag absorbs the torque error through the
    profile-torque part of Q, sum(B q c Cd cos(phi) r dr), which is the only part it controls directly.
    """

    lift_scale, drag_scale = 1.0, 1.0
    for _ in range(100):
        result = solve_rotor(
            stations,
            op,
            polar,
            collective_offset_deg=collective_offset_deg,
            tip_loss=tip_loss,
            corrections=(lift * lift_scale, drag * drag_scale),
        )
        thrust, torque = float(result.summary["thrust_N"]), float(result.summary["torque_Nm"])
        profile = sum(
            op.blade_count * 0.5 * op.rho_kg_m3 * s.w_m_s**2 * c * s.cd * math.cos(s.phi_rad) * r * dr
            for s, c, r, dr in zip(result.solutions, stations.chord_m, stations.r_m, stations.dr_m, strict=True)
        )
        if abs(thrust / thrust_target - 1.0) < tolerance and abs(torque / torque_target - 1.0) < tolerance:
            break
        lift_scale *= thrust_target / thrust
        drag_scale *= max(0.05, 1.0 + (torque_target - torque) / max(profile, 1.0e-12))
    return lift * lift_scale, drag * drag_scale
