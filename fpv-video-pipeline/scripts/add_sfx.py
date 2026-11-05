#!/usr/bin/env python3
"""Place the ElevenLabs sound library on a cut list (fpvpipe/sfx.py). Preview and Resolve both read the result.

    python scripts/make_sfx_library.py --bpm 88 --variants 3     # once
    python scripts/add_sfx.py reel.json                          # then per cut list; prints what went where
    python scripts/add_sfx.py reel.json --clear
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, cutlist as cl, sfx, transitions  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cutlist", type=Path)
    ap.add_argument("--library", type=Path, help="default: [paths].sfx")
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--bed", help="location ambience under everything: mountain_wind, beach, river, snow, ...")
    ap.add_argument("--config", type=Path)
    a = ap.parse_args()
    c = cl.CutList.load(a.cutlist)
    if a.clear:
        c.sfx = []
        c.save(a.cutlist)
        print("cleared")
        return 0
    cfg = config.load(a.config)
    root = a.library or Path(cfg["paths"].get("sfx") or cfg["paths"]["music"].parent / "SFX")
    lib = sfx.library(root)
    if not lib:
        raise SystemExit(f"no sounds in {root}: run scripts/make_sfx_library.py first")
    beats = np.array(c.beats_s)
    bar = float(np.median(np.diff(beats))) * 4 if len(beats) > 4 else None
    c.sfx = sfx.plan(c, lib, transitions.analyse(c), bar, a.bed)
    c.save(a.cutlist)
    for s in sorted(c.sfx, key=lambda s: s["at"]):
        print(f"{s['at']:7.2f}s  {s['gain_db']:6.1f} dB  {s['name']}")
    print(f"{len(c.sfx)} sounds on {c.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
