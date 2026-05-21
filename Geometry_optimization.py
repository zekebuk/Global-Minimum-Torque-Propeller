from __future__ import annotations

import argparse
import csv
import json
import math
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, List, Sequence
from xml.sax.saxutils import escape

from common_plot_style import apply_matplotlib_style, save_matplotlib_figure


SCALED_REFERENCE_STATEMENT = (
    "The reference MIL and GMT geometries are NASA/SciTech 18-inch geometries scaled to 22 inches "
    "by preserving r/R, c/R, twist, and airfoil schedule."
)

ORIGINAL_REFERENCE_DIAMETER_IN = 18.0
SCALED_REFERENCE_DIAMETER_IN = 22.0
SCALED_REFERENCE_SCALE_FACTOR = SCALED_REFERENCE_DIAMETER_IN / ORIGINAL_REFERENCE_DIAMETER_IN
THESIS_22_STATIC_OPERATING_POINT = {
    "diameter_in": 22.0,
    "radius_m": 0.2794,
    "rpm": 4000.0,
    "freestream_mps": 0.0,
    "target_thrust_n": 35.0,
    "hover_mode": True,
    "operating_point": "thesis_22_static_35N",
}
NASA_SCITECH_ORIGINAL_OPERATING_POINT = {
    "diameter_in": 18.0,
    "rpm": 3000.0,
    "freestream_mps": 9.144,
    "advance_ratio": 0.4,
    "target_thrust_n": None,
}

REFERENCE_NOTES = f"""
Geometry pipeline implemented from:
1. Adkins, C. N., and Liebeck, R. H., "Design of Optimum Propellers," Journal of Propulsion and Power, 1994.
2. Robb, C. N., Gray, M. N., Ferrante, T. D., and Bowers, A. H., "Design of a Propeller with Global Minimum Torque," AIAA SciTech 2024 Forum.
3. Bowers, A. H., et al., "Experimental Flight Validation of the GMT 1933 Bell Spanload," NASA TM-20210014683, 2021.

{SCALED_REFERENCE_STATEMENT}
""".strip()

METHODOLOGY_STATEMENT = f"""
{SCALED_REFERENCE_STATEMENT} The scaled reference and optimized cases are evaluated at the thesis operating point: 22 in diameter, 4000 rpm, V_inf = 0 m/s, and 35 N target thrust. This is not the NASA/SciTech original 18-inch operating point.
""".strip()

CLI_EXAMPLES = """
Recommended thesis run:
python Geometry_optimization.py \\
  --preset thesis_gmt_22 \\
  --reference-geometry-csv reference_gmt.csv \\
  --rpm 4000 \\
  --thrust 35 \\
  --v-inf 0 \\
  --output-dir outputs/thesis_gmt_22 \\
  --make-plots

Free optimization comparison:
python Geometry_optimization.py \\
  --spanload gmt_bell \\
  --mode free_optimize \\
  --diameter-in 22 \\
  --rpm 4000 \\
  --thrust 35 \\
  --v-inf 0 \\
  --output-dir outputs/optimized_gmt_free

MIL comparison:
python Geometry_optimization.py \\
  --spanload minimum_induced_loss \\
  --mode reference_regularized \\
  --diameter-in 22 \\
  --rpm 4000 \\
  --thrust 35 \\
  --v-inf 0 \\
  --output-dir outputs/optimized_mil
""".strip()

INCH_TO_METER = 0.0254
DEFAULT_REFERENCE_MAX_CHORD_MARGIN = 1.08
SPANLOAD_CHOICES = [
    "minimum_induced_loss",
    "gmt_bell",
    "gmt_bell_simple",
    "gmt_bell_cubic",
    "paper_inspired",
    "reference_calibrated_gmt",
]
MODE_CHOICES = ["free_optimize", "reference_regularized", "reproduce_reference"]
OPTIMIZER_CHOICES = ["auto", "coordinate_search", "scipy"]
PRESET_CHOICES = ["thesis_gmt_22", "thesis_gmt_22_static"]
REFERENCE_MIL_CASE = "reference_mil"
REFERENCE_GMT_CASE = "reference_gmt"
OPTIMIZED_MIL_CASE = "optimized_mil"
OPTIMIZED_GMT_CASE = "optimized_gmt"
STANDARD_CASE_NAMES = [REFERENCE_MIL_CASE, REFERENCE_GMT_CASE, OPTIMIZED_MIL_CASE, OPTIMIZED_GMT_CASE]
REFERENCE_VARIANT_ALIASES = {
    REFERENCE_MIL_CASE: "mil_22",
    REFERENCE_GMT_CASE: "gmt_22",
    "scaled_reference_mil_22": "mil_22",
    "scaled_reference_gmt_22": "gmt_22",
}
REFERENCE_XLSX_ROW_RANGES = {
    "gmt_22": range(6, 44),
    "mil_22": range(56, 93),
}
REFERENCE_XLSX_SOURCE_LABELS = {
    "gmt_22": "scaled NASA/SciTech 18-inch GMT geometry to 22 inches",
    "mil_22": "scaled NASA/SciTech 18-inch MIL geometry to 22 inches",
}
REFERENCE_VARIANT_CHOICES = [
    "gmt_22",
    "mil_22",
    "scaled_reference_gmt_22",
    "scaled_reference_mil_22",
    REFERENCE_GMT_CASE,
    REFERENCE_MIL_CASE,
]


def diameter_in_to_radius_m(diameter_in: float) -> float:
    return 0.5 * diameter_in * INCH_TO_METER


def meters_to_inches(value_m: float) -> float:
    return value_m / INCH_TO_METER


def inches_to_meters(value_in: float) -> float:
    return value_in * INCH_TO_METER


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def rms(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(value * value for value in values) / len(values))


def safe_div(numerator: float, denominator: float, fallback: float = 0.0) -> float:
    if abs(denominator) <= 1.0e-12:
        return fallback
    return numerator / denominator


def rpm_to_omega(rpm: float) -> float:
    return rpm * 2.0 * math.pi / 60.0


def advance_ratio(v_inf: float, n_rev_per_sec: float, diameter_m: float) -> float:
    return safe_div(v_inf, n_rev_per_sec * diameter_m)


def compute_section_velocities(
    v_inf: float,
    omega: float,
    r: float,
    induced_velocity: float = 0.0,
) -> tuple[float, float, float, float]:
    v_axial = v_inf + induced_velocity
    v_tan = max(omega * r, 1.0e-6)
    v_rel = math.hypot(v_axial, v_tan)
    phi_deg = math.degrees(math.atan2(v_axial, v_tan))
    return v_axial, v_tan, v_rel, phi_deg


def compute_coefficients(T: float, Q: float, rho: float, n: float, D: float) -> tuple[float, float, float]:
    denominator_ct = rho * n * n * D**4
    denominator_cq = rho * n * n * D**5
    denominator_cp = rho * n**3 * D**5
    ct = safe_div(T, denominator_ct)
    cq = safe_div(Q, denominator_cq)
    cp = safe_div(2.0 * math.pi * n * Q, denominator_cp)
    return ct, cq, cp


# This is a reduced-order polar approximation for inverse design and calibration.
# It is not a replacement for XFOIL, XROTOR, CFD, or tunnel-derived section polars.
@dataclass(frozen=True)
class AirfoilModel:
    name: str
    cl_alpha_per_rad: float
    alpha_l0_deg: float
    cl_max: float
    cd0: float
    induced_drag_k: float
    thickness_to_chord: float

    def raw_cl(self, alpha_deg: float) -> float:
        alpha_rad = math.radians(alpha_deg - self.alpha_l0_deg)
        return self.cl_alpha_per_rad * alpha_rad

    def cl(self, alpha_deg: float) -> float:
        raw = self.raw_cl(alpha_deg)
        return clamp(raw, -0.8 * self.cl_max, self.cl_max)

    def cd(self, cl_value: float) -> float:
        return self.cd0 + self.induced_drag_k * cl_value * cl_value


@dataclass(frozen=True)
class ReferenceStation:
    mu: float
    chord_fraction: float
    twist_deg: float
    airfoil: str


@dataclass(frozen=True)
class ObjectiveWeights:
    w_thrust: float
    w_torque: float
    w_spanload: float
    w_reference_twist: float
    w_reference_chord: float
    w_smooth: float
    w_bounds: float


@dataclass(frozen=True)
class OptimizationParameters:
    base_alpha_deg: float = 0.0
    twist_bias_inner: float = 0.0
    twist_bias_outer: float = 0.0
    twist_curvature: float = 0.0
    tip_unload_strength: float = 0.0
    loading_scale: float = 1.0
    chord_scale: float = 1.0


@dataclass(frozen=True)
class SectionAero:
    phi_deg: float
    alpha_deg: float
    cl: float
    cd: float
    dthrust_n: float
    dtorque_nm: float
    hit_alpha_min: bool
    hit_alpha_max: bool
    hit_cl_limit: bool
    negative_force: bool
    impossible_force: bool


@dataclass(frozen=True)
class DesignInputs:
    blades: int
    radius_m: float
    hub_radius_fraction: float
    rpm: float
    freestream_mps: float
    thrust_target_n: float
    rho: float
    spanload_type: str
    mode: str
    chord_min_in: float
    chord_max_in: float
    chord_blend: float
    twist_blend: float
    smooth_passes: int
    alpha_search_min_deg: float
    alpha_search_max_deg: float
    alpha_search_step_deg: float
    reference_csv_path: Path
    reference_geometry_csv: Path | None
    output_directory: Path
    lock_chord: bool
    lock_airfoil: bool
    optimize_twist_only: bool
    make_plots: bool
    optimizer: str
    max_iterations: int
    tolerance: float

    @property
    def chord_min_m(self) -> float:
        return inches_to_meters(self.chord_min_in)

    @property
    def chord_max_m(self) -> float:
        return inches_to_meters(self.chord_max_in)


@dataclass(frozen=True)
class StationResult:
    station_index: int
    mu: float
    radius_m: float
    radius_in: float
    dr_m: float
    chord_m: float
    chord_in: float
    chord_fraction: float
    twist_deg: float
    cad_plane_angle_deg: float
    phi_deg: float
    alpha_deg: float
    cl: float
    cd: float
    dthrust_n: float
    dtorque_nm: float
    airfoil: str
    target_spanload_weight: float
    actual_spanload_weight: float
    reference_chord_fraction: float
    reference_twist_deg: float
    hit_chord_min: bool
    hit_chord_max: bool
    hit_alpha_min: bool
    hit_alpha_max: bool
    hit_cl_limit: bool
    negative_force: bool
    impossible_force: bool


@dataclass(frozen=True)
class DesignResult:
    config: DesignInputs
    target_alpha_deg: float
    objective: float
    thrust_n: float
    torque_nm: float
    power_w: float
    eta_ideal: float
    eta_dynamic: float
    eta_static: float
    induced_velocity_mps: float
    j_advance: float
    ct: float
    cq: float
    cp: float
    spanload_error: float
    reference_twist_rms_deg: float
    reference_twist_max_deg: float
    reference_chord_rms_percent: float
    smoothness_penalty: float
    bounds_penalty: float
    normalized_thrust_error: float
    normalized_torque: float
    aero_calibration_scale: float
    objective_breakdown: dict[str, float]
    weights: ObjectiveWeights
    parameters: OptimizationParameters
    stations: Sequence[StationResult]
    warnings: Sequence[str]
    convergence_history: Sequence[dict[str, float]]
    reference_source: str
    optimizer_name: str


AIRFOIL_LIBRARY = {
    "MH115": AirfoilModel("MH115", 5.7, -1.8, 1.35, 0.0115, 0.010, 0.115),
    "A3": AirfoilModel("A3", 5.8, -1.7, 1.28, 0.0108, 0.010, 0.105),
    "A4": AirfoilModel("A4", 5.8, -1.6, 1.26, 0.0103, 0.010, 0.102),
    "A6": AirfoilModel("A6", 5.9, -1.4, 1.23, 0.0098, 0.011, 0.098),
    "A7": AirfoilModel("A7", 5.9, -1.3, 1.21, 0.0095, 0.011, 0.096),
    "A11": AirfoilModel("A11", 6.0, -1.2, 1.18, 0.0092, 0.012, 0.094),
    "A13": AirfoilModel("A13", 6.0, -1.1, 1.16, 0.0090, 0.012, 0.093),
    "S4310": AirfoilModel("S4310", 6.1, -0.9, 1.10, 0.0088, 0.013, 0.100),
}


REFERENCE_MU = [
    0.20, 0.22, 0.24, 0.26, 0.28, 0.30, 0.32, 0.34, 0.36, 0.38,
    0.40, 0.42, 0.44, 0.46, 0.48, 0.50, 0.52, 0.54, 0.56, 0.58,
    0.60, 0.62, 0.62, 0.64, 0.68, 0.70, 0.72, 0.74, 0.76, 0.78,
    0.80, 0.82, 0.86, 0.88, 0.91, 0.95, 0.98, 1.00,
]

REFERENCE_CHORD_FRACTION = [
    0.193, 0.196, 0.199, 0.201, 0.202, 0.203, 0.203, 0.202, 0.201, 0.200,
    0.198, 0.196, 0.194, 0.191, 0.188, 0.185, 0.182, 0.179, 0.176, 0.172,
    0.169, 0.166, 0.166, 0.162, 0.155, 0.152, 0.148, 0.144, 0.140, 0.136,
    0.132, 0.128, 0.120, 0.116, 0.110, 0.103, 0.097, 0.093,
]

REFERENCE_TWIST_DEG = [
    49.52, 47.52, 45.58, 43.74, 42.02, 40.45, 39.04, 37.77, 36.65, 35.67,
    34.81, 34.06, 33.40, 32.81, 32.28, 31.80, 31.34, 30.90, 30.47, 30.04,
    29.61, 29.16, 29.16, 28.71, 27.77, 27.28, 26.77, 26.25, 25.70, 25.12,
    24.48, 23.77, 21.97, 20.78, 18.44, 13.85, 8.81, 3.99,
]


