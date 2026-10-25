"""Design-to-results pipeline: aero -> sections -> cad, and aero + sections -> beam.

Each stage is cached in a content-addressed run folder (see ``prandtl_prop.runs``). A stage's key
covers the design sections it reads, the bytes of its input files, the source code of the modules
it runs, and its upstream keys, so editing any of these re-runs exactly the affected stages.
"""

from __future__ import annotations

import csv
import hashlib
import importlib
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from prandtl_prop.aero import AnalyticPolar, BladeStations, OperatingPoint, TablePolar, solve_rotor, trim_to_thrust
from prandtl_prop.cad.airfoils import find_airfoil_file, load_airfoil
from prandtl_prop.cad.sections import BladeSection, build_sections, section_table
from prandtl_prop.design import DesignCase, resolve_path
from prandtl_prop.runs import RunStore, StageRun, stage_key

STAGES = ("aero", "sections", "dfm", "cad", "beam", "fea", "modal", "mold", "hub", "cfd", "envelope", "mission")
DEFAULT_STAGES = ("aero", "sections", "dfm", "cad", "beam")  # seconds; fea, modal, and cfd take minutes to hours
STAGE_UPSTREAM = {
    "aero": (),
    "sections": ("aero",),
    "dfm": ("aero", "sections"),
    "cad": ("aero", "sections"),
    "beam": ("aero", "sections"),
    "fea": ("aero", "sections", "cad", "beam"),
    "modal": ("aero", "cad"),
    "mold": ("aero", "cad"),
    "hub": ("aero", "cad"),
    "cfd": ("aero", "cad"),
    "envelope": ("aero",),
    "mission": ("aero", "cad", "envelope"),
}
STAGE_MODULES = {
    "aero": (
        "prandtl_prop.aero.bemt",
        "prandtl_prop.aero.polars",
        "prandtl_prop.aero.corrections",
        "prandtl_prop.manufacturing.injection",
        "prandtl_prop.pipeline",
    ),
    "sections": ("prandtl_prop.cad.airfoils", "prandtl_prop.cad.sections", "prandtl_prop.manufacturing.injection", "prandtl_prop.pipeline"),
    "dfm": ("prandtl_prop.manufacturing.injection", "prandtl_prop.structures.modal_beam", "prandtl_prop.cad.root", "prandtl_prop.pipeline"),
    "cad": (
        "prandtl_prop.cad.step",
        "prandtl_prop.cad.render",
        "prandtl_prop.cad.root",
        "prandtl_prop.cad.hub_variants",
        "prandtl_prop.pipeline",
    ),
    "beam": ("prandtl_prop.structures.loads", "prandtl_prop.structures.beam", "prandtl_prop.pipeline"),
    "fea": ("prandtl_prop.structures.loads", "prandtl_prop.structures.calculix", "prandtl_prop.pipeline"),
    "modal": ("prandtl_prop.structures.calculix", "prandtl_prop.pipeline"),
    "mold": (
        "prandtl_prop.manufacturing.mold",
        "prandtl_prop.cad.step",
        "prandtl_prop.cad.root",
        "prandtl_prop.cad.render",
        "prandtl_prop.pipeline",
    ),
    "hub": (
        "prandtl_prop.cad.hub",
        "prandtl_prop.cad.hub_variants",
        "prandtl_prop.cad.step",
        "prandtl_prop.cad.root",
        "prandtl_prop.cad.render",
        "prandtl_prop.pipeline",
    ),
    "cfd": ("prandtl_prop.cfd.openfoam", "prandtl_prop.pipeline"),
    "envelope": ("prandtl_prop.mission", "prandtl_prop.aero.bemt", "prandtl_prop.plot_style", "prandtl_prop.pipeline"),
    "mission": (
        "prandtl_prop.mission",
        "prandtl_prop.cfd.openfoam",
        "prandtl_prop.cfd.flowplots",
        "prandtl_prop.structures.calculix",
        "prandtl_prop.structures.cfd_coupling",
        "prandtl_prop.structures.loads",
        "prandtl_prop.cad.render",
        "prandtl_prop.plot_style",
        "prandtl_prop.pipeline",
    ),
}


@dataclass
class StageOutcome:
    stage: str
    run: StageRun

    @property
    def cached(self) -> bool:
        return self.run.cached


def run_design(
    design: DesignCase,
    stages: Sequence[str] = DEFAULT_STAGES,
    *,
    store: RunStore | None = None,
    force: bool = False,
) -> dict[str, StageOutcome]:
    """Run the requested stages (and their upstream stages) for one design."""

    store = store or RunStore()
    unknown = set(stages) - set(STAGES)
    if unknown:
        raise ValueError(f"Unknown stage(s) {sorted(unknown)}; choose from {STAGES}")
    needed: list[str] = []

    def require(stage: str) -> None:
        for upstream in STAGE_UPSTREAM[stage]:
            require(upstream)
        if stage not in needed:
            needed.append(stage)

    for stage in stages:
        require(stage)

    outcomes: dict[str, StageOutcome] = {}
    for stage in needed:
        upstream_runs = {name: outcomes[name].run for name in STAGE_UPSTREAM[stage]}
        outcomes[stage] = _run_stage(stage, design, upstream_runs, store, force)
    return outcomes


# --------------------------------------------------------------------------------------- stages


def _stage_aero(design: DesignCase, run: StageRun, upstream: dict[str, StageRun]) -> tuple[dict[str, Any], list[str]]:
    op_spec = design.operating_point
    op = _operating_point(design)
    stations = BladeStations.from_rows(_read_csv(resolve_path(design.geometry.stations_csv)), op.radius_m)
    polar = _polar(design)
    corrections = _corrections(design, stations, op.radius_m)
    thickness_drag = _thickness_drag_factors(design, stations, op.radius_m)
    if thickness_drag is not None:
        lift, drag = corrections if corrections is not None else (np.ones(len(stations.r_m)), np.ones(len(stations.r_m)))
        corrections = (lift, drag * thickness_drag)
    if design.aero.trim_to_target_thrust:
        result = trim_to_thrust(stations, op, polar, float(op_spec.target_thrust_N), tip_loss=design.aero.tip_loss, corrections=corrections)
    else:
        result = solve_rotor(stations, op, polar, tip_loss=design.aero.tip_loss, corrections=corrections)
    _write_csv(run.path("stations.csv"), result.station_rows())
    metrics = dict(result.summary)
    warnings = []
    for name in ("nonconverged_station_count", "negative_loading_station_count", "alpha_clipped_station_count", "re_clipped_station_count"):
        if metrics[name]:
            warnings.append(f"{metrics[name]} station(s) flagged: {name.removesuffix('_count')}")
    if design.aero.polar.type == "analytic":
        warnings.append("Analytic polar in use: results verify the pipeline, not the airfoils.")
    if thickness_drag is not None:
        metrics["thickness_drag_factor_mean"] = float(thickness_drag.mean())
        warnings.append(
            "Thickness modified for manufacturing: profile drag scaled by the Hoerner form-factor ratio; "
            "polars are for the unmodified sections and the blunt-TE base drag is not modelled. Regenerate XFOIL polars for the final sections."
        )
    if corrections is not None and design.aero.corrections_csv is not None:
        metrics["corrections_csv"] = design.aero.corrections_csv
        metrics["mean_lift_factor"] = float(corrections[0].mean())
        metrics["mean_drag_factor"] = float(corrections[1].mean())
    return metrics, warnings


