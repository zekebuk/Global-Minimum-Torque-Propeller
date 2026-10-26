"""XFOIL section polars (Viterna extension, molded geometry, run if available) and the free-vortex-wake solver."""

from __future__ import annotations

import math

import numpy as np
import pytest

from prandtl_prop.aero import fvw, xfoil
from prandtl_prop.aero.bemt import BladeStations, OperatingPoint, solve_rotor
from prandtl_prop.aero.polars import AnalyticPolar
from prandtl_prop.cad.airfoils import naca4
from prandtl_prop.manufacturing.injection import modify_airfoil


def test_viterna_extends_to_180_and_is_continuous():
    alpha = np.arange(-6.0, 14.5, 0.5)
    cl = 0.4 + 0.1 * alpha
    cd = 0.01 + 0.0008 * alpha**2
    a, c_l, c_d = xfoil.viterna(alpha, cl, cd)
    assert a[0] == pytest.approx(-180.0) and a[-1] == pytest.approx(180.0)
    assert np.all(np.diff(a) > 0)
    assert np.max(np.abs(np.diff(c_l))) < 0.25  # no jumps at the stitching points
    assert c_d[np.argmin(np.abs(a - 90.0))] == pytest.approx(1.11 + 0.018 * 10, rel=1e-6)  # flat plate at 90 deg
    assert np.all(c_d > 0)


def test_selig_dat_round_trip(tmp_path):
    foil = modify_airfoil(naca4("NACA4410", 60), 1.2, 0.02)
    path = xfoil.write_dat(foil, tmp_path / "f.dat")
    pts = np.loadtxt(path, skiprows=1)
    assert pts[0, 0] == pytest.approx(1.0) and pts[-1, 0] == pytest.approx(1.0)
    assert pts[0, 1] - pts[-1, 1] == pytest.approx(0.02 * 1.0 + 0.0, abs=5e-3)  # blunt trailing edge kept
    assert np.argmin(pts[:, 0]) == len(foil.x) - 1


@pytest.mark.skipif(xfoil.find_xfoil() is None, reason="needs an XFOIL build without FP traps")
def test_xfoil_low_re_polar_is_physical():
    foil = modify_airfoil(naca4("NACA4410", 120), 1.0, 0.01)
    rows = xfoil.run_xfoil(foil, 1.0e5, xfoil.PolarSettings(alpha_min_deg=-2.0, alpha_max_deg=6.0, alpha_step_deg=1.0))
    assert len(rows) >= 6
    by = {round(r["alpha"]): r for r in rows}
    assert 0.35 < by[0]["cl"] < 0.6  # 4 % camber: zero-lift angle near -4 deg
    assert 0.008 < by[2]["cd"] < 0.03
    assert by[4]["cl"] > by[0]["cl"]


def _blade() -> tuple[BladeStations, OperatingPoint]:
    radius = 0.127
    edges = np.linspace(0.2, 1.0, 11) * radius
    r = 0.5 * (edges[1:] + edges[:-1])
    twist = np.degrees(np.arctan(0.11 / (2 * math.pi * r)))
    return BladeStations(r, np.diff(edges), np.full(len(r), 0.016), twist, ("NACA4410",) * len(r)), OperatingPoint(radius, 3, 7500.0)


def test_free_wake_hover_matches_bemt():
    """Hover: the free wake and momentum BEMT agree to within ~15 % in thrust and ~8 % in torque.

    The difference is systematic, not resolution: this blade converges to T -12 %, Q -4 % vs BEMT at
    8 panels, 24 steps/rev, an 8-revolution wake (the 10-inch design: -9 %, -5 %). The free wake's
    non-uniform inflow, with the strongest downwash inboard of the previous blade's tip vortex, is
    what BEMT's annulus momentum balance plus Prandtl tip loss approximates."""

    stations, op = _blade()
    polar = AnalyticPolar(4.22, -5.19, 0.0178, 0.029, 1.2, -1.0)
    bemt = solve_rotor(stations, op, polar).summary
    result = fvw.solve(
        stations,
        op.radius_m,
        op.blade_count,
        op.rpm,
        polar,
        settings=fvw.FvwSettings(panels=6, steps_per_rev=16, revolutions=3.0, run_revolutions=4.0),
    )
    assert result.thrust_N == pytest.approx(float(bemt["thrust_N"]), rel=0.15)
    assert result.torque_Nm == pytest.approx(float(bemt["torque_Nm"]), rel=0.08)
    assert np.all(result.gamma[-1] > 0)  # positive bound circulation on every panel
    assert result.v_axial_induced[-1].mean() > 0  # downwash through the disk