def build_default_airfoil_schedule(mu_values: Sequence[float]) -> List[str]:
    exact_schedule = {
        0.20: "MH115",
        0.22: "MH115",
        0.24: "MH115",
        0.26: "MH115",
        0.28: "MH115",
        0.30: "MH115",
        0.32: "MH115",
        0.34: "MH115",
        0.36: "MH115",
        0.38: "MH115",
        0.40: "MH115",
        0.42: "MH115",
        0.44: "MH115",
        0.46: "MH115",
        0.48: "MH115",
        0.50: "MH115",
        0.52: "MH115",
        0.54: "MH115",
        0.56: "MH115",
        0.58: "MH115",
        0.60: "MH115",
        0.62: "MH115",
        0.64: "MH115",
        0.68: "MH115",
        0.70: "MH115",
        0.72: "MH115",
        0.74: "A3",
        0.76: "A3",
        0.78: "A3",
        0.80: "A4",
        0.82: "A4",
        0.86: "A6",
        0.88: "A7",
        0.91: "A11",
        0.95: "A13",
        0.98: "S4310",
        1.00: "S4310",
    }
    return [exact_schedule.get(round(mu, 2), "MH115") for mu in mu_values]


def validate_user_inputs(config: DesignInputs) -> None:
    if config.blades < 1:
        raise ValueError("blade count must be at least 1")
    if config.radius_m <= 0.0:
        raise ValueError("radius must be positive")
    if not 0.0 <= config.hub_radius_fraction < 1.0:
        raise ValueError("hub_radius_fraction must be in [0, 1)")
    if config.rpm <= 0.0:
        raise ValueError("rpm must be positive")
    if config.thrust_target_n <= 0.0:
        raise ValueError("thrust target must be positive")
    if config.rho <= 0.0:
        raise ValueError("air density must be positive")
    if config.spanload_type not in SPANLOAD_CHOICES:
        raise ValueError(f"unsupported spanload type: {config.spanload_type}")
    if config.mode not in MODE_CHOICES:
        raise ValueError(f"unsupported solver mode: {config.mode}")
    if config.optimizer not in OPTIMIZER_CHOICES:
        raise ValueError(f"unsupported optimizer: {config.optimizer}")
    if config.chord_min_m <= 0.0:
        raise ValueError("minimum chord must be positive")
    if config.chord_max_m <= config.chord_min_m:
        raise ValueError("maximum chord must exceed minimum chord")
    if not 0.0 <= config.chord_blend <= 1.0:
        raise ValueError("chord_blend must be between 0 and 1")
    if not 0.0 <= config.twist_blend <= 1.0:
        raise ValueError("twist_blend must be between 0 and 1")
    if config.alpha_search_step_deg <= 0.0:
        raise ValueError("alpha search step must be positive")
    if config.alpha_search_max_deg <= config.alpha_search_min_deg:
        raise ValueError("alpha max must exceed alpha min")
    if config.max_iterations < 0:
        raise ValueError("max_iterations must be non-negative")
    if config.tolerance <= 0.0:
        raise ValueError("tolerance must be positive")


