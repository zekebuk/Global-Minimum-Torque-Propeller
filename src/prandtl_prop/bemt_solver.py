"""Vectorized hover BEMT solver used as the truth model for surrogate and PIML studies.

The physics matches the locked thesis solver (`archive/legacy_scripts/aero_solver.py`):
stationwise annular momentum with Prandtl tip loss, static hover, no swirl.

    blade element:  dT/dr = B * 0.5 * rho * W^2 * c * (Cl cos(phi) - Cd sin(phi))
                    dQ/dr = B * 0.5 * rho * W^2 * c * (Cl sin(phi) + Cd cos(phi)) * r
    momentum:       dT/dr = 4 * pi * rho * F * r * vi^2
    kinematics:     phi = atan(vi / (Omega r)),  alpha = beta - phi

The XFOIL polar CSVs are not part of this repository, so the airfoil polars are replaced by a
smooth analytic model fitted to the Cl/Cd/alpha/Re values recorded in the locked station CSVs.
The fit is only trusted inside the recorded alpha and Reynolds ranges; outside them the model
adds an assumed soft stall so that solvers and gradients stay well behaved.

All physics functions take an array namespace `xp` (numpy or torch) so the PINN can reuse the
exact same residual that the bisection solver drives to zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from prandtl_prop import bemt, config, geometry

TIP_LOSS_FACTOR_FLOOR = 0.05
# Stations outboard of this are dominated by the tip-loss floor (F -> 0.05, vi blows up); they are
# kept in the load integrals but excluded from the angle-of-attack validity statistics.
ALPHA_STATS_MAX_MU = 0.99
RE_REF = 2.0e5
KINEMATIC_VISCOSITY_M2_S = 1.46e-5


@dataclass(frozen=True)
class PolarModel:
    """Analytic polar fitted to the locked XFOIL station values.

    Cl = cl0[airfoil] + cl_alpha * alpha + cl_re * ln(Re / Re_ref), softly capped at cl_max.
    Cd = c0 + c1 a + c2 a^2 + c3 l + c4 l^2 + c5 a l (+ assumed post-stall rise), a in deg, l = ln(Re/Re_ref).
    """

    airfoils: tuple[str, ...]
    cl0: tuple[float, ...]
    cl_alpha_per_deg: float
    cl_re: float
    cd_coef: tuple[float, float, float, float, float, float]
    alpha_fit_min_deg: float
    alpha_fit_max_deg: float
    re_fit_min: float
    re_fit_max: float
    cl_rms_error: float
    cd_rms_error: float
    cl_max: float = 1.15
    stall_sharpness: float = 20.0
    post_stall_cd_per_deg2: float = 0.01

    def airfoil_index(self, labels: list[str]) -> np.ndarray:
        lookup = {name: index for index, name in enumerate(self.airfoils)}
        return np.array([lookup[label.strip().upper()] for label in labels], dtype=int)

    def cl(self, xp: Any, alpha_deg: Any, log_re: Any, cl0: Any) -> Any:
        linear = cl0 + self.cl_alpha_per_deg * alpha_deg + self.cl_re * log_re
        return _smooth_min(xp, linear, self.cl_max + 0.0 * linear, self.stall_sharpness)

    def cd(self, xp: Any, alpha_deg: Any, log_re: Any) -> Any:
        c0, c1, c2, c3, c4, c5 = self.cd_coef
        base = c0 + c1 * alpha_deg + c2 * alpha_deg**2 + c3 * log_re + c4 * log_re**2 + c5 * alpha_deg * log_re
        overshoot = _softplus(xp, alpha_deg - self.alpha_fit_max_deg - 1.0)
        return base + self.post_stall_cd_per_deg2 * overshoot**2

    def log_re(self, xp: Any, w_m_s: Any, chord_m: Any) -> Any:
        reynolds = w_m_s * chord_m / KINEMATIC_VISCOSITY_M2_S
        reynolds = xp.clip(reynolds, 0.7 * self.re_fit_min, 1.3 * self.re_fit_max)
        return xp.log(reynolds / RE_REF)


def _smooth_min(xp: Any, a: Any, b: Any, k: float) -> Any:
    return -xp.logaddexp(-k * a, -k * b) / k


def _softplus(xp: Any, x: Any) -> Any:
    return xp.logaddexp(x, 0.0 * x)


def fit_polar_from_locked_data() -> PolarModel:
    """Least-squares fit of the analytic polar to the locked MIL and GMT station data."""

    rows = bemt.load_station_data("MIL") + bemt.load_station_data("GMT")
    alpha = np.array([float(row["alpha_deg"]) for row in rows])
    reynolds = np.array([float(row["Re"]) for row in rows])
    cl = np.array([float(row["Cl"]) for row in rows])
    cd = np.array([float(row["Cd"]) for row in rows])
    labels = [row["airfoil"].strip().upper() for row in rows]
    airfoils = tuple(sorted(set(labels)))
    log_re = np.log(reynolds / RE_REF)

    one_hot = np.column_stack([[label == name for label in labels] for name in airfoils]).astype(float)
    cl_matrix = np.column_stack([one_hot, alpha, log_re])
    cl_coef, *_ = np.linalg.lstsq(cl_matrix, cl, rcond=None)
    cd_matrix = np.column_stack([np.ones_like(alpha), alpha, alpha**2, log_re, log_re**2, alpha * log_re])
    cd_coef, *_ = np.linalg.lstsq(cd_matrix, cd, rcond=None)

    n_airfoils = len(airfoils)
    return PolarModel(
        airfoils=airfoils,
        cl0=tuple(float(value) for value in cl_coef[:n_airfoils]),
        cl_alpha_per_deg=float(cl_coef[n_airfoils]),
        cl_re=float(cl_coef[n_airfoils + 1]),
        cd_coef=tuple(float(value) for value in cd_coef),
        alpha_fit_min_deg=float(alpha.min()),
        alpha_fit_max_deg=float(alpha.max()),
        re_fit_min=float(reynolds.min()),
        re_fit_max=float(reynolds.max()),
        cl_rms_error=float(np.sqrt(np.mean((cl_matrix @ cl_coef - cl) ** 2))),
        cd_rms_error=float(np.sqrt(np.mean((cd_matrix @ cd_coef - cd) ** 2))),
    )


@dataclass(frozen=True)
class RotorStations:
    """Fixed radial discretization shared by MIL and GMT (stations, widths, chord, airfoils)."""

    mu: np.ndarray
    r_m: np.ndarray
    dr_m: np.ndarray
    chord_m: np.ndarray
    cl0: np.ndarray
    airfoils: tuple[str, ...]
    radius_m: float = config.DESIGN_RADIUS_M
    blade_count: int = config.DESIGN_BLADE_COUNT
    rho: float = config.AIR_DENSITY_KG_M3


def load_rotor_stations(polar: PolarModel) -> RotorStations:
    """Use the locked station grid (r, dr, chord, airfoil) from the MIL BEMT CSV."""

    rows = bemt.load_station_data("MIL")
    labels = [row["airfoil"].strip().upper() for row in rows]
    return RotorStations(
        mu=np.array(bemt.column_values(rows, "r_R")),
        r_m=np.array(bemt.column_values(rows, "r_m")),
        dr_m=np.array(bemt.column_values(rows, "dr_m")),
        chord_m=np.array(bemt.column_values(rows, "chord_m")),
        cl0=np.array(polar.cl0)[polar.airfoil_index(labels)],
        airfoils=tuple(labels),
    )


def locked_twist_deg(case_label: str) -> np.ndarray:
    """Final locked twist distribution for `MIL` or `GMT`."""

    return np.array(geometry.twist_values_deg(geometry.load_case_geometry(case_label)))


def prandtl_tip_loss(xp: Any, mu: Any, phi_rad: Any, blade_count: int) -> Any:
    sin_phi = xp.clip(xp.sin(phi_rad), 1.0e-6, 1.0)
    argument = 0.5 * blade_count * (1.0 - mu) / (mu * sin_phi)
    factor = (2.0 / math.pi) * xp.arccos(xp.clip(xp.exp(-argument), 0.0, 1.0 - 1.0e-12))
    return xp.clip(factor, TIP_LOSS_FACTOR_FLOOR, 1.0)


def station_physics(
    xp: Any,
    vi: Any,
    omega: Any,
    beta_deg: Any,
    chord_m: Any,
    r_m: Any,
    mu: Any,
    cl0: Any,
    polar: PolarModel,
    blade_count: int = config.DESIGN_BLADE_COUNT,
    rho: float = config.AIR_DENSITY_KG_M3,
) -> dict[str, Any]:
    """Blade-element and momentum loads per unit span at given induced velocity."""

    vt = omega * r_m
    phi = xp.arctan2(vi, vt)
    w_sq = vi**2 + vt**2
    alpha = beta_deg - phi * (180.0 / math.pi)
    log_re = polar.log_re(xp, xp.sqrt(w_sq), chord_m)
    cl = polar.cl(xp, alpha, log_re, cl0)
    cd = polar.cd(xp, alpha, log_re)
    q_c = blade_count * 0.5 * rho * w_sq * chord_m
    tip_loss = prandtl_tip_loss(xp, mu, phi, blade_count)
    return {
        "phi_rad": phi,
        "alpha_deg": alpha,
        "Cl": cl,
        "Cd": cd,
        "F": tip_loss,
        "dT_dr_blade": q_c * (cl * xp.cos(phi) - cd * xp.sin(phi)),
        "dQ_dr": q_c * (cl * xp.sin(phi) + cd * xp.cos(phi)) * r_m,
        "dT_dr_momentum": 4.0 * math.pi * rho * tip_loss * r_m * vi**2,
    }


def normalized_residual(xp: Any, physics: dict[str, Any], omega: Any, chord_m: Any, r_m: Any, blade_count: int) -> Any:
    """(momentum - blade element) thrust per span, scaled by B * c * (Omega r)^2 / 2 (rho cancels)."""

    scale = blade_count * 0.5 * config.AIR_DENSITY_KG_M3 * chord_m * (omega * r_m) ** 2
    return (physics["dT_dr_momentum"] - physics["dT_dr_blade"]) / scale


def solve_inflow(
    rotor: RotorStations,
    polar: PolarModel,
    twist_deg: np.ndarray,
    chord_m: np.ndarray,
    rpm: np.ndarray,
    vi_max: float = 80.0,
    iterations: int = 60,
) -> np.ndarray:
    """Bisection on the momentum/blade-element balance for a batch of designs, shape (N, S)."""

    omega = (np.asarray(rpm, dtype=float) * 2.0 * math.pi / 60.0)[:, None]

    def residual(vi: np.ndarray) -> np.ndarray:
        phys = station_physics(
            np, vi, omega, twist_deg, chord_m, rotor.r_m, rotor.mu, rotor.cl0, polar, rotor.blade_count, rotor.rho
        )
        return phys["dT_dr_momentum"] - phys["dT_dr_blade"]

    shape = np.broadcast_shapes(np.shape(twist_deg), np.shape(chord_m), (omega.shape[0], rotor.mu.size))
    low = np.zeros(shape)
    high = np.full(shape, vi_max)
    no_positive_thrust = residual(low) >= 0.0
    for _ in range(iterations):
        mid = 0.5 * (low + high)
        above = residual(mid) > 0.0
        high = np.where(above, mid, high)
        low = np.where(above, low, mid)
    return np.where(no_positive_thrust, 0.0, 0.5 * (low + high))


def evaluate_rotor(
    rotor: RotorStations,
    polar: PolarModel,
    twist_deg: np.ndarray,
    chord_m: np.ndarray,
    rpm: np.ndarray,
    vi: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Integrated performance for a batch of designs; `vi` defaults to the converged BEMT inflow."""

    twist_deg = np.atleast_2d(twist_deg)
    chord_m = np.atleast_2d(chord_m)
    rpm = np.atleast_1d(np.asarray(rpm, dtype=float))
    if vi is None:
        vi = solve_inflow(rotor, polar, twist_deg, chord_m, rpm)
    omega = rpm * 2.0 * math.pi / 60.0
    phys = station_physics(
        np, vi, omega[:, None], twist_deg, chord_m, rotor.r_m, rotor.mu, rotor.cl0, polar, rotor.blade_count, rotor.rho
    )
    d_thrust = phys["dT_dr_blade"] * rotor.dr_m
    d_torque = phys["dQ_dr"] * rotor.dr_m
    thrust = d_thrust.sum(axis=1)
    torque = d_torque.sum(axis=1)
    power = torque * omega
    disk_area = math.pi * rotor.radius_m**2
    ideal_power = np.clip(thrust, 0.0, None) ** 1.5 / math.sqrt(2.0 * rotor.rho * disk_area)
    root_moment = (d_thrust * (rotor.r_m - rotor.r_m[0])).sum(axis=1) / rotor.blade_count
    return {
        "thrust_N": thrust,
        "torque_Nm": torque,
        "power_W": power,
        "figure_of_merit": ideal_power / power,
        "root_moment_Nm": root_moment,
        "center_of_thrust_mu": (d_thrust * rotor.mu).sum(axis=1) / thrust,
        "min_alpha_deg": phys["alpha_deg"][:, rotor.mu <= ALPHA_STATS_MAX_MU].min(axis=1),
        "max_alpha_deg": phys["alpha_deg"][:, rotor.mu <= ALPHA_STATS_MAX_MU].max(axis=1),
        "vi_m_s": vi,
        "dT_N": d_thrust,
        "dQ_Nm": d_torque,
        "alpha_deg": phys["alpha_deg"],
        "Cl": phys["Cl"],
        "Cd": phys["Cd"],
    }


def trim_collective_to_thrust(
    rotor: RotorStations,
    polar: PolarModel,
    twist_deg: np.ndarray,
    chord_m: np.ndarray,
    rpm: float,
    target_thrust_n: float = config.TARGET_THRUST_N,
    span_deg: float = 6.0,
) -> float:
    """Collective twist offset [deg] that makes a single design hit the target thrust (bisection)."""

    low, high = -span_deg, span_deg
    for _ in range(60):
        mid = 0.5 * (low + high)
        thrust = evaluate_rotor(rotor, polar, twist_deg + mid, chord_m, np.array([rpm]))["thrust_N"][0]
        low, high = (mid, high) if thrust < target_thrust_n else (low, mid)
    return 0.5 * (low + high)
