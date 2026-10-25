"""``prandtl design-gmt``: a global-minimum-torque prop for a multirotor, from a spec design file.

Steps (all at the hover thrust per rotor, W / n_rotors):

1. **Design space.** For every (hover rpm, c_max/R) cell, build the planform (``gmt.planform_csv``
   chord shape scaled to c_max/R) and give it the global-minimum-torque twist at hover
   (prandtl_prop.aero.gmt, Cl <= ``gmt.cl_limit``).
2. **Multirotor match.** With that fixed-pitch prop, find the full-throttle rpm for the
   thrust-to-weight requirement, match the motor Kv to it, and evaluate the hover electrical power
   (prandtl_prop.multirotor). Cells that exceed the motor current, the tip Mach limit, or the
   maximum power transfer are infeasible.
3. **Selection.** The feasible cell with the lowest ``gmt.objective`` (hover electrical power by
   default, or hover torque) is the design. At a fixed rpm, minimum torque is also minimum shaft
   power and minimum motor current, so the GMT twist is optimal for either objective; the
   objectives differ only in the rpm and chord they pick.
4. **Comparison at the selected cell:** the bell family (root-bending weight mu), Betz/MIL, and a
   constant-pitch prop of the same planform, each at the same thrust and rpm, and the vehicle
   (hover, full throttle, forward flight, range, control) with the GMT and the constant-pitch prop.

Outputs: the stations CSV (``geometry.stations_csv``), a design YAML ready for ``prandtl run``,
and a report folder with ``design_map.png``, ``gmt_vs_baselines.png``, ``bell_family.png``,
``vehicle.png``, ``design_scan.csv``, and ``summary.json``.
"""

from __future__ import annotations

import copy
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from prandtl_prop import multirotor
from prandtl_prop.aero import gmt
from prandtl_prop.aero.bemt import BladeStations, OperatingPoint
from prandtl_prop.design import DesignCase, design_from_dict, resolve_path


def stations_r_R(spec) -> np.ndarray:
    """Sine-spaced stations from the root to just inside the tip (denser where the tip loss acts)."""

    s = np.linspace(0.0, 0.98, spec.station_count)
    return spec.root_r_R + (1.0 - spec.root_r_R) * np.sin(0.5 * math.pi * s)


def planform(design: DesignCase, chord_max_over_R: float) -> BladeStations:
    spec, radius = design.gmt, design.operating_point.radius_m
    with resolve_path(spec.planform_csv).open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    mu = np.array([float(row.get("mu") or row["r_R"]) for row in rows])
    chord = np.array([float(row["chord_over_R"]) for row in rows])
    x = stations_r_R(spec)
    edges = np.concatenate([[spec.root_r_R], 0.5 * (x[1:] + x[:-1]), [1.0]])
    return BladeStations(
        r_m=x * radius,
        dr_m=np.diff(edges) * radius,
        chord_m=chord_max_over_R * radius * np.interp(x, mu, chord / chord.max()),
        twist_deg=np.zeros_like(x),
        airfoil=(spec.airfoil,) * len(x),
    )


def prop_inertia(design: DesignCase, stations: BladeStations) -> float:
    """B * sum(rho A r^2 dr) with the NACA 4-digit section area 0.685 t c (hub and root tab neglected)."""

    digits = "".join(ch for ch in design.gmt.airfoil if ch.isdigit())
    t_c = int(digits[-2:]) / 100.0 if len(digits) >= 2 else 0.10
    area = 0.685 * t_c * stations.chord_m**2
    rho = design.structure.material.density_kg_m3
    return float(design.operating_point.blade_count * (rho * area * stations.r_m**2 * stations.dr_m).sum())


def _operating_point(design: DesignCase, rpm: float) -> OperatingPoint:
    op = design.operating_point
    return OperatingPoint(op.radius_m, op.blade_count, rpm, op.rho_kg_m3, op.mu_air_kg_m_s)


