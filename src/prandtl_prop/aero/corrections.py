"""Station lift/drag correction files (written by CFD calibration, read by the aero stage)."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from prandtl_prop.aero.bemt import BladeStations


def write_corrections(path: Path, stations: BladeStations, radius_m: float, calibration, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        handle.write(f"# BEMT station corrections calibrated against CFD: {source}\n")
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["r_R", "lift_factor", "drag_factor"])
        for r, lift, drag in zip(stations.r_m / radius_m, calibration.lift_factor, calibration.drag_factor, strict=True):
            writer.writerow([f"{r:.10g}", f"{lift:.10g}", f"{drag:.10g}"])
    return path


def read_corrections(path: Path, r_R: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Read a corrections file and interpolate its factors onto ``r_R`` (clamped at the ends)."""

    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(line for line in handle if not line.startswith("#")))
    if not rows:
        raise ValueError(f"Corrections file has no rows: {path}")
    stations = np.array([float(row["r_R"]) for row in rows])
    order = np.argsort(stations)
    lift = np.array([float(row["lift_factor"]) for row in rows])[order]
    drag = np.array([float(row["drag_factor"]) for row in rows])[order]
    return np.interp(r_R, stations[order], lift), np.interp(r_R, stations[order], drag)
