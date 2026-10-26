"""Energy audit: where the battery's watt-hours go, and which uncertain inputs move the answer most.

Two questions decide what to work on next:

1. **Loss breakdown** (``replay.energy_breakdown``). Over each mission, the cell energy splits into
   battery internal resistance, ESC, motor copper, motor iron and friction, rotor spin-up, rotor profile
   drag, induced excess over the actuator-disk ideal, and the ideal itself (momentum minimum for the
   thrust at that disk area, including climb work). Only the last is irreducible for the vehicle's
   mass and disk area; every other slice is a design target, ranked by size.
2. **Sensitivity** (one input at a time, a realistic uncertainty band each). Each perturbation is
   flown for the design as built and for a candidate. It reports the change in mission energy (how
   much the absolute prediction can be trusted) and the change in the candidate's advantage (whether
   the decision survives). The input that moves the decision most is where measurement or modelling
   effort pays first.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from prandtl_prop.aero.bemt import BladeStations
from prandtl_prop.aero.polars import AnalyticPolar
from prandtl_prop.sitl import missions as ms
from prandtl_prop.sitl import motors as mt
from prandtl_prop.sitl import tables as tb
from prandtl_prop.sitl.replay import replay

LOSS_KEYS = (
    "battery_internal",
    "esc",
    "motor_copper",
    "motor_iron_friction",
    "rotor_spin_up",
    "profile_drag",
    "induced_excess",
    "ideal_induced_and_climb",
)
LOSS_LABELS = {
    "battery_internal": "battery internal R",
    "esc": "ESC",
    "motor_copper": "motor copper (I^2 R)",
    "motor_iron_friction": "motor iron + friction (I0)",
    "rotor_spin_up": "rotor spin-up (net)",
    "profile_drag": "blade profile drag",
    "induced_excess": "induced excess over ideal",
    "ideal_induced_and_climb": "ideal (momentum) + climb",
}

# (label, kind, change): realistic one-at-a-time uncertainty bands.
PERTURBATIONS = (
    ("section drag cd0 +30%", "polar", {"cd0": 1.3}),
    ("section drag cd0 -30%", "polar", {"cd0": 0.7}),
    ("drag due to lift k +50%", "polar", {"k": 1.5}),
    ("lift slope -10%", "polar", {"cl_alpha_per_rad": 0.9}),
    ("Cl max 1.0 (early stall)", "polar_set", {"cl_max": 1.0}),
    ("air density -10% (hot / high)", "rho", 0.9),
    ("motor resistance +30%", "motor", {"resistance_ohm": 1.3}),
    ("motor no-load current x2", "motor", {"no_load_current_A": 2.0}),
    ("motor no-load current x0.5", "motor", {"no_load_current_A": 0.5}),
    ("all-up mass +5%", "mass", 1.05),
    ("body drag area +50%", "cda", 1.5),
    ("battery resistance x2", "battery", 2.0),
)


def stall_cl(design, polar) -> float:
    """Stall flag threshold: the design's limit, or just under the polar's Cl clamp when that is lower."""

    cl_max = getattr(polar, "cl_max", None)
    return min(design.multirotor.forward_cl_limit, cl_max - 0.05) if cl_max is not None else design.multirotor.forward_cl_limit


def _flown(design, vehicle, tables: dict[str, tb.RotorTable], motors: dict, battery, weights, cl_limit: float) -> dict[str, dict[str, Any]]:
    traces = [ms.fly(ms.mission(name), vehicle, design.sitl.sample_rate_hz) for name in design.sitl.missions]
    out = {}
    for label, table in tables.items():
        results = [replay(t, vehicle, table, motors[label], battery, cl_limit=cl_limit) for t in traces]
        out[label] = {"results": results, "energy": float(sum(w * r["energy_Wh"] for w, r in zip(weights, results, strict=True)))}
    return out


def audit(
    design,
    ctx: dict[str, Any],
    props: dict[str, BladeStations],
    motor_entry: dict[str, Any],
    prop_mass_kg: float,
    prop_inertia_kg_m2: float,
    *,
    sensitivity: bool = True,
    echo=print,
) -> dict[str, Any]:
    op, polar, corrections = ctx["op"], ctx["polar"], ctx["corrections"]
    battery = ctx["battery"]
    weights = list(design.sitl.mission_weights)
    tip_loss = design.aero.tip_loss

    def build(p, point) -> dict[str, tb.RotorTable]:
        return {
            label: tb.build_table(label, st, point, p, design.sitl, tip_loss=tip_loss, corrections=corrections)
            for label, st in props.items()
        }

    def motors_for(entry) -> dict:
        return {label: mt.motor_for(entry, design, prop_inertia_kg_m2) for label in props}

    vehicle = mt.vehicle_for(motor_entry, design, prop_mass_kg)
    tables = build(polar, op)
    base = _flown(design, vehicle, tables, motors_for(motor_entry), battery, weights, stall_cl(design, polar))
    breakdown = []
    for label, flown in base.items():
        for r in flown["results"]:
            breakdown.append({"prop": label, "mission": r["mission"], **r["energy_breakdown_Wh"]})
    echo("loss breakdown (Wh) " + " | ".join(LOSS_LABELS[k] for k in LOSS_KEYS))
    for row in breakdown:
        echo(
            f"  {row['prop']:>18} {row['mission']:>10}: " + "  ".join(f"{row[k]:6.2f}" for k in LOSS_KEYS) + f"   cells {row['cells']:6.2f}"
        )
    out: dict[str, Any] = {"breakdown": breakdown, "base": {k: v["energy"] for k, v in base.items()}}
    if not sensitivity:
        return out

    labels = list(props)
    reference, candidate = labels[0], labels[-1]
    advantage0 = 100.0 * (base[candidate]["energy"] / base[reference]["energy"] - 1.0)
    rows = []
    for name, kind, change in PERTURBATIONS:
        p, point, entry, veh, bat = polar, op, motor_entry, vehicle, battery
        if kind in ("polar", "polar_set"):
            if not isinstance(polar, AnalyticPolar):
                continue
            fields = {k: (getattr(polar, k) * v if kind == "polar" else v) for k, v in change.items()}
            p = replace(polar, **fields)
        elif kind == "rho":
            point = replace(op, rho_kg_m3=op.rho_kg_m3 * change)
            veh = replace(vehicle, rho_kg_m3=vehicle.rho_kg_m3 * change)
        elif kind == "motor":
            key, factor = next(iter(change.items()))
            entry = dict(motor_entry)
            entry[{"resistance_ohm": "resistance_ohm", "no_load_current_A": "no_load_A"}[key]] *= factor
        elif kind == "mass":
            veh = replace(vehicle, mass_kg=vehicle.mass_kg * change)
        elif kind == "cda":
            veh = replace(vehicle, body_cda_m2=vehicle.body_cda_m2 * change)
        elif kind == "battery":
            bat = replace(battery, resistance_ohm=battery.resistance_ohm * change)
        perturbed_tables = build(p, point) if kind in ("polar", "polar_set", "rho") else tables
        flown = _flown(design, veh, perturbed_tables, motors_for(entry), bat, weights, stall_cl(design, p))
        advantage = 100.0 * (flown[candidate]["energy"] / flown[reference]["energy"] - 1.0)
        row = {
            "input": name,
            f"{reference}_energy_change_pct": 100.0 * (flown[reference]["energy"] / base[reference]["energy"] - 1.0),
            f"{candidate}_energy_change_pct": 100.0 * (flown[candidate]["energy"] / base[candidate]["energy"] - 1.0),
            "candidate_advantage_pct": advantage,
            "advantage_shift_pct_points": advantage - advantage0,
            "candidate_still_better": advantage < 0.0,
            "candidate_worst_stall_fraction": max(r["stall_fraction"] for r in flown[candidate]["results"]),
            "candidate_worst_saturated_fraction": max(r["saturated_fraction"] for r in flown[candidate]["results"]),
        }
        rows.append(row)
        echo(
            f"  {name:>32}: {reference} {row[f'{reference}_energy_change_pct']:+6.1f} %, {candidate} {row[f'{candidate}_energy_change_pct']:+6.1f} %, "
            f"advantage {advantage:+6.2f} % ({row['advantage_shift_pct_points']:+5.2f} pts), stall {100 * row['candidate_worst_stall_fraction']:.1f} %"
        )
    out.update({"sensitivity": rows, "advantage_pct": advantage0, "reference": reference, "candidate": candidate})
    return out


def plot(result: dict[str, Any], title: str, output: Path) -> None:
    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import style

    style()
    colors = {
        "battery_internal": "#c9a227",
        "esc": "#e8b84a",
        "motor_copper": "#d6336c",
        "motor_iron_friction": "#f08cae",
        "rotor_spin_up": "#bdbbb6",
        "profile_drag": "#eb6834",
        "induced_excess": "#86b6ef",
        "ideal_induced_and_climb": "#1c5cab",
    }
    has_sens = "sensitivity" in result
    figure, axes = plt.subplots(1, 2 if has_sens else 1, figsize=(20 if has_sens else 11, 6.2), squeeze=False)
    ax = axes[0, 0]
    rows = result["breakdown"]
    labels = [f"{r['mission']}\n{r['prop']}" for r in rows]
    x = np.arange(len(rows))
    bottom = np.zeros(len(rows))
    for key in reversed(LOSS_KEYS):
        share = np.array([max(r[key], 0.0) / r["cells"] * 100.0 for r in rows])
        ax.bar(x, share, bottom=bottom, color=colors[key], label=LOSS_LABELS[key], edgecolor="white", linewidth=0.6)
        for xi, b, h in zip(x, bottom, share, strict=True):
            if h > 4.0:
                ax.text(xi, b + h / 2, f"{h:.0f}", ha="center", va="center", fontsize=7, color="white")
        bottom += share
    ax.set_xticks(x, labels, fontsize=8)
    ax.set_ylabel("share of the energy drawn from the cells (%)")
    ax.set_title("Where the battery energy goes")
    ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    if has_sens:
        ax = axes[0, 1]
        sens = sorted(result["sensitivity"], key=lambda r: abs(r["advantage_shift_pct_points"]))
        ref, cand = result["reference"], result["candidate"]
        y = np.arange(len(sens))
        ax.barh(y - 0.2, [r[f"{ref}_energy_change_pct"] for r in sens], height=0.4, color="#2a78d6", label=f"energy change, {ref}")
        ax.barh(
            y + 0.2, [r["advantage_shift_pct_points"] for r in sens], height=0.4, color="#eb6834", label=f"shift in {cand} advantage (pts)"
        )
        for yi, r in zip(y, sens, strict=True):
            if not r["candidate_still_better"]:
                ax.text(0, yi, "  decision flips", va="center", fontsize=8, color="#d6336c")
        ax.axvline(0.0, color="#52514e", linewidth=0.8)
        ax.set_yticks(y, [r["input"] for r in sens], fontsize=8)
        ax.set_xlabel(f"%  (baseline advantage {result['advantage_pct']:+.1f} %)")
        ax.set_title("What moves the answer (one input at a time)")
        ax.legend(fontsize=8)
    figure.suptitle(title, fontsize=12)
    figure.tight_layout()
    figure.savefig(output, dpi=150)
    plt.close(figure)
