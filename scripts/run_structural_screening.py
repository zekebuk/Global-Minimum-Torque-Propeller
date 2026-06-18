"""Report available structural screening artifacts for the thesis workflow."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prandtl_prop import config, structural


def main() -> int:
    """Write a clean structural screening report."""

    report = structural.write_structural_report(config.OUTPUT_REPORTS_DIR / "structural_screening_report.md")
    artifacts = structural.discover_structural_artifacts()
    print("Structural screening report complete")
    print(f"  artifacts found: {len(artifacts)}")
    print(f"  {report.relative_to(config.PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
