"""Regenerate thesis-ready figures from the locked MIL/GMT data."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prandtl_prop import config, plotting


def main() -> int:
    """Create the final clean figure set."""

    outputs = plotting.make_all_thesis_figures(config.OUTPUT_FIGURES_DIR)
    print("Thesis figure generation complete")
    print(f"  figures written/copied: {len(outputs)}")
    print(f"  output folder: {config.OUTPUT_FIGURES_DIR.relative_to(config.PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
