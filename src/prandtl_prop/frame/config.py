"""Load the YAML configuration (single source of truth)."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

# Frame data (config, Onshape templates, docs, generated outputs) lives in <repo>/drone_frame;
# the code lives here in prandtl_prop.frame.
ROOT = Path(__file__).resolve().parents[3] / "drone_frame"
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "outputs"
INCH = 25.4
G = 9.80665


def load_frame_config(path: Path | str | None = None) -> dict:
    path = Path(path) if path else CONFIG_DIR / "frame.yaml"
    with open(path) as fh:
        cfg = yaml.safe_load(fh)
    cfg["propulsion"]["prop_diameter"] = cfg["propulsion"]["prop_diameter_in"] * INCH
    return cfg


def load_materials_config(path: Path | str | None = None) -> dict:
    path = Path(path) if path else CONFIG_DIR / "materials.yaml"
    with open(path) as fh:
        return yaml.safe_load(fh)


def deep_update(base: dict, override: dict) -> dict:
    """Return a copy of base with override merged in (used for sweeps / tests)."""
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_update(out[k], v)
        else:
            out[k] = v
    return out
