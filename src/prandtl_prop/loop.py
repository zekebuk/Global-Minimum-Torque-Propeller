"""Close the design loop: BEMT trim -> CAD -> CFD -> calibrate BEMT -> re-trim, until CFD meets the target.

Each outer iteration k:

1. Runs aero (BEMT trimmed to target thrust, using the corrections from iteration k-1, if any),
   sections, cad, and cfd. All are cached, so repeating a loop with identical inputs is free.
2. Calibrates per-station lift and drag factors so BEMT, at exactly the geometry the CFD saw,
   reproduces the CFD station loads (``prandtl_prop.cfd.calibration``).
3. Writes those factors as the corrections file for iteration k+1.

The loop converges when the CFD thrust is within ``thrust_tolerance`` of the target and the trim
offset stops changing. At that point BEMT, CAD, and CFD agree on one geometry, and the corrected
BEMT can be used for cheap design studies around it. The factors are fitted at one operating point,
so re-run the loop after large design changes.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from prandtl_prop.aero import BladeStations
from prandtl_prop.cfd.calibration import calibrate, write_corrections
from prandtl_prop.design import load_design, resolve_path
from prandtl_prop.pipeline import _operating_point, _polar, _read_csv, run_design
from prandtl_prop.runs import RunStore


@dataclass(frozen=True)
class LoopResult:
    history: list[dict[str, Any]]
    converged: bool
    corrections_csv: Path | None
    history_csv: Path


def run_cfd_loop(
    design_path: Path,
    *,
    overrides: list[str] | None = None,
    store: RunStore | None = None,
    max_iterations: int = 4,
    thrust_tolerance: float = 0.005,
    offset_tolerance_deg: float = 0.02,
    factor_degree: int | None = 3,
) -> LoopResult:
    store = store or RunStore()
    base_overrides = list(overrides or [])
    corrections: Path | None = None
    history: list[dict[str, Any]] = []
    converged = False
    loop_dir: Path | None = None
    previous_offset: float | None = None
    for iteration in range(max_iterations):
        extra = [f"aero.corrections_csv={corrections}"] if corrections is not None else []
        design = load_design(design_path, base_overrides + extra)
        if loop_dir is None:
            loop_dir = store.root / design.name / "loop"
            loop_dir.mkdir(parents=True, exist_ok=True)
        outcomes = run_design(design, ["cfd"], store=store)
        aero, cfd = outcomes["aero"].run, outcomes["cfd"].run
        rows = _read_csv(cfd.output("cfd_station_loads.csv"))
        cfd_dT = np.array([float(row["cfd_dT_N"]) for row in rows])
        cfd_dQ = np.array([float(row["cfd_dQ_Nm"]) for row in rows])
        op = _operating_point(design)
        stations = BladeStations.from_rows(_read_csv(resolve_path(design.geometry.stations_csv)), op.radius_m)
        offset = float(cfd.metrics["geometry_collective_offset_deg"])
        calibration = calibrate(
            stations, op, _polar(design), cfd_dT, cfd_dQ, collective_offset_deg=offset, tip_loss=design.aero.tip_loss, degree=factor_degree
        )
        next_corrections = write_corrections(
            loop_dir / f"iteration_{iteration + 1}_corrections.csv", stations, op.radius_m, calibration, f"cfd run {cfd.key}"
        )
        target = float(design.operating_point.target_thrust_N)
        thrust_error = (float(cfd.metrics["thrust_N"]) - target) / target
        history.append(
            {
                "iteration": iteration,
                "corrections_csv": str(corrections) if corrections else "",
                "collective_offset_deg": offset,
                "bemt_thrust_N": float(aero.metrics["thrust_N"]),
                "bemt_torque_Nm": float(aero.metrics["torque_Nm"]),
                "cfd_thrust_N": float(cfd.metrics["thrust_N"]),
                "cfd_torque_Nm": float(cfd.metrics["torque_Nm"]),
                "cfd_thrust_error_fraction": thrust_error,
                "cfd_wall_vs_farfield_fraction": float(cfd.metrics["wall_vs_farfield_thrust_difference_fraction"]),
                "mean_lift_factor": float(calibration.lift_factor.mean()),
                "mean_drag_factor": float(calibration.drag_factor.mean()),
                "calibration_station_mismatch": calibration.max_load_mismatch,
                "aero_run": aero.key,
                "cfd_run": cfd.key,
            }
        )
        offset_settled = previous_offset is not None and abs(offset - previous_offset) < offset_tolerance_deg
        if abs(thrust_error) < thrust_tolerance and (offset_settled or corrections is not None):
            converged = True
            break
        previous_offset = offset
        corrections = next_corrections
    assert loop_dir is not None
    history_csv = loop_dir / "loop_history.csv"
    with history_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(history[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(history)
    return LoopResult(history, converged, corrections, history_csv)
