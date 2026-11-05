#!/usr/bin/env python3
"""PF10 frame pipeline: requirements -> optimisation -> CAD -> Onshape -> print plan -> report.

    python run_pipeline.py                 # full run (~5-8 min on 4 cores)
    python run_pipeline.py --fast          # fewer optimiser generations, no material comparison
    python run_pipeline.py --no-cad        # analysis only

The non-arm printed mass is closed against the CAD: run analysis with an estimate, build the CAD,
feed the CAD mass back and repeat until it changes by less than 5 g.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from frame_pipeline.analysis import compare, dump, run_analysis
from frame_pipeline.config import OUTPUT_DIR, load_frame_config, load_materials_config
from frame_pipeline.geometry import derive
from frame_pipeline.materials import load_materials


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
    out = Path(args.out)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    cfg = load_frame_config(args.config)
    mats = load_materials(load_materials_config(args.materials))
    if args.fast:
        cfg["optimization"]["de_maxiter"] = 40
        cfg["optimization"]["closure_iterations"] = 3

    nonarm = None
    cad = None
    for outer in range(args.max_outer):
        print(f"[analysis] outer pass {outer} (non-arm printed mass = {nonarm if nonarm else 'initial estimate'} g)")
        an = run_analysis(cfg, mats, nonarm_g=nonarm, compare_materials=False)
        if args.no_cad:
            break
        from frame_pipeline.cad.assembly import build_assembly
        g = derive(cfg, an)
        print("[cad] building reference geometry")
        cad = build_assembly(g, cfg, mats, out / "cad")
        new = cad["nonarm_mass_g_without_legs"]
        if nonarm is not None and abs(new - nonarm) < 5.0:
            break
        if nonarm is None and abs(new - cfg["optimization"]["nonarm_frame_mass_initial_g"]) < 5.0:
            break
        nonarm = new

    if not args.no_compare and not args.fast:
        print("[analysis] material comparison at the converged state")
        an["material_comparison"] = compare(cfg, mats, an)

    g = derive(cfg, an)
    dump(an, out / "analysis.json")
    with open(out / "frame_geometry.json", "w") as fh:
        json.dump(g.to_dict(), fh, indent=2)

    if not args.no_cad:
        from frame_pipeline.cad.assembly import build_assembly
        from frame_pipeline.cad.render import render_all
        cad = build_assembly(g, cfg, mats, out / "cad")
        print("[render] previews")
        mat_of = {n: mats[cfg["materials_assignment"][k]].name for n, k in
                  {"arm": "arm", "bottom_body": "bottom_body", "top_plate": "top_plate", "jetson_tray": "jetson_tray",
                   "canopy": "canopy", "camera_mount": "camera_mount", "battery_pad": "battery_pad", "gps_mast": "gps_mast"}.items()}
        render_all(cad["_shapes"], mat_of, out / "figures")
        cad.pop("_shapes")

    from frame_pipeline.onshape_export import export_onshape
    from frame_pipeline.print_plan import plan, write
    from frame_pipeline.report import write_report
    print("[onshape] FeatureScript + variables")
    export_onshape(g, an)
    print("[print] X1C print plan")
    write(plan(g, cfg, mats, cad), out)
    print("[report] design report")
    rp = write_report(cfg, mats, an, cad, out, out / "figures")
    print(f"done in {time.time() - t0:.0f} s -> {rp}")


if __name__ == "__main__":
    main()
