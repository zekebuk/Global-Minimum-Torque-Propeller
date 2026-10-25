"""Design-case definition: the single source of truth that every pipeline stage reads.

A design file is YAML (or JSON) with the sections below. Unknown keys are rejected so a typo cannot
silently fall back to a default. Relative paths resolve against the repository root.

    name: gmt
    operating_point: {diameter_in: 22, blade_count: 2, rpm: 4000, target_thrust_N: 35}
    geometry: {stations_csv: data/geometry/final_gmt_geometry.csv}
    aero: {polar: {type: table, directory: data/xfoil_polars}}
    airfoils: {directory: data/airfoils}
    structure: {material: {name: aluminum, E_Pa: 70.0e9, nu: 0.33, density_kg_m3: 2700, allowable_stress_Pa: 250.0e6}}
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints

import yaml

from prandtl_prop import config
from prandtl_prop.cad.hub import HubDesign
from prandtl_prop.manufacturing.mold import MoldDesign


@dataclass(frozen=True)
class OperatingPointSpec:
    diameter_in: float = config.DESIGN_DIAMETER_IN
    blade_count: int = config.DESIGN_BLADE_COUNT
    rpm: float = config.DESIGN_RPM
    target_thrust_N: float | None = config.TARGET_THRUST_N
    rho_kg_m3: float = config.AIR_DENSITY_KG_M3
    mu_air_kg_m_s: float = config.AIR_VISCOSITY_KG_M_S
    v_inf_m_s: float = 0.0

    @property
    def radius_m(self) -> float:
        return self.diameter_in * config.INCH_TO_M / 2.0


@dataclass(frozen=True)
class GeometrySpec:
    stations_csv: str
    pitch_axis_chord_fraction: float = 0.25
    hub_radius_m: float | None = None


@dataclass(frozen=True)
class PolarSpec:
    type: str = "table"
    directory: str = "data/xfoil_polars"
    cl_alpha_per_rad: float = 2.0 * math.pi
    alpha0_deg: float = 0.0
    cd0: float = 0.0
    k: float = 0.0
    cl_max: float = 1.4
    cl_min: float = -1.0


@dataclass(frozen=True)
class AeroSpec:
    polar: PolarSpec = field(default_factory=PolarSpec)
    tip_loss: bool = True
    trim_to_target_thrust: bool = True
    corrections_csv: str | None = None


@dataclass(frozen=True)
class RootSpec:
    """Clamped root tab and transition (see prandtl_prop.cad.root). Off by default: the blade then starts at the first BEMT station."""

    enabled: bool = False
    tab_inner_radius_m: float = 0.014
    tab_outer_radius_m: float = 0.034
    tab_width_m: float = 0.028
    tab_thickness_m: float = 0.0085
    corner_exponent: float = 4.0
    tab_pitch_deg: float = 0.0
    transition_sections: int = 10
    bolt_diameter_m: float = 0.0053
    bolt_radii_m: tuple[float, ...] = (0.020, 0.029)
    tab_centre_s_m: float | None = None  # None: chord midpoint (see prandtl_prop.cad.root.RootGeometry)
    tab_end: str = "square"  # "round" for a folding blade's hinge end

    def geometry(self):
        from prandtl_prop.cad.root import RootGeometry

        return RootGeometry(**{key: value for key, value in self.__dict__.items() if key != "enabled"})


@dataclass(frozen=True)
class AirfoilSpec:
    directory: str = "data/airfoils"
    points_per_side: int = 80
    substitutes: dict[str, str] = field(default_factory=dict)
    # Manufacturing modifications (camber preserved): [[r/R, thickness scale], ...] and a minimum blunt TE.
    thickness_scale: tuple[tuple[float, float], ...] = ()
    trailing_edge_thickness_m: float | None = None


@dataclass(frozen=True)
class MaterialSpec:
    name: str = "aluminum"
    E_Pa: float = 70.0e9
    nu: float = 0.33
    density_kg_m3: float = 2700.0
    allowable_stress_Pa: float = 250.0e6


@dataclass(frozen=True)
class StructureSpec:
    material: MaterialSpec = field(default_factory=MaterialSpec)
    include_centrifugal: bool = True
    include_aero_pitching_moment: bool = True


@dataclass(frozen=True)
class CadSpec:
    apply_trim_offset: bool = True
    export_rotor: bool = True
    export_stl: bool = True
    export_solidworks_curves: bool = True
    stl_tolerance_m: float = 1.0e-4


@dataclass(frozen=True)
class MoldingSpec:
    """Injection-molding rules and the operating envelope used for frequency placement."""

    min_trailing_edge_thickness_m: float = 0.0006
    max_section_thickness_m: float = 0.0065
    max_thickness_gradient: float = 0.05
    max_flow_length_ratio: float = 150.0
    min_draft_deg: float = 0.5
    pull_angle_deg: float | None = None
    operating_rpm_fractions: tuple[float, float] = (0.9, 1.1)
    frequency_margin: float = 0.10
    stress_safety_factor: float = 1.5
    max_tip_deflection_fraction: float = 0.04


@dataclass(frozen=True)
class FeaSpec:
    mesh_size_m: float = 0.003
    nlgeom: bool = True
    ccx_executable: str | None = None
    threads: int = 1
    timeout_s: int = 3600
    modes: int = 6
    campbell_rpm_fractions: tuple[float, ...] = (0.0, 0.5, 0.9, 1.0, 1.1, 1.2)
    excitation_harmonics: int = 4
    frequency_margin: float = 0.10


@dataclass(frozen=True)
class CfdSpec:
    side_factor: float = 3.0
    above_factor: float = 3.0
    below_factor: float = 6.0
    background_cells_per_radius: int = 4
    mrf_radius_factor: float = 1.08
    mrf_refinement_level: int = 2
    surface_refinement_min: int = 5
    surface_refinement_max: int = 6
    feature_refinement_level: int = 6
    iterations: int = 1500
    snapshots: int = 5
    processors: int = 0
    function_objects: bool = False
    openfoam_bashrc: str | None = None
    keep_case: bool = True
    timeout_s: int = 6 * 3600


@dataclass(frozen=True)
class DesignCase:
    name: str
    geometry: GeometrySpec
    description: str = ""
    operating_point: OperatingPointSpec = field(default_factory=OperatingPointSpec)
    aero: AeroSpec = field(default_factory=AeroSpec)
    airfoils: AirfoilSpec = field(default_factory=AirfoilSpec)
    structure: StructureSpec = field(default_factory=StructureSpec)
    cad: CadSpec = field(default_factory=CadSpec)
    fea: FeaSpec = field(default_factory=FeaSpec)
    cfd: CfdSpec = field(default_factory=CfdSpec)
    molding: MoldingSpec = field(default_factory=MoldingSpec)
    root: RootSpec = field(default_factory=RootSpec)
    mold: MoldDesign = field(default_factory=MoldDesign)
    hub: HubDesign = field(default_factory=HubDesign)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def section(self, *names: str) -> dict[str, Any]:
        """Return the named sections or dotted values; stage cache keys hash only what a stage reads."""

        data = self.to_dict()
        selected: dict[str, Any] = {}
        for name in names:
            value: Any = data
            for key in name.split("."):
                value = value[key]
            selected[name] = value
        return selected


def resolve_path(path: str | Path) -> Path:
    """Resolve a design path against the repository root."""

    candidate = Path(path)
    return candidate if candidate.is_absolute() else config.PROJECT_ROOT / candidate


def read_override_file(path: str | Path) -> list[str]:
    """Read ``key=value`` overrides, one per line; blank lines and ``#`` comments are skipped."""

    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def load_design(path: str | Path, overrides: list[str] | None = None) -> DesignCase:
    """Load a design file, applying ``dotted.key=value`` overrides (values parsed as YAML)."""

    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for override in overrides or []:
        apply_override(data, override)
    return design_from_dict(data)