def evaluate_cell(design: DesignCase, polar, rpm: float, chord_max_over_R: float) -> dict[str, Any]:
    vehicle_spec = design.multirotor
    stations = planform(design, chord_max_over_R)
    op = _operating_point(design, rpm)
    row: dict[str, Any] = {"hover_rpm": rpm, "chord_max_over_R": chord_max_over_R, "solidity": 0.0, "feasible": False, "reason": ""}
    row["solidity"] = float(op.blade_count * (stations.chord_m * stations.dr_m).sum() / (math.pi * op.radius_m**2))
    try:
        solution = gmt.design(
            stations, op, polar, vehicle_spec.hover_thrust_N, cl_limit=design.gmt.cl_limit, bending_weight=design.gmt.bending_weight
        )
    except ValueError as exc:
        row["reason"] = f"aero: {exc}"
        return row
    twisted = gmt.with_twist(stations, solution.twist_deg)
    vehicle = multirotor.evaluate_vehicle(
        twisted,
        op,
        polar,
        vehicle_spec,
        tip_loss=design.aero.tip_loss,
        prop_inertia_kg_m2=prop_inertia(design, stations),
        forward_flight=False,
    )
    row.update({key: value for key, value in vehicle.items() if isinstance(value, int | float | bool)})
    reasons = []
    if not vehicle["feasible_power_transfer"]:
        reasons.append("beyond max power transfer")
    else:
        if not vehicle["current_within_limit"]:
            reasons.append(f"full-throttle current {vehicle['max_thrust_current_A']:.0f} A")
        if not vehicle["tip_mach_within_limit"]:
            reasons.append(f"tip Mach {vehicle['max_tip_mach']:.2f}")
    row["feasible"] = not reasons
    row["reason"] = "; ".join(reasons)
    row["max_design_cl"] = float(np.max(solution.cl))
    return row


def scan(design: DesignCase, polar) -> list[dict[str, Any]]:
    return [evaluate_cell(design, polar, rpm, c) for c in design.gmt.chord_max_over_R_grid for rpm in design.gmt.hover_rpm_grid]


def _objective(row: dict[str, Any], objective: str) -> float:
    if objective == "torque":
        return row["hover_torque_Nm"]
    if objective == "electrical_power":
        return row["hover_total_electrical_power_W"]
    raise ValueError(f"gmt.objective must be electrical_power or torque, not {objective!r}")


