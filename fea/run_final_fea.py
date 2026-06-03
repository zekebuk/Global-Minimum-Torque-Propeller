from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FINAL_GEOMETRY_DIR = ROOT / "results" / "final_geometry"
LEGACY_FEA_DIR = ROOT / "Code" / "Propeller_3D_Beam_6DOF_FEA"


def main() -> int:
    print("Final FEA wrapper")
    print(f"  final controlled geometry folder: {FINAL_GEOMETRY_DIR}")
    print(f"  legacy MATLAB FEA implementation: {LEGACY_FEA_DIR}")
    print("  Run MATLAB FEA from the legacy folder until final load mapping is explicitly updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
