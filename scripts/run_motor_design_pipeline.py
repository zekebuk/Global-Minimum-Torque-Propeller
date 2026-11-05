"""Run the RL-driven BLDC motor design pipeline (equivalent to `bldc-motor design run`).

Example:
    python scripts/run_motor_design_pipeline.py --mission 5in_freestyle_6s --rl-steps 150000
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bldc_motor.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["design", "run", *sys.argv[1:]]))
