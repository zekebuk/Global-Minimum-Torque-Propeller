"""Geometry-generation and audit helpers for the final thesis scripts."""

from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Iterable

from scripts.helpers import csv_io, paths

GeometryRow = dict[str, str]


def load_geometry_csv(path: Path) -> list[GeometryRow]:
    """Load a station-based propeller geometry CSV."""

    return csv_io.read_csv_rows(path)


def load_case_geometry(case_label: str) -> list[GeometryRow]:
    """Load locked geometry for `MIL` or `GMT`."""

    case = case_label.strip().upper()
    if case == "MIL":
        return load_geometry_csv(paths.MIL_GEOMETRY_CSV)
    if case == "GMT":
        return load_geometry_csv(paths.GMT_GEOMETRY_CSV)
    raise ValueError(f"Unsupported geometry case: {case_label}")


def row_value(row: GeometryRow, *candidates: str) -> str:
    """Return the first populated value from a row."""

    for name in candidates:
        if name in row and str(row[name]).strip() != "":
            return row[name]
    raise KeyError(f"None of the columns are present: {candidates}")


def numeric_column(rows: Iterable[GeometryRow], *candidates: str) -> list[float]:
    """Return a numeric column using the first available column name."""

    return [float(row_value(row, *candidates)) for row in rows]


def radial_stations(rows: Iterable[GeometryRow]) -> list[float]:
    """Return nondimensional radial stations."""

    return numeric_column(rows, "mu", "r_R", "r/R")


def chord_values_m(rows: Iterable[GeometryRow]) -> list[float]:
    """Return chord values in meters."""

    return numeric_column(rows, "chord_m")


def twist_values_deg(rows: Iterable[GeometryRow]) -> list[float]:
    """Return twist values in degrees."""

    return numeric_column(rows, "twist_deg", "beta_final_deg")


def validate_geometry(rows: list[GeometryRow]) -> None:
    """Run basic engineering sanity checks on a geometry table."""

    stations = radial_stations(rows)
    chords = chord_values_m(rows)
    twists = twist_values_deg(rows)
    if any(b <= a for a, b in zip(stations, stations[1:])):
        raise AssertionError("Radial stations must be strictly increasing")
    if any(chord <= 0.0 for chord in chords):
        raise AssertionError("Chord values must be positive")
    if any(not math.isfinite(twist) for twist in twists):
        raise AssertionError("Twist values must be finite")
    if min(stations) <= 0.0 or max(stations) > 1.0001:
        raise AssertionError("Radial stations should lie in the open span interval ending at the tip")


def validate_geometry_pair(mil_rows: list[GeometryRow], gmt_rows: list[GeometryRow], tolerance: float = 1.0e-10) -> None:
    """Check that MIL and GMT share stations, chord, and airfoil schedule."""

    validate_geometry(mil_rows)
    validate_geometry(gmt_rows)
    if len(mil_rows) != len(gmt_rows):
        raise AssertionError(f"Geometry row mismatch: MIL={len(mil_rows)}, GMT={len(gmt_rows)}")
    for index, (mil_row, gmt_row) in enumerate(zip(mil_rows, gmt_rows), start=1):
        for column in ("mu", "radius_m", "chord_m"):
            delta = abs(float(row_value(mil_row, column)) - float(row_value(gmt_row, column)))
            if delta > tolerance:
                raise AssertionError(f"Station {index} {column} differs between MIL and GMT by {delta}")
        if row_value(mil_row, "airfoil").strip().upper() != row_value(gmt_row, "airfoil").strip().upper():
            raise AssertionError(f"Station {index} airfoil differs between MIL and GMT")


def export_locked_geometry(output_dir: Path = paths.OUTPUT_GEOMETRY_DIR) -> list[Path]:
    """Export the locked generated geometry into `outputs/geometry_exports/`."""

    mil_rows = load_case_geometry("MIL")
    gmt_rows = load_case_geometry("GMT")
    validate_geometry_pair(mil_rows, gmt_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = [
        output_dir / "final_mil_geometry.csv",
        output_dir / "final_gmt_geometry.csv",
    ]
    shutil.copy2(paths.MIL_GEOMETRY_CSV, outputs[0])
    shutil.copy2(paths.GMT_GEOMETRY_CSV, outputs[1])
    return outputs


def write_geometry_audit(output_path: Path = paths.OUTPUT_REPORTS_DIR / "geometry_audit.md") -> Path:
    """Write a compact geometry audit report."""

    mil_rows = load_case_geometry("MIL")
    gmt_rows = load_case_geometry("GMT")
    validate_geometry_pair(mil_rows, gmt_rows)
    mil_twist = twist_values_deg(mil_rows)
    gmt_twist = twist_values_deg(gmt_rows)
    lines = [
        "# Geometry Audit",
        "",
        f"Station count: {len(mil_rows)}",
        f"Diameter: {paths.DESIGN_DIAMETER_IN:.1f} in",
        f"Blade count: {paths.DESIGN_BLADE_COUNT}",
        "Radial stations, chord distribution, and airfoil schedule match between MIL and GMT.",
        f"MIL twist range: {min(mil_twist):.3f} to {max(mil_twist):.3f} deg",
        f"GMT twist range: {min(gmt_twist):.3f} to {max(gmt_twist):.3f} deg",
        "",
    ]
    return csv_io.write_text(output_path, "\n".join(lines))


def run_geometry_workflow() -> tuple[list[Path], Path]:
    """Run the readable geometry-generation workflow."""

    exported = export_locked_geometry()
    audit = write_geometry_audit()
    return exported, audit