def apply_override(data: dict[str, Any], override: str) -> None:
    """Apply one ``a.b.c=value`` override in place."""

    if "=" not in override:
        raise ValueError(f"Override must look like 'section.key=value': {override!r}")
    dotted, raw_value = override.split("=", 1)
    keys = dotted.strip().split(".")
    target = data
    for key in keys[:-1]:
        target = target.setdefault(key, {})
        if not isinstance(target, dict):
            raise ValueError(f"Override path {dotted!r} passes through a non-mapping value")
    target[keys[-1]] = yaml.safe_load(raw_value)


def design_from_dict(data: dict[str, Any]) -> DesignCase:
    """Build a validated ``DesignCase`` from plain data."""

    design = _build(DesignCase, copy.deepcopy(data), "design")
    _validate(design)
    return design


def canonical_json(value: Any) -> str:
    """Stable JSON used for hashing."""

    if is_dataclass(value):
        value = asdict(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _build(cls: type, data: Any, where: str) -> Any:
    if not isinstance(data, dict):
        raise ValueError(f"{where} must be a mapping, got {type(data).__name__}")
    known = {item.name: item for item in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"Unknown key(s) in {where}: {sorted(unknown)}; allowed: {sorted(known)}")
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for name, value in data.items():
        nested = _NESTED.get((cls, name))
        kwargs[name] = _build(nested, value, f"{where}.{name}") if nested else _coerce(value, hints[name], f"{where}.{name}")
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ValueError(f"Invalid {where}: {exc}") from exc


def _coerce(value: Any, hint: Any, where: str) -> Any:
    """Cast numbers by annotation. PyYAML (YAML 1.1) reads ``70.0e9`` as a string, so this matters."""

    if value is None:
        return None
    for numeric in (int, float):
        if hint is numeric or hint == numeric | None:
            if isinstance(value, bool):
                raise ValueError(f"{where} must be a number, got {value!r}")
            try:
                cast = numeric(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{where} must be a number, got {value!r}") from exc
            if numeric is int and cast != float(value):
                raise ValueError(f"{where} must be an integer, got {value!r}")
            return cast
    if hint is bool and not isinstance(value, bool):
        raise ValueError(f"{where} must be true or false, got {value!r}")
    return value


_NESTED: dict[tuple[type, str], type] = {
    (DesignCase, "geometry"): GeometrySpec,
    (DesignCase, "operating_point"): OperatingPointSpec,
    (DesignCase, "aero"): AeroSpec,
    (DesignCase, "airfoils"): AirfoilSpec,
    (DesignCase, "structure"): StructureSpec,
    (DesignCase, "cad"): CadSpec,
    (DesignCase, "fea"): FeaSpec,
    (DesignCase, "cfd"): CfdSpec,
    (DesignCase, "molding"): MoldingSpec,
    (DesignCase, "root"): RootSpec,
    (DesignCase, "mold"): MoldDesign,
    (DesignCase, "hub"): HubDesign,
    (AeroSpec, "polar"): PolarSpec,
    (StructureSpec, "material"): MaterialSpec,
}


def _validate(design: DesignCase) -> None:
    op = design.operating_point
    checks = [
        (bool(design.name.strip()) and all(char.isalnum() or char in "-_" for char in design.name), "name must be non-empty [A-Za-z0-9_-]"),
        (op.diameter_in > 0.0 and op.rpm > 0.0 and op.blade_count >= 1, "operating_point needs positive diameter, rpm, and blade_count"),
        (op.target_thrust_N is None or op.target_thrust_N > 0.0, "target_thrust_N must be positive or null"),
        (0.0 <= design.geometry.pitch_axis_chord_fraction <= 1.0, "pitch_axis_chord_fraction must be in [0, 1]"),
        (design.aero.polar.type in ("table", "analytic"), "aero.polar.type must be 'table' or 'analytic'"),
        (not design.aero.trim_to_target_thrust or op.target_thrust_N is not None, "trim_to_target_thrust needs target_thrust_N"),
        (design.structure.material.E_Pa > 0.0 and design.structure.material.density_kg_m3 > 0.0, "material E and density must be positive"),
        (design.airfoils.points_per_side >= 20, "airfoils.points_per_side must be >= 20"),
        (design.fea.mesh_size_m > 0.0, "fea.mesh_size_m must be positive"),
        (1.0 in tuple(design.fea.campbell_rpm_fractions), "fea.campbell_rpm_fractions must include 1.0 (the design rpm)"),
        (design.cfd.iterations >= 1 and design.cfd.snapshots >= 1, "cfd.iterations and cfd.snapshots must be >= 1"),
        (design.cfd.surface_refinement_min <= design.cfd.surface_refinement_max, "cfd.surface_refinement_min must be <= max"),
    ]
    for ok, message in checks:
        if not ok:
            raise ValueError(f"Invalid design {design.name!r}: {message}")
