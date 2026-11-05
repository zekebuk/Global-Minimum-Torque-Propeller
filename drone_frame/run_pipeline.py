#!/usr/bin/env python3
"""PF10 frame pipeline: requirements -> optimisation -> CAD -> Onshape -> print plan -> report.

    python drone_frame/run_pipeline.py            # standalone, propulsion placeholders from config/frame.yaml (~6 min)
    python drone_frame/run_pipeline.py --fast     # fewer optimiser generations, no material comparison
    python drone_frame/run_pipeline.py --no-cad   # analysis only
    prandtl run designs/gmt_fpv10_tri.yaml --stages frame   # as a prandtl stage: propulsion from BEMT + vehicle

The non-arm printed mass is closed against the CAD: run analysis with an estimate, build the CAD,
feed the CAD mass back and repeat until it changes by less than 5 g.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # run without installing the package

from prandtl_prop.frame.config import OUTPUT_DIR, load_frame_config, load_materials_config
from prandtl_prop.frame.materials import load_materials
from prandtl_prop.frame.workflow import run_frame


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--materials", default=None)
    ap.add_argument("--out", default=str(OUTPUT_DIR))
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--no-cad", action="store_true")
    ap.add_argument("--no-compare", action="store_true")
    ap.add_argument("--max-outer", type=int, default=3)
    args = ap.parse_args()

    t0 = time.time()
    cfg = load_frame_config(args.config)
    mats = load_materials(load_materials_config(args.materials))
    if args.fast:
        cfg["optimization"]["de_maxiter"] = 40
        cfg["optimization"]["closure_iterations"] = 3
    res = run_frame(cfg, mats, Path(args.out), build_cad=not args.no_cad,
                    compare_materials=not (args.no_compare or args.fast), cad_mass_passes=args.max_outer)
    print(f"done in {time.time() - t0:.0f} s -> {res['report']}")


if __name__ == "__main__":
    main()