def _stage_sections(design: DesignCase, run: StageRun, upstream: dict[str, StageRun]) -> tuple[dict[str, Any], list[str]]:
    sections = design_sections(design, upstream["aero"])
    rows = section_table(sections)
    _write_csv(run.path("section_properties.csv"), rows)
    run.write_json(
        "sections.json",
        {
            "frame": "x span, z thrust/rotation axis, blade moves +y; points in metres",
            "pitch_axis_chord_fraction": design.geometry.pitch_axis_chord_fraction,
            "sections": [
                {
                    "r_m": s.r_m,
                    "chord_m": s.chord_m,
                    "beta_deg": s.beta_deg,
                    "airfoil": s.airfoil.name,
                    "points": s.points_3d().round(9).tolist(),
                }
                for s in sections
            ],
        },
    )
    substituted = sorted({row["airfoil_source"] for row in rows if str(row["airfoil_source"]).startswith("SUBSTITUTE")})
    metrics = {
        "section_count": len(sections),
        "substituted_airfoil_count": len(substituted),
        "trim_offset_applied_deg": _trim_offset(design, upstream["aero"]),
    }
    return metrics, [f"Airfoil stand-in used: {text}" for text in substituted]


def _stage_dfm(design: DesignCase, run: StageRun, upstream: dict[str, StageRun]) -> tuple[dict[str, Any], list[str]]:
    """Injection-molding checks plus a fast rotating-beam Campbell check over the operating rpm band."""

    from prandtl_prop.manufacturing.injection import MoldingRules, check_molding
    from prandtl_prop.structures.modal_beam import blade_arrays, campbell, excitation_margin

    spec = design.molding
    rules = MoldingRules(
        min_trailing_edge_thickness_m=spec.min_trailing_edge_thickness_m,
        max_section_thickness_m=spec.max_section_thickness_m,
        max_thickness_gradient=spec.max_thickness_gradient,
        max_flow_length_ratio=spec.max_flow_length_ratio,
        min_draft_deg=spec.min_draft_deg,
    )
    sections = design_sections(design, upstream["aero"])
    report = check_molding(sections, rules, spec.pull_angle_deg)
    if design.root.enabled:  # the root tab and transition must release with the same pull
        from prandtl_prop.cad.root import full_blade_outlines
        from prandtl_prop.manufacturing.injection import best_pull_angle, pull_check_outlines

        outlines = full_blade_outlines(sections, design.root.geometry())
        pull = (
            pull_check_outlines(outlines, spec.pull_angle_deg, rules)
            if spec.pull_angle_deg is not None
            else best_pull_angle(outlines, rules)
        )
        report.update(
            {
                "pull_angle_deg": pull.pull_angle_deg,
                "undercut_sections": pull.undercut_sections,
                "min_draft_deg": pull.min_draft_deg,
                "min_draft_section": pull.worst_section,
            }
        )
        report["checks"]["undercut_free"] = not pull.undercut_sections
        report["checks"]["draft"] = pull.min_draft_deg >= rules.min_draft_deg
    material = design.structure.material
    lo, hi = spec.operating_rpm_fractions
    rpm_values = list(np.linspace(lo, hi, 9) * design.operating_point.rpm)
    beam = blade_arrays(sections, design.root.geometry() if design.root.enabled else None, _clamp_radius(design))
    modes = campbell(beam, E_Pa=material.E_Pa, density_kg_m3=material.density_kg_m3, rpm_values=rpm_values, count=3)
    report["operating_rpm_range"] = [rpm_values[0], rpm_values[-1]]
    report["beam_flap1_hz_over_band"] = [m[0].frequency_hz for m in modes]
    report["beam_excitation_margin_over_band"] = excitation_margin(modes, rpm_values)
    report["checks"]["frequency_margin"] = report["beam_excitation_margin_over_band"] >= spec.frequency_margin
    report["all_pass"] = all(report["checks"].values())
    run.write_json("molding_report.json", report)
    warnings = [f"Molding/frequency check failed: {name}" for name, ok in report["checks"].items() if not ok]
    metrics = {key: value for key, value in report.items() if key != "checks"}
    metrics.update({f"check_{name}": ok for name, ok in report["checks"].items()})
    return metrics, warnings


def _stage_cad(design: DesignCase, run: StageRun, upstream: dict[str, StageRun]) -> tuple[dict[str, Any], list[str]]:
    import numpy as np

    from prandtl_prop.cad import step

    sections = design_sections(design, upstream["aero"])
    prop = None
    if design.root.enabled:
        from prandtl_prop.cad.root import full_blade_outlines

        geometry = design.root.geometry()
        outlines = full_blade_outlines(sections, geometry)
        run.write_json("root_outlines.json", {"outlines_m": [o.round(9).tolist() for o in outlines]})
        if design.hub.variant == "one_piece":
            prop, blade = _one_piece_prop(design, sections)  # blade: blade 1 + its half of the boss (for the FEA)
        else:
            blade = step.full_blade_solid(sections, geometry)
    else:
        blade = step.blade_solid(sections, design.geometry.hub_radius_m)
    step.export_step(prop if prop is not None else blade, run.path("blade.step"))  # millimetres, for SolidWorks and other CAD
    blade.exportBrep(str(run.path("blade_m.brep")))  # metres, for meshing (CalculiX)
    if prop is not None:
        prop.exportBrep(str(run.path("prop_m.brep")))
    report = step.report(blade)
    areas = np.array([section.properties().area_m2 for section in sections])
    radii = np.array([section.r_m for section in sections])
    quadrature_volume = float(np.trapezoid(areas, radii))
    metrics: dict[str, Any] = {
        "blade_volume_m3": report.volume_m3,
        "blade_mass_kg": report.volume_m3 * design.structure.material.density_kg_m3,
        "blade_center_of_mass_m": list(report.center_of_mass_m),
        "blade_bounding_box_m": list(report.bounding_box_m),
        "blade_solid_valid": report.is_valid,
        "blade_inertia_cg_zz_kg_m2": step.inertia_zz_about_cg(blade, design.structure.material.density_kg_m3),
        "loft_vs_section_quadrature_volume_ratio": report.volume_m3 / quadrature_volume
        if design.geometry.hub_radius_m is None and not design.root.enabled
        else None,
    }
    if prop is not None:
        metrics["prop_mass_kg"] = step.report(prop).volume_m3 * design.structure.material.density_kg_m3
        metrics["prop_solid_valid"] = bool(prop.isValid())
    if design.cad.export_stl:
        step.export_stl(prop if prop is not None else blade, run.path("blade_m.stl"), design.cad.stl_tolerance_m)
        from prandtl_prop.cad.render import render

        picked = sections[::3] + ([sections[-1]] if (len(sections) - 1) % 3 else [])
        render(
            run.path("blade_m.stl"),
            run.path("render.png"),
            sections=[s.points_3d() for s in picked],
            radii_m=[s.r_m for s in picked],
            title=f"{design.name}: {design.description}"[:140],
        )
    if design.cad.export_solidworks_curves:
        curves = step.write_solidworks_curves(sections, run.path("solidworks_curves"))
        metrics["solidworks_curve_count"] = len(curves)
    if design.cad.export_rotor:
        rotor = prop if prop is not None else step.rotor_solid(blade, design.operating_point.blade_count, design.geometry.hub_radius_m)
        step.export_step(rotor, run.path("rotor.step"))
        step.export_stl(rotor, run.path("rotor_m.stl"), design.cad.stl_tolerance_m)  # metres, for OpenFOAM
        metrics["rotor_volume_m3"] = step.report(rotor).volume_m3
    warnings = [] if report.is_valid else ["Blade solid failed OpenCascade validity check"]
    return metrics, warnings


