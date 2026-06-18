from __future__ import annotations

import csv
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
GMT_GEOMETRY = RESULTS / "geometry_optimizer" / "optimized_gmt" / "optimized_gmt_optimizer_geometry.csv"
GMT_SUMMARY = RESULTS / "geometry_optimizer" / "optimized_gmt" / "optimized_gmt_summary.json"
FINAL_EXPORTS = RESULTS / "geometry_optimizer" / "final_exports"
VALIDATION_CSV = RESULTS / "geometry_validation_summary.csv"
GEOMETRY_PLOTS = RESULTS / "plots" / "geometry_framework"
TRACEABILITY_MD = RESULTS / "geometry_framework_meeting_note_traceability.md"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _summary(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _f(row: dict[str, str], name: str) -> float:
    return float(row[name])


def test_gmt_beta_equals_alpha_plus_phi() -> None:
    rows = _rows(GMT_GEOMETRY)

    for row in rows:
        beta = _f(row, "twist_deg")
        alpha = _f(row, "alpha_deg")
        phi = _f(row, "phi_deg")
        assert beta == pytest_approx(alpha + phi, abs=1.0e-3)


def test_final_geometry_exports_have_unique_increasing_mu() -> None:
    for path in FINAL_EXPORTS.glob("final_*_geometry.csv"):
        rows = _rows(path)
        mus = [_f(row, "mu") for row in rows]
        assert len(mus) == len(set(mus)), path.name
        assert all(b > a for a, b in zip(mus, mus[1:])), path.name


def test_cleaned_gmt_tip_loading_is_positive_and_unloaded() -> None:
    rows = _rows(GMT_GEOMETRY)
    cls = [_f(row, "cl") for row in rows]
    dts = [_f(row, "dthrust_n") for row in rows]
    tip_rows = rows[-5:]
    tip_dts = [_f(row, "dthrust_n") for row in tip_rows]

    assert min(cls) >= 0.0
    assert min(dts) >= 0.0
    assert tip_dts[-1] > 0.0
    assert tip_dts[-1] < max(dts)


def test_cleaned_gmt_tip_trends_are_reported() -> None:
    rows = _rows(VALIDATION_CSV)
    gmt = next(row for row in rows if row["case"] == "GMT_cleaned_valid")

    assert float(gmt["min_Cl"]) >= 0.0
    assert float(gmt["min_dT_N"]) >= 0.0
    assert int(gmt["negative_dT_station_count"]) == 0
    assert gmt["tip_Cl_trend"].startswith("positive")
    assert gmt["tip_dT_trend"].startswith("positive")
    assert float(gmt["max_tip_twist_slope_deg_per_mu"]) > 0.0


def test_meeting_note_traceability_and_tip_plots_exist() -> None:
    required = [
        GEOMETRY_PLOTS / "tip_unloading_zoom_cl.png",
        GEOMETRY_PLOTS / "tip_unloading_zoom_cl.svg",
        GEOMETRY_PLOTS / "tip_unloading_zoom_dT.png",
        GEOMETRY_PLOTS / "tip_unloading_zoom_dT.svg",
        GEOMETRY_PLOTS / "loading_smoothness_check.png",
        GEOMETRY_PLOTS / "loading_smoothness_check.svg",
    ]

    for path in required:
        assert path.exists(), path
        assert path.stat().st_size > 0, path

    text = TRACEABILITY_MD.read_text(encoding="utf-8")
    assert "anecdotal" in text.lower()
    assert "Twist is the main geometry mechanism" in text


def test_cleaned_gmt_thrust_power_and_coefficients_match_summary() -> None:
    summary = _summary(GMT_SUMMARY)
    operating = summary["operating_point"]
    thrust = float(summary["actual_thrust_N"])
    torque = float(summary["torque_Nm"])
    power = float(summary["power_W"])
    rpm = float(operating["rpm"])
    rho = float(operating["rho_kg_m3"])
    diameter_m = 2.0 * float(operating["radius_m"])
    rev_per_s = rpm / 60.0
    omega = 2.0 * math.pi * rev_per_s

    assert abs(thrust - float(operating["target_thrust_n"])) <= 0.05
    assert power == pytest_approx(omega * torque, rel=1.0e-9)
    assert float(summary["CT"]) == pytest_approx(thrust / (rho * rev_per_s**2 * diameter_m**4), rel=1.0e-9)
    assert float(summary["CQ"]) == pytest_approx(torque / (rho * rev_per_s**2 * diameter_m**5), rel=1.0e-9)
    assert float(summary["CP"]) == pytest_approx(power / (rho * rev_per_s**3 * diameter_m**5), rel=1.0e-9)


def test_final_gmt_metadata_matches_file_contents() -> None:
    path = FINAL_EXPORTS / "final_gmt_cleaned_valid_geometry.csv"
    metadata_path = FINAL_EXPORTS / "final_gmt_cleaned_valid_geometry.metadata.json"
    rows = _rows(path)
    metadata = _summary(metadata_path)

    assert metadata["case"] == "GMT_cleaned_valid"
    assert metadata["station_count"] == len(rows)
    assert metadata["duplicate_station_handling"]["after_cleanup_duplicate_count"] == 0
    assert metadata["diameter_in"] == pytest_approx(22.0)


def pytest_approx(*args, **kwargs):
    import pytest

    return pytest.approx(*args, **kwargs)
