"""Export and audit the locked MIL/GMT geometry used by the thesis workflow."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prandtl_prop import config, geometry


def main() -> int:
    """Copy locked geometry to outputs and write a geometry audit report."""

    exported = geometry.export_locked_geometry(config.OUTPUT_GEOMETRY_DIR)
    audit = geometry.write_geometry_audit(config.OUTPUT_REPORTS_DIR / "geometry_audit.md")
    print("Locked geometry export complete")
    for path in exported:
        print(f"  {path.relative_to(config.PROJECT_ROOT)}")
    print(f"  {audit.relative_to(config.PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
