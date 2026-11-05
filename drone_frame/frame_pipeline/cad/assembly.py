"""Build the full frame, export STEP/STL, check interferences, print-bed fit and CAD mass."""

from __future__ import annotations

import json
from pathlib import Path

import cadquery as cq

from ..geometry import FrameGeometry, arm_axes
from . import parts as P

# part name -> (builder, material key in materials_assignment, print-orientation rotation (axis, deg), fill factor)
# fill factor = printed mass / solid CAD mass for the slicer settings in print_plan.py
PART_TABLE = {
    "arm":          ("arm", ((1, 0, 0), 180), 1.00),   # flight-top face on the bed, 100 % solid
    "bottom_body":  ("bottom_body", None, 0.78),
    "top_plate":    ("top_plate", None, 0.85),
    "jetson_tray":  ("jetson_tray", None, 0.85),
    "canopy":       ("canopy", None, 1.00),
    "camera_mount": ("camera_mount", ((0, 1, 0), -90), 0.90),
    "battery_pad":  ("battery_pad", None, 0.70),
    "gps_mast":     ("gps_mast", None, 0.60),
    "landing_leg":  ("landing_foot", ((1, 0, 0), 180), 0.70),
}
QTY = {"arm": 4, "landing_leg": 4}


def build_parts(g: FrameGeometry) -> dict[str, cq.Workplane]:
    return {
        "arm": P.make_arm(g),
        "bottom_body": P.make_bottom_body(g),
        "top_plate": P.make_top_plate(g),
        "jetson_tray": P.make_jetson_tray(g),
        "canopy": P.make_canopy(g),
        "camera_mount": P.make_camera_mount(g),
        "battery_pad": P.make_battery_pad(g),
        "gps_mast": P.make_gps_mast(g),
        "landing_leg": P.make_landing_leg(g),
    }


def _vol(wp: cq.Workplane) -> float:
    return sum(s.Volume() for s in wp.solids().vals())


def _bbox(wp: cq.Workplane):
    bb = wp.val().BoundingBox() if len(wp.solids().vals()) == 1 else cq.Compound.makeCompound(wp.solids().vals()).BoundingBox()
    return bb


def intersection_volume(a: cq.Workplane, b: cq.Workplane) -> float:
    try:
        return _vol(a.intersect(b))
    except Exception:  # OCC boolean failure -> report as unknown
        return float("nan")


def print_oriented(wp: cq.Workplane, rot) -> cq.Workplane:
    if rot:
        wp = wp.rotate((0, 0, 0), rot[0], rot[1])
    bb = _bbox(wp)
    return wp.translate((-(bb.xmin + bb.xmax) / 2, -(bb.ymin + bb.ymax) / 2, -bb.zmin))


