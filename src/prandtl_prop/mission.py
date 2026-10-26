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
            stations,
            replace(op, v_inf_m_s=v),
            polar,
            thrust,
            collective_offset_deg=collective,
            tip_loss=tip_loss,
            corrections=corrections,
            rpm_bracket=rpm_search(spec),
        )
        row = _row(result, spec.stall_cl)
        row.update(
            {"point": index, "name": point_name(v, thrust), "target_thrust_N": thrust, "rpm_within_limit": row["rpm"] <= spec.max_rpm}
        )
        points.append(row)
        solutions.append(result)
    return {"grid": grid, "points": points, "solutions": solutions, "collective_offset_deg": collective, "radius_m": op.radius_m}


def rpm_search(spec) -> tuple[float, float]:
    """rpm range for the trims: wide enough for small fast props (twice the motor limit, at least 12,000 rpm)."""

    return (300.0, max(12000.0, 2.0 * spec.max_rpm))


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


# ------------------------------------------------------------------------------------ CFD + FEA


def run_mission(design, run, upstream) -> tuple[dict[str, Any], list[str]]:
    """CFD at the chosen mission points on one shared mesh, flow plots, and CFD-loaded FEA.

    Geometry and collective are frozen (the design-point CAD); each point runs at its trimmed rpm
    and free stream from the envelope stage. FEA at ``mission.fea_points`` maps the live CFD wall
    pressure and shear onto the blade (prandtl_prop.structures.cfd_coupling), adds the centrifugal
    load at that rpm, and is repeated with BEMT strip loads for comparison.
    """

    import os
    import shutil

    from prandtl_prop import pipeline
    from prandtl_prop.cfd import flowplots, openfoam

    spec, cfd = design.mission, design.cfd
    op = pipeline._operating_point(design)
    points = upstream["envelope"].metrics["mission_points"]
    stl = upstream["cad"].output("rotor_m.stl")
    processors = cfd.processors or os.cpu_count() or 1
    nu = op.mu_air_kg_m_s / op.rho_kg_m3
    sections = pipeline.design_sections(design, upstream["aero"])

    def params(rpm: float, v: float) -> openfoam.CaseParameters:
        return openfoam.CaseParameters(
            radius_m=op.radius_m,
            omega_rad_s=rpm * math.pi / 30.0,
            nu_m2_s=nu,
            rho_kg_m3=op.rho_kg_m3,
            blade_z_extent_m=openfoam.blade_z_extent(stl),
            side_factor=cfd.side_factor,
            above_factor=cfd.above_factor,
            below_factor=cfd.below_factor,
            background_cells_per_radius=cfd.background_cells_per_radius,
            mrf_radius_factor=cfd.mrf_radius_factor,
            mrf_refinement_level=cfd.mrf_refinement_level,
            surface_refinement_levels=(cfd.surface_refinement_min, cfd.surface_refinement_max),
            feature_refinement_level=cfd.feature_refinement_level,
            iterations=cfd.iterations,
            snapshots=cfd.snapshots,
            processors=processors,
            v_inf_m_s=v,
        )

    mesh_case = openfoam.write_case(run.path("cfd/mesh"), stl, params(op.rpm, 0.0))
    openfoam.run_case(mesh_case, processors=processors, bashrc=cfd.openfoam_bashrc, timeout_s=cfd.timeout_s, phase="mesh")
    mesh = openfoam.read_mesh(mesh_case)
    centres = flowplots.all_cell_centres(mesh)
    blade_faces = list(mesh.patch_faces("blade"))
    _, face_areas = openfoam.face_geometry(mesh, blade_faces)  # same face order as the wall loads
    radius = op.radius_m
    records: list[dict[str, Any]] = []
    wall_by_point: dict[int, Any] = {}
    section_data: dict[str, Any] = {}
    labels: dict[str, str] = {}
    warnings: list[str] = []
    stations_ctx, polar, corrections, collective, _ = bemt_context(design, upstream["aero"])

    def bemt_at(rpm: float, v: float) -> BemtResult:
        return solve_rotor(
            stations_ctx,
            replace(op, rpm=rpm, v_inf_m_s=v),
            polar,
            collective_offset_deg=collective,
            tip_loss=design.aero.tip_loss,
            corrections=corrections,
        )

    def solve(case_dir: Path, rpm: float, v: float, warm_from: Path | None):
        """One CFD solve on the shared mesh; a warm start copies the previous final fields into 0/."""

        iterations = cfd.iterations if warm_from is None else spec.cfd_trim_warm_iterations
        case = openfoam.write_case(case_dir, stl, replace(params(rpm, v), iterations=iterations))
        openfoam.reuse_mesh(mesh_case, case)
        if warm_from is not None:
            for field_file in warm_from.iterdir():
                if field_file.is_file():
                    shutil.copy2(field_file, case / "0" / field_file.name)
        openfoam.run_case(case, processors=processors, bashrc=cfd.openfoam_bashrc, timeout_s=cfd.timeout_s, phase="solve")
        for path in case.glob("processor*"):
            shutil.rmtree(path)
        times = openfoam.solution_times(case)
        loads = [openfoam.wall_loads(case, t, rho_kg_m3=op.rho_kg_m3, nu_m2_s=nu, mesh=mesh) for t in times[-2:]]
        for old in times[:-1]:
            shutil.rmtree(old)
        wall = loads[-1]
        thrust = float(wall.force_N[:, 2].sum())
        torque = -float((wall.centres[:, 0] * wall.force_N[:, 1] - wall.centres[:, 1] * wall.force_N[:, 0]).sum())
        return case, times[-1], wall, thrust, torque, float(loads[0].force_N[:, 2].sum())

    solves: list[dict[str, Any]] = []  # every CFD solve, trim iterations included (effective-polar data)
    for index in spec.cfd_points:
        point = points[index]
        name, v, target = point["name"], float(point["v_inf_m_s"]), float(point["target_thrust_N"])
        bemt_rpm = float(point["rpm"])
        # CFD rpm trim. Step 1 assumes the CFD/BEMT thrust ratio holds near this point and re-trims BEMT
        # to target / ratio; later steps are secant steps on the CFD thrust itself.
        history: list[tuple[float, float]] = []
        rpm, warm, case, last_time = bemt_rpm, None, None, None
        for iteration in range(spec.cfd_trim_iterations + 1):
            if case is not None:
                warm = case.parent / f"{name}_warm"
                if warm.exists():
                    shutil.rmtree(warm)
                shutil.move(str(last_time), warm)  # keep the fields to warm-start from
                shutil.rmtree(case)
            case, last_time, wall, thrust, torque, previous = solve(run.path(f"cfd/{name}"), rpm, v, warm)
            history.append((rpm, thrust))
            solves.append(
                {"point": index, "iteration": iteration, "v_inf_m_s": v, "rpm": rpm, "cfd_thrust_N": thrust, "cfd_torque_Nm": torque}
            )
            if abs(thrust / target - 1.0) <= spec.cfd_trim_tolerance or iteration == spec.cfd_trim_iterations:
                break
            if len(history) == 1:
                ratio = thrust / float(bemt_at(rpm, v).summary["thrust_N"])
                rpm = float(
                    trim_rpm_to_thrust(
                        stations_ctx,
                        replace(op, v_inf_m_s=v),
                        polar,
                        target / ratio,
                        collective_offset_deg=collective,
                        tip_loss=design.aero.tip_loss,
                        corrections=corrections,
                        rpm_bracket=rpm_search(spec),
                    ).summary["rpm"]
                )
            else:
                (n0, t0), (n1, t1) = history[-2], history[-1]
                rpm = n1 + (target - t1) * (n1 - n0) / (t1 - t0) if t1 != t0 else n1
        if warm is not None and warm.exists():
            shutil.rmtree(warm)
        if abs(thrust / target - 1.0) > spec.cfd_trim_tolerance and spec.cfd_trim_iterations > 0:
            warnings.append(f"{name}: CFD rpm trim stopped {thrust / target - 1.0:+.1%} from the target thrust")
        omega = rpm * math.pi / 30.0
        far = openfoam.farfield_thrust(case, last_time, rho_kg_m3=op.rho_kg_m3, mesh=mesh, v_inf_m_s=v)
        power = torque * omega
        bemt = bemt_at(rpm, v)  # BEMT at the CFD rpm: same operating point, so the gap is model error only
        stations = bemt.station_rows()
        r_st = np.array([float(row["r_m"]) for row in stations])
        dT, dQ = openfoam.station_loads(wall, r_st)
        record = {
            "point": index,
            "name": name,
            "v_inf_m_s": v,
            "target_thrust_N": target,
            "bemt_trim_rpm": bemt_rpm,
            "bemt_trim_power_W": float(point["power_W"]),
            "rpm": rpm,
            "cfd_trim_iterations": len(history) - 1,
            "cfd_trim_rpm_history": " ".join(f"{n:.0f}" for n, _ in history),
            "cfd_trim_thrust_history_N": " ".join(f"{t:.2f}" for _, t in history),
            "bemt_thrust_N": float(bemt.summary["thrust_N"]),
            "bemt_power_W": float(bemt.summary["power_W"]),
            "cfd_thrust_N": thrust,
            "cfd_thrust_farfield_N": far["thrust_N"],
            "cfd_torque_Nm": torque,
            "cfd_power_W": power,
            "cfd_over_bemt_thrust": thrust / float(bemt.summary["thrust_N"]),
            "cfd_over_bemt_power": power / float(bemt.summary["power_W"]),
            "wall_vs_farfield_fraction": (thrust - far["thrust_N"]) / thrust if thrust else math.nan,
            "last_interval_thrust_change_fraction": abs(thrust - previous) / abs(thrust) if thrust else math.nan,
            "cfd_propulsive_efficiency": thrust * v / power if power > 0 and v > 0 else math.nan,
            "cfd_figure_of_merit": thrust**1.5 / math.sqrt(2.0 * op.rho_kg_m3 * math.pi * radius**2) / power
            if thrust > 0 and power > 0 and v == 0
            else math.nan,
            "rpm_within_limit": rpm <= spec.max_rpm,
        }
        records.append(record)
        pipeline._write_csv(
            run.path(f"loads_{name}.csv"),
            [
                {
                    "r_R": float(row["r_R"]),
                    "bemt_dT_N": float(row["dT_N"]),
                    "cfd_dT_N": float(dT[i]),
                    "bemt_dQ_Nm": float(row["dQ_Nm"]),
                    "cfd_dQ_Nm": float(dQ[i]),
                }
                for i, row in enumerate(stations)
            ],
        )
        U = openfoam.read_field(last_time / "U", 3)
        p = openfoam.read_field(last_time / "p", 1)
        owners = mesh.owner[blade_faces]
        p_wall = p.boundary.get("blade")
        p_wall = p.internal[owners] if p_wall is None else np.asarray(p_wall).reshape(-1)
        title = f"{design.name}: V = {v:.0f} m/s, {rpm:.0f} rpm (CFD trim), CFD thrust {thrust:.1f} N"
        window = (-1.6 * radius, 1.6 * radius, -3.0 * radius, 1.0 * radius)
        flowplots.plot_meridional(
            flowplots.meridional_slice(centres, U.internal, p.internal, radius_m=radius, omega_rad_s=omega, v_inf_m_s=v, window=window),
            radius_m=radius,
            title=title,
            output=run.path(f"flow_{name}.png"),
        )
        triangles, cp = flowplots.blade_surface(mesh, p_wall, omega_rad_s=omega, v_inf_m_s=v)
        flowplots.plot_blade_cp(triangles, cp, title=title, output=run.path(f"blade_cp_{name}.png"))
        section_data[name] = flowplots.section_cp(
            mesh, p_wall, sections, (0.3, 0.5, 0.7, 0.9), radius_m=radius, omega_rad_s=omega, v_inf_m_s=v
        )
        labels[name] = f"V = {v:.0f} m/s, {rpm:.0f} rpm"
        wall_by_point[index] = (wall, rpm, v, stations)
        if abs(record["wall_vs_farfield_fraction"]) > 0.02:
            warnings.append(f"{name}: wall and far-field thrust differ by {record['wall_vs_farfield_fraction']:.1%}")
        if record["last_interval_thrust_change_fraction"] > 0.005:
            warnings.append(
                f"{name}: thrust still changing {record['last_interval_thrust_change_fraction']:.2%} between the last snapshots"
            )
        if rpm > spec.max_rpm:
            warnings.append(f"{name}: CFD-trimmed rpm {rpm:.0f} is above the {spec.max_rpm:.0f} rpm limit")
    pipeline._write_csv(run.path("cfd_solves.csv"), solves)
    effective = effective_polar(
        polar, solves, bemt_at_polar=lambda pol, n, v: _bemt_tq(stations_ctx, op, pol, n, v, collective, design.aero.tip_loss, corrections)
    )
    if effective:
        pipeline._write_csv(run.path("effective_polar.csv"), [effective])
    pipeline._write_csv(run.path("cfd_points.csv"), records)
    flowplots.plot_section_cp(
        section_data, labels, run.path("section_cp.png"), f"{design.name}: chordwise pressure on blade 1 across the mission (CFD)"
    )
    _plot_mission_cfd(records, run, design, radius)

    fea_records = _mission_fea(design, run, upstream, sections, wall_by_point, face_areas) if spec.fea_points else []
    if fea_records:
        pipeline._write_csv(run.path("fea_points.csv"), fea_records)
    metrics: dict[str, Any] = {
        "cfd_points": records,
        "fea_points": fea_records,
        "effective_polar": effective,
        "cell_count": int(len(centres)),
    }
    return metrics, warnings


