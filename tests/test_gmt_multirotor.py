"""Global-minimum-torque design (separable BEMT Lagrangian) and the multirotor model."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from prandtl_prop import multirotor
from prandtl_prop.aero import gmt
from prandtl_prop.aero.bemt import OperatingPoint, solve_rotor, solve_station, trim_to_thrust
from prandtl_prop.design import load_design
from prandtl_prop.gmt_design import planform
from prandtl_prop.pipeline import _polar


@pytest.fixture(scope="module")
def case():
    design = load_design("designs/fpv10_tri_spec.yaml")
    stations = planform(design, 0.13)
    op = OperatingPoint(design.operating_point.radius_m, 3, 6500.0)
    polar = _polar(design)
    solution = gmt.design(stations, op, polar, design.multirotor.hover_thrust_N, cl_limit=design.gmt.cl_limit)
    return design, stations, op, polar, solution


def test_vectorised_station_tables_match_the_scalar_solver(case) -> None:
    _, stations, op, polar, _ = case
    betas = np.array([2.0, 12.0, 24.0])
    tables = gmt.station_tables(stations, op, polar, betas)
    for i in (0, 17, len(stations.r_m) - 1):
        for k, beta in enumerate(betas):
            ref = solve_station(r_m=stations.r_m[i], chord_m=stations.chord_m[i], beta_deg=beta, airfoil="x", op=op, polar=polar)
            assert tables.thrust_per_span_N_m[i, k] == pytest.approx(ref.thrust_per_span_N_m, rel=1e-9, abs=1e-9)
            assert tables.torque_per_span_N[i, k] == pytest.approx(ref.tangential_per_span_N_m * stations.r_m[i], rel=1e-9, abs=1e-12)


def test_gmt_is_the_minimum_torque_twist(case) -> None:
    design, stations, op, polar, solution = case
    thrust = design.multirotor.hover_thrust_N
    best = gmt.evaluate(gmt.with_twist(stations, solution.twist_deg), op, polar, thrust)
    q_gmt = best.summary["torque_Nm"]
    assert best.summary["thrust_N"] == pytest.approx(thrust, rel=1e-6)
    out = gmt.compare(stations, op, polar, thrust, cl_limit=design.gmt.cl_limit)
    assert q_gmt <= out["mil"][1].summary["torque_Nm"] * (1 + 1e-5)
    assert q_gmt < out["helix"][1].summary["torque_Nm"]
    # No smooth twist perturbation (re-trimmed to the same thrust) does better.
    rng = np.random.default_rng(3)
    x = stations.r_m / op.radius_m
    for _ in range(12):
        bump = rng.normal(0.0, 0.6) * np.sin(math.pi * rng.uniform(1, 4) * x) + rng.normal(0.0, 0.4) * (x - 0.6)
        trial = trim_to_thrust(gmt.with_twist(stations, solution.twist_deg + bump), op, polar, thrust, bracket_deg=(-6, 6))
        cl_ok = max(s.cl for s in trial.solutions) <= design.gmt.cl_limit
        if cl_ok:
            assert trial.summary["torque_Nm"] >= q_gmt * (1 - 2e-5)


def test_equal_marginal_torque_per_thrust_across_the_span(case) -> None:
    """Optimality: dQ'/dT' = lambda at every station that is not at the Cl limit."""

    design, stations, op, polar, solution = case
    for i in range(2, len(stations.r_m) - 4, 5):
        if solution.cl[i] > design.gmt.cl_limit - 0.02:
            continue
        b = solution.twist_deg[i]
        loads = [
            solve_station(r_m=stations.r_m[i], chord_m=stations.chord_m[i], beta_deg=b + d, airfoil="x", op=op, polar=polar)
            for d in (-0.05, 0.05)
        ]
        dT = loads[1].thrust_per_span_N_m - loads[0].thrust_per_span_N_m
        dQ = (loads[1].tangential_per_span_N_m - loads[0].tangential_per_span_N_m) * stations.r_m[i]
        assert dQ / dT == pytest.approx(solution.lambda_m, rel=0.02)


def test_bell_family_trades_torque_for_root_moment(case) -> None:
    design, stations, op, polar, _ = case
    rows = []
    for mu in (0.0, 0.1, 0.4):
        s = gmt.design(stations, op, polar, design.multirotor.hover_thrust_N, cl_limit=design.gmt.cl_limit, bending_weight=mu)
        rows.append((s.torque_Nm, s.root_moment_Nm))
    assert rows[0][0] < rows[1][0] < rows[2][0]
    assert rows[0][1] > rows[1][1] > rows[2][1]


def test_forward_flight_model_reduces_to_axial_bemt(case) -> None:
    _, stations, op, polar, solution = case
    twisted = gmt.with_twist(stations, solution.twist_deg)
    for v in (0.0, 6.0):
        axial = solve_rotor(twisted, replace(op, v_inf_m_s=v), polar).summary
        model = multirotor.rotor_forward(twisted, op, polar, v_edge_m_s=0.0, v_axial_m_s=v)
        assert model["thrust_N"] == pytest.approx(axial["thrust_N"], rel=1e-9)
        assert model["torque_Nm"] == pytest.approx(axial["torque_Nm"], rel=1e-9)
        assert abs(model["h_force_N"]) < 1e-9
    edge = multirotor.rotor_forward(twisted, op, polar, v_edge_m_s=15.0, v_axial_m_s=0.0)
    assert edge["h_force_N"] > 0.0  # in-plane drag opposes the edgewise motion
    hover = multirotor.rotor_forward(twisted, op, polar, v_edge_m_s=0.0, v_axial_m_s=0.0)
    assert edge["mean_induced_m_s"] < hover["mean_induced_m_s"]  # Glauert: edgewise flow relieves the induced velocity


def test_motor_match_and_level_flight_trim(case) -> None:
    design, stations, op, polar, solution = case
    spec = design.multirotor
    torque, omega = 0.2, 1400.0
    kv = multirotor.matched_kv(spec, torque, omega)
    point = multirotor.motor_point(spec, kv, torque, omega)
    assert point["voltage_V"] == pytest.approx(spec.battery_voltage_V, rel=1e-9)  # full throttle reaches omega exactly
    trim = multirotor.trim_level_flight(gmt.with_twist(stations, solution.twist_deg), op, polar, spec, 12.0)
    th = math.radians(trim["pitch_deg"])
    n = spec.rotor_count
    assert n * (trim["thrust_N"] * math.cos(th) + trim["h_force_N"] * math.sin(th)) == pytest.approx(spec.weight_N, rel=2e-3)
    assert n * (trim["thrust_N"] * math.sin(th) - trim["h_force_N"] * math.cos(th)) == pytest.approx(trim["body_drag_N"], rel=2e-3)


def test_blade_stress_matches_closed_forms(case) -> None:
    """Tension alone: sum of rho A omega^2 r dr outboard over A; bending alone: outboard thrust moment over Z."""

    from prandtl_prop.gmt_design import SECTION_AREA, SECTION_MODULUS, blade_stress, thickness_to_chord

    design, stations, op, _, _ = case
    r, dr, c = stations.r_m, stations.dr_m, stations.chord_m
    rho, t = design.structure.material.density_kg_m3, thickness_to_chord(design) * c
    area, modulus = SECTION_AREA * c * t, SECTION_MODULUS * c * t**2
    omega = 1500.0
    tension = blade_stress(design, stations, np.zeros_like(r), omega, 1.0)
    i = 5
    expected = (rho * area[i:] * omega**2 * r[i:] * dr[i:]).sum() / area[i]
    assert tension[i] == pytest.approx(expected, rel=1e-12)
    load = np.full_like(r, 30.0)  # N/m, all blades
    bending = blade_stress(design, stations, load, 0.0, 1.0)
    per_blade = load * dr / op.blade_count
    assert bending[i] == pytest.approx((per_blade[i + 1 :] * (r[i + 1 :] - r[i])).sum() / modulus[i], rel=1e-12)
    thicker = blade_stress(design, stations, load, 0.0, 1.5)
    assert thicker[0] == pytest.approx(bending[0] / 1.5**2, rel=1e-9)  # Z grows with t^2 at the root
