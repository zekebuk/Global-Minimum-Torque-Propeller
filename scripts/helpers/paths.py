"""Canonical paths and locked constants for script workflows."""

from __future__ import annotations

import math
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INCH_TO_M = 0.0254
DESIGN_DIAMETER_IN = 22.0
DESIGN_BLADE_COUNT = 2
DESIGN_RPM = 4000.0
TARGET_THRUST_N = 35.0
AIR_DENSITY_KG_M3 = 1.225

DESIGN_DIAMETER_M = DESIGN_DIAMETER_IN * INCH_TO_M
DESIGN_RADIUS_M = DESIGN_DIAMETER_M / 2.0
DESIGN_OMEGA_RAD_S = DESIGN_RPM * 2.0 * math.pi / 60.0
DISK_AREA_M2 = math.pi * DESIGN_RADIUS_M**2

LOCKED_MIL_THRUST_N = 35.00000016969875
LOCKED_GMT_THRUST_N = 34.999994345585726
LOCKED_MIL_TORQUE_NM = 0.859265776418447
LOCKED_GMT_TORQUE_NM = 0.8464030742167892
LOCKED_TORQUE_REDUCTION_PCT = 1.4969410576633924
LOCKED_GMT_TIP_LOADING_REDUCTION_PCT = 10.286824874431757
LOCKED_CENTER_OF_THRUST_SHIFT_RR = 0.018959311812037583

DATA_DIR = PROJECT_ROOT / "data"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
GUI_SCRIPTS_DIR = SCRIPTS_DIR / "gui"
GEOMETRY_DATA_DIR = DATA_DIR / "geometry"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
OUTPUT_FIGURES_DIR = OUTPUTS_DIR / "figures"
OUTPUT_TABLES_DIR = OUTPUTS_DIR / "tables"
OUTPUT_GEOMETRY_DIR = OUTPUTS_DIR / "geometry_exports"
OUTPUT_REPORTS_DIR = OUTPUTS_DIR / "reports"

ARCHIVE_DIR = PROJECT_ROOT / "archive"
LEGACY_SCRIPTS_DIR = ARCHIVE_DIR / "legacy_scripts"
LEGACY_FEA_DIR = LEGACY_SCRIPTS_DIR / "Propeller_3D_Beam_6DOF_FEA"

MIL_GEOMETRY_CSV = GEOMETRY_DATA_DIR / "final_mil_geometry.csv"
GMT_GEOMETRY_CSV = GEOMETRY_DATA_DIR / "final_gmt_geometry.csv"
MIL_STATION_CSV = PROCESSED_DATA_DIR / "mil_bemt_validation_LOCKED.csv"
GMT_STATION_CSV = PROCESSED_DATA_DIR / "gmt_bemt_validation_LOCKED.csv"
LOCKED_SUMMARY_CSV = PROCESSED_DATA_DIR / "locked_aero_summary.csv"
LOCKED_SUMMARY_JSON = PROCESSED_DATA_DIR / "locked_aero_summary.json"
LOCKED_DESIGN_SUMMARY_JSON = PROCESSED_DATA_DIR / "design_summary_LOCKED.json"
LOCKED_COMPARISON_CSV = PROCESSED_DATA_DIR / "locked_aero_comparison.csv"


def relative(path: Path) -> str:
    """Return a display path relative to the repo root when possible."""

    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)
