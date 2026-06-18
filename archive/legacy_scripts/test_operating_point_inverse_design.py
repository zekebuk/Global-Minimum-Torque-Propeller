from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest


CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CODE_DIR.parent
GEOMETRY_DIR = CODE_DIR / "geometry"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(GEOMETRY_DIR) not in sys.path:
    sys.path.insert(0, str(GEOMETRY_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import operating_point_inverse_design as inverse_design
import run_final_thesis_solver


@pytest.fixture(scope="module")
def design_bundle() -> inverse_design.DesignBundle:
    return inverse_design.run_design(write_outputs=False, make_plots=False)


def test_inverse_design_uses_geometry_bemt_backend() -> None:
    source_path = GEOMETRY_DIR / "operating_point_inverse_design.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert "bemt" in imports
    assert "postprocess_final_geometries" not in source
    assert "final_post" not in source


def test_mil_gmt_fixed_geometry_variables_match(design_bundle: inverse_design.DesignBundle) -> None:
    mil = design_bundle.mil.stations
    gmt = design_bundle.gmt.stations

    assert len(mil) == len(gmt)
    for mil_station, gmt_station in zip(mil, gmt):
        assert gmt_station.mu == pytest.approx(mil_station.mu, abs=1.0e-12)
        assert gmt_station.radius_m == pytest.approx(mil_station.radius_m, abs=1.0e-12)
        assert gmt_station.chord_m == pytest.approx(mil_station.chord_m, abs=1.0e-12)
        assert gmt_station.airfoil == mil_station.airfoil


def test_operating_point_design_hits_target_thrust(design_bundle: inverse_design.DesignBundle) -> None:
    for case in (design_bundle.mil, design_bundle.gmt):
        assert abs(case.summary["thrust_error_pct"]) <= inverse_design.MAX_FINAL_THRUST_ERROR_PCT
        assert case.summary["negative_thrust_station_count"] == 0
        assert case.summary["alpha_outside_polar_range_count"] == 0


def test_static_hover_uses_induced_velocity_not_zero_freestream(design_bundle: inverse_design.DesignBundle) -> None:
    op = inverse_design.OperatingPoint()

    assert op.v_inf_m_s == 0.0
    assert op.target_induced_velocity_m_s > 0.0
    for row in design_bundle.mil.aero_rows + design_bundle.gmt.aero_rows:
        assert row["actuator_disk_initial_Vi_m_s"] == pytest.approx(op.target_induced_velocity_m_s)
        assert row["inflow_model"] == inverse_design.INFLOW_MODEL
        assert row["Vi_m_s"] > 0.0
        assert row["Va_m_s"] > 0.0
        assert row["phi_deg"] > 0.0


def test_gmt_lambda_shape_moves_loading_inboard_and_unloads_tip(design_bundle: inverse_design.DesignBundle) -> None:
    validation = design_bundle.validation

    assert validation["checks"]["gmt_tip_unloaded_relative_to_mil"]
    assert validation["checks"]["gmt_tip_loading_reduction_target"]
    assert validation["checks"]["gmt_center_of_thrust_inboard_of_mil"]
    assert validation["checks"]["gmt_center_shift_target"]
    assert validation["checks"]["gmt_geometry_not_barely_different"]
    assert design_bundle.gmt.summary["tip_loading_fraction"] < design_bundle.mil.summary["tip_loading_fraction"]
    assert design_bundle.gmt.summary["center_of_thrust_mu"] < design_bundle.mil.summary["center_of_thrust_mu"]
    assert design_bundle.gmt.summary["max_abs_twist_delta_vs_mil_deg"] >= inverse_design.MIN_DESIRED_TWIST_DIFFERENCE_DEG
    assert design_bundle.gmt.summary["lambda_gmt"] > 0.0


def test_gmt_torque_gate_warns_if_not_lower(design_bundle: inverse_design.DesignBundle) -> None:
    torque_lower = design_bundle.gmt.summary["total_torque_Nm"] < design_bundle.mil.summary["total_torque_Nm"]

    if torque_lower:
        assert design_bundle.validation["checks"]["gmt_torque_lower_than_mil"]
    else:
        assert not design_bundle.validation["checks"]["gmt_torque_lower_than_mil"]
        assert "GMT torque is not lower than MIL at matched thrust" in design_bundle.validation["warnings"]
    if abs(design_bundle.summary["gmt_torque_reduction_pct"]) < inverse_design.MIN_STRONG_TORQUE_REDUCTION_PCT:
        assert any("does not show a strong modeled torque benefit" in item for item in design_bundle.validation["warnings"])


def test_no_artificial_twist_plateau_or_large_smoothing_change(
    design_bundle: inverse_design.DesignBundle,
) -> None:
    assert design_bundle.validation["checks"]["mil_no_artificial_plateau"]
    assert design_bundle.validation["checks"]["gmt_no_artificial_plateau"]
    assert design_bundle.mil.summary["max_smoothing_delta_deg"] <= inverse_design.MAX_SMOOTHING_DELTA_DEG
    assert design_bundle.gmt.summary["max_smoothing_delta_deg"] <= inverse_design.MAX_SMOOTHING_DELTA_DEG


def test_gmt_uses_scalar_lambda_loading_search(design_bundle: inverse_design.DesignBundle) -> None:
    gmt = design_bundle.gmt.summary
    history = [row for row in design_bundle.gmt.optimization_history if "lambda_gmt" in row]

    assert gmt["lambda_search_method"].startswith("bounded scalar")
    assert gmt["gmt_nominal_inboard_boost_deg"] == inverse_design.GMT_INBOARD_BOOST_AMPLITUDE_DEG
    assert gmt["gmt_nominal_outer_unload_deg"] == inverse_design.GMT_OUTER_UNLOAD_AMPLITUDE_DEG
    assert gmt["gmt_nominal_tip_unload_deg"] == inverse_design.GMT_TIP_UNLOAD_AMPLITUDE_DEG
    assert len(history) >= 20
    assert any(row["meets_tip_unload_target"] for row in history)
    assert any(row["meets_visible_twist_target"] for row in history)


def test_legacy_repair_twist_is_passive_validation_only() -> None:
    source = inspect.getsource(run_final_thesis_solver.repair_twist)

    assert "station.beta_final_deg = station.beta_raw_deg" in source
    assert "minimum_defensible_beta" not in source
    assert "MAX_ADJACENT_TWIST_DROP_DEG" not in source
    assert "repaired" not in source
