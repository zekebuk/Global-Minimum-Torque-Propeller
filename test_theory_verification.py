import math
import sys
from argparse import ArgumentParser
from pathlib import Path
from types import SimpleNamespace

import pytest

CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CODE_DIR.parent if CODE_DIR.name.lower() == "code" else CODE_DIR
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from Geometry_optimization import (
    NASA_SCITECH_ORIGINAL_OPERATING_POINT,
    ORIGINAL_REFERENCE_DIAMETER_IN,
    SCALED_REFERENCE_DIAMETER_IN,
    SCALED_REFERENCE_SCALE_FACTOR,
    THESIS_22_STATIC_OPERATING_POINT,
    add_arguments,
    apply_defaults,
    diameter_in_to_radius_m,
    is_thesis_22_static_operating_point,
    load_reference_geometry,
    meters_to_inches,
    read_geometry_xlsx,
    reference_workbook_scale_rows,
    scaled_reference_no_double_scaling_warnings,
    validate_scaled_reference_pair,
    validate_scaled_reference_rows,
    write_scaled_reference_geometry_audit,
)

from theory_verification import (
    VERIFICATION_OUTCOMES,
    apply_verification_preset,
    build_arg_parser,
    common_grid_for,
    cumulative_fraction,
    integrate_annular_band,
    interpolate_series,
    loading_reduction_pct,
    propeller_coefficients,
    rpm_to_rad_s,
    run_verification,
    shaft_power_w,
)


WORKBOOK = CODE_DIR / "Reference_Propeller_Geometry.xlsx"


def test_units_rpm_to_rad_s():
    assert abs(rpm_to_rad_s(60.0) - 2.0 * math.pi) < 1.0e-12


def test_power_equals_torque_times_omega():
    omega = rpm_to_rad_s(4000.0)
    assert abs(shaft_power_w(1.25, omega) - 1.25 * omega) < 1.0e-12


def test_ct_cq_cp_definitions():
    thrust = 10.0
    torque = 2.0
    rho = 1.0
    rpm = 120.0
    diameter = 3.0
    n_rev_per_s = rpm / 60.0

    ct, cq, cp = propeller_coefficients(thrust, torque, rho, rpm, diameter)

    assert abs(ct - thrust / (rho * n_rev_per_s**2 * diameter**4)) < 1.0e-12
    assert abs(cq - torque / (rho * n_rev_per_s**2 * diameter**5)) < 1.0e-12
    assert abs(cp - (torque * rpm_to_rad_s(rpm)) / (rho * n_rev_per_s**3 * diameter**5)) < 1.0e-12


def test_common_grid_interpolation():
    source_x = [0.2, 0.6, 1.0]
    source_y = [2.0, 6.0, 10.0]
    target_x = [0.2, 0.4, 0.6, 0.8, 1.0]

    values = interpolate_series(source_x, source_y, target_x)
    grid, identical = common_grid_for([0.2, 0.6, 1.0], [0.2, 0.4, 1.0])

    assert values == [2.0, 4.0, 6.0, 8.0, 10.0]
    assert not identical
    assert grid == [0.2, 0.4, 0.6, 1.0]


def test_cumulative_integral_reaches_one():
    cumulative = cumulative_fraction([1.0, 2.0, 1.0])
    assert cumulative[-1] == 1.0
    assert cumulative[0] == 0.25


def test_tip_loading_metric_positive_for_known_synthetic_gmt_case():
    mu = [0.25, 0.5, 0.75, 0.95]
    mil_dthrust = [4.0, 4.0, 4.0, 8.0]
    gmt_dthrust = [5.0, 6.0, 5.0, 4.0]

    mil_tip = integrate_annular_band(mu, mil_dthrust, 0.85, 1.0, hub_mu=0.2)
    gmt_tip = integrate_annular_band(mu, gmt_dthrust, 0.85, 1.0, hub_mu=0.2)
    reduction = loading_reduction_pct(mu, mil_dthrust, gmt_dthrust, 0.85, 1.0, hub_mu=0.2)

    assert gmt_tip < mil_tip
    assert reduction > 0.0


def workbook_scaled_rows() -> list[dict[str, object]]:
    return reference_workbook_scale_rows(WORKBOOK, "scaled_reference_mil_22") + reference_workbook_scale_rows(
        WORKBOOK, "scaled_reference_gmt_22"
    )


