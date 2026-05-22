import csv
import json
import math
from pathlib import Path

import pytest

import aero_solver
from aero_solver import (
    AirfoilPolar,
    REQUIRED_AIRFOILS,
    REQUIRED_POLAR_REYNOLDS_NUMBERS,
    compute_disk_area,
    compute_hover_induced_velocity,
    compare_cases,
    evaluate_propeller,
    export_results,
    find_missing_xfoil_polar_csvs,
    get_default_aero_config,
    iterate_to_target_thrust,
    load_geometry,
    load_polar,
    parse_xrotor_output,
    rpm_to_omega,
    run_xrotor_comparison,
    validate_aero_config,
)
from airfoil_tools.generate_xfoil_polars import (
    DEFAULT_XFOIL_ROOT,
    expected_alpha_values,
    find_xfoil_executable,
    parse_xfoil_polar,
    rows_for_csv,
)
from airfoil_tools.test_xfoil_headless import run_xfoil_quit_smoke_test
from airfoil_tools.airfoil_polar_loader import load_airfoil_polars


def _write_geometry(path: Path) -> Path:
    rows = [
        {"r_R": 0.20, "r_m": 0.05588, "chord_m": 0.045, "twist_deg": 25.0, "airfoil": "TEST"},
        {"r_R": 0.40, "r_m": 0.11176, "chord_m": 0.050, "twist_deg": 20.0, "airfoil": "TEST"},
        {"r_R": 0.60, "r_m": 0.16764, "chord_m": 0.043, "twist_deg": 16.0, "airfoil": "TEST"},
        {"r_R": 0.80, "r_m": 0.22352, "chord_m": 0.032, "twist_deg": 12.0, "airfoil": "TEST"},
        {"r_R": 1.00, "r_m": 0.27940, "chord_m": 0.018, "twist_deg": 8.0, "airfoil": "TEST"},
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_alias_geometry(path: Path) -> Path:
    rows = [
        {"mu": 0.20, "radius_m": 0.05588, "chord_m": 0.045, "twist_deg": 25.0, "airfoil": "TEST"},
        {"mu": 0.40, "radius_m": 0.11176, "chord_m": 0.050, "twist_deg": 20.0, "airfoil": "TEST"},
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_polar(path: Path) -> Path:
    rows = [
        {"alpha_deg": -20.0, "Cl": -1.2, "Cd": 0.025},
        {"alpha_deg": 0.0, "Cl": 0.0, "Cd": 0.010},
        {"alpha_deg": 20.0, "Cl": 1.2, "Cd": 0.025},
        {"alpha_deg": 40.0, "Cl": 1.4, "Cd": 0.050},
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_airfoil_polar(path: Path) -> Path:
    rows = [
        {"airfoil_name": "ROOT", "alpha_deg": 0.0, "Cl": 0.0, "Cd": 0.010},
        {"airfoil_name": "ROOT", "alpha_deg": 10.0, "Cl": 1.0, "Cd": 0.020},
        {"airfoil_name": "TIP", "alpha_deg": 0.0, "Cl": 0.0, "Cd": 0.012},
        {"airfoil_name": "TIP", "alpha_deg": 10.0, "Cl": 0.5, "Cd": 0.018},
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_re_polar(path: Path) -> Path:
    rows = [
        {"airfoil": "TEST", "Re": 100000.0, "alpha_deg": 0.0, "Cl": 0.0, "Cd": 0.010},
        {"airfoil": "TEST", "Re": 100000.0, "alpha_deg": 10.0, "Cl": 1.0, "Cd": 0.020},
        {"airfoil": "TEST", "Re": 200000.0, "alpha_deg": 0.0, "Cl": 0.0, "Cd": 0.012},
        {"airfoil": "TEST", "Re": 200000.0, "alpha_deg": 10.0, "Cl": 1.2, "Cd": 0.024},
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_required_generated_polars(airfoil_dir: Path) -> None:
    generated = airfoil_dir / "generated_polars"
    generated.mkdir(parents=True)
    for airfoil in REQUIRED_AIRFOILS:
        for reynolds_number in REQUIRED_POLAR_REYNOLDS_NUMBERS:
            polar_csv = generated / f"{airfoil}_Re{reynolds_number}_polar.csv"
            with polar_csv.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["airfoil", "Re", "alpha_deg", "Cl", "Cd", "Cm", "converged"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "airfoil": airfoil,
                        "Re": reynolds_number,
                        "alpha_deg": -10.0,
                        "Cl": -0.8,
                        "Cd": 0.030,
                        "Cm": -0.02,
                        "converged": True,
                    }
                )
                writer.writerow(
                    {
                        "airfoil": airfoil,
                        "Re": reynolds_number,
                        "alpha_deg": 0.0,
                        "Cl": 0.0,
                        "Cd": 0.010,
                        "Cm": -0.02,
                        "converged": True,
                    }
                )
                writer.writerow(
                    {
                        "airfoil": airfoil,
                        "Re": reynolds_number,
                        "alpha_deg": 20.0,
                        "Cl": 1.2,
                        "Cd": 0.035,
                        "Cm": -0.02,
                        "converged": True,
                    }
                )


def _operating_condition(target_thrust=20.0):
    return {
        "case_name": "test_case",
        "diameter_m": 0.5588,
        "radius_m": 0.2794,
        "blade_count": 2,
        "rpm": 4000.0,
        "rho": 1.225,
        "V_inf_m_s": 0.0,
        "target_thrust_N": target_thrust,
        "hover_mode": True,
    }


def test_unit_conversions_and_actuator_disk():
    assert abs(rpm_to_omega(60.0) - 2.0 * math.pi) < 1.0e-12
    area = compute_disk_area(0.5)
    assert abs(area - math.pi * 0.25) < 1.0e-12
    vi = compute_hover_induced_velocity(10.0, 1.225, area)
    assert abs(vi - math.sqrt(10.0 / (2.0 * 1.225 * area))) < 1.0e-12


def test_geometry_loader_accepts_requested_columns_and_aliases(tmp_path):
    requested = load_geometry(_write_geometry(tmp_path / "geometry.csv"))
    aliases = load_geometry(_write_alias_geometry(tmp_path / "alias_geometry.csv"))

    assert requested[0]["r_R"] == pytest.approx(0.20)
    assert requested[0]["dr_m"] > 0.0
    assert aliases[0]["r_R"] == pytest.approx(0.20)
    assert aliases[0]["r_m"] == pytest.approx(0.05588)


def test_polar_loader_interpolates_and_clips_with_warning(tmp_path):
    polar = load_polar(_write_polar(tmp_path / "polar.csv"))

    cl, cd, clipped = polar.evaluate(10.0)
    assert cl == pytest.approx(0.6)
    assert cd == pytest.approx(0.0175)
    assert clipped is False

    with pytest.warns(RuntimeWarning):
        cl, cd, clipped = polar.evaluate(50.0)
    assert cl == pytest.approx(1.4)
    assert clipped is True


def test_airfoil_polar_loader_interpolates_by_airfoil(tmp_path):
    polar = load_polar(_write_airfoil_polar(tmp_path / "multi_polar.csv"))

    root_cl, root_cd, root_clipped = polar.evaluate(5.0, "ROOT")
    tip_cl, tip_cd, tip_clipped = polar.evaluate(5.0, "TIP")

    assert root_cl == pytest.approx(0.5)
    assert root_cd == pytest.approx(0.015)
    assert root_clipped is False
    assert tip_cl == pytest.approx(0.25)
    assert tip_cd == pytest.approx(0.015)
    assert tip_clipped is False


def test_placeholder_polar_clips_debug_cl():
    polar = AirfoilPolar(placeholder=True, cl_max_debug=1.5)

    cl, cd, clipped = polar.evaluate(40.0, "TEST")

    assert cl == pytest.approx(1.5)
    assert cd == pytest.approx(0.010 + 0.010 * 1.5**2)
    assert clipped is True


def test_reynolds_polar_interpolates_by_reynolds_number(tmp_path):
    polar = load_polar(_write_re_polar(tmp_path / "test_re_polar.csv"))

    result = polar.evaluate_with_metadata(5.0, "TEST", 150000.0)

    assert result["Cl"] == pytest.approx(0.55)
    assert result["Cd"] == pytest.approx(0.0165)
    assert result["polar_Re_used"] == "100000-200000"
    assert result["alpha_clipped"] is False


def test_xfoil_executable_discovery_uses_environment_variable(tmp_path, monkeypatch):
    fake_xfoil = tmp_path / "xfoil.exe"
    fake_xfoil.write_text("", encoding="utf-8")
    monkeypatch.setenv("XFOIL_EXE", str(fake_xfoil))

    assert find_xfoil_executable() == fake_xfoil.resolve()


def test_xfoil_executable_discovery_finds_xfoil699_root():
    discovered = find_xfoil_executable(xfoil_root=DEFAULT_XFOIL_ROOT)

    assert discovered is not None
    assert discovered.name.lower() == "xfoil.exe"


def test_xfoil_headless_quit_smoke_test_succeeds_if_xfoil_exists():
    discovered = find_xfoil_executable(xfoil_root=DEFAULT_XFOIL_ROOT)
    if discovered is None:
        pytest.skip("XFOIL6.99 executable is not available in this checkout")

    assert run_xfoil_quit_smoke_test(discovered, timeout_s=30.0) == 0


def test_xfoil_polar_parser_and_convergence_rows(tmp_path):
    raw = tmp_path / "raw_polar.txt"
    raw.write_text(
        "\n".join(
            [
                "alpha CL CD CDp CM Top_Xtr Bot_Xtr",
                "------ ------ ------- ------- ------- ------- -------",
                "-8.000 -0.5000 0.03000 0.02000 0.0100 1.0 1.0",
                "-7.500 -0.4500 0.02800 0.01900 0.0090 1.0 1.0",
                "malformed row",
            ]
        ),
        encoding="utf-8",
    )

    parsed = parse_xfoil_polar(raw)
    rows = rows_for_csv("TEST", 100000, parsed)

    assert parsed[-8.0]["Cl"] == pytest.approx(-0.5)
    assert parsed[-8.0]["Cd"] == pytest.approx(0.03)
    assert parsed[-8.0]["Cm"] == pytest.approx(0.01)
    assert len(expected_alpha_values()) == 49
    assert len(rows) == 2
    assert rows[0]["converged"] is True
    assert rows[-1]["converged"] is True


def test_airfoil_polar_loader_reads_generated_polars_with_cm_and_convergence(tmp_path):
    generated = tmp_path / "generated_polars"
    generated.mkdir()
    polar_csv = generated / "MH115_Re100000_polar.csv"
    with polar_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["airfoil", "Re", "alpha_deg", "Cl", "Cd", "Cm", "converged"])
        writer.writeheader()
        writer.writerow(
            {"airfoil": "mh115", "Re": 100000, "alpha_deg": 0.0, "Cl": 0.1, "Cd": 0.01, "Cm": -0.02, "converged": True}
        )
        writer.writerow(
            {"airfoil": "mh115", "Re": 100000, "alpha_deg": 0.5, "Cl": "", "Cd": "", "Cm": "", "converged": False}
        )

    polars = load_airfoil_polars(tmp_path)

    assert list(polars) == ["MH115"]
    frame = polars["MH115"]
    assert list(frame.columns) == ["airfoil", "Re", "alpha_deg", "Cl", "Cd", "Cm", "polar_source"]
    assert len(frame) == 1
    assert frame.iloc[0]["Cm"] == pytest.approx(-0.02)


def test_xfoil_polar_availability_check_uses_generated_csvs(tmp_path):
    generated = tmp_path / "generated_polars"
    generated.mkdir()
    polar_csv = generated / "MH115_Re100000_polar.csv"
    with polar_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["airfoil", "Re", "alpha_deg", "Cl", "Cd", "Cm", "converged"])
        writer.writeheader()
        writer.writerow({"airfoil": "MH115", "Re": 100000, "alpha_deg": 0.0, "Cl": 0.0, "Cd": 0.01, "Cm": 0.0, "converged": True})

    assert find_missing_xfoil_polar_csvs(tmp_path, required_airfoils=("MH115",), required_reynolds_numbers=(100000,)) == []
    assert find_missing_xfoil_polar_csvs(tmp_path, required_airfoils=("MH115",), required_reynolds_numbers=(150000,)) == [
        "MH115 Re=150000"
    ]


def test_xrotor_output_parser_extracts_dimensional_and_coefficients():
    sample_stdout = """
 thrust(N)  :   33.6        power(W)   :   307.       torque(N-m):  0.733
 Efficiency :  0.0109       speed(m/s) :    0.100     rpm        :   3999.999
   Ct:    0.06328       Cp:    0.01552  (by rho,Diam,N)
"""

    metrics = parse_xrotor_output(sample_stdout, _operating_condition(35.0))

    assert metrics["thrust_N"] == pytest.approx(33.6)
    assert metrics["torque_Nm"] == pytest.approx(0.733)
    assert metrics["power_W"] == pytest.approx(307.0)
    assert metrics["efficiency"] == pytest.approx(0.0109)
    assert metrics["CT"] == pytest.approx(33.6 / (1.225 * (4000.0 / 60.0) ** 2 * 0.5588**4))
    assert metrics["CQ"] == pytest.approx(0.733 / (1.225 * (4000.0 / 60.0) ** 2 * 0.5588**5))
    assert metrics["CP"] == pytest.approx(307.0 / (1.225 * (4000.0 / 60.0) ** 3 * 0.5588**5))


def test_aero_config_validation_catches_missing_geometry_missing_polars_and_invalid_rpm(tmp_path):
    config = get_default_aero_config()
    config.update(
        {
            "geometry_csv_path": str(tmp_path / "missing_geometry.csv"),
            "airfoil_dir": str(tmp_path / "empty_airfoils"),
            "rpm": -1.0,
            "allow_debug_placeholder_polar": False,
        }
    )
    (tmp_path / "empty_airfoils").mkdir()

    errors = validate_aero_config(config, require_polars=True)

    assert any("Geometry CSV not found" in error for error in errors)
    assert any("Missing XFOIL-generated polar CSVs" in error for error in errors)
    assert any("RPM must be >= 0" in error for error in errors)


def test_evaluate_propeller_integrates_loadings_and_coefficients(tmp_path):
    geometry = load_geometry(_write_geometry(tmp_path / "geometry.csv"))
    polar = AirfoilPolar(placeholder=True)
    results = evaluate_propeller(geometry, _operating_condition(), polar)
    summary = results["summary"]
    stations = results["stations"]

    assert results["sanity_checks"]["P_equals_Q_omega"] is True
    assert results["sanity_checks"]["CP_equals_2pi_CQ"] is True
    assert sum(row["dT_N"] for row in stations) == pytest.approx(summary["thrust_N"])
    assert sum(row["qz_N_per_m"] * row["dr_m"] for row in stations) == pytest.approx(summary["thrust_N"])
    assert sum(row["torque_loading_Nm_per_m"] * row["dr_m"] for row in stations) == pytest.approx(summary["torque_Nm"])
    assert stations[-1]["cumulative_thrust_fraction"] == pytest.approx(1.0)
    assert stations[-1]["cumulative_torque_fraction"] == pytest.approx(1.0)
    assert "target_matched" in summary
    assert "valid_for_thesis" in summary


def test_target_thrust_iteration_reports_matched_offset(tmp_path):
    geometry = load_geometry(_write_geometry(tmp_path / "geometry.csv"))
    polar = AirfoilPolar(placeholder=True)
    target = 20.0

    results, offset, error_pct = iterate_to_target_thrust(
        geometry,
        _operating_condition(target),
        polar,
        target_thrust_N=target,
        tolerance_pct=1.0,
    )

    assert abs(error_pct) <= 1.0
    assert results["summary"]["collective_twist_offset_deg"] == pytest.approx(offset)
    assert results["summary"]["thrust_error_pct"] == pytest.approx(error_pct)
    assert results["summary"]["target_matched"] is True


def test_export_results_writes_station_and_summary_files(tmp_path):
    geometry = load_geometry(_write_geometry(tmp_path / "geometry.csv"))
    polar = AirfoilPolar(placeholder=True)
    results = evaluate_propeller(geometry, _operating_condition(), polar)

    written = export_results(results, tmp_path / "out", "test_case")

    assert written["station_csv"].exists()
    assert written["summary_json"].exists()
    assert written["summary_txt"].exists()
    assert "thrust_N" in written["summary_txt"].read_text(encoding="utf-8")
    payload = json.loads(written["summary_json"].read_text(encoding="utf-8"))
    required_summary_flags = (
        "xfoil_polars_used",
        "xfoil_success",
        "xrotor_ran",
        "xrotor_success",
        "polar_is_placeholder",
        "valid_for_thesis",
    )
    for key in required_summary_flags:
        assert key in payload["summary"]
        assert key in payload


def test_xrotor_failure_does_not_invalidate_bemt_result(tmp_path, monkeypatch):
    geometry = load_geometry(_write_geometry(tmp_path / "geometry.csv"))
    polar = AirfoilPolar(placeholder=True)
    results = evaluate_propeller(geometry, _operating_condition(), polar)
    results["summary"]["valid_for_thesis"] = True

    monkeypatch.setattr(aero_solver, "find_xrotor_executable", lambda _root: None)
    run_xrotor_comparison(results, polar, tmp_path)

    assert results["summary"]["xrotor_ran"] is True
    assert results["summary"]["xrotor_success"] is False
    assert results["summary"]["valid_for_thesis"] is True


def test_main_xfoil_failure_writes_invalid_summary(tmp_path, monkeypatch):
    def fail_xfoil(*_args, **_kwargs):
        raise RuntimeError("forced XFOIL failure")

    monkeypatch.setattr(aero_solver, "ensure_xfoil_polars_available", fail_xfoil)

    output_dir = tmp_path / "out"
    rc = aero_solver.main(
        [
            "--case-name",
            "xfoil_fail_case",
            "--airfoil-dir",
            str(tmp_path / "airfoils"),
            "--results-dir",
            str(output_dir),
            "--no-plots",
        ]
    )

    assert rc == 2
    payload = json.loads((output_dir / "xfoil_fail_case_summary.json").read_text(encoding="utf-8"))
    assert payload["summary"]["xfoil_success"] is False
    assert payload["summary"]["xrotor_ran"] is False
    assert payload["summary"]["valid_for_thesis"] is False
    assert "forced XFOIL failure" in payload["summary"]["failure_reason"]


def test_main_ignores_config_xrotor_without_cli_flag(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"run_xrotor": True}), encoding="utf-8")
    output_dir = tmp_path / "out"

    monkeypatch.setattr(aero_solver, "load_geometry", lambda _path: [{"r_R": 1.0, "r_m": 0.2794, "chord_m": 0.02, "twist_deg": 5.0, "airfoil": "TEST", "dr_m": 0.01}])
    monkeypatch.setattr(
        aero_solver,
        "iterate_to_target_thrust",
        lambda *_args, **_kwargs: (
            {
                "case_name": "config_no_xrotor",
                "summary": {"valid_for_thesis": True},
                "sanity_checks": {"no_false_values": True},
                "warnings": [],
            },
            0.0,
            0.0,
        ),
    )
    monkeypatch.setattr(aero_solver, "print_run_summary", lambda _results: None)
    monkeypatch.setattr(
        aero_solver,
        "export_results",
        lambda *_args, **_kwargs: {
            "station_csv": output_dir / "stations.csv",
            "summary_json": output_dir / "summary.json",
            "summary_txt": output_dir / "summary.txt",
        },
    )

    xrotor_called = False

    def fake_xrotor(*_args, **_kwargs):
        nonlocal xrotor_called
        xrotor_called = True

    monkeypatch.setattr(aero_solver, "run_xrotor_comparison", fake_xrotor)

    rc = aero_solver.main(
        [
            "--config",
            str(config_path),
            "--debug-mode",
            "--case-name",
            "config_no_xrotor",
            "--results-dir",
            str(output_dir),
            "--no-plots",
        ]
    )

    assert rc == 0
    assert xrotor_called is False