def build_assembly(g: FrameGeometry, cfg: dict, mats: dict, out_dir: Path, log=print) -> dict:
    out_dir = Path(out_dir)
    (out_dir / "step").mkdir(parents=True, exist_ok=True)
    (out_dir / "stl").mkdir(parents=True, exist_ok=True)
    parts = build_parts(g)
    env = P.envelopes(g)
    arms = {f"arm_{i}": P.place_arm(parts["arm"], g, ang) for i, (ang, _) in enumerate(arm_axes(g))}
    legs = {f"leg_{i}": P.place_arm(parts["landing_leg"], g, ang) for i, (ang, _) in enumerate(arm_axes(g))}

    report = {"parts": {}, "interference": {}, "bed_fit": {}}
    bed = cfg["project"]["printer_build_volume"]
    assign = cfg["materials_assignment"]
    for name, wp in parts.items():
        key, rot, fill = PART_TABLE[name]
        mat = mats[assign[key]]
        vol = _vol(wp)
        po = print_oriented(wp, rot)
        bb = _bbox(po)
        dims = sorted([bb.xlen, bb.ylen], reverse=True) + [bb.zlen]
        fits = bb.xlen <= bed[0] and bb.ylen <= bed[1] and bb.zlen <= bed[2]
        cq.exporters.export(wp, str(out_dir / "step" / f"{name}.step"))
        cq.exporters.export(po, str(out_dir / "stl" / f"{name}.stl"), tolerance=0.05, angularTolerance=0.2)
        qty = QTY.get(name, 1)
        m = vol * mat.rho * fill
        report["parts"][name] = {
            "material": mat.name, "qty": qty, "solid_volume_cm3": vol / 1e3, "fill_factor": fill,
            "mass_each_g": m, "mass_total_g": m * qty, "print_bbox_mm": [round(v, 1) for v in dims],
        }
        report["bed_fit"][name] = fits
        log(f"  {name:13s} {mat.name:8s} x{qty}  {vol/1e3:7.2f} cm3  {m:6.1f} g each  bbox {dims[0]:.0f}x{dims[1]:.0f}x{dims[2]:.0f}  fits={fits}")

    # interference checks (positive volume = clash). Touching faces give ~0.
    body, top = parts["bottom_body"], parts["top_plate"]
    checks = {}
    for k, a in arms.items():
        checks[f"{k} x bottom_body"] = (a, body)
        checks[f"{k} x top_plate"] = (a, top)
        checks[f"{k} x fc_stack"] = (a, env["fc_stack"])
    checks["arm_0 x arm_1"] = (arms["arm_0"], arms["arm_1"])
    checks["fc_stack x bottom_body"] = (env["fc_stack"], body)
    checks["jetson x canopy"] = (env["jetson"], parts["canopy"])
    checks["jetson x jetson_tray"] = (env["jetson"], parts["jetson_tray"])
    checks["jetson_tray x canopy"] = (parts["jetson_tray"], parts["canopy"])
    checks["props x canopy"] = (env["props"], parts["canopy"])
    checks["props x gps_mast"] = (env["props"], parts["gps_mast"])
    checks["camera x camera_mount"] = (env["camera"], parts["camera_mount"])
    checks["camera_mount x bottom_body"] = (parts["camera_mount"], body)
    checks["battery x battery_pad"] = (env["battery"], parts["battery_pad"])
    checks["battery_pad x bottom_body"] = (parts["battery_pad"], body)
    checks["leg_0 x battery"] = (legs["leg_0"], env["battery"])
    for k, (a, b) in checks.items():
        v = intersection_volume(a, b)
        report["interference"][k] = v
    clashes = {k: v for k, v in report["interference"].items() if not (v < 1e-3)}
    report["clashes"] = clashes
    log(f"  interference: {len(checks)} pairs checked, {len(clashes)} clashes {clashes if clashes else ''}")

    # dovetail joint checks on arm 0 (all four sockets are identical by construction)
    ang0, (ux, uy) = arm_axes(g)[0]
    slide = {}
    for d in (2.0, 10.0, 20.0, g.tenon_L + 5.0):
        moved = arms["arm_0"].translate((ux * d, uy * d, 0))
        slide[f"{d:.0f} mm out"] = intersection_volume(moved, body) + intersection_volume(moved, top)
    deeper = arms["arm_0"].translate((-ux * 0.5, -uy * 0.5, 0))
    seat = intersection_volume(deeper, body)
    report["joint"] = {
        "slide_out_clash_mm3": slide,
        "slide_out_clear": all(v < 1e-3 for v in slide.values()),
        "seat_check_interference_0p5mm_deeper_mm3": seat,
        "wedges_when_pushed": seat > 1.0,
    }
    log(f"  dovetail: slide-out path clear={report['joint']['slide_out_clear']}  "
        f"pushed 0.5 mm deeper -> {seat:.1f} mm3 interference (wedge seat {'OK' if seat > 1.0 else 'MISSING'})")

    # assembly STEP (flight position, colour-coded)
    asm = cq.Assembly(name="PF10")
    colours = {"PPA-CF": (0.15, 0.15, 0.17), "PAHT-CF": (0.30, 0.30, 0.33), "PETG-HF": (0.95, 0.45, 0.10)}
    for name in ("bottom_body", "top_plate", "jetson_tray", "canopy", "camera_mount", "battery_pad", "gps_mast"):
        asm.add(parts[name], name=name, color=cq.Color(*colours[mats[assign[PART_TABLE[name][0]]].name]))
    for k, a in arms.items():
        asm.add(a, name=k, color=cq.Color(*colours[mats[assign["arm"]].name]))
    for k, a in env.items():
        asm.add(a, name=f"env_{k}", color=cq.Color(0.2, 0.6, 0.9, 0.35))
    asm.save(str(out_dir / "step" / "PF10_assembly.step"))
    envc = cq.Workplane("XY").newObject([cq.Compound.makeCompound([s for w in env.values() for s in w.solids().vals()])])
    cq.exporters.export(envc, str(out_dir / "step" / "envelopes.step"))

    total = sum(p["mass_total_g"] for n, p in report["parts"].items() if n != "landing_leg")
    report["frame_mass_g_without_legs"] = total
    report["arms_mass_g"] = report["parts"]["arm"]["mass_total_g"]
    report["nonarm_mass_g_without_legs"] = total - report["arms_mass_g"]
    report["legs_mass_g"] = report["parts"]["landing_leg"]["mass_total_g"]
    with open(out_dir / "cad_report.json", "w") as fh:
        json.dump(report, fh, indent=2, default=float)
    report["_shapes"] = {"parts": parts, "arms": arms, "legs": legs, "env": env}
    return report