def _one_piece_prop(design: DesignCase, sections: list[BladeSection]):
    """One-piece prop (both blades + boss) and the FEA half; holes: centre bore and the motor pattern."""

    from prandtl_prop.cad import step
    from prandtl_prop.cad.hub_variants import _motor_holes
    from prandtl_prop.cad.root import full_blade_outlines, tab_outline_3d

    geometry, hub = design.root.geometry(), design.hub
    if not geometry.tab_inner_radius_m < 0.5 * hub.boss_diameter_m < geometry.tab_outer_radius_m:
        raise ValueError("One-piece prop: the tab must start inside the boss and end outside it (tab_inner < boss radius < tab_outer)")
    if hub.boss_thickness_m <= geometry.tab_thickness_m:
        raise ValueError("One-piece prop: hub.boss_thickness_m must exceed root.tab_thickness_m (no coincident faces)")
    tab = tab_outline_3d(sections, geometry)
    z_centre = 0.5 * float(tab[:, 2].max() + tab[:, 2].min())
    holes = [(0.0, 0.0, z_centre)] + [(x, y, z_centre) for x, y in _motor_holes(hub)]
    diameters = [hub.center_bore_m] + [_motor_clearance(hub)] * hub.motor_screw_count
    return step.one_piece_solid(
        full_blade_outlines(sections, geometry), hub.boss_diameter_m, hub.boss_thickness_m, z_centre, holes, diameters
    )


def _motor_clearance(hub) -> float:
    from prandtl_prop.cad.hub import SCREWS

    return SCREWS[hub.motor_screw].clearance_medium


def _stage_beam(design: DesignCase, run: StageRun, upstream: dict[str, StageRun]) -> tuple[dict[str, Any], list[str]]:
    from prandtl_prop.structures.beam import solve_beam
    from prandtl_prop.structures.loads import map_station_loads

    material = design.structure.material
    op = _operating_point(design)
    sections = design_sections(design, upstream["aero"])
    loads = map_station_loads(
        _read_csv(upstream["aero"].output("stations.csv")),
        sections,
        blade_count=op.blade_count,
        omega_rad_s=op.omega_rad_s,
        rho_air_kg_m3=op.rho_kg_m3,
        material_density_kg_m3=material.density_kg_m3,
        include_centrifugal=design.structure.include_centrifugal,
        include_pitching_moment=design.structure.include_aero_pitching_moment,
    )
    result = solve_beam(
        sections,
        loads,
        E_Pa=material.E_Pa,
        nu=material.nu,
        density_kg_m3=material.density_kg_m3,
        allowable_stress_Pa=material.allowable_stress_Pa,
    )
    _write_csv(run.path("line_loads.csv"), loads.rows())
    _write_csv(run.path("beam_results.csv"), result.rows)
    warnings = [] if result.metrics["margin_of_safety"] >= 0.0 else ["Negative margin of safety against the allowable stress"]
    return result.metrics, warnings


def _stage_fea(design: DesignCase, run: StageRun, upstream: dict[str, StageRun]) -> tuple[dict[str, Any], list[str]]:
    import numpy as np

    from prandtl_prop.structures import calculix
    from prandtl_prop.structures.loads import map_station_loads

    material = design.structure.material
    op = _operating_point(design)
    sections = design_sections(design, upstream["aero"])
    station_rows = _read_csv(upstream["aero"].output("stations.csv"))
    # Centrifugal load is applied exactly by CENTRIF, so only aerodynamic line loads are mapped.
    aero_loads = map_station_loads(
        station_rows,
        sections,
        blade_count=op.blade_count,
        omega_rad_s=op.omega_rad_s,
        rho_air_kg_m3=op.rho_kg_m3,
        material_density_kg_m3=material.density_kg_m3,
        include_centrifugal=False,
        include_pitching_moment=design.structure.include_aero_pitching_moment,
    )
    mesh = calculix.mesh_solid(upstream["cad"].output("blade_m.brep"), design.fea.mesh_size_m)
    root_x = float(mesh.nodes[:, 0].min())
    clamp, inner_edge, clamp_zone_end = _root_boundary(design, sections, mesh)
    weights = np.array([float(row["dr_m"]) for row in station_rows])
    forces = calculix.strip_nodal_forces(mesh, sections, aero_loads, weights, root_x_m=root_x, inner_edge_m=inner_edge)
    deck = calculix.write_input_deck(
        run.path("blade.inp"),
        mesh,
        E_Pa=material.E_Pa,
        nu=material.nu,
        density_kg_m3=material.density_kg_m3,
        omega_rad_s=op.omega_rad_s if design.structure.include_centrifugal else 0.0,
        nodal_forces=forces,
        root_x_m=root_x,
        nlgeom=design.fea.nlgeom,
        clamp_nodes=clamp,
    )
    result = calculix.run_calculix(
        deck, ccx_executable=design.fea.ccx_executable, threads=design.fea.threads, timeout_s=design.fea.timeout_s
    )
    metrics, rows = calculix.summarize(
        mesh, result, sections, allowable_stress_Pa=material.allowable_stress_Pa, clamp_zone_end_m=clamp_zone_end
    )
    if design.root.enabled:
        if design.hub.variant == "bolted":  # folding and one-piece joints are checked in the hub stage
            metrics.update(_bolt_checks(design, sections, upstream["cad"].metrics))
    beam = upstream["beam"].metrics
    metrics["applied_aero_force_N"] = forces.sum(axis=0).tolist()
    metrics["tip_deflection_z_ratio_fea_over_beam"] = metrics["tip_deflection_z_m"] / beam["tip_deflection_z_m"]
    metrics["root_centrifugal_ratio_fea_over_beam"] = -metrics["root_reaction_x_N"] / beam["root_centrifugal_force_N"]
    _write_csv(run.path("fea_station_stress.csv"), rows)
    for name in ("blade.dat", "blade.sta", "blade.cvg", "spooles.out"):
        if run.path(name).exists() and name != "blade.dat":
            run.path(name).unlink()
    metrics["mesh_curved_elements"] = mesh.curved
    warnings = _mesh_warnings(mesh)
    if metrics["margin_of_safety_outside_clamp_zone"] < 0.0:
        warnings.append("Negative margin of safety outside the clamp zone")
    return metrics, warnings


