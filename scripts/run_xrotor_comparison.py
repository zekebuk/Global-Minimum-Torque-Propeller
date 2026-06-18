"""Report available XROTOR comparison artifacts for the thesis workflow."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prandtl_prop import config, xrotor_io


def main() -> int:
    """Write a clean XROTOR comparison report."""

    report = xrotor_io.write_xrotor_report(config.OUTPUT_REPORTS_DIR / "xrotor_comparison_report.md")
    artifacts = xrotor_io.discover_xrotor_artifacts()
    print("XROTOR comparison report complete")
    print(f"  artifacts found: {len(artifacts)}")
    print(f"  {report.relative_to(config.PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
