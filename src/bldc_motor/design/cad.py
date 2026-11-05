"""3-D solid models (CadQuery / OpenCascade) and STEP export for every part and the assembly.

STEP is what CNC shops (bell, base, shaft), laser-lamination vendors (they also take the DXF)
and magnet suppliers quote from. Solids are built from ``mechanical.derive`` dimensions, so
CAD, drawings and the electromagnetic model cannot drift apart.

Requires the optional dependency ``cadquery`` (``pip install cadquery``).
"""

from __future__ import annotations

import math
from pathlib import Path

import cadquery as cq

from bldc_motor.design import export
from bldc_motor.design import mechanical as mech
from bldc_motor.design.mechanical import MechanicalDesign
from bldc_motor.design.model import DesignAnalysis

DENSITY_G_MM3 = {
    "7075-T6": 2.81e-3,
    "52100": 7.81e-3,
    "steel_1010": 7.87e-3,
    "si_steel": 7.65e-3,
    "ndfeb": 7.50e-3,
    "copper": 8.96e-3,
}


def _revolve(profile: list[tuple[float, float]]) -> cq.Workplane:
    """Revolve a closed (r, z) polygon about the z axis."""

    return cq.Workplane("XZ").polyline(profile).close().revolve(360.0, (0, 0, 0), (0, 1, 0))


def base(m: MechanicalDesign) -> cq.Workplane:
    d = m.dims
    solid = _revolve(mech.base_profile(m))
    a = d["mount_pattern"] / 2
    holes = (
        cq.Workplane("XY")
        .pushPoints([(a, a), (-a, a), (a, -a), (-a, -a)])
        .circle(1.25)
        .extrude(d["mount_thread_depth"])
    )
    windows = None
    n = d["base_windows"]
    half = math.radians(d["base_window_half_angle_deg"])
    ri, ro = d["base_window_r_in"], d["base_window_r_out"]
    for k in range(n):
        c = 2 * math.pi * k / n
        w = (
            cq.Workplane("XY")
            .workplane(offset=-0.01)
            .moveTo(ri * math.cos(c - half), ri * math.sin(c - half))
            .lineTo(ro * math.cos(c - half), ro * math.sin(c - half))
            .threePointArc((ro * math.cos(c), ro * math.sin(c)), (ro * math.cos(c + half), ro * math.sin(c + half)))
            .lineTo(ri * math.cos(c + half), ri * math.sin(c + half))
            .threePointArc((ri * math.cos(c), ri * math.sin(c)), (ri * math.cos(c - half), ri * math.sin(c - half)))
            .close()
            .extrude(d["flange_thickness"] + 0.02)
        )
        windows = w if windows is None else windows.union(w)
    return solid.cut(holes).cut(windows)


def shaft(m: MechanicalDesign) -> cq.Workplane:
    d, z = m.dims, m.dims["z"]
    r5 = d["shaft_upper_d"] / 2
    solid = _revolve(mech.shaft_profile(m))
    flat = (
        cq.Workplane("XY")
        .workplane(offset=z["plate_top"])
        .center(r5 - 0.25 + 1.5, 0)
        .rect(3.0, 6.0)
        .extrude(z["hub_top"] - z["plate_top"] - 0.3)
    )
    return solid.cut(flat)


def bell(m: MechanicalDesign) -> cq.Workplane:
    d, z = m.dims, m.dims["z"]
    rboss = d["hub_boss_d"] / 2
    solid = _revolve(mech.bell_profile(m))
    pcd = d["vent_pcd"] / 2
    pts = [
        (pcd * math.cos(2 * math.pi * k / d["vent_holes"]), pcd * math.sin(2 * math.pi * k / d["vent_holes"]))
        for k in range(d["vent_holes"])
    ]
    vents = (
        cq.Workplane("XY")
        .workplane(offset=z["plate_bottom"] - 0.01)
        .pushPoints(pts)
        .circle(d["vent_hole_d"] / 2)
        .extrude(d["bell_plate"] + 0.02)
    )
    grub = cq.Workplane("YZ").workplane(offset=0).center(0, z["grub"]).circle(1.25).extrude(rboss + 0.1)
    return solid.cut(vents).cut(grub)