def test_compare_cases_returns_requested_metrics(tmp_path):
    geometry = load_geometry(_write_geometry(tmp_path / "geometry.csv"))
    polar = AirfoilPolar(placeholder=True)
    mil = evaluate_propeller(geometry, _operating_condition(), polar, case_name="mil")
    gmt = evaluate_propeller(
        geometry,
        _operating_condition(),
        polar,
        collective_twist_offset_deg=-1.0,
        case_name="gmt",
    )

    metrics = compare_cases(mil, gmt)

    assert "thrust_match_error_pct" in metrics
    assert "torque_reduction_pct" in metrics
    assert "mil_tip_thrust_fraction" in metrics
    assert "gmt_cumulative_50_torque_r_R" in metrics
    assert math.isfinite(metrics["mil_max_Cl"])


def test_thesis_22_static_35n_regression_matches_target():
    geometry_path = Path("results/geometry/optimized/optimized_mil/optimized_mil.csv")
    geometry = load_geometry(geometry_path)
    polar = AirfoilPolar(placeholder=True, cl_max_debug=1.5)

    results, _, error_pct = iterate_to_target_thrust(
        geometry,
        {
            "case_name": "thesis_22_static_35N",
            "diameter_m": 0.5588,
            "radius_m": 0.2794,
            "blade_count": 2,
            "rpm": 4000.0,
            "rho": 1.225,
            "V_inf_m_s": 0.0,
            "target_thrust_N": 35.0,
            "hover_mode": True,
        },
        polar,
        target_thrust_N=35.0,
        tolerance_pct=1.0,
    )

    assert abs(error_pct) < 5.0
    assert results["summary"]["target_matched"] is True
    assert results["sanity_checks"]["P_equals_Q_omega"] is True
    assert results["sanity_checks"]["CP_equals_2pi_CQ"] is True
    assert results["sanity_checks"]["no_nan_or_inf"] is True