def _mesh_warnings(mesh) -> list[str]:
    warnings = []
    if mesh.min_quality < 0.01:  # curved C3D10 at a sharp trailing edge typically reach ~0.03 with no inverted elements
        warnings.append(f"Poor mesh quality (min SICN {mesh.min_quality:.3f}); refine fea.mesh_size_m")
    if not mesh.curved:
        warnings.append("gmsh's high-order optimiser aborted; meshed with straight-sided C3D10 (geometry faceted at element size)")
    return warnings


def _root_boundary(design: DesignCase, sections: list[BladeSection], mesh) -> tuple[np.ndarray | None, float | None, float | None]:
    """Clamp nodes, first aero-strip inner edge, and clamp-zone end for the full blade (None for a plain blade)."""

    if not design.root.enabled:
        return None, None, None
    from prandtl_prop.cad.root import tab_outline_3d
    from prandtl_prop.structures import calculix

    root = design.root
    tab = tab_outline_3d(sections, root.geometry())
    z_centre = 0.5 * float(tab[:, 2].max() + tab[:, 2].min())
    inner_edge = sections[0].r_m - 0.5 * (sections[1].r_m - sections[0].r_m)
    if design.hub.variant == "one_piece":  # boss faces under the cap and on the motor, plus the cut at the axis
        radius = 0.5 * design.hub.boss_diameter_m
        clamp = calculix.hub_clamp_nodes(mesh, radius, design.hub.boss_thickness_m, z_centre)
        return clamp, inner_edge, radius + root.tab_width_m
    clamp = calculix.tab_clamp_nodes(mesh, root.tab_outer_radius_m, root.tab_thickness_m, z_centre)
    return clamp, inner_edge, root.tab_outer_radius_m + root.tab_width_m


def _clamp_radius(design: DesignCase) -> float | None:
    """Where the hub stops supporting the blade in flap (None: the tab's outer edge)."""

    return 0.5 * design.hub.boss_diameter_m if design.root.enabled and design.hub.variant == "one_piece" else None


def _bolt_checks(design: DesignCase, sections: list[BladeSection], cad_metrics: dict[str, Any]) -> dict[str, float]:
    """Root bolts after any loss of clamp preload: bolt shear, bearing on the tab, net tension, and shear-out.

    The bolts take the whole centrifugal load, each in double shear, including the couple from the
    load line (which runs through the rotation axis) missing the bolt-group centroid
    (``prandtl_prop.cad.hub.root_joint_loads``). The hub stage checks the friction path as well.
    """

    from prandtl_prop.cad.hub import root_joint_loads
    from prandtl_prop.cad.root import bolt_centres

    root = design.root
    d, t, w = root.bolt_diameter_m, root.tab_thickness_m, root.tab_width_m
    count = len(root.bolt_radii_m)
    bolts = np.array([c[:2] for c in bolt_centres(sections, root.geometry())])
    joint = root_joint_loads(cad_metrics["blade_mass_kg"], cad_metrics["blade_center_of_mass_m"], design.operating_point.rpm, bolts)
    per_bolt, radial = joint["bolt_max_shear_N"], joint["centrifugal_force_N"]
    edge = min(root.bolt_radii_m) - root.tab_inner_radius_m
    allowable = design.structure.material.allowable_stress_Pa
    checks = {
        "bolt_count": count,
        "root_inplane_moment_Nm": joint["root_inplane_moment_Nm"],
        "bolt_max_shear_N": per_bolt,
        "bolt_double_shear_stress_Pa": per_bolt / (2.0 * math.pi * (0.5 * d) ** 2),
        "tab_bearing_stress_Pa": per_bolt / (d * t),
        "tab_net_section_stress_Pa": radial / ((w - d) * t),
        "tab_shear_out_stress_Pa": per_bolt / (2.0 * edge * t),
        "tab_edge_distance_over_diameter": edge / d,
    }
    checks["tab_min_margin_vs_allowable"] = (
        allowable / max(checks["tab_bearing_stress_Pa"], 3.0 * checks["tab_net_section_stress_Pa"], 2.0 * checks["tab_shear_out_stress_Pa"])
        - 1.0
    )
    return checks


def _stage_modal(design: DesignCase, run: StageRun, upstream: dict[str, StageRun]) -> tuple[dict[str, Any], list[str]]:
    """Campbell diagram: prestressed modes at several rotor speeds, checked against per-rev excitation."""

    from prandtl_prop.structures import calculix

    material = design.structure.material
    spec = design.fea
    op = _operating_point(design)
    mesh = calculix.mesh_solid(upstream["cad"].output("blade_m.brep"), spec.mesh_size_m)
    root_x = float(mesh.nodes[:, 0].min())
    clamp = _root_boundary(design, design_sections(design, upstream["aero"]), mesh)[0] if design.root.enabled else None
    rows: list[dict[str, Any]] = []
    design_modes: list[calculix.Mode] = []
    for fraction in sorted(set(spec.campbell_rpm_fractions)):
        rpm = op.rpm * fraction
        deck = calculix.write_modal_deck(
            run.path(f"modal_{int(round(rpm)):05d}rpm.inp"),
            mesh,
            E_Pa=material.E_Pa,
            nu=material.nu,
            density_kg_m3=material.density_kg_m3,
            omega_rad_s=rpm * 2.0 * math.pi / 60.0,
            root_x_m=root_x,
            modes=spec.modes,
            clamp_nodes=clamp,
        )
        modes = calculix.run_modal(deck, mesh, ccx_executable=spec.ccx_executable, threads=spec.threads, timeout_s=spec.timeout_s)
        for mode in modes:
            rows.append({"rpm": rpm, "mode": mode.number, "kind": mode.kind, "frequency_hz": mode.frequency_hz})
        if fraction == 1.0:
            design_modes = modes
        for path in run.directory.glob("modal_*"):
            if path.suffix not in (".inp", ".dat", ".log"):
                path.unlink()
    _write_csv(run.path("campbell.csv"), rows)
    rev_hz = op.rpm / 60.0
    southwell = _southwell_check(rows)
    metrics: dict[str, Any] = {"design_rpm": op.rpm, "one_per_rev_hz": rev_hz}
    warnings: list[str] = _mesh_warnings(mesh)
    worst = math.inf
    for mode in design_modes:
        nearest = min(range(1, spec.excitation_harmonics + 1), key=lambda k: abs(mode.frequency_hz - k * rev_hz))
        margin = abs(mode.frequency_hz - nearest * rev_hz) / (nearest * rev_hz)  # separation as a fraction of the excitation
        metrics[f"mode{mode.number}_{mode.kind}_hz"] = mode.frequency_hz
        metrics[f"mode{mode.number}_nearest_excitation"] = f"{nearest}P"
        metrics[f"mode{mode.number}_margin"] = margin
        worst = min(worst, margin)
        if margin < spec.frequency_margin:
            warnings.append(
                f"Mode {mode.number} ({mode.kind}, {mode.frequency_hz:.1f} Hz) is within {margin:.1%} of {nearest}P at design rpm"
            )
    metrics["mesh_curved_elements"] = mesh.curved
    metrics["min_frequency_margin"] = worst
    # Over the operating band, using every Campbell speed that falls inside it (include the band edges in
    # fea.campbell_rpm_fractions to check them).
    lo, hi = design.molding.operating_rpm_fractions
    band_rows = [row for row in rows if lo * op.rpm - 1e-6 <= row["rpm"] <= hi * op.rpm + 1e-6 and row["rpm"] > 0]
    band_margin = min(
        (
            abs(row["frequency_hz"] - k * row["rpm"] / 60.0) / (k * row["rpm"] / 60.0)
            for row in band_rows
            for k in range(1, spec.excitation_harmonics + 1)
        ),
        default=float("nan"),
    )
    metrics["min_frequency_margin_over_band"] = band_margin
    metrics["band_rpm_checked"] = sorted({row["rpm"] for row in band_rows})
    if band_margin == band_margin and band_margin < spec.frequency_margin:
        warnings.append(f"A mode is within {band_margin:.1%} of a per-rev line inside the operating band {lo:.2f}-{hi:.2f} x rpm")
    metrics.update({f"southwell_{key}": value for key, value in southwell.items()})
    if southwell.get("first_flap_fit_max_error_fraction", 0.0) > 0.02:
        warnings.append("First flap mode does not follow f^2 = f0^2 + K n^2 across rpm (>2% misfit): check the modal results")
    return metrics, warnings


