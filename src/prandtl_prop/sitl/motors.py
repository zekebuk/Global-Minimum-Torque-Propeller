"""COTS motor selection: every catalogue motor flown with each prop through the reference missions.

What changes with the motor, from first principles:

- **Mass.** All-up mass = payload + n (motor + prop). Hover power grows as AUW^1.5 (momentum theory),
  so a heavier motor pays about 1.5 x its mass fraction in power before any efficiency gain. The
  reference missions are re-flown at each motor's AUW, and the roll and yaw inertias grow by
  4 dm a^2 and 4 dm L^2 (quad X, motors at the arm tips).
- **Losses.** Copper loss I^2 R with I = Q / Kt + I0: the motor constant Km = Kt / sqrt(R) sets the
  copper loss at a given torque, (Q / Km)^2. Iron and friction loss through the no-load current
  I0(omega) (``motor.Motor``), which at hover (~32 W shaft per rotor on the 10 in) is comparable to
  the copper loss and often larger.
- **Kv.** Sets the rpm reachable at full duty on a sagging pack (saturation in the replay) and the
  current per torque; within one stator R ~ 1/Kv^2, so Km is nearly Kv-independent.
- **Rotor inertia.** Bell inertia ~ 0.4 m (D_stator / 2 + 3 mm)^2 (about 40% of the mass in the bell at
  the magnet radius) adds to the prop's and slows the thrust response.

The static thrust-to-weight at full duty is reported at a full (4.2 V/cell) and an empty (3.5 V/cell)
pack, each sagged by the pack resistance at the motors' continuous current.

A motor is feasible on a prop when, on every mission, the time saturated stays within
``sitl.allowed_saturation_fraction``, the RMS current stays within the motor's continuous rating, and
the peak current within its peak rating. The ranking is the mission-weighted battery energy.
"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from prandtl_prop.sitl import missions as ms
from prandtl_prop.sitl import vehicle as vh
from prandtl_prop.sitl.motor import Battery, Motor
from prandtl_prop.sitl.replay import replay
from prandtl_prop.sitl.tables import RotorTable


def load_catalog(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return list(data["motors"])


def bell_inertia(mass_kg: float, stator_diameter_mm: float) -> float:
    return 0.4 * mass_kg * (0.5 * stator_diameter_mm + 3.0) ** 2 * 1.0e-6


def motor_constant(kv: float, resistance_ohm: float) -> float:
    return (60.0 / (2.0 * math.pi * kv)) / math.sqrt(resistance_ohm)


def vehicle_for(entry: dict[str, Any], design, prop_mass_kg: float) -> vh.Vehicle:
    """The vehicle with this motor: AUW re-closed, roll/yaw inertia grown by the motor's mass change at the arm tips."""

    spec, sitl = design.multirotor, design.sitl
    n = spec.rotor_count
    payload = spec.payload_mass_kg if spec.payload_mass_kg is not None else spec.mass_kg - n * (spec.motor_base_mass_kg + prop_mass_kg)
    base = vh.from_specs(spec, sitl, design.operating_point.rho_kg_m3)
    a2 = float(np.mean(base.positions_m[:, 1] ** 2))
    l2 = float(np.mean(np.sum(base.positions_m**2, axis=1)))
    dm = entry["mass_kg"] - entry.get("_reference_mass_kg", entry["mass_kg"])
    return replace(
        base, mass_kg=payload + n * (entry["mass_kg"] + prop_mass_kg), inertia_kg_m2=base.inertia_kg_m2 + n * dm * np.array([a2, a2, l2])
    )


def motor_for(entry: dict[str, Any], design, prop_inertia_kg_m2: float) -> Motor:
    spec = design.multirotor
    return Motor(
        kv_rpm_per_V=float(entry["kv"]),
        resistance_ohm=float(entry["resistance_ohm"]) + spec.esc_resistance_ohm,
        no_load_current_A=float(entry["no_load_A"]),
        inertia_kg_m2=prop_inertia_kg_m2 + bell_inertia(entry["mass_kg"], entry["stator_diameter_mm"]),
        max_current_A=float(entry["continuous_A"]),
        esc_efficiency=spec.esc_efficiency,
        active_braking=design.sitl.active_braking,
        no_load_test_V=entry.get("no_load_test_V"),
    )