def _bemt_tq(stations, op, polar, rpm, v, collective, tip_loss, corrections) -> tuple[float, float]:
    result = solve_rotor(
        stations, replace(op, rpm=rpm, v_inf_m_s=v), polar, collective_offset_deg=collective, tip_loss=tip_loss, corrections=corrections
    )
    return float(result.summary["thrust_N"]), float(result.summary["torque_Nm"])


def effective_polar(polar, solves: list[dict[str, Any]], *, bemt_at_polar) -> dict[str, Any]:
    """The single analytic polar (a, alpha0, cd0, k) that makes BEMT reproduce every CFD solve's thrust and torque.

    A diagnostic, not a calibration: if one polar fits all speeds, the CFD-BEMT gap is a section-polar
    mismatch (the CFD sees a different lift curve) rather than a breakdown of momentum theory off
    design. Thrust and torque give two numbers per solve, so the split between cd0 and k is weakly
    determined; a parameter at its bound is reported. Only for analytic polars.
    """

    from scipy.optimize import least_squares

    from prandtl_prop.aero.polars import AnalyticPolar

    if not isinstance(polar, AnalyticPolar) or len(solves) < 2:
        return {}
    data = [(float(row["v_inf_m_s"]), float(row["rpm"]), float(row["cfd_thrust_N"]), float(row["cfd_torque_Nm"])) for row in solves]

    def residuals(x: np.ndarray) -> np.ndarray:
        candidate = replace(polar, cl_alpha_per_rad=x[0], alpha0_deg=x[1], cd0=x[2], k=x[3])
        out = [bemt_at_polar(candidate, n, v) for v, n, _, _ in data]
        return np.array(
            [o[0] / t - 1.0 for o, (_, _, t, _) in zip(out, data, strict=True)]
            + [o[1] / q - 1.0 for o, (_, _, _, q) in zip(out, data, strict=True)]
        )

    x0 = np.array([polar.cl_alpha_per_rad, polar.alpha0_deg, polar.cd0, polar.k])
    lower, upper = np.array([2.0, -8.0, 0.002, 0.0]), np.array([7.0, 2.0, 0.06, 0.05])
    baseline = residuals(x0)
    fit = least_squares(residuals, x0, bounds=(lower, upper))
    names = ("cl_alpha_per_rad", "alpha0_deg", "cd0", "k")
    at_bound = [n for n, x, lo, hi in zip(names, fit.x, lower, upper, strict=True) if np.isclose(x, lo) or np.isclose(x, hi)]
    half = len(data)
    return {
        **{f"baseline_{n}": float(x) for n, x in zip(names, x0, strict=True)},
        **{f"fit_{n}": float(x) for n, x in zip(names, fit.x, strict=True)},
        "solves": len(data),
        "baseline_max_thrust_error": float(np.abs(baseline[:half]).max()),
        "baseline_max_torque_error": float(np.abs(baseline[half:]).max()),
        "fit_max_thrust_error": float(np.abs(fit.fun[:half]).max()),
        "fit_max_torque_error": float(np.abs(fit.fun[half:]).max()),
        "parameters_at_bound": " ".join(at_bound),
    }


