"""Regenerate compact locked BEMT comparison tables."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prandtl_prop import bemt, config, metrics


def main() -> int:
    """Validate and write BEMT summary outputs."""

    metrics.assert_locked_summary_matches_constants()
    table = metrics.write_locked_result_table(config.OUTPUT_TABLES_DIR / "locked_bemt_summary.csv")
    station_summary = bemt.write_station_summary(config.OUTPUT_TABLES_DIR / "locked_bemt_station_summary.csv")
    reduction = metrics.torque_reduction_pct(config.LOCKED_MIL_TORQUE_NM, config.LOCKED_GMT_TORQUE_NM)
    print("Locked BEMT comparison complete")
    print(f"  torque reduction: {reduction:.6f}%")
    print(f"  {table.relative_to(config.PROJECT_ROOT)}")
    print(f"  {station_summary.relative_to(config.PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
