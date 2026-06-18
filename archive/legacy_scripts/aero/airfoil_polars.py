from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import postprocess_final_geometries as final_post


def load_final_polar_database() -> dict:
    return final_post.load_polar_database(final_post.AIRFOIL_DIR)
