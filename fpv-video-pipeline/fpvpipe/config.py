"""Load config.toml and resolve its paths."""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.toml"


def _resolve(p: str) -> Path | None:
    if not p:
        return None
    path = Path(p).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


def load(path: Path | str | None = None) -> dict:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    with open(cfg_path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["paths"] = {k: _resolve(v) for k, v in cfg.get("paths", {}).items()}
    color = cfg.setdefault("color", {})
    for key in ("conversion_lut", "base_grade_drx"):
        color[key] = _resolve(color.get(key, ""))
    color["luts"] = {k: _resolve(v) for k, v in color.get("luts", {}).items()}
    return cfg
