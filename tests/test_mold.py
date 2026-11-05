"""Two-plate mold construction: parting surface geometry and a core/cavity build on a small blade."""

from __future__ import annotations

import numpy as np
import pytest

from prandtl_prop.cad.airfoils import naca4
from prandtl_prop.cad.root import RootGeometry, bolt_centres, full_blade_outlines
from prandtl_prop.cad.sections import build_sections
from prandtl_prop.manufacturing.injection import modify_airfoil
from prandtl_prop.manufacturing.mold import MoldDesign, half_chains, parting_row


def _blade(count: int = 16):
    r = np.linspace(0.05, 0.20, count)
    chord = 0.045 - 0.08 * (r - 0.05)
    beta = 20.0 - 60.0 * (r - 0.05)
    blunt = modify_airfoil(naca4("NACA4412", 40), 1.0, 0.015)  # 0.6 mm TE at 40 mm chord, as molded
    return build_sections(r, chord, beta, [blunt] * count, 0.25)


def test_parting_row_stays_inside_the_section_and_eases_flat() -> None:
    section = _blade()[0]
    outline = section.points_3d()[:, 1:]
    upper, lower = half_chains(outline, te_fraction=0.2)
    y = np.linspace(outline[:, 0].min() - 0.03, outline[:, 0].max() + 0.03, 2001)
    z = parting_row(outline, y, ease_m=0.003, te_fraction=0.2)
    assert upper[0, 0] > outline[:, 0].min()  # the exit is on the TE flat, off the extreme corner
    inside = (y > upper[0, 0] + 1e-4) & (y < outline[:, 0].max() - 1e-4)
    assert np.all(z[inside] < np.interp(y[inside], upper[:, 0], upper[:, 1]))
    assert np.all(z[inside] > np.interp(y[inside], lower[:, 0], lower[:, 1]))
    slope = np.gradient(z, y)
    assert abs(slope[0]) < 1e-3 and abs(slope[-1]) < 1e-3  # flat at the insert edges
    assert np.max(np.abs(np.diff(z))) < 2.0 * (y[1] - y[0])  # continuous and never steep (single-valued, no sliver)


def test_mold_splits_the_part_between_valid_core_and_cavity(tmp_path) -> None:
    pytest.importorskip("cadquery")
    from prandtl_prop.manufacturing.mold import build_mold, export_mold

    sections = _blade()
    geometry = RootGeometry(
        tab_inner_radius_m=0.012,
        tab_outer_radius_m=0.030,
        tab_width_m=0.024,
        tab_thickness_m=0.006,
        transition_sections=4,
        bolt_radii_m=(0.018, 0.025),
    )
    design = MoldDesign(grid_step_m=0.002, margin_m=0.02, root_margin_m=0.025, ejector_radii_m=(0.015, 0.028))
    result = build_mold(full_blade_outlines(sections, geometry), bolt_centres(sections, geometry), geometry.bolt_diameter_m, design)
    metrics = result.metrics
    assert metrics["core_valid"] and metrics["cavity_valid"]
    assert 0.3 < metrics["part_volume_fraction_in_cavity_half"] < 0.7
    assert metrics["impression_over_part_volume"] == pytest.approx(1.0, abs=0.01)  # the impression is the part
    assert metrics["blanks_over_block_volume"] == pytest.approx(1.0, abs=1e-3)  # the halves meet on one parting surface
    assert metrics["clearance_ok"]
    assert result.core.BoundingBox().zmax > result.cavity.BoundingBox().zmax
    paths = export_mold(result, tmp_path)
    assert all(path.stat().st_size > 0 for path in paths.values())
