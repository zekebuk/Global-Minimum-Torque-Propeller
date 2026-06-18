from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GEOMETRY_CODE_DIR = Path(__file__).resolve().parent
if str(GEOMETRY_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(GEOMETRY_CODE_DIR))

import operating_point_inverse_design


def main() -> int:
    bundle = operating_point_inverse_design.run_design(write_outputs=True, make_plots=True)
    print("Final operating-point geometry complete")
    print(f"  output folder: {operating_point_inverse_design.GEOMETRY_DIR}")
    print(f"  aero validation folder: {operating_point_inverse_design.AERO_DIR}")
    print(f"  diagnostics folder: {operating_point_inverse_design.DIAGNOSTIC_DIR}")
    print(f"  report: {operating_point_inverse_design.REPORT_PATH}")
    print(f"  MIL thrust: {bundle.mil.summary['total_thrust_N']:.6f} N")
    print(f"  GMT thrust: {bundle.gmt.summary['total_thrust_N']:.6f} N")
    print(f"  MIL torque: {bundle.mil.summary['total_torque_Nm']:.6f} N*m")
    print(f"  GMT torque: {bundle.gmt.summary['total_torque_Nm']:.6f} N*m")
    print(f"  GMT torque reduction: {bundle.summary['gmt_torque_reduction_pct']:.3f}%")
    for warning in bundle.summary["warnings"]:
        print(f"  WARNING: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
