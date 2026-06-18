"""Locked performance metrics used by script workflows."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

from scripts.helpers import csv_io, paths


@dataclass(frozen=True)
class PerformanceCase:
    """Integrated performance for one locked propeller case."""

    case_label: str
    thrust_n: float
    torque_nm: float
    power_w: float
    notes: str


def rpm_to_omega_rad_s(rpm: float) -> float:
    """Convert rotational speed from rpm to rad/s."""

    return float(rpm) * 2.0 * math.pi / 60.0


def power_from_torque(torque_nm: float, omega_rad_s: float = paths.DESIGN_OMEGA_RAD_S) -> float:
    """Compute shaft power from torque and angular velocity."""

    return float(torque_nm) * float(omega_rad_s)


def percent_reduction(baseline_value: float, candidate_value: float) -> float:
    """Return the percent reduction from a baseline to candidate value."""

    baseline = float(baseline_value)
    if baseline == 0.0:
        raise ValueError("Cannot compute percent reduction from zero baseline")
    return (baseline - float(candidate_value)) / baseline * 100.0


def torque_reduction_pct(mil_torque_nm: float, gmt_torque_nm: float) -> float:
    """Return GMT torque reduction relative to MIL."""

    return percent_reduction(mil_torque_nm, gmt_torque_nm)


def locked_performance_cases() -> list[PerformanceCase]:
    """Return the locked modeled MIL/GMT result."""

    return [
        PerformanceCase(
            case_label="MIL",
            thrust_n=paths.LOCKED_MIL_THRUST_N,
            torque_nm=paths.LOCKED_MIL_TORQUE_NM,
            power_w=power_from_torque(paths.LOCKED_MIL_TORQUE_NM),
            notes="Baseline matched case",
        ),
        PerformanceCase(
            case_label="GMT",
            thrust_n=paths.LOCKED_GMT_THRUST_N,
            torque_nm=paths.LOCKED_GMT_TORQUE_NM,
            power_w=power_from_torque(paths.LOCKED_GMT_TORQUE_NM),
            notes="GMT-inspired matched case",
        ),
    ]


def assert_locked_summary_matches_constants(rows: list[dict[str, str]] | None = None) -> None:
    """Check that processed summary data still match locked constants."""

    summary_rows = rows if rows is not None else csv_io.read_csv_rows(paths.LOCKED_SUMMARY_CSV)
    by_case = {row["case_label"].upper(): row for row in summary_rows}
    missing = {"MIL", "GMT"} - set(by_case)
    if missing:
        raise AssertionError(f"Locked summary is missing cases: {sorted(missing)}")

    checks = [
        ("MIL thrust", float(by_case["MIL"]["total_thrust_N"]), paths.LOCKED_MIL_THRUST_N, 2.0e-5),
        ("GMT thrust", float(by_case["GMT"]["total_thrust_N"]), paths.LOCKED_GMT_THRUST_N, 2.0e-5),
        ("MIL torque", float(by_case["MIL"]["total_torque_Nm"]), paths.LOCKED_MIL_TORQUE_NM, 2.0e-6),
        ("GMT torque", float(by_case["GMT"]["total_torque_Nm"]), paths.LOCKED_GMT_TORQUE_NM, 2.0e-6),
    ]
    for label, actual, expected, tolerance in checks:
        if abs(actual - expected) > tolerance:
            raise AssertionError(f"{label} changed: {actual} vs locked {expected}")

    reduction = torque_reduction_pct(
        float(by_case["MIL"]["total_torque_Nm"]),
        float(by_case["GMT"]["total_torque_Nm"]),
    )
    if abs(reduction - paths.LOCKED_TORQUE_REDUCTION_PCT) > 2.0e-3:
        raise AssertionError(f"Torque reduction changed: {reduction} vs locked {paths.LOCKED_TORQUE_REDUCTION_PCT}")


def write_locked_result_table(output_path: Path) -> Path:
    """Write a compact locked-result table for thesis documentation."""

    fieldnames = ["case", "thrust_N", "torque_Nm", "power_W", "notes"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for case in locked_performance_cases():
            writer.writerow(
                {
                    "case": case.case_label,
                    "thrust_N": f"{case.thrust_n:.12g}",
                    "torque_Nm": f"{case.torque_nm:.12g}",
                    "power_W": f"{case.power_w:.12g}",
                    "notes": case.notes,
                }
            )
    return output_path

