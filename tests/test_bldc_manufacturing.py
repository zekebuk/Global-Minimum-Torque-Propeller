"""Mechanical design, CAD, drawings and production-plan checks for the manufacturing package."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from bldc_motor.design import manufacturing_package as mp
from bldc_motor.design import mechanical, mission, model
from bldc_motor.production import assembly_line, report, suppliers

ROOT = Path(__file__).resolve().parents[1]
DESIGN_JSON = ROOT / "data" / "motor_designs" / "rl_5in_freestyle_6s_v1" / "design.json"


@pytest.fixture(scope="module")
def built():
    design, _ = mp.load_design(DESIGN_JSON)
    analysis = model.analyze(design)
    return design, analysis, mechanical.derive(analysis, "RL2407")


def test_reference_design_passes_all_mechanical_checks(built) -> None:
    _, _, mech = built
    failed = [c for c in mech.checks if not c[3]]
    assert not failed, failed


def test_standard_parts_are_consistent(built) -> None:
    _, _, mech = built
    d = mech.dims
    assert (d["bearing_d"], d["bearing_D"], d["bearing_B"]) == (4.0, 9.0, 4.0)  # 684ZZ
    clip = mechanical.E_CLIPS[d["e_clip"]]
    assert clip["shaft_min"] <= d["shaft_d"] <= clip["shaft_max"] and clip["groove_d"] < d["shaft_d"]
    assert d["tube_od"] < d["stator_id"]  # bonded slip fit, never interference on the laminations


def test_stack_up_is_ordered(built) -> None:
    z = built[2].dims["z"]
    order = ["shaft_bottom", "clip_groove", "flange_top", "lower_bearing_top", "skirt_bottom", "mag_bottom", "stator_bottom",
             "stator_top", "mag_top", "ring_top", "plate_bottom", "plate_top", "hub_top", "shaft_top"]  # fmt: skip
    values = [z[k] for k in order]
    assert values == sorted(values)
    assert z["mag_top"] - z["mag_bottom"] == pytest.approx(built[2].dims["magnet_length"])


def test_mass_override_changes_aircraft_weight(built) -> None:
    design, analysis, _ = built
    spec = mission.preset_missions()["5in_freestyle_6s"]
    light = mission.evaluate(design, spec)
    heavy = mission.evaluate(design, spec, mass_override_g=analysis.mass_g + 10.0)
    assert heavy.metrics["auw_g"] == pytest.approx(light.metrics["auw_g"] + 4 * 10.0)
    assert heavy.metrics["thrust_to_weight"] < light.metrics["thrust_to_weight"]


def test_cad_solids_and_masses(built) -> None:
    pytest.importorskip("cadquery")
    from bldc_motor.design import cad

    _, analysis, mech = built
    parts = cad.build_parts(mech, analysis)
    assert all(p.val().isValid() for p in parts.values())
    d = mech.dims
    arc = math.radians(d["magnet_arc_deg"])
    analytic = arc / 2 * (d["magnet_ro"] ** 2 - d["magnet_ri"] ** 2) * d["magnet_length"]
    assert parts["P-003_magnet"].val().Volume() == pytest.approx(analytic, rel=1e-3)
    masses = cad.masses_g(mech, analysis, parts)
    assert masses["P-003_magnet"] * d["poles"] == pytest.approx(analysis.internals["mass_magnets_g"], rel=0.01)
    audit = mp.mass_audit(mech, analysis, masses)
    assert audit["cad_total_g"] > audit["model_total_g"]  # documents the model's under-count of hardware


def test_drawing_set_renders(built) -> None:
    pytest.importorskip("cadquery")
    from bldc_motor.design import cad, drawings

    _, analysis, mech = built
    masses = cad.masses_g(mech, analysis)
    masses["total_g"] = 47.5
    sheets = drawings.build_all(mech, analysis, masses, None)
    assert [s.number for s in sheets] == [f"DWG-00{i}" for i in range(9)]
    for i, sheet in enumerate(sheets, start=1):
        svg = sheet.render(i, len(sheets))
        ET.fromstring(svg)  # well-formed
        assert sheet.number in svg and "NOT CHECKED" in svg and "nan" not in svg.lower()
        assert sheet.notes, sheet.number


def test_section_edges_of_a_tube() -> None:
    from bldc_motor.design.drawings import _section_edges

    tube = [(2.0, 0.0), (3.0, 0.0), (3.0, 5.0), (2.0, 5.0)]
    assert sorted(_section_edges(tube)) == [(0.0, pytest.approx(2.0, abs=0.01)), (5.0, pytest.approx(2.0, abs=0.01))]


def test_line_fits_the_budget() -> None:
    plan = assembly_line.plan()
    assert plan.within_budget and plan.capital_total <= assembly_line.BUDGET_USD
    assert plan.contingency == pytest.approx(assembly_line.CONTINGENCY * plan.equipment_total)
    assert plan.labour_min_per_motor == pytest.approx(sum(s.labour_min for s in assembly_line.STATIONS))
    assert plan.capacity_per_year_single_shift[2] == pytest.approx(2 * plan.capacity_per_year_single_shift[1], abs=2)


def test_cnc_winder_reduces_cost_and_raises_capacity() -> None:
    base, cnc = assembly_line.plan(), assembly_line.plan(cnc_winder_min=3.0)
    assert cnc.capacity_per_year_single_shift[1] > base.capacity_per_year_single_shift[1]
    key = list(base.unit_cost)[1]
    assert cnc.unit_cost[key]["total_usd"] < base.unit_cost[key]["total_usd"]


def test_equipment_and_suppliers_have_provenance() -> None:
    stations = {s.key for s in assembly_line.STATIONS}
    keys = {e.key for e in assembly_line.EQUIPMENT}
    for e in assembly_line.EQUIPMENT:
        assert e.status in {"page", "snippet", "listing", "estimate"}
        if e.status != "estimate":
            assert e.url.startswith("http"), e.key
    for s in assembly_line.STATIONS:
        assert set(s.equipment) <= keys, s.key
    assert stations == {f"S{i}" for i in range(1, 12)}
    for s in suppliers.SUPPLIERS:
        assert s.url.startswith("http") and s.status in {"page", "snippet", "listing", "quote"}


def test_production_report_writes(tmp_path) -> None:
    files = report.write_all(tmp_path)
    assert all(p.exists() for p in files.values())
    ET.fromstring(files["layout"].read_text())
    plan_md = files["plan"].read_text()
    assert "within $15,000" in plan_md and "684ZZ" in files["suppliers"].read_text()
