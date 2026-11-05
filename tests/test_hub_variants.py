"""Folding hub and one-piece prop: closed-form checks of the hinge dynamics and builds on a small blade."""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from prandtl_prop.cad.hub import SCREWS, HubDesign
from prandtl_prop.cad.hub_variants import (
    folding_stack,
    hull_of_circles,
    hull_polygon,
    ideal_pin_y,
    lag_equilibrium,
    lag_frequency_per_rev,
    max_fold_angle,
)
from prandtl_prop.cad.root import RootGeometry, bolt_centres, full_blade_outlines, root_outlines, tab_outline_3d


def _polygon_area(points: np.ndarray) -> float:
    x, y = points[:, 0], points[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def test_hull_of_two_circles_is_an_obround() -> None:
    segments = hull_of_circles([(-0.03, 0.0, 0.01), (0.03, 0.0, 0.01), (0.0, 0.0, 0.005)])
    assert [kind for kind, _ in segments].count("line") == 2  # the small centre circle is inside
    area = _polygon_area(hull_polygon([(-0.03, 0.0, 0.01), (0.03, 0.0, 0.01)], step_deg=0.5))
    assert area == pytest.approx(math.pi * 0.01**2 + 0.06 * 0.02, rel=1e-3)


def test_lag_hinge_closed_form() -> None:
    # Point mass d beyond a hinge at offset e, on the radial line: no aero moment, no lag; nu = sqrt(e / d).
    e, d, m = 0.03, 0.09, 0.06
    assert lag_equilibrium(m, (e + d, 0.0, 0.0), (e, 0.0), 4000.0, 0.0) == pytest.approx(0.0, abs=1e-9)
    assert lag_frequency_per_rev(m, (e + d, 0.0, 0.0), (e, 0.0), 0.0, 4000.0) == pytest.approx(math.sqrt(e / d), rel=1e-4)
    # A drag moment lags the blade; placing the pin at ideal_pin_y restores zero lag.
    drag = -0.4
    assert lag_equilibrium(m, (e + d, 0.0, 0.0), (e, 0.0), 4000.0, drag) < 0.0
    y_p = ideal_pin_y(m, (e + d, 0.0, 0.0), e, 4000.0, drag)
    assert lag_equilibrium(m, (e + d, 0.0, 0.0), (e, y_p), 4000.0, drag) == pytest.approx(0.0, abs=1e-6)


def test_fold_angle_of_two_bars() -> None:
    # Bars 10 mm wide from their pins at x = +/-20 mm out to 120 mm: parallel, 40 mm apart, when folded 90 deg.
    bar = np.array([[0.015, -0.005], [0.12, -0.005], [0.12, 0.005], [0.015, 0.005]])
    # Past 90 deg the tips swing toward each other and meet near 90 + asin(40 / 210) = 101 deg, less the bar width.
    assert 95.0 <= max_fold_angle(bar, (0.02, 0.0), limit_deg=120.0) <= 101.0
    wide = np.array([[0.015, -0.03], [0.12, -0.03], [0.12, 0.03], [0.015, 0.03]])  # 60 mm wide: they collide
    assert max_fold_angle(wide, (0.02, 0.0), limit_deg=120.0) < 90.0


def test_folding_stack_uses_stock_parts() -> None:
    stack = folding_stack(HubDesign(variant="folding"), 0.0085, SCREWS["M3"])
    assert stack is not None
    assert stack["dowel_press_depth_m"] >= 0.006 and stack["dowel_press_depth_m"] <= stack["bottom_plate_m"]
    assert 0.0024 <= stack["motor_screw_engagement_m"] <= 0.0035


def _sections():
    from prandtl_prop.cad.airfoils import naca4
    from prandtl_prop.cad.sections import build_sections
    from prandtl_prop.manufacturing.injection import modify_airfoil

    r = np.linspace(0.056, 0.20, 16)
    airfoil = modify_airfoil(naca4("NACA4412", 40), 1.0, 0.015)
    return build_sections(r, 0.050 - 0.08 * (r - 0.056), 22.0 - 60.0 * (r - 0.056), [airfoil] * len(r), 0.25)


def test_round_tab_end_is_centred_on_the_pin() -> None:
    geometry = RootGeometry(
        tab_end="round",
        bolt_radii_m=(0.029,),
        tab_inner_radius_m=0.015,
        tab_outer_radius_m=0.045,
        transition_sections=3,
        corner_exponent=8.0,
    )
    outlines = root_outlines(_sections()[0], geometry)
    pin = bolt_centres(_sections(), geometry)[0]
    for outline in outlines[:7]:  # the semicircular end
        half_width = 0.5 * np.ptp(outline[:, 1])
        assert math.hypot(outline[0, 0] - pin[0], half_width) == pytest.approx(0.014, rel=0.02)
    with pytest.raises(ValueError):
        root_outlines(_sections()[0], dataclasses.replace(geometry, tab_inner_radius_m=0.012))


def test_folding_hub_builds_and_folds(tmp_path) -> None:
    pytest.importorskip("cadquery")
    from prandtl_prop.cad.hub import export_hub
    from prandtl_prop.cad.hub_variants import build_folding_hub

    sections = _sections()
    geometry = RootGeometry(
        tab_end="round",
        bolt_radii_m=(0.029,),
        bolt_diameter_m=0.008,
        tab_inner_radius_m=0.015,
        tab_outer_radius_m=0.045,
        transition_sections=3,
        corner_exponent=8.0,
    )
    result = build_folding_hub(
        geometry,
        bolt_centres(sections, geometry)[0],
        tab_outline_3d(sections, geometry),
        full_blade_outlines(sections, geometry),
        blade_mass_kg=0.05,
        blade_cg_m=(0.11, -0.001, 0.0),
        blade_inertia_cg_zz=2.5e-4,
        rpm=4000.0,
        thrust_N=35.0,
        torque_Nm=0.85,
        aero_moment_about_pin_Nm=-0.35,
        root_flap_moment_Nm=2.5,
        tab_allowable_Pa=60e6,
        design=HubDesign(variant="folding"),
    )
    assert set(result.parts) == {"hub_top_plate", "hub_core_spacer", "hub_bottom_plate"}
    assert all(part.isValid() for part in result.parts.values())
    margins = {k: v for k, v in result.metrics.items() if k.endswith("_margin")}
    assert all(v > 0 for v in margins.values()), margins
    assert 0.2 < result.metrics["lag_frequency_per_rev"] < 0.9
    assert result.metrics["max_same_side_fold_deg"] >= 90.0
    paths = export_hub(result, tmp_path)
    assert paths["hub_core_spacer_dxf"].stat().st_size > 0


def test_one_piece_prop_and_mold(tmp_path) -> None:
    pytest.importorskip("cadquery")
    from prandtl_prop.cad.step import one_piece_solid, report
    from prandtl_prop.manufacturing.mold import MoldDesign, build_one_piece_mold, export_mold

    sections = _sections()
    geometry = RootGeometry(bolt_radii_m=(), tab_inner_radius_m=0.016, tab_outer_radius_m=0.024, transition_sections=8, corner_exponent=8.0)
    outlines = full_blade_outlines(sections, geometry)
    holes = [(0.0125 * math.cos(math.radians(45 + 90 * k)), 0.0125 * math.sin(math.radians(45 + 90 * k)), 0.0) for k in range(4)]
    prop, half = one_piece_solid(outlines, 0.036, 0.010, 0.0, [(0, 0, 0)] + holes, [0.010] + [0.0034] * 4)
    assert prop.isValid() and half.isValid() and len(prop.Solids()) == 1
    assert report(half).volume_m3 == pytest.approx(0.5 * report(prop).volume_m3, rel=1e-3)
    result = build_one_piece_mold(outlines, 0.036, 0.010, 0.0, [(x, y, 0.0034) for x, y, _ in holes], MoldDesign(grid_step_m=0.002))
    metrics = result.metrics
    assert metrics["core_valid"] and metrics["cavity_valid"]
    assert metrics["impression_over_part_volume"] == pytest.approx(1.0, abs=0.02)
    assert 0.4 < metrics["part_volume_fraction_in_cavity_half"] < 0.6
    assert metrics["blanks_over_block_volume"] == pytest.approx(1.0, abs=1e-3)
    assert all(p.stat().st_size > 0 for p in export_mold(result, tmp_path).values())