def _mission_fea(design, run, upstream, sections, wall_by_point, face_areas: np.ndarray) -> list[dict[str, Any]]:
    from prandtl_prop import pipeline
    from prandtl_prop.structures import calculix
    from prandtl_prop.structures.cfd_coupling import map_wall_loads, plot_fe_results, read_frd_nodal, von_mises
    from prandtl_prop.structures.loads import map_station_loads

    material = design.structure.material
    op = pipeline._operating_point(design)
    mesh = calculix.mesh_solid(upstream["cad"].output("blade_m.brep"), design.fea.mesh_size_m)
    root_x = float(mesh.nodes[:, 0].min())
    clamp, inner_edge, clamp_zone_end = pipeline._root_boundary(design, sections, mesh)
    records = []
    for index in design.mission.fea_points:
        if index not in wall_by_point:
            continue
        wall, rpm, v, stations = wall_by_point[index]
        omega = rpm * math.pi / 30.0
        cfd_forces, mapping = map_wall_loads(
            mesh.nodes, mesh.elements, wall.centres, face_areas, wall.pressure_force_N, wall.viscous_force_N
        )
        bemt_loads = map_station_loads(
            stations,
            sections,
            blade_count=op.blade_count,
            omega_rad_s=omega,
            rho_air_kg_m3=op.rho_kg_m3,
            material_density_kg_m3=material.density_kg_m3,
            include_centrifugal=False,
            include_pitching_moment=design.structure.include_aero_pitching_moment,
        )
        weights = np.array([float(row["dr_m"]) for row in stations])
        bemt_forces = calculix.strip_nodal_forces(mesh, sections, bemt_loads, weights, root_x_m=root_x, inner_edge_m=inner_edge)
        name = point_name(v, float(upstream["envelope"].metrics["mission_points"][index]["target_thrust_N"]))
        for source, forces in (("cfd", cfd_forces), ("bemt", bemt_forces)):
            folder = run.path(f"fea/{name}_{source}")
            folder.mkdir(parents=True, exist_ok=True)
            deck = calculix.write_input_deck(
                folder / "blade.inp",
                mesh,
                E_Pa=material.E_Pa,
                nu=material.nu,
                density_kg_m3=material.density_kg_m3,
                omega_rad_s=omega,
                nodal_forces=forces,
                root_x_m=root_x,
                nlgeom=design.fea.nlgeom,
                clamp_nodes=clamp,
            )
            result = calculix.run_calculix(
                deck, ccx_executable=design.fea.ccx_executable, threads=design.fea.threads, timeout_s=design.fea.timeout_s
            )
            summary, _ = calculix.summarize(
                mesh, result, sections, allowable_stress_Pa=material.allowable_stress_Pa, clamp_zone_end_m=clamp_zone_end
            )
            frd = read_frd_nodal(folder / "blade.frd")
            displacement = frd["DISP"][:, :3]
            vm = von_mises(frd["STRESS"][:, :6])
            record = {
                "point": index,
                "name": name,
                "load_source": source,
                "v_inf_m_s": v,
                "rpm": rpm,
                "aero_force_z_N": float(forces[:, 2].sum()),
                "max_von_mises_outside_clamp_zone_Pa": summary["max_von_mises_outside_clamp_zone_Pa"],
                "margin_of_safety_outside_clamp_zone": summary["margin_of_safety_outside_clamp_zone"],
                "tip_deflection_z_m": summary["tip_deflection_z_m"],
                "tip_deflection_y_m": summary["tip_deflection_y_m"],
                "max_nodal_deflection_m": float(np.linalg.norm(displacement, axis=1).max()),
            }
            if source == "cfd":
                record.update({f"mapping_{k}": val for k, val in mapping.items()})
            records.append(record)
            plot_fe_results(
                mesh.nodes,
                mesh.elements,
                displacement,
                vm,
                title=f"{design.name}: {source.upper()} aero loads + centrifugal, V = {v:.0f} m/s, {rpm:.0f} rpm; "
                f"tip flap {summary['tip_deflection_z_m'] * 1000:.2f} mm, peak outside clamp {summary['max_von_mises_outside_clamp_zone_Pa'] / 1e6:.1f} MPa",
                output=run.path(f"fea_{name}_{source}.png"),
            )
            for junk in folder.glob("*.frd"):
                junk.unlink()
    return records