def ring(m: MechanicalDesign) -> cq.Workplane:
    return _revolve(mech.ring_profile(m))


def magnet(m: MechanicalDesign, index: int = 0) -> cq.Workplane:
    d, z = m.dims, m.dims["z"]
    half = math.radians(d["magnet_arc_deg"]) / 2
    ri, ro = d["magnet_ri"], d["magnet_ro"]
    sketch = (
        cq.Workplane("XY")
        .workplane(offset=z["mag_bottom"])
        .moveTo(ri * math.cos(-half), ri * math.sin(-half))
        .lineTo(ro * math.cos(-half), ro * math.sin(-half))
        .threePointArc((ro, 0), (ro * math.cos(half), ro * math.sin(half)))
        .lineTo(ri * math.cos(half), ri * math.sin(half))
        .threePointArc((ri, 0), (ri * math.cos(-half), ri * math.sin(-half)))
        .close()
        .extrude(d["magnet_length"])
    )
    return sketch.rotate((0, 0, 0), (0, 0, 1), index * 360.0 / d["poles"])


def stator_stack(m: MechanicalDesign, analysis: DesignAnalysis) -> cq.Workplane:
    d, z = m.dims, m.dims["z"]
    pts = export.stator_outline_mm(analysis, arc_segments=4)
    outline = cq.Workplane("XY").workplane(offset=z["stator_bottom"]).polyline(pts).close().extrude(d["stack"])
    bore = (
        cq.Workplane("XY")
        .workplane(offset=z["stator_bottom"] - 0.01)
        .circle(d["stator_id"] / 2)
        .extrude(d["stack"] + 0.02)
    )
    return outline.cut(bore)


def lamination(m: MechanicalDesign, analysis: DesignAnalysis) -> cq.Workplane:
    d = m.dims
    pts = export.stator_outline_mm(analysis, arc_segments=8)
    return (
        cq.Workplane("XY")
        .polyline(pts)
        .close()
        .extrude(d["lamination_t"])
        .cut(cq.Workplane("XY").circle(d["stator_id"] / 2).extrude(d["lamination_t"]))
    )


def coils(m: MechanicalDesign, analysis: DesignAnalysis) -> cq.Workplane:
    """Each tooth coil as a rectangular tube around the tooth (envelope of the winding)."""

    d, z = m.dims, m.dims["z"]
    i = analysis.internals
    r1, r2 = d["slot_bottom_d"] / 2 + d["coat"], d["shoe_inner_d"] / 2 - 0.1
    build = i["coil_build_mm"]
    w_in = d["tooth_width"] + 2 * d["coat"]
    h_in = d["stack"] + 2 * d["coat"]
    radial = r2 - r1
    zc = (z["stator_bottom"] + z["stator_top"]) / 2
    outer = cq.Workplane("XY").box(radial, w_in + 2 * build, h_in + 2 * build)
    inner = cq.Workplane("XY").box(radial + 0.2, w_in, h_in)
    unit = outer.cut(inner).translate(((r1 + r2) / 2, 0, zc))
    result = None
    for k in range(d["slots"]):
        piece = unit.rotate((0, 0, 0), (0, 0, 1), k * 360.0 / d["slots"])
        result = piece if result is None else result.union(piece)
    return result


def bearing(m: MechanicalDesign, z0: float) -> cq.Workplane:
    return _revolve(mech.bearing_profile(m, z0))


def e_clip(m: MechanicalDesign) -> cq.Workplane:
    d, z = m.dims, m.dims["z"]
    zc = z["clip_groove"]
    return _revolve(
        [
            (d["clip_groove_d"] / 2, zc - 0.3),
            (d["clip_groove_d"] / 2 + 2.0, zc - 0.3),
            (d["clip_groove_d"] / 2 + 2.0, zc + 0.3),
            (d["clip_groove_d"] / 2, zc + 0.3),
        ]
    )


