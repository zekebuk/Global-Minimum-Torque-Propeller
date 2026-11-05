"""Analysis orchestration with mass-budget closure.

AUW depends on the arm mass, the arm loads depend on AUW, and the layout depends on the arm width
(socket size). Iterate: AUW -> operating point -> layout -> arm optimisation -> AUW until the AUW
change is below `closure_tol_g`.
"""

from __future__ import annotations

import json
import math

import numpy as np

from .arm_model import ArmContext, evaluate, socket_half_width
from .arm_opt import optimise_arm
from .layout import body_half_dims, solve_layout
from .materials import Material
from .rotor import operating_points


def component_mass_g(cfg: dict) -> dict:
    p, e = cfg["propulsion"], cfg["electronics"]
    items = {
        "motors (4)": 4 * p["motor_mass_g"],
        "props (4)": 4 * p["prop_mass_g"],
        "Jetson + carrier + cooler": e["jetson"]["mass_g"],
        "FC/ESC stack": e["fc_stack"]["mass_g"],
        "VTX + camera": e["vtx_mass_g"],
        "GPS": e["gps_mass_g"],
        "receiver": e["receiver_mass_g"],
        "Jetson BEC": e["bec_mass_g"],
        "wiring": e["wiring_mass_g"],
        "hardware": e["hardware_mass_g"],
        "battery": e["battery"]["mass_g"],
    }
    return items


def _ctx(cfg, mat, body_mat, lay, op, nonarm_g):
    hx, hy = body_half_dims(cfg)
    return ArmContext(cfg, mat, body_mat, lay["arm_free_length"], lay["root_radius"], 2 * hy, nonarm_g, op)


def run_analysis(cfg: dict, mats: dict[str, Material], nonarm_g: float | None = None,
                 compare_materials: bool = True, log=print) -> dict:
    opt = cfg["optimization"]
    nonarm = nonarm_g if nonarm_g is not None else opt["nonarm_frame_mass_initial_g"]
    arm_mat = mats[cfg["materials_assignment"]["arm"]]
    body_mat = mats[cfg["materials_assignment"]["bottom_body"]]
    comp = component_mass_g(cfg)
    comp_total = sum(comp.values())
    arm_g, b = 65.0, 20.0
    history = []
    auw_prev = None
    for it in range(opt["closure_iterations"]):
        auw = comp_total + nonarm + 4 * arm_g
        op = operating_points(auw, cfg)
        lay = solve_layout(cfg, socket_half_width(b, cfg))
        ctx = _ctx(cfg, arm_mat, body_mat, lay, op, nonarm)
        res = optimise_arm(ctx, maxiter=opt["de_maxiter"], popsize=opt["de_popsize"], seed=opt["seed"])
        r = res["result"]
        arm_g, b = r["mass"]["total_g"], r["design"]["b"]
        history.append({"iter": it, "auw_g": auw, "arm_g": arm_g, "b": b, "L": lay["arm_free_length"],
                        "feasible": res["feasible"]})
        log(f"  closure {it}: AUW {auw:7.1f} g  arm {arm_g:5.1f} g  b {b:5.2f}  L {lay['arm_free_length']:6.1f}  feasible={res['feasible']}")
        if auw_prev is not None and abs(auw - auw_prev) < opt["closure_tol_g"]:
            break
        auw_prev = auw
    # final consistent state
    auw = comp_total + nonarm + 4 * arm_g
    op = operating_points(auw, cfg)
    lay = solve_layout(cfg, socket_half_width(b, cfg))
    ctx = _ctx(cfg, arm_mat, body_mat, lay, op, nonarm)
    final = evaluate(res["x"], ctx)

    out = {
        "components_g": comp, "components_total_g": comp_total, "nonarm_frame_g": nonarm,
        "arm_mass_g": arm_g, "frame_mass_g": nonarm + 4 * arm_g, "auw_g": auw,
        "operating_point": op, "layout": lay, "arm": {"x": res["x"], "result": final, "feasible": res["feasible"],
                                                       "active": res["active_constraints"]},
        "material_comparison": {}, "closure_history": history,
    }
    if compare_materials:
        out["material_comparison"] = compare(cfg, mats, out, log)
    return out


def compare(cfg: dict, mats: dict[str, Material], an: dict, log=print) -> dict:
    """Optimise the arm in every material at the converged layout/loads (same body, same constraints)."""
    opt = cfg["optimization"]
    body_mat = mats[cfg["materials_assignment"]["bottom_body"]]
    arm_name = cfg["materials_assignment"]["arm"]
    comparison = {}
    for name, m in mats.items():
        if name == arm_name:
            comparison[name] = {"x": an["arm"]["x"], "result": an["arm"]["result"], "feasible": an["arm"]["feasible"]}
            continue
        log(f"  material comparison: optimising {name} arm")
        c = optimise_arm(_ctx(cfg, m, body_mat, an["layout"], an["operating_point"], an["nonarm_frame_g"]),
                         maxiter=opt["de_maxiter"], popsize=opt["de_popsize"], seed=opt["seed"])
        comparison[name] = {"x": c["x"], "result": c["result"], "feasible": c["feasible"]}
    return comparison


def to_jsonable(o):
    if isinstance(o, dict):
        return {str(k): to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [to_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return v if math.isfinite(v) else str(v)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return to_jsonable(o.tolist())
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def dump(result: dict, path) -> None:
    with open(path, "w") as fh:
        json.dump(to_jsonable(result), fh, indent=2)
