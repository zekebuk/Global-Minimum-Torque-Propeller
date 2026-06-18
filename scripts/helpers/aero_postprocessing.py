"""BEMT aero-postprocessing helpers for the final thesis scripts."""

from __future__ import annotations

from pathlib import Path

from scripts.helpers import csv_io, metrics, paths

StationRow = dict[str, str]


def load_station_data(case_label: str) -> list[StationRow]:
    """Load locked station-level BEMT data for `MIL` or `GMT`."""

    case = case_label.strip().upper()
    if case == "MIL":
        return csv_io.read_csv_rows(paths.MIL_STATION_CSV)
    if case == "GMT":
        return csv_io.read_csv_rows(paths.GMT_STATION_CSV)
    raise ValueError(f"Unsupported BEMT case: {case_label}")


def column_values(rows: list[StationRow], column: str) -> list[float]:
    """Return one numeric station column."""

    return [float(row[column]) for row in rows]


def total_station_load(rows: list[StationRow], column: str) -> float:
    """Sum an integrated station-load column such as `dT_N` or `dQ_Nm`."""

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


def write_station_summary(output_path: Path = paths.OUTPUT_TABLES_DIR / "locked_bemt_station_summary.csv") -> Path:
    """Write a compact station-data summary table."""

    rows: list[dict[str, object]] = []
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
    return csv_io.write_csv_rows(output_path, rows, list(rows[0]))


def write_locked_result_table(output_path: Path = paths.OUTPUT_TABLES_DIR / "locked_bemt_summary.csv") -> Path:
    """Write the locked integrated BEMT result table."""

    metrics.assert_locked_summary_matches_constants()
    return metrics.write_locked_result_table(output_path)


def run_aero_postprocessing() -> tuple[Path, Path, float]:
    """Run the final aero-postprocessing workflow."""

    table = write_locked_result_table()
    station_summary = write_station_summary()
    reduction = metrics.torque_reduction_pct(paths.LOCKED_MIL_TORQUE_NM, paths.LOCKED_GMT_TORQUE_NM)
    return table, station_summary, reduction

