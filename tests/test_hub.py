"""Center hub: joint loads against closed form, fastener sizing, and a full build on a small blade."""

from __future__ import annotations

import math

import numpy as np
import pytest

from prandtl_prop.cad.hub import (
    SCREWS,
    HubDesign,
    bolt_group_shear,
    clamp_contact_fraction,
    ideal_tab_centre_s,
    motor_screw_fit,
    root_joint_loads,
    screw_for_hole,
    tapped_bolt_length,
)
from prandtl_prop.cad.root import RootGeometry, bolt_centres, root_outlines


def test_bolt_group_shear_closed_form() -> None:
    bolts = np.array([[0.020, 0.0], [0.030, 0.0]])
    through_centroid = bolt_group_shear(bolts, (1000.0, 0.0), (0.025, 0.0))
    np.testing.assert_allclose(through_centroid, [[500.0, 0.0], [500.0, 0.0]])
    # Force along x through the origin, 10 mm off the bolt line: couple 1000 N * 0.010 m over 10 mm spacing.
    offset = bolt_group_shear(bolts - [0.0, 0.010], (1000.0, 0.0), (0.0, 0.0))
    np.testing.assert_allclose(offset[:, 0], [500.0, 500.0], atol=1e-9)
    np.testing.assert_allclose(np.abs(offset[:, 1]), [1000.0, 1000.0], rtol=1e-12)


def test_ideal_tab_centre_puts_bolts_on_the_centrifugal_line() -> None:
    cg = (0.116, -0.0099, 0.0)
    radii = (0.020, 0.029)
    s = ideal_tab_centre_s(cg, radii)
    bolts = np.array([[r, -s] for r in radii])  # zero-pitch tab: y = -s
    loads = root_joint_loads(0.075, cg, 4000.0, bolts)
    assert abs(loads["root_inplane_moment_Nm"]) < 0.02 * loads["centrifugal_force_N"] * 0.0245
    assert loads["centrifugal_force_N"] == pytest.approx(0.075 * (4000 * math.pi / 30) ** 2 * math.hypot(cg[0], cg[1]))
    off_line = root_joint_loads(0.075, cg, 4000.0, bolts - [0.0, 0.0115])
    assert off_line["bolt_max_shear_N"] > 2.5 * loads["bolt_max_shear_N"]  # the chord-midpoint tab of the first design


def test_fastener_sizing() -> None:
    assert screw_for_hole(0.0053).name == "M5"
    design = HubDesign()
    length, engagement = tapped_bolt_length(SCREWS["M5"], 0.001 + 0.004 + 0.0085, 0.008, design)
    assert length == pytest.approx(0.020) and engagement >= 1.25 * 0.005 and engagement <= 0.0075
    assert tapped_bolt_length(SCREWS["M5"], 0.0135, 0.005, design) is None  # plate too thin for 1.25 d
    length, depth, engagement = motor_screw_fit(SCREWS["M3"], 0.008, design)
    assert engagement <= design.motor_thread_depth_m - 0.5e-3 and engagement >= 0.8 * 0.003
    assert depth >= SCREWS["M3"].head_h + 0.3e-3 and length - (0.008 - depth) == pytest.approx(engagement)


def _root_section():
    from prandtl_prop.cad.airfoils import naca4
    from prandtl_prop.cad.sections import build_sections
    from prandtl_prop.manufacturing.injection import modify_airfoil

    r = np.linspace(0.05, 0.20, 16)
    airfoil = modify_airfoil(naca4("NACA4412", 40), 1.0, 0.015)
    return build_sections(r, 0.045 - 0.08 * (r - 0.05), 20.0 - 60.0 * (r - 0.05), [airfoil] * len(r), 0.25)


def test_flat_tab_faces_give_the_plates_contact() -> None:
    section = _root_section()[0]
    round_tab = root_outlines(section, RootGeometry(tab_outer_radius_m=0.030, corner_exponent=4.0))[0]
    flat_tab = root_outlines(section, RootGeometry(tab_outer_radius_m=0.030, corner_exponent=8.0))[0]
    assert min(clamp_contact_fraction(round_tab)) < 0.5 < 0.7 < min(clamp_contact_fraction(flat_tab))


def test_hub_builds_with_valid_plates_and_positive_margins(tmp_path) -> None:
    pytest.importorskip("cadquery")
    from prandtl_prop.cad.hub import build_hub, export_hub

    sections = _root_section()
    geometry = RootGeometry(
        tab_inner_radius_m=0.014, tab_outer_radius_m=0.034, corner_exponent=8.0, tab_centre_s_m=0.002, transition_sections=4
    )
    result = build_hub(
        geometry,
        bolt_centres(sections, geometry),
        root_outlines(sections[0], geometry)[0],
        blade_mass_kg=0.06,
        blade_cg_m=(0.11, -0.002, 0.0),
        rpm=4000.0,
        thrust_N=35.0,
        torque_Nm=0.85,
        blade_count=2,
        root_flap_moment_Nm=2.7,
        tab_allowable_Pa=60e6,
    )
    assert result.top_plate.isValid() and result.bottom_plate.isValid()
    margins = {k: v for k, v in result.metrics.items() if k.endswith("_margin")}
    assert margins and all(v > 0 for v in margins.values()), margins
    assert result.metrics["motor_access_clearance_to_tabs_m"] > 0
    assert any("placeholder" in w for w in result.warnings)  # motor pattern not confirmed
    # Holes: top has 4 bolt + 4 access + centre; bottom has 4 tapped + 4 counterbored + centre.
    assert len([f for f in result.top_plate.Faces() if f.geomType() == "CYLINDER"]) >= 9
    paths = export_hub(result, tmp_path)
    assert all(path.stat().st_size > 0 for path in paths.values())
    assert sum(row["qty"] for row in result.bom if row["source"].startswith("COTS ISO")) == 12
