"""``prandtl design-gmt``: a global-minimum-torque prop for a multirotor, from a spec design file.

Steps:

1. **Design space.** For every (hover rpm, c_max/R) cell, build the planform (``gmt.planform_csv``
   chord shape scaled to c_max/R) and tabulate every station's thrust and torque over a twist grid
   once (the tables depend on rpm and chord only).
2. **Global minimum torque at hover, structurally closed.** The twist is the global minimum-torque
   solution at the hover thrust (prandtl_prop.aero.gmt, Cl <= ``gmt.cl_limit``). At full throttle
   each blade section carries centrifugal tension and flap bending, sigma = P/A + M/Z (NACA
   4-digit A = 0.682 c t, Z = 0.0782 c t^2, computed from the coordinates). The root may thicken
   up to ``gmt.max_root_thickness_scale`` x t/c (tapering back to the airfoil by r/R = 0.5). If
   that is not enough, the root-bending weight mu is raised (the Prandtl-bell direction) until the
   stress fits: the minimum torque for the allowable root stress.
3. **Multirotor closure.** Full throttle follows from hover by static self-similarity (T and Q scale
   with omega^2 at fixed twist when the polar does not depend on Re; exact here). The motor Kv is
   matched to reach it at full battery voltage, the motor mass follows from the peak torque, and
   the all-up mass, hover thrust, and design are iterated to a fixed point
   (prandtl_prop.multirotor). Cells that exceed the motor current, the tip Mach limit, the maximum
   power transfer, or the root stress are infeasible.
4. **Selection.** Feasible cells are ranked by ``gmt.objective``: hover torque (the motor torque,
   hence motor size and current) or hover electrical power. Candidates are taken in order until one
   also trims level flight at ``multirotor.max_speed_m_s`` without retreating-blade stall.
5. **Comparison at the selected cell:** the bell family, the unconstrained GMT, Betz/MIL, and a
   constant-pitch prop of the same planform (each at the same thrust and rpm), and the vehicle
   (hover, full throttle, forward flight, range, control) with the GMT and with the constant-pitch
   prop, each sized with its own motors.

Outputs: the stations CSV (``geometry.stations_csv``), a design YAML ready for ``prandtl run``
(with the root thickness schedule), and a report folder with ``design_map.png``,
``gmt_vs_baselines.png``, ``bell_family.png``, ``vehicle.png``, ``design_scan.csv``, and
``summary.json``.
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
from scipy.optimize import brentq

from prandtl_prop import multirotor
from prandtl_prop.aero import gmt
from prandtl_prop.aero.bemt import BladeStations, OperatingPoint, trim_rpm_to_thrust
from prandtl_prop.aero.polars import AnalyticPolar
from prandtl_prop.design import DesignCase, design_from_dict, resolve_path

SECTION_AREA = 0.682  # A / (c t), NACA 4410 (from the generated coordinates)
SECTION_MODULUS = 0.0782  # Z / (c t^2) about the chord-parallel centroidal axis (flap; conservative when pitched)
THICKENED_TO_R_R = 0.5


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


def thickness_to_chord(design: DesignCase) -> float:
    digits = "".join(ch for ch in design.gmt.airfoil if ch.isdigit())
    return int(digits[-2:]) / 100.0 if len(digits) >= 2 else 0.10


def thickness_schedule(design: DesignCase, root_scale: float) -> list[list[float]]:
    """``airfoils.thickness_scale`` for a root thickened by ``root_scale``, back to the airfoil at r/R = 0.5."""

    return [[design.gmt.root_r_R, float(root_scale)], [THICKENED_TO_R_R, 1.0], [1.0, 1.0]]


def _sections(design: DesignCase, stations: BladeStations, root_scale: float) -> tuple[np.ndarray, np.ndarray]:
    r_R = stations.r_m / design.operating_point.radius_m
    xs, ys = zip(*thickness_schedule(design, root_scale), strict=True)
    t = thickness_to_chord(design) * np.interp(r_R, xs, ys) * stations.chord_m
    return SECTION_AREA * stations.chord_m * t, SECTION_MODULUS * stations.chord_m * t**2


def prop_mass(design: DesignCase, stations: BladeStations, root_scale: float = 1.0) -> float:
    """Blades over the aerodynamic span plus the hub boss (solid cylinder, bore neglected)."""

    area, _ = _sections(design, stations, root_scale)
    rho = design.structure.material.density_kg_m3
    hub = design.hub
    blades = design.operating_point.blade_count * rho * float((area * stations.dr_m).sum())
    return blades + math.pi * (0.5 * hub.boss_diameter_m) ** 2 * hub.boss_thickness_m * rho


def prop_inertia(design: DesignCase, stations: BladeStations, root_scale: float = 1.0) -> float:
    """B * sum(rho A r^2 dr) over the aerodynamic span (hub and root tab neglected)."""

    area, _ = _sections(design, stations, root_scale)
    rho = design.structure.material.density_kg_m3
    return float(design.operating_point.blade_count * rho * (area * stations.r_m**2 * stations.dr_m).sum())


def blade_stress(
    design: DesignCase, stations: BladeStations, thrust_per_span_N_m: np.ndarray, omega_rad_s: float, root_scale: float
) -> np.ndarray:
    """sigma = P/A + M/Z at each station: centrifugal tension plus flap bending from the outboard thrust."""

    area, modulus = _sections(design, stations, root_scale)
    r, dr = stations.r_m, stations.dr_m
    rho = design.structure.material.density_kg_m3
    f = thrust_per_span_N_m * dr / design.operating_point.blade_count  # per blade, per strip
    outboard_f = np.cumsum(f[::-1])[::-1] - f
    outboard_fr = np.cumsum((f * r)[::-1])[::-1] - f * r
    moment = outboard_fr - r * outboard_f
    tension = np.cumsum((rho * area * omega_rad_s**2 * r * dr)[::-1])[::-1]
    return tension / area + moment / modulus


def root_scale_for(design: DesignCase, stations: BladeStations, thrust_per_span_N_m: np.ndarray, omega_rad_s: float) -> float | None:
    """Smallest root thickness scale that keeps every station within allowable / SF (None if beyond the cap)."""

    allowable = design.structure.material.allowable_stress_Pa / design.molding.stress_safety_factor

    def excess(scale: float) -> float:
        return float(blade_stress(design, stations, thrust_per_span_N_m, omega_rad_s, scale).max()) - allowable

    if excess(1.0) <= 0.0:
        return 1.0
    cap = design.gmt.max_root_thickness_scale
    if excess(cap) > 0.0:
        return None
    return brentq(excess, 1.0, cap, xtol=1e-4)


def _structural_gmt(design: DesignCase, tables, stations: BladeStations, thrust_N: float, omega_max: float, thrust_factor: float):
    """GMT at the lowest mu (from ``gmt.bending_weight`` up) whose full-throttle stress fits. Returns (solution, mu, root scale)."""

    def attempt(mu: float):
        solution = gmt.select_twist(
            tables, stations, thrust_N, blade_count=design.operating_point.blade_count, cl_limit=design.gmt.cl_limit, bending_weight=mu
        )
        return solution, root_scale_for(design, stations, thrust_factor * solution.thrust_per_span_N_m, omega_max)

    mu = design.gmt.bending_weight
    solution, scale = attempt(mu)
    if scale is not None:
        return solution, mu, scale
    low, high = mu, max(0.05, 2.0 * mu)
    while True:
        solution, scale = attempt(high)
        if scale is not None:
            break
        low, high = high, 2.0 * high
        if high > 50.0:
            return None, math.inf, None
    best = (solution, high, scale)
    for _ in range(18):
        mid = 0.5 * (low + high)
        solution, scale = attempt(mid)
        if scale is None:
            low = mid
        else:
            high, best = mid, (solution, mid, scale)
    return best


def _operating_point(design: DesignCase, rpm: float) -> OperatingPoint:
    op = design.operating_point
    return OperatingPoint(op.radius_m, op.blade_count, rpm, op.rho_kg_m3, op.mu_air_kg_m_s)


def _full_throttle(design, polar, stations, solution, op, hover_thrust, factor) -> tuple[float, float]:
    """(omega, torque) at factor x hover thrust: exact static self-similarity for a Re-independent polar, else a trim."""

    if isinstance(polar, AnalyticPolar):
        return op.omega_rad_s * math.sqrt(factor), factor * solution.torque_Nm
    result = trim_rpm_to_thrust(
        gmt.with_twist(stations, solution.twist_deg),
        op,
        polar,
        factor * hover_thrust,
        collective_offset_deg=0.0,
        tip_loss=design.aero.tip_loss,
        rpm_bracket=(300.0, 60000.0),
    )
    return float(result.summary["rpm"]) * math.pi / 30.0, float(result.summary["torque_Nm"])


def close_cell(design: DesignCase, polar, rpm: float, chord_max_over_R: float) -> dict[str, Any]:
    """One design-space cell: GMT twist with the structure and the vehicle mass closed. Keys "_spec"/"_stations"/"_solution" carry objects."""

    stations = planform(design, chord_max_over_R)
    op = _operating_point(design, rpm)
    radius = op.radius_m
    row: dict[str, Any] = {
        "hover_rpm": rpm,
        "chord_max_over_R": chord_max_over_R,
        "solidity": float(op.blade_count * (stations.chord_m * stations.dr_m).sum() / (math.pi * radius**2)),
        "feasible": False,
        "reason": "",
    }
    try:
        tables = gmt.station_tables(stations, op, polar, gmt.twist_grid(design.gmt.twist_step_deg), tip_loss=design.aero.tip_loss)
    except ValueError as exc:
        row["reason"] = f"aero: {exc}"
        return row
    spec = design.multirotor
    factor = spec.thrust_to_weight
    for _ in range(12):
        thrust = spec.hover_thrust_N
        omega_max_guess = op.omega_rad_s * math.sqrt(factor)
        try:
            solution, mu, scale = _structural_gmt(design, tables, stations, thrust, omega_max_guess, factor)
        except ValueError as exc:
            row["reason"] = f"aero: {exc}"
            return row
        if solution is None:
            row["reason"] = "root stress beyond the thickness cap at any bending weight"
            return row
        omega_max, torque_max = _full_throttle(design, polar, stations, solution, op, thrust, factor)
        resized = multirotor.size_vehicle(spec, torque_max, prop_mass(design, stations, scale))
        if abs(resized.mass_kg - spec.mass_kg) < 1.0e-6 or spec.payload_mass_kg is None:
            break
        spec = resized
    kv = multirotor.matched_kv(spec, torque_max, omega_max)
    omega_h = op.omega_rad_s
    power = solution.torque_Nm * omega_h
    row.update(
        {
            "mass_kg": spec.mass_kg,
            "hover_thrust_N": spec.hover_thrust_N,
            "hover_torque_Nm": solution.torque_Nm,
            "hover_shaft_power_W": power,
            "hover_figure_of_merit": spec.hover_thrust_N**1.5 / math.sqrt(2.0 * op.rho_kg_m3 * math.pi * radius**2) / power,
            "max_thrust_rpm": omega_max * 30.0 / math.pi,
            "max_thrust_torque_Nm": torque_max,
            "max_tip_mach": omega_max * radius / spec.speed_of_sound_m_s,
            "bending_weight": mu,
            "root_thickness_scale": scale,
            "max_design_cl": float(np.max(solution.cl)),
            "motor_mass_kg": multirotor.motor_mass(spec, torque_max),
            "prop_mass_kg": prop_mass(design, stations, scale),
            "motor_kv_rpm_per_V": kv,
            "_spec": spec,
            "_stations": stations,
            "_solution": solution,
        }
    )
    reasons = []
    if kv is None:
        reasons.append("beyond max power transfer")
    else:
        hover = multirotor.motor_point(spec, kv, solution.torque_Nm, omega_h)
        top = multirotor.motor_point(spec, kv, torque_max, omega_max)
        row.update({f"hover_{k}": v for k, v in hover.items()})
        row.update({f"max_thrust_{k}": v for k, v in top.items()})
        row["hover_total_electrical_power_W"] = spec.rotor_count * hover["electrical_power_W"]
        row["hover_endurance_min"] = 60.0 * spec.battery_energy_Wh * spec.battery_usable_fraction / row["hover_total_electrical_power_W"]
        if top["current_A"] > spec.motor_max_current_A:
            reasons.append(f"full-throttle current {top['current_A']:.0f} A")
    if row["max_tip_mach"] > spec.max_tip_mach:
        reasons.append(f"tip Mach {row['max_tip_mach']:.2f}")
    row["feasible"] = not reasons
    row["reason"] = "; ".join(reasons)
    return row


def forward_check(design: DesignCase, polar, row: dict[str, Any]) -> bool:
    """Trim level flight at max speed with this cell's prop; records the result and marks stall infeasible."""

    spec = row["_spec"]
    op = _operating_point(design, row["hover_rpm"])
    twisted = gmt.with_twist(row["_stations"], row["_solution"].twist_deg)
    try:
        fast = multirotor.trim_level_flight(twisted, op, polar, spec, spec.max_speed_m_s, tip_loss=design.aero.tip_loss)
    except ValueError as exc:
        row.update({"feasible": False, "reason": f"no trim at {spec.max_speed_m_s:g} m/s ({exc})"})
        return False
    row.update(
        {
            "max_speed_peak_cl": fast["peak_cl"],
            "max_speed_pitch_deg": fast["pitch_deg"],
            "max_speed_advance_ratio": fast["advance_ratio_mu"],
        }
    )
    if fast["peak_cl"] > spec.forward_cl_limit:
        row.update({"feasible": False, "reason": f"retreating-blade Cl {fast['peak_cl']:.2f} at {spec.max_speed_m_s:g} m/s"})
        return False
    return True