def _plot_mission_cfd(records, run, design, radius) -> None:
    """Four panels: spanwise loading (CFD vs BEMT at the same rpm), rpm to hold the target thrust
    (BEMT trim vs CFD trim, motor limit), shaft power at trim, and the CFD trim convergence."""

    import matplotlib.pyplot as plt

    from prandtl_prop import pipeline
    from prandtl_prop.plot_style import INK_2, REFERENCE, SERIES, ordinal, style

    style()
    figure, axes = plt.subplots(1, 4, figsize=(21, 4.9))
    colors = ordinal(len(records))
    for record, color in zip(records, colors, strict=True):
        rows = pipeline._read_csv(run.path(f"loads_{record['name']}.csv"))
        r = np.array([float(row["r_R"]) for row in rows])
        dr = np.gradient(r) * radius
        axes[0].plot(r, np.array([float(row["cfd_dT_N"]) for row in rows]) / dr, color=color, label=f"V = {record['v_inf_m_s']:.0f} m/s")
        axes[0].plot(r, np.array([float(row["bemt_dT_N"]) for row in rows]) / dr, color=color, linestyle="--", linewidth=1.2)
    axes[0].set_title("Spanwise thrust loading at the CFD-trimmed rpm\n(solid CFD, dashed BEMT at the same rpm)")
    axes[0].set_xlabel("r / R")
    axes[0].set_ylabel("dT/dr, both blades (N/m)")
    axes[0].legend(loc="upper left")
    v = [r_["v_inf_m_s"] for r_ in records]
    axes[1].plot(v, [r_["bemt_trim_rpm"] for r_ in records], color=SERIES[1], marker="s", label="BEMT trim (analytic polar)")
    axes[1].plot(v, [r_["rpm"] for r_ in records], color=SERIES[0], marker="o", label="CFD trim")
    axes[1].axhline(design.mission.max_rpm, color=REFERENCE, linestyle="--", linewidth=1.0)
    axes[1].annotate(
        f"motor limit {design.mission.max_rpm:.0f} rpm",
        (0.0, design.mission.max_rpm),
        xytext=(2, 4),
        textcoords="offset points",
        fontsize=8,
        color=INK_2,
    )
    axes[1].set_title(f"rpm to hold {records[0]['target_thrust_N']:.0f} N (fixed pitch)")
    axes[1].set_xlabel("free stream V (m/s)")
    axes[1].set_ylabel("rpm")
    axes[1].legend(loc="lower right")
    axes[2].plot(v, [r_["bemt_trim_power_W"] for r_ in records], color=SERIES[1], marker="s", label="BEMT at BEMT trim")
    axes[2].plot(v, [r_["cfd_power_W"] for r_ in records], color=SERIES[0], marker="o", label="CFD at CFD trim")
    axes[2].set_title("Shaft power at trim")
    axes[2].set_xlabel("free stream V (m/s)")
    axes[2].set_ylabel("power (W)")
    axes[2].legend(loc="upper left")
    for record, color in zip(records, colors, strict=True):
        thrusts = [float(t) for t in str(record["cfd_trim_thrust_history_N"]).split()]
        error = np.abs(np.array(thrusts) / record["target_thrust_N"] - 1.0) * 100.0
        axes[3].semilogy(range(len(error)), np.maximum(error, 1e-3), color=color, marker="o", label=f"V = {record['v_inf_m_s']:.0f} m/s")
    axes[3].axhline(design.mission.cfd_trim_tolerance * 100.0, color=REFERENCE, linestyle="--", linewidth=1.0)
    axes[3].set_title("CFD rpm trim convergence\n(iteration 0 = BEMT-trimmed rpm)")
    axes[3].set_xlabel("CFD solve")
    axes[3].set_ylabel("|T / T_target - 1| (%)")
    axes[3].set_xticks(range(design.mission.cfd_trim_iterations + 1))
    axes[3].legend(loc="upper right")
    figure.suptitle(f"{design.name}: CFD across the mission (frozen geometry and collective, rpm control only)", fontsize=12)
    figure.tight_layout()
    figure.savefig(run.path("mission_cfd.png"), dpi=150)
    plt.close(figure)