def design_gmt(spec_file: Path, *, output_design: Path, report_dir: Path, overrides: list[str] | None = None) -> dict[str, Any]:
    from prandtl_prop import pipeline
    from prandtl_prop.design import apply_override

    data = yaml.safe_load(Path(spec_file).read_text(encoding="utf-8")) or {}
    for override in overrides or []:
        apply_override(data, override)
    design = design_from_dict(data)
    polar = pipeline._polar(design)
    report_dir.mkdir(parents=True, exist_ok=True)

    rows = scan(design, polar)
    feasible = [row for row in rows if row["feasible"]]
    if not feasible:
        raise ValueError("No feasible design in the scan; widen gmt.hover_rpm_grid / chord_max_over_R_grid or relax the requirements")
    best = min(feasible, key=lambda row: _objective(row, design.gmt.objective))
    other = "torque" if design.gmt.objective == "electrical_power" else "electrical_power"
    best_other = min(feasible, key=lambda row: _objective(row, other))
    _write_csv(report_dir / "design_scan.csv", rows)

    rpm, chord_max = best["hover_rpm"], best["chord_max_over_R"]
    stations = planform(design, chord_max)
    op = _operating_point(design, rpm)
    thrust = design.multirotor.hover_thrust_N
    family = sorted(set(design.gmt.bending_family) | {design.gmt.bending_weight})
    comparison = gmt.compare(
        stations, op, polar, thrust, cl_limit=design.gmt.cl_limit, bending_weights=tuple(family), tip_loss=design.aero.tip_loss
    )
    chosen, chosen_result = comparison["gmt"][design.gmt.bending_weight]
    pitch, helix_result = comparison["helix"]
    inertia = prop_inertia(design, stations)
    vehicles = {
        "gmt": multirotor.evaluate_vehicle(
            gmt.with_twist(stations, chosen.twist_deg),
            op,
            polar,
            design.multirotor,
            tip_loss=design.aero.tip_loss,
            prop_inertia_kg_m2=inertia,
        ),
        "helix": multirotor.evaluate_vehicle(
            helix_result.stations, op, polar, design.multirotor, tip_loss=design.aero.tip_loss, prop_inertia_kg_m2=inertia
        ),
    }

    csv_path = resolve_path(design.geometry.stations_csv)
    _write_stations(csv_path, design, chosen_result, chosen, chord_max)
    output = copy.deepcopy(data)
    output["name"] = output_design.stem
    output.setdefault("operating_point", {}).update({"rpm": float(rpm), "target_thrust_N": float(thrust)})
    header = (
        f"# Generated by `prandtl design-gmt {spec_file}`: global-minimum-torque twist at hover\n"
        f"# ({thrust:.3f} N per rotor at {rpm:.0f} rpm, c_max/R = {chord_max:.2f}). Edit the spec, not this file.\n"
    )
    output_design.write_text(header + yaml.safe_dump(output, sort_keys=False), encoding="utf-8")

    r_root = float(stations.r_m[0])
    summary = {
        "selected": {"hover_rpm": rpm, "chord_max_over_R": chord_max, "objective": design.gmt.objective, **_scalars(best)},
        f"best_by_{other}": {
            "hover_rpm": best_other["hover_rpm"],
            "chord_max_over_R": best_other["chord_max_over_R"],
            **_scalars(best_other),
        },
        "same_cell_comparison": {
            "thrust_N": thrust,
            "rpm": rpm,
            **{
                name: {
                    "torque_Nm": float(result.summary["torque_Nm"]),
                    "power_W": float(result.summary["power_W"]),
                    "figure_of_merit": float(result.summary["figure_of_merit"]),
                    "root_moment_Nm": gmt.root_moment(result, r_root),
                }
                for name, result in (("gmt", chosen_result), ("mil", comparison["mil"][1]), ("helix", helix_result))
            },
            "helix_pitch_over_diameter": pitch / (2.0 * op.radius_m),
        },
        "bell_family": [
            {
                "bending_weight": mu,
                "torque_Nm": float(result.summary["torque_Nm"]),
                "root_moment_Nm": gmt.root_moment(result, r_root),
                "center_of_thrust_mu": float(result.summary["center_of_thrust_mu"]),
            }
            for mu, (_, result) in sorted(comparison["gmt"].items())
        ],
        "vehicle": {name: {k: v for k, v in vehicle.items() if k != "forward_flight"} for name, vehicle in vehicles.items()},
        "prop_inertia_estimate_kg_m2": inertia,
        "stations_csv": str(design.geometry.stations_csv),
        "design_file": str(output_design),
    }
    (report_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float), encoding="utf-8")
    for name, vehicle in vehicles.items():
        _write_csv(report_dir / f"forward_flight_{name}.csv", vehicle.get("forward_flight", []))
    _plot_map(rows, best, best_other, design, report_dir / "design_map.png")
    _plot_baselines(comparison, design, op, report_dir / "gmt_vs_baselines.png")
    _plot_bell(summary["bell_family"], comparison, op, report_dir / "bell_family.png")
    _plot_vehicle(vehicles, design, report_dir / "vehicle.png")
    return summary


