"""Helpers for reading and checking locked BEMT postprocessing outputs."""

from __future__ import annotations

import csv
from pathlib import Path

from prandtl_prop import config

StationRow = dict[str, str]


def load_station_data(case_label: str) -> list[StationRow]:
    """Load locked station-level BEMT data for `MIL` or `GMT`."""

    case = case_label.strip().upper()
    if case == "MIL":
        path = config.MIL_STATION_CSV
    elif case == "GMT":
        path = config.GMT_STATION_CSV
    else:
        raise ValueError(f"Unsupported BEMT case: {case_label}")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise ValueError(f"BEMT station CSV has no rows: {path}")
    return rows


def column_values(rows: list[StationRow], column: str) -> list[float]:
    """Return a numeric station column."""

    return [float(row[column]) for row in rows]


def total_station_load(rows: list[StationRow], column: str) -> float:
    """Sum a station-integrated load column such as `dT_N` or `dQ_Nm`."""

    return sum(column_values(rows, column))


def assert_station_data_sane(rows: list[StationRow]) -> None:
    """Check basic BEMT station data quality."""

    thrust = column_values(rows, "dT_N")
    torque = column_values(rows, "dQ_Nm")
    radius = column_values(rows, "r_R")
    chord = column_values(rows, "chord_m")
    twist = column_values(rows, "twist_deg")

    if any(b <= a for a, b in zip(radius, radius[1:])):
        raise AssertionError("BEMT radial stations must be strictly increasing")
    if any(value < -1.0e-8 for value in thrust):
        raise AssertionError("Locked final case has an undocumented negative thrust station")
    if any(value <= 0.0 for value in torque):
        raise AssertionError("Station torque contributions should be positive for the locked final case")
    if any(value <= 0.0 for value in chord):
        raise AssertionError("Station chord values must be positive")
    if any(abs(value) > 90.0 for value in twist):
        raise AssertionError("Station twist values are outside a reasonable engineering range")


def write_station_summary(output_path: Path) -> Path:
    """Write a compact station-data summary table."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in ("MIL", "GMT"):
        station_rows = load_station_data(case)
        assert_station_data_sane(station_rows)
        rows.append(
            {
                "case": case,
                "station_count": len(station_rows),
                "sum_dT_N": total_station_load(station_rows, "dT_N"),
                "sum_dQ_Nm": total_station_load(station_rows, "dQ_Nm"),
                "min_alpha_deg": min(column_values(station_rows, "alpha_deg")),
                "max_alpha_deg": max(column_values(station_rows, "alpha_deg")),
                "min_reynolds": min(column_values(station_rows, "Re")),
                "max_reynolds": max(column_values(station_rows, "Re")),
            }
        )

    fieldnames = list(rows[0])
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})
    return output_path


def _csv_value(value: object) -> object:
    if isinstance(value, float):
        return f"{value:.12g}"
    return value
