"""The ``sitl`` stage and the ``sitl-replay`` / ``sitl-optimize`` commands: glue between the pipeline and the SITL bridge."""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from prandtl_prop.sitl import missions as ms
from prandtl_prop.sitl import motor as mo
from prandtl_prop.sitl import px4
from prandtl_prop.sitl import tables as tb
from prandtl_prop.sitl import vehicle as vh
from prandtl_prop.sitl.replay import duty_histogram, replay

HEADER_SOURCE = Path(__file__).with_name("cpp") / "prandtl_rotor.hpp"


def context(design, aero_run, multirotor_run) -> dict[str, Any]:
    """Blade (as flown, trim collective included), polar, corrections, motor, battery, and vehicle."""

    from prandtl_prop import mission

    stations, polar, corrections, collective, op = mission.bemt_context(design, aero_run)
    stations = replace(stations, twist_deg=stations.twist_deg + collective)
    metrics = multirotor_run.metrics
    kv = metrics.get("motor_kv_rpm_per_V")
    if kv is None:
        raise ValueError("The multirotor stage found no feasible motor (beyond the maximum power transfer)")
    motor, battery = mo.from_vehicle(design.multirotor, float(kv), float(metrics["prop_inertia_kg_m2"]), design.sitl)
    vehicle = vh.from_specs(design.multirotor, design.sitl, op.rho_kg_m3)
    return {
        "stations": stations,
        "polar": polar,
        "corrections": corrections,
        "op": replace(op, rpm=float(metrics["hover_rpm"]), v_inf_m_s=0.0),
        "motor": motor,
        "battery": battery,
        "vehicle": vehicle,
        "hover_rpm": float(metrics["hover_rpm"]),
        "hover_thrust_N": design.multirotor.hover_thrust_N,
    }


def reference_traces(design, vehicle) -> tuple[list, list[float]]:
    spec = design.sitl
    if len(spec.mission_weights) != len(spec.missions):
        raise ValueError("sitl.mission_weights must give one weight per sitl.missions entry")
    return [ms.fly(ms.mission(name), vehicle, spec.sample_rate_hz) for name in spec.missions], list(spec.mission_weights)


def summary(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def write_truth_csv(result: dict[str, Any], trace, path: Path) -> Path:
    """A replayed mission in the truth-log format ``px4.read_truth_csv`` reads: the template for a simulator's logger."""

    s = result["_series"]
    n = s["rpm"].shape[1]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["t_s", "vx_b", "vy_b", "vz_b", "p", "q", "r", "v_bus", *(f"rpm_{i}" for i in range(n)), *(f"duty_{i}" for i in range(n))]
        )
        for k in range(len(trace.t_s)):
            writer.writerow(
                [
                    f"{trace.t_s[k]:.4f}",
                    *(f"{x:.5f}" for x in trace.v_air_body_m_s[k]),
                    *(f"{x:.5f}" for x in trace.body_rate_rad_s[k]),
                    f"{s['v_bus_V'][k]:.4f}",
                    *(f"{x:.2f}" for x in s["rpm"][k]),
                    *(f"{x:.5f}" for x in s["duty"][k]),
                ]
            )
    return path


