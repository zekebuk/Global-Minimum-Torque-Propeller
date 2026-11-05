"""Performance metrics for the locked MIL/GMT modeled comparison."""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prandtl_prop import config


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


def power_from_torque(torque_nm: float, omega_rad_s: float = config.DESIGN_OMEGA_RAD_S) -> float:
    """Compute shaft power from torque and angular velocity."""

    return float(torque_nm) * float(omega_rad_s)


def percent_reduction(baseline_value: float, candidate_value: float) -> float:
    """Return the percent reduction from a baseline value to a candidate value."""

    baseline = float(baseline_value)
    if baseline == 0.0:
        raise ValueError("Cannot compute percent reduction from a zero baseline")
    return (baseline - float(candidate_value)) / baseline * 100.0


def torque_reduction_pct(mil_torque_nm: float, gmt_torque_nm: float) -> float:
    """Return GMT torque reduction relative to the MIL torque."""

    return percent_reduction(mil_torque_nm, gmt_torque_nm)


def locked_performance_cases() -> list[PerformanceCase]:
    """Return the locked modeled MIL/GMT result using the central constants."""

    return [
        PerformanceCase(
            case_label="MIL",
            thrust_n=config.LOCKED_MIL_THRUST_N,
            torque_nm=config.LOCKED_MIL_TORQUE_NM,
            power_w=power_from_torque(config.LOCKED_MIL_TORQUE_NM),
            notes="Baseline matched case",
        ),
        PerformanceCase(
            case_label="GMT",
            thrust_n=config.LOCKED_GMT_THRUST_N,
            torque_nm=config.LOCKED_GMT_TORQUE_NM,
            power_w=power_from_torque(config.LOCKED_GMT_TORQUE_NM),
            notes="GMT-inspired matched case",
        ),
    ]


def load_locked_summary_csv(path: Path = config.LOCKED_SUMMARY_CSV) -> list[dict[str, str]]:
    """Load the locked integrated aerodynamic summary CSV."""

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise ValueError(f"Locked summary CSV has no rows: {path}")
    return rows


def load_locked_summary_json(path: Path = config.LOCKED_SUMMARY_JSON) -> dict[str, Any]:
    """Load the locked integrated aerodynamic summary JSON."""

    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def assert_locked_summary_matches_constants(rows: list[dict[str, str]] | None = None) -> None:
    """Check that the mirrored processed summary still matches the locked constants."""

    summary_rows = rows if rows is not None else load_locked_summary_csv()
    by_case = {row["case_label"].upper(): row for row in summary_rows}
    required = {"MIL", "GMT"}
    missing = required - set(by_case)
    if missing:
        raise AssertionError(f"Locked summary is missing cases: {sorted(missing)}")

    checks = [
        ("MIL thrust", float(by_case["MIL"]["total_thrust_N"]), config.LOCKED_MIL_THRUST_N, 2.0e-5),
        ("GMT thrust", float(by_case["GMT"]["total_thrust_N"]), config.LOCKED_GMT_THRUST_N, 2.0e-5),
        ("MIL torque", float(by_case["MIL"]["total_torque_Nm"]), config.LOCKED_MIL_TORQUE_NM, 2.0e-6),
        ("GMT torque", float(by_case["GMT"]["total_torque_Nm"]), config.LOCKED_GMT_TORQUE_NM, 2.0e-6),
    ]
    for label, actual, expected, tolerance in checks:
        if abs(actual - expected) > tolerance:
            raise AssertionError(f"{label} changed: {actual} vs locked {expected}")

    reduction = torque_reduction_pct(
        float(by_case["MIL"]["total_torque_Nm"]),
        float(by_case["GMT"]["total_torque_Nm"]),
    )
    if abs(reduction - config.LOCKED_TORQUE_REDUCTION_PCT) > 2.0e-3:
        raise AssertionError(f"Torque reduction changed: {reduction} vs locked {config.LOCKED_TORQUE_REDUCTION_PCT}")


def write_locked_result_table(output_path: Path) -> Path:
    """Write a compact locked-result table for thesis documentation."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["case", "thrust_N", "torque_Nm", "power_W", "notes"]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
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
