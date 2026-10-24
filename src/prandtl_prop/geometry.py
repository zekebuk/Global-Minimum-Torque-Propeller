"""CSV loaders and sanity checks for station-based propeller geometry."""

from __future__ import annotations

import csv
import math
import shutil
from collections.abc import Iterable
from pathlib import Path

from prandtl_prop import config

GeometryRow = dict[str, str]


def load_geometry_csv(path: Path) -> list[GeometryRow]:
    """Load a station-based geometry CSV."""

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise ValueError(f"Geometry CSV has no rows: {path}")
    return rows


def load_case_geometry(case_label: str) -> list[GeometryRow]:
    """Load the locked geometry for `MIL` or `GMT`."""

    case = case_label.strip().upper()
    if case == "MIL":
        return load_geometry_csv(config.MIL_GEOMETRY_CSV)
    if case == "GMT":
        return load_geometry_csv(config.GMT_GEOMETRY_CSV)
    raise ValueError(f"Unsupported geometry case: {case_label}")


def numeric_column(rows: Iterable[GeometryRow], *candidates: str) -> list[float]:
    """Return a numeric column using the first available candidate column name."""

    output: list[float] = []
    for row in rows:
        value = row_value(row, *candidates)
        output.append(float(value))
    return output


def row_value(row: GeometryRow, *candidates: str) -> str:
    """Return the first available value from a row."""

    for name in candidates:
        if name in row and str(row[name]).strip() != "":
            return row[name]
    raise KeyError(f"None of the columns are present: {candidates}")


def radial_stations(rows: Iterable[GeometryRow]) -> list[float]:
    """Return nondimensional radial stations."""

    return numeric_column(rows, "mu", "r_R", "r/R")


def chord_values_m(rows: Iterable[GeometryRow]) -> list[float]:
    """Return chord values in meters."""

    return numeric_column(rows, "chord_m")


def twist_values_deg(rows: Iterable[GeometryRow]) -> list[float]:
    """Return final twist values in degrees."""

    return numeric_column(rows, "twist_deg", "beta_final_deg")


def airfoil_labels(rows: Iterable[GeometryRow]) -> list[str]:
    """Return station airfoil labels."""

    return [row_value(row, "airfoil").strip().upper() for row in rows]


def validate_geometry(rows: list[GeometryRow]) -> None:
    """Run basic engineering sanity checks on a geometry table."""

    stations = radial_stations(rows)
    chords = chord_values_m(rows)
    twists = twist_values_deg(rows)

    if any(b <= a for a, b in zip(stations, stations[1:], strict=False)):
        raise AssertionError("Radial stations must be strictly increasing")
    if any(chord <= 0.0 for chord in chords):
        raise AssertionError("Chord values must be positive")
    if any(not math.isfinite(twist) for twist in twists):
        raise AssertionError("Twist values must be finite")
    if min(stations) <= 0.0 or max(stations) > 1.0001:
        raise AssertionError("Radial stations must lie in (0, 1] (tolerance 1e-4 at the tip)")


def validate_geometry_pair(mil_rows: list[GeometryRow], gmt_rows: list[GeometryRow], tolerance: float = 1.0e-10) -> None:
    """Check that MIL and GMT share stations, chord, and airfoil schedule."""

    validate_geometry(mil_rows)
    validate_geometry(gmt_rows)
    if len(mil_rows) != len(gmt_rows):
        raise AssertionError(f"Geometry row mismatch: MIL={len(mil_rows)}, GMT={len(gmt_rows)}")

    for column, values in (("radial station", radial_stations), ("chord", chord_values_m)):
        for index, (a, b) in enumerate(zip(values(mil_rows), values(gmt_rows), strict=True), start=1):
            if abs(a - b) > tolerance:
                raise AssertionError(f"Station {index} {column} differs between MIL and GMT by {abs(a - b)}")
    for index, (mil_row, gmt_row) in enumerate(zip(mil_rows, gmt_rows, strict=True), start=1):
        if row_value(mil_row, "airfoil").strip().upper() != row_value(gmt_row, "airfoil").strip().upper():
            raise AssertionError(f"Station {index} airfoil differs between MIL and GMT")


def export_locked_geometry(output_dir: Path = config.OUTPUT_GEOMETRY_DIR) -> list[Path]:
    """Validate the locked geometry pair, then copy it to the clean output geometry folder."""

    validate_geometry_pair(load_case_geometry("MIL"), load_case_geometry("GMT"))
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = [
        output_dir / "final_mil_geometry.csv",
        output_dir / "final_gmt_geometry.csv",
    ]
    shutil.copy2(config.MIL_GEOMETRY_CSV, outputs[0])
    shutil.copy2(config.GMT_GEOMETRY_CSV, outputs[1])
    return outputs


def write_geometry_audit(output_path: Path = config.OUTPUT_REPORTS_DIR / "geometry_audit.md") -> Path:
    """Write a compact geometry audit report."""

    mil_rows = load_case_geometry("MIL")
    gmt_rows = load_case_geometry("GMT")
    validate_geometry_pair(mil_rows, gmt_rows)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    mil_twist = twist_values_deg(mil_rows)
    gmt_twist = twist_values_deg(gmt_rows)
    lines = [
        "# Geometry Audit",
        "",
        f"Station count: {len(mil_rows)}",
        f"Diameter: {config.DESIGN_DIAMETER_IN:.1f} in",
        f"Blade count: {config.DESIGN_BLADE_COUNT}",
        "Radial stations, chord distribution, and airfoil schedule match between MIL and GMT.",
        f"MIL twist range: {min(mil_twist):.3f} to {max(mil_twist):.3f} deg",
        f"GMT twist range: {min(gmt_twist):.3f} to {max(gmt_twist):.3f} deg",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def run_geometry_workflow() -> tuple[list[Path], Path]:
    """Export the validated locked geometry and write the geometry audit."""

    return export_locked_geometry(), write_geometry_audit()
