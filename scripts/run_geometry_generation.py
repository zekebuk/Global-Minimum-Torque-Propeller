"""Generate and audit the locked MIL/GMT geometry used by the thesis workflow."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from prandtl_prop import config, geometry


def main() -> int:
    """Export locked geometry and write a geometry audit report."""

    exported, audit = geometry.run_geometry_workflow()
    print("Locked geometry export complete")
    for path in exported:
        print(f"  {config.relative(path)}")
    print(f"  {config.relative(audit)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