def test_workbook_aliases_load_scaled_18_inch_geometry_as_22_inch_case():
    gmt_alias = read_geometry_xlsx(WORKBOOK, "reference_gmt")
    gmt_canonical = read_geometry_xlsx(WORKBOOK, "gmt_22")
    mil_alias = read_geometry_xlsx(WORKBOOK, "reference_mil")
    mil_canonical = read_geometry_xlsx(WORKBOOK, "mil_22")

    assert [station.mu for station in gmt_alias] == [station.mu for station in gmt_canonical]
    assert [station.twist_deg for station in mil_alias] == [station.twist_deg for station in mil_canonical]

    first_radius_in = meters_to_inches(gmt_alias[0].mu * diameter_in_to_radius_m(22.0))
    assert abs(first_radius_in - 2.2) < 1.0e-12
    assert abs(first_radius_in - 1.8) > 0.1


def test_scaled_reference_preserves_r_R():
    rows = workbook_scaled_rows()

    assert not validate_scaled_reference_rows(rows)
    assert all(abs(float(row["r_R_scaled"]) - float(row["r_R_original"])) <= 1.0e-12 for row in rows)


def test_scaled_reference_preserves_c_R():
    rows = workbook_scaled_rows()

    assert not validate_scaled_reference_rows(rows)
    assert all(abs(float(row["c_R_scaled"]) - float(row["c_R_original"])) <= 1.0e-12 for row in rows)


def test_scaled_reference_preserves_twist():
    rows = workbook_scaled_rows()

    assert not validate_scaled_reference_rows(rows)
    assert all(abs(float(row["twist_scaled_deg"]) - float(row["twist_original_deg"])) <= 1.0e-12 for row in rows)


def test_scaled_reference_preserves_airfoil_schedule():
    rows = workbook_scaled_rows()

    assert not validate_scaled_reference_rows(rows)
    assert all(row["airfoil_scaled"] == row["airfoil_original"] for row in rows)


def test_scaled_reference_radius_is_22in():
    rows = workbook_scaled_rows()
    expected_radius_m = diameter_in_to_radius_m(SCALED_REFERENCE_DIAMETER_IN)

    assert abs(max(float(row["r_22_m"]) for row in rows) - expected_radius_m) <= 1.0e-4
    assert abs(expected_radius_m - THESIS_22_STATIC_OPERATING_POINT["radius_m"]) <= 1.0e-12
    assert SCALED_REFERENCE_SCALE_FACTOR == pytest.approx(SCALED_REFERENCE_DIAMETER_IN / ORIGINAL_REFERENCE_DIAMETER_IN)


def test_scaled_reference_no_double_scaling():
    rows = workbook_scaled_rows()

    assert scaled_reference_no_double_scaling_warnings(rows) == []
    max_radius_m = max(float(row["r_22_m"]) for row in rows)
    assert abs(max_radius_m - diameter_in_to_radius_m(18.0)) > 1.0e-3
    assert abs(max_radius_m - THESIS_22_STATIC_OPERATING_POINT["radius_m"] * SCALED_REFERENCE_SCALE_FACTOR) > 1.0e-3


def test_scaled_reference_mil_gmt_same_chord_airfoil():
    mil_stations, _, _ = load_reference_geometry(None, WORKBOOK, "scaled_reference_mil_22")
    gmt_stations, _, _ = load_reference_geometry(None, WORKBOOK, "scaled_reference_gmt_22")

    assert not validate_scaled_reference_pair(mil_stations, gmt_stations, THESIS_22_STATIC_OPERATING_POINT["radius_m"])
    assert [station.mu for station in mil_stations] == [station.mu for station in gmt_stations]
    assert [station.chord_fraction for station in mil_stations] == [station.chord_fraction for station in gmt_stations]
    assert [station.airfoil for station in mil_stations] == [station.airfoil for station in gmt_stations]


def test_scaled_reference_mil_gmt_different_twist():
    mil_stations, _, _ = load_reference_geometry(None, WORKBOOK, "scaled_reference_mil_22")
    gmt_stations, _, _ = load_reference_geometry(None, WORKBOOK, "scaled_reference_gmt_22")

    twist_delta = max(abs(mil.twist_deg - gmt.twist_deg) for mil, gmt in zip(mil_stations, gmt_stations))
    assert twist_delta > 1.0