def export(design, ctx: dict[str, Any], out: Path) -> dict[str, Any]:
    """Rotor tables (JSON + C++), the model card, PX4 parameters, and the reference-mission replays."""

    table = tb.build_table(
        design.name, ctx["stations"], ctx["op"], ctx["polar"], design.sitl, tip_loss=design.aero.tip_loss, corrections=ctx["corrections"]
    )
    table.save(out / "rotor_table.json")
    tb.write_cpp_tables(table, out / "rotor_tables.hpp")
    shutil.copyfile(HEADER_SOURCE, out / "prandtl_rotor.hpp")
    motor, battery, vehicle = ctx["motor"], ctx["battery"], ctx["vehicle"]
    fit = px4.fit_parameters(motor, table, battery, vehicle, ctx["hover_thrust_N"])
    px4.write_params(
        fit["params"],
        out / "px4_params.txt",
        f"PX4 parameters fitted to design '{design.name}' by prandtl_prop.sitl (rotor positions are layout placeholders\n"
        "until the frame is chosen). Check names against your PX4 version; load with `param load` or an airframe file.",
    )
    card = {
        "design": design.name,
        "rotor_table": "rotor_table.json",
        "motor": {**motor.__dict__},
        "battery": {
            **battery.__dict__,
            "energy_Wh": battery.energy_Wh,
            "ocv_per_cell": {"soc": mo.LIPO_SOC.tolist(), "V": mo.LIPO_OCV.tolist()},
        },
        "vehicle": {
            "mass_kg": vehicle.mass_kg,
            "inertia_kg_m2": vehicle.inertia_kg_m2.tolist(),
            "rotor_positions_m": vehicle.positions_m.tolist(),
            "spins_ccw_from_above": vehicle.spins.tolist(),
            "body_cda_m2": vehicle.body_cda_m2,
            "frames": "body FRD; rotors thrust along -z_b; spin +1 = counter-clockwise seen from above",
        },
        "px4": fit,
        "assumptions": [
            "BEMT with uniform induced velocity over the disk; reverse flow carries no load; Glauert momentum in edgewise flow",
            "vortex-ring state flagged, not modelled",
            "rigid hub: the roll moment is not relieved by flapping",
            "no rotor-rotor or rotor-arm interference (arm blockage deferred until the frame is chosen)",
            "open-loop duty-cycle ESC, ideal DC motor with lumped resistance and no-load current",
        ],
    }
    (out / "sitl_model.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    traces, weights = reference_traces(design, vehicle)
    results = [replay(t, vehicle, table, motor, battery, max_throttle=design.sitl.max_throttle) for t in traces]
    for trace, result in zip(traces, results, strict=True):
        write_truth_csv(result, trace, out / f"mission_{trace.name}.csv")
    plot_tables(table, out / "rotor_tables.png")
    plot_missions(results, out / "missions.png", design.name)
    return {"table": table, "fit": fit, "traces": traces, "weights": weights, "results": results}


def plot_tables(table: tb.RotorTable, output: Path) -> None:
    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import style

    style()
    figure, axes = plt.subplots(1, 4, figsize=(22, 4.8))
    lam, mu = table.lambda_c, table.mu
    colors = plt.get_cmap("Blues")(np.linspace(0.35, 1.0, len(mu)))
    for ax, key, label in zip(axes, ("ct", "cq", "ch", "cm"), ("C_T", "C_Q", "C_H", "C_M (hub roll)"), strict=True):
        values = getattr(table, key)[len(table.rpm) // 2]
        for j in range(0, len(mu), 2):
            ax.plot(lam, values[:, j], color=colors[j], label=f"mu = {mu[j]:g}")
        vrs = table.vrs[len(table.rpm) // 2]
        if key == "ct" and vrs.any():
            ii, jj = np.nonzero(vrs)
            ax.scatter(lam[ii], values[ii, jj], marker="x", color="#d6336c", zorder=3, label="vortex-ring state")
        ax.axvline(0.0, color="#52514e", linewidth=0.8)
        ax.set_xlabel("lambda_c = V_axial / (Omega R)  (climb > 0)")
        ax.set_ylabel(label)
        ax.set_title(f"{label} over inflow")
    axes[0].legend(fontsize=7)
    figure.suptitle(f"{table.name}: rotor tables (Reynolds-independent: exact at every rpm)" if len(table.rpm) == 1 else table.name)
    figure.tight_layout()
    figure.savefig(output, dpi=150)
    plt.close(figure)


def plot_missions(results: list[dict[str, Any]], output: Path, title: str) -> None:
    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import style

    style()
    figure, axes = plt.subplots(len(results), 3, figsize=(20, 3.6 * len(results)), squeeze=False)
    for row, r in zip(axes, results, strict=True):
        s = r["_series"]
        t = s["t_s"]
        row[0].plot(t, s["rpm"], linewidth=0.8)
        row[0].set_ylabel("rpm (each rotor)")
        row[0].set_title(f"{r['mission']}: rotor speeds")
        row[1].plot(t, s["duty"], linewidth=0.8)
        row[1].axhline(1.0, color="#d6336c", linewidth=1.0)
        row[1].set_ylabel("motor duty")
        row[1].set_title(f"duty (p99 {r['duty_p99']:.2f}, saturated {100 * r['saturated_fraction']:.2f} % of time)")
        row[2].plot(t, s["battery_power_W"], color="#0b0b0b", linewidth=0.8)
        row[2].set_ylabel("battery power (W)")
        row[2].set_title(f"{r['energy_Wh']:.2f} Wh in {r['duration_s']:.0f} s; VRS {100 * r['vrs_fraction']:.1f} % of time")
        for ax in row:
            ax.set_xlabel("time (s)")
    figure.suptitle(f"{title}: reference missions flown by the replay (frozen trajectories)", fontsize=12)
    figure.tight_layout()
    figure.savefig(output, dpi=130)
    plt.close(figure)


def histogram_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    h = duty_histogram(result)
    rows = []
    for i in range(len(h["v_axial_edges"]) - 1):
        for j in range(len(h["v_edge_edges"]) - 1):
            if h["time_fraction"][i, j] > 0:
                rows.append(
                    {
                        "mission": result["mission"],
                        "v_axial_from_m_s": h["v_axial_edges"][i],
                        "v_axial_to_m_s": h["v_axial_edges"][i + 1],
                        "v_edge_from_m_s": h["v_edge_edges"][j],
                        "v_edge_to_m_s": h["v_edge_edges"][j + 1],
                        "time_fraction": h["time_fraction"][i, j],
                        "energy_fraction": h["energy_fraction"][i, j],
                    }
                )
    return rows