def scan(design: DesignCase, polar) -> list[dict[str, Any]]:
    return [close_cell(design, polar, rpm, c) for c in design.gmt.chord_max_over_R_grid for rpm in design.gmt.hover_rpm_grid]


def _objective(row: dict[str, Any], objective: str) -> float:
    if objective == "torque":
        return row["hover_torque_Nm"]
    if objective == "electrical_power":
        return row["hover_total_electrical_power_W"]
    raise ValueError(f"gmt.objective must be electrical_power or torque, not {objective!r}")


def _select(design: DesignCase, polar, rows: list[dict[str, Any]], objective: str) -> dict[str, Any]:
    for row in sorted((r for r in rows if r["feasible"]), key=lambda r: _objective(r, objective)):
        if "max_speed_peak_cl" in row or forward_check(design, polar, row):
            if row["feasible"]:
                return row
    raise ValueError("No feasible design in the scan; widen gmt.hover_rpm_grid / chord_max_over_R_grid or relax the requirements")


def _size_baseline(design, polar, stations, op, spec, inertia, m_prop):
    """Constant-pitch prop of the same planform with its own motors (its peak torque sets its AUW)."""

    for _ in range(12):
        pitch, hover = gmt.helix_for_thrust(stations, op, polar, spec.hover_thrust_N, tip_loss=design.aero.tip_loss)
        vehicle = multirotor.evaluate_vehicle(
            hover.stations, op, polar, spec, tip_loss=design.aero.tip_loss, prop_inertia_kg_m2=inertia, forward_flight=False
        )
        resized = multirotor.size_vehicle(spec, vehicle["max_thrust_torque_Nm"], m_prop)
        if abs(resized.mass_kg - spec.mass_kg) < 1.0e-6 or spec.payload_mass_kg is None:
            break
        spec = resized
    return spec, pitch, hover


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
    best = _select(design, polar, rows, design.gmt.objective)
    other = "torque" if design.gmt.objective == "electrical_power" else "electrical_power"
    best_other = _select(design, polar, rows, other)
    _write_csv(report_dir / "design_scan.csv", [_scalars(row) for row in rows])

    rpm, chord_max = best["hover_rpm"], best["chord_max_over_R"]
    sized, stations, mu = best["_spec"], best["_stations"], best["bending_weight"]
    op = _operating_point(design, rpm)
    thrust, factor = sized.hover_thrust_N, sized.thrust_to_weight
    omega_max = op.omega_rad_s * math.sqrt(factor)
    family = sorted(set(design.gmt.bending_family) | {mu, 0.0})
    comparison = gmt.compare(
        stations, op, polar, thrust, cl_limit=design.gmt.cl_limit, bending_weights=tuple(family), tip_loss=design.aero.tip_loss
    )
    chosen, chosen_result = comparison["gmt"][mu]
    loads = np.array([s_.thrust_per_span_N_m for s_ in chosen_result.solutions])
    scale = root_scale_for(design, stations, factor * loads, omega_max)
    structure_note = ""
    if scale is None:  # the refined twist moved the stress over the cap by a hair: keep the scan's scale and say so
        scale, structure_note = best["root_thickness_scale"], "refined twist exceeds the root stress at the cap; scan scale kept"
    inertia, m_prop = prop_inertia(design, stations, scale), prop_mass(design, stations, scale)
    gmt_vehicle = multirotor.evaluate_vehicle(
        gmt.with_twist(stations, chosen.twist_deg), op, polar, sized, tip_loss=design.aero.tip_loss, prop_inertia_kg_m2=inertia
    )
    helix_spec, pitch, helix_hover = _size_baseline(design, polar, stations, op, sized, inertia, m_prop)
    helix_vehicle = multirotor.evaluate_vehicle(
        helix_hover.stations, op, polar, helix_spec, tip_loss=design.aero.tip_loss, prop_inertia_kg_m2=inertia
    )
    vehicles = {"gmt": gmt_vehicle, "helix": helix_vehicle}
    masses = {"gmt": sized.mass_kg, "helix": helix_spec.mass_kg}

    def stress_summary(result) -> dict[str, Any]:
        per_span = factor * np.array([s_.thrust_per_span_N_m for s_ in result.solutions])
        needed = root_scale_for(design, stations, per_span, omega_max)
        return {
            "root_scale_needed": needed,
            "peak_stress_at_design_scale_MPa": float(blade_stress(design, stations, per_span, omega_max, scale).max()) / 1e6,
        }

    csv_path = resolve_path(design.geometry.stations_csv)
    _write_stations(csv_path, design, chosen_result, chosen, chord_max)
    output = copy.deepcopy(data)
    output["name"] = output_design.stem
    output.setdefault("operating_point", {}).update({"rpm": float(rpm), "target_thrust_N": float(thrust)})
    output.setdefault("multirotor", {})["mass_kg"] = float(sized.mass_kg)  # closed all-up mass
    output.setdefault("airfoils", {})["thickness_scale"] = thickness_schedule(design, scale) if scale > 1.0 else []
    output.setdefault("gmt", {})["bending_weight"] = float(mu)  # the weight the selected design needed
    if "mission" in output and output["mission"].get("thrust_N"):
        output["mission"]["thrust_N"] = [float(thrust), float(sized.max_thrust_N)]  # hover and full throttle
    header = (
        f"# Generated by `prandtl design-gmt {spec_file}`: global-minimum-torque twist at hover\n"
        f"# ({thrust:.3f} N per rotor at {rpm:.0f} rpm, c_max/R = {chord_max:.2f}, mu = {mu:.3g}). Edit the spec, not this file.\n"
    )
    output_design.write_text(header + yaml.safe_dump(output, sort_keys=False), encoding="utf-8")

    r_root = float(stations.r_m[0])
    summary = {
        "selected": {"objective": design.gmt.objective, **_scalars(best)},
        f"best_by_{other}": _scalars(best_other),
        "structure": {
            "root_thickness_scale": scale,
            "allowable_over_safety_factor_MPa": design.structure.material.allowable_stress_Pa / design.molding.stress_safety_factor / 1e6,
            "full_throttle_rpm": omega_max * 30.0 / math.pi,
            "note": structure_note,
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
                    **stress_summary(result),
                }
                for name, result in (
                    ("gmt", chosen_result),
                    ("gmt_unconstrained", comparison["gmt"][0.0][1]),
                    ("mil", comparison["mil"][1]),
                    ("helix", comparison["helix"][1]),
                )
            },
            "helix_pitch_over_diameter": comparison["helix"][0] / (2.0 * op.radius_m),
        },
        "bell_family": [
            {
                "bending_weight": m,
                "torque_Nm": float(result.summary["torque_Nm"]),
                "root_moment_Nm": gmt.root_moment(result, r_root),
                "center_of_thrust_mu": float(result.summary["center_of_thrust_mu"]),
            }
            for m, (_, result) in sorted(comparison["gmt"].items())
        ],
        "vehicle": {
            name: {"mass_kg": masses[name], **{k: v for k, v in vehicle.items() if k != "forward_flight"}}
            for name, vehicle in vehicles.items()
        },
        "helix_vehicle_pitch_over_diameter": pitch / (2.0 * op.radius_m),
        "prop_inertia_estimate_kg_m2": inertia,
        "prop_mass_estimate_kg": m_prop,
        "stations_csv": str(design.geometry.stations_csv),
        "design_file": str(output_design),
    }
    (report_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float), encoding="utf-8")
    for name, vehicle in vehicles.items():
        _write_csv(report_dir / f"forward_flight_{name}.csv", vehicle.get("forward_flight", []))
    _plot_map(rows, best, best_other, design, report_dir / "design_map.png")
    _plot_baselines(comparison, mu, design, op, report_dir / "gmt_vs_baselines.png")
    _plot_bell(summary["bell_family"], comparison, op, report_dir / "bell_family.png")
    multirotor.plot_forward_flight(
        {f"GMT ({masses['gmt']:.3f} kg)": vehicles["gmt"], f"constant pitch ({masses['helix']:.3f} kg)": vehicles["helix"]},
        sized,
        f"{design.name}: {sized.rotor_count}-rotor vehicle, each prop with its own torque-sized motors",
        report_dir / "vehicle.png",
    )
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
    figure, axes = plt.subplots(1, 3, figsize=(21, 5.4))
    for ax, key, label, scale in (
        (axes[0], "hover_total_electrical_power_W", "hover electrical power, all rotors (W)", 1.0),
        (axes[1], "hover_torque_Nm", "hover torque per rotor (mN m)", 1000.0),
        (axes[2], "bending_weight", "root-bending weight mu needed (0: unconstrained GMT)", 1.0),
    ):
        values = np.array([row.get(key, np.nan) for row in rows], dtype=float).reshape(shape) * scale
        shown = np.ma.array(values, mask=~feasible | ~np.isfinite(values))
        mesh = ax.pcolormesh(rpms, chords, shown, cmap=sequential_cmap("blue"), shading="nearest")
        levels = np.nanpercentile(shown.compressed(), [5, 20, 40, 60, 80]) if shown.count() > 5 else None
        if levels is not None and np.unique(levels).size > 1:
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
    axes[2].set_title("Bell weight the root stress needs")
    figure.suptitle(
        f"{design.name}: design space, GMT twist and matched motor in every cell (T/W {design.multirotor.thrust_to_weight:g})", fontsize=12
    )
    figure.tight_layout()
    figure.savefig(output, dpi=150)
    plt.close(figure)


def _plot_baselines(comparison, mu: float, design: DesignCase, op: OperatingPoint, output: Path) -> None:
    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import MUTED, SERIES, style

    style()
    cases = [(f"GMT, root stress closed (mu = {mu:.3g})", comparison["gmt"][mu][1], SERIES[0], "-")]
    if mu > 0.0:
        cases.append(("GMT, unconstrained (mu = 0)", comparison["gmt"][0.0][1], MUTED, "-."))
    cases += [("Betz / MIL", comparison["mil"][1], SERIES[1], "--"), ("constant pitch", comparison["helix"][1], SERIES[2], ":")]
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
        ("chord (mm)", "Chord (same planform for all)"),
    )
    for ax, (ylabel, title) in zip(axes.ravel(), titles, strict=True):
        ax.set_xlabel("r / R")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
    axes[0, 0].legend(loc="upper right", fontsize=8)
    figure.suptitle(
        f"{design.name}: same planform, {op.rpm:.0f} rpm, {cases[0][1].summary['thrust_N']:.2f} N per rotor (B = {op.blade_count})",
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