def default_reference_csv_path() -> Path:
    code_dir = Path(__file__).resolve().parent
    candidates = [
        code_dir / "Reference_Propeller_Geometry.xlsx",
        code_dir / "reference_propeller_geometry.xlsx",
        code_dir / "reference_propeller_geometry.csv",
        code_dir / "reference_geometry_from_cad.csv",
        code_dir.parent / "reference_geometry_from_cad.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def builtin_reference_stations() -> List[ReferenceStation]:
    airfoils = build_default_airfoil_schedule(REFERENCE_MU)
    return [
        ReferenceStation(mu=mu, chord_fraction=chord_fraction, twist_deg=twist_deg, airfoil=airfoil)
        for mu, chord_fraction, twist_deg, airfoil in zip(
            REFERENCE_MU, REFERENCE_CHORD_FRACTION, REFERENCE_TWIST_DEG, airfoils
        )
    ]


def clean_duplicate_mu_stations(
    stations: Sequence[ReferenceStation],
    tolerance: float = 1.0e-8,
) -> tuple[List[ReferenceStation], List[str]]:
    if not stations:
        return [], ["no reference geometry stations were supplied"]

    sorted_stations = sorted(stations, key=lambda station: station.mu)
    cleaned: List[ReferenceStation] = []
    warnings: List[str] = []
    idx = 0
    while idx < len(sorted_stations):
        group = [sorted_stations[idx]]
        idx += 1
        while idx < len(sorted_stations) and abs(sorted_stations[idx].mu - group[0].mu) <= tolerance:
            group.append(sorted_stations[idx])
            idx += 1
        if len(group) > 1:
            mu = sum(station.mu for station in group) / len(group)
            chord_fraction = sum(station.chord_fraction for station in group) / len(group)
            twist_deg = sum(station.twist_deg for station in group) / len(group)
            airfoil = group[0].airfoil
            warnings.append(
                f"merged {len(group)} duplicate stations at mu={group[0].mu:.4f} before integration"
            )
            cleaned.append(
                ReferenceStation(
                    mu=mu,
                    chord_fraction=chord_fraction,
                    twist_deg=twist_deg,
                    airfoil=airfoil,
                )
            )
        else:
            cleaned.append(group[0])

    return cleaned, warnings


def canonical_airfoil_name(raw_airfoil: object, default_airfoil: str) -> str:
    airfoil = str(raw_airfoil).strip()
    if not airfoil:
        return default_airfoil
    if airfoil in AIRFOIL_LIBRARY:
        return airfoil
    upper = airfoil.upper()
    if upper in AIRFOIL_LIBRARY:
        return upper
    return default_airfoil


def station_from_csv_row(row: dict[str, str], default_airfoil: str) -> ReferenceStation:
    airfoil = canonical_airfoil_name(row.get("airfoil", ""), default_airfoil)
    return ReferenceStation(
        mu=float(row["mu"]),
        chord_fraction=float(row["chord_over_R"]),
        twist_deg=float(row["twist_deg"]),
        airfoil=airfoil,
    )


def read_geometry_csv(path: Path) -> List[ReferenceStation]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        return []
    required = {"mu", "chord_over_R", "twist_deg"}
    missing = required.difference(rows[0].keys())
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(f"{path} is missing required column(s): {missing_text}")
    default_airfoils = build_default_airfoil_schedule([float(row["mu"]) for row in rows])
    return [station_from_csv_row(row, airfoil) for row, airfoil in zip(rows, default_airfoils)]


def normalize_reference_variant(variant: str) -> str:
    canonical = REFERENCE_VARIANT_ALIASES.get(variant, variant)
    if canonical not in REFERENCE_XLSX_ROW_RANGES:
        raise ValueError(f"unsupported reference geometry variant: {variant}")
    return canonical


def read_geometry_xlsx(path: Path, variant: str = "gmt_22") -> List[ReferenceStation]:
    rows = read_xlsx_sheet_rows(path, "xl/worksheets/sheet1.xml")
    canonical_variant = normalize_reference_variant(variant)
    row_range = REFERENCE_XLSX_ROW_RANGES[canonical_variant]
    source_label = REFERENCE_XLSX_SOURCE_LABELS[canonical_variant]

    raw_stations: list[dict[str, str]] = []
    for row in rows:
        row_number = int(row.get("__row_number__", "0"))
        if row_number not in row_range:
            continue
        # Active thesis variants use the NASA/SciTech 18-inch tables in C:H.
        # Dimensional 22-inch geometry is obtained by applying the configured
        # 11-inch radius while preserving r/R, c/R, twist, and airfoil.
        record = {
            "mu": row.get("C", ""),
            "chord_over_R": row.get("D", ""),
            "radius_in": row.get("E", ""),
            "chord_in": row.get("F", ""),
            "airfoil": row.get("G", ""),
            "twist_deg": row.get("H", ""),
        }
        if not any(str(value).strip() for value in record.values()):
            continue
        try:
            mu = float(record["mu"])
            chord_fraction = float(record["chord_over_R"])
            twist_deg = float(record["twist_deg"])
        except (TypeError, ValueError):
            continue
        if not (0.0 < mu <= 1.05 and chord_fraction > 0.0):
            continue
        common_airfoil = build_default_airfoil_schedule([mu])[0]
        raw_stations.append(
            {
                "mu": f"{mu:.12g}",
                "chord_over_R": f"{chord_fraction:.12g}",
                "twist_deg": f"{twist_deg:.12g}",
                # NASA/SciTech MIL and GMT use the same airfoil schedule.
                # The workbook's dimensional columns are rounded and one MIL
                # airfoil transition is shifted; use the common r/R schedule
                # for the active scaled-reference geometry.
                "airfoil": common_airfoil,
            }
        )

    if not raw_stations:
        raise ValueError(f"{path} did not contain readable {source_label} geometry in columns C:H")

    default_airfoils = build_default_airfoil_schedule([float(row["mu"]) for row in raw_stations])
    return [station_from_csv_row(row, airfoil) for row, airfoil in zip(raw_stations, default_airfoils)]


def read_xlsx_sheet_rows(path: Path, sheet_member: str) -> list[dict[str, str]]:
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        shared_strings = read_xlsx_shared_strings(archive, namespace)
        root = ET.fromstring(archive.read(sheet_member))

    rows: list[dict[str, str]] = []
    for row_node in root.findall("main:sheetData/main:row", namespace):
        row: dict[str, str] = {"__row_number__": row_node.attrib.get("r", "0")}
        for cell in row_node.findall("main:c", namespace):
            ref = cell.attrib.get("r", "")
            column = "".join(ch for ch in ref if ch.isalpha()).upper()
            if not column:
                continue
            row[column] = xlsx_cell_text(cell, shared_strings, namespace)
        rows.append(row)
    return rows


def read_xlsx_shared_strings(archive: zipfile.ZipFile, namespace: dict[str, str]) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("main:si", namespace):
        values.append("".join(text.text or "" for text in item.findall(".//main:t", namespace)))
    return values


def xlsx_cell_text(cell: ET.Element, shared_strings: Sequence[str], namespace: dict[str, str]) -> str:
    value_node = cell.find("main:v", namespace)
    if cell.attrib.get("t") == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//main:t", namespace))
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text
    if cell.attrib.get("t") == "s":
        return shared_strings[int(raw)]
    return raw


def read_geometry_file(path: Path, variant: str = "gmt_22") -> List[ReferenceStation]:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return read_geometry_xlsx(path, variant)
    return read_geometry_csv(path)


def load_reference_geometry(
    reference_path: Path | None,
    fallback_reference_path: Path,
    reference_variant: str = "gmt_22",
) -> tuple[List[ReferenceStation], str, List[str]]:
    warnings: List[str] = []
    source_variant = normalize_reference_variant(reference_variant)
    if reference_path is not None:
        if not reference_path.exists():
            raise FileNotFoundError(f"reference geometry file not found: {reference_path}")
        stations = read_geometry_file(reference_path, reference_variant)
        source = f"{reference_path} ({source_variant})" if reference_path.suffix.lower() in {".xlsx", ".xlsm"} else str(reference_path)
    elif fallback_reference_path.exists():
        stations = read_geometry_file(fallback_reference_path, reference_variant)
        source = f"{fallback_reference_path} ({source_variant})" if fallback_reference_path.suffix.lower() in {".xlsx", ".xlsm"} else str(fallback_reference_path)
    else:
        stations = builtin_reference_stations()
        source = "built-in NASA/SciTech reference geometry"

    cleaned, clean_warnings = clean_duplicate_mu_stations(stations)
    warnings.extend(clean_warnings)
    return cleaned, source, warnings


def load_reference_geometry_from_path(path: Path) -> List[ReferenceStation]:
    stations, _, _ = load_reference_geometry(None, path)
    return stations


def reference_case_label(variant: str) -> str:
    canonical = normalize_reference_variant(variant)
    family = "mil" if canonical == "mil_22" else "gmt"
    return f"scaled_reference_{family}_22"


def reference_workbook_scale_rows(path: Path, variant: str) -> list[dict[str, Any]]:
    rows = read_xlsx_sheet_rows(path, "xl/worksheets/sheet1.xml")
    canonical = normalize_reference_variant(variant)
    row_range = REFERENCE_XLSX_ROW_RANGES[canonical]
    family = "MIL" if canonical == "mil_22" else "GMT"
    out: list[dict[str, Any]] = []
    for row in rows:
        row_number = int(row.get("__row_number__", "0"))
        if row_number not in row_range:
            continue
        try:
            mu_original = float(row.get("C", ""))
            chord_fraction_original = float(row.get("D", ""))
            source_radius_18_in = float(row.get("E", ""))
            source_chord_18_in = float(row.get("F", ""))
            twist_original = float(row.get("H", ""))
            mu_scaled = float(row.get("L", ""))
            chord_fraction_scaled = float(row.get("M", ""))
            source_radius_22_in = float(row.get("N", ""))
            source_chord_22_in = float(row.get("O", ""))
            twist_scaled = float(row.get("Q", ""))
        except (TypeError, ValueError):
            continue
        if not (0.0 < mu_original <= 1.05 and chord_fraction_original > 0.0):
            continue
        common_airfoil = build_default_airfoil_schedule([mu_original])[0]
        radius_18_in = mu_original * (ORIGINAL_REFERENCE_DIAMETER_IN / 2.0)
        chord_18_in = chord_fraction_original * (ORIGINAL_REFERENCE_DIAMETER_IN / 2.0)
        radius_22_in = mu_original * (SCALED_REFERENCE_DIAMETER_IN / 2.0)
        chord_22_in = chord_fraction_original * (SCALED_REFERENCE_DIAMETER_IN / 2.0)
        out.append(
            {
                "case": reference_case_label(canonical),
                "family": family,
                "station": len(out) + 1,
                "source_row": row_number,
                "r_R_original": mu_original,
                "c_R_original": chord_fraction_original,
                "r_18_in": radius_18_in,
                "c_18_in": chord_18_in,
                "source_r_18_in": source_radius_18_in,
                "source_c_18_in": source_chord_18_in,
                "twist_original_deg": twist_original,
                "r_R_scaled": mu_scaled,
                "c_R_scaled": chord_fraction_scaled,
                "r_22_in": radius_22_in,
                "c_22_in": chord_22_in,
                "r_22_m": radius_22_in * INCH_TO_METER,
                "chord_22_m": chord_22_in * INCH_TO_METER,
                "source_r_22_in": source_radius_22_in,
                "source_c_22_in": source_chord_22_in,
                "airfoil_original": common_airfoil,
                "source_airfoil_original": canonical_airfoil_name(row.get("G", ""), common_airfoil),
                "airfoil_scaled": common_airfoil,
                "source_airfoil_scaled": canonical_airfoil_name(row.get("P", ""), common_airfoil),
                "twist_scaled_deg": twist_scaled,
            }
        )
    if not out:
        raise ValueError(f"{path} did not contain readable scaled reference rows for {variant}")
    return out


def validate_scaled_reference_rows(rows: Sequence[dict[str, Any]], tolerance: float = 1.0e-8) -> list[str]:
    failures: list[str] = []
    for row in rows:
        label = f"{row['case']} row {row['source_row']}"
        checks = [
            ("r/R preserved", row["r_R_scaled"], row["r_R_original"]),
            ("c/R preserved", row["c_R_scaled"], row["c_R_original"]),
            ("twist preserved", row["twist_scaled_deg"], row["twist_original_deg"]),
            ("r_18_in = r/R * 9", row["r_18_in"], row["r_R_original"] * (ORIGINAL_REFERENCE_DIAMETER_IN / 2.0)),
            ("c_18_in = c/R * 9", row["c_18_in"], row["c_R_original"] * (ORIGINAL_REFERENCE_DIAMETER_IN / 2.0)),
            ("r_22_in = r/R * 11", row["r_22_in"], row["r_R_original"] * (SCALED_REFERENCE_DIAMETER_IN / 2.0)),
            ("c_22_in = c/R * 11", row["c_22_in"], row["c_R_original"] * (SCALED_REFERENCE_DIAMETER_IN / 2.0)),
            ("r_m = r_22_in * 0.0254", row["r_22_m"], row["r_22_in"] * INCH_TO_METER),
            ("chord_m = c_22_in * 0.0254", row["chord_22_m"], row["c_22_in"] * INCH_TO_METER),
        ]
        for name, actual, expected in checks:
            if abs(float(actual) - float(expected)) > tolerance:
                failures.append(f"{label}: {name} failed ({actual} vs {expected})")
        if row["airfoil_scaled"] != row["airfoil_original"]:
            failures.append(
                f"{label}: airfoil schedule changed ({row['airfoil_original']} vs {row['airfoil_scaled']})"
            )
    return failures


def validate_scaled_reference_pair(
    mil_stations: Sequence[ReferenceStation],
    gmt_stations: Sequence[ReferenceStation],
    radius_m: float,
    tolerance: float = 1.0e-9,
) -> list[str]:
    failures: list[str] = []
    if len(mil_stations) != len(gmt_stations):
        failures.append(f"MIL/GMT station counts differ ({len(mil_stations)} vs {len(gmt_stations)})")
        return failures
    max_twist_delta = 0.0
    for idx, (mil, gmt) in enumerate(zip(mil_stations, gmt_stations), start=1):
        if abs(mil.mu - gmt.mu) > tolerance:
            failures.append(f"station {idx}: r/R differs ({mil.mu} vs {gmt.mu})")
        if abs(mil.chord_fraction - gmt.chord_fraction) > tolerance:
            failures.append(f"station {idx}: c/R differs ({mil.chord_fraction} vs {gmt.chord_fraction})")
        if abs(mil.chord_fraction * radius_m - gmt.chord_fraction * radius_m) > tolerance:
            failures.append(f"station {idx}: chord_m differs")
        if mil.airfoil != gmt.airfoil:
            failures.append(f"station {idx}: airfoil differs ({mil.airfoil} vs {gmt.airfoil})")
        max_twist_delta = max(max_twist_delta, abs(mil.twist_deg - gmt.twist_deg))
    if max_twist_delta <= tolerance:
        failures.append("MIL/GMT twist distributions are not different")
    return failures


def scaled_reference_no_double_scaling_warnings(rows: Sequence[dict[str, Any]]) -> list[str]:
    radius_values = [float(row["r_22_m"]) for row in rows]
    chord_values = [float(row["chord_22_m"]) for row in rows]
    max_radius = max(radius_values)
    warnings: list[str] = []
    if abs(max_radius - THESIS_22_STATIC_OPERATING_POINT["radius_m"]) > 5.0e-4:
        warnings.append(f"critical: max scaled radius is {max_radius:.6f} m, expected approximately 0.2794 m")
    if abs(max_radius - diameter_in_to_radius_m(18.0)) < 5.0e-4:
        warnings.append("critical: max radius appears to still be 18-inch radius (0.2286 m)")
    if abs(max_radius - THESIS_22_STATIC_OPERATING_POINT["radius_m"] * SCALED_REFERENCE_SCALE_FACTOR) < 5.0e-4:
        warnings.append("critical: max radius appears double-scaled to about 0.341 m")
    for row in rows:
        expected_chord = float(row["c_R_original"]) * THESIS_22_STATIC_OPERATING_POINT["radius_m"]
        if abs(float(row["chord_22_m"]) - expected_chord) > 5.0e-4:
            warnings.append(
                f"critical: {row['case']} row {row['source_row']} chord does not match c/R * 0.2794 m"
            )
            break
    if not chord_values:
        warnings.append("critical: no chord values found for double-scaling check")
    return warnings


def is_thesis_22_static_operating_point(config: DesignInputs, tolerance: float = 1.0e-9) -> bool:
    return (
        abs(meters_to_inches(2.0 * config.radius_m) - THESIS_22_STATIC_OPERATING_POINT["diameter_in"]) <= tolerance
        and abs(config.radius_m - THESIS_22_STATIC_OPERATING_POINT["radius_m"]) <= 1.0e-9
        and abs(config.rpm - THESIS_22_STATIC_OPERATING_POINT["rpm"]) <= tolerance
        and abs(config.freestream_mps - THESIS_22_STATIC_OPERATING_POINT["freestream_mps"]) <= tolerance
        and abs(config.thrust_target_n - THESIS_22_STATIC_OPERATING_POINT["target_thrust_n"]) <= tolerance
    )


def geometry_csv_metadata(config: DesignInputs) -> dict[str, object]:
    scaled_to_diameter_in = meters_to_inches(2.0 * config.radius_m)
    return {
        "original_source": "NASA/SciTech 18-inch geometry",
        "scaled_to_diameter_in": scaled_to_diameter_in,
        "scale_factor": scaled_to_diameter_in / ORIGINAL_REFERENCE_DIAMETER_IN,
        "operating_point": THESIS_22_STATIC_OPERATING_POINT["operating_point"]
        if is_thesis_22_static_operating_point(config)
        else "custom",
        "not_a_native_nasa_22in_geometry": True,
    }


def optimized_rows_by_mu(candidates: Sequence[Path]) -> dict[float, dict[str, str]]:
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {round(float(row["mu"]), 10): row for row in csv.DictReader(handle)}


def scaled_reference_audit_rows(
    workbook_path: Path,
    optimized_root: Path,
) -> tuple[list[dict[str, object]], list[str]]:
    all_source_rows = reference_workbook_scale_rows(workbook_path, "mil_22") + reference_workbook_scale_rows(
        workbook_path, "gmt_22"
    )
    failures = validate_scaled_reference_rows(all_source_rows)
    failures.extend(scaled_reference_no_double_scaling_warnings(all_source_rows))
    optimized_by_family = {
        "MIL": optimized_rows_by_mu(
            [
                optimized_root / "optimized_mil" / "optimized_mil.csv",
                optimized_root / "optimized_mil.csv",
            ]
        ),
        "GMT": optimized_rows_by_mu(
            [
                optimized_root / "optimized_gmt" / "optimized_gmt.csv",
                optimized_root / "optimized_gmt.csv",
            ]
        ),
    }
    rows: list[dict[str, object]] = []
    for row in all_source_rows:
        family = str(row["family"])
        optimized = optimized_by_family.get(family, {}).get(round(float(row["r_R_original"]), 10), {})
        rows.append(
            {
                "case": row["case"],
                "family": family,
                "station": row["station"],
                "source_row": row["source_row"],
                "r_R": row["r_R_original"],
                "c_R": row["c_R_original"],
                "original_18_radius_in": row["r_18_in"],
                "original_18_chord_in": row["c_18_in"],
                "scaled_22_radius_in": row["r_22_in"],
                "scaled_22_chord_in": row["c_22_in"],
                "scaled_22_radius_m": row["r_22_m"],
                "scaled_22_chord_m": row["chord_22_m"],
                "reference_twist_deg": row["twist_original_deg"],
                "reference_airfoil": row["airfoil_original"],
                "optimized_22_radius_m": optimized.get("radius_m", ""),
                "optimized_22_chord_m": optimized.get("chord_m", ""),
                "optimized_twist_deg": optimized.get("twist_deg", ""),
                "optimized_airfoil": optimized.get("airfoil", ""),
            }
        )
    return rows, failures


def write_markdown_table(rows: Sequence[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        output_path.write_text("No rows.\n", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(format_markdown_value(row.get(header, "")) for header in headers) + " |")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_markdown_value(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.10g}"
    text = str(value)
    return text.replace("|", "\\|")


def write_dict_rows_csv(rows: Sequence[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        output_path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def write_scaled_reference_geometry_audit(
    output_dir: Path,
    workbook_path: Path | None = None,
    optimized_root: Path | None = None,
) -> tuple[Path, Path, Path]:
    workbook_path = workbook_path or default_reference_csv_path()
    optimized_root = optimized_root or output_dir.parent / "optimized"
    output_dir.mkdir(parents=True, exist_ok=True)
    rows, failures = scaled_reference_audit_rows(workbook_path, optimized_root)
    scaling_failures = validate_scaled_reference_rows(
        reference_workbook_scale_rows(workbook_path, "mil_22") + reference_workbook_scale_rows(workbook_path, "gmt_22")
    )
    double_scaling_warnings = scaled_reference_no_double_scaling_warnings(
        reference_workbook_scale_rows(workbook_path, "mil_22") + reference_workbook_scale_rows(workbook_path, "gmt_22")
    )
    csv_path = output_dir / "scaled_reference_geometry_check.csv"
    md_path = output_dir / "scaled_reference_geometry_check.md"
    report_path = output_dir / "scaled_reference_geometry_audit.md"
    write_dict_rows_csv(rows, csv_path)
    write_markdown_table(rows, md_path)

    mil_stations, _, _ = load_reference_geometry(None, workbook_path, "mil_22")
    gmt_stations, _, _ = load_reference_geometry(None, workbook_path, "gmt_22")
    pair_failures = validate_scaled_reference_pair(
        mil_stations,
        gmt_stations,
        THESIS_22_STATIC_OPERATING_POINT["radius_m"],
    )
    failures.extend(pair_failures)

    radius_18 = [float(row["original_18_radius_in"]) * INCH_TO_METER for row in rows]
    radius_22 = [float(row["scaled_22_radius_m"]) for row in rows]
    chord_18 = [float(row["original_18_chord_in"]) * INCH_TO_METER for row in rows]
    chord_22 = [float(row["scaled_22_chord_m"]) for row in rows]
    report_lines = [
        "# Scaled Reference Geometry Audit",
        "",
        f"PASS/FAIL summary: {'FAIL' if failures else 'PASS'}",
        "",
        f"Source geometry file: `{workbook_path}`",
        f"Original diameter: {ORIGINAL_REFERENCE_DIAMETER_IN:g} in",
        f"Scaled diameter: {SCALED_REFERENCE_DIAMETER_IN:g} in",
        f"Scale factor: {SCALED_REFERENCE_SCALE_FACTOR:.10f}",
        "",
        SCALED_REFERENCE_STATEMENT,
        "",
        "The scaled geometry is evaluated at the thesis operating point: diameter = 22 in, radius = 0.2794 m, rpm = 4000, V_inf = 0 m/s, target_thrust_N = 35 N, hover_mode = true.",
        "This is not the NASA/SciTech original operating point. The NASA/SciTech original context remains 18 in diameter, about 3000 rpm, V_inf = 9.144 m/s, and advance ratio around J = 0.4.",
        "35 N is only the thesis 22-inch static target, not the NASA/SciTech original case.",
        "",
        "## Radius and Chord Ranges",
        "",
        f"Original 18-inch radius range: {min(radius_18):.6f} to {max(radius_18):.6f} m",
        f"Scaled 22-inch radius range: {min(radius_22):.6f} to {max(radius_22):.6f} m",
        f"Original 18-inch chord range: {min(chord_18):.6f} to {max(chord_18):.6f} m",
        f"Scaled 22-inch chord range: {min(chord_22):.6f} to {max(chord_22):.6f} m",
        "",
        "## Confirmations",
        "",
        "- r/R preserved: PASS" if not scaling_failures else "- r/R preserved: see failures below",
        "- c/R preserved: PASS" if not scaling_failures else "- c/R preserved: see failures below",
        "- twist preserved during scaling: PASS" if not scaling_failures else "- twist preserved during scaling: see failures below",
        "- airfoil schedule preserved during scaling: PASS" if not scaling_failures else "- airfoil schedule preserved during scaling: see failures below",
        "- scaled reference MIL/GMT use same r/R, c/R, chord, and airfoil schedule, with different twist: PASS"
        if not pair_failures
        else "- scaled reference MIL/GMT locked-geometry check: FAIL",
        "- no double-scaling detected: PASS" if not double_scaling_warnings else "- no double-scaling detected: FAIL",
        "",
        "## Failures or Critical Warnings",
        "",
    ]
    report_lines.extend([f"- {failure}" for failure in failures] if failures else ["None."])
    report_lines.extend(
        [
            "",
            "## Output Tables",
            "",
            f"- CSV: `{csv_path}`",
            f"- Markdown: `{md_path}`",
        ]
    )
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return csv_path, md_path, report_path


def annulus_widths(mu_values: Sequence[float], hub_mu: float, radius_m: float) -> List[float]:
    dr_values: List[float] = []
    count = len(mu_values)
    for idx, mu in enumerate(mu_values):
        left = hub_mu if idx == 0 else 0.5 * (mu_values[idx - 1] + mu)
        right = 1.0 if idx == count - 1 else 0.5 * (mu + mu_values[idx + 1])
        dr_values.append(max((right - left) * radius_m, 1.0e-5))
    return dr_values


def smoothstep(value: float) -> float:
    x = clamp(value, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def spanload_density(mu: float, kind: str, hub_mu: float) -> float:
    x = clamp((mu - hub_mu) / max(1.0 - hub_mu, 1.0e-9), 0.0, 1.0)
    root_factor = max(x, 1.0e-9) ** 0.75
    elliptic_tip = max(1.0 - x * x, 0.0)

    if kind == "minimum_induced_loss":
        return root_factor * math.sqrt(elliptic_tip)
    if kind == "gmt_bell_simple":
        return root_factor * elliptic_tip**2.0
    if kind == "gmt_bell_cubic":
        return root_factor * elliptic_tip**3.0
    if kind in {"paper_inspired", "reference_calibrated_gmt", "gmt_bell"}:
        midspan_hold = 1.0 + 0.70 * math.exp(-((x - 0.58) / 0.22) ** 2)
        shoulder = 1.0 + 0.20 * math.exp(-((x - 0.45) / 0.24) ** 2)
        rapid_tip_unload = max(1.0 - x**2.7, 0.0) ** 2.6
        return root_factor * midspan_hold * shoulder * rapid_tip_unload
    raise ValueError(f"unsupported spanload_type: {kind}")


def normalized_section_weights(
    mu_values: Sequence[float],
    dr_values: Sequence[float],
    kind: str,
    hub_mu: float,
) -> List[float]:
    raw = [spanload_density(mu, kind, hub_mu) * dr_m for mu, dr_m in zip(mu_values, dr_values)]
    total = sum(raw)
    if total <= 0.0:
        raise ValueError("spanload distribution collapsed to zero")
    return [value / total for value in raw]


def disk_induced_velocity(thrust_n: float, rho: float, radius_m: float, hub_mu: float, v_inf: float) -> float:
    hub_radius_m = hub_mu * radius_m
    disk_area = math.pi * max(radius_m * radius_m - hub_radius_m * hub_radius_m, 1.0e-8)
    return -0.5 * v_inf + math.sqrt(max(0.25 * v_inf * v_inf + thrust_n / max(2.0 * rho * disk_area, 1.0e-12), 0.0))


def smooth_series(values: Sequence[float], passes: int) -> List[float]:
    smoothed = list(values)
    for _ in range(max(passes, 0)):
        updated = list(smoothed)
        for idx in range(1, len(smoothed) - 1):
            updated[idx] = 0.25 * smoothed[idx - 1] + 0.5 * smoothed[idx] + 0.25 * smoothed[idx + 1]
        smoothed = updated
    return smoothed


def local_alpha_target(base_alpha_deg: float, mu: float, hub_mu: float, spanload_type: str) -> float:
    x = clamp((mu - hub_mu) / max(1.0 - hub_mu, 1.0e-9), 0.0, 1.0)
    bell_bias = 0.0
    if spanload_type in {"gmt_bell", "paper_inspired", "reference_calibrated_gmt"}:
        bell_bias = 1.6 * (0.55 - x) - 3.0 * smoothstep((x - 0.72) / 0.28)
    return clamp(base_alpha_deg + bell_bias, 2.0, 16.0)


def reference_loading_proxy(station: ReferenceStation) -> float:
    return station.chord_fraction * max(station.mu, 0.05) ** 1.25 * max(math.sin(math.radians(station.twist_deg)), 0.15)


def normalized_reference_loading(stations: Sequence[ReferenceStation], dr_values: Sequence[float]) -> List[float]:
    raw = [reference_loading_proxy(station) * dr_m for station, dr_m in zip(stations, dr_values)]
    total = sum(raw)
    if total <= 0.0:
        return [1.0 / max(len(stations), 1)] * len(stations)
    return [value / total for value in raw]


def tip_unload_shape(mu: float, hub_mu: float) -> float:
    x = clamp((mu - hub_mu) / max(1.0 - hub_mu, 1.0e-9), 0.0, 1.0)
    return smoothstep((x - 0.68) / 0.32)


def twist_correction(mu: float, hub_mu: float, parameters: OptimizationParameters) -> float:
    x = clamp((mu - hub_mu) / max(1.0 - hub_mu, 1.0e-9), 0.0, 1.0)
    inner_shape = math.exp(-((x - 0.45) / 0.24) ** 2)
    outer_linear = mu - 0.70
    curvature = (mu - 0.70) ** 2 - 0.035
    return (
        parameters.base_alpha_deg
        + parameters.twist_bias_inner * inner_shape
        + parameters.twist_bias_outer * outer_linear
        + parameters.twist_curvature * curvature
        - parameters.tip_unload_strength * tip_unload_shape(mu, hub_mu)
    )


def compute_section_forces(
    blades: int,
    rho: float,
    chord_m: float,
    dr_m: float,
    radius_m: float,
    twist_deg: float,
    airfoil: AirfoilModel,
    v_axial: float,
    v_tan: float,
    alpha_min_deg: float,
    alpha_max_deg: float,
) -> SectionAero:
    v_rel = math.hypot(v_axial, v_tan)
    phi_deg = math.degrees(math.atan2(v_axial, max(v_tan, 1.0e-6)))
    alpha_deg = twist_deg - phi_deg
    raw_cl = airfoil.raw_cl(alpha_deg)
    cl_value = airfoil.cl(alpha_deg)
    cd_value = airfoil.cd(cl_value)
    phi_rad = math.radians(phi_deg)
    force_factor = cl_value * math.cos(phi_rad) - cd_value * math.sin(phi_rad)
    torque_factor = cl_value * math.sin(phi_rad) + cd_value * math.cos(phi_rad)
    dynamic_pressure = 0.5 * rho * v_rel * v_rel
    dthrust_n = blades * dynamic_pressure * chord_m * force_factor * dr_m
    dtorque_nm = blades * dynamic_pressure * chord_m * torque_factor * radius_m * dr_m
    hit_alpha_min = alpha_deg < alpha_min_deg
    hit_alpha_max = alpha_deg > alpha_max_deg
    hit_cl_limit = abs(raw_cl - cl_value) > 1.0e-9
    negative_force = dthrust_n <= 0.0 or dtorque_nm <= 0.0
    impossible_force = chord_m <= 0.0 or dr_m <= 0.0 or not math.isfinite(dthrust_n) or not math.isfinite(dtorque_nm)
    return SectionAero(
        phi_deg=phi_deg,
        alpha_deg=alpha_deg,
        cl=cl_value,
        cd=cd_value,
        dthrust_n=dthrust_n,
        dtorque_nm=dtorque_nm,
        hit_alpha_min=hit_alpha_min,
        hit_alpha_max=hit_alpha_max,
        hit_cl_limit=hit_cl_limit,
        negative_force=negative_force,
        impossible_force=impossible_force,
    )


def weights_for_mode(mode: str) -> ObjectiveWeights:
    if mode == "free_optimize":
        return ObjectiveWeights(
            w_thrust=22.0,
            w_torque=0.65,
            w_spanload=4.0,
            w_reference_twist=0.04,
            w_reference_chord=0.02,
            w_smooth=0.22,
            w_bounds=5.0,
        )
    if mode == "reproduce_reference":
        return ObjectiveWeights(
            w_thrust=8.0,
            w_torque=0.20,
            w_spanload=1.0,
            w_reference_twist=25.0,
            w_reference_chord=18.0,
            w_smooth=0.10,
            w_bounds=3.0,
        )
    return ObjectiveWeights(
        w_thrust=24.0,
        w_torque=0.55,
        w_spanload=4.5,
        w_reference_twist=0.85,
        w_reference_chord=0.35,
        w_smooth=0.28,
        w_bounds=5.0,
    )


def parameter_dict(parameters: OptimizationParameters) -> dict[str, float]:
    return {
        "base_alpha_deg": parameters.base_alpha_deg,
        "twist_bias_inner": parameters.twist_bias_inner,
        "twist_bias_outer": parameters.twist_bias_outer,
        "twist_curvature": parameters.twist_curvature,
        "tip_unload_strength": parameters.tip_unload_strength,
        "loading_scale": parameters.loading_scale,
        "chord_scale": parameters.chord_scale,
    }


def candidate_chord(
    config: DesignInputs,
    station: ReferenceStation,
    target_weight: float,
    reference_weight: float,
    parameters: OptimizationParameters,
) -> float:
    reference_chord_m = station.chord_fraction * config.radius_m
    if config.lock_chord or config.optimize_twist_only or config.mode == "reproduce_reference":
        return reference_chord_m
    load_ratio = target_weight / max(reference_weight, 1.0e-9)
    planform_scale = clamp(load_ratio**0.24, 0.78, 1.24)
    blended_scale = config.chord_blend + (1.0 - config.chord_blend) * planform_scale
    chord_m = reference_chord_m * blended_scale * parameters.chord_scale * parameters.loading_scale**0.15
    return clamp(chord_m, config.chord_min_m, config.chord_max_m)


def candidate_airfoil(config: DesignInputs, station: ReferenceStation) -> str:
    if config.lock_airfoil or config.optimize_twist_only or config.mode == "reproduce_reference":
        return station.airfoil
    return build_default_airfoil_schedule([station.mu])[0]


def build_candidate_result(
    config: DesignInputs,
    reference_stations: Sequence[ReferenceStation],
    parameters: OptimizationParameters,
    weights: ObjectiveWeights,
    q_reference: float,
    reference_source: str,
    optimizer_name: str,
    initial_warnings: Sequence[str] = (),
    aero_calibration_scale: float = 1.0,
) -> DesignResult:
    omega = rpm_to_omega(config.rpm)
    n_rev_per_sec = config.rpm / 60.0
    diameter_m = 2.0 * config.radius_m
    mu_values = [station.mu for station in reference_stations]
    dr_values = annulus_widths(mu_values, config.hub_radius_fraction, config.radius_m)
    target_weights = normalized_section_weights(mu_values, dr_values, config.spanload_type, config.hub_radius_fraction)
    reference_weights = normalized_reference_loading(reference_stations, dr_values)
    loading_scale = max(parameters.loading_scale, 0.05)
    induced_velocity = disk_induced_velocity(
        config.thrust_target_n * loading_scale,
        config.rho,
        config.radius_m,
        config.hub_radius_fraction,
        config.freestream_mps,
    )
    average_target_weight = sum(target_weights) / max(len(target_weights), 1)

    raw_chords: List[float] = []
    raw_twists: List[float] = []
    raw_airfoils: List[str] = []
    for station, target_weight, reference_weight in zip(reference_stations, target_weights, reference_weights):
        chord_m = candidate_chord(config, station, target_weight, reference_weight, parameters)
        if config.mode == "reproduce_reference":
            twist_deg = station.twist_deg
        else:
            correction = twist_correction(station.mu, config.hub_radius_fraction, parameters)
            twist_deg = station.twist_deg + (1.0 - config.twist_blend) * correction
        raw_chords.append(chord_m)
        raw_twists.append(clamp(twist_deg, 0.0, 65.0))
        raw_airfoils.append(candidate_airfoil(config, station))

    if config.lock_chord or config.optimize_twist_only or config.mode == "reproduce_reference":
        smoothed_chords = raw_chords
    else:
        smoothed_chords = [
            clamp(value, config.chord_min_m, config.chord_max_m)
            for value in smooth_series(raw_chords, config.smooth_passes)
        ]

    if config.mode == "reproduce_reference":
        smoothed_twists = raw_twists
    else:
        smoothed_twists = [
            clamp(value, 0.0, 65.0)
            for value in smooth_series(raw_twists, config.smooth_passes)
        ]

    stations: List[StationResult] = []
    thrust_n = 0.0
    torque_nm = 0.0
    for idx, (station, dr_m, target_weight, chord_m, twist_deg, airfoil_name) in enumerate(
        zip(reference_stations, dr_values, target_weights, smoothed_chords, smoothed_twists, raw_airfoils),
        start=1,
    ):
        local_vi_multiplier = 0.82 + 0.50 * target_weight / max(average_target_weight, 1.0e-9)
        local_vi = induced_velocity * local_vi_multiplier
        radius_m = station.mu * config.radius_m
        v_axial, v_tan, _, _ = compute_section_velocities(config.freestream_mps, omega, radius_m, local_vi)
        airfoil = AIRFOIL_LIBRARY[airfoil_name]
        aero = compute_section_forces(
            config.blades,
            config.rho,
            chord_m,
            dr_m,
            radius_m,
            twist_deg,
            airfoil,
            v_axial,
            v_tan,
            config.alpha_search_min_deg,
            config.alpha_search_max_deg,
        )
        dthrust_n = aero.dthrust_n * aero_calibration_scale
        dtorque_nm = aero.dtorque_nm * aero_calibration_scale
        thrust_n += dthrust_n
        torque_nm += dtorque_nm
        hit_chord_min = abs(chord_m - config.chord_min_m) < 1.0e-9
        hit_chord_max = abs(chord_m - config.chord_max_m) < 1.0e-9
        stations.append(
            StationResult(
                station_index=idx,
                mu=station.mu,
                radius_m=radius_m,
                radius_in=meters_to_inches(radius_m),
                dr_m=dr_m,
                chord_m=chord_m,
                chord_in=meters_to_inches(chord_m),
                chord_fraction=chord_m / config.radius_m,
                twist_deg=twist_deg,
                cad_plane_angle_deg=90.0 - twist_deg,
                phi_deg=aero.phi_deg,
                alpha_deg=aero.alpha_deg,
                cl=aero.cl,
                cd=aero.cd,
                dthrust_n=dthrust_n,
                dtorque_nm=dtorque_nm,
                airfoil=airfoil_name,
                target_spanload_weight=target_weight,
                actual_spanload_weight=0.0,
                reference_chord_fraction=station.chord_fraction,
                reference_twist_deg=station.twist_deg,
                hit_chord_min=hit_chord_min,
                hit_chord_max=hit_chord_max,
                hit_alpha_min=aero.hit_alpha_min,
                hit_alpha_max=aero.hit_alpha_max,
                hit_cl_limit=aero.hit_cl_limit,
                negative_force=aero.negative_force,
                impossible_force=aero.impossible_force,
            )
        )

    if abs(thrust_n) > 1.0e-12:
        stations = [
            replace(station, actual_spanload_weight=station.dthrust_n / thrust_n)
            for station in stations
        ]

    power_w = torque_nm * omega
    j_advance = advance_ratio(config.freestream_mps, n_rev_per_sec, diameter_m)
    ct, cq, cp = compute_coefficients(thrust_n, torque_nm, config.rho, n_rev_per_sec, diameter_m)
    eta_dynamic = safe_div(j_advance * ct, cp) if j_advance > 1.0e-9 and cp > 0.0 else 0.0
    disk_area = math.pi * max(config.radius_m**2 - (config.hub_radius_fraction * config.radius_m) ** 2, 1.0e-9)
    eta_static = 0.0
    if config.freestream_mps <= 1.0e-6 and power_w > 0.0 and thrust_n > 0.0:
        eta_static = thrust_n**1.5 / (power_w * math.sqrt(2.0 * config.rho * disk_area))
    eta_ideal = eta_dynamic if j_advance > 0.0 else eta_static

    normalized_thrust_error = abs(thrust_n - config.thrust_target_n) / max(config.thrust_target_n, 1.0e-9)
    normalized_torque = torque_nm / max(abs(q_reference), 1.0e-9)
    spanload_error = math.sqrt(
        sum((station.actual_spanload_weight - station.target_spanload_weight) ** 2 for station in stations)
    )
    twist_differences = [station.twist_deg - station.reference_twist_deg for station in stations]
    reference_twist_rms_deg = rms(twist_differences)
    reference_twist_max_deg = max((abs(value) for value in twist_differences), default=0.0)
    chord_differences = [station.chord_fraction - station.reference_chord_fraction for station in stations]
    reference_chord_rms_fraction = rms(chord_differences)
    reference_chord_rms_percent = 100.0 * reference_chord_rms_fraction

    smoothness_penalty = 0.0
    if len(stations) >= 3:
        for idx in range(1, len(stations) - 1):
            smoothness_penalty += abs(
                stations[idx + 1].chord_fraction - 2.0 * stations[idx].chord_fraction + stations[idx - 1].chord_fraction
            ) / 0.02
            smoothness_penalty += abs(
                stations[idx + 1].twist_deg - 2.0 * stations[idx].twist_deg + stations[idx - 1].twist_deg
            ) / 8.0
        smoothness_penalty /= max(len(stations) - 2, 1)

    count = max(len(stations), 1)
    chord_limit_fraction = sum(station.hit_chord_min or station.hit_chord_max for station in stations) / count
    alpha_limit_fraction = sum(station.hit_alpha_min or station.hit_alpha_max for station in stations) / count
    cl_limit_fraction = sum(station.hit_cl_limit for station in stations) / count
    negative_force_fraction = sum(station.negative_force for station in stations) / count
    impossible_force_fraction = sum(station.impossible_force for station in stations) / count
    bounds_penalty = (
        1.5 * chord_limit_fraction
        + 2.0 * alpha_limit_fraction
        + 1.5 * cl_limit_fraction
        + 3.0 * negative_force_fraction
        + 8.0 * impossible_force_fraction
    )
    if torque_nm <= 0.0:
        bounds_penalty += 10.0

    reference_twist_error = reference_twist_rms_deg / 10.0
    reference_chord_error = reference_chord_rms_fraction / 0.05
    objective_terms = {
        "normalized_thrust_error": weights.w_thrust * normalized_thrust_error,
        "normalized_torque": weights.w_torque * normalized_torque,
        "spanload_shape_error": weights.w_spanload * spanload_error,
        "reference_twist_error": weights.w_reference_twist * reference_twist_error,
        "reference_chord_error": weights.w_reference_chord * reference_chord_error,
        "smoothness_penalty": weights.w_smooth * smoothness_penalty,
        "bounds_penalty": weights.w_bounds * bounds_penalty,
    }
    objective = sum(objective_terms.values())

    warnings: List[str] = list(initial_warnings)
    if normalized_thrust_error > 0.05:
        warnings.append(f"target thrust mismatch is {100.0 * normalized_thrust_error:.1f}%")
    if chord_limit_fraction > 0.0:
        warnings.append(f"{100.0 * chord_limit_fraction:.1f}% of stations hit chord limits")
    if alpha_limit_fraction > 0.20:
        warnings.append(f"{100.0 * alpha_limit_fraction:.1f}% of stations hit alpha limits")
    if cl_limit_fraction > 0.20:
        warnings.append(f"{100.0 * cl_limit_fraction:.1f}% of stations are clipped at reduced-order Cl limits")
    if negative_force_fraction > 0.0:
        warnings.append(f"{100.0 * negative_force_fraction:.1f}% of stations produced negative thrust or torque contributions")
    if torque_nm <= 0.0:
        warnings.append("computed torque is non-positive")

    return DesignResult(
        config=config,
        target_alpha_deg=parameters.base_alpha_deg,
        objective=objective,
        thrust_n=thrust_n,
        torque_nm=torque_nm,
        power_w=power_w,
        eta_ideal=eta_ideal,
        eta_dynamic=eta_dynamic,
        eta_static=eta_static,
        induced_velocity_mps=induced_velocity,
        j_advance=j_advance,
        ct=ct,
        cq=cq,
        cp=cp,
        spanload_error=spanload_error,
        reference_twist_rms_deg=reference_twist_rms_deg,
        reference_twist_max_deg=reference_twist_max_deg,
        reference_chord_rms_percent=reference_chord_rms_percent,
        smoothness_penalty=smoothness_penalty,
        bounds_penalty=bounds_penalty,
        normalized_thrust_error=normalized_thrust_error,
        normalized_torque=normalized_torque,
        aero_calibration_scale=aero_calibration_scale,
        objective_breakdown=objective_terms,
        weights=weights,
        parameters=parameters,
        stations=stations,
        warnings=warnings,
        convergence_history=[],
        reference_source=reference_source,
        optimizer_name=optimizer_name,
    )


def convergence_record(iteration: int, result: DesignResult) -> dict[str, float]:
    return {
        "iteration": float(iteration),
        "objective": result.objective,
        "thrust_n": result.thrust_n,
        "thrust_error_percent": 100.0 * result.normalized_thrust_error,
        "torque_nm": result.torque_nm,
        "power_w": result.power_w,
        "CT": result.ct,
        "CQ": result.cq,
        "CP": result.cp,
        "spanload_error": result.spanload_error,
        "reference_twist_rms_deg": result.reference_twist_rms_deg,
        "reference_chord_rms_percent": result.reference_chord_rms_percent,
        "smoothness_penalty": result.smoothness_penalty,
    }


def parameter_bounds(config: DesignInputs) -> dict[str, tuple[float, float]]:
    return {
        "base_alpha_deg": (-28.0, 14.0),
        "twist_bias_inner": (-10.0, 10.0),
        "twist_bias_outer": (-28.0, 28.0),
        "twist_curvature": (-35.0, 35.0),
        "tip_unload_strength": (0.0, 22.0),
        "loading_scale": (0.60, 1.45),
        "chord_scale": (1.0, 1.0) if config.lock_chord or config.optimize_twist_only else (0.75, 1.25),
    }


def clamp_parameters(config: DesignInputs, parameters: OptimizationParameters) -> OptimizationParameters:
    bounds = parameter_bounds(config)
    return OptimizationParameters(
        base_alpha_deg=clamp(parameters.base_alpha_deg, *bounds["base_alpha_deg"]),
        twist_bias_inner=clamp(parameters.twist_bias_inner, *bounds["twist_bias_inner"]),
        twist_bias_outer=clamp(parameters.twist_bias_outer, *bounds["twist_bias_outer"]),
        twist_curvature=clamp(parameters.twist_curvature, *bounds["twist_curvature"]),
        tip_unload_strength=clamp(parameters.tip_unload_strength, *bounds["tip_unload_strength"]),
        loading_scale=clamp(parameters.loading_scale, *bounds["loading_scale"]),
        chord_scale=clamp(parameters.chord_scale, *bounds["chord_scale"]),
    )


def with_parameter(parameters: OptimizationParameters, name: str, value: float) -> OptimizationParameters:
    data = parameter_dict(parameters)
    data[name] = value
    return OptimizationParameters(**data)


def match_thrust_with_base_alpha(
    config: DesignInputs,
    reference_stations: Sequence[ReferenceStation],
    parameters: OptimizationParameters,
    weights: ObjectiveWeights,
    q_reference: float,
    reference_source: str,
    optimizer_name: str,
    initial_warnings: Sequence[str],
    aero_calibration_scale: float,
) -> OptimizationParameters:
    if config.mode == "reproduce_reference":
        return parameters

    bounds = parameter_bounds(config)["base_alpha_deg"]
    low = with_parameter(parameters, "base_alpha_deg", bounds[0])
    high = with_parameter(parameters, "base_alpha_deg", bounds[1])
    low_result = build_candidate_result(
        config, reference_stations, low, weights, q_reference, reference_source, optimizer_name, initial_warnings, aero_calibration_scale
    )
    high_result = build_candidate_result(
        config, reference_stations, high, weights, q_reference, reference_source, optimizer_name, initial_warnings, aero_calibration_scale
    )
    best_params = low if low_result.normalized_thrust_error <= high_result.normalized_thrust_error else high
    best_error = min(low_result.normalized_thrust_error, high_result.normalized_thrust_error)

    if (low_result.thrust_n - config.thrust_target_n) * (high_result.thrust_n - config.thrust_target_n) > 0.0:
        return best_params

    left = low
    right = high
    for _ in range(24):
        mid_value = 0.5 * (left.base_alpha_deg + right.base_alpha_deg)
        mid = with_parameter(parameters, "base_alpha_deg", mid_value)
        mid_result = build_candidate_result(
            config, reference_stations, mid, weights, q_reference, reference_source, optimizer_name, initial_warnings, aero_calibration_scale
        )
        if mid_result.normalized_thrust_error < best_error:
            best_error = mid_result.normalized_thrust_error
            best_params = mid
        if mid_result.thrust_n < config.thrust_target_n:
            left = mid
        else:
            right = mid
        if mid_result.normalized_thrust_error < 5.0e-4:
            break
    return best_params


def initial_parameters(config: DesignInputs) -> OptimizationParameters:
    if config.mode == "free_optimize":
        return OptimizationParameters(
            base_alpha_deg=0.0,
            twist_bias_inner=0.0,
            twist_bias_outer=0.0,
            twist_curvature=0.0,
            tip_unload_strength=3.0,
            loading_scale=1.0,
            chord_scale=1.0,
        )
    return OptimizationParameters(
        base_alpha_deg=0.0,
        twist_bias_inner=0.0,
        twist_bias_outer=0.0,
        twist_curvature=0.0,
        tip_unload_strength=1.0,
        loading_scale=1.0,
        chord_scale=1.0,
    )


def evaluate_with_matched_alpha(
    config: DesignInputs,
    reference_stations: Sequence[ReferenceStation],
    parameters: OptimizationParameters,
    weights: ObjectiveWeights,
    q_reference: float,
    reference_source: str,
    optimizer_name: str,
    initial_warnings: Sequence[str],
    aero_calibration_scale: float,
) -> DesignResult:
    parameters = clamp_parameters(config, parameters)
    parameters = match_thrust_with_base_alpha(
        config,
        reference_stations,
        parameters,
        weights,
        q_reference,
        reference_source,
        optimizer_name,
        initial_warnings,
        aero_calibration_scale,
    )
    return build_candidate_result(
        config,
        reference_stations,
        parameters,
        weights,
        q_reference,
        reference_source,
        optimizer_name,
        initial_warnings,
        aero_calibration_scale,
    )


def optimize_with_coordinate_search(
    config: DesignInputs,
    reference_stations: Sequence[ReferenceStation],
    weights: ObjectiveWeights,
    q_reference: float,
    reference_source: str,
    initial_warnings: Sequence[str],
    aero_calibration_scale: float,
) -> tuple[DesignResult, List[dict[str, float]]]:
    optimizer_name = "coordinate_search"
    history: List[dict[str, float]] = []
    iteration = 0

    best = evaluate_with_matched_alpha(
        config,
        reference_stations,
        initial_parameters(config),
        weights,
        q_reference,
        reference_source,
        optimizer_name,
        initial_warnings,
        aero_calibration_scale,
    )
    history.append(convergence_record(iteration, best))
    best_params = best.parameters

    steps = {
        "twist_bias_inner": 2.5,
        "twist_bias_outer": 6.0,
        "twist_curvature": 8.0,
        "tip_unload_strength": 3.0,
        "loading_scale": 0.12,
        "chord_scale": 0.06,
    }
    if config.lock_chord or config.optimize_twist_only:
        steps.pop("chord_scale", None)

    stagnant_cycles = 0
    for _ in range(config.max_iterations):
        improved = False
        cycle_start_objective = best.objective
        for name, step in list(steps.items()):
            accepted = False
            for direction in (1.0, -1.0):
                trial_value = parameter_dict(best_params)[name] + direction * step
                trial_params = with_parameter(best_params, name, trial_value)
                trial_params = clamp_parameters(config, trial_params)
                trial = evaluate_with_matched_alpha(
                    config,
                    reference_stations,
                    trial_params,
                    weights,
                    q_reference,
                    reference_source,
                    optimizer_name,
                    initial_warnings,
                    aero_calibration_scale,
                )
                iteration += 1
                history.append(convergence_record(iteration, trial))
                if trial.objective + config.tolerance < best.objective:
                    best = trial
                    best_params = trial.parameters
                    improved = True
                    accepted = True
                    break
            if accepted:
                continue

        improvement = cycle_start_objective - best.objective
        if not improved or improvement < config.tolerance:
            stagnant_cycles += 1
            steps = {name: 0.5 * value for name, value in steps.items()}
        else:
            stagnant_cycles = 0
        if stagnant_cycles >= 4 or max(steps.values(), default=0.0) < 0.02:
            break

    return best, history


def optimize_with_scipy(
    config: DesignInputs,
    reference_stations: Sequence[ReferenceStation],
    weights: ObjectiveWeights,
    q_reference: float,
    reference_source: str,
    initial_warnings: Sequence[str],
    aero_calibration_scale: float,
) -> tuple[DesignResult, List[dict[str, float]]]:
    try:
        from scipy.optimize import minimize
    except Exception as exc:
        raise RuntimeError(f"scipy.optimize is unavailable: {exc}") from exc

    optimizer_name = "scipy_powell"
    history: List[dict[str, float]] = []
    iteration = 0
    names = ["twist_bias_inner", "twist_bias_outer", "twist_curvature", "tip_unload_strength", "loading_scale"]
    if not (config.lock_chord or config.optimize_twist_only):
        names.append("chord_scale")
    bounds_map = parameter_bounds(config)
    bounds = [bounds_map[name] for name in names]
    start_params = evaluate_with_matched_alpha(
        config,
        reference_stations,
        initial_parameters(config),
        weights,
        q_reference,
        reference_source,
        optimizer_name,
        initial_warnings,
        aero_calibration_scale,
    ).parameters
    x0 = [parameter_dict(start_params)[name] for name in names]
    best_result: DesignResult | None = None

    def objective_from_vector(values: Sequence[float]) -> float:
        nonlocal iteration, best_result
        data = parameter_dict(start_params)
        for name, value in zip(names, values):
            data[name] = value
        params = clamp_parameters(config, OptimizationParameters(**data))
        result = evaluate_with_matched_alpha(
            config,
            reference_stations,
            params,
            weights,
            q_reference,
            reference_source,
            optimizer_name,
            initial_warnings,
            aero_calibration_scale,
        )
        iteration += 1
        history.append(convergence_record(iteration, result))
        if best_result is None or result.objective < best_result.objective:
            best_result = result
        return result.objective

    minimize(
        objective_from_vector,
        x0,
        method="Powell",
        bounds=bounds,
        options={
            "maxiter": max(config.max_iterations, 1),
            "maxfev": max(120, config.max_iterations * max(len(names), 1) * 10),
            "xtol": config.tolerance,
            "ftol": config.tolerance,
        },
    )
    if best_result is None:
        raise RuntimeError("scipy optimizer did not evaluate any candidates")
    return best_result, history


def validate_geometry_result(result: DesignResult) -> List[str]:
    warnings: List[str] = []
    stations = list(result.stations)
    for previous, current in zip(stations, stations[1:]):
        if current.mu <= previous.mu:
            warnings.append("mu stations are not strictly increasing after duplicate cleaning")
            break
    if any(station.chord_m <= 0.0 for station in stations):
        warnings.append("one or more stations have non-positive chord")
    if any(station.twist_deg < 0.0 or station.twist_deg > 65.0 for station in stations):
        warnings.append("one or more stations have twist outside the 0 to 65 deg physical check range")
    if any(station.alpha_deg < -5.0 or station.alpha_deg > 25.0 for station in stations):
        warnings.append("one or more stations have alpha outside the -5 to 25 deg sanity check range")
    cl_clip_fraction = sum(station.hit_cl_limit for station in stations) / max(len(stations), 1)
    if cl_clip_fraction > 0.20:
        warnings.append(f"{100.0 * cl_clip_fraction:.1f}% of stations are at the reduced-order Cl limit")
    if result.normalized_thrust_error > 0.05:
        warnings.append("actual thrust is outside the 5% target-thrust acceptance band")
    elif result.normalized_thrust_error > 0.03:
        warnings.append("actual thrust is outside 3% but within the 5% target-thrust acceptance band")
    if result.torque_nm <= 0.0:
        warnings.append("torque is non-positive")

    values: List[float] = [
        result.objective,
        result.thrust_n,
        result.torque_nm,
        result.power_w,
        result.ct,
        result.cq,
        result.cp,
    ]
    for station in stations:
        values.extend(
            [
                station.mu,
                station.radius_m,
                station.dr_m,
                station.chord_m,
                station.twist_deg,
                station.alpha_deg,
                station.cl,
                station.cd,
                station.dthrust_n,
                station.dtorque_nm,
            ]
        )
    if any(not math.isfinite(value) for value in values):
        warnings.append("NaN or infinite value detected in solver result")
    return warnings


def dedupe_warnings(warnings: Sequence[str]) -> List[str]:
    unique: List[str] = []
    seen: set[str] = set()
    for warning in warnings:
        if warning not in seen:
            seen.add(warning)
            unique.append(warning)
    return unique


def optimize_geometry(
    config: DesignInputs,
    reference_stations: Sequence[ReferenceStation],
    initial_warnings: Sequence[str] = (),
    reference_source: str = "unknown reference",
) -> DesignResult:
    weights = weights_for_mode(config.mode)
    reference_params = OptimizationParameters()
    reference_config = replace(
        config,
        mode="reproduce_reference",
        lock_chord=True,
        lock_airfoil=True,
        optimize_twist_only=True,
    )
    reference_result = build_candidate_result(
        reference_config,
        reference_stations,
        reference_params,
        weights_for_mode("reproduce_reference"),
        1.0,
        reference_source,
        "reference_evaluation",
        initial_warnings,
    )
    aero_calibration_scale = 1.0
    calibration_warnings = list(initial_warnings)
    if config.mode == "reference_regularized" and reference_result.thrust_n > 1.0e-9:
        unconstrained_scale = config.thrust_target_n / reference_result.thrust_n
        aero_calibration_scale = clamp(unconstrained_scale, 0.35, 1.25)
        calibration_warnings.append(
            "applied reference aerodynamic calibration scale "
            f"{aero_calibration_scale:.4f} to the reduced-order section forces "
            f"(raw reference thrust {reference_result.thrust_n:.3f} N at target condition)"
        )
        if abs(aero_calibration_scale - unconstrained_scale) > 1.0e-9:
            calibration_warnings.append(
                "reference aerodynamic calibration scale was clipped to keep the reduced-order model bounded"
            )
    q_reference = max(abs(reference_result.torque_nm * aero_calibration_scale), 1.0e-6)

    if config.mode == "reproduce_reference" or config.max_iterations == 0:
        result = build_candidate_result(
            config,
            reference_stations,
            reference_params,
            weights,
            q_reference,
            reference_source,
            "reference_evaluation",
            calibration_warnings,
            aero_calibration_scale,
        )
        history = [convergence_record(0, result)]
        warnings = dedupe_warnings(list(result.warnings) + validate_geometry_result(result))
        return replace(result, convergence_history=history, warnings=warnings)

    if config.optimizer in {"auto", "scipy"}:
        try:
            result, history = optimize_with_scipy(
                config,
                reference_stations,
                weights,
                q_reference,
                reference_source,
                calibration_warnings,
                aero_calibration_scale,
            )
        except Exception as exc:
            if config.optimizer == "scipy":
                raise
            fallback_warning = f"scipy optimizer unavailable or failed; used coordinate search instead ({exc})"
            result, history = optimize_with_coordinate_search(
                config,
                reference_stations,
                weights,
                q_reference,
                reference_source,
                calibration_warnings + [fallback_warning],
                aero_calibration_scale,
            )
    else:
        result, history = optimize_with_coordinate_search(
            config,
            reference_stations,
            weights,
            q_reference,
            reference_source,
            calibration_warnings,
            aero_calibration_scale,
        )

    warnings = dedupe_warnings(list(result.warnings) + validate_geometry_result(result))
    return replace(result, convergence_history=history, warnings=warnings)


def build_geometry(
    config: DesignInputs,
    reference_stations: Sequence[ReferenceStation],
    base_alpha_deg: float,
    loading_scale: float,
) -> DesignResult:
    weights = weights_for_mode(config.mode)
    params = OptimizationParameters(base_alpha_deg=base_alpha_deg, loading_scale=loading_scale)
    return build_candidate_result(
        config,
        reference_stations,
        params,
        weights,
        1.0,
        str(config.reference_geometry_csv or config.reference_csv_path),
        "legacy_build_geometry",
    )


def solve_geometry_for_alpha(config: DesignInputs, reference_stations: Sequence[ReferenceStation], base_alpha_deg: float) -> DesignResult:
    lower = 0.05
    upper = 1.00
    lower_result = build_geometry(config, reference_stations, base_alpha_deg, lower)
    upper_result = build_geometry(config, reference_stations, base_alpha_deg, upper)
    candidate = upper_result

    while lower_result.thrust_n > config.thrust_target_n and lower > 0.005:
        upper = lower
        upper_result = lower_result
        lower *= 0.5
        lower_result = build_geometry(config, reference_stations, base_alpha_deg, lower)

    while upper_result.thrust_n < config.thrust_target_n and upper < 5.0:
        lower = upper
        lower_result = upper_result
        upper *= 1.5
        upper_result = build_geometry(config, reference_stations, base_alpha_deg, upper)

    for _ in range(30):
        mid = 0.5 * (lower + upper)
        trial = build_geometry(config, reference_stations, base_alpha_deg, mid)
        candidate = trial
        if abs(trial.thrust_n - config.thrust_target_n) / config.thrust_target_n < 1.0e-3:
            break
        if trial.thrust_n < config.thrust_target_n:
            lower = mid
        else:
            upper = mid
    return candidate


def frange(start: float, stop: float, step: float) -> Iterable[float]:
    value = start
    while value <= stop + 1.0e-9:
        yield round(value, 6)
        value += step


def write_csv(result: DesignResult, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = geometry_csv_metadata(result.config)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "station",
                "mu",
                "radius_m",
                "radius_in",
                "dr_m",
                "chord_m",
                "chord_in",
                "chord_over_R",
                "twist_deg",
                "cad_plane_angle_deg",
                "phi_deg",
                "alpha_deg",
                "cl",
                "cd",
                "dthrust_n",
                "dtorque_nm",
                "airfoil",
                "target_spanload_weight",
                "actual_spanload_weight",
                "reference_chord_over_R",
                "reference_twist_deg",
                "hit_chord_min",
                "hit_chord_max",
                "hit_alpha_min",
                "hit_alpha_max",
                "hit_cl_limit",
                "negative_force",
                "impossible_force",
                "original_source",
                "scaled_to_diameter_in",
                "scale_factor",
                "operating_point",
                "not_a_native_nasa_22in_geometry",
            ]
        )
        for station in result.stations:
            writer.writerow(
                [
                    station.station_index,
                    f"{station.mu:.4f}",
                    f"{station.radius_m:.6f}",
                    f"{station.radius_in:.4f}",
                    f"{station.dr_m:.6f}",
                    f"{station.chord_m:.6f}",
                    f"{station.chord_in:.4f}",
                    f"{station.chord_fraction:.6f}",
                    f"{station.twist_deg:.4f}",
                    f"{station.cad_plane_angle_deg:.4f}",
                    f"{station.phi_deg:.4f}",
                    f"{station.alpha_deg:.4f}",
                    f"{station.cl:.5f}",
                    f"{station.cd:.5f}",
                    f"{station.dthrust_n:.5f}",
                    f"{station.dtorque_nm:.5f}",
                    station.airfoil,
                    f"{station.target_spanload_weight:.6f}",
                    f"{station.actual_spanload_weight:.6f}",
                    f"{station.reference_chord_fraction:.6f}",
                    f"{station.reference_twist_deg:.4f}",
                    str(station.hit_chord_min),
                    str(station.hit_chord_max),
                    str(station.hit_alpha_min),
                    str(station.hit_alpha_max),
                    str(station.hit_cl_limit),
                    str(station.negative_force),
                    str(station.impossible_force),
                    metadata["original_source"],
                    metadata["scaled_to_diameter_in"],
                    f"{float(metadata['scale_factor']):.10f}",
                    metadata["operating_point"],
                    str(metadata["not_a_native_nasa_22in_geometry"]).lower(),
                ]
            )


def write_reference_csv(reference_stations: Sequence[ReferenceStation], radius_m: float, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scaled_to_diameter_in = meters_to_inches(2.0 * radius_m)
    operating_point = (
        THESIS_22_STATIC_OPERATING_POINT["operating_point"]
        if abs(scaled_to_diameter_in - SCALED_REFERENCE_DIAMETER_IN) <= 1.0e-9
        else "custom"
    )
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "mu",
                "chord_over_R",
                "chord_m",
                "twist_deg",
                "cad_plane_angle_deg",
                "airfoil",
                "original_source",
                "scaled_to_diameter_in",
                "scale_factor",
                "operating_point",
                "not_a_native_nasa_22in_geometry",
            ]
        )
        for station in reference_stations:
            writer.writerow(
                [
                    f"{station.mu:.4f}",
                    f"{station.chord_fraction:.6f}",
                    f"{station.chord_fraction * radius_m:.6f}",
                    f"{station.twist_deg:.4f}",
                    f"{90.0 - station.twist_deg:.4f}",
                    station.airfoil,
                    "NASA/SciTech 18-inch geometry",
                    f"{scaled_to_diameter_in:.10g}",
                    f"{scaled_to_diameter_in / ORIGINAL_REFERENCE_DIAMETER_IN:.10f}",
                    operating_point,
                    "true",
                ]
            )


def write_convergence_history_csv(history: Sequence[dict[str, float]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "iteration",
        "objective",
        "thrust_n",
        "thrust_error_percent",
        "torque_nm",
        "power_w",
        "CT",
        "CQ",
        "CP",
        "spanload_error",
        "reference_twist_rms_deg",
        "reference_chord_rms_percent",
        "smoothness_penalty",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in history:
            writer.writerow({field: record.get(field, "") for field in fields})


def write_methodology_statement(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(METHODOLOGY_STATEMENT + "\n", encoding="utf-8")


def excel_column_name(index: int) -> str:
    name = ""
    value = index
    while value > 0:
        value, rem = divmod(value - 1, 26)
        name = chr(ord("A") + rem) + name
    return name


def excel_cell_ref(row_index: int, col_index: int) -> str:
    return f"{excel_column_name(col_index)}{row_index}"


def worksheet_xml(rows: Sequence[Sequence[object]]) -> str:
    row_xml: List[str] = []
    for row_idx, row in enumerate(rows, start=1):
        cells: List[str] = []
        for col_idx, value in enumerate(row, start=1):
            if value is None or value == "":
                continue
            ref = excel_cell_ref(row_idx, col_idx)
            if isinstance(value, bool):
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{str(value)}</t></is></c>')
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                number = format(value, ".12g")
                cells.append(f'<c r="{ref}"><v>{number}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        row_xml.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        "</worksheet>"
    )


def workbook_xml(sheet_names: Sequence[str]) -> str:
    sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, name in enumerate(sheet_names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets>"
        "</workbook>"
    )


def workbook_rels_xml(sheet_count: int) -> str:
    rels = "".join(
        f'<Relationship Id="rId{idx}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{idx}.xml"/>'
        for idx in range(1, sheet_count + 1)
    )
    rels += (
        f'<Relationship Id="rId{sheet_count + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels}"
        "</Relationships>"
    )


def content_types_xml(sheet_count: int) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for idx in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f"{overrides}"
        "</Types>"
    )


def root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
        '<cellXfs count="1"><xf xfId="0"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )


def build_workbook_sheets(result: DesignResult, reference_stations: Sequence[ReferenceStation]) -> List[tuple[str, List[List[object]]]]:
    diameter_m = 2.0 * result.config.radius_m
    diameter_in = meters_to_inches(diameter_m)
    summary_rows: List[List[object]] = [
        ["Parameter", "Value", "Units", "Notes"],
        ["Solver mode", result.config.mode, "", "free, reference-regularized, or reference reproduction"],
        ["Optimizer", result.optimizer_name, "", ""],
        ["Spanload", result.config.spanload_type, "", ""],
        ["Reference geometry", result.reference_source, "", ""],
        ["Reference aero calibration scale", result.aero_calibration_scale, "", "Explicit reduced-order section-force calibration"],
        ["Optimized global twist/alpha offset", result.target_alpha_deg, "deg", "Low-order twist-basis offset"],
        ["Radius", result.config.radius_m, "m", ""],
        ["Diameter", diameter_m, "m", f"{diameter_in:.3f} in"],
        ["RPM", result.config.rpm, "rpm", ""],
        ["Freestream", result.config.freestream_mps, "m/s", "Velocity normal to the propeller plane"],
        ["Advance ratio J", result.j_advance, "", ""],
        ["Target thrust", result.config.thrust_target_n, "N", ""],
        ["Actual thrust", result.thrust_n, "N", ""],
        ["Thrust error", 100.0 * result.normalized_thrust_error, "%", ""],
        ["Actual torque", result.torque_nm, "N*m", ""],
        ["Shaft power", result.power_w, "W", ""],
        ["CT", result.ct, "", ""],
        ["CQ", result.cq, "", ""],
        ["CP", result.cp, "", ""],
        ["Dynamic efficiency", result.eta_dynamic, "", "J*CT/CP for non-static runs"],
        ["Static ideal efficiency", result.eta_static, "", "Actuator-disk static figure of merit proxy"],
        ["Disk induced velocity", result.induced_velocity_mps, "m/s", ""],
        ["Objective", result.objective, "", "Weighted reduced-order objective"],
        ["Spanload error", result.spanload_error, "", ""],
        ["Reference twist RMS", result.reference_twist_rms_deg, "deg", ""],
        ["Reference twist max", result.reference_twist_max_deg, "deg", ""],
        ["Reference chord RMS", result.reference_chord_rms_percent, "% c/R", ""],
        ["Blade count", result.config.blades, "", ""],
        ["Hub radius fraction", result.config.hub_radius_fraction, "r/R", ""],
        ["Chord minimum", result.config.chord_min_m, "m", f"{result.config.chord_min_in:.3f} in"],
        ["Chord maximum", result.config.chord_max_m, "m", f"{result.config.chord_max_in:.3f} in"],
        ["Lock chord", result.config.lock_chord, "", ""],
        ["Lock airfoil", result.config.lock_airfoil, "", ""],
        ["Optimize twist only", result.config.optimize_twist_only, "", ""],
    ]
    if result.warnings:
        summary_rows.append([])
        summary_rows.append(["Warnings", "", "", ""])
        for warning in result.warnings:
            summary_rows.append([warning, "", "", ""])

    reference_rows: List[List[object]] = [[
        "Station", "mu", "radius_m", "radius_in", "chord_m", "chord_in", "chord_over_R",
        "twist_deg", "cad_plane_angle_deg", "airfoil"
    ]]
    for idx, station in enumerate(reference_stations, start=1):
        radius_m = station.mu * result.config.radius_m
        chord_m = station.chord_fraction * result.config.radius_m
        reference_rows.append(
            [
                idx,
                station.mu,
                radius_m,
                meters_to_inches(radius_m),
                chord_m,
                meters_to_inches(chord_m),
                station.chord_fraction,
                station.twist_deg,
                90.0 - station.twist_deg,
                station.airfoil,
            ]
        )

    optimized_rows: List[List[object]] = [[
        "Station", "mu", "radius_m", "radius_in", "dr_m", "chord_m", "chord_in", "chord_over_R",
        "twist_deg", "cad_plane_angle_deg", "phi_deg", "alpha_deg", "cl", "cd",
        "dthrust_n", "dtorque_nm", "airfoil", "target_spanload_weight", "actual_spanload_weight",
        "reference_chord_over_R", "reference_twist_deg", "hit_chord_min", "hit_chord_max",
        "hit_alpha_min", "hit_alpha_max", "hit_cl_limit", "negative_force", "impossible_force"
    ]]
    for station in result.stations:
        optimized_rows.append(
            [
                station.station_index,
                station.mu,
                station.radius_m,
                station.radius_in,
                station.dr_m,
                station.chord_m,
                station.chord_in,
                station.chord_fraction,
                station.twist_deg,
                station.cad_plane_angle_deg,
                station.phi_deg,
                station.alpha_deg,
                station.cl,
                station.cd,
                station.dthrust_n,
                station.dtorque_nm,
                station.airfoil,
                station.target_spanload_weight,
                station.actual_spanload_weight,
                station.reference_chord_fraction,
                station.reference_twist_deg,
                station.hit_chord_min,
                station.hit_chord_max,
                station.hit_alpha_min,
                station.hit_alpha_max,
                station.hit_cl_limit,
                station.negative_force,
                station.impossible_force,
            ]
        )

    convergence_rows: List[List[object]] = [[
        "iteration", "objective", "thrust_n", "thrust_error_percent", "torque_nm", "power_w",
        "CT", "CQ", "CP", "spanload_error", "reference_twist_rms_deg",
        "reference_chord_rms_percent", "smoothness_penalty"
    ]]
    for record in result.convergence_history:
        convergence_rows.append(
            [
                record.get("iteration", ""),
                record.get("objective", ""),
                record.get("thrust_n", ""),
                record.get("thrust_error_percent", ""),
                record.get("torque_nm", ""),
                record.get("power_w", ""),
                record.get("CT", ""),
                record.get("CQ", ""),
                record.get("CP", ""),
                record.get("spanload_error", ""),
                record.get("reference_twist_rms_deg", ""),
                record.get("reference_chord_rms_percent", ""),
                record.get("smoothness_penalty", ""),
            ]
        )

    solidworks_rows: List[List[object]] = [["Variable", "Value", "Units", "Comment"]]
    solidworks_rows.extend(
        [
            ["RADIUS_PROP", result.config.radius_m, "m", "Overall propeller radius"],
            ["DIAMETER_PROP", diameter_m, "m", "Overall propeller diameter"],
            ["RPM_DESIGN", result.config.rpm, "rpm", "Design RPM"],
            ["THRUST_TARGET", result.config.thrust_target_n, "N", "Design thrust target"],
        ]
    )
    for station in result.stations:
        suffix = f"{station.station_index:02d}"
        solidworks_rows.extend(
            [
                [f"MU_{suffix}", station.mu, "r/R", "Section radial position"],
                [f"R_{suffix}", station.radius_m, "m", "Section radius"],
                [f"C_{suffix}", station.chord_m, "m", "Section chord"],
                [f"BETA_{suffix}", station.twist_deg, "deg", "Section geometric twist"],
                [f"CAD_ANGLE_{suffix}", station.cad_plane_angle_deg, "deg", "Plane angle for CAD sketch/orient"],
                [f"AIRFOIL_{suffix}", station.airfoil, "", "Assigned airfoil"],
            ]
        )

    return [
        ("Inputs_Summary", summary_rows),
        ("Reference_Geometry", reference_rows),
        ("Optimized_Geometry", optimized_rows),
        ("Convergence", convergence_rows),
        ("SolidWorks_Equations", solidworks_rows),
    ]


def write_xlsx(result: DesignResult, reference_stations: Sequence[ReferenceStation], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheets = build_workbook_sheets(result, reference_stations)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", content_types_xml(len(sheets)))
        workbook.writestr("_rels/.rels", root_rels_xml())
        workbook.writestr("xl/workbook.xml", workbook_xml([name for name, _ in sheets]))
        workbook.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml(len(sheets)))
        workbook.writestr("xl/styles.xml", styles_xml())
        for idx, (_, rows) in enumerate(sheets, start=1):
            workbook.writestr(f"xl/worksheets/sheet{idx}.xml", worksheet_xml(rows))


def result_summary_dict(result: DesignResult) -> dict[str, Any]:
    mode_label = {
        "free_optimize": "free reduced-order optimization",
        "reference_regularized": "reference-regularized optimization",
        "reproduce_reference": "reference reproduction",
    }[result.config.mode]
    return {
        "solver_mode": result.config.mode,
        "solver_mode_label": mode_label,
        "optimizer": result.optimizer_name,
        "spanload_type": result.config.spanload_type,
        "reference_geometry_source": result.reference_source,
        "reference_geometry_scaling": {
            "statement": SCALED_REFERENCE_STATEMENT,
            "original_source": "NASA/SciTech 18-inch geometry",
            "original_diameter_in": ORIGINAL_REFERENCE_DIAMETER_IN,
            "scaled_to_diameter_in": meters_to_inches(2.0 * result.config.radius_m),
            "scale_factor_for_thesis_22": SCALED_REFERENCE_SCALE_FACTOR,
            "not_a_native_nasa_22in_geometry": True,
        },
        "operating_point_distinction": {
            "thesis_case": THESIS_22_STATIC_OPERATING_POINT,
            "nasa_scitech_original_context": NASA_SCITECH_ORIGINAL_OPERATING_POINT,
            "statement": "35 N is only the thesis 22-inch static target, not the NASA/SciTech original operating point.",
        },
        "optimized_geometry_source": "Generated by the present Geometry_optimization.py run.",
        "case_name": optimized_case_name(result.config.spanload_type),
        "reference_case_name": reference_case_name(result.config.spanload_type),
        "reference_regularization": {
            "twist_weight": result.weights.w_reference_twist,
            "chord_weight": result.weights.w_reference_chord,
            "statement": "Reference terms are calibration/regularization terms, not undisclosed hard-coded optimizer output.",
        },
        "aero_calibration": {
            "section_force_scale": result.aero_calibration_scale,
            "statement": "This scalar calibrates the reduced-order section-force model in reference-regularized runs and is reported explicitly.",
        },
        "locked_inputs": {
            "diameter_m": 2.0 * result.config.radius_m,
            "diameter_in": meters_to_inches(2.0 * result.config.radius_m),
            "chord_locked": result.config.lock_chord or result.config.optimize_twist_only,
            "airfoil_locked": result.config.lock_airfoil or result.config.optimize_twist_only,
            "optimize_twist_only": result.config.optimize_twist_only,
            "rpm": result.config.rpm,
            "v_inf_mps": result.config.freestream_mps,
            "advance_ratio": result.j_advance,
            "target_thrust_n": result.config.thrust_target_n,
        },
        "gmt_tip_unloading_verification": {
            "status": "not_evaluated_in_single_case_summary",
            "statement": "Run theory_verification.py or the reference-vs-optimized comparison workflow to verify same thrust, lower torque, reduced tip loading, inboard-shifted cumulative loading, and acceptable Cl clipping fraction.",
        },
        "final_metrics": {
            "objective": result.objective,
            "thrust_n": result.thrust_n,
            "thrust_error_percent": 100.0 * result.normalized_thrust_error,
            "torque_nm": result.torque_nm,
            "power_w": result.power_w,
            "CT": result.ct,
            "CQ": result.cq,
            "CP": result.cp,
            "eta_dynamic": result.eta_dynamic,
            "eta_static": result.eta_static,
            "spanload_error": result.spanload_error,
            "reference_twist_rms_deg": result.reference_twist_rms_deg,
            "reference_twist_max_deg": result.reference_twist_max_deg,
            "reference_chord_rms_percent": result.reference_chord_rms_percent,
            "smoothness_penalty": result.smoothness_penalty,
            "bounds_penalty": result.bounds_penalty,
        },
        "objective_breakdown": result.objective_breakdown,
        "parameters": parameter_dict(result.parameters),
        "warnings": list(result.warnings),
        "methodology_statement": METHODOLOGY_STATEMENT,
    }


def write_summary_json(result: DesignResult, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(result_summary_dict(result), handle, indent=2)
        handle.write("\n")


def plot_thesis_outputs(result: DesignResult, reference_stations: Sequence[ReferenceStation], output_dir: Path) -> List[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        return [f"matplotlib unavailable; plots were not generated ({exc})"]

    apply_matplotlib_style(plt)

    mu = [station.mu for station in result.stations]
    optimized_twist = [station.twist_deg for station in result.stations]
    reference_twist = [station.twist_deg for station in reference_stations]
    optimized_chord = [station.chord_fraction for station in result.stations]
    reference_chord = [station.chord_fraction for station in reference_stations]
    target_spanload = [station.target_spanload_weight for station in result.stations]
    actual_spanload = [station.actual_spanload_weight for station in result.stations]
    alpha = [station.alpha_deg for station in result.stations]
    cl = [station.cl for station in result.stations]
    dr_values = [station.dr_m for station in result.stations]
    mil_weights = normalized_section_weights(mu, dr_values, "minimum_induced_loss", result.config.hub_radius_fraction)
    reference_weights = normalized_reference_loading(reference_stations, dr_values)
    mil_twist_proxy = [
        reference.twist_deg + 2.0 * math.log(max(mil_weight, 1.0e-9) / max(reference_weight, 1.0e-9))
        for reference, mil_weight, reference_weight in zip(reference_stations, mil_weights, reference_weights)
    ]

    fig = plt.figure()
    plt.plot(mu, reference_twist, label="reference")
    plt.plot(mu, optimized_twist, label="optimized")
    plt.plot(mu, mil_twist_proxy, label="MIL proxy")
    plt.xlabel("r/R")
    plt.ylabel("twist beta [deg]")
    plt.legend()
    fig.tight_layout()
    save_matplotlib_figure(fig, output_dir, "twist_comparison")
    plt.close(fig)

    fig = plt.figure()
    plt.plot(mu, reference_chord, label="reference")
    plt.plot(mu, optimized_chord, label="optimized")
    plt.xlabel("r/R")
    plt.ylabel("c/R")
    plt.legend()
    fig.tight_layout()
    save_matplotlib_figure(fig, output_dir, "chord_comparison")
    plt.close(fig)

    fig = plt.figure()
    plt.plot(mu, target_spanload, label="target GMT")
    plt.plot(mu, actual_spanload, label="optimized")
    plt.plot(mu, mil_weights, label="MIL proxy")
    plt.xlabel("r/R")
    plt.ylabel("normalized dT")
    plt.legend()
    fig.tight_layout()
    save_matplotlib_figure(fig, output_dir, "spanload_comparison")
    plt.close(fig)

    fig, ax_alpha = plt.subplots()
    ax_cl = ax_alpha.twinx()
    ax_alpha.plot(mu, alpha, label="alpha")
    ax_cl.plot(mu, cl, label="Cl")
    ax_alpha.set_xlabel("r/R")
    ax_alpha.set_ylabel("alpha [deg]")
    ax_cl.set_ylabel("Cl")
    lines_a, labels_a = ax_alpha.get_legend_handles_labels()
    lines_b, labels_b = ax_cl.get_legend_handles_labels()
    ax_alpha.legend(lines_a + lines_b, labels_a + labels_b)
    fig.tight_layout()
    save_matplotlib_figure(fig, output_dir, "alpha_cl_distribution")
    plt.close(fig)

    if result.convergence_history:
        fig = plt.figure()
        plt.plot(
            [record["iteration"] for record in result.convergence_history],
            [record["objective"] for record in result.convergence_history],
        )
        plt.xlabel("iteration")
        plt.ylabel("objective")
        fig.tight_layout()
        save_matplotlib_figure(fig, output_dir, "convergence_objective")
        plt.close(fig)

    return []


def print_summary(result: DesignResult) -> None:
    diameter_m = 2.0 * result.config.radius_m
    diameter_in = meters_to_inches(diameter_m)
    iteration_count = len(result.convergence_history)
    print(REFERENCE_NOTES)
    print()
    print("Design summary")
    print(f"  solver mode          : {result.config.mode}")
    print(f"  optimizer            : {result.optimizer_name}")
    print(f"  iterations/evals     : {iteration_count}")
    print(f"  spanload type        : {result.config.spanload_type}")
    print(f"  reference source     : {result.reference_source}")
    print(f"  aero calibration     : {result.aero_calibration_scale:.4f}")
    print(f"  global twist offset  : {result.target_alpha_deg:.3f} deg")
    print(f"  diameter             : {diameter_m:.4f} m ({diameter_in:.2f} in)")
    print(f"  rpm                  : {result.config.rpm:.1f}")
    print(f"  v_inf                : {result.config.freestream_mps:.3f} m/s")
    print(f"  advance ratio J      : {result.j_advance:.5f}")
    print(f"  final objective      : {result.objective:.6f}")
    print(f"  target thrust        : {result.config.thrust_target_n:.3f} N")
    print(f"  actual thrust        : {result.thrust_n:.3f} N")
    print(f"  thrust error         : {100.0 * result.normalized_thrust_error:.3f}%")
    print(f"  actual torque        : {result.torque_nm:.4f} N*m")
    print(f"  shaft power          : {result.power_w:.2f} W")
    print(f"  CT / CQ / CP         : {result.ct:.6f} / {result.cq:.6f} / {result.cp:.6f}")
    print(f"  eta dynamic/static   : {result.eta_dynamic:.4f} / {result.eta_static:.4f}")
    print(f"  induced velocity     : {result.induced_velocity_mps:.3f} m/s")
    print(f"  spanload error       : {result.spanload_error:.6f}")
    print(f"  ref twist RMS/max    : {result.reference_twist_rms_deg:.3f} / {result.reference_twist_max_deg:.3f} deg")
    print(f"  ref chord RMS        : {result.reference_chord_rms_percent:.3f}% c/R")
    chord_limit_fraction = sum(station.hit_chord_min or station.hit_chord_max for station in result.stations) / max(len(result.stations), 1)
    alpha_limit_fraction = sum(station.hit_alpha_min or station.hit_alpha_max for station in result.stations) / max(len(result.stations), 1)
    if chord_limit_fraction > 0.20 or alpha_limit_fraction > 0.20:
        print(
            "  limit warning        : "
            f"{100.0 * chord_limit_fraction:.1f}% chord, {100.0 * alpha_limit_fraction:.1f}% alpha stations at limits"
        )
    if result.warnings:
        print()
        print("Warnings")
        for warning in result.warnings:
            print(f"  - {warning}")
    print()
    print("First 10 stations")
    print("  stn   mu     c/R     chord[in]  twist[deg]  alpha[deg]  Cl      dT[N]   airfoil")
    for station in result.stations[:10]:
        print(
            f"  {station.station_index:02d}   "
            f"{station.mu:0.2f}   "
            f"{station.chord_fraction:0.4f}   "
            f"{station.chord_in:8.3f}   "
            f"{station.twist_deg:9.3f}   "
            f"{station.alpha_deg:9.3f}   "
            f"{station.cl:6.3f}   "
            f"{station.dthrust_n:6.3f}   "
            f"{station.airfoil:6s}"
        )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--preset", choices=PRESET_CHOICES, default=None)
    parser.add_argument("--spanload", choices=SPANLOAD_CHOICES, default=None)
    parser.add_argument("--mode", choices=MODE_CHOICES, default=None)
    parser.add_argument("--reference-geometry-csv", type=Path, default=None, help="Reference geometry CSV or XLSX.")
    parser.add_argument("--rpm", type=float, default=None)
    parser.add_argument("--thrust", type=float, default=None, help="Target total thrust in newtons.")
    parser.add_argument("--v-inf", type=float, default=None, help="Freestream velocity normal to the propeller plane in m/s.")
    parser.add_argument("--diameter-in", type=float, default=None, help="Propeller diameter in inches.")
    parser.add_argument("--blades", type=int, default=None)
    parser.add_argument("--rho", type=float, default=None)
    parser.add_argument("--hub-radius-fraction", type=float, default=None)
    parser.add_argument("--chord-min-in", type=float, default=None)
    parser.add_argument("--chord-max-in", type=float, default=None)
    parser.add_argument("--chord-blend", type=float, default=None, help="0 keeps the aero planform estimate, 1 keeps the reference chord.")
    parser.add_argument("--twist-blend", type=float, default=None, help="0 applies all twist correction, 1 keeps reference twist.")
    parser.add_argument("--smooth-passes", type=int, default=None)
    parser.add_argument("--alpha-min", type=float, default=None, help="Lower section alpha sanity/penalty limit in degrees.")
    parser.add_argument("--alpha-max", type=float, default=None, help="Upper section alpha sanity/penalty limit in degrees.")
    parser.add_argument("--alpha-step", type=float, default=None, help="Legacy alpha-search step retained for compatibility.")
    parser.add_argument("--reference-csv", type=Path, default=None, help="Fallback reference geometry CSV or XLSX.")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--lock-chord", action="store_true")
    parser.add_argument("--lock-airfoil", action="store_true")
    parser.add_argument("--optimize-twist-only", action="store_true")
    parser.add_argument("--make-plots", action="store_true")
    parser.add_argument("--optimizer", choices=OPTIMIZER_CHOICES, default=None)
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--tolerance", type=float, default=None)


def apply_defaults(args: argparse.Namespace) -> argparse.Namespace:
    reference_path = default_reference_csv_path()
    reference_for_default = builtin_reference_stations()
    max_chord_fraction = max(station.chord_fraction for station in reference_for_default)

    if args.preset in {"thesis_gmt_22", "thesis_gmt_22_static"}:
        if args.diameter_in is None:
            args.diameter_in = 22.0
        if args.spanload is None:
            args.spanload = "gmt_bell"
        if args.mode is None:
            args.mode = "reference_regularized"
        args.lock_chord = True
        args.lock_airfoil = True
        args.optimize_twist_only = True
        if args.smooth_passes is None:
            args.smooth_passes = 3
        if args.rpm is None:
            args.rpm = 4000.0
        if args.v_inf is None:
            args.v_inf = 0.0
        if args.thrust is None:
            args.thrust = 35.0
        if args.preset == "thesis_gmt_22_static" and args.blades is None:
            args.blades = 2

    if args.spanload is None:
        args.spanload = "gmt_bell"
    if args.mode is None:
        args.mode = "reference_regularized"
    if args.rpm is None:
        args.rpm = 4500.0
    if args.thrust is None:
        args.thrust = 35.0
    if args.v_inf is None:
        args.v_inf = 0.0
    if args.diameter_in is None:
        args.diameter_in = 22.0
    if args.blades is None:
        args.blades = 2
    if args.rho is None:
        args.rho = 1.225
    if args.hub_radius_fraction is None:
        args.hub_radius_fraction = 0.20
    if args.chord_min_in is None:
        args.chord_min_in = 0.50
    if args.chord_max_in is None:
        default_radius_m = diameter_in_to_radius_m(args.diameter_in)
        args.chord_max_in = max_chord_fraction * meters_to_inches(default_radius_m) * DEFAULT_REFERENCE_MAX_CHORD_MARGIN
    if args.chord_blend is None:
        args.chord_blend = 0.58
    if args.twist_blend is None:
        args.twist_blend = 0.0 if args.optimize_twist_only else 0.20
    if args.smooth_passes is None:
        args.smooth_passes = 2
    if args.alpha_min is None:
        args.alpha_min = -2.0
    if args.alpha_max is None:
        args.alpha_max = 18.0
    if args.alpha_step is None:
        args.alpha_step = 0.5
    if args.reference_csv is None:
        args.reference_csv = reference_path
    if args.output_dir is None:
        args.output_dir = Path(__file__).resolve().parent.parent / "results" / "geometry" / "optimized"
    if args.optimizer is None:
        args.optimizer = "auto"
    if args.max_iterations is None:
        args.max_iterations = 35
    if args.tolerance is None:
        args.tolerance = 1.0e-4
    return args


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reduced-order inverse/calibration solver for GMT-inspired propeller geometry.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_arguments(parser)
    parsed = parser.parse_args()
    return apply_defaults(parsed)


def case_family_for_spanload(spanload_type: str) -> str:
    return "mil" if spanload_type == "minimum_induced_loss" else "gmt"


def optimized_case_name(spanload_type: str) -> str:
    return OPTIMIZED_MIL_CASE if spanload_type == "minimum_induced_loss" else OPTIMIZED_GMT_CASE


def reference_case_name(spanload_type: str) -> str:
    return REFERENCE_MIL_CASE if spanload_type == "minimum_induced_loss" else REFERENCE_GMT_CASE


def output_spanload_name(spanload_type: str) -> str:
    return case_family_for_spanload(spanload_type)


def main() -> None:
    args = parse_args()
    config = DesignInputs(
        blades=args.blades,
        radius_m=diameter_in_to_radius_m(args.diameter_in),
        hub_radius_fraction=args.hub_radius_fraction,
        rpm=args.rpm,
        freestream_mps=args.v_inf,
        thrust_target_n=args.thrust,
        rho=args.rho,
        spanload_type=args.spanload,
        mode=args.mode,
        chord_min_in=args.chord_min_in,
        chord_max_in=args.chord_max_in,
        chord_blend=args.chord_blend,
        twist_blend=args.twist_blend,
        smooth_passes=args.smooth_passes,
        alpha_search_min_deg=args.alpha_min,
        alpha_search_max_deg=args.alpha_max,
        alpha_search_step_deg=args.alpha_step,
        reference_csv_path=args.reference_csv,
        reference_geometry_csv=args.reference_geometry_csv,
        output_directory=args.output_dir,
        lock_chord=args.lock_chord,
        lock_airfoil=args.lock_airfoil,
        optimize_twist_only=args.optimize_twist_only,
        make_plots=args.make_plots,
        optimizer=args.optimizer,
        max_iterations=args.max_iterations,
        tolerance=args.tolerance,
    )
    validate_user_inputs(config)

    fallback_reference_path = config.reference_csv_path
    reference_variant = "mil_22" if config.spanload_type == "minimum_induced_loss" else "gmt_22"
    reference_stations, reference_source, input_warnings = load_reference_geometry(
        config.reference_geometry_csv,
        fallback_reference_path,
        reference_variant,
    )
    result = optimize_geometry(config, reference_stations, input_warnings, reference_source)

    output_dir = config.output_directory
    optimized_name = optimized_case_name(config.spanload_type)
    reference_name = reference_case_name(config.spanload_type)
    optimized_csv = output_dir / f"{optimized_name}.csv"
    optimized_xlsx = output_dir / f"{optimized_name}.xlsx"
    reference_csv = output_dir.parent / "reference" / f"{reference_name}.csv"
    history_csv = output_dir / "convergence_history.csv"
    summary_json = output_dir / "solver_summary.json"
    methodology_txt = output_dir / "methodology_statement.txt"

    write_csv(result, optimized_csv)
    write_reference_csv(reference_stations, config.radius_m, reference_csv)
    write_convergence_history_csv(result.convergence_history, history_csv)
    plot_warnings: List[str] = []
    if config.make_plots:
        plot_warnings = plot_thesis_outputs(result, reference_stations, output_dir)
        if plot_warnings:
            result = replace(result, warnings=dedupe_warnings(list(result.warnings) + plot_warnings))
    write_summary_json(result, summary_json)
    write_methodology_statement(methodology_txt)
    audit_reference_path = config.reference_csv_path
    if audit_reference_path.suffix.lower() not in {".xlsx", ".xlsm"}:
        audit_reference_path = default_reference_csv_path()
    if audit_reference_path.exists() and audit_reference_path.suffix.lower() in {".xlsx", ".xlsm"}:
        write_scaled_reference_geometry_audit(output_dir.parent / "reference", audit_reference_path, output_dir.parent / "optimized")
    write_xlsx(result, reference_stations, optimized_xlsx)

    print_summary(result)
    print()
    print(f"Wrote optimized geometry CSV to: {optimized_csv}")
    print(f"Wrote optimized geometry Excel workbook to: {optimized_xlsx}")
    print(f"Wrote reference geometry CSV to: {reference_csv}")
    print(f"Wrote convergence history CSV to: {history_csv}")
    print(f"Wrote solver summary JSON to: {summary_json}")
    print(f"Wrote methodology statement to: {methodology_txt}")
    if config.make_plots and not plot_warnings:
        print(f"Wrote thesis plots to: {output_dir}")


if __name__ == "__main__":
    main()


# CLI examples are kept in this file for thesis reproducibility. They mirror CLI_EXAMPLES above.
#
# Recommended thesis run:
# python Geometry_optimization.py \
#   --preset thesis_gmt_22 \
#   --reference-geometry-csv reference_gmt.csv \
#   --rpm 4000 \
#   --thrust 35 \
#   --v-inf 0 \
#   --output-dir outputs/thesis_gmt_22 \
#   --make-plots
#
# Free optimization comparison:
# python Geometry_optimization.py \
#   --spanload gmt_bell \
#   --mode free_optimize \
#   --diameter-in 22 \
#   --rpm 4000 \
#   --thrust 35 \
#   --v-inf 0 \
#   --output-dir outputs/optimized_gmt_free
#
# MIL comparison:
# python Geometry_optimization.py \
#   --spanload minimum_induced_loss \
#   --mode reference_regularized \
#   --diameter-in 22 \
#   --rpm 4000 \
#   --thrust 35 \
#   --v-inf 0 \
#   --output-dir outputs/optimized_mil
