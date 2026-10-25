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
    for index in spec.cfd_points:
        point = points[index]
        name, rpm, v = point["name"], float(point["rpm"]), float(point["v_inf_m_s"])
        omega = rpm * math.pi / 30.0
        case = openfoam.write_case(run.path(f"cfd/{name}"), stl, params(rpm, v))
        openfoam.reuse_mesh(mesh_case, case)
        openfoam.run_case(case, processors=processors, bashrc=cfd.openfoam_bashrc, timeout_s=cfd.timeout_s, phase="solve")
        times = openfoam.solution_times(case)
        loads = [openfoam.wall_loads(case, t, rho_kg_m3=op.rho_kg_m3, nu_m2_s=nu, mesh=mesh) for t in times[-2:]]
        wall = loads[-1]
        far = openfoam.farfield_thrust(case, times[-1], rho_kg_m3=op.rho_kg_m3, mesh=mesh, v_inf_m_s=v)
        thrust = float(wall.force_N[:, 2].sum())
        torque = -float((wall.centres[:, 0] * wall.force_N[:, 1] - wall.centres[:, 1] * wall.force_N[:, 0]).sum())
        previous = float(loads[0].force_N[:, 2].sum())
        power = torque * omega
        stations = pipeline._read_csv(upstream["envelope"].output(f"stations_{name}.csv"))
        r_st = np.array([float(row["r_m"]) for row in stations])
        dT, dQ = openfoam.station_loads(wall, r_st)
        record = {
            "point": index,
            "name": name,
            "v_inf_m_s": v,
            "rpm": rpm,
            "bemt_thrust_N": float(point["thrust_N"]),
            "bemt_power_W": float(point["power_W"]),
            "cfd_thrust_N": thrust,
            "cfd_thrust_farfield_N": far["thrust_N"],
            "cfd_torque_Nm": torque,
            "cfd_power_W": power,
            "cfd_over_bemt_thrust": thrust / float(point["thrust_N"]),
            "cfd_over_bemt_power": power / float(point["power_W"]),
            "wall_vs_farfield_fraction": (thrust - far["thrust_N"]) / thrust if thrust else math.nan,
            "last_interval_thrust_change_fraction": abs(thrust - previous) / abs(thrust) if thrust else math.nan,
            "cfd_propulsive_efficiency": thrust * v / power if power > 0 and v > 0 else math.nan,
            "cfd_figure_of_merit": thrust**1.5 / math.sqrt(2.0 * op.rho_kg_m3 * math.pi * radius**2) / power
            if thrust > 0 and power > 0 and v == 0
            else math.nan,
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
        U = openfoam.read_field(times[-1] / "U", 3)
        p = openfoam.read_field(times[-1] / "p", 1)
        owners = mesh.owner[blade_faces]
        p_wall = p.boundary.get("blade")
        p_wall = p.internal[owners] if p_wall is None else np.asarray(p_wall).reshape(-1)
        title = f"{design.name}: V = {v:.0f} m/s, {rpm:.0f} rpm, CFD thrust {thrust:.1f} N (BEMT {point['thrust_N']:.1f} N)"
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
        for path in case.glob("processor*"):
            shutil.rmtree(path)
        for old in times[:-1]:
            shutil.rmtree(old)
    pipeline._write_csv(run.path("cfd_points.csv"), records)
    flowplots.plot_section_cp(
        section_data, labels, run.path("section_cp.png"), f"{design.name}: chordwise pressure on blade 1 across the mission (CFD)"
    )
    _plot_mission_cfd(records, run, design, radius)

    fea_records = _mission_fea(design, run, upstream, sections, wall_by_point, face_areas) if spec.fea_points else []
    if fea_records:
        pipeline._write_csv(run.path("fea_points.csv"), fea_records)
    metrics: dict[str, Any] = {"cfd_points": records, "fea_points": fea_records, "cell_count": int(len(centres))}
    return metrics, warnings


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
    import matplotlib.pyplot as plt

    from prandtl_prop import pipeline
    from prandtl_prop.plot_style import INK_2, SERIES, ordinal, style

    style()
    figure, axes = plt.subplots(1, 3, figsize=(16, 4.8))
    colors = ordinal(len(records))
    for record, color in zip(records, colors, strict=True):
        rows = pipeline._read_csv(run.path(f"loads_{record['name']}.csv"))
        r = np.array([float(row["r_R"]) for row in rows])
        dr = np.gradient(r) * radius
        axes[0].plot(
            r, np.array([float(row["cfd_dT_N"]) for row in rows]) / dr, color=color, label=f"CFD, V = {record['v_inf_m_s']:.0f} m/s"
        )
        axes[0].plot(r, np.array([float(row["bemt_dT_N"]) for row in rows]) / dr, color=color, linestyle="--", linewidth=1.2)
    axes[0].set_title("Spanwise thrust loading (solid CFD, dashed BEMT)")
    axes[0].set_xlabel("r / R")
    axes[0].set_ylabel("dT/dr, both blades (N/m)")
    axes[0].legend(loc="upper left")
    v = [r_["v_inf_m_s"] for r_ in records]
    axes[1].plot(v, [r_["cfd_thrust_N"] for r_ in records], color=SERIES[0], marker="o", label="CFD")
    axes[1].plot(v, [r_["bemt_thrust_N"] for r_ in records], color=SERIES[1], marker="s", label="BEMT (trim target)")
    axes[1].set_title("Thrust at the BEMT-trimmed rpm")
    axes[1].set_xlabel("free stream V (m/s)")
    axes[1].set_ylabel("thrust (N)")
    axes[1].legend()
    axes[2].plot(v, [r_["cfd_power_W"] for r_ in records], color=SERIES[0], marker="o", label="CFD")
    axes[2].plot(v, [r_["bemt_power_W"] for r_ in records], color=SERIES[1], marker="s", label="BEMT")
    axes[2].set_title("Shaft power")
    axes[2].set_xlabel("free stream V (m/s)")
    axes[2].set_ylabel("power (W)")
    axes[2].legend()
    for record in records:
        axes[1].annotate(
            f"{record['rpm']:.0f} rpm",
            (record["v_inf_m_s"], record["cfd_thrust_N"]),
            xytext=(0, -14),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            color=INK_2,
        )
    figure.suptitle(f"{design.name}: CFD across the mission (frozen geometry, rpm from BEMT trim)", fontsize=12)
    figure.tight_layout()
    figure.savefig(run.path("mission_cfd.png"), dpi=150)
    plt.close(figure)
