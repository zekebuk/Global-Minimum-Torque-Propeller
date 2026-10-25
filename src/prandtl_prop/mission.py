"""Off-design mission envelope of a fixed-pitch prop in axial flight (static to cruise).

Assumptions (first principles):

- Axial free stream V along the rotor axis: vertical climb, or a tilt-rotor or tail-sitter in
  forward flight. Edgewise flow (multirotor cruise) is a different problem (unsteady, skewed wake)
  and is not modelled here.
- Fixed pitch: the blade geometry, including the collective set by the design-point trim, is
  frozen. At every other point the controller changes rpm only, so each mission point (V, T) is
  solved by trimming rpm. Momentum: dT = 4 pi rho r F (V + v_i) v_i dr. Sections that unload at
  high advance ratio use the windmill-brake branch (v_i < 0, V + 2 v_i > 0).
- Off-design health per point: rpm against the motor limit, sections at or past the stall Cl,
  sections with negative loading, advance ratio J = V / (n D), propulsive efficiency T V / P, and
  in hover the figure of merit.
"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from prandtl_prop.aero.bemt import BemtResult, BladeStations, solve_rotor, trim_rpm_to_thrust


def bemt_context(design, aero_run):
    """Stations, polar, corrections, collective and operating point exactly as the design-point aero stage used them."""

    from prandtl_prop import pipeline
    from prandtl_prop.design import resolve_path

    op = pipeline._operating_point(design)
    stations = BladeStations.from_rows(pipeline._read_csv(resolve_path(design.geometry.stations_csv)), op.radius_m)
    corrections = pipeline._corrections(design, stations, op.radius_m)
    thickness = pipeline._thickness_drag_factors(design, stations, op.radius_m)
    if thickness is not None:
        lift, drag = corrections if corrections is not None else (np.ones(len(stations.r_m)), np.ones(len(stations.r_m)))
        corrections = (lift, drag * thickness)
    collective = float(aero_run.metrics.get("collective_offset_deg", 0.0))
    return stations, pipeline._polar(design), corrections, collective, op


def envelope(design, aero_run) -> dict[str, Any]:
    """Performance map over (V, rpm) and the rpm-trimmed mission points, all at the frozen geometry."""

    stations, polar, corrections, collective, op = bemt_context(design, aero_run)
    spec = design.mission
    tip_loss = design.aero.tip_loss
    grid = []
    for v in spec.map_v_inf_m_s:
        for rpm in spec.map_rpm:
            result = solve_rotor(
                stations,
                replace(op, rpm=rpm, v_inf_m_s=v),
                polar,
                collective_offset_deg=collective,
                tip_loss=tip_loss,
                corrections=corrections,
            )
            grid.append(_row(result, spec.stall_cl))
    points: list[dict[str, Any]] = []
    solutions: list[BemtResult] = []
    target = float(design.operating_point.target_thrust_N)
    for index, (v, thrust) in enumerate(spec.points(target)):
        result = trim_rpm_to_thrust(
            stations, replace(op, v_inf_m_s=v), polar, thrust, collective_offset_deg=collective, tip_loss=tip_loss, corrections=corrections
        )
        row = _row(result, spec.stall_cl)
        row.update(
            {"point": index, "name": point_name(v, thrust), "target_thrust_N": thrust, "rpm_within_limit": row["rpm"] <= spec.max_rpm}
        )
        points.append(row)
        solutions.append(result)
    return {"grid": grid, "points": points, "solutions": solutions, "collective_offset_deg": collective, "radius_m": op.radius_m}


def point_name(v_inf_m_s: float, thrust_N: float) -> str:
    return f"V{v_inf_m_s:04.1f}_T{thrust_N:04.1f}".replace(".", "p")


def _row(result: BemtResult, stall_cl: float) -> dict[str, Any]:
    s = result.summary
    cl = np.array([sol.cl for sol in result.solutions])
    alpha = np.array([sol.alpha_deg for sol in result.solutions])
    return {
        "v_inf_m_s": float(s["v_inf_m_s"]),
        "rpm": float(s["rpm"]),
        "thrust_N": float(s["thrust_N"]),
        "torque_Nm": float(s["torque_Nm"]),
        "power_W": float(s["power_W"]),
        "advance_ratio_J": float(s["advance_ratio_J"]),
        "CT": float(s["CT"]),
        "CP": float(s["CP"]),
        "propulsive_efficiency": float(s["propulsive_efficiency"]),
        "figure_of_merit": float(s["figure_of_merit"]),
        "alpha_min_deg": float(alpha.min()),
        "alpha_max_deg": float(alpha.max()),
        "stalled_station_count": int(np.sum(cl >= stall_cl - 1e-9)),
        "negative_loading_station_count": int(s["negative_loading_station_count"]),
        "tip_mach": float(result.operating_point.omega_rad_s * result.operating_point.radius_m / 343.0),
    }


def plot_envelope(data: dict[str, Any], design, output: Path) -> Path:
    """Four panels: thrust map vs V, mission rpm and power, efficiency, spanwise alpha at each point."""

    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import INK_2, MUTED, label_end, ordinal, style

    style()
    spec = design.mission
    grid, points, solutions = data["grid"], data["points"], data["solutions"]
    figure, axes = plt.subplots(2, 2, figsize=(13, 9))
    (ax_map, ax_rpm), (ax_eff, ax_alpha) = axes

    rpms = sorted({row["rpm"] for row in grid})
    shown = [rpm for rpm in rpms if rpm >= 3000.0 and abs(rpm % 500.0) < 1e-6] or rpms[:: max(1, len(rpms) // 6)]
    last_label = -math.inf
    for rpm, color in zip(shown, ordinal(len(shown)), strict=True):
        rows = [row for row in grid if row["rpm"] == rpm]
        v = [row["v_inf_m_s"] for row in rows]
        t = [row["thrust_N"] for row in rows]
        ax_map.plot(v, t, color=color, label=f"{rpm:.0f} rpm")
        if t[-1] - last_label > 4.0:  # direct labels only where they do not collide
            label_end(ax_map, v, t, f"{rpm:.0f}")
            last_label = t[-1]
    targets = [p["target_thrust_N"] for p in points]
    ax_map.plot([p["v_inf_m_s"] for p in points], targets, color=INK_2, linestyle="--", linewidth=1.2, label="mission thrust")
    ax_map.set_title("Thrust at fixed rpm falls with airspeed (frozen pitch)")
    ax_map.set_xlabel("free stream V (m/s)")
    ax_map.set_ylabel("thrust (N)")
    ax_map.legend(loc="upper right", ncol=2)

    v = [p["v_inf_m_s"] for p in points]
    ax_rpm.plot(v, [p["rpm"] for p in points], color=ordinal(5)[3], marker="o")
    ax_rpm.axhline(spec.max_rpm, color=MUTED, linewidth=1.0, linestyle=":")
    ax_rpm.annotate(
        f"motor limit {spec.max_rpm:.0f} rpm", (v[0], spec.max_rpm), xytext=(2, -12), textcoords="offset points", fontsize=8, color=INK_2
    )
    ax_rpm.set_title("rpm needed to hold mission thrust")
    ax_rpm.set_xlabel("free stream V (m/s)")
    ax_rpm.set_ylabel("rpm")
    for p in points:
        ax_rpm.annotate(
            f"{p['power_W']:.0f} W",
            (p["v_inf_m_s"], p["rpm"]),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            color=INK_2,
        )

    eff = [p["propulsive_efficiency"] if p["v_inf_m_s"] > 0 else math.nan for p in points]
    ax_eff.plot(v, eff, color=ordinal(5)[3], marker="o", label="propulsive efficiency T V / P")
    ax_eff.set_ylim(0, 1)
    ax_eff.set_title(f"Efficiency along the mission (hover FM = {points[0]['figure_of_merit']:.2f})")
    ax_eff.set_xlabel("free stream V (m/s)")
    ax_eff.set_ylabel("efficiency")
    for p in points:
        ax_eff.annotate(
            f"J = {p['advance_ratio_J']:.2f}",
            (p["v_inf_m_s"], eff[points.index(p)] if p["v_inf_m_s"] > 0 else 0.05),
            xytext=(0, -14),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            color=INK_2,
        )

    colors = ordinal(len(solutions))
    radius = data["radius_m"]
    for result, p, color in zip(solutions, points, colors, strict=True):
        r = result.stations.r_m / radius
        a = [sol.alpha_deg for sol in result.solutions]
        ax_alpha.plot(r, a, color=color, label=f"V = {p['v_inf_m_s']:.0f} m/s, {p['rpm']:.0f} rpm")
    ax_alpha.axhline(0.0, color=MUTED, linewidth=0.8)
    ax_alpha.set_title("Section angle of attack: design point vs off-design")
    ax_alpha.set_xlabel("r / R")
    ax_alpha.set_ylabel("alpha (deg)")
    ax_alpha.legend(loc="upper right")
    figure.suptitle(f"{design.name}: mission envelope (BEMT, collective frozen at {data['collective_offset_deg']:+.2f} deg)", fontsize=12)
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150)
    plt.close(figure)
    return output