def _southwell_check(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Fit f^2 = f0^2 + K n^2 to the first flap mode over rpm; a large misfit flags a bad eigen-solution."""

    import numpy as np

    points = []
    for rpm in sorted({row["rpm"] for row in rows}):
        flap = [row["frequency_hz"] for row in rows if row["rpm"] == rpm and row["kind"] == "flap"]
        if flap:
            points.append((rpm / 60.0, min(flap)))
    if len(points) < 3:
        return {}
    n, f = np.array(points).T
    design = np.column_stack([np.ones_like(n), n**2])
    (f0_squared, k), *_ = np.linalg.lstsq(design, f**2, rcond=None)
    fitted = np.sqrt(np.maximum(design @ np.array([f0_squared, k]), 0.0))
    return {
        "first_flap_f0_hz": float(np.sqrt(max(f0_squared, 0.0))),
        "first_flap_coefficient": float(k),
        "first_flap_fit_max_error_fraction": float(np.max(np.abs(fitted - f) / f)),
    }


def _stage_mold(design: DesignCase, run: StageRun, upstream: dict[str, StageRun]) -> tuple[dict[str, Any], list[str]]:
    """Two-plate core/cavity inserts (prandtl_prop.manufacturing.mold), STEP in mm, plus an exploded render."""

    from prandtl_prop.cad.render import render_mold
    from prandtl_prop.manufacturing.mold import build_mold, build_one_piece_mold, export_mold

    sections = design_sections(design, upstream["aero"])
    if design.root.enabled and design.hub.variant == "one_piece":
        from prandtl_prop.cad.hub_variants import _motor_holes
        from prandtl_prop.cad.root import full_blade_outlines, tab_outline_3d

        geometry, hub = design.root.geometry(), design.hub
        tab = tab_outline_3d(sections, geometry)
        result = build_one_piece_mold(
            full_blade_outlines(sections, geometry),
            hub.boss_diameter_m,
            hub.boss_thickness_m,
            0.5 * float(tab[:, 2].max() + tab[:, 2].min()),
            [(x, y, _motor_clearance(hub)) for x, y in _motor_holes(hub)],
            design.mold,
        )
    elif design.root.enabled:
        from prandtl_prop.cad.root import bolt_centres, full_blade_outlines

        geometry = design.root.geometry()
        result = build_mold(
            full_blade_outlines(sections, geometry), bolt_centres(sections, geometry), geometry.bolt_diameter_m, design.mold
        )
    else:
        result = build_mold([section.points_3d() for section in sections], design=design.mold)
    paths = export_mold(result, run.directory)
    lift = 0.6 * (result.metrics["insert_size_mm"][2] / 1000.0)
    render_mold(
        [
            (paths["cavity_stl"], (0.62, 0.64, 0.68), (0.0, 0.0, -lift)),
            (paths["part_stl"], (0.20, 0.22, 0.25), (0.0, 0.0, 0.0)),
            (paths["core_stl"], (0.80, 0.55, 0.30), (0.0, 0.0, lift)),
        ],
        run.path("mold_render.png"),
        title=f"{design.name}: two-plate mold, cavity (bottom, fixed) and core (top, moving)",
    )
    metrics = dict(result.metrics)
    warnings = []
    if not (metrics["core_valid"] and metrics["cavity_valid"]):
        warnings.append("Mold insert failed OpenCascade validity check")
    if not metrics["clearance_ok"]:
        warnings.append("Cooling channel steel clearance below 3 mm")
    return metrics, warnings


def _stage_hub(design: DesignCase, run: StageRun, upstream: dict[str, StageRun]) -> tuple[dict[str, Any], list[str]]:
    """Center hub by ``hub.variant``: bolted (prandtl_prop.cad.hub), folding or one-piece (prandtl_prop.cad.hub_variants)."""

    import cadquery as cq

    from prandtl_prop.cad import hub as hub_module
    from prandtl_prop.cad import hub_variants
    from prandtl_prop.cad.render import render_mold
    from prandtl_prop.cad.root import bolt_centres, full_blade_outlines, tab_outline_3d
    from prandtl_prop.cad.sections import polygon_properties

    if not design.root.enabled:
        raise ValueError("The hub stage needs the blade root tab: set root.enabled: true")
    sections = design_sections(design, upstream["aero"])
    geometry, spec = design.root.geometry(), design.hub
    cad, aero = upstream["cad"].metrics, upstream["aero"].metrics
    op = _operating_point(design)
    blades = design.operating_point.blade_count
    stations = [
        (float(row["r_m"]), float(row["dr_m"]), float(row["qz_N_per_m"]) / blades, float(row["qy_tangential_N_per_m"]) / blades)
        for row in _read_csv(upstream["aero"].output("stations.csv"))
    ]

    def flap_moment(about_r: float) -> float:
        return sum(qz * (r - about_r) * dr for r, dr, qz, _ in stations if r > about_r)

    tab = tab_outline_3d(sections, geometry)
    blade = cq.Shape.importBrep(str(upstream["cad"].output("blade_m.brep")))
    common = dict(rpm=op.rpm, thrust_N=float(aero["thrust_N"]), torque_Nm=float(aero["torque_Nm"]), design=spec)
    extra: dict[str, Any] = {}
    if spec.variant == "bolted":
        result = hub_module.build_hub(
            geometry,
            bolt_centres(sections, geometry),
            tab,
            blade_mass_kg=cad["blade_mass_kg"],
            blade_cg_m=cad["blade_center_of_mass_m"],
            blade_count=blades,
            root_flap_moment_Nm=flap_moment(geometry.tab_outer_radius_m),
            tab_allowable_Pa=design.structure.material.allowable_stress_Pa,
            **common,
        )
        assembly = [blade, blade.rotate(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), 180.0)]
    elif spec.variant == "folding":
        pin = bolt_centres(sections, geometry)[0]
        aero_moment = sum(-(r - pin[0]) * qy * dr for r, dr, _, qy in stations)
        result = hub_variants.build_folding_hub(
            geometry,
            pin,
            tab,
            full_blade_outlines(sections, geometry),
            blade_mass_kg=cad["blade_mass_kg"],
            blade_cg_m=cad["blade_center_of_mass_m"],
            blade_inertia_cg_zz=cad["blade_inertia_cg_zz_kg_m2"],
            aero_moment_about_pin_Nm=aero_moment,
            root_flap_moment_Nm=flap_moment(geometry.tab_outer_radius_m),
            tab_allowable_Pa=design.structure.material.allowable_stress_Pa,
            **common,
        )
        assembly = hub_variants.fold_blades(blade, pin, 0.0)
        extra["folded"] = hub_variants.fold_blades(blade, pin, spec.fold_angle_deg)
    elif spec.variant == "one_piece":
        props = polygon_properties(tab[:, 1:])
        neck = type("Neck", (), {"area_m2": props.area_m2, "I_flap_m4": props.I_ss_m4, "height_m": float(np.ptp(tab[:, 2]))})()
        result = hub_variants.build_one_piece_hub(
            0.5 * float(tab[:, 2].max() + tab[:, 2].min()),
            half_mass_kg=cad["blade_mass_kg"],
            half_cg_m=cad["blade_center_of_mass_m"],
            flap_moment_at_axis_Nm=flap_moment(0.0),
            flap_moment_at_boss_edge_Nm=flap_moment(0.5 * spec.boss_diameter_m),
            neck_section=neck,
            prop_allowable_Pa=design.structure.material.allowable_stress_Pa,
            prop_safety_factor=design.molding.stress_safety_factor,
            **common,
        )
        assembly = [cq.Shape.importBrep(str(upstream["cad"].output("prop_m.brep")))]
    else:
        raise ValueError(f"hub.variant must be bolted, folding, or one_piece, not {spec.variant!r}")
    paths = hub_module.export_hub(result, run.directory, assembly)
    parts_stl = [path for key, path in paths.items() if key.endswith("_stl") and key not in ("blades_stl", "fasteners_stl")]
    metal = (0.72, 0.74, 0.78)
    lift = 0.03
    scene = [(paths["blades_stl"], (0.16, 0.17, 0.19), (0.0, 0.0, 0.0)), (paths["fasteners_stl"], (0.85, 0.65, 0.25), (0.0, 0.0, 0.0))]
    for path in parts_stl:
        z = lift if "top" in path.name or "cap" in path.name else -lift if "bottom" in path.name else 0.0
        scene.append((path, metal, (0.0, 0.0, z)))
    title = {"bolted": "bolted hub", "folding": "folding hub (one hinge pin per blade)", "one_piece": "one-piece prop with clamp cap"}[
        spec.variant
    ]
    render_mold(
        scene,
        run.path("hub_render.png"),
        title=f"{design.name}: {title}, parts exploded along the rotor axis",
        views=(("From above the leading edge", 35.0, 60.0), ("From below (motor side)", -35.0, 120.0)),
        clip_radius_m=0.075,
    )
    assembled = [(paths["blades_stl"], (0.16, 0.17, 0.19), (0.0, 0.0, 0.0)), (paths["fasteners_stl"], (0.85, 0.65, 0.25), (0.0, 0.0, 0.0))]
    assembled += [(path, metal, (0.0, 0.0, 0.0)) for path in parts_stl]
    render_mold(
        assembled, run.path("prop_render.png"), title=f"{design.name}: {title}", views=(("3/4 view", 30.0, 60.0), ("Top view", 89.0, 90.0))
    )
    if "folded" in extra:
        from prandtl_prop.cad.step import export_stl

        folded = export_stl(cq.Compound.makeCompound(extra["folded"]), run.path("hub_blades_folded_m.stl"), 2e-4)
        render_mold(
            [(folded, (0.16, 0.17, 0.19), (0.0, 0.0, 0.0))] + assembled[1:],
            run.path("folded_render.png"),
            title=f"{design.name}: folded for stowage ({spec.fold_angle_deg:.0f} deg, both blades to one side)",
            views=(("3/4 view", 30.0, 60.0), ("Top view", 89.0, 90.0)),
        )
    metrics = dict(result.metrics)
    metrics["root_flap_moment_at_clamp_Nm"] = flap_moment(_clamp_radius(design) or geometry.tab_outer_radius_m)
    return metrics, list(result.warnings)


def _stage_envelope(design: DesignCase, run: StageRun, upstream: dict[str, StageRun]) -> tuple[dict[str, Any], list[str]]:
    """Off-design mission envelope (prandtl_prop.mission): BEMT map over V and rpm, rpm-trimmed mission points."""

    from prandtl_prop import mission

    data = mission.envelope(design, upstream["aero"])
    _write_csv(run.path("envelope_map.csv"), data["grid"])
    _write_csv(run.path("mission_points.csv"), data["points"])
    for point, result in zip(data["points"], data["solutions"], strict=True):
        _write_csv(run.path(f"stations_{point['name']}.csv"), result.station_rows())
    mission.plot_envelope(data, design, run.path("envelope.png"))
    metrics: dict[str, Any] = {"collective_offset_deg": data["collective_offset_deg"], "mission_points": data["points"]}
    warnings = []
    for point in data["points"]:
        if not point["rpm_within_limit"]:
            warnings.append(f"{point['name']}: needs {point['rpm']:.0f} rpm, above the {design.mission.max_rpm:.0f} rpm limit")
        if point["stalled_station_count"]:
            warnings.append(f"{point['name']}: {point['stalled_station_count']} station(s) at or past Cl = {design.mission.stall_cl}")
        if point["negative_loading_station_count"]:
            warnings.append(f"{point['name']}: {point['negative_loading_station_count']} station(s) with negative loading")
    return metrics, warnings


def _stage_mission(design: DesignCase, run: StageRun, upstream: dict[str, StageRun]) -> tuple[dict[str, Any], list[str]]:
    """CFD at the mission points (shared mesh), flow plots, and CFD-loaded FEA (prandtl_prop.mission.run_mission)."""

    from prandtl_prop import mission

    return mission.run_mission(design, run, upstream)


def _stage_cfd(design: DesignCase, run: StageRun, upstream: dict[str, StageRun]) -> tuple[dict[str, Any], list[str]]:
    import os
    import shutil

    import numpy as np

    from prandtl_prop.cfd import openfoam

    spec = design.cfd
    op = _operating_point(design)
    stl = upstream["cad"].output("rotor_m.stl")
    if not stl.is_file():
        raise FileNotFoundError("cfd needs the rotor STL; enable cad.export_rotor")
    processors = spec.processors or os.cpu_count() or 1
    nu = op.mu_air_kg_m_s / op.rho_kg_m3
    params = openfoam.CaseParameters(
        radius_m=op.radius_m,
        omega_rad_s=op.omega_rad_s,
        nu_m2_s=nu,
        rho_kg_m3=op.rho_kg_m3,
        blade_z_extent_m=openfoam.blade_z_extent(stl),
        side_factor=spec.side_factor,
        above_factor=spec.above_factor,
        below_factor=spec.below_factor,
        background_cells_per_radius=spec.background_cells_per_radius,
        mrf_radius_factor=spec.mrf_radius_factor,
        mrf_refinement_level=spec.mrf_refinement_level,
        surface_refinement_levels=(spec.surface_refinement_min, spec.surface_refinement_max),
        feature_refinement_level=spec.feature_refinement_level,
        iterations=spec.iterations,
        snapshots=spec.snapshots,
        processors=processors,
        function_objects=spec.function_objects,
        v_inf_m_s=op.v_inf_m_s,
    )
    case = openfoam.write_case(run.path("case"), stl, params)
    openfoam.run_case(case, processors=processors, bashrc=spec.openfoam_bashrc, timeout_s=spec.timeout_s)

    mesh = openfoam.read_mesh(case)
    history = []
    for time_dir in openfoam.solution_times(case):
        wall = openfoam.wall_loads(case, time_dir, rho_kg_m3=op.rho_kg_m3, nu_m2_s=nu, mesh=mesh)
        far = openfoam.farfield_thrust(case, time_dir, rho_kg_m3=op.rho_kg_m3, mesh=mesh, v_inf_m_s=op.v_inf_m_s)
        torque = -float((wall.centres[:, 0] * wall.force_N[:, 1] - wall.centres[:, 1] * wall.force_N[:, 0]).sum())
        history.append(
            {
                "iteration": float(time_dir.name),
                "thrust_wall_N": float(wall.force_N[:, 2].sum()),
                "thrust_farfield_N": far["thrust_N"],
                "torque_Nm": torque,
                "mass_imbalance_fraction": far["net_volume_flux_m3_s"] / max(far["outflow_m3_s"], 1.0e-12),
            }
        )
    _write_csv(run.path("cfd_history.csv"), history)

    station_rows = _read_csv(upstream["aero"].output("stations.csv"))
    radii = np.array([float(row["r_m"]) for row in station_rows])
    dT, dQ = openfoam.station_loads(wall, radii)
    comparison = [
        {
            "r_R": float(row["r_R"]),
            "r_m": float(row["r_m"]),
            "bemt_dT_N": float(row["dT_N"]),
            "cfd_dT_N": float(dT[index]),
            "bemt_dQ_Nm": float(row["dQ_Nm"]),
            "cfd_dQ_Nm": float(dQ[index]),
        }
        for index, row in enumerate(station_rows)
    ]
    _write_csv(run.path("cfd_station_loads.csv"), comparison)
    residuals = openfoam.residual_history(case)
    final, previous = history[-1], history[-2] if len(history) > 1 else history[-1]
    thrust, torque = final["thrust_wall_N"], final["torque_Nm"]
    bemt = upstream["aero"].metrics
    power = torque * op.omega_rad_s
    ideal = thrust**1.5 / math.sqrt(2.0 * op.rho_kg_m3 * op.disk_area_m2) if thrust > 0 else math.nan
    metrics: dict[str, Any] = {
        "cell_count": int(max(mesh.owner.max(), mesh.neighbour.max())) + 1,
        "thrust_N": thrust,
        "torque_Nm": torque,
        "power_W": power,
        "figure_of_merit": ideal / power if power > 0 else math.nan,
        "thrust_ratio_cfd_over_bemt": thrust / float(bemt["thrust_N"]),
        "torque_ratio_cfd_over_bemt": torque / float(bemt["torque_Nm"]),
        "wall_vs_farfield_thrust_difference_fraction": (thrust - final["thrust_farfield_N"]) / thrust if thrust else math.nan,
        "mass_imbalance_fraction": final["mass_imbalance_fraction"],
        "last_interval_thrust_change_fraction": abs(final["thrust_wall_N"] - previous["thrust_wall_N"]) / abs(thrust)
        if thrust
        else math.nan,
        "last_interval_torque_change_fraction": abs(final["torque_Nm"] - previous["torque_Nm"]) / abs(torque) if torque else math.nan,
        "final_residuals": {name: values[-1] for name, values in residuals.items()},
        "geometry_collective_offset_deg": float(bemt.get("collective_offset_deg", 0.0)) if design.cad.apply_trim_offset else 0.0,
    }
    warnings = []
    if abs(metrics["wall_vs_farfield_thrust_difference_fraction"]) > 0.02:
        warnings.append("Wall-integrated and far-field momentum thrust differ by more than 2%: check convergence and domain size")
    if metrics["last_interval_thrust_change_fraction"] > 0.005:
        warnings.append("Thrust still changing by more than 0.5% between the last snapshots: run more iterations")
    if not spec.keep_case:
        shutil.rmtree(case)
    else:
        for path in case.glob("processor*"):
            shutil.rmtree(path)
    return metrics, warnings


STAGE_FUNCTIONS: dict[str, Callable[[DesignCase, StageRun, dict[str, StageRun]], tuple[dict[str, Any], list[str]]]] = {
    "aero": _stage_aero,
    "sections": _stage_sections,
    "dfm": _stage_dfm,
    "cad": _stage_cad,
    "beam": _stage_beam,
    "fea": _stage_fea,
    "modal": _stage_modal,
    "mold": _stage_mold,
    "hub": _stage_hub,
    "cfd": _stage_cfd,
    "envelope": _stage_envelope,
    "mission": _stage_mission,
}

STAGE_DESIGN_INPUTS = {
    "aero": ("operating_point", "geometry", "aero", "airfoils.thickness_scale", "airfoils.trailing_edge_thickness_m"),
    "sections": ("geometry", "airfoils", "cad.apply_trim_offset"),
    "dfm": ("operating_point", "structure", "molding", "root", "hub"),
    "cad": ("geometry", "cad", "operating_point", "structure", "root", "hub"),
    "beam": ("operating_point", "structure"),
    "fea": ("operating_point", "structure", "fea", "hub"),
    "modal": ("operating_point", "structure", "fea", "hub"),
    "mold": ("mold", "root", "hub"),
    "hub": ("hub", "root", "operating_point", "structure"),
    "cfd": ("operating_point", "cfd"),
    "envelope": ("operating_point", "geometry", "aero", "airfoils.thickness_scale", "airfoils.trailing_edge_thickness_m", "mission"),
    "mission": ("operating_point", "structure", "fea", "cfd", "mission", "root", "hub"),
}


# -------------------------------------------------------------------------------------- helpers


def design_sections(design: DesignCase, aero_run: StageRun) -> list[BladeSection]:
    """Rebuild the design's 3D sections at the trimmed twist the aero stage settled on."""

    rows = _read_csv(resolve_path(design.geometry.stations_csv))
    stations = BladeStations.from_rows(rows, design.operating_point.radius_m)
    directory = resolve_path(design.airfoils.directory)
    cache: dict[str, Any] = {}
    airfoils = []
    for name in stations.airfoil:
        if name not in cache:
            cache[name] = load_airfoil(name, directory, design.airfoils.points_per_side, design.airfoils.substitutes)
        airfoils.append(cache[name])
    offset = _trim_offset(design, aero_run)
    sections = build_sections(
        stations.r_m, stations.chord_m, stations.twist_deg + offset, airfoils, design.geometry.pitch_axis_chord_fraction
    )
    return apply_manufacturing(design, sections)


def apply_manufacturing(design: DesignCase, sections: list[BladeSection]) -> list[BladeSection]:
    """Apply the design's thickness schedule and minimum trailing-edge thickness (camber preserved)."""

    spec = design.airfoils
    if not spec.thickness_scale and spec.trailing_edge_thickness_m is None:
        return sections
    import numpy as np

    from prandtl_prop.manufacturing.injection import molded_sections, thickness_schedule

    r_R = np.array([section.r_m for section in sections]) / design.operating_point.radius_m
    scales = thickness_schedule(r_R, [tuple(point) for point in spec.thickness_scale])
    return molded_sections(sections, scales, spec.trailing_edge_thickness_m)


def _trim_offset(design: DesignCase, aero_run: StageRun) -> float:
    return float(aero_run.metrics.get("collective_offset_deg", 0.0)) if design.cad.apply_trim_offset else 0.0


def _thickness_drag_factors(design: DesignCase, stations: BladeStations, radius_m: float):
    """Hoerner form-factor ratio FF(t'/c) / FF(t/c) per station when the design modifies section thickness."""

    spec = design.airfoils
    if not spec.thickness_scale and spec.trailing_edge_thickness_m is None:
        return None
    from prandtl_prop.manufacturing.injection import hoerner_form_factor, modify_airfoil, thickness_schedule

    directory = resolve_path(spec.directory)
    scales = thickness_schedule(stations.r_m / radius_m, [tuple(point) for point in spec.thickness_scale])
    factors = np.ones(len(stations.r_m))
    for index, (name, scale, chord) in enumerate(zip(stations.airfoil, scales, stations.chord_m, strict=True)):
        base = load_airfoil(name, directory, spec.points_per_side, spec.substitutes)
        modified = modify_airfoil(base, float(scale), (spec.trailing_edge_thickness_m or 0.0) / float(chord))
        factors[index] = float(hoerner_form_factor(modified.max_thickness) / hoerner_form_factor(base.max_thickness))
    return factors


def _corrections(design: DesignCase, stations: BladeStations, radius_m: float):
    if design.aero.corrections_csv is None:
        return None
    from prandtl_prop.aero.corrections import read_corrections

    return read_corrections(resolve_path(design.aero.corrections_csv), stations.r_m / radius_m)


def _operating_point(design: DesignCase) -> OperatingPoint:
    spec = design.operating_point
    return OperatingPoint(
        radius_m=spec.radius_m,
        blade_count=spec.blade_count,
        rpm=spec.rpm,
        rho_kg_m3=spec.rho_kg_m3,
        mu_air_kg_m_s=spec.mu_air_kg_m_s,
        v_inf_m_s=spec.v_inf_m_s,
    )


def _polar(design: DesignCase):
    spec = design.aero.polar
    if spec.type == "analytic":
        return AnalyticPolar(spec.cl_alpha_per_rad, spec.alpha0_deg, spec.cd0, spec.k, spec.cl_max, spec.cl_min)
    directory = resolve_path(spec.directory)
    try:
        return TablePolar.from_directory(directory)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"{exc}. Copy the XFOIL polars (thesis repo: CAD/airfoils_good/generated_polars/*.csv) into {spec.directory}, "
            "or run with --set aero.polar.type=analytic for a pipeline smoke test."
        ) from exc


