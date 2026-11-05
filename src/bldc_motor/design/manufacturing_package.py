"""Build the complete manufacturing data package for a design.

design.json -> analytical model -> mechanical design (fits, stack-up, clearance checks)
            -> STEP (each part + assembly), lamination DXF
            -> dimensioned drawing set (SVG sheets + combined PDF)
            -> CAD mass audit (CAD vs analytical model) and re-scored mission performance
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from bldc_motor.design import export, mechanical, mission, model

LEAD_MASS_G_PER_MM = 0.518 * 8.96e-3 + 0.35 / 150.0  # 20 AWG copper + silicone jacket
SMALL_PARTS_G = 0.3  # e-clip, grub, shim
BEARING_MASS_G = mechanical.BEARINGS["684ZZ"]["mass_g"]


def load_design(path: str | Path) -> tuple[dict, dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return export.design_from_json(payload.get("design", payload)), payload


def mass_audit(m, analysis, masses: dict) -> dict:
    i = analysis.internals
    rows = {
        "stator core (CAD x stacking 0.95)": (masses["P-002_stator_stack"] * 0.95, i["mass_stator_iron_g"]),
        "copper winding (model)": (i["mass_copper_g"], i["mass_copper_g"]),
        "magnets x poles": (masses["P-003_magnet"] * m.dims["poles"], i["mass_magnets_g"]),
        "back-iron ring": (masses["P-004_back_iron_ring"], i["mass_bell_steel_g"]),
        "bell": (masses["P-005_bell"], i["mass_bell_al_g"]),
        "base + shaft + bearings + small parts": (
            masses["P-006_base"] + masses["P-007_shaft"] + 2 * BEARING_MASS_G + SMALL_PARTS_G,
            i["mass_fixed_g"],
        ),
    }
    cad_total = sum(v[0] for v in rows.values())
    model_total = sum(v[1] for v in rows.values())
    leads = 3 * m.dims["lead_length"] * LEAD_MASS_G_PER_MM
    return {"rows": rows, "cad_total_g": cad_total, "model_total_g": model_total, "leads_g": leads}


def _pdf_with_node(svg_paths: list[Path], pdf: Path) -> Path | None:
    if shutil.which("node") is None:
        return None
    pages = "".join(f'<div class="page">{p.read_text(encoding="utf-8")}</div>' for p in svg_paths)
    html = (
        '<!doctype html><html><head><meta charset="utf-8"><style>@page{size:420mm 297mm;margin:0}body{margin:0}'
        ".page{width:420mm;height:297mm;page-break-after:always;overflow:hidden}.page svg{width:420mm;height:297mm;display:block}"
        f"</style></head><body>{pages}</body></html>"
    )
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "sheets.html"
        html_path.write_text(html, encoding="utf-8")
        script = Path(tmp) / "pdf.js"
        script.write_text(
            "const {chromium}=require('playwright');(async()=>{const b=await chromium.launch();const p=await b.newPage();"
            f"await p.goto('file://{html_path}');await p.pdf({{path:'{pdf}',width:'420mm',height:'297mm',printBackground:true}});"
            "await b.close();})();",
            encoding="utf-8",
        )
        result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            return None
    return pdf


def build_package(
    design_path: str | Path, out_dir: str | Path, name: str = "RL2407", mission_name: str | None = None
) -> dict:
    from bldc_motor.design import cad, drawings  # optional dependency (cadquery)

    design, payload = load_design(design_path)
    out = Path(out_dir)
    for sub in ("step", "drawings", "dxf"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    analysis = model.analyze(design, name=name)
    mech = mechanical.derive(analysis, name)
    parts = cad.build_parts(mech, analysis)
    masses = cad.masses_g(mech, analysis, parts)
    audit = mass_audit(mech, analysis, masses)
    masses["total_g"] = audit["cad_total_g"]

    files: dict[str, str] = {}
    for key, path in cad.export_step(mech, analysis, out / "step").items():
        files[f"step:{key}"] = str(path)
    files["dxf:lamination"] = str(export.write_dxf(analysis, out / "dxf" / "P-001_lamination.dxf"))
    iso = cad.isometric_svg(mech, analysis, out / "drawings" / "isometric.svg")
    sheets = drawings.build_all(mech, analysis, masses, iso.read_text(encoding="utf-8"))
    svgs = drawings.write_sheets(sheets, out / "drawings")
    files.update({f"drawing:{p.stem}": str(p) for p in svgs})
    pdf = _pdf_with_node(svgs, (out / "drawings" / f"{name}_drawing_set.pdf").resolve())
    if pdf:
        files["drawing:pdf"] = str(pdf)
    export.write_winding_table(analysis, out / "winding.csv")

    # Re-score in the aircraft at the true (CAD + leads) mass.
    rescored = {}
    mission_key = mission_name or payload.get("mission")
    if mission_key:
        spec = mission.preset_missions()[mission_key]
        for label, mass in (
            ("analytical model mass", None),
            ("CAD mass", audit["cad_total_g"]),
            ("CAD mass + leads", audit["cad_total_g"] + audit["leads_g"]),
        ):
            ev = mission.evaluate(design, spec, mass_override_g=mass)
            rescored[label] = {
                "mass_g": ev.metrics["mass_g"],
                "score": ev.score,
                "feasible": ev.feasible,
                "auw_g": ev.metrics.get("auw_g"),
                "hover_g_per_w": ev.metrics.get("hover_g_per_w"),
                "thrust_to_weight": ev.metrics.get("thrust_to_weight"),
                "burst_winding_c": ev.metrics.get("burst_winding_c"),
            }
    with (out / "mass_audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["group", "cad_g", "analytical_model_g", "delta_g"])
        for key, (cad_g, model_g) in audit["rows"].items():
            writer.writerow([key, f"{cad_g:.2f}", f"{model_g:.2f}", f"{cad_g - model_g:+.2f}"])
        writer.writerow(
            [
                "TOTAL (without leads)",
                f"{audit['cad_total_g']:.2f}",
                f"{audit['model_total_g']:.2f}",
                f"{audit['cad_total_g'] - audit['model_total_g']:+.2f}",
            ]
        )
        writer.writerow(["leads 3 x 150 mm 20 AWG", f"{audit['leads_g']:.2f}", "", ""])
    lines = [f"# Mechanical checks: {name}", "", "| Check | Value | Limit | Result |", "| --- | --- | --- | --- |"]
    lines += [f"| {c[0]} | {c[1]:.3f} | {c[2]} | {'OK' if c[3] else 'FAIL'} |" for c in mech.checks]
    lines += ["", "## Axial stack-up (z from base bottom face, mm)", "", "| Datum | z |", "| --- | --- |"]
    lines += [f"| {k} | {v:.3f} |" for k, v in mech.dims["z"].items()]
    if rescored:
        lines += [
            "",
            "## Mission performance at true mass",
            "",
            "| Mass basis | Motor g | AUW g | Hover g/W | T/W | Burst winding C | Score | Feasible |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for label, r in rescored.items():
            lines.append(
                f"| {label} | {r['mass_g']:.1f} | {r['auw_g']:.0f} | {r['hover_g_per_w']:.2f} | {r['thrust_to_weight']:.2f} | {r['burst_winding_c']:.0f} | {r['score']:+.3f} | {r['feasible']} |"
            )
    (out / "mechanical_checks.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "mechanical_design.json").write_text(
        json.dumps(
            {"name": name, "dims": mech.dims, "fits": mechanical.FITS, "rules": mechanical.MECH_RULES},
            indent=2,
            default=float,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"files": files, "audit": audit, "checks_ok": mech.ok, "rescored": rescored, "mechanical": mech}