def evaluate(
    entry: dict[str, Any],
    design,
    props: dict[str, tuple[RotorTable, float]],
    prop_mass_kg: float,
    battery: Battery,
    weights: list[float],
) -> list[dict[str, Any]]:
    """One motor with every prop: rows of mission energy, endurance, saturation, current, and feasibility."""

    spec, sitl = design.multirotor, design.sitl
    n = spec.rotor_count
    vehicle = vehicle_for(entry, design, prop_mass_kg)
    mass = vehicle.mass_kg
    traces = [ms.fly(ms.mission(name), vehicle, sitl.sample_rate_hz) for name in sitl.missions]
    rows = []
    for label, (table, prop_inertia) in props.items():
        motor = motor_for(entry, design, prop_inertia)
        results = [
            replay(t, vehicle, table, motor, battery, max_throttle=sitl.max_throttle, cl_limit=spec.forward_cl_limit) for t in traces
        ]
        row: dict[str, Any] = {
            "motor": entry["name"],
            "prop": label,
            "kv": entry["kv"],
            "motor_mass_g": 1000.0 * entry["mass_kg"],
            "auw_kg": mass,
            "km_Nm_per_sqrtW": motor_constant(entry["kv"], entry["resistance_ohm"]),
            "confidence": entry.get("confidence", ""),
            "weighted_energy_Wh": float(sum(w * r["energy_Wh"] for w, r in zip(weights, results, strict=True))),
        }
        reasons = []
        for r in results:
            m = r["mission"]
            s = r["_series"]
            current = np.nan_to_num(s["current_A"])
            rms = float(np.sqrt(np.mean(current**2)))
            row.update(
                {
                    f"{m}_energy_Wh": r["energy_Wh"],
                    f"{m}_mean_battery_power_W": r["mean_battery_power_W"],
                    f"{m}_saturated_fraction": r["saturated_fraction"],
                    f"{m}_duty_p99": r["duty_p99"],
                    f"{m}_peak_current_A": r["peak_motor_current_A"],
                    f"{m}_rms_current_A": rms,
                    f"{m}_stall_fraction": r["stall_fraction"],
                    f"{m}_yaw_limited_fraction": r["yaw_limited_fraction"],
                }
            )
            if r["saturated_fraction"] > sitl.allowed_saturation_fraction:
                reasons.append(f"{m}: saturated {100 * r['saturated_fraction']:.1f} %")
            if rms > entry["continuous_A"]:
                reasons.append(f"{m}: {rms:.1f} A RMS > {entry['continuous_A']} A continuous")
            if entry.get("peak_A") is not None and r["peak_motor_current_A"] > entry["peak_A"]:
                reasons.append(f"{m}: {r['peak_motor_current_A']:.0f} A peak > {entry['peak_A']} A")
        from prandtl_prop.sitl.px4 import steady_speed_vec

        for label_v, cell_V in (("full", 4.2), ("empty", 3.5)):
            v_bus = battery.cells * cell_V - battery.resistance_ohm * n * entry["continuous_A"]  # pack sag at the motors' rating
            rpm = float(steady_speed_vec(motor, table, 1.0, v_bus, 0.0, 0.0))
            row[f"static_thrust_to_weight_{label_v}"] = float(table.evaluate(rpm, 0.0, 0.0)["thrust_N"]) * n / (mass * vh.G)
        hover = next((r for r in results if r["mission"] == "hover"), None)
        if hover is not None:
            usable = battery.energy_Wh * spec.battery_usable_fraction
            row["hover_endurance_min"] = 60.0 * usable / hover["mean_battery_power_W"]
        row["worst_yaw_limited_fraction"] = max(r["yaw_limited_fraction"] for r in results)
        row["feasible"] = not reasons
        row["reason"] = "; ".join(reasons)
        rows.append(row)
    return rows


def plot(rows: list[dict[str, Any]], missions: list[str], title: str, output: Path) -> None:
    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import style

    style()
    props = list(dict.fromkeys(r["prop"] for r in rows))
    motors = list(dict.fromkeys(r["motor"] for r in rows))
    figure, axes = plt.subplots(1, 3, figsize=(21, 6.0))
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#8a63d2"]
    width = 0.8 / len(props)
    y = np.arange(len(motors))
    for k, (prop, color) in enumerate(zip(props, colors, strict=False)):
        sub = {r["motor"]: r for r in rows if r["prop"] == prop}
        energy = [sub[m]["weighted_energy_Wh"] for m in motors]
        endurance = [sub[m].get("hover_endurance_min", np.nan) for m in motors]
        duty = [max(sub[m][f"{mi}_duty_p99"] for mi in missions) for m in motors]
        for ax, values in zip(axes, (energy, endurance, duty), strict=True):
            bars = ax.barh(y + k * width, values, height=width, color=color, label=prop)
            for bar, m in zip(bars, motors, strict=True):
                if not sub[m]["feasible"]:
                    bar.set_hatch("//")
                    bar.set_alpha(0.5)
    for ax in axes:
        ax.set_yticks(y + 0.4 - width / 2, motors, fontsize=9)
        ax.invert_yaxis()
    axes[0].set_xlabel("mission-weighted battery energy (Wh)")
    axes[0].set_title("Energy over the missions (hatched: infeasible)")
    axes[0].legend(fontsize=8)
    axes[1].set_xlabel("hover endurance on the usable pack (min)")
    axes[1].set_title("Hover endurance")
    axes[2].axvline(1.0, color="#d6336c", linewidth=1.0)
    axes[2].set_xlabel("worst-mission 99th-percentile duty")
    axes[2].set_title("Throttle headroom")
    for ax in axes[1:]:
        ax.set_yticklabels([])
    figure.suptitle(title, fontsize=12)
    figure.tight_layout()
    figure.savefig(output, dpi=150)
    plt.close(figure)
