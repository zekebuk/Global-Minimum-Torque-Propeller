from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from Geometry_optimization import (
    FAILURE_ALPHA_LIMIT_FRACTION,
    FAILURE_CLIPPING_FRACTION,
    FAILURE_NEGATIVE_OR_NONFINITE_LOADING,
    FAILURE_TARGET_INFEASIBLE_LOCKED,
    DesignInputs,
    DesignResult,
    ObjectiveWeights,
    OptimizationParameters,
    ReferenceStation,
    StationResult,
    add_arguments,
    alpha_match_constraints_pass,
    apply_defaults,
    build_candidate_result,
    builtin_reference_stations,
    free_design_enabled,
    match_thrust_with_base_alpha,
    result_summary_dict,
    run_thrust_validity_sweep,
    solve_geometry,
    weights_for_mode,
)


def _station(
    index: int,
    mu: float,
    twist_deg: float,
    *,
    hit_cl_limit: bool = False,
    hit_alpha_max: bool = False,
    negative_force: bool = False,
) -> StationResult:
    return StationResult(
        station_index=index,
        mu=mu,
        radius_m=0.1 + 0.01 * index,
        radius_in=4.0 + index,
        dr_m=0.01,
        chord_m=0.04,
        chord_in=1.57,
        chord_fraction=0.15,
        twist_deg=twist_deg,
        cad_plane_angle_deg=90.0 - twist_deg,
        phi_deg=5.0,
        alpha_deg=19.0 if hit_alpha_max else 4.0,
        unclipped_cl_demand=1.6 if hit_cl_limit else 0.4,
        cl=1.35 if hit_cl_limit else 0.4,
        cl_max=1.35,
        cl_margin=0.0 if hit_cl_limit else 0.95,
        cd=0.02,
        reynolds_number=250000.0,
        alpha_stall_deg=12.0,
        dthrust_n=-0.1 if negative_force else 1.0,
        dtorque_nm=0.03,
        airfoil="MH115",
        target_spanload_weight=0.1,
        actual_spanload_weight=0.1,
        reference_chord_fraction=0.15,
        reference_twist_deg=twist_deg,
        hit_chord_min=False,
        hit_chord_max=False,
        hit_alpha_min=False,
        hit_alpha_max=hit_alpha_max,
        hit_cl_limit=hit_cl_limit,
        is_clipped=hit_cl_limit,
        negative_force=negative_force,
        impossible_force=False,
    )