def test_35N_only_used_for_thesis_22_static_case():
    parser = ArgumentParser()
    add_arguments(parser)
    thesis_args = apply_defaults(parser.parse_args(["--preset", "thesis_gmt_22_static"]))

    assert thesis_args.thrust == THESIS_22_STATIC_OPERATING_POINT["target_thrust_n"]
    assert is_thesis_22_static_operating_point(thesis_args_to_config(thesis_args))
    assert NASA_SCITECH_ORIGINAL_OPERATING_POINT["diameter_in"] == ORIGINAL_REFERENCE_DIAMETER_IN
    assert NASA_SCITECH_ORIGINAL_OPERATING_POINT["rpm"] == 3000.0
    assert NASA_SCITECH_ORIGINAL_OPERATING_POINT["freestream_mps"] == 9.144
    assert NASA_SCITECH_ORIGINAL_OPERATING_POINT["target_thrust_n"] != 35.0


def thesis_args_to_config(args):
    return SimpleNamespace(
        radius_m=diameter_in_to_radius_m(args.diameter_in),
        rpm=args.rpm,
        freestream_mps=args.v_inf,
        thrust_target_n=args.thrust,
    )


def test_scaled_reference_geometry_audit_outputs(tmp_path):
    csv_path, md_path, report_path = write_scaled_reference_geometry_audit(
        tmp_path / "reference",
        WORKBOOK,
        tmp_path / "optimized",
    )

    assert csv_path.exists()
    assert md_path.exists()
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert "PASS/FAIL summary: PASS" in report
    assert "NASA/SciTech 18-inch geometries scaled to 22 inches" in report
    assert "35 N is only the thesis 22-inch static target" in report


def test_thesis_static_preset_uses_22_inch_defaults():
    parser = ArgumentParser()
    add_arguments(parser)
    args = apply_defaults(parser.parse_args(["--preset", "thesis_gmt_22_static"]))

    assert args.diameter_in == 22.0
    assert args.blades == 2
    assert args.rpm == 4000.0
    assert args.v_inf == 0.0
    assert args.thrust == 35.0
    assert args.mode == "reference_regularized"
    assert args.lock_chord
    assert args.lock_airfoil
    assert args.optimize_twist_only


def test_reference_aliases_share_chord_distribution():
    gmt_stations, _, _ = load_reference_geometry(None, WORKBOOK, "reference_gmt")
    mil_stations, _, _ = load_reference_geometry(None, WORKBOOK, "reference_mil")

    assert [station.mu for station in gmt_stations] == [station.mu for station in mil_stations]
    assert [station.chord_fraction for station in gmt_stations] == [station.chord_fraction for station in mil_stations]


@pytest.fixture()
def same_geometry_verification(tmp_path):
    parser = build_arg_parser()
    args = parser.parse_args(
        [
            "--preset",
            "thesis_gmt_22_static",
            "--comparison-geometry",
            "same",
            "--shared-reference-variant",
            "reference_gmt",
            "--max-iterations",
            "0",
            "--skip-plots",
            "--write-reference-cases",
            "--results-dir",
            str(tmp_path / "results" / "geometry" / "comparison"),
            "--figures-dir",
            str(tmp_path / "figures_paper" / "comparison"),
        ]
    )
    return run_verification(apply_verification_preset(args))


def test_locked_comparison_preserves_chord_and_airfoil_schedule(same_geometry_verification):
    verification = same_geometry_verification

    assert verification.metrics.max_chord_diff_m <= 1.0e-12
    assert verification.mil_span.airfoils == verification.gmt_span.airfoils


def test_hover_inflow_angle_uses_induced_velocity(same_geometry_verification):
    verification = same_geometry_verification

    assert verification.mil.result.config.freestream_mps == 0.0
    assert verification.mil.result.induced_velocity_mps > 0.0
    assert verification.gmt.result.induced_velocity_mps > 0.0
    assert verification.mil.result.stations[0].phi_deg > 0.0
    assert verification.gmt.result.stations[0].phi_deg > 0.0


def test_22_inch_verification_has_deterministic_outcome(same_geometry_verification):
    assert same_geometry_verification.outcome in VERIFICATION_OUTCOMES


def test_optional_reference_case_outputs_are_written(same_geometry_verification):
    reference_root = same_geometry_verification.results_dir.parent / "reference"

    assert (reference_root / "reference_mil" / "reference_mil.csv").exists()
    assert (reference_root / "reference_gmt" / "reference_gmt.csv").exists()
