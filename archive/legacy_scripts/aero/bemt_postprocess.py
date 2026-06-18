from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_final_thesis_solver as core


def evaluate_final_case(stations: list[core.Station], polar_database: dict, geometry_path: Path):
    return core.evaluate_case(
        stations,
        core.OperatingCondition(),
        polar_database,
        use_final_beta=True,
        geometry_path=geometry_path,
    )
