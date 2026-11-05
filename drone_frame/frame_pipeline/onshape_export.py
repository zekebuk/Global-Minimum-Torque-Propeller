"""Generate the Onshape setup from the optimised geometry.

Outputs (onshape/generated/):
  PF10ModularArm.fs       custom feature: native parametric arm (defaults = optimiser result)
  PF10ArmSocket.fs        custom feature: cuts sockets into a natively-modelled body
  PF10FrameVariables.fs   custom feature: one click defines every #pf10_* variable in a Part Studio
  variables.csv           the same values for a Variable Studio / configuration table
  variable_studio.txt     copy-paste list for a Variable Studio
"""

from __future__ import annotations

import csv
import datetime as dt
import shutil
from pathlib import Path
from string import Template

from .geometry import FrameGeometry

ONSHAPE_DIR = Path(__file__).resolve().parents[1] / "onshape"
# Any published std version works; Onshape keeps every version. If Onshape complains, keep the two header
# lines it writes into a new Feature Studio and paste everything below them.
FS_VERSION = "2399"


def _f(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def variables(g: FrameGeometry) -> list[tuple[str, str, str, str]]:
    """(name, value, unit, description)."""
    mm = "mm"
    rows = [
        ("pf10_arm_angle", g.arm_angle_deg, "deg", "arm angle from +X (forward)"),
        ("pf10_motor_radius", g.motor_radius, mm, "body centre to motor centre"),
        ("pf10_root_radius", g.root_radius, mm, "body centre to arm root face"),
        ("pf10_arm_L", g.arm_L, mm, "free arm length root face -> motor centre"),
        ("pf10_arm_b", g.arm_b, mm, "arm width"),
        ("pf10_arm_hr", g.arm_h_root, mm, "arm root height"),
        ("pf10_arm_ht", g.arm_h_tip, mm, "arm tip height"),
        ("pf10_arm_tw", g.arm_t_w, mm, f"web thickness = {g.arm_n_walls} wall loops"),
        ("pf10_arm_tf", g.arm_t_f, mm, f"flange thickness = {g.arm_n_layers} skin layers"),
        ("pf10_tenon_L", g.tenon_L, mm, "tenon length"),
        ("pf10_tenon_b", g.tenon_b, mm, "tenon width incl. bolt ears"),
        ("pf10_retention_y", g.tenon_bolt_y, mm, "retention insert/screw lateral offset"),
        ("pf10_dv_depth", g.dv_depth, mm, "dovetail foot depth"),
        ("pf10_dv_angle", g.dv_beta_deg, "deg", "dovetail flank angle from vertical (60 deg dovetail)"),
        ("pf10_dv_flare", g.dv_flare, mm, "dovetail flare per side"),
        ("pf10_dv_home_gap", g.dv_home_gap, mm, "free travel behind the seated tenon"),
        ("pf10_dv_side_clear", g.dv_side_clear, mm, "straight-side clearance (no contact)"),
        ("pf10_dv_top_relief", g.dv_top_relief, mm, "top-plate relief over the tenon"),
        ("pf10_tie_x", g.tie_bolt_x, mm, "tie bolt position along the arm (arm-local)"),
        ("pf10_tie_y", g.tie_bolt_y, mm, "tie bolt lateral offset"),
        ("pf10_pad_d", g.pad_d, mm, "motor pad diameter"),
        ("pf10_body_hx", g.body_hx, mm, "body half length (X)"),
        ("pf10_body_hy", g.body_hy, mm, "body half width (Y)"),
        ("pf10_H_int", g.H_int, mm, "body interior height"),
        ("pf10_t_bp", g.t_bp, mm, "bottom plate thickness"),
        ("pf10_t_tp", g.t_tp, mm, "top plate thickness"),
        ("pf10_wall_t", g.wall_t, mm, "body wall thickness"),
        ("pf10_z_arm_top", g.z_arm_top, mm, "arm top / top-plate underside Z"),
        ("pf10_socket_half_w", g.socket_half_w, mm, "socket boss half width"),
        ("pf10_socket_wall", g.socket_wall_t, mm, "socket wall / back wall"),
        ("pf10_stack_pattern", g.stack_pattern, mm, "FC/ESC stack hole pattern"),
        ("pf10_jetson_L", g.jetson_env[0], mm, "Jetson envelope L"),
        ("pf10_jetson_W", g.jetson_env[1], mm, "Jetson envelope W"),
        ("pf10_jetson_H", g.jetson_env[2], mm, "Jetson envelope H"),
        ("pf10_jetson_mx", g.jetson_mount_rect[0], mm, "carrier hole rectangle X (PLACEHOLDER)"),
        ("pf10_jetson_my", g.jetson_mount_rect[1], mm, "carrier hole rectangle Y (PLACEHOLDER)"),
        ("pf10_grommet_h", g.grommet_h, mm, "Jetson tray soft-mount gap"),
        ("pf10_prop_d", g.prop_d, mm, "prop diameter"),
    ]
    return [(n, _f(v), u, d) for n, v, u, d in rows]


def _subs(g: FrameGeometry, analysis: dict) -> dict:
    r = analysis["arm"]["result"]
    return {
        "FS_VERSION": FS_VERSION,
        "DATE": dt.date.today().isoformat(),
        "ARM_SUMMARY": (f"{analysis['arm']['result']['design']['n_walls']} walls / {analysis['arm']['result']['design']['n_layers']} layers, "
                        f"{r['mass']['total_g']:.1f} g, modes {r['f_vert_Hz']:.0f}/{r['f_lat_Hz']:.0f}/{r['f_tor_Hz']:.0f} Hz"),
        "ARM_L": _f(g.arm_L), "ARM_B": _f(g.arm_b), "ARM_HR": _f(g.arm_h_root), "ARM_HT": _f(g.arm_h_tip),
        "ARM_TW": _f(g.arm_t_w), "ARM_TF": _f(g.arm_t_f), "TENON_L": _f(g.tenon_L), "TENON_B": _f(g.tenon_b),
        "TENON_CHAMFER": _f(g.tenon_chamfer), "PAD_D": _f(g.pad_d), "CLAMP_T": _f(g.motor_clamp_t),
        "BOLT_Y": _f(g.tenon_bolt_y), "SHAFT_D": _f(g.shaft_d), "HOLE_D": _f(g.motor_hole_d), "M3_CLEAR": _f(g.m3_clear),
        "P16": _f(g.motor_patterns[0]), "P19": _f(g.motor_patterns[1]),
        "ROOT_R": _f(g.root_radius), "Z_TOP": _f(g.z_arm_top), "CLEAR": _f(g.socket_clearance),
        "BACK_WALL": _f(g.socket_wall_t), "ANGLE": _f(g.arm_angle_deg), "NUT_AF": _f(g.m3_nut_af),
        "NUT_DEPTH": _f(g.m3_nut_depth), "WINDOW_W": _f(g.arm_b - 2 * g.arm_t_w + 4),
        "DV_DEPTH": _f(g.dv_depth), "DV_ANGLE": _f(g.dv_beta_deg), "DV_TAPER": _f(g.dv_taper),
        "INSERT_DEPTH": _f(g.dv_insert_depth), "INSERT_D": _f(g.m3_insert), "TIE_Y": _f(g.tie_bolt_y),
        "HOME_GAP": _f(g.dv_home_gap), "SIDE_CLEAR": _f(g.dv_side_clear), "TOP_RELIEF": _f(g.dv_top_relief),
        "T_TP": _f(g.t_tp),
    }


def _variables_fs(g: FrameGeometry) -> str:
    lines = [
        f"FeatureScript {FS_VERSION};",
        f'import(path : "onshape/std/common.fs", version : "{FS_VERSION}.0");',
        "",
        f"// PF10 Frame Variables - GENERATED {dt.date.today().isoformat()} by frame_pipeline/onshape_export.py.",
        "// Insert this feature first in a Part Studio; every #pf10_* variable is then available to later features.",
        "",
        'annotation { "Feature Type Name" : "PF10 Frame Variables" }',
        "export const pf10FrameVariables = defineFeature(function(context is Context, id is Id, definition is map)",
        "    precondition",
        "    {",
        "    }",
        "    {",
    ]
    for n, v, u, d in variables(g):
        unit = "degree" if u == "deg" else "millimeter"
        lines.append(f'        setVariable(context, "{n}", {v} * {unit}); // {d}')
    lines += ["    });", ""]
    return "\n".join(lines)


def export_onshape(g: FrameGeometry, analysis: dict, step_dir: Path | None = None) -> dict:
    out = ONSHAPE_DIR / "generated"
    out.mkdir(parents=True, exist_ok=True)
    subs = _subs(g, analysis)
    written = []
    for tmpl in ("PF10ModularArm.fs.tmpl", "PF10ArmSocket.fs.tmpl"):
        text = Template((ONSHAPE_DIR / "templates" / tmpl).read_text()).substitute(subs)
        p = out / tmpl.replace(".tmpl", "")
        p.write_text(text)
        written.append(p)
    p = out / "PF10FrameVariables.fs"
    p.write_text(_variables_fs(g))
    written.append(p)
    with open(out / "variables.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "value", "unit", "description"])
        w.writerows(variables(g))
    written.append(out / "variables.csv")
    (out / "variable_studio.txt").write_text(
        "\n".join(f"#{n} = {v} {u}    // {d}" for n, v, u, d in variables(g)) + "\n")
    written.append(out / "variable_studio.txt")
    if step_dir is not None:
        dst = out / "step"
        dst.mkdir(exist_ok=True)
        for s in Path(step_dir).glob("*.step"):
            shutil.copy2(s, dst / s.name)
    return {"files": [str(x) for x in written]}
