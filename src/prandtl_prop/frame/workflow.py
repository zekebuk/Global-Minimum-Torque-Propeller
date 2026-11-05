"""End-to-end frame run: analysis <-> CAD mass closure, renders, Onshape export, print plan, report.

Shared by the standalone runner (drone_frame/run_pipeline.py) and the prandtl ``frame`` stage.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .analysis import compare, dump, run_analysis
from .geometry import derive


def run_frame(
    cfg: dict[str, Any],
    mats: dict,
    out_dir: Path,
    *,
    build_cad: bool = True,
    compare_materials: bool = True,
    onshape_dir: Path | None = None,
    export_onshape: bool = True,
    cad_mass_passes: int = 3,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    out_dir = Path(out_dir)
    (out_dir / "figures").mkdir(parents=True, exist_ok=True)
    nonarm = None
    cad = None
    an = None
    for outer in range(max(1, cad_mass_passes)):
        log(f"[frame] analysis pass {outer} (non-arm printed mass = {f'{nonarm:.1f}' if nonarm else 'initial estimate'} g)")
        an = run_analysis(cfg, mats, nonarm_g=nonarm, compare_materials=False, log=log)
        if not build_cad:
            break
        from .cad.assembly import build_assembly

        cad = build_assembly(derive(cfg, an), cfg, mats, out_dir / "cad", log=log)
        new = cad["nonarm_mass_g_without_legs"]
        ref = nonarm if nonarm is not None else cfg["optimization"]["nonarm_frame_mass_initial_g"]
        if abs(new - ref) < 5.0:
            break
        nonarm = new

    if compare_materials:
        log("[frame] material comparison at the converged state")
        an["material_comparison"] = compare(cfg, mats, an, log=log)
    an["_cfg_propulsion"] = dict(cfg["propulsion"])
    if "_provenance" in cfg:
        an["provenance"] = cfg["_provenance"]

    g = derive(cfg, an)
    dump(an, out_dir / "analysis.json")
    (out_dir / "frame_geometry.json").write_text(json.dumps(g.to_dict(), indent=2))

    if build_cad:
        from .cad.assembly import build_assembly
        from .cad.render import render_all

        cad = build_assembly(g, cfg, mats, out_dir / "cad", log=log)
        assign = cfg["materials_assignment"]
        keys = {"arm": "arm", "bottom_body": "bottom_body", "top_plate": "top_plate", "jetson_tray": "jetson_tray",
                "canopy": "canopy", "camera_mount": "camera_mount", "battery_pad": "battery_pad", "gps_mast": "gps_mast"}
        render_all(cad["_shapes"], {n: mats[assign[k]].name for n, k in keys.items()}, out_dir / "figures")
        cad.pop("_shapes")

    from .print_plan import plan, write
    from .report import write_report

    if export_onshape:
        from .onshape_export import export_onshape as _export

        _export(g, an, out_dir=onshape_dir)
    write(plan(g, cfg, mats, cad), out_dir)
    report = write_report(cfg, mats, an, cad, out_dir, out_dir / "figures")
    return {"analysis": an, "cad": cad, "geometry": g, "report": report}
