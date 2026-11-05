"""Hardware deliverables for a selected design.

* ``lamination.dxf``: stator lamination outline (R12 LINE/CIRCLE entities, mm) for laser cutting,
  wire EDM or a stamping-die quote;
* ``cross_section.svg``: stator, magnets, bell and per-tooth winding phases;
* ``winding.csv``: tooth-by-tooth phase/direction/turns table for the winding operator;
* ``bom.csv``: parts with dimensions, quantities and illustrative cost;
* ``design.json``: the design vector, mission and predicted performance;
* a ``library.json`` entry, so the design can be used with ``bldc-motor point/match/thermal``.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from bldc_motor.design import geometry, materials
from bldc_motor.design.model import DesignAnalysis


def stator_outline_mm(analysis: DesignAnalysis, arc_segments: int = 6) -> list[tuple[float, float]]:
    """Closed outer contour of one lamination (teeth, shoes, slot bottoms), in mm."""

    g = analysis.geometry
    slots, _ = analysis.design["slot_pole"]
    mm = 1e3
    r_s, r1, r2 = g.r_s * mm, g.r1 * mm, g.r2 * mm
    r_tip = r_s - geometry.TOOTH_TIP_HEIGHT_MM
    half_w = g.w_t * mm / 2.0
    shoe_half = (g.slot_pitch - g.slot_opening) * mm / 2.0 / r_s  # angular half-width of the shoe
    pitch = 2.0 * math.pi / slots
    points: list[tuple[float, float]] = []

    def polar(r, a):
        return (r * math.cos(a), r * math.sin(a))

    for k in range(slots):
        c = k * pitch
        a_root = math.asin(min(half_w / r1, 1.0))
        a_top = math.asin(min(half_w / r2, 1.0))
        # leading slot-bottom arc ends at this tooth's right root
        points.append(polar(r1, c - a_root))
        points.append(polar(r2, c - a_top))
        points.append(polar(r_tip, c - shoe_half))
        points.append(polar(r_s, c - shoe_half))
        for i in range(1, arc_segments):
            points.append(polar(r_s, c - shoe_half + 2.0 * shoe_half * i / arc_segments))
        points.append(polar(r_s, c + shoe_half))
        points.append(polar(r_tip, c + shoe_half))
        points.append(polar(r2, c + a_top))
        points.append(polar(r1, c + a_root))
        # slot bottom arc to the next tooth
        next_start = (k + 1) * pitch - a_root
        for i in range(1, arc_segments):
            a = c + a_root + (next_start - c - a_root) * i / arc_segments
            points.append(polar(r1, a))
    return points


def write_dxf(analysis: DesignAnalysis, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pts = stator_outline_mm(analysis)
    lines = ["0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1], strict=True):
        lines += [
            "0",
            "LINE",
            "8",
            "LAMINATION",
            "10",
            f"{x1:.5f}",
            "20",
            f"{y1:.5f}",
            "30",
            "0.0",
            "11",
            f"{x2:.5f}",
            "21",
            f"{y2:.5f}",
            "31",
            "0.0",
        ]
    lines += [
        "0",
        "CIRCLE",
        "8",
        "LAMINATION",
        "10",
        "0.0",
        "20",
        "0.0",
        "30",
        "0.0",
        "40",
        f"{analysis.geometry.r_i * 1e3:.5f}",
    ]
    lines += ["0", "ENDSEC", "0", "EOF"]
    target.write_text("\n".join(lines) + "\n", encoding="ascii")
    return target


def write_svg(analysis: DesignAnalysis, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    g = analysis.geometry
    mm = 1e3
    r_bell = g.r_bell_out * mm
    size = 2.0 * r_bell + 4.0
    scale = 600.0 / size
    pts = stator_outline_mm(analysis)
    path_d = "M " + " L ".join(f"{x * scale:.2f},{-y * scale:.2f}" for x, y in pts) + " Z"
    slots, poles = analysis.design["slot_pole"]
    labels = []
    colors = {"A": "#d1495b", "B": "#2e86ab", "C": "#3d9970"}
    r_label = (g.r1 + g.r2) / 2.0 * mm
    for k, (phase, direction) in enumerate(analysis.layout.coils):
        a = 2.0 * math.pi * k / slots
        x, y = r_label * math.cos(a) * scale, -r_label * math.sin(a) * scale
        text = phase if direction > 0 else phase.lower()
        labels.append(
            f'<text x="{x:.1f}" y="{y:.1f}" fill="{colors[phase]}" font-size="{max(10, 14 * 22 / max(slots, 9)):.0f}" text-anchor="middle" dominant-baseline="middle" font-family="sans-serif" font-weight="bold">{text}</text>'
        )
    magnets = []
    alpha = analysis.design["magnet_arc"]
    for j in range(poles):
        a0 = 2.0 * math.pi * j / poles - alpha * math.pi / poles
        a1 = a0 + alpha * 2.0 * math.pi / poles
        ri, ro = g.r_m_in * mm * scale, g.r_m_out * mm * scale
        d = (
            f"M {ri * math.cos(a0):.2f},{-ri * math.sin(a0):.2f} A {ri:.2f},{ri:.2f} 0 0 0 {ri * math.cos(a1):.2f},{-ri * math.sin(a1):.2f} "
            f"L {ro * math.cos(a1):.2f},{-ro * math.sin(a1):.2f} A {ro:.2f},{ro:.2f} 0 0 1 {ro * math.cos(a0):.2f},{-ro * math.sin(a0):.2f} Z"
        )
        magnets.append(
            f'<path d="{d}" fill="{"#8e44ad" if j % 2 == 0 else "#f39c12"}" stroke="#333" stroke-width="0.5"/>'
        )
    half = size * scale / 2.0
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-half:.1f} {-half:.1f} {2 * half:.1f} {2 * half + 40:.1f}" width="640" height="680">
<rect x="{-half:.1f}" y="{-half:.1f}" width="{2 * half:.1f}" height="{2 * half + 40:.1f}" fill="white"/>
<circle r="{r_bell * scale:.2f}" fill="#bdc3c7" stroke="#333" stroke-width="0.8"/>
<circle r="{g.r_m_out * mm * scale:.2f}" fill="white" stroke="#333" stroke-width="0.5"/>
{"".join(magnets)}
<path d="{path_d}" fill="#95a5a6" stroke="#222" stroke-width="0.8" fill-rule="evenodd"/>
<circle r="{g.r_i * mm * scale:.2f}" fill="white" stroke="#222" stroke-width="0.8"/>
{"".join(labels)}
<text x="0" y="{half + 25:.1f}" text-anchor="middle" font-family="sans-serif" font-size="16">{slots}N{poles}P  {analysis.design["connection"]}  {analysis.design["turns"]}T x{analysis.design["strands"]} AWG{analysis.design["awg"]}  stator {analysis.design["stator_od_mm"]:.1f}x{analysis.design["stack_mm"]:.1f} mm</text>
</svg>
"""
    target.write_text(svg, encoding="utf-8")
    return target


