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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prandtl_prop.aero import AnalyticPolar, BladeStations, OperatingPoint, TablePolar, solve_rotor, trim_to_thrust
from prandtl_prop.cad.airfoils import find_airfoil_file, load_airfoil
from prandtl_prop.cad.sections import BladeSection, build_sections, section_table
from prandtl_prop.design import DesignCase, resolve_path
from prandtl_prop.runs import RunStore, StageRun, stage_key

STAGES = ("aero", "sections", "cad", "beam", "fea")
STAGE_UPSTREAM = {
    "aero": (),
    "sections": ("aero",),
    "cad": ("aero", "sections"),
    "beam": ("aero", "sections"),
    "fea": ("aero", "sections", "cad", "beam"),
}
STAGE_MODULES = {
    "aero": ("prandtl_prop.aero.bemt", "prandtl_prop.aero.polars", "prandtl_prop.pipeline"),
    "sections": ("prandtl_prop.cad.airfoils", "prandtl_prop.cad.sections", "prandtl_prop.pipeline"),
    "cad": ("prandtl_prop.cad.step", "prandtl_prop.pipeline"),
    "beam": ("prandtl_prop.structures.loads", "prandtl_prop.structures.beam", "prandtl_prop.pipeline"),
    "fea": ("prandtl_prop.structures.loads", "prandtl_prop.structures.calculix", "prandtl_prop.pipeline"),
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
    stages: Sequence[str] = STAGES,
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
    if design.aero.trim_to_target_thrust:
        result = trim_to_thrust(stations, op, polar, float(op_spec.target_thrust_N), tip_loss=design.aero.tip_loss)
    else:
        result = solve_rotor(stations, op, polar, tip_loss=design.aero.tip_loss)
    _write_csv(run.path("stations.csv"), result.station_rows())
    metrics = dict(result.summary)
    warnings = []
    for name in ("nonconverged_station_count", "negative_loading_station_count", "alpha_clipped_station_count", "re_clipped_station_count"):
        if metrics[name]:
            warnings.append(f"{metrics[name]} station(s) flagged: {name.removesuffix('_count')}")
    if design.aero.polar.type == "analytic":
        warnings.append("Analytic polar in use: results verify the pipeline, not the airfoils.")
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


def _stage_cad(design: DesignCase, run: StageRun, upstream: dict[str, StageRun]) -> tuple[dict[str, Any], list[str]]:
    import numpy as np

    from prandtl_prop.cad import step

    sections = design_sections(design, upstream["aero"])
    blade = step.blade_solid(sections, design.geometry.hub_radius_m)
    step.export_step(blade, run.path("blade.step"))  # millimetres, for SolidWorks and other CAD
    blade.exportBrep(str(run.path("blade_m.brep")))  # metres, for meshing (CalculiX)
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
        "loft_vs_section_quadrature_volume_ratio": report.volume_m3 / quadrature_volume if design.geometry.hub_radius_m is None else None,
    }
    if design.cad.export_stl:
        step.export_stl(blade, run.path("blade_m.stl"), design.cad.stl_tolerance_m)
    if design.cad.export_solidworks_curves:
        curves = step.write_solidworks_curves(sections, run.path("solidworks_curves"))
        metrics["solidworks_curve_count"] = len(curves)
    if design.cad.export_rotor:
        rotor = step.rotor_solid(blade, design.operating_point.blade_count, design.geometry.hub_radius_m)
        step.export_step(rotor, run.path("rotor.step"))
        step.export_stl(rotor, run.path("rotor_m.stl"), design.cad.stl_tolerance_m)  # metres, for OpenFOAM
        metrics["rotor_volume_m3"] = step.report(rotor).volume_m3
    warnings = [] if report.is_valid else ["Blade solid failed OpenCascade validity check"]
    return metrics, warnings


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
    weights = np.array([float(row["dr_m"]) for row in station_rows])
    forces = calculix.strip_nodal_forces(mesh, sections, aero_loads, weights, root_x_m=root_x)
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
    )
    result = calculix.run_calculix(deck, ccx_executable=design.fea.ccx_executable, timeout_s=design.fea.timeout_s)
    metrics, rows = calculix.summarize(mesh, result, sections, allowable_stress_Pa=material.allowable_stress_Pa)
    beam = upstream["beam"].metrics
    metrics["applied_aero_force_N"] = forces.sum(axis=0).tolist()
    metrics["tip_deflection_z_ratio_fea_over_beam"] = metrics["tip_deflection_z_m"] / beam["tip_deflection_z_m"]
    metrics["root_centrifugal_ratio_fea_over_beam"] = -metrics["root_reaction_x_N"] / beam["root_centrifugal_force_N"]
    _write_csv(run.path("fea_station_stress.csv"), rows)
    for name in ("blade.dat", "blade.sta", "blade.cvg", "spooles.out"):
        if run.path(name).exists() and name != "blade.dat":
            run.path(name).unlink()
    warnings = []
    if mesh.min_quality < 0.05:
        warnings.append(f"Poor mesh quality (min SICN {mesh.min_quality:.3f}); refine fea.mesh_size_m")
    if metrics["margin_of_safety_outside_clamp_zone"] < 0.0:
        warnings.append("Negative margin of safety outside the clamp zone")
    return metrics, warnings


STAGE_FUNCTIONS: dict[str, Callable[[DesignCase, StageRun, dict[str, StageRun]], tuple[dict[str, Any], list[str]]]] = {
    "aero": _stage_aero,
    "sections": _stage_sections,
    "cad": _stage_cad,
    "beam": _stage_beam,
    "fea": _stage_fea,
}

STAGE_DESIGN_INPUTS = {
    "aero": ("operating_point", "geometry", "aero"),
    "sections": ("geometry", "airfoils", "cad.apply_trim_offset"),
    "cad": ("geometry", "cad", "operating_point", "structure"),
    "beam": ("operating_point", "structure"),
    "fea": ("operating_point", "structure", "fea"),
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
    return build_sections(stations.r_m, stations.chord_m, stations.twist_deg + offset, airfoils, design.geometry.pitch_axis_chord_fraction)


def _trim_offset(design: DesignCase, aero_run: StageRun) -> float:
    return float(aero_run.metrics.get("collective_offset_deg", 0.0)) if design.cad.apply_trim_offset else 0.0


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
