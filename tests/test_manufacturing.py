"""Injection-molding geometry modification and manufacturability checks."""

from __future__ import annotations

import numpy as np
import pytest

from prandtl_prop.cad.airfoils import naca4
from prandtl_prop.cad.sections import build_sections
from prandtl_prop.manufacturing.injection import (
    MoldingRules,
    best_pull_angle,
    check_molding,
    hoerner_form_factor,
    modify_airfoil,
    pull_check,
    thickness_schedule,
)


def test_thickness_modification_preserves_camber_and_sets_trailing_edge() -> None:
    base = naca4("NACA4412", 80)
    modified = modify_airfoil(base, 1.3, 0.02)
    np.testing.assert_allclose(0.5 * (modified.upper + modified.lower), 0.5 * (base.upper + base.lower), atol=1e-15)
    assert modified.upper[-1] - modified.lower[-1] == pytest.approx(0.02)
    # Thickness scales by 1.3 plus a linear wedge that is zero at the leading edge.
    middle = 40
    expected = 1.3 * (base.upper[middle] - base.lower[middle]) + 0.02 * base.x[middle]
    assert modified.upper[middle] - modified.lower[middle] == pytest.approx(expected)


def test_thickness_schedule_interpolates_and_clamps() -> None:
    scales = thickness_schedule(np.array([0.1, 0.2, 0.6, 1.0, 1.1]), [(0.2, 1.5), (1.0, 1.1)])
    np.testing.assert_allclose(scales, [1.5, 1.5, 1.3, 1.1, 1.1])


def test_undercut_detected_when_pulling_along_a_cambered_chord() -> None:
    """Pulled along its chord normal, an airfoil releases; pitched 89 deg (chord almost along the pull) the
    cambered outline folds back along the pull direction, so the mold cannot open."""

    cambered = naca4("NACA6412", 60)
    (normal,) = build_sections([0.2], [0.05], [15.0], [cambered], 0.25)
    assert pull_check([normal], 15.0).undercut_sections == []
    (edge_on,) = build_sections([0.2], [0.05], [89.0], [cambered], 0.25)
    assert pull_check([edge_on], 0.0).undercut_sections == [0]


def test_best_pull_angle_follows_section_pitch() -> None:
    airfoil = naca4("NACA0012", 60)
    sections = build_sections([0.2, 0.25], [0.05, 0.05], [20.0, 20.0], [airfoil, airfoil], 0.25)
    best = best_pull_angle(sections)
    assert not best.undercut_sections
    assert abs(best.pull_angle_deg - 20.0) <= 6.0  # symmetric section: best pull is near the chord normal


def test_check_molding_flags_sharp_trailing_edge() -> None:
    airfoil = naca4("NACA4410", 60)
    r = np.linspace(0.06, 0.28, 8)
    sections = build_sections(r, np.linspace(0.05, 0.026, 8), np.linspace(22, 9, 8), [airfoil] * 8)
    sharp = check_molding(sections)
    assert not sharp["checks"]["trailing_edge_thickness"]
    from prandtl_prop.manufacturing.injection import molded_sections

    blunt = check_molding(molded_sections(sections, np.ones(8), 0.0006), MoldingRules())
    assert blunt["checks"]["trailing_edge_thickness"] and blunt["min_trailing_edge_thickness_m"] == pytest.approx(0.0006)
    assert blunt["fill_resistance_index_m^-3"] < sharp["fill_resistance_index_m^-3"]


def test_hoerner_form_factor() -> None:
    assert hoerner_form_factor(0.0) == 1.0
    assert hoerner_form_factor(0.12) == pytest.approx(1.0 + 0.24 + 60 * 0.12**4)


def test_campbell_windows_closed_form() -> None:
    """f0 window between kP and (k+1)P: f(n_hi) >= (1+m) k n_hi and f(n_lo) <= (1-m)(k+1) n_lo, with f^2 = f0^2 + K n^2."""

    from prandtl_prop.manufacturing.optimize import campbell_windows

    n = 4000.0 / 60.0
    narrow = campbell_windows(1.6, 0.9 * n, 1.1 * n, 0.10)
    two_three = narrow[1]
    assert two_three.lower_harmonic == 2 and two_three.feasible
    assert two_three.f0_min_hz == pytest.approx(np.sqrt((1.1 * 2 * 1.1 * n) ** 2 - 1.6 * (1.1 * n) ** 2))
    assert two_three.f0_max_hz == pytest.approx(np.sqrt((0.9 * 3 * 0.9 * n) ** 2 - 1.6 * (0.9 * n) ** 2))
    wide = campbell_windows(1.6, 0.8 * n, 1.2 * n, 0.10)
    assert wide[0].feasible and not wide[1].feasible  # +/-20% rpm: only the soft 1P-2P placement exists


def test_optimiser_returns_a_design_that_satisfies_every_constraint() -> None:
    from prandtl_prop.aero import AnalyticPolar, BladeStations, OperatingPoint, solve_rotor
    from prandtl_prop.manufacturing.materials import preset
    from prandtl_prop.manufacturing.optimize import ThicknessProblem, optimise

    count = 12
    r = np.linspace(0.056, 0.279, count)
    stations = BladeStations(r, np.gradient(r), np.linspace(0.054, 0.026, count), np.linspace(22.0, 9.5, count), ("X",) * count)
    rows = solve_rotor(
        stations, OperatingPoint(0.2794, 2, 4000.0), AnalyticPolar(cl_alpha_per_rad=5.8, alpha0_deg=-2.5, cd0=0.011, k=0.006)
    ).station_rows()
    rows = [{key: str(value) for key, value in row.items()} for row in rows]
    sections = build_sections(r, stations.chord_m, stations.twist_deg, [naca4("NACA4410", 50)] * count)
    problem = ThicknessProblem(
        sections,
        rows,
        blade_count=2,
        rpm=4000.0,
        rho_air=1.225,
        material=preset("peek-cf30"),
        rules=MoldingRules(max_section_thickness_m=0.010),
        trailing_edge_thickness_m=0.0006,
        rpm_band=(0.9, 1.1),
        margin=0.10,
        stress_safety_factor=1.5,
        max_tip_deflection_m=0.011,
        radius_m=0.2794,
    )
    result = optimise(problem, uniform_steps=11)
    assert result.best is not None
    assert np.min(problem.constraints(result.best.control, result.window)) >= 0.0
    assert result.best.molding["all_pass"]
    assert np.all(result.best.scales >= 1.0 - 1e-12)  # default: never thinner than the designed airfoils
