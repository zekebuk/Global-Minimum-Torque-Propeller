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


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> Path:
    keys: list[str] = []
    for row in rows:
        keys += [k for k in row if k not in keys]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_stations(path: Path, stations, radius_m: float) -> Path:
    rows = [
        {"r_R": r / radius_m, "dr_m": dr, "chord_m": c, "twist_deg": b, "airfoil": a}
        for r, dr, c, b, a in zip(stations.r_m, stations.dr_m, stations.chord_m, stations.twist_deg, stations.airfoil, strict=True)
    ]
    return _write_rows(path, rows)


def mission_study(design, store, logs: list[Path], weights: list[float] | None, output_dir: Path, *, optimize: bool, echo=print) -> dict[str, Any]:
    """Replay logs (or the reference missions) with the design as built; with ``optimize``, rank the GMT twist family on them."""

    from prandtl_prop.pipeline import run_design
    from prandtl_prop.sitl import optimize as opt

    outcomes = run_design(design, ["sitl"], store=store)
    ctx = context(design, outcomes["aero"].run, outcomes["multirotor"].run)
    table = tb.RotorTable.load(outcomes["sitl"].run.output("rotor_table.json"))
    vehicle, motor, battery = ctx["vehicle"], ctx["motor"], ctx["battery"]
    if logs:
        traces = [px4.read_log(path, table, vehicle, motor) for path in logs]
        weights = weights or [1.0] * len(traces)
    else:
        traces, default_weights = reference_traces(design, vehicle)
        weights = weights or default_weights
    if len(weights) != len(traces):
        raise ValueError(f"{len(weights)} weights for {len(traces)} missions")
    output_dir.mkdir(parents=True, exist_ok=True)
    results = [replay(t, vehicle, table, motor, battery, max_throttle=design.sitl.max_throttle) for t in traces]
    for trace, result in zip(traces, results, strict=True):
        echo(
            f"{trace.name:>14}: {result['duration_s']:7.1f} s  {result['energy_Wh']:6.2f} Wh  {result['mean_battery_power_W']:6.1f} W  "
            f"duty p99 {result['duty_p99']:.2f}  saturated {100 * result['saturated_fraction']:.2f} %  VRS {100 * result['vrs_fraction']:.1f} %  ({trace.source})"
        )
    _write_rows(output_dir / "replay.csv", [summary(r) for r in results])
    _write_rows(output_dir / "operating_histogram.csv", [row for r in results for row in histogram_rows(r)])
    plot_missions(results, output_dir / "replay.png", design.name)
    out: dict[str, Any] = {"results": results, "weights": weights}
    if not optimize:
        return out

    family = opt.candidates(design, ctx["stations"], ctx["op"], ctx["polar"], ctx["hover_rpm"], ctx["hover_thrust_N"])
    rows, tables = [], {}
    for i, candidate in enumerate(family):
        if candidate.get("stations") is None:
            rows.append({k: v for k, v in candidate.items() if k != "stations"})
            echo(f"  [{i + 1}/{len(family)}] {candidate['label']}: skipped ({candidate['reason']})")
            continue
        evaluated = opt.evaluate(candidate, design, ctx["op"], ctx["polar"], ctx["corrections"], traces, weights, vehicle, motor, battery)
        rows.append(evaluated["row"])
        tables[candidate["label"]] = (evaluated["table"], candidate["stations"])
        r = evaluated["row"]
        echo(f"  [{i + 1}/{len(family)}] {candidate['label']}: {r['weighted_energy_Wh']:.3f} Wh, worst saturated/over-current {100 * r['worst_limit_fraction']:.2f} %")
    best = opt.select(rows, design.sitl.allowed_saturation_fraction)
    _write_rows(output_dir / "candidates.csv", rows)
    names = [t.name for t in traces]
    opt.plot(rows, best, names, f"{design.name}: mission-weighted prop selection (same motor, battery, and planform)", output_dir / "candidates.png")
    out.update({"rows": rows, "best": best})
    if best is not None:
        best_table, best_stations = tables[best["label"]]
        folder = output_dir / "best"
        folder.mkdir(exist_ok=True)
        best_table.save(folder / "rotor_table.json")
        tb.write_cpp_tables(best_table, folder / "rotor_tables.hpp")
        shutil.copyfile(HEADER_SOURCE, folder / "prandtl_rotor.hpp")
        _write_stations(folder / "stations.csv", best_stations, ctx["op"].radius_m)
        fit = px4.fit_parameters(motor, best_table, battery, vehicle, ctx["hover_thrust_N"])
        px4.write_params(fit["params"], folder / "px4_params.txt", f"Mission-selected prop '{best['label']}' for design '{design.name}'.")
        echo(f"best: {best['label']} ({best['delta_energy_vs_built_pct']:+.2f} % energy vs as built); tables and PX4 params in {folder}")
    else:
        echo("no candidate meets the saturation limits")
    return out