def _scalars(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if isinstance(v, int | float | bool | str)}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    keys: list[str] = []
    for row in rows:
        keys += [k for k in row if k not in keys and not isinstance(row[k], list | dict)]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_stations(path: Path, design: DesignCase, result, solution: gmt.GmtSolution, chord_max: float) -> None:
    radius = design.operating_point.radius_m
    st = result.stations
    twist = st.twist_deg + result.collective_offset_deg  # fold the (tiny) exact-thrust trim into the geometry
    rows = []
    for i, s in enumerate(result.solutions):
        rows.append(
            {
                "case": design.name,
                "mu": float(st.r_m[i] / radius),
                "radius_m": float(st.r_m[i]),
                "dr_m": float(st.dr_m[i]),
                "chord_m": float(st.chord_m[i]),
                "chord_over_R": float(st.chord_m[i] / radius),
                "airfoil": st.airfoil[i],
                "twist_deg": float(twist[i]),
                "alpha_design_deg": s.alpha_deg,
                "phi_design_deg": math.degrees(s.phi_rad),
                "cl_design": s.cl,
                "design_method": f"global minimum torque (separable BEMT Lagrangian), lambda = {solution.lambda_m:.6g} m, mu = {solution.bending_weight:g}",
                "notes": f"c_max/R = {chord_max:.3f}; hover {result.operating_point.rpm:.0f} rpm",
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(path, rows)


# ------------------------------------------------------------------------------------------ plots


def _plot_map(rows, best, best_other, design: DesignCase, output: Path) -> None:
    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import INK_2, MUTED, SERIES, sequential_cmap, style

    style()
    rpms = np.array(design.gmt.hover_rpm_grid)
    chords = np.array(design.gmt.chord_max_over_R_grid)
    shape = (len(chords), len(rpms))
    feasible = np.array([row["feasible"] for row in rows]).reshape(shape)
    figure, axes = plt.subplots(1, 2, figsize=(14, 5.4))
    for ax, key, label, scale in (
        (axes[0], "hover_total_electrical_power_W", "hover electrical power, all rotors (W)", 1.0),
        (axes[1], "hover_torque_Nm", "hover torque per rotor (mN m)", 1000.0),
    ):
        values = np.array([row.get(key, np.nan) for row in rows], dtype=float).reshape(shape) * scale
        shown = np.ma.array(values, mask=~feasible | ~np.isfinite(values))
        mesh = ax.pcolormesh(rpms, chords, shown, cmap=sequential_cmap("blue"), shading="nearest")
        levels = np.nanpercentile(shown.compressed(), [5, 20, 40, 60, 80]) if shown.count() > 5 else None
        if levels is not None:
            contour = ax.contour(rpms, chords, shown, levels=np.unique(levels), colors=INK_2, linewidths=0.7)
            ax.clabel(contour, fmt="%.1f", fontsize=7)
        ax.pcolormesh(rpms, chords, np.ma.array(np.ones(shape), mask=feasible), cmap="Greys", vmin=0, vmax=4, shading="nearest", alpha=0.5)
        figure.colorbar(mesh, ax=ax, label=label)
        ax.plot(
            best["hover_rpm"],
            best["chord_max_over_R"],
            marker="*",
            markersize=16,
            color=SERIES[1],
            markeredgecolor="white",
            label="selected",
        )
        ax.plot(
            best_other["hover_rpm"],
            best_other["chord_max_over_R"],
            marker="D",
            markersize=8,
            color=MUTED,
            markeredgecolor="white",
            label="best by the other objective",
        )
        ax.set_xlabel("hover rpm (design point)")
        ax.set_ylabel("c_max / R")
        ax.legend(loc="upper right", fontsize=8)
    axes[0].set_title("Hover electrical power (grey: infeasible)")
    axes[1].set_title("Hover torque with the GMT twist")
    figure.suptitle(
        f"{design.name}: design space, GMT twist and matched motor in every cell (T/W {design.multirotor.thrust_to_weight:g})", fontsize=12
    )
    figure.tight_layout()
    figure.savefig(output, dpi=150)
    plt.close(figure)


def _plot_baselines(comparison, design: DesignCase, op: OperatingPoint, output: Path) -> None:
    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import SERIES, style

    style()
    chosen = comparison["gmt"][design.gmt.bending_weight][1]
    cases = (
        ("GMT (global min torque)", chosen, SERIES[0], "-"),
        ("Betz / MIL", comparison["mil"][1], SERIES[1], "--"),
        ("constant pitch", comparison["helix"][1], SERIES[2], ":"),
    )
    figure, axes = plt.subplots(2, 3, figsize=(16, 8.4))
    for name, result, color, line in cases:
        st = result.stations
        x = st.r_m / op.radius_m
        twist = st.twist_deg + result.collective_offset_deg
        sol = result.solutions
        q = result.summary
        label = f"{name}: Q = {q['torque_Nm'] * 1000:.2f} mN m"
        axes[0, 0].plot(x, twist, color=color, linestyle=line, label=label)
        axes[0, 1].plot(x, [s.alpha_deg for s in sol], color=color, linestyle=line)
        axes[0, 2].plot(x, [s.cl for s in sol], color=color, linestyle=line)
        axes[1, 0].plot(x, [s.thrust_per_span_N_m for s in sol], color=color, linestyle=line)
        axes[1, 1].plot(x, [s.tangential_per_span_N_m * r for s, r in zip(sol, st.r_m, strict=True)], color=color, linestyle=line)
        axes[1, 2].plot(x, st.chord_m * 1000.0, color=color, linestyle=line)
    titles = (
        ("twist beta (deg)", "Twist"),
        ("alpha (deg)", "Angle of attack"),
        ("Cl", f"Lift coefficient (limit {design.gmt.cl_limit:g})"),
        ("dT/dr, all blades (N/m)", "Thrust loading"),
        ("dQ/dr, all blades (N m/m)", "Torque loading"),
        ("chord (mm)", "Chord (same planform for all three)"),
    )
    for ax, (ylabel, title) in zip(axes.ravel(), titles, strict=True):
        ax.set_xlabel("r / R")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
    axes[0, 0].legend(loc="upper right", fontsize=8)
    figure.suptitle(
        f"{design.name}: same planform, {op.rpm:.0f} rpm, {design.multirotor.hover_thrust_N:.2f} N per rotor (B = {op.blade_count})",
        fontsize=12,
    )
    figure.tight_layout()
    figure.savefig(output, dpi=150)
    plt.close(figure)


def _plot_bell(family, comparison, op: OperatingPoint, output: Path) -> None:
    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import ordinal, style

    style()
    base = family[0]
    figure, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    colors = ordinal(len(family))
    dq = [100.0 * (row["torque_Nm"] / base["torque_Nm"] - 1.0) for row in family]
    dm = [100.0 * (row["root_moment_Nm"] / base["root_moment_Nm"] - 1.0) for row in family]
    axes[0].plot(dm, dq, color="#52514e", linewidth=1.0, zorder=1)
    for row, x_, y_, color in zip(family, dm, dq, colors, strict=True):
        axes[0].scatter([x_], [y_], color=color, s=50, zorder=2, label=f"mu = {row['bending_weight']:g}")
    axes[0].set_xlabel("root flap moment change (%)")
    axes[0].set_ylabel("hover torque change (%)")
    axes[0].set_title("Bell family: torque cost of unloading the blade root")
    axes[0].legend(fontsize=8)
    for (mu, (_, result)), color in zip(sorted(comparison["gmt"].items()), colors, strict=True):
        st = result.stations
        axes[1].plot(st.r_m / op.radius_m, [s.thrust_per_span_N_m for s in result.solutions], color=color, label=f"mu = {mu:g}")
    axes[1].set_xlabel("r / R")
    axes[1].set_ylabel("dT/dr, all blades (N/m)")
    axes[1].set_title("Thrust loading moves inboard as mu grows")
    figure.tight_layout()
    figure.savefig(output, dpi=150)
    plt.close(figure)


def _plot_vehicle(vehicles, design: DesignCase, output: Path) -> None:
    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import SERIES, style

    style()
    figure, axes = plt.subplots(1, 3, figsize=(16, 4.6))
    for (name, vehicle), color, line in zip(vehicles.items(), SERIES, ("-", "--"), strict=False):
        rows = vehicle.get("forward_flight", [])
        v = [row["v_m_s"] for row in rows]
        label = {"gmt": "GMT", "helix": "constant pitch"}[name]
        axes[0].plot(v, [row["total_electrical_power_W"] for row in rows], color=color, linestyle=line, marker="o", label=label)
        axes[1].plot(v, [row["range_km"] for row in rows], color=color, linestyle=line, marker="o", label=label)
        axes[2].plot(v, [row["pitch_deg"] for row in rows], color=color, linestyle=line, marker="o", label=label)
    axes[0].set_title("Battery power in level flight (all rotors)")
    axes[0].set_ylabel("power (W)")
    axes[1].set_title(f"Range on {design.multirotor.battery_energy_Wh * design.multirotor.battery_usable_fraction:.0f} Wh usable")
    axes[1].set_ylabel("range (km)")
    axes[2].set_title("Body pitch to trim")
    axes[2].set_ylabel("nose-down pitch (deg)")
    for ax in axes:
        ax.set_xlabel("flight speed (m/s)")
        ax.legend(fontsize=8)
    figure.suptitle(
        f"{design.name}: {design.multirotor.rotor_count}-rotor vehicle, {design.multirotor.mass_kg:g} kg, matched motors", fontsize=12
    )
    figure.tight_layout()
    figure.savefig(output, dpi=150)
    plt.close(figure)
