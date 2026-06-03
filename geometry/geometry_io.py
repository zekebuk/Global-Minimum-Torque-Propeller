from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_final_thesis_solver as core


def read_frozen_geometry(path: Path, case_label: str) -> list[core.Station]:
    return core.read_frozen_geometry(path, case_label)


def write_controlled_geometry(path: Path, stations: list[core.Station], case_name: str) -> None:
    core.write_controlled_geometry(path, stations, case_name)