def write_winding_table(analysis: DesignAnalysis, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    d = analysis.design
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tooth", "phase", "direction", "turns", "strands_in_hand", "awg", "connection"])
        for k, (phase, direction) in enumerate(analysis.layout.coils, start=1):
            writer.writerow(
                [k, phase, "CW" if direction > 0 else "CCW", d["turns"], d["strands"], d["awg"], d["connection"]]
            )
    return target


def bom_rows(analysis: DesignAnalysis, cost: dict) -> list[dict]:
    d = analysis.design
    i = analysis.internals
    g = analysis.geometry
    slots, poles = d["slot_pole"]
    magnet_arc_deg = d["magnet_arc"] * 360.0 / poles
    lam = next(m for m in materials.LAMINATIONS if abs(m.thickness_mm - d["lamination_mm"]) < 1e-9)
    copper_length_m = 3.0 * (i["n_series_turns_per_phase"] * i["turn_length_mm"] / 1000.0) * d["strands"]
    return [
        {"item": "stator lamination", "spec": f"{lam.name}, OD {d['stator_od_mm']:.2f} ID {2 * g.r_i * 1e3:.2f} mm, {slots} teeth (lamination.dxf)", "qty": i["lamination_count"], "unit": "pcs", "cost_usd": cost["laminations"]},
        {"item": "stator coating", "spec": f"epoxy powder coat ~{materials.SLOT_LINER_THICKNESS_M * 1e3:.2f} mm", "qty": 1, "unit": "lot", "cost_usd": 0.0},
        {"item": "magnet wire", "spec": f"AWG{d['awg']} ({i['wire_bare_mm']:.3f} mm bare) grade-2, {d['strands']} in hand", "qty": round(copper_length_m, 2), "unit": "m", "cost_usd": cost["copper"]},
        {"item": "arc magnet", "spec": f"{d['magnet_grade']} {d['magnet_mm']:.2f} mm thick, {magnet_arc_deg:.1f} deg arc, R_in {g.r_m_in * 1e3:.2f} mm, L {d['stack_mm'] + 1.0:.1f} mm, radial, alternating", "qty": poles, "unit": "pcs", "cost_usd": cost["magnets"]},
        {"item": "back-iron ring", "spec": f"low-carbon steel ID {2 * g.r_m_out * 1e3:.2f} OD {2 * g.r_bell_out * 1e3:.2f} L {d['stack_mm'] + 1.0:.1f} mm", "qty": 1, "unit": "pcs", "cost_usd": 0.0},
        {"item": "bell", "spec": f"7075 aluminium, machined, vented top, fits ring OD {2 * g.r_bell_out * 1e3:.2f} mm", "qty": 1, "unit": "pcs", "cost_usd": cost["bell"]},
        {"item": "shaft + bearings + base", "spec": "size-class standard", "qty": 1, "unit": "set", "cost_usd": cost["bearings_shaft"]},
        {"item": "winding labour", "spec": f"{slots} coils x {d['turns']} T needle winding", "qty": 1, "unit": "motor", "cost_usd": cost["winding_labour"]},
        {"item": "assembly, balance, test", "spec": "glue magnets, balance rotor, end-of-line Kv/R test", "qty": 1, "unit": "motor", "cost_usd": cost["assembly_test"]},
    ]  # fmt: skip


def write_bom(analysis: DesignAnalysis, cost: dict, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = bom_rows(analysis, cost)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "cost_usd": f"{row['cost_usd']:.3f}"})
    return target


def design_to_json(design: dict) -> dict:
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in design.items()}


def design_from_json(data: dict) -> dict:
    design = dict(data)
    design["slot_pole"] = tuple(design["slot_pole"])
    return design


def write_design_json(payload: dict, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, default=float) + "\n", encoding="utf-8")
    return target