def _synthetic_result(stations: list[StationResult], config: DesignInputs | None = None) -> DesignResult:
    return DesignResult(
        config=config or DesignInputs(thrust_target_n=10.0),
        target_alpha_deg=0.0,
        objective=1.0,
        thrust_n=10.0,
        torque_nm=1.0,
        power_w=400.0,
        eta_ideal=0.5,
        eta_dynamic=0.0,
        eta_static=0.5,
        induced_velocity_mps=7.0,
        j_advance=0.0,
        ct=0.1,
        cq=0.01,
        cp=0.02,
        spanload_error=0.0,
        reference_twist_rms_deg=0.0,
        reference_twist_max_deg=0.0,
        reference_chord_rms_percent=0.0,
        smoothness_penalty=0.0,
        bounds_penalty=0.0,
        normalized_thrust_error=0.0,
        normalized_torque=1.0,
        aero_calibration_scale=1.0,
        objective_breakdown={},
        weights=ObjectiveWeights(1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
        parameters=OptimizationParameters(),
        stations=stations,
        warnings=[],
        convergence_history=[],
        reference_source="synthetic",
        optimizer_name="unit_test",
    )


def _preset_args(preset: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    return apply_defaults(parser.parse_args(["--preset", preset, "--max-iterations", "0"]))


@pytest.mark.parametrize(
    "preset",
    ["thesis_gmt_22", "thesis_gmt_22_static", "thesis_gmt_22_manufacturable", "thesis_mil_22_manufacturable"],
)
def test_thesis_presets_lock_chord_airfoil_and_twist_only(preset: str) -> None:
    args = _preset_args(preset)

    assert args.lock_chord is True
    assert args.lock_airfoil is True
    assert args.optimize_twist_only is True
    assert args.manufacturable_twist is True


def test_free_design_preset_unlocks_chord_airfoil_and_twist() -> None:
    args = _preset_args("thesis_gmt_22_free_design")

    assert args.mode == "gmt_requirements"
    assert args.lock_chord is False
    assert args.lock_airfoil is False
    assert args.optimize_twist_only is False
    assert args.manufacturable_twist is True


def test_thesis_summary_reports_locked_inputs() -> None:
    config = DesignInputs(
        lock_chord=True,
        lock_airfoil=True,
        optimize_twist_only=True,
        manufacturable_twist=True,
        max_iterations=0,
    )
    result = build_candidate_result(
        config,
        builtin_reference_stations(),
        OptimizationParameters(),
        weights_for_mode(config.mode),
        1.0,
        "unit",
        "unit",
    )
    summary = result_summary_dict(result)

    assert summary["chord_locked"] is True
    assert summary["airfoil_locked"] is True
    assert summary["optimize_twist_only"] is True
    assert summary["locked_inputs"]["chord_locked"] is True
    assert summary["locked_inputs"]["airfoil_locked"] is True


def test_thesis_default_output_stays_under_geometry_optimizer() -> None:
    args = _preset_args("thesis_gmt_22")
    output_dir = str(args.output_dir).replace("\\", "/")

    assert "/results/geometry_optimizer/" in output_dir
    assert "/results/aero" not in output_dir


def test_cl_clipping_invalidates_thesis_export() -> None:
    stations = [_station(i, 0.2 + 0.05 * i, 20.0 - 0.5 * i, hit_cl_limit=i <= 2) for i in range(1, 11)]
    result = _synthetic_result(stations, DesignInputs(thrust_target_n=10.0, max_cl_limit_fraction_pct=10.0))
    summary = result_summary_dict(result)

    assert summary["valid_for_thesis_export"] is False
    assert FAILURE_CLIPPING_FRACTION in summary["failure_reasons"]


def test_alpha_limit_invalidates_thesis_export() -> None:
    stations = [_station(i, 0.2 + 0.05 * i, 20.0 - 0.5 * i, hit_alpha_max=i <= 2) for i in range(1, 11)]
    result = _synthetic_result(stations, DesignInputs(thrust_target_n=10.0, max_alpha_limit_fraction_pct=10.0))
    summary = result_summary_dict(result)

    assert summary["valid_for_thesis_export"] is False
    assert FAILURE_ALPHA_LIMIT_FRACTION in summary["failure_reasons"]


def test_negative_station_loading_invalidates_thesis_export() -> None:
    stations = [_station(i, 0.2 + 0.05 * i, 20.0 - 0.5 * i, negative_force=i == 3) for i in range(1, 11)]
    result = _synthetic_result(stations, DesignInputs(thrust_target_n=10.0))
    summary = result_summary_dict(result)

    assert summary["valid_for_thesis_export"] is False
    assert summary["negative_loading_station_count"] == 1
    assert FAILURE_NEGATIVE_OR_NONFINITE_LOADING in summary["failure_reasons"]


def test_twist_manufacturability_penalty_is_active() -> None:
    config = DesignInputs(
        mode="reproduce_reference",
        manufacturable_twist=True,
        max_positive_twist_bump_deg=0.1,
        twist_constraint_start_mu=0.35,
        max_abs_twist_gradient_deg_per_mu_limit=200.0,
        max_twist_curvature_limit=5000.0,
    )
    reference_stations = [
        ReferenceStation(0.20, 0.15, 20.0, "MH115"),
        ReferenceStation(0.40, 0.15, 18.0, "MH115"),
        ReferenceStation(0.60, 0.15, 19.0, "MH115"),
        ReferenceStation(0.80, 0.15, 17.0, "MH115"),
    ]

    result = build_candidate_result(
        config,
        reference_stations,
        OptimizationParameters(),
        weights_for_mode(config.mode),
        1.0,
        "unit",
        "unit",
    )
    summary = result_summary_dict(result)

    assert result.objective_breakdown["twist_bump_penalty"] > 0.0
    assert summary["twist_manufacturability"]["manufacturable_twist_pass"] is False
    assert summary["valid_for_thesis_export"] is False


def test_alpha_matching_prefers_lower_thrust_valid_candidate_over_clipped_match() -> None:
    config = DesignInputs(
        lock_chord=True,
        lock_airfoil=True,
        optimize_twist_only=True,
        manufacturable_twist=True,
        thrust_target_n=35.0,
        alpha_search_step_deg=2.0,
        optimizer="coordinate_search",
        max_iterations=0,
        max_positive_twist_bump_deg=5.0,
        max_abs_twist_gradient_deg_per_mu_limit=500.0,
        max_twist_curvature_limit=10000.0,
    )
    weights = weights_for_mode(config.mode)

    match = match_thrust_with_base_alpha(
        config,
        builtin_reference_stations(),
        OptimizationParameters(),
        weights,
        1.0,
        "unit",
        "unit",
        [],
        1.0,
    )

    assert match.selected_result is not None
    assert alpha_match_constraints_pass(match.selected_result)
    assert match.selected_result.thrust_n < config.thrust_target_n
    assert match.selected_result.cl_limit_fraction_pct <= config.max_cl_limit_fraction_pct
    assert FAILURE_TARGET_INFEASIBLE_LOCKED in match.failure_reasons


def test_locked_infeasible_case_is_not_successful() -> None:
    config = DesignInputs(
        lock_chord=True,
        lock_airfoil=True,
        optimize_twist_only=True,
        manufacturable_twist=True,
        thrust_target_n=35.0,
        optimizer="coordinate_search",
        max_iterations=0,
    )

    result = solve_geometry(config, builtin_reference_stations())
    summary = result_summary_dict(result)

    assert summary["valid_for_thesis_export"] is False
    assert FAILURE_TARGET_INFEASIBLE_LOCKED in summary["failure_reasons"]


def test_thrust_validity_sweep_writes_csv_and_json(tmp_path: Path) -> None:
    config = DesignInputs(
        lock_chord=True,
        lock_airfoil=True,
        optimize_twist_only=True,
        manufacturable_twist=True,
        max_iterations=0,
        optimizer="coordinate_search",
    )
    audits_dir = tmp_path / "results" / "geometry_optimizer" / "audits"

    outputs = run_thrust_validity_sweep(
        config,
        builtin_reference_stations(),
        [],
        "unit",
        targets=(15.0, 35.0),
        audits_dir=audits_dir,
    )

    assert outputs["csv_path"].exists()
    assert outputs["json_path"].exists()
    payload = json.loads(outputs["json_path"].read_text(encoding="utf-8"))
    assert "highest_valid_target_thrust_N" in payload
    assert "highest_valid_thrust_at_4000_rpm_N" in payload
    assert "lowest_RPM_that_can_meet_35N" in payload
    assert payload["thirty_five_N_feasible_under_locked_chord_airfoil_constraints"] in {True, False}
    assert len(payload["rows"]) == 2


def test_free_design_mode_allows_chord_changes_within_bounds() -> None:
    config = DesignInputs(
        mode="gmt_requirements",
        lock_chord=False,
        lock_airfoil=False,
        optimize_twist_only=False,
        manufacturable_twist=True,
        optimizer="coordinate_search",
        max_iterations=0,
        chord_min_in=0.5,
        chord_max_in=3.4,
    )

    result = solve_geometry(config, builtin_reference_stations())
    summary = result_summary_dict(result)

    assert free_design_enabled(config) is True
    assert summary["free_design_enabled"] is True
    assert any(abs(station.chord_fraction - station.reference_chord_fraction) > 1.0e-8 for station in result.stations)
    assert all(config.chord_min_m <= station.chord_m <= config.chord_max_m for station in result.stations)


def test_gmt_aggressiveness_can_reduce_to_recover_validity(monkeypatch: pytest.MonkeyPatch) -> None:
    import Geometry_optimization as geom

    calls: list[float] = []

    def fake_once(config: DesignInputs, reference_stations=None, initial_warnings=(), reference_source="unknown reference"):
        calls.append(config.gmt_aggressiveness)
        station = _station(1, 0.5, 10.0, hit_cl_limit=config.gmt_aggressiveness > 0.25)
        result = _synthetic_result([station], config)
        valid = config.gmt_aggressiveness <= 0.25
        return replace_result_validity(result, valid, config.gmt_aggressiveness)

    def replace_result_validity(result: DesignResult, valid: bool, aggressiveness: float) -> DesignResult:
        from dataclasses import replace

        return replace(
            result,
            valid_for_thesis_export=valid,
            failure_reasons=() if valid else (FAILURE_CLIPPING_FRACTION,),
            failure_reason="none" if valid else FAILURE_CLIPPING_FRACTION,
            cl_limit_fraction_pct=0.0 if valid else 100.0,
            requested_gmt_aggressiveness=1.0,
            achieved_gmt_aggressiveness=aggressiveness,
        )

    monkeypatch.setattr(geom, "_optimize_geometry_once", fake_once)

    result = geom.optimize_geometry(DesignInputs(mode="gmt_requirements", gmt_aggressiveness=1.0))

    assert result.valid_for_thesis_export is True
    assert result.requested_gmt_aggressiveness == 1.0
    assert result.achieved_gmt_aggressiveness <= 0.25
    assert calls[0] == 1.0


def test_geometry_optimizer_plots_do_not_create_pdfs() -> None:
    source = (Path(__file__).resolve().parent / "Geometry_optimization.py").read_text(encoding="utf-8").lower()

    assert ".pdf" not in source
    assert "pdf" not in source.replace("png_svg_only", "")
