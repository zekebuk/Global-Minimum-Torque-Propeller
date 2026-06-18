"""Postprocess the locked BEMT aero results for the thesis workflow."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.helpers import aero_postprocessing, paths


def main() -> int:
    """Validate and write final aero summary outputs."""

    table, station_summary, reduction = aero_postprocessing.run_aero_postprocessing()
    print("Locked aero postprocessing complete")
    print(f"  torque reduction: {reduction:.6f}%")
    print(f"  {paths.relative(table)}")
    print(f"  {paths.relative(station_summary)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