def _input_files(stage: str, design: DesignCase) -> list[Path]:
    geometry_csv = resolve_path(design.geometry.stations_csv)
    if stage == "aero":
        files = [geometry_csv]
        if design.aero.corrections_csv is not None:
            files.append(resolve_path(design.aero.corrections_csv))
        if design.airfoils.thickness_scale or design.airfoils.trailing_edge_thickness_m is not None:
            files += _input_files("sections", design)[1:]
        if design.aero.polar.type == "table":
            directory = resolve_path(design.aero.polar.directory)
            files += (
                TablePolar.from_directory(directory).source_files() if directory.is_dir() and any(directory.rglob("*_polar.csv")) else []
            )
        return files
    if stage == "sections":
        directory = resolve_path(design.airfoils.directory)
        names = {row["airfoil"].strip() for row in _read_csv(geometry_csv)} | set(design.airfoils.substitutes.values())
        return [geometry_csv] + [path for path in (find_airfoil_file(name, directory) for name in sorted(names)) if path]
    return []


def _code_version(stage: str) -> str:
    digest = hashlib.sha256()
    for module_name in STAGE_MODULES[stage]:
        module = importlib.import_module(module_name)
        digest.update(Path(module.__file__).read_bytes())
    return digest.hexdigest()[:16]


def _run_stage(stage: str, design: DesignCase, upstream: dict[str, StageRun], store: RunStore, force: bool) -> StageOutcome:
    upstream_keys = {name: run.key for name, run in upstream.items()}
    design_inputs = design.section(*STAGE_DESIGN_INPUTS[stage])
    input_files = _input_files(stage, design)
    key = stage_key(stage=stage, version=_code_version(stage), design_inputs=design_inputs, input_files=input_files, upstream=upstream_keys)
    if not force:
        cached = store.find(design.name, stage, key)
        if cached is not None:
            return StageOutcome(stage, cached)
    run = store.start(
        design.name,
        stage,
        key,
        inputs={"design": design_inputs, "files": [str(path) for path in input_files], "upstream": upstream_keys},
    )
    metrics, warnings = STAGE_FUNCTIONS[stage](design, run, upstream)
    return StageOutcome(stage, run.finish(metrics=metrics, warnings=warnings))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"CSV has no rows: {path}")
    return rows


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: f"{value:.12g}" if isinstance(value, float) else value for key, value in row.items()})
    return path


def metrics_json(outcomes: dict[str, StageOutcome]) -> str:
    return json.dumps({stage: outcome.run.metrics for stage, outcome in outcomes.items()}, indent=2, default=str)
