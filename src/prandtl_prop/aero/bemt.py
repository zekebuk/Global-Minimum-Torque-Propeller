"""Blade-element momentum theory with a bracketed per-station solve.

Model (same physics as the archived ``aero_solver.py`` stationwise model, without its fixed-point
iteration):

    Vt = omega r,  Va = V_inf + vi,  phi = atan2(Va, Vt),  alpha = beta - phi,  W^2 = Vt^2 + Va^2
    blade element, all blades, per unit span:  T'_BE = B * 0.5 rho W^2 c (Cl cos(phi) - Cd sin(phi))
    annular momentum, per unit span:           T'_M  = 4 pi rho r F(phi) Va vi
    Prandtl tip loss:                          F = (2/pi) acos(exp(-B (1 - r/R) / (2 (r/R) sin(phi))))

Both thrust expressions scale with the annulus width, so the induced velocity at each station is
the root of ``g(vi) = T'_BE - T'_M`` and the station width only weights the spanwise sums. For a
positively loaded section ``g(0) > 0`` and ``g -> -inf`` as ``vi`` grows, so Brent's method on a
sign-changing bracket always converges. Approaching the tip, ``F -> 0`` drives the root toward the
zero-loading angle, which is the physical limit; no tip-loss floor is applied. Swirl and hub loss
are neglected, as in the thesis model.

Sign convention: thrust along +z, positive torque is the shaft torque required to drive the rotor.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import numpy as np
from scipy.optimize import brentq

from prandtl_prop.aero.polars import Polar

TIP_LOADING_LOWER_MU = 0.75


@dataclass(frozen=True)
class OperatingPoint:
    """Rotor operating condition."""

    radius_m: float
    blade_count: int
    rpm: float
    rho_kg_m3: float = 1.225
    mu_air_kg_m_s: float = 1.81e-5
    v_inf_m_s: float = 0.0

    @property
    def omega_rad_s(self) -> float:
        return self.rpm * 2.0 * math.pi / 60.0

    @property
    def disk_area_m2(self) -> float:
        return math.pi * self.radius_m**2


@dataclass(frozen=True)
class BladeStations:
    """Station-based blade definition. ``dr_m`` are the spanwise quadrature weights."""

    r_m: np.ndarray
    dr_m: np.ndarray
    chord_m: np.ndarray
    twist_deg: np.ndarray
    airfoil: tuple[str, ...]

    def __post_init__(self) -> None:
        lengths = {len(self.r_m), len(self.dr_m), len(self.chord_m), len(self.twist_deg), len(self.airfoil)}
        if len(lengths) != 1:
            raise ValueError("All station arrays must have the same length")
        if np.any(np.diff(self.r_m) <= 0.0):
            raise ValueError("Stations must be strictly increasing in radius")
        if np.any(self.chord_m <= 0.0) or np.any(self.dr_m <= 0.0):
            raise ValueError("Chord and station width must be positive")

    @classmethod
    def from_rows(cls, rows: Sequence[dict[str, str]], radius_m: float) -> BladeStations:
        """Build stations from geometry CSV rows (``mu``/``r_R``, ``dr_m``, ``chord_m``, ``twist_deg``, ``airfoil``)."""

        def number(row: dict[str, str], *names: str) -> float:
            for name in names:
                if row.get(name, "").strip() != "":
                    return float(row[name])
            raise KeyError(f"Geometry row is missing all of {names}")

        r_R = np.array([number(row, "mu", "r_R") for row in rows])
        return cls(
            r_m=r_R * radius_m,
            dr_m=np.array([number(row, "dr_m") for row in rows]),
            chord_m=np.array([number(row, "chord_m") for row in rows]),
            twist_deg=np.array([number(row, "twist_deg", "beta_final_deg") for row in rows]),
            airfoil=tuple(row["airfoil"].strip() for row in rows),
        )


@dataclass(frozen=True)
class StationSolution:
    vi_m_s: float
    phi_rad: float
    alpha_deg: float
    reynolds: float
    w_m_s: float
    tip_loss: float
    cl: float
    cd: float
    cm: float
    thrust_per_span_N_m: float
    tangential_per_span_N_m: float
    residual_N_m: float
    converged: bool
    negative_loading: bool
    alpha_clipped: bool
    re_clipped: bool


@dataclass
class BemtResult:
    """Spanwise solution and integrated performance for one operating point."""

    operating_point: OperatingPoint
    stations: BladeStations
    collective_offset_deg: float
    solutions: list[StationSolution]
    summary: dict[str, float | int | bool] = field(default_factory=dict)

    def station_rows(self) -> list[dict[str, float | str | bool]]:
        """Return station rows with column names compatible with the locked thesis CSVs."""

        op = self.operating_point
        rows: list[dict[str, float | str | bool]] = []
        cumulative_thrust = 0.0
        cumulative_torque = 0.0
        thrust_total = float(self.summary["thrust_N"])
        torque_total = float(self.summary["torque_Nm"])
        for index, solution in enumerate(self.solutions):
            r_m = float(self.stations.r_m[index])
            dr_m = float(self.stations.dr_m[index])
            dT = solution.thrust_per_span_N_m * dr_m
            dQ = solution.tangential_per_span_N_m * r_m * dr_m
            cumulative_thrust += dT
            cumulative_torque += dQ
            rows.append(
                {
                    "r_R": r_m / op.radius_m,
                    "r_m": r_m,
                    "dr_m": dr_m,
                    "chord_m": float(self.stations.chord_m[index]),
                    "twist_deg": float(self.stations.twist_deg[index]) + self.collective_offset_deg,
                    "airfoil": self.stations.airfoil[index],
                    "Vi_m_s": solution.vi_m_s,
                    "tip_loss_factor": solution.tip_loss,
                    "induced_velocity_converged": solution.converged,
                    "residual_N_per_m": solution.residual_N_m,
                    "Vt_m_s": op.omega_rad_s * r_m,
                    "Va_m_s": op.v_inf_m_s + solution.vi_m_s,
                    "Vrel_m_s": solution.w_m_s,
                    "phi_deg": math.degrees(solution.phi_rad),
                    "alpha_deg": solution.alpha_deg,
                    "Re": solution.reynolds,
                    "Cl": solution.cl,
                    "Cd": solution.cd,
                    "Cm": solution.cm,
                    "alpha_outside_polar_range": solution.alpha_clipped,
                    "re_outside_polar_range": solution.re_clipped,
                    "negative_loading": solution.negative_loading,
                    "dT_N": dT,
                    "dQ_Nm": dQ,
                    "qz_N_per_m": solution.thrust_per_span_N_m,
                    "qy_tangential_N_per_m": solution.tangential_per_span_N_m,
                    "torque_loading_Nm_per_m": solution.tangential_per_span_N_m * r_m,
                    "cumulative_thrust_N": cumulative_thrust,
                    "cumulative_torque_Nm": cumulative_torque,
                    "cumulative_thrust_fraction": cumulative_thrust / thrust_total if thrust_total else math.nan,
                    "cumulative_torque_fraction": cumulative_torque / torque_total if torque_total else math.nan,
                }
            )
        return rows


def prandtl_tip_loss(blade_count: int, r_R: float, phi_rad: float) -> float:
    """Prandtl tip-loss factor. Returns 0 exactly at the tip; no floor is applied."""

    if r_R >= 1.0:
        return 0.0
    sin_phi = max(abs(math.sin(phi_rad)), 1.0e-12)
    exponent = 0.5 * blade_count * (1.0 - r_R) / (r_R * sin_phi)
    if exponent > 50.0:
        return 1.0
    return (2.0 / math.pi) * math.acos(math.exp(-exponent))


def solve_station(
    *,
    r_m: float,
    chord_m: float,
    beta_deg: float,
    airfoil: str,
    op: OperatingPoint,
    polar: Polar,
    tip_loss: bool = True,
    lift_factor: float = 1.0,
    drag_factor: float = 1.0,
) -> StationSolution:
    """Solve the momentum/blade-element balance at one station.

    ``lift_factor``/``drag_factor`` scale the polar's Cl/Cd at this station (CFD calibration);
    the induced velocity is re-solved consistently with the corrected loads.
    """

    omega_r = op.omega_rad_s * r_m
    r_R = r_m / op.radius_m

    def evaluate(vi: float) -> tuple[float, StationSolution]:
        va = op.v_inf_m_s + vi
        phi = math.atan2(va, omega_r)
        w2 = omega_r**2 + va**2
        w = math.sqrt(w2)
        alpha = beta_deg - math.degrees(phi)
        reynolds = op.rho_kg_m3 * w * chord_m / op.mu_air_kg_m_s
        point = polar.evaluate(airfoil, alpha, reynolds)
        cl, cd = point.cl * lift_factor, point.cd * drag_factor
        q_c = 0.5 * op.rho_kg_m3 * w2 * chord_m
        thrust_be = op.blade_count * q_c * (cl * math.cos(phi) - cd * math.sin(phi))
        tangential = op.blade_count * q_c * (cl * math.sin(phi) + cd * math.cos(phi))
        factor = prandtl_tip_loss(op.blade_count, r_R, phi) if tip_loss else 1.0
        thrust_momentum = 4.0 * math.pi * op.rho_kg_m3 * r_m * factor * va * vi
        residual = thrust_be - thrust_momentum
        return residual, StationSolution(
            vi_m_s=vi,
            phi_rad=phi,
            alpha_deg=alpha,
            reynolds=reynolds,
            w_m_s=w,
            tip_loss=factor,
            cl=cl,
            cd=cd,
            cm=point.cm,
            thrust_per_span_N_m=thrust_be,
            tangential_per_span_N_m=tangential,
            residual_N_m=residual,
            converged=True,
            negative_loading=False,
            alpha_clipped=point.alpha_clipped,
            re_clipped=point.re_clipped,
        )

    residual_at_zero, solution_at_zero = evaluate(0.0)
    if residual_at_zero <= 0.0:
        # No positive thrust even with zero induced velocity. In axial flight (V > 0) momentum
        # theory still holds for mildly negative loading (windmill-brake branch: vi < 0 while the
        # slipstream keeps moving, V + 2 vi > 0). Search that branch; otherwise report the
        # zero-inflow loads and flag the station.
        lowest = -0.49 * op.v_inf_m_s
        if op.v_inf_m_s > 0.0 and evaluate(lowest)[0] > 0.0:
            vi = brentq(lambda value: evaluate(value)[0], lowest, 0.0, xtol=1.0e-12, rtol=1.0e-12, maxiter=200)
            residual, solution = evaluate(vi)
            scale = max(abs(solution.thrust_per_span_N_m), 1.0e-9)
            return replace(solution, negative_loading=True, converged=abs(residual) <= 1.0e-8 * scale + 1.0e-10)
        return replace(solution_at_zero, negative_loading=True)

    upper = max(omega_r * math.tan(math.radians(min(max(beta_deg, 1.0), 80.0))), 1.0e-3)
    while evaluate(upper)[0] > 0.0:
        upper *= 2.0
        if upper > 50.0 * max(omega_r, 1.0):
            return replace(evaluate(upper)[1], converged=False)

    vi = brentq(lambda value: evaluate(value)[0], 0.0, upper, xtol=1.0e-12, rtol=1.0e-12, maxiter=200)
    residual, solution = evaluate(vi)
    scale = max(abs(solution.thrust_per_span_N_m), 1.0e-9)
    return replace(solution, converged=abs(residual) <= 1.0e-8 * scale + 1.0e-10)


def solve_rotor(
    stations: BladeStations,
    op: OperatingPoint,
    polar: Polar,
    *,
    collective_offset_deg: float = 0.0,
    tip_loss: bool = True,
    corrections: tuple[np.ndarray, np.ndarray] | None = None,
) -> BemtResult:
    """Solve every station at a fixed collective offset and integrate performance.

    ``corrections`` is an optional (lift_factors, drag_factors) pair, one value per station.
    """

    lift, drag = corrections if corrections is not None else (np.ones(len(stations.r_m)), np.ones(len(stations.r_m)))
    solutions = [
        solve_station(
            r_m=float(stations.r_m[index]),
            chord_m=float(stations.chord_m[index]),
            beta_deg=float(stations.twist_deg[index]) + collective_offset_deg,
            airfoil=stations.airfoil[index],
            op=op,
            polar=polar,
            tip_loss=tip_loss,
            lift_factor=float(lift[index]),
            drag_factor=float(drag[index]),
        )
        for index in range(len(stations.r_m))
    ]
    result = BemtResult(op, stations, collective_offset_deg, solutions)
    result.summary = _summarize(result)
    return result


def trim_to_thrust(
    stations: BladeStations,
    op: OperatingPoint,
    polar: Polar,
    target_thrust_N: float,
    *,
    tip_loss: bool = True,
    corrections: tuple[np.ndarray, np.ndarray] | None = None,
    bracket_deg: tuple[float, float] = (-10.0, 10.0),
    thrust_tolerance_N: float = 1.0e-9,
) -> BemtResult:
    """Find the collective offset that produces ``target_thrust_N`` and return that solution."""

    def thrust_error(offset: float) -> float:
        return (
            float(
                solve_rotor(stations, op, polar, collective_offset_deg=offset, tip_loss=tip_loss, corrections=corrections).summary[
                    "thrust_N"
                ]
            )
            - target_thrust_N
        )

    low, high = bracket_deg
    for _ in range(4):
        if thrust_error(low) < 0.0 < thrust_error(high):
            break
        low, high = low - 10.0, high + 10.0
    else:
        raise ValueError(f"Could not bracket target thrust {target_thrust_N} N with collective offsets in [{low}, {high}] deg")

    offset = brentq(thrust_error, low, high, xtol=1.0e-12, maxiter=200)
    result = solve_rotor(stations, op, polar, collective_offset_deg=offset, tip_loss=tip_loss, corrections=corrections)
    error = float(result.summary["thrust_N"]) - target_thrust_N
    if abs(error) > max(thrust_tolerance_N, 1.0e-9 * target_thrust_N):
        raise ValueError(f"Trim did not reach target thrust: error {error:.3e} N")
    result.summary["target_thrust_N"] = target_thrust_N
    return result


def trim_rpm_to_thrust(
    stations: BladeStations,
    op: OperatingPoint,
    polar: Polar,
    target_thrust_N: float,
    *,
    collective_offset_deg: float = 0.0,
    tip_loss: bool = True,
    corrections: tuple[np.ndarray, np.ndarray] | None = None,
    rpm_bracket: tuple[float, float] = (500.0, 12000.0),
) -> BemtResult:
    """Fixed-pitch off-design: the rpm that gives ``target_thrust_N`` at the geometry's collective.

    A fixed-pitch prop is designed (collective set) at one point; elsewhere the controller only
    changes rpm. Thrust rises monotonically with rpm at fixed V over the useful range, so the root
    is bracketed by scanning up from the lower bound.
    """

    def thrust_error(rpm: float) -> float:
        trial = replace(op, rpm=rpm)
        return (
            float(
                solve_rotor(
                    stations, trial, polar, collective_offset_deg=collective_offset_deg, tip_loss=tip_loss, corrections=corrections
                ).summary["thrust_N"]
            )
            - target_thrust_N
        )

    grid = np.geomspace(rpm_bracket[0], rpm_bracket[1], 24)
    errors = [thrust_error(rpm) for rpm in grid]
    for low, high, e_low, e_high in zip(grid[:-1], grid[1:], errors[:-1], errors[1:], strict=True):
        if e_low < 0.0 <= e_high:
            rpm = brentq(thrust_error, low, high, xtol=1.0e-9, maxiter=200)
            result = solve_rotor(
                stations,
                replace(op, rpm=rpm),
                polar,
                collective_offset_deg=collective_offset_deg,
                tip_loss=tip_loss,
                corrections=corrections,
            )
            result.summary["target_thrust_N"] = target_thrust_N
            return result
    raise ValueError(f"No rpm in {rpm_bracket} gives {target_thrust_N} N at V = {op.v_inf_m_s} m/s")


def _summarize(result: BemtResult) -> dict[str, float | int | bool]:
    op = result.operating_point
    stations = result.stations
    dT = np.array([solution.thrust_per_span_N_m for solution in result.solutions]) * stations.dr_m
    dQ = np.array([solution.tangential_per_span_N_m for solution in result.solutions]) * stations.r_m * stations.dr_m
    thrust, torque = float(dT.sum()), float(dQ.sum())
    power = torque * op.omega_rad_s
    n = op.rpm / 60.0
    diameter = 2.0 * op.radius_m
    r_R = stations.r_m / op.radius_m
    ideal_power = thrust**1.5 / math.sqrt(2.0 * op.rho_kg_m3 * op.disk_area_m2) if thrust > 0.0 else math.nan
    return {
        "thrust_N": thrust,
        "torque_Nm": torque,
        "power_W": power,
        "CT": thrust / (op.rho_kg_m3 * n**2 * diameter**4),
        "CQ": torque / (op.rho_kg_m3 * n**2 * diameter**5),
        "CP": power / (op.rho_kg_m3 * n**3 * diameter**5),
        "figure_of_merit": ideal_power / power if power > 0.0 else math.nan,
        "v_inf_m_s": op.v_inf_m_s,
        "rpm": op.rpm,
        "advance_ratio_J": op.v_inf_m_s / (n * diameter) if n > 0 else math.nan,
        "propulsive_efficiency": thrust * op.v_inf_m_s / power if power > 0.0 else math.nan,
        "collective_offset_deg": result.collective_offset_deg,
        "center_of_thrust_mu": float((r_R * dT).sum() / thrust) if thrust else math.nan,
        "tip_loading_fraction": float(dT[r_R >= TIP_LOADING_LOWER_MU].sum() / thrust) if thrust else math.nan,
        "station_count": len(result.solutions),
        "nonconverged_station_count": sum(not solution.converged for solution in result.solutions),
        "negative_loading_station_count": sum(solution.negative_loading for solution in result.solutions),
        "alpha_clipped_station_count": sum(solution.alpha_clipped for solution in result.solutions),
        "re_clipped_station_count": sum(solution.re_clipped for solution in result.solutions),
        "max_abs_residual_N_per_m": max(abs(solution.residual_N_m) for solution in result.solutions),
    }