PARTS = {
    "P-001_lamination": ("si_steel", lamination),
    "P-002_stator_stack": ("si_steel", stator_stack),
    "P-003_magnet": ("ndfeb", magnet),
    "P-004_back_iron_ring": ("steel_1010", ring),
    "P-005_bell": ("7075-T6", bell),
    "P-006_base": ("7075-T6", base),
    "P-007_shaft": ("52100", shaft),
}


def build_parts(m: MechanicalDesign, analysis: DesignAnalysis) -> dict[str, cq.Workplane]:
    out = {}
    for name, (_material, builder) in PARTS.items():
        out[name] = builder(m, analysis) if builder in (lamination, stator_stack) else builder(m)
    return out


def masses_g(m: MechanicalDesign, analysis: DesignAnalysis, parts: dict | None = None) -> dict[str, float]:
    parts = parts or build_parts(m, analysis)
    masses = {}
    for name, (material, _) in PARTS.items():
        masses[name] = parts[name].val().Volume() * DENSITY_G_MM3[material]
    masses["P-003_magnet"] *= 1  # per piece
    masses["coils_envelope"] = coils(m, analysis).val().Volume() * DENSITY_G_MM3["copper"]
    return masses


def assembly(m: MechanicalDesign, analysis: DesignAnalysis) -> cq.Assembly:
    d, z = m.dims, m.dims["z"]
    asm = cq.Assembly(name=m.name)
    asm.add(base(m), name="base", color=cq.Color(0.55, 0.55, 0.6))
    asm.add(stator_stack(m, analysis), name="stator", color=cq.Color(0.35, 0.35, 0.38))
    asm.add(coils(m, analysis), name="coils", color=cq.Color(0.80, 0.45, 0.15))
    asm.add(bearing(m, 0.0), name="bearing_lower", color=cq.Color(0.8, 0.8, 0.8))
    asm.add(bearing(m, z["upper_bearing_bottom"]), name="bearing_upper", color=cq.Color(0.8, 0.8, 0.8))
    asm.add(shaft(m), name="shaft", color=cq.Color(0.75, 0.75, 0.78))
    asm.add(e_clip(m), name="e_clip", color=cq.Color(0.2, 0.2, 0.2))
    asm.add(ring(m), name="back_iron_ring", color=cq.Color(0.3, 0.3, 0.3))
    for k in range(d["poles"]):
        color = cq.Color(0.55, 0.25, 0.65) if k % 2 == 0 else cq.Color(0.95, 0.6, 0.1)
        asm.add(magnet(m, k), name=f"magnet_{k + 1:02d}", color=color)
    asm.add(bell(m), name="bell", color=cq.Color(0.15, 0.35, 0.75))
    return asm


def export_step(m: MechanicalDesign, analysis: DesignAnalysis, out_dir: str | Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    parts = build_parts(m, analysis)
    for name, solid in parts.items():
        path = out / f"{name}.step"
        cq.exporters.export(solid, str(path))
        files[name] = path
    asm_path = out / f"{m.name}_assembly.step"
    assembly(m, analysis).save(str(asm_path))
    files["assembly"] = asm_path
    return files


def isometric_svg(m: MechanicalDesign, analysis: DesignAnalysis, path: str | Path, cutaway: bool = True) -> Path:
    """Hidden-line isometric line drawing of the assembly (quarter cut-away), shaft up."""

    shapes = [base(m), stator_stack(m, analysis), coils(m, analysis), shaft(m), ring(m), bell(m)]
    shapes += [magnet(m, k) for k in range(m.dims["poles"])]
    compound = cq.Compound.makeCompound([s.val() for s in shapes])
    if cutaway:
        compound = compound.cut(cq.Workplane("XY").box(200.0, 200.0, 200.0, centered=(False, False, True)).val())
    # CadQuery's SVG projection draws +z downwards; flip so the prop shaft points up.
    compound = compound.rotate(cq.Vector(0, 0, 0), cq.Vector(1, 0, 0), 180)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(
        cq.Workplane().add(compound),
        str(target),
        opt={"width": 520, "height": 420, "marginLeft": 40, "marginTop": 30, "showAxes": False,
             "projectionDir": (1.0, -1.0, -0.6), "strokeWidth": 0.25, "showHidden": False},
    )  # fmt: skip
    return target
