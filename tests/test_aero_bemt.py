"""Verification of the bracketed BEMT solver against closed-form rotor theory and the locked data."""

from __future__ import annotations

import csv
import math

import numpy as np
import pytest

from prandtl_prop import config, geometry
from prandtl_prop.aero import AnalyticPolar, BladeStations, OperatingPoint, TablePolar, solve_rotor, trim_to_thrust
from prandtl_prop.aero.bemt import prandtl_tip_loss, solve_station

OP = OperatingPoint(radius_m=config.DESIGN_RADIUS_M, blade_count=config.DESIGN_BLADE_COUNT, rpm=config.DESIGN_RPM)
THESIS_LIKE_POLAR = AnalyticPolar(cl_alpha_per_rad=5.8, alpha0_deg=-2.5, cd0=0.011, k=0.006)


def _ideal_twist_rotor(theta_tip_deg: float, chord_m: float, count: int = 60) -> BladeStations:
    x_edges = np.linspace(0.3, 1.0, count + 1)
    x = 0.5 * (x_edges[:-1] + x_edges[1:])
    return BladeStations(
        r_m=x * OP.radius_m,
        dr_m=np.diff(x_edges) * OP.radius_m,
        chord_m=np.full(count, chord_m),
        twist_deg=theta_tip_deg / x,
        airfoil=("ANALYTIC",) * count,
    )


def test_ideal_twist_gives_uniform_inflow_matching_small_angle_theory() -> None:
    """With beta = theta_t / x, no drag and no tip loss, inflow is uniform: lambda = sigma a/16 (sqrt(1 + 32 theta_t/(sigma a)) - 1)."""

    chord, theta_tip, lift_slope = 0.02, 2.0, 2.0 * math.pi
    result = solve_rotor(_ideal_twist_rotor(theta_tip, chord), OP, AnalyticPolar(cl_alpha_per_rad=lift_slope), tip_loss=False)
    sigma_a = OP.blade_count * chord / (math.pi * OP.radius_m) * lift_slope
    expected_lambda = sigma_a / 16.0 * (math.sqrt(1.0 + 32.0 * math.radians(theta_tip) / sigma_a) - 1.0)
    inflow = np.array([solution.vi_m_s for solution in result.solutions]) / (OP.omega_rad_s * OP.radius_m)
    assert inflow == pytest.approx(expected_lambda, rel=1.0e-2)
    assert np.ptp(inflow) / inflow.mean() < 1.0e-2


def test_inviscid_power_equals_thrust_times_induced_velocity() -> None:
    """With Cd = 0 and F = 1, shaft power equals sum(dT * vi) exactly, since dQ omega = dL sin(phi) omega r = dT vi."""

    stations = BladeStations.from_rows(geometry.load_case_geometry("MIL"), OP.radius_m)
    result = solve_rotor(stations, OP, AnalyticPolar(cl_alpha_per_rad=5.8), tip_loss=False)
    induced_power = sum(row["dT_N"] * row["Vi_m_s"] for row in result.station_rows())
    assert result.summary["power_W"] == pytest.approx(induced_power, rel=1.0e-10)


@pytest.mark.parametrize("case", ["MIL", "GMT"])
def test_every_locked_geometry_station_converges_including_the_tip(case: str) -> None:
    stations = BladeStations.from_rows(geometry.load_case_geometry(case), OP.radius_m)
    result = trim_to_thrust(stations, OP, THESIS_LIKE_POLAR, config.TARGET_THRUST_N)
    assert result.summary["thrust_N"] == pytest.approx(config.TARGET_THRUST_N, abs=1.0e-8)
    assert result.summary["nonconverged_station_count"] == 0
    assert result.summary["max_abs_residual_N_per_m"] < 1.0e-8
    assert 0.0 < result.solutions[-1].tip_loss < 0.1


def test_station_solution_is_a_true_momentum_balance() -> None:
    solution = solve_station(r_m=0.27, chord_m=0.03, beta_deg=9.0, airfoil="X", op=OP, polar=THESIS_LIKE_POLAR)
    momentum = 4.0 * math.pi * OP.rho_kg_m3 * 0.27 * solution.tip_loss * solution.vi_m_s**2
    assert solution.thrust_per_span_N_m == pytest.approx(momentum, rel=1.0e-9)


def test_section_with_no_lift_is_flagged_not_iterated() -> None:
    solution = solve_station(r_m=0.2, chord_m=0.03, beta_deg=-5.0, airfoil="X", op=OP, polar=THESIS_LIKE_POLAR)
    assert solution.negative_loading and solution.vi_m_s == 0.0


def test_prandtl_tip_loss_limits() -> None:
    assert prandtl_tip_loss(2, 1.0, 0.1) == 0.0
    assert prandtl_tip_loss(2, 0.3, 0.1) == pytest.approx(1.0, abs=1.0e-9)
    values = [prandtl_tip_loss(2, r_R, 0.1) for r_R in np.linspace(0.8, 0.9999, 30)]
    assert all(b < a for a, b in zip(values, values[1:], strict=False))


def test_locked_data_tip_station_violates_momentum_balance() -> None:
    """Documents the defect this solver replaces: locked converged stations balance; the GMT tip station does not."""

    for path, max_tip_imbalance in ((config.MIL_STATION_CSV, 1.0e-3), (config.GMT_STATION_CSV, None)):
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            momentum = (
                4.0
                * math.pi
                * OP.rho_kg_m3
                * float(row["tip_loss_factor"])
                * float(row["r_m"])
                * float(row["Vi_m_s"]) ** 2
                * float(row["dr_m"])
            )
            imbalance = abs(float(row["dT_N"]) - momentum) / float(row["dT_N"])
            if row["induced_velocity_converged"].strip().lower() == "true":
                assert imbalance < 1.0e-5
            elif max_tip_imbalance is None:
                assert imbalance > 0.5
            else:
                assert imbalance < max_tip_imbalance


def test_table_polar_reads_thesis_csv_format_and_interpolates_in_reynolds(tmp_path) -> None:
    for reynolds, offset in ((100000, 0.0), (200000, 0.1)):
        lines = ["alpha,Cl,Cd,Cm,converged"] + [
            f"{alpha},{0.1 * alpha + offset},{0.01 + offset / 10},-0.05,True" for alpha in range(-5, 11)
        ]
        lines.append("11,9.9,9.9,0,False")
        (tmp_path / f"MH115_Re{reynolds}_polar.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    polar = TablePolar.from_directory(tmp_path)
    point = polar.evaluate("mh 115", 2.5, 150000.0)
    assert point.cl == pytest.approx(0.30)
    assert point.cd == pytest.approx(0.015)
    assert not point.alpha_clipped and not point.re_clipped
    assert polar.evaluate("MH115", 10.5, 250000.0).alpha_clipped
    assert polar.evaluate("MH115", 0.0, 50000.0).re_clipped
    with pytest.raises(KeyError):
        polar.evaluate("S4310", 0.0, 1.0e5)
