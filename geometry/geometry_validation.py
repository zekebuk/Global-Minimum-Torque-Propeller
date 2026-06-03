from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_final_thesis_solver as core


def lock_chord_and_verify(mil: list[core.Station], gmt: list[core.Station]) -> dict[str, object]:
    return core.lock_chord_and_verify(mil, gmt)


def repair_twist(stations: list[core.Station], op: core.OperatingCondition, polar_database: dict) -> dict[str, object]:
    return core.repair_twist(stations, op, polar_database)
