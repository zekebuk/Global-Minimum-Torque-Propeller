"""Regenerate thesis-ready figures from the locked MIL/GMT data."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.helpers import paths, thesis_plotting


def main() -> int:
    """Create the final clean figure set."""

    outputs = thesis_plotting.make_all_thesis_figures()
    print("Thesis figure generation complete")
    print(f"  figures written: {len(outputs)}")
    print(f"  output folder: {paths.relative(paths.OUTPUT_FIGURES_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
