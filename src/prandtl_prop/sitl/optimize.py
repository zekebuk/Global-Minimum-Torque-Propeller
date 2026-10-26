"""Mission-weighted prop selection: GMT twists flown through the same missions, ranked by battery energy.

The candidate family keeps the design's planform (optionally scaled in chord) and changes the
twist only: the global-minimum-torque twist (``aero.gmt``) designed for the vehicle's hover thrust
at design rpm f x (hover rpm of the design as built) and bell weight mu. f > 1 gives a flatter,
faster-turning blade; f < 1 a steeper one, limited by ``gmt.cl_limit`` at the design point. The
design as built, the Betz/MIL twist, and a constant-pitch prop at the hover point ride along as
references.

Every candidate gets its own rotor tables and replays every mission trace with the *same motor and
battery* (the hardware you have bought or chosen: Kv from the design as built). The objective is the
weighted battery energy sum_m w_m E_m; a candidate is feasible when, on every mission, the time it
spends saturated or above the motor's current limit, and separately the time it spends with a
rotor's peak section Cl above ``multirotor.forward_cl_limit`` (retreating-blade or descent stall),
each stay within ``sitl.allowed_saturation_fraction`` or no worse than the design as built (whichever is larger: an iteration must not make the vehicle
worse at what it already does).

A second family designs the planform too (``aero.gmt.design_with_chord``): chord and twist per
station within ``sitl.chord_bounds_over_R``, at design rpm f x hover rpm. Every candidate gets the
root thickening its full-throttle stress needs (``gmt_design.root_scale_for``; beyond the cap it is
infeasible), and the prop mass and inertia that follow (section-area model scaled to the CAD of the
design as built); the all-up mass changes by n dm_prop and the traces' wrench is scaled with it
(same kinematics).

Assumptions: the traces are frozen (same trajectory, see ``replay``); the twist and planform are
designed in axial flow; the multi-point optimum is searched over these families, not over every
blade.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np

from prandtl_prop.aero import gmt
from prandtl_prop.aero.bemt import BladeStations, OperatingPoint
from prandtl_prop.sitl import tables as tb
from prandtl_prop.sitl.motor import Battery, Motor
from prandtl_prop.sitl.replay import Trace, replay


def candidates(design, stations: BladeStations, op: OperatingPoint, polar, hover_rpm: float, hover_thrust_N: float) -> list[dict[str, Any]]:
    """Twist candidates: dicts with label, stations, chord scale, and the design parameters (or the reason they failed)."""

    spec = design.sitl
    tip_loss = design.aero.tip_loss
    out = [{"label": "design as built", "stations": stations, "chord_scale": 1.0, "family": "built"}]
    hover = replace(op, rpm=hover_rpm, v_inf_m_s=0.0)
    for scale in spec.chord_scales:
        planform = replace(stations, chord_m=stations.chord_m * scale)
        for f in spec.design_rpm_fractions:
            point = replace(op, rpm=f * hover_rpm, v_inf_m_s=0.0)
            for mu in spec.bending_weights:
                row = {"label": f"GMT f={f:g} mu={mu:g}" + (f" c x{scale:g}" if scale != 1.0 else ""), "chord_scale": scale}
                row.update({"family": "gmt", "design_rpm_fraction": f, "bending_weight": mu})
                try:
                    solution = gmt.design(
                        planform, point, polar, hover_thrust_N, cl_limit=design.gmt.cl_limit, bending_weight=mu, tip_loss=tip_loss
                    )
                except ValueError as exc:
                    out.append({**row, "stations": None, "reason": str(exc)})
                    continue
                out.append({**row, "stations": gmt.with_twist(planform, solution.twist_deg), "max_design_cl": float(solution.cl.max())})
    radius = op.radius_m
    bounds = (spec.chord_bounds_over_R[0] * radius, spec.chord_bounds_over_R[1] * radius)
    for f in spec.planform_rpm_fractions:
        point = replace(op, rpm=f * hover_rpm, v_inf_m_s=0.0)
        for mu in spec.planform_bending_weights:
            row = {
                "label": f"GMT planform f={f:g} mu={mu:g}",
                "chord_scale": 1.0,
                "family": "planform",
                "design_rpm_fraction": f,
                "bending_weight": mu,
            }
            try:
                blade, solution = gmt.design_with_chord(
                    stations,
                    point,
                    polar,
                    hover_thrust_N,
                    cl_limit=design.gmt.cl_limit,
                    chord_bounds_m=bounds,
                    bending_weight=mu,
                    tip_loss=tip_loss,
                )
            except ValueError as exc:
                out.append({**row, "stations": None, "reason": str(exc)})
                continue
            out.append(
                {
                    **row,
                    "stations": blade,
                    "max_design_cl": float(solution.cl.max()),
                    "max_chord_over_R": float(blade.chord_m.max() / radius),
                }
            )
    if spec.helix_baseline:
        mil = gmt.design(stations, hover, gmt._drag_free(polar), hover_thrust_N, cl_limit=10.0, tip_loss=tip_loss)
        out.append({"label": "Betz/MIL", "stations": gmt.with_twist(stations, mil.twist_deg), "chord_scale": 1.0, "family": "mil"})
        pitch, _ = gmt.helix_for_thrust(stations, hover, polar, hover_thrust_N, tip_loss=tip_loss)
        out.append(
            {
                "label": f"constant pitch {pitch / 0.0254:.2f} in",
                "stations": gmt.with_twist(stations, gmt.helix_twist(stations, pitch)),
                "chord_scale": 1.0,
                "family": "helix",
                "pitch_m": pitch,
            }
        )
    return out


def evaluate(
    candidate: dict[str, Any],
    design,
    op: OperatingPoint,
    polar,
    corrections,
    traces: list[Trace],
    weights: list[float],
    vehicle,
    motor: Motor,
    battery: Battery,
    reference: dict[str, Any],
) -> dict[str, Any]:
    spec = design.sitl
    row = {k: v for k, v in candidate.items() if k != "stations" and not k.startswith("_")}
    build = structure(design, candidate["stations"], op, polar, corrections, reference)
    row.update({k: v for k, v in build.items() if not k.startswith("_")})
    if build["root_thickness_scale"] is None and candidate["family"] != "built":
        row["reason"] = "root stress beyond the thickness cap at full throttle"
        return {"row": row, "table": None, "results": []}
    table = tb.build_table(
        candidate["label"], candidate["stations"], op, polar, spec, tip_loss=design.aero.tip_loss, corrections=corrections
    )
    delta_m, delta_j = build.get("_auw_delta_kg", 0.0), build.get("_prop_inertia_delta_kg_m2", 0.0)
    rotor = replace(motor, inertia_kg_m2=motor.inertia_kg_m2 + delta_j)
    vehicle = replace(vehicle, mass_kg=vehicle.mass_kg + delta_m)
    scale = vehicle.mass_kg / (vehicle.mass_kg - delta_m)
    flown = [replace(t, wrench=t.wrench * scale) for t in traces]  # same kinematics, the new mass
    results = [
        replay(t, vehicle, table, rotor, battery, max_throttle=spec.max_throttle, cl_limit=design.multirotor.forward_cl_limit)
        for t in flown
    ]
    row["weighted_energy_Wh"] = float(sum(w * r["energy_Wh"] for w, r in zip(weights, results, strict=True)))
    for r in results:
        name = r["mission"]
        for key in (
            "energy_Wh",
            "mean_battery_power_W",
            "saturated_fraction",
            "over_current_fraction",
            "max_duty",
            "duty_p99",
            "peak_motor_current_A",
            "vrs_fraction",
            "stall_fraction",
            "peak_section_cl",
        ):
            row[f"{name}_{key}"] = r[key]
    row["worst_limit_fraction"] = max(r["saturated_fraction"] + r["over_current_fraction"] for r in results)
    row["worst_stall_fraction"] = max(r["stall_fraction"] for r in results)
    hover = table.evaluate(op.rpm, 0.0, 0.0)
    row["torque_per_thrust_m"] = float(hover["torque_Nm"] / hover["thrust_N"])
    return {"row": row, "table": table, "results": results}


def structure(design, stations: BladeStations, op: OperatingPoint, polar, corrections, reference: dict[str, Any] | None) -> dict[str, Any]:
    """Root thickening for full-throttle stress (gmt_design.root_scale_for) and the prop mass and inertia that follow.

    Mass and inertia come from the section-area model of gmt_design, scaled by the design as built's
    CAD values (``reference``: its model mass/inertia and CAD mass/inertia), so the model's bias cancels.
    """

    from prandtl_prop import gmt_design
    from prandtl_prop.aero.bemt import solve_rotor, trim_rpm_to_thrust

    spec = design.multirotor
    full = trim_rpm_to_thrust(
        stations, op, polar, spec.max_thrust_N, collective_offset_deg=0.0, tip_loss=design.aero.tip_loss, rpm_bracket=(300.0, 60000.0)
    )
    rpm = float(full.summary["rpm"])
    per_span = np.array(
        [
            s.thrust_per_span_N_m
            for s in solve_rotor(stations, replace(op, rpm=rpm), polar, tip_loss=design.aero.tip_loss, corrections=corrections).solutions
        ]
    )
    root = gmt_design.root_scale_for(design, stations, per_span, rpm * np.pi / 30.0)
    out: dict[str, Any] = {"root_thickness_scale": root, "full_throttle_rpm_static": rpm}
    if root is None:
        return out
    mass = gmt_design.prop_mass(design, stations, root)
    inertia = gmt_design.prop_inertia(design, stations, root)
    if reference is None:
        out.update({"_model_mass": mass, "_model_inertia": inertia, "_auw_delta_kg": 0.0, "_prop_inertia_delta_kg_m2": 0.0})
        return out
    cad_mass = reference["cad_mass_kg"] * mass / reference["_model_mass"]
    cad_inertia = reference["cad_inertia_kg_m2"] * inertia / reference["_model_inertia"]
    out.update(
        {
            "prop_mass_g": 1000.0 * cad_mass,
            "prop_inertia_kg_m2": cad_inertia,
            "_auw_delta_kg": spec.rotor_count * (cad_mass - reference["cad_mass_kg"]),
            "_prop_inertia_delta_kg_m2": cad_inertia - reference["cad_inertia_kg_m2"],
        }
    )
    return out


def spec_prop_inertia(design, motor: Motor) -> float:
    """Prop share of the rotor inertia (motor bell excluded)."""

    return motor.inertia_kg_m2 - design.multirotor.motor_rotor_inertia_kg_m2


def select(rows: list[dict[str, Any]], allowed: float) -> dict[str, Any] | None:
    built = next(r for r in rows if r["family"] == "built")
    limit = max(allowed, built["worst_limit_fraction"]) + 1.0e-9
    stall = max(allowed, built["worst_stall_fraction"]) + 1.0e-9
    for r in rows:
        r["feasible"] = r.get("reason") is None and r["worst_limit_fraction"] <= limit and r["worst_stall_fraction"] <= stall
    ranked = sorted((r for r in rows if r["feasible"]), key=lambda r: r["weighted_energy_Wh"])
    for r in rows:
        if "weighted_energy_Wh" in r:
            r["delta_energy_vs_built_pct"] = 100.0 * (r["weighted_energy_Wh"] / built["weighted_energy_Wh"] - 1.0)
    return ranked[0] if ranked else None


def plot(rows: list[dict[str, Any]], best: dict[str, Any] | None, missions: list[str], title: str, output) -> None:
    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import style

    style()
    gmt_rows = [r for r in rows if r["family"] == "gmt" and "weighted_energy_Wh" in r]  # twist-only family
    built = next(r for r in rows if r["family"] == "built")
    figure, axes = plt.subplots(1, 3, figsize=(19, 5.2))
    ax = axes[0]
    palette = ["#86b6ef", "#2a78d6", "#1c5cab", "#0d366b", "#0b0b0b"]
    for color, mu in zip(palette, sorted({r["bending_weight"] for r in gmt_rows}), strict=False):
        pts = sorted(
            (r for r in gmt_rows if r["bending_weight"] == mu and r.get("chord_scale", 1.0) == 1.0), key=lambda r: r["design_rpm_fraction"]
        )
        if pts:
            ax.plot(
                [r["design_rpm_fraction"] for r in pts],
                [r["delta_energy_vs_built_pct"] for r in pts],
                marker="o",
                color=color,
                label=f"GMT mu = {mu:g}",
            )
            for r in pts:
                if not r["feasible"]:
                    ax.plot(r["design_rpm_fraction"], r["delta_energy_vs_built_pct"], marker="x", color="#d6336c", markersize=9)
    planform = sorted(
        (r for r in rows if r["family"] == "planform" and "weighted_energy_Wh" in r and r["bending_weight"] == 0.0),
        key=lambda r: r["design_rpm_fraction"],
    )
    if planform:
        ax.plot(
            [r["design_rpm_fraction"] for r in planform],
            [r["delta_energy_vs_built_pct"] for r in planform],
            marker="s",
            color="#1baf7a",
            linewidth=2.0,
            label="GMT chord + twist (mu = 0)",
        )
        for r in planform:
            if not r["feasible"]:
                ax.plot(r["design_rpm_fraction"], r["delta_energy_vs_built_pct"], marker="x", color="#d6336c", markersize=9)
    if best is not None and best["family"] in ("gmt", "planform"):
        ax.plot(
            best["design_rpm_fraction"],
            best["delta_energy_vs_built_pct"],
            marker="*",
            color="#c9a227",
            markersize=18,
            label=f"selected: {best['label']}",
        )
    for r in rows:
        if r["family"] in ("mil", "helix") and "weighted_energy_Wh" in r:
            ax.axhline(
                r["delta_energy_vs_built_pct"], color="#eb6834" if r["family"] == "mil" else "#1baf7a", linestyle="--", label=r["label"]
            )
    ax.axhline(0.0, color="#52514e", linewidth=0.8)
    ax.set_xlabel("GMT design rpm / hover rpm of the design as built")
    ax.set_ylabel("mission-weighted battery energy vs as built (%)")
    ax.set_title("Energy over the missions (x: saturates or stalls more than allowed)")
    ax.legend(fontsize=8)
    ax = axes[1]
    show = [r for r in (built, best) if r is not None] + [r for r in rows if r["family"] in ("mil", "helix") and "weighted_energy_Wh" in r]
    width = 0.8 / max(len(show), 1)
    for i, r in enumerate(show):
        ax.bar(np.arange(len(missions)) + i * width, [r[f"{m}_mean_battery_power_W"] for m in missions], width=width, label=r["label"])
    ax.set_xticks(np.arange(len(missions)) + 0.4 - width / 2, missions)
    ax.set_ylabel("mean battery power (W)")
    ax.set_title("Per mission")
    ax.legend(fontsize=8)
    ax = axes[2]
    for i, r in enumerate(show):
        ax.bar(np.arange(len(missions)) + i * width, [r[f"{m}_duty_p99"] for m in missions], width=width, label=r["label"])
    ax.axhline(1.0, color="#d6336c", linewidth=1.0)
    ax.set_xticks(np.arange(len(missions)) + 0.4 - width / 2, missions)
    ax.set_ylabel("99th-percentile motor duty")
    ax.set_title("Headroom (same motor and battery)")
    figure.suptitle(title, fontsize=12)
    figure.tight_layout()
    figure.savefig(output, dpi=150)
    plt.close(figure)
