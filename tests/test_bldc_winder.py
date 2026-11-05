"""CNC needle winder: clearances, toolpath correctness (independent verification), timing, BOM."""

from __future__ import annotations

from pathlib import Path

import pytest

from bldc_motor.design import manufacturing_package as mp
from bldc_motor.design import mechanical, model
from bldc_motor.production import assembly_line
from bldc_motor.production import winder as w

DESIGN_JSON = Path(__file__).resolve().parents[1] / "data" / "motor_designs" / "rl_5in_freestyle_6s_v1" / "design.json"


@pytest.fixture(scope="module")
def setup():
    design, _ = mp.load_design(DESIGN_JSON)
    analysis = model.analyze(design)
    mech = mechanical.derive(analysis)
    tp = w.generate(mech, analysis)
    return analysis, mech, tp


def test_all_clearance_checks_pass(setup) -> None:
    analysis, mech, _ = setup
    failed = [c for c in w.clearance_report(mech, analysis) if not c[3]]
    assert not failed, failed


def test_gcode_winds_exactly_the_winding_table(setup) -> None:
    analysis, _, tp = setup
    points = w.parse_gcode(w.to_gcode(tp, "RL2407"))
    result = w.verify(points, tp.geometry)
    expected = [analysis.design["turns"] * direction for _, direction in analysis.layout.coils]
    assert [round(t) for t in result["turns_per_tooth"]] == expected
    assert all(abs(t - round(t)) < 1e-6 for t in result["turns_per_tooth"])
    assert result["collision_count"] == 0


def test_verifier_catches_a_bad_move(setup) -> None:
    _, _, tp = setup
    points = w.parse_gcode(w.to_gcode(tp, "RL2407"))
    # drive the needle radially into a slot at mid-stack: must be flagged
    bad = points[:40] + [(9.0, 0.0, points[39][2], "move")] + points[40:]
    assert w.verify(bad, tp.geometry)["collision_count"] > 0


def test_parser_ignores_non_motion_g_codes() -> None:
    pts = w.parse_gcode("G21 G90 G94\nG17\nG0 X5 Z-2 A10\nG1 Z3 F600\nM0\n")
    assert [p[:3] for p in pts] == [(5.0, -2.0, 10.0), (5.0, 3.0, 10.0), (5.0, 3.0, 10.0)]
    assert pts[-1][3] == "pause"


def test_layers_stay_inside_the_slot(setup) -> None:
    analysis, _, tp = setup
    g = tp.geometry
    radii = [w.layer_radius(g, j) for j in range(analysis.design["turns"])]
    assert all(g["r_root"] - 1e-9 <= r <= g["r_max"] + 1e-9 for r in radii)
    assert radii[: g["turns_per_layer"]] == sorted(radii[: g["turns_per_layer"]])  # layer 1 root -> tip


def test_three_phases_pause_for_anchoring(setup) -> None:
    _, _, tp = setup
    pauses = [m for m in tp.moves if m.kind == "pause"]
    assert len(pauses) == 6 and sum("START" in m.comment for m in pauses) == 3


def test_cycle_time_beats_hand_winding_and_drives_are_lightly_loaded(setup) -> None:
    analysis, _, tp = setup
    t = w.cycle_time(tp)
    hand = next(s.labour_min for s in assembly_line.STATIONS if s.key == "S3")
    assert t["station_min"] < hand / 2
    loads = w.axis_loads(tp, analysis.internals["mass_stator_iron_g"] + analysis.internals["mass_copper_g"])
    assert loads["a_motor_torque_nm"] < 0.25 * 0.45  # <= 25 % of a NEMA17
    assert loads["a_motor_rpm"] <= 800 and loads["z_motor_rpm"] <= 800


def test_mandrel_respects_needle_link_path(setup) -> None:
    _, _, tp = setup
    md = w.mandrel_dims(tp.geometry, tp.config)
    assert md["relief_back_d"] / 2 <= tp.geometry["r_link"] - tp.config.needle_od_mm / 2 - 0.3 + 1e-9
    assert md["sleeve_d"] == pytest.approx(2 * tp.geometry["r_yoke_inner"])


def test_wire_length_matches_turn_model(setup) -> None:
    analysis, _, tp = setup
    copper_only = 12 * analysis.design["turns"] * analysis.internals["turn_length_mm"] / 1000
    assert copper_only < w.wire_length_m(tp, analysis) < copper_only * 1.15


def test_bom_feeds_the_line_budget() -> None:
    entry = next(e for e in assembly_line.EQUIPMENT if e.key == "cnc_winder")
    assert entry.price_usd == pytest.approx(w.bom_total(), abs=0.01)
    assert w.bom_total() < 1000.0
    assert assembly_line.plan().within_budget


def test_fluidnc_config_steps() -> None:
    cfg = w.fluidnc_config()
    assert "steps_per_mm: 400.000" in cfg and "steps_per_mm: 44.4444" in cfg
