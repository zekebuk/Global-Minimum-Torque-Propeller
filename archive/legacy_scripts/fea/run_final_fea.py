from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FINAL_THESIS_DIR = ROOT / "results" / "FINAL_THESIS_USE_THESE"
FINAL_GEOMETRY_DIR = FINAL_THESIS_DIR / "01_geometry"
FINAL_AERO_DIR = FINAL_THESIS_DIR / "02_aero_validation"
LEGACY_FEA_DIR = ROOT / "Code" / "Propeller_3D_Beam_6DOF_FEA"
MAIN_FEA_SCRIPT = LEGACY_FEA_DIR / "main_propeller_3d_beam_fea.m"


def main() -> int:
    print("Final FEA wrapper")
    print(f"  locked final geometry folder: {FINAL_GEOMETRY_DIR}")
    print(f"  locked final aero folder: {FINAL_AERO_DIR}")
    print(f"  MATLAB FEA implementation: {LEGACY_FEA_DIR}")
    print(f"  main script: {MAIN_FEA_SCRIPT}")
    print("  Run from MATLAB:")
    print(f"    cd {LEGACY_FEA_DIR}")
    print("    main_propeller_3d_beam_fea")
    print("  The main script now reads final_mil_geometry.csv, final_gmt_geometry.csv,")
    print("  mil_bemt_validation_LOCKED.csv, gmt_bemt_validation_LOCKED.csv,")
    print("  and locked_aero_summary.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
