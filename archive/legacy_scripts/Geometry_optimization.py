from __future__ import annotations

import argparse
import csv
import json
import math
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Sequence
from xml.sax.saxutils import escape

from common_plot_style import apply_matplotlib_style, save_matplotlib_figure, style_matplotlib_axes
from output_paths import (
    canonical_case_dir_name,
    get_figures_dir,
    get_results_dir,
    resolve_project_output_path,
    assert_geometry_output_path,
    assert_verification_output_path,
    write_metadata_sidecar,
)


SCALED_REFERENCE_STATEMENT = (
    "The reference MIL and GMT geometries are NASA/SciTech 18-inch geometries scaled to 22 inches "
    "by preserving r/R, c/R, twist, and airfoil schedule."
)

ORIGINAL_REFERENCE_DIAMETER_IN = 18.0
SCALED_REFERENCE_DIAMETER_IN = 22.0
SCALED_REFERENCE_SCALE_FACTOR = SCALED_REFERENCE_DIAMETER_IN / ORIGINAL_REFERENCE_DIAMETER_IN
MODEL_SOURCE_OPTIMIZER = "optimizer_reduced_order"
GENERATING_SCRIPT = "Geometry_optimization.py"
OPTIMIZER_GEOMETRY_SUFFIX = "optimizer_geometry"
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
AIR_DYNAMIC_VISCOSITY_KG_M_S = 1.81e-5
DEFAULT_REFERENCE_MAX_CHORD_MARGIN = 1.08
GMT_SPANLOAD_TYPES = {"gmt_bell", "gmt_bell_simple", "gmt_bell_cubic", "paper_inspired", "reference_calibrated_gmt"}
TIP_UNLOAD_START_MU = 0.78
SPANLOAD_CHOICES = [
    "minimum_induced_loss",
    "gmt_bell",
    "gmt_bell_simple",
    "gmt_bell_cubic",
    "paper_inspired",
    "reference_calibrated_gmt",
]
MODE_CHOICES = ["free_optimize", "reference_regularized", "reproduce_reference", "gmt_requirements"]
OPTIMIZER_CHOICES = ["auto", "coordinate_search", "scipy"]
PRESET_CHOICES = [
    "thesis_gmt_22",
    "thesis_gmt_22_static",
    "thesis_gmt_requirements_22",
    "thesis_gmt_22_free_design",
    "thesis_gmt_22_manufacturable",
    "thesis_mil_22_manufacturable",
]
REFERENCE_MIL_CASE = "reference_mil"
REFERENCE_GMT_CASE = "reference_gmt"
OPTIMIZED_MIL_CASE = "optimized_mil"
OPTIMIZED_GMT_CASE = "optimized_gmt"
STANDARD_CASE_NAMES = [REFERENCE_MIL_CASE, REFERENCE_GMT_CASE, OPTIMIZED_MIL_CASE, OPTIMIZED_GMT_CASE]
THESIS_THRUST_TOLERANCE_FRACTION = 0.05
DOMINANT_VALIDITY_PENALTY = 1.0e6
THRUST_VALIDITY_SWEEP_TARGETS_N = (15.0, 20.0, 25.0, 30.0, 35.0)
FAILURE_NONE = "none"
FAILURE_CLIPPING_FRACTION = "clipping_fraction_exceeds_limit"
FAILURE_ALPHA_LIMIT_FRACTION = "alpha_limit_fraction_exceeds_limit"
FAILURE_TWIST_MANUFACTURABILITY = "twist_manufacturability_failed"
FAILURE_NEGATIVE_OR_NONFINITE_LOADING = "negative_or_nonfinite_loading"
FAILURE_TARGET_INFEASIBLE_LOCKED = "target_thrust_infeasible_under_locked_geometry"
FAILURE_THRUST_TARGET_NOT_MET = "thrust_target_not_met"
FAILURE_AIRFOIL_SCHEDULE_INVALID = "airfoil_schedule_invalid"
FAILURE_MULTIPLE = "multiple_validity_failures"
RECOMMEND_GEOMETRY_VALID = "geometry is valid for CAD export"
RECOMMEND_REDUCE_THRUST = "reduce target thrust"
RECOMMEND_INCREASE_RPM = "increase RPM"
RECOMMEND_ALLOW_CHORD = "allow chord optimization"
RECOMMEND_ALLOW_AIRFOIL = "allow airfoil schedule optimization"
RECOMMEND_REDUCE_GMT = "reduce GMT aggressiveness"
RECOMMEND_EXPAND_POLARS = "expand valid polar database"
THRUST_RPM_SWEEP_RPMS = (3000.0, 3500.0, 4000.0, 4500.0, 5000.0)
GMT_AGGRESSIVENESS_LABELS = {
    "mil": 0.0,
    "mil-like": 0.0,
    "mild": 0.25,
    "moderate": 0.55,
    "aggressive": 1.0,
}
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


def chord_in_to_fraction(chord_in: float | None, diameter_in: float) -> float:
    if chord_in is None or chord_in <= 0.0 or diameter_in <= 0.0:
        return math.inf
    return chord_in / max(0.5 * diameter_in, 1.0e-12)


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

    def alpha_for_cl(self, cl_value: float) -> float:
        return self.alpha_l0_deg + math.degrees(cl_value / max(self.cl_alpha_per_rad, 1.0e-9))

    @property
    def alpha_stall_deg(self) -> float:
        return self.alpha_for_cl(self.cl_max)


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
    w_positive_loading: float
    w_tip_positive_unload: float
    w_twist_bump: float = 0.0
    w_twist_gradient: float = 0.0
    w_twist_curvature: float = 0.0
    w_aero_limits: float = 0.0
    w_twist_curvature_smooth: float = 0.0
    w_cl_curvature_smooth: float = 0.0
    w_tip_cl_target: float = 0.0


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
    unclipped_cl_demand: float
    cl: float
    cl_max: float
    cl_margin: float
    cd: float
    reynolds_number: float
    alpha_stall_deg: float
    dthrust_n: float
    dtorque_nm: float
    hit_alpha_min: bool
    hit_alpha_max: bool
    hit_cl_limit: bool
    negative_force: bool
    impossible_force: bool


@dataclass(frozen=True)
class DesignInputs:
    preset: str = ""
    blades: int = 2
    radius_m: float = diameter_in_to_radius_m(22.0)
    hub_radius_fraction: float = 0.20
    rpm: float = 4000.0
    freestream_mps: float = 0.0
    thrust_target_n: float = 35.0
    rho: float = 1.225
    spanload_type: str = "gmt_bell"
    mode: str = "reference_regularized"
    gmt_aggressiveness: float = 1.0
    chord_min_in: float = 0.50
    chord_max_in: float = 2.45
    twist_min_deg: float = 0.0
    twist_max_deg: float = 65.0
    chord_blend: float = 0.58
    twist_blend: float = 0.20
    smooth_passes: int = 2
    alpha_search_min_deg: float = -2.0
    alpha_search_max_deg: float = 18.0
    alpha_search_step_deg: float = 0.5
    reference_csv_path: Path = Path(__file__).resolve().parent / "Reference_Propeller_Geometry.xlsx"
    reference_geometry_csv: Path | None = None
    output_directory: Path = (
        Path(__file__).resolve().parent.parent / "results" / "geometry_optimizer" / OPTIMIZED_GMT_CASE
    )
    lock_chord: bool = False
    lock_airfoil: bool = False
    optimize_twist_only: bool = False
    make_plots: bool = False
    optimizer: str = "auto"
    max_iterations: int = 35
    tolerance: float = 1.0e-4
    manufacturable_twist: bool = False
    max_positive_twist_bump_deg: float = 0.5
    twist_constraint_start_mu: float = 0.35
    max_abs_twist_gradient_deg_per_mu_limit: float = 100.0
    max_twist_curvature_limit: float = 1500.0
    max_cl_limit_fraction_pct: float = 10.0
    max_alpha_limit_fraction_pct: float = 10.0
    allowed_airfoils: tuple[str, ...] = ()

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
    unclipped_cl_demand: float
    cl: float
    cl_max: float
    cl_margin: float
    cd: float
    reynolds_number: float
    alpha_stall_deg: float
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
    is_clipped: bool
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
    cl_limit_fraction_pct: float = 0.0
    alpha_limit_fraction_pct: float = 0.0
    high_alpha_fraction_pct: float = 0.0
    negative_loading_fraction_pct: float = 0.0
    nonfinite_loading_fraction_pct: float = 0.0
    manufacturable_twist_pass: bool = True
    valid_for_optimizer_summary: bool = False
    valid_for_bemt_summary: bool = False
    valid_for_thesis_export: bool = False
    failure_reason: str = FAILURE_NONE
    failure_reasons: Sequence[str] = ()
    aero_calibration_used: bool = False
    calibration_mode: str = "none"
    requested_gmt_aggressiveness: float = 1.0
    achieved_gmt_aggressiveness: float = 1.0
    optimizer_fallback_used: bool = False
    optimizer_fallback_reason: str = ""


@dataclass(frozen=True)
class AlphaMatchResult:
    parameters: OptimizationParameters
    selected_result: DesignResult | None
    valid_candidate_found: bool
    target_met_with_valid_candidate: bool
    selected_candidate_valid: bool
    failure_reasons: Sequence[str] = ()


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


def allowed_airfoils_for_config(config: DesignInputs) -> tuple[str, ...]:
    if config.allowed_airfoils:
        return tuple(canonical_allowed_airfoil_name(name) for name in config.allowed_airfoils)
    return tuple(AIRFOIL_LIBRARY.keys())


def canonical_allowed_airfoil_name(raw_airfoil: object) -> str:
    airfoil = str(raw_airfoil).strip()
    if airfoil in AIRFOIL_LIBRARY:
        return airfoil
    upper = airfoil.upper()
    if upper in AIRFOIL_LIBRARY:
        return upper
    return airfoil


def parse_gmt_aggressiveness(value: object) -> float:
    if value is None:
        return 1.0
    if isinstance(value, (int, float)):
        return clamp(float(value), 0.0, 1.0)
    text = str(value).strip().lower()
    if text in GMT_AGGRESSIVENESS_LABELS:
        return GMT_AGGRESSIVENESS_LABELS[text]
    try:
        return clamp(float(text), 0.0, 1.0)
    except ValueError as exc:
        choices = ", ".join(sorted(GMT_AGGRESSIVENESS_LABELS))
        raise ValueError(f"gmt_aggressiveness must be numeric 0..1 or one of: {choices}") from exc


def parse_allowed_airfoils(value: str | None) -> tuple[str, ...]:
    if value is None or not value.strip():
        return ()
    return tuple(canonical_allowed_airfoil_name(part.strip()) for part in value.split(",") if part.strip())


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
    if not 0.0 <= config.gmt_aggressiveness <= 1.0:
        raise ValueError("gmt_aggressiveness must be between 0 and 1")
    if config.chord_min_m <= 0.0:
        raise ValueError("minimum chord must be positive")
    if config.chord_max_m <= config.chord_min_m:
        raise ValueError("maximum chord must exceed minimum chord")
    if config.twist_max_deg <= config.twist_min_deg:
        raise ValueError("twist maximum must exceed twist minimum")
    if config.twist_min_deg < -10.0 or config.twist_max_deg > 75.0:
        raise ValueError("twist bounds must stay within the -10 to 75 deg sanity range")
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
    if config.max_positive_twist_bump_deg < 0.0:
        raise ValueError("max positive twist bump must be non-negative")
    if config.max_abs_twist_gradient_deg_per_mu_limit <= 0.0:
        raise ValueError("max twist gradient must be positive")
    if config.max_twist_curvature_limit <= 0.0:
        raise ValueError("max twist curvature must be positive")
    if not 0.0 <= config.max_cl_limit_fraction_pct <= 100.0:
        raise ValueError("max Cl limit fraction percent must be between 0 and 100")
    if not 0.0 <= config.max_alpha_limit_fraction_pct <= 100.0:
        raise ValueError("max alpha limit fraction percent must be between 0 and 100")
    unknown_airfoils = [name for name in allowed_airfoils_for_config(config) if name not in AIRFOIL_LIBRARY]
    if unknown_airfoils:
        raise ValueError(f"allowed airfoil schedule contains unknown airfoil(s): {', '.join(unknown_airfoils)}")


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


def active_operating_point_dict(config: DesignInputs) -> dict[str, object]:
    return {
        "diameter_in": meters_to_inches(2.0 * config.radius_m),
        "radius_m": config.radius_m,
        "rpm": config.rpm,
        "freestream_mps": config.freestream_mps,
        "target_thrust_n": config.thrust_target_n,
        "rho_kg_m3": config.rho,
        "hover_mode": abs(config.freestream_mps) <= 1.0e-9,
        "operating_point": THESIS_22_STATIC_OPERATING_POINT["operating_point"]
        if is_thesis_22_static_operating_point(config)
        else "custom",
    }


def methodology_statement_for_config(config: DesignInputs) -> str:
    diameter_in = meters_to_inches(2.0 * config.radius_m)
    return (
        f"{SCALED_REFERENCE_STATEMENT} The scaled reference and optimized cases are evaluated at the active "
        f"operating point: {diameter_in:.6g} in diameter, {config.rpm:.6g} rpm, "
        f"V_inf = {config.freestream_mps:.6g} m/s, rho = {config.rho:.6g} kg/m^3, "
        f"and {config.thrust_target_n:.6g} N target thrust. This is not the NASA/SciTech original "
        "18-inch operating point."
    )


def induced_velocity_formula_inputs(result: DesignResult) -> dict[str, object]:
    disk_area = actuator_disk_area(result.config.radius_m)
    return {
        "formula": "vi = -0.5*V_inf + sqrt(0.25*V_inf^2 + T/(2*rho*A))",
        "thrust_n": result.config.thrust_target_n,
        "rho_kg_m3": result.config.rho,
        "radius_m": result.config.radius_m,
        "diameter_m": 2.0 * result.config.radius_m,
        "disk_area_m2": disk_area,
        "disk_area_definition": "full propeller disk area pi*R^2",
        "hub_radius_fraction_not_subtracted": result.config.hub_radius_fraction,
        "freestream_mps": result.config.freestream_mps,
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
                optimized_root / "optimized_mil" / optimizer_geometry_csv_name("optimized_mil"),
                optimized_root / optimizer_geometry_csv_name("optimized_mil"),
                optimized_root / "optimized_mil" / "optimized_mil.csv",
                optimized_root / "optimized_mil.csv",
            ]
        ),
        "GMT": optimized_rows_by_mu(
            [
                optimized_root / "optimized_gmt" / optimizer_geometry_csv_name("optimized_gmt"),
                optimized_root / optimizer_geometry_csv_name("optimized_gmt"),
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


def write_markdown_table(rows: Sequence[dict[str, object]], output_path: Path, *, output_domain: str = "geometry") -> None:
    _assert_output_path(output_path, output_domain)
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


def write_dict_rows_csv(rows: Sequence[dict[str, object]], output_path: Path, *, output_domain: str = "geometry") -> None:
    _assert_output_path(output_path, output_domain)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        output_path.write_text("", encoding="utf-8")
        write_metadata_sidecar(output_path, _base_output_metadata(case_name=output_path.stem))
        return
    headers = list(rows[0].keys())
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})
    write_metadata_sidecar(output_path, _base_output_metadata(case_name=output_path.stem))


def write_scaled_reference_geometry_audit(
    output_dir: Path,
    workbook_path: Path | None = None,
    optimized_root: Path | None = None,
) -> tuple[Path, Path, Path]:
    workbook_path = workbook_path or default_reference_csv_path()
    optimized_root = optimized_root or output_dir.parent / "optimized"
    assert_geometry_output_path(output_dir)
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


def annulus_widths(mu_values: Sequence[float], hub_mu: float, radius_m: float | None = None) -> List[float]:
    if radius_m is None:
        radius_m = hub_mu
        hub_mu = 0.0
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


def spanload_density(mu: float, kind: str, hub_mu: float, gmt_aggressiveness: float = 1.0) -> float:
    x = clamp((mu - hub_mu) / max(1.0 - hub_mu, 1.0e-9), 0.0, 1.0)
    root_factor = max(x, 1.0e-9) ** 0.75
    elliptic_tip = max(1.0 - x * x, 0.0)
    mil_density = root_factor * math.sqrt(elliptic_tip)

    if kind == "minimum_induced_loss":
        return mil_density
    if kind == "gmt_bell_simple":
        aggressive_density = root_factor * elliptic_tip**2.0
        return (1.0 - gmt_aggressiveness) * mil_density + gmt_aggressiveness * aggressive_density
    if kind == "gmt_bell_cubic":
        aggressive_density = root_factor * elliptic_tip**3.0
        return (1.0 - gmt_aggressiveness) * mil_density + gmt_aggressiveness * aggressive_density
    if kind in {"paper_inspired", "reference_calibrated_gmt", "gmt_bell"}:
        midspan_hold = 1.0 + 0.70 * math.exp(-((x - 0.58) / 0.22) ** 2)
        shoulder = 1.0 + 0.20 * math.exp(-((x - 0.45) / 0.24) ** 2)
        rapid_tip_unload = max(1.0 - x**2.7, 0.0) ** 2.6
        aggressive_density = root_factor * midspan_hold * shoulder * rapid_tip_unload
        return (1.0 - gmt_aggressiveness) * mil_density + gmt_aggressiveness * aggressive_density
    raise ValueError(f"unsupported spanload_type: {kind}")


def normalized_section_weights(
    mu_values: Sequence[float],
    dr_values: Sequence[float],
    kind: str,
    hub_mu: float,
    gmt_aggressiveness: float = 1.0,
) -> List[float]:
    raw = [
        spanload_density(mu, kind, hub_mu, gmt_aggressiveness) * dr_m
        for mu, dr_m in zip(mu_values, dr_values)
    ]
    total = sum(raw)
    if total <= 0.0:
        raise ValueError("spanload distribution collapsed to zero")
    return [value / total for value in raw]


def is_gmt_spanload_type(spanload_type: str) -> bool:
    return spanload_type in GMT_SPANLOAD_TYPES


def tip_positive_loading_constraints_active(config: DesignInputs) -> bool:
    return config.mode != "reproduce_reference" and is_gmt_spanload_type(config.spanload_type)


def twist_slope_constraints_active(config: DesignInputs) -> bool:
    return config.manufacturable_twist or config.mode == "gmt_requirements" or is_gmt_spanload_type(config.spanload_type)


def local_inflow_density_factors(
    mu_values: Sequence[float],
    kind: str,
    hub_mu: float,
    gmt_aggressiveness: float = 1.0,
) -> List[float]:
    densities = [spanload_density(mu, kind, hub_mu, gmt_aggressiveness) for mu in mu_values]
    average_density = sum(densities) / max(len(densities), 1)
    if average_density <= 0.0:
        return [1.0] * len(densities)
    return [density / average_density for density in densities]


def local_induced_velocity_multiplier(density_factor: float) -> float:
    return 0.82 + 0.50 * max(density_factor, 0.0)


def gmt_tip_target_cl(mu: float) -> float:
    normalized_tip = clamp((mu - TIP_UNLOAD_START_MU) / max(1.0 - TIP_UNLOAD_START_MU, 1.0e-9), 0.0, 1.0)
    return 0.015 + 0.30 * (1.0 - normalized_tip) ** 1.7


def squared_second_difference_penalty(values: Sequence[float], scale: float) -> float:
    if len(values) < 3:
        return 0.0
    safe_scale = max(abs(scale), 1.0e-9)
    total = 0.0
    for idx in range(1, len(values) - 1):
        second_difference = values[idx + 1] - 2.0 * values[idx] + values[idx - 1]
        total += (second_difference / safe_scale) ** 2
    return total / max(len(values) - 2, 1)


def interval_slope_excess_penalty(
    mu_values: Sequence[float],
    values: Sequence[float],
    limit: float,
) -> float:
    if len(values) < 2:
        return 0.0
    safe_limit = max(abs(limit), 1.0e-9)
    total = 0.0
    count = 0
    for left_mu, right_mu, left_value, right_value in zip(mu_values[:-1], mu_values[1:], values[:-1], values[1:]):
        dmu = right_mu - left_mu
        if abs(dmu) <= 1.0e-12:
            continue
        slope = (right_value - left_value) / dmu
        total += (max(0.0, abs(slope) - safe_limit) / safe_limit) ** 2
        count += 1
    return total / max(count, 1)


def normalized_spanload(
    mu_values: Sequence[float],
    kind: str,
    hub_mu: float,
    gmt_aggressiveness: float = 1.0,
) -> List[float]:
    dr_values = annulus_widths(mu_values, hub_mu, 1.0)
    return normalized_section_weights(mu_values, dr_values, kind, hub_mu, gmt_aggressiveness)


def actuator_disk_area(radius_m: float) -> float:
    return math.pi * max(radius_m * radius_m, 1.0e-8)


def disk_induced_velocity(thrust_n: float, rho: float, radius_m: float, hub_mu: float, v_inf: float) -> float:
    del hub_mu
    disk_area = actuator_disk_area(radius_m)
    return -0.5 * v_inf + math.sqrt(max(0.25 * v_inf * v_inf + thrust_n / max(2.0 * rho * disk_area, 1.0e-12), 0.0))


def thrust_to_induced_velocity(thrust_n: float, rho: float, radius_m: float, hub_mu: float, v_inf: float) -> float:
    return disk_induced_velocity(thrust_n, rho, radius_m, hub_mu, v_inf)


def smooth_series(values: Sequence[float], passes: int) -> List[float]:
    smoothed = list(values)
    for _ in range(max(passes, 0)):
        updated = list(smoothed)
        for idx in range(1, len(smoothed) - 1):
            updated[idx] = 0.25 * smoothed[idx - 1] + 0.5 * smoothed[idx] + 0.25 * smoothed[idx + 1]
        smoothed = updated
    return smoothed


def local_alpha_target(
    base_alpha_deg: float,
    mu: float,
    hub_mu: float,
    spanload_type: str,
    gmt_aggressiveness: float = 1.0,
) -> float:
    x = clamp((mu - hub_mu) / max(1.0 - hub_mu, 1.0e-9), 0.0, 1.0)
    bell_bias = 0.0
    if spanload_type in {"gmt_bell", "paper_inspired", "reference_calibrated_gmt"}:
        bell_bias = gmt_aggressiveness * (1.6 * (0.55 - x) - 3.0 * smoothstep((x - 0.72) / 0.28))
    return clamp(base_alpha_deg + bell_bias, 2.0, 16.0)


def assign_target_alpha(
    airfoil: AirfoilModel,
    base_alpha_deg: float,
    mu: float,
    spanload_type: str,
    gmt_aggressiveness: float = 1.0,
) -> float:
    del airfoil
    return local_alpha_target(base_alpha_deg, mu, 0.20, spanload_type, gmt_aggressiveness)


def reference_loading_proxy(station: ReferenceStation) -> float:
    return station.chord_fraction * max(station.mu, 0.05) ** 1.25 * max(math.sin(math.radians(station.twist_deg)), 0.15)


def normalized_reference_loading(stations: Sequence[ReferenceStation], dr_values: Sequence[float]) -> List[float]:
    raw = [reference_loading_proxy(station) * dr_m for station, dr_m in zip(stations, dr_values)]
    total = sum(raw)
    if total <= 0.0:
        return [1.0 / max(len(stations), 1)] * len(stations)
    return [value / total for value in raw]


def smoothness_penalty(chords_m: Sequence[float], twists_deg: Sequence[float], radius_m: float) -> float:
    del radius_m
    if len(chords_m) < 3 or len(twists_deg) < 3:
        return 0.0
    penalty = 0.0
    count = min(len(chords_m), len(twists_deg))
    for idx in range(1, count - 1):
        penalty += abs(chords_m[idx + 1] - 2.0 * chords_m[idx] + chords_m[idx - 1])
        penalty += abs(twists_deg[idx + 1] - 2.0 * twists_deg[idx] + twists_deg[idx - 1]) / 100.0
    return penalty / max(count - 2, 1)


def station_twist_derivatives(
    mu_values: Sequence[float],
    twist_values_deg: Sequence[float],
) -> tuple[List[float], List[float]]:
    count = min(len(mu_values), len(twist_values_deg))
    if count <= 1:
        return [0.0] * count, [0.0] * count

    interval_slopes: List[float] = []
    for left_mu, right_mu, left_twist, right_twist in zip(
        mu_values[: count - 1],
        mu_values[1:count],
        twist_values_deg[: count - 1],
        twist_values_deg[1:count],
    ):
        dmu = right_mu - left_mu
        interval_slopes.append((right_twist - left_twist) / dmu if abs(dmu) > 1.0e-12 else 0.0)

    station_slopes: List[float] = []
    for idx in range(count):
        if idx == 0:
            station_slopes.append(interval_slopes[0])
        elif idx == count - 1:
            station_slopes.append(interval_slopes[-1])
        else:
            station_slopes.append(0.5 * (interval_slopes[idx - 1] + interval_slopes[idx]))

    station_curvatures = [0.0] * count
    for idx in range(1, count - 1):
        left_step = mu_values[idx] - mu_values[idx - 1]
        right_step = mu_values[idx + 1] - mu_values[idx]
        span = mu_values[idx + 1] - mu_values[idx - 1]
        if min(abs(left_step), abs(right_step), abs(span)) <= 1.0e-12:
            continue
        left_slope = (twist_values_deg[idx] - twist_values_deg[idx - 1]) / left_step
        right_slope = (twist_values_deg[idx + 1] - twist_values_deg[idx]) / right_step
        station_curvatures[idx] = 2.0 * (right_slope - left_slope) / span

    if count > 2:
        station_curvatures[0] = station_curvatures[1]
        station_curvatures[-1] = station_curvatures[-2]
    return station_slopes, station_curvatures


def twist_manufacturability_metrics(
    mu_values: Sequence[float],
    twist_values_deg: Sequence[float],
    max_positive_bump_deg: float = 0.5,
    start_mu: float = 0.35,
    max_abs_gradient_deg_per_mu_limit: float = 100.0,
    max_curvature_limit: float = 1500.0,
) -> dict[str, Any]:
    count = min(len(mu_values), len(twist_values_deg))
    mu = list(mu_values[:count])
    twist = list(twist_values_deg[:count])
    positive_bumps: List[float] = []
    for left_mu, right_mu, left_twist, right_twist in zip(mu[:-1], mu[1:], twist[:-1], twist[1:]):
        if right_mu > start_mu:
            positive_bumps.append(max(0.0, right_twist - left_twist))

    slopes, curvatures = station_twist_derivatives(mu, twist)
    interval_gradients: List[float] = []
    for left_mu, right_mu, left_twist, right_twist in zip(mu[:-1], mu[1:], twist[:-1], twist[1:]):
        if right_mu <= start_mu:
            continue
        dmu = right_mu - left_mu
        if abs(dmu) > 1.0e-12:
            interval_gradients.append((right_twist - left_twist) / dmu)
    if not interval_gradients:
        interval_gradients = slopes
    curvatures_after_start = [value for station_mu, value in zip(mu, curvatures) if station_mu > start_mu]
    if not curvatures_after_start:
        curvatures_after_start = curvatures

    max_positive_bump = max(positive_bumps, default=0.0)
    positive_bump_count = sum(value > max_positive_bump_deg + 1.0e-9 for value in positive_bumps)
    max_abs_gradient = max((abs(value) for value in interval_gradients), default=0.0)
    max_curvature = max((abs(value) for value in curvatures_after_start), default=0.0)
    manufacturable_pass = (
        max_positive_bump <= max_positive_bump_deg + 1.0e-9
        and max_abs_gradient <= max_abs_gradient_deg_per_mu_limit + 1.0e-9
        and max_curvature <= max_curvature_limit + 1.0e-9
    )

    return {
        "max_positive_twist_bump_deg": max_positive_bump,
        "positive_twist_bump_count": positive_bump_count,
        "positive_twist_bumps_deg": positive_bumps,
        "max_abs_twist_gradient_deg_per_mu": max_abs_gradient,
        "max_twist_curvature": max_curvature,
        "manufacturable_twist_pass": manufacturable_pass,
        "max_positive_twist_bump_limit_deg": max_positive_bump_deg,
        "twist_constraint_start_mu": start_mu,
        "max_abs_twist_gradient_limit_deg_per_mu": max_abs_gradient_deg_per_mu_limit,
        "max_twist_curvature_limit": max_curvature_limit,
    }


def design_twist_metrics(config: DesignInputs, stations: Sequence[StationResult]) -> dict[str, Any]:
    return twist_manufacturability_metrics(
        [station.mu for station in stations],
        [station.twist_deg for station in stations],
        config.max_positive_twist_bump_deg,
        config.twist_constraint_start_mu,
        config.max_abs_twist_gradient_deg_per_mu_limit,
        config.max_twist_curvature_limit,
    )


def enforce_manufacturable_twist_profile(
    mu_values: Sequence[float],
    twist_values_deg: Sequence[float],
    config: DesignInputs,
) -> List[float]:
    twists = [clamp(value, config.twist_min_deg, config.twist_max_deg) for value in twist_values_deg]
    if len(twists) < 3:
        return twists

    twists = smooth_series(twists, max(config.smooth_passes, 6))
    start_mu = config.twist_constraint_start_mu
    max_positive = max(config.max_positive_twist_bump_deg, 0.0)
    max_gradient = max(config.max_abs_twist_gradient_deg_per_mu_limit, 1.0e-6)
    max_curvature = max(config.max_twist_curvature_limit, 1.0e-6)

    for _ in range(8):
        for idx in range(1, len(twists)):
            dmu = max(mu_values[idx] - mu_values[idx - 1], 1.0e-9)
            if mu_values[idx] > start_mu:
                twists[idx] = min(twists[idx], twists[idx - 1] + max_positive)
            twists[idx] = max(twists[idx], twists[idx - 1] - max_gradient * dmu)
            twists[idx] = min(twists[idx], twists[idx - 1] + max_gradient * dmu)

        updated = list(twists)
        for idx in range(1, len(twists) - 1):
            if mu_values[idx] <= start_mu:
                continue
            left_step = max(mu_values[idx] - mu_values[idx - 1], 1.0e-9)
            right_step = max(mu_values[idx + 1] - mu_values[idx], 1.0e-9)
            span = max(mu_values[idx + 1] - mu_values[idx - 1], 1.0e-9)
            left_slope = (twists[idx] - twists[idx - 1]) / left_step
            right_slope = (twists[idx + 1] - twists[idx]) / right_step
            curvature = 2.0 * (right_slope - left_slope) / span
            if abs(curvature) > max_curvature:
                updated[idx] = 0.25 * twists[idx - 1] + 0.5 * twists[idx] + 0.25 * twists[idx + 1]
        twists = [clamp(value, config.twist_min_deg, config.twist_max_deg) for value in updated]

    for idx in range(1, len(twists)):
        dmu = max(mu_values[idx] - mu_values[idx - 1], 1.0e-9)
        if mu_values[idx] > start_mu:
            twists[idx] = min(twists[idx], twists[idx - 1] + max_positive)
        twists[idx] = max(twists[idx], twists[idx - 1] - max_gradient * dmu)
        twists[idx] = min(twists[idx], twists[idx - 1] + max_gradient * dmu)
    return [clamp(value, config.twist_min_deg, config.twist_max_deg) for value in twists]


def enforce_positive_tip_loading_twist_profile(
    mu_values: Sequence[float],
    twist_values_deg: Sequence[float],
    config: DesignInputs,
    reference_stations: Sequence[ReferenceStation],
    density_factors: Sequence[float],
    airfoil_names: Sequence[str],
    induced_velocity: float,
    omega: float,
) -> List[float]:
    twists = list(twist_values_deg)
    if not tip_positive_loading_constraints_active(config):
        return twists

    for idx, (mu, station, airfoil_name) in enumerate(zip(mu_values, reference_stations, airfoil_names)):
        if mu < TIP_UNLOAD_START_MU:
            continue
        airfoil = AIRFOIL_LIBRARY[airfoil_name]
        radius_m = station.mu * config.radius_m
        density_factor = density_factors[idx] if idx < len(density_factors) else 1.0
        local_vi = induced_velocity * local_induced_velocity_multiplier(density_factor)
        v_axial, v_tan, _, _ = compute_section_velocities(config.freestream_mps, omega, radius_m, local_vi)
        phi_deg = math.degrees(math.atan2(v_axial, max(v_tan, 1.0e-6)))
        target_cl = gmt_tip_target_cl(mu)
        cl_floor = 0.005
        cl_ceiling = min(0.92 * airfoil.cl_max, max(target_cl, cl_floor + 0.015))
        alpha_floor = airfoil.alpha_for_cl(cl_floor)
        alpha_ceiling = min(config.alpha_search_max_deg - 0.75, airfoil.alpha_for_cl(cl_ceiling))
        twists[idx] = clamp(twists[idx], phi_deg + alpha_floor, phi_deg + alpha_ceiling)

    return [clamp(value, config.twist_min_deg, config.twist_max_deg) for value in twists]


def reference_chord_penalty(chords_m: Sequence[float], radius_m: float) -> float:
    if not chords_m:
        return 0.0
    reference_chords_m = [fraction * radius_m for fraction in REFERENCE_CHORD_FRACTION[: len(chords_m)]]
    if len(reference_chords_m) < len(chords_m):
        reference_chords_m.extend([reference_chords_m[-1]] * (len(chords_m) - len(reference_chords_m)))
    return rms([chord - reference for chord, reference in zip(chords_m, reference_chords_m)])


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
    cl_margin = airfoil.cl_max - cl_value
    cd_value = airfoil.cd(cl_value)
    phi_rad = math.radians(phi_deg)
    force_factor = cl_value * math.cos(phi_rad) - cd_value * math.sin(phi_rad)
    torque_factor = cl_value * math.sin(phi_rad) + cd_value * math.cos(phi_rad)
    dynamic_pressure = 0.5 * rho * v_rel * v_rel
    reynolds_number = rho * v_rel * chord_m / max(AIR_DYNAMIC_VISCOSITY_KG_M_S, 1.0e-12)
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
        unclipped_cl_demand=raw_cl,
        cl=cl_value,
        cl_max=airfoil.cl_max,
        cl_margin=cl_margin,
        cd=cd_value,
        reynolds_number=reynolds_number,
        alpha_stall_deg=airfoil.alpha_stall_deg,
        dthrust_n=dthrust_n,
        dtorque_nm=dtorque_nm,
        hit_alpha_min=hit_alpha_min,
        hit_alpha_max=hit_alpha_max,
        hit_cl_limit=hit_cl_limit,
        negative_force=negative_force,
        impossible_force=impossible_force,
    )


def weights_for_mode(mode: str) -> ObjectiveWeights:
    if mode == "gmt_requirements":
        return ObjectiveWeights(
            w_thrust=32.0,
            w_torque=5.5,
            w_spanload=3.0,
            w_reference_twist=0.12,
            w_reference_chord=0.08,
            w_smooth=0.35,
            w_bounds=8.0,
            w_positive_loading=70.0,
            w_tip_positive_unload=30.0,
            w_twist_bump=120.0,
            w_twist_gradient=45.0,
            w_twist_curvature=30.0,
            w_aero_limits=95.0,
            w_twist_curvature_smooth=9.0,
            w_cl_curvature_smooth=7.0,
            w_tip_cl_target=16.0,
        )
    if mode == "free_optimize":
        return ObjectiveWeights(
            w_thrust=22.0,
            w_torque=0.65,
            w_spanload=4.0,
            w_reference_twist=0.04,
            w_reference_chord=0.02,
            w_smooth=0.22,
            w_bounds=5.0,
            w_positive_loading=40.0,
            w_tip_positive_unload=28.0,
            w_twist_curvature_smooth=4.0,
            w_cl_curvature_smooth=4.0,
            w_tip_cl_target=8.0,
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
            w_positive_loading=12.0,
            w_tip_positive_unload=4.0,
            w_twist_curvature_smooth=1.0,
            w_cl_curvature_smooth=1.0,
        )
    return ObjectiveWeights(
        w_thrust=24.0,
        w_torque=0.55,
        w_spanload=4.5,
        w_reference_twist=0.85,
        w_reference_chord=0.35,
        w_smooth=0.28,
        w_bounds=5.0,
        w_positive_loading=48.0,
        w_tip_positive_unload=35.0,
        w_twist_curvature_smooth=5.0,
        w_cl_curvature_smooth=5.0,
        w_tip_cl_target=10.0,
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
    preferred = build_default_airfoil_schedule([station.mu])[0]
    allowed = allowed_airfoils_for_config(config)
    if preferred in allowed:
        return preferred
    return allowed[0] if allowed else preferred


def unique_failure_reasons(reasons: Iterable[str]) -> List[str]:
    unique: List[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if not reason or reason == FAILURE_NONE or reason in seen:
            continue
        seen.add(reason)
        unique.append(reason)
    return unique


def primary_failure_reason(reasons: Sequence[str]) -> str:
    if not reasons:
        return FAILURE_NONE
    if len(reasons) > 1:
        return FAILURE_MULTIPLE
    return reasons[0]


def calibration_mode_for_config(config: DesignInputs) -> str:
    if config.mode == "reference_regularized":
        return "reference_regularized_diagnostic"
    return "none"


def aero_calibration_used_for_config(config: DesignInputs) -> bool:
    return calibration_mode_for_config(config) != "none"


def locked_thesis_geometry(config: DesignInputs) -> bool:
    return config.lock_chord and config.lock_airfoil and config.optimize_twist_only


def free_design_enabled(config: DesignInputs) -> bool:
    return not config.optimize_twist_only and (not config.lock_chord or not config.lock_airfoil)


def airfoil_schedule_valid(config: DesignInputs, stations: Sequence[StationResult]) -> bool:
    allowed = set(allowed_airfoils_for_config(config))
    return all(station.airfoil in AIRFOIL_LIBRARY and station.airfoil in allowed for station in stations)


def station_validity_metrics(
    config: DesignInputs,
    stations: Sequence[StationResult],
    twist_metrics: dict[str, Any],
) -> dict[str, float | bool]:
    count = max(len(stations), 1)
    alpha_limit_fraction = sum(station.hit_alpha_min or station.hit_alpha_max for station in stations) / count
    cl_limit_fraction = sum(station.hit_cl_limit for station in stations) / count
    negative_loading_fraction = sum(station.negative_force for station in stations) / count
    nonfinite_loading_fraction = sum(
        station.impossible_force
        or not all(
            math.isfinite(value)
            for value in (
                station.dthrust_n,
                station.dtorque_nm,
                station.cl,
                station.alpha_deg,
                station.chord_m,
                station.twist_deg,
            )
        )
        for station in stations
    ) / count
    high_alpha_threshold = config.alpha_search_max_deg - 1.0
    high_alpha_fraction = sum(station.alpha_deg >= high_alpha_threshold for station in stations) / count
    return {
        "cl_limit_fraction_pct": 100.0 * cl_limit_fraction,
        "alpha_limit_fraction_pct": 100.0 * alpha_limit_fraction,
        "high_alpha_fraction_pct": 100.0 * high_alpha_fraction,
        "negative_loading_fraction_pct": 100.0 * negative_loading_fraction,
        "nonfinite_loading_fraction_pct": 100.0 * nonfinite_loading_fraction,
        "manufacturable_twist_pass": bool(twist_metrics["manufacturable_twist_pass"]),
        "airfoil_schedule_valid": airfoil_schedule_valid(config, stations),
    }


def validity_failure_reasons_from_metrics(
    config: DesignInputs,
    normalized_thrust_error: float,
    metrics: dict[str, float | bool],
    extra_failure_reasons: Sequence[str] = (),
) -> List[str]:
    reasons: List[str] = []
    if float(metrics["cl_limit_fraction_pct"]) > config.max_cl_limit_fraction_pct:
        reasons.append(FAILURE_CLIPPING_FRACTION)
    if float(metrics["alpha_limit_fraction_pct"]) > config.max_alpha_limit_fraction_pct:
        reasons.append(FAILURE_ALPHA_LIMIT_FRACTION)
    if twist_slope_constraints_active(config) and not bool(metrics["manufacturable_twist_pass"]):
        reasons.append(FAILURE_TWIST_MANUFACTURABILITY)
    if (
        float(metrics["negative_loading_fraction_pct"]) > 0.0
        or float(metrics["nonfinite_loading_fraction_pct"]) > 0.0
    ):
        reasons.append(FAILURE_NEGATIVE_OR_NONFINITE_LOADING)
    if not bool(metrics.get("airfoil_schedule_valid", True)):
        reasons.append(FAILURE_AIRFOIL_SCHEDULE_INVALID)
    reasons.extend(extra_failure_reasons)
    reasons = unique_failure_reasons(reasons)
    if normalized_thrust_error > THESIS_THRUST_TOLERANCE_FRACTION:
        if FAILURE_TARGET_INFEASIBLE_LOCKED not in reasons:
            reasons.append(FAILURE_THRUST_TARGET_NOT_MET)
    return unique_failure_reasons(reasons)


def validity_flags_from_metrics(
    result_finite: bool,
    torque_nm: float,
    normalized_thrust_error: float,
    failure_reasons: Sequence[str],
) -> tuple[bool, bool, bool]:
    valid_for_optimizer_summary = (
        normalized_thrust_error <= THESIS_THRUST_TOLERANCE_FRACTION
        and result_finite
        and torque_nm > 0.0
        and FAILURE_NEGATIVE_OR_NONFINITE_LOADING not in failure_reasons
    )
    valid_for_bemt_summary = False
    thesis_blocking_reasons = {
        FAILURE_CLIPPING_FRACTION,
        FAILURE_ALPHA_LIMIT_FRACTION,
        FAILURE_TWIST_MANUFACTURABILITY,
        FAILURE_NEGATIVE_OR_NONFINITE_LOADING,
        FAILURE_TARGET_INFEASIBLE_LOCKED,
        FAILURE_THRUST_TARGET_NOT_MET,
        FAILURE_AIRFOIL_SCHEDULE_INVALID,
    }
    valid_for_thesis_export = (
        valid_for_optimizer_summary
        and not any(reason in thesis_blocking_reasons for reason in failure_reasons)
    )
    return valid_for_optimizer_summary, valid_for_bemt_summary, valid_for_thesis_export


def alpha_match_constraints_pass(result: DesignResult) -> bool:
    return (
        result.cl_limit_fraction_pct <= result.config.max_cl_limit_fraction_pct
        and result.alpha_limit_fraction_pct <= result.config.max_alpha_limit_fraction_pct
        and result.negative_loading_fraction_pct <= 0.0
        and result.nonfinite_loading_fraction_pct <= 0.0
        and (not twist_slope_constraints_active(result.config) or result.manufacturable_twist_pass)
        and airfoil_schedule_valid(result.config, result.stations)
        and math.isfinite(result.thrust_n)
        and math.isfinite(result.torque_nm)
        and result.torque_nm > 0.0
    )


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
    forced_failure_reasons: Sequence[str] = (),
) -> DesignResult:
    omega = rpm_to_omega(config.rpm)
    n_rev_per_sec = config.rpm / 60.0
    diameter_m = 2.0 * config.radius_m
    mu_values = [station.mu for station in reference_stations]
    dr_values = annulus_widths(mu_values, config.hub_radius_fraction, config.radius_m)
    target_weights = normalized_section_weights(
        mu_values,
        dr_values,
        config.spanload_type,
        config.hub_radius_fraction,
        config.gmt_aggressiveness,
    )
    density_factors = local_inflow_density_factors(
        mu_values,
        config.spanload_type,
        config.hub_radius_fraction,
        config.gmt_aggressiveness,
    )
    reference_weights = normalized_reference_loading(reference_stations, dr_values)
    loading_scale = max(parameters.loading_scale, 0.05)
    induced_velocity = disk_induced_velocity(
        config.thrust_target_n,
        config.rho,
        config.radius_m,
        config.hub_radius_fraction,
        config.freestream_mps,
    )

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
        raw_twists.append(clamp(twist_deg, config.twist_min_deg, config.twist_max_deg))
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
            clamp(value, config.twist_min_deg, config.twist_max_deg)
            for value in smooth_series(raw_twists, config.smooth_passes)
        ]
    if config.manufacturable_twist and config.mode != "reproduce_reference":
        smoothed_twists = enforce_manufacturable_twist_profile(mu_values, smoothed_twists, config)
        capped_twists: List[float] = []
        for station, density_factor, twist_deg, airfoil_name in zip(
            reference_stations,
            density_factors,
            smoothed_twists,
            raw_airfoils,
        ):
            local_vi_multiplier = local_induced_velocity_multiplier(density_factor)
            local_vi = induced_velocity * local_vi_multiplier
            radius_m = station.mu * config.radius_m
            v_axial, v_tan, _, _ = compute_section_velocities(config.freestream_mps, omega, radius_m, local_vi)
            phi_deg = math.degrees(math.atan2(v_axial, max(v_tan, 1.0e-6)))
            airfoil = AIRFOIL_LIBRARY[airfoil_name]
            alpha_for_cl_cap = math.degrees(0.99 * airfoil.cl_max / max(airfoil.cl_alpha_per_rad, 1.0e-9)) + airfoil.alpha_l0_deg
            alpha_ceiling = min(config.alpha_search_max_deg - 1.0, alpha_for_cl_cap)
            alpha_floor = (
                -0.5
                if config.spanload_type in {"gmt_bell", "paper_inspired", "reference_calibrated_gmt"} and station.mu >= 0.80
                else 0.25 if station.mu >= 0.85 else config.alpha_search_min_deg + 0.5
            )
            capped_twists.append(clamp(twist_deg, phi_deg + alpha_floor, phi_deg + alpha_ceiling))
        smoothed_twists = enforce_manufacturable_twist_profile(mu_values, capped_twists, config)
        capped_twists = []
        for station, density_factor, twist_deg, airfoil_name in zip(
            reference_stations,
            density_factors,
            smoothed_twists,
            raw_airfoils,
        ):
            local_vi_multiplier = local_induced_velocity_multiplier(density_factor)
            local_vi = induced_velocity * local_vi_multiplier
            radius_m = station.mu * config.radius_m
            v_axial, v_tan, _, _ = compute_section_velocities(config.freestream_mps, omega, radius_m, local_vi)
            phi_deg = math.degrees(math.atan2(v_axial, max(v_tan, 1.0e-6)))
            airfoil = AIRFOIL_LIBRARY[airfoil_name]
            alpha_for_cl_cap = math.degrees(0.99 * airfoil.cl_max / max(airfoil.cl_alpha_per_rad, 1.0e-9)) + airfoil.alpha_l0_deg
            alpha_ceiling = min(config.alpha_search_max_deg - 1.0, alpha_for_cl_cap)
            alpha_floor = (
                -0.5
                if config.spanload_type in {"gmt_bell", "paper_inspired", "reference_calibrated_gmt"} and station.mu >= 0.80
                else 0.25 if station.mu >= 0.85 else config.alpha_search_min_deg + 0.5
            )
            capped_twists.append(clamp(twist_deg, phi_deg + alpha_floor, phi_deg + alpha_ceiling))
        smoothed_twists = capped_twists
    smoothed_twists = enforce_positive_tip_loading_twist_profile(
        mu_values,
        smoothed_twists,
        config,
        reference_stations,
        density_factors,
        raw_airfoils,
        induced_velocity,
        omega,
    )

    stations: List[StationResult] = []
    thrust_n = 0.0
    torque_nm = 0.0
    for idx, (station, dr_m, target_weight, density_factor, chord_m, twist_deg, airfoil_name) in enumerate(
        zip(reference_stations, dr_values, target_weights, density_factors, smoothed_chords, smoothed_twists, raw_airfoils),
        start=1,
    ):
        local_vi_multiplier = local_induced_velocity_multiplier(density_factor)
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
                unclipped_cl_demand=aero.unclipped_cl_demand,
                cl=aero.cl,
                cl_max=aero.cl_max,
                cl_margin=aero.cl_margin,
                cd=aero.cd,
                reynolds_number=aero.reynolds_number,
                alpha_stall_deg=aero.alpha_stall_deg,
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
                is_clipped=aero.hit_cl_limit,
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
    disk_area = actuator_disk_area(config.radius_m)
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
    beta_values = [station.twist_deg for station in stations]
    cl_values = [station.cl for station in stations]
    mu_station_values = [station.mu for station in stations]
    twist_curvature_smooth_penalty = squared_second_difference_penalty(beta_values, 1.5)
    cl_curvature_smooth_penalty = squared_second_difference_penalty(cl_values, 0.08)
    twist_slope_excess_penalty = interval_slope_excess_penalty(
        mu_station_values,
        beta_values,
        config.max_abs_twist_gradient_deg_per_mu_limit,
    )

    count = max(len(stations), 1)
    chord_limit_fraction = sum(station.hit_chord_min or station.hit_chord_max for station in stations) / count
    alpha_limit_fraction = sum(station.hit_alpha_min or station.hit_alpha_max for station in stations) / count
    cl_limit_fraction = sum(station.hit_cl_limit for station in stations) / count
    negative_force_fraction = sum(station.negative_force for station in stations) / count
    impossible_force_fraction = sum(station.impossible_force for station in stations) / count
    high_alpha_threshold = config.alpha_search_max_deg - 1.0
    high_alpha_fraction = sum(station.alpha_deg >= high_alpha_threshold for station in stations) / count
    polar_range_fraction = sum(
        station.hit_alpha_min or station.hit_alpha_max or station.hit_cl_limit
        for station in stations
    ) / count
    bounds_penalty = (
        1.5 * chord_limit_fraction
        + 5.0 * alpha_limit_fraction
        + 7.5 * cl_limit_fraction
        + 4.0 * high_alpha_fraction
        + 3.0 * negative_force_fraction
        + 8.0 * impossible_force_fraction
    )
    if torque_nm <= 0.0:
        bounds_penalty += 10.0

    positive_loading_penalty = 0.0
    if stations:
        thrust_scale = max(abs(config.thrust_target_n) / len(stations), 1.0e-6)
        torque_scale = max(abs(q_reference) / len(stations), 1.0e-6)
        for station in stations:
            positive_loading_penalty += (max(0.0, -station.cl) / 0.02) ** 2
            positive_loading_penalty += (max(0.0, -station.dthrust_n) / thrust_scale) ** 2
            positive_loading_penalty += (max(0.0, -station.dtorque_nm) / torque_scale) ** 2
        running_thrust = 0.0
        running_torque = 0.0
        for station in stations[:-1]:
            running_thrust += station.dthrust_n
            running_torque += station.dtorque_nm
            positive_loading_penalty += (max(0.0, safe_div(running_thrust, thrust_n) - 1.001) / 0.01) ** 2
            positive_loading_penalty += (max(0.0, safe_div(running_torque, torque_nm) - 1.001) / 0.01) ** 2
        positive_loading_penalty /= len(stations)

    tip_positive_unload_penalty = 0.0
    average_dthrust = max(abs(config.thrust_target_n) / max(len(stations), 1), 1.0e-6)
    average_dtorque = max(abs(q_reference) / max(len(stations), 1), 1.0e-6)
    tip_stations = [station for station in stations if station.mu > 0.85]
    if tip_stations:
        for station in tip_stations:
            normalized_tip = clamp((station.mu - 0.85) / 0.15, 0.0, 1.0)
            cl_upper_target = 0.08 + 0.30 * (1.0 - normalized_tip) ** 1.5
            alpha_floor_deg = 0.25 + 0.75 * normalized_tip
            tip_positive_unload_penalty += (max(0.0, 0.02 - station.cl) / 0.02) ** 2
            tip_positive_unload_penalty += (max(0.0, station.cl - cl_upper_target) / max(cl_upper_target, 1.0e-6)) ** 2
            tip_positive_unload_penalty += (max(0.0, -station.dthrust_n) / average_dthrust) ** 2
            tip_positive_unload_penalty += (max(0.0, -station.dtorque_nm) / average_dtorque) ** 2
            tip_positive_unload_penalty += (max(0.0, alpha_floor_deg - station.alpha_deg) / 1.0) ** 2
        tip_positive_unload_penalty /= len(tip_stations)
    tip_cl_target_penalty = 0.0
    tip_target_stations = [station for station in stations if station.mu >= TIP_UNLOAD_START_MU]
    if tip_target_stations:
        for station in tip_target_stations:
            target_cl = gmt_tip_target_cl(station.mu)
            tip_cl_target_penalty += ((station.cl - target_cl) / max(0.08, target_cl)) ** 2
            tip_cl_target_penalty += (max(0.0, -station.cl) / 0.01) ** 2
            tip_cl_target_penalty += (max(0.0, -station.dthrust_n) / average_dthrust) ** 2
        tip_cl_target_penalty /= len(tip_target_stations)

    twist_metrics = design_twist_metrics(config, stations)
    twist_bump_limit = max(config.max_positive_twist_bump_deg, 1.0e-6)
    twist_bump_penalty = (
        max(0.0, twist_metrics["max_positive_twist_bump_deg"] - config.max_positive_twist_bump_deg)
        / twist_bump_limit
    ) ** 2
    twist_gradient_penalty = (
        max(
            0.0,
            twist_metrics["max_abs_twist_gradient_deg_per_mu"]
            / max(config.max_abs_twist_gradient_deg_per_mu_limit, 1.0e-6)
            - 1.0,
        )
        ** 2
    )
    twist_curvature_penalty = (
        max(0.0, twist_metrics["max_twist_curvature"] / max(config.max_twist_curvature_limit, 1.0e-6) - 1.0)
        ** 2
    )
    aero_limit_penalty = (
        (cl_limit_fraction / max(config.max_cl_limit_fraction_pct / 100.0, 1.0e-6)) ** 2
        + (alpha_limit_fraction / max(config.max_alpha_limit_fraction_pct / 100.0, 1.0e-6)) ** 2
        + (high_alpha_fraction / max(config.max_alpha_limit_fraction_pct / 100.0, 1.0e-6)) ** 2
        + 4.0 * polar_range_fraction
    )
    validity_metrics = station_validity_metrics(config, stations, twist_metrics)
    result_values = [
        thrust_n,
        torque_nm,
        power_w,
        ct,
        cq,
        cp,
        spanload_error,
        normalized_thrust_error,
        normalized_torque,
    ]
    result_finite = all(math.isfinite(value) for value in result_values)
    failure_reasons = validity_failure_reasons_from_metrics(
        config,
        normalized_thrust_error,
        validity_metrics,
        forced_failure_reasons,
    )
    valid_for_optimizer_summary, valid_for_bemt_summary, valid_for_thesis_export = validity_flags_from_metrics(
        result_finite,
        torque_nm,
        normalized_thrust_error,
        failure_reasons,
    )
    cl_threshold = max(config.max_cl_limit_fraction_pct, 1.0e-6)
    alpha_threshold = max(config.max_alpha_limit_fraction_pct, 1.0e-6)
    dominant_validity_penalty_raw = 0.0
    if validity_metrics["cl_limit_fraction_pct"] > config.max_cl_limit_fraction_pct:
        dominant_validity_penalty_raw += (
            1.0
            + (
                (float(validity_metrics["cl_limit_fraction_pct"]) - config.max_cl_limit_fraction_pct)
                / cl_threshold
            )
            ** 2
        )
    if validity_metrics["alpha_limit_fraction_pct"] > config.max_alpha_limit_fraction_pct:
        dominant_validity_penalty_raw += (
            1.0
            + (
                (float(validity_metrics["alpha_limit_fraction_pct"]) - config.max_alpha_limit_fraction_pct)
                / alpha_threshold
            )
            ** 2
        )
    if (
        float(validity_metrics["negative_loading_fraction_pct"]) > 0.0
        or float(validity_metrics["nonfinite_loading_fraction_pct"]) > 0.0
        or not result_finite
    ):
        dominant_validity_penalty_raw += (
            1.0
            + float(validity_metrics["negative_loading_fraction_pct"]) / 100.0
            + float(validity_metrics["nonfinite_loading_fraction_pct"]) / 100.0
        )
    if twist_slope_constraints_active(config) and not twist_metrics["manufacturable_twist_pass"]:
        dominant_validity_penalty_raw += 1.0 + twist_bump_penalty + twist_gradient_penalty + twist_curvature_penalty
    if normalized_thrust_error > THESIS_THRUST_TOLERANCE_FRACTION:
        excess = (normalized_thrust_error - THESIS_THRUST_TOLERANCE_FRACTION) / THESIS_THRUST_TOLERANCE_FRACTION
        dominant_validity_penalty_raw += 1.0 + excess * excess
    if not bool(validity_metrics.get("airfoil_schedule_valid", True)):
        dominant_validity_penalty_raw += 1.0
    twist_bump_weight = weights.w_twist_bump or (90.0 if twist_slope_constraints_active(config) else 0.0)
    twist_gradient_weight = weights.w_twist_gradient or (35.0 if twist_slope_constraints_active(config) else 0.0)
    twist_curvature_weight = weights.w_twist_curvature or (22.0 if twist_slope_constraints_active(config) else 0.0)
    aero_limit_weight = weights.w_aero_limits or (75.0 if config.manufacturable_twist else 18.0)
    torque_weight = weights.w_torque
    if twist_slope_constraints_active(config) and config.spanload_type != "minimum_induced_loss":
        torque_weight = max(torque_weight, 4.0)

    reference_twist_error = reference_twist_rms_deg / 10.0
    reference_chord_error = reference_chord_rms_fraction / 0.05
    objective_terms = {
        "normalized_thrust_error": weights.w_thrust * normalized_thrust_error,
        "normalized_torque": torque_weight * normalized_torque,
        "spanload_shape_error": weights.w_spanload * spanload_error,
        "reference_twist_error": weights.w_reference_twist * reference_twist_error,
        "reference_chord_error": weights.w_reference_chord * reference_chord_error,
        "smoothness_penalty": weights.w_smooth * smoothness_penalty,
        "bounds_penalty": weights.w_bounds * bounds_penalty,
        "positive_loading_penalty": weights.w_positive_loading * positive_loading_penalty,
        "tip_positive_unload_penalty": weights.w_tip_positive_unload * tip_positive_unload_penalty,
        "twist_bump_penalty": twist_bump_weight * twist_bump_penalty,
        "twist_gradient_penalty": twist_gradient_weight * twist_gradient_penalty,
        "twist_curvature_penalty": twist_curvature_weight * twist_curvature_penalty,
        "twist_slope_excess_penalty": twist_gradient_weight * twist_slope_excess_penalty,
        "twist_curvature_smooth_penalty": weights.w_twist_curvature_smooth * twist_curvature_smooth_penalty,
        "cl_curvature_smooth_penalty": weights.w_cl_curvature_smooth * cl_curvature_smooth_penalty,
        "tip_cl_target_penalty": weights.w_tip_cl_target * tip_cl_target_penalty,
        "aero_limit_penalty": aero_limit_weight * aero_limit_penalty,
        "dominant_validity_penalty": DOMINANT_VALIDITY_PENALTY * dominant_validity_penalty_raw,
    }
    objective = sum(objective_terms.values())

    warnings: List[str] = list(initial_warnings)
    if FAILURE_TARGET_INFEASIBLE_LOCKED in failure_reasons:
        warnings.append("target thrust is infeasible under locked chord/airfoil/twist-only constraints")
    if normalized_thrust_error > 0.05:
        warnings.append(f"target thrust mismatch is {100.0 * normalized_thrust_error:.1f}%")
    if chord_limit_fraction > 0.0:
        warnings.append(f"{100.0 * chord_limit_fraction:.1f}% of stations hit chord limits")
    if alpha_limit_fraction > config.max_alpha_limit_fraction_pct / 100.0:
        warnings.append(f"{100.0 * alpha_limit_fraction:.1f}% of stations hit alpha limits")
    if cl_limit_fraction > config.max_cl_limit_fraction_pct / 100.0:
        warnings.append(f"{100.0 * cl_limit_fraction:.1f}% of stations are clipped at reduced-order Cl limits")
    if not twist_metrics["manufacturable_twist_pass"]:
        warnings.append(
            "twist manufacturability check failed "
            f"(max bump {twist_metrics['max_positive_twist_bump_deg']:.3f} deg, "
            f"max |d beta/dmu| {twist_metrics['max_abs_twist_gradient_deg_per_mu']:.1f}, "
            f"max curvature {twist_metrics['max_twist_curvature']:.1f})"
        )
    if negative_force_fraction > 0.0:
        warnings.append(f"{100.0 * negative_force_fraction:.1f}% of stations produced negative thrust or torque contributions")
    if abs(config.freestream_mps) <= 1.0e-9 and any(station.dthrust_n < -1.0e-9 for station in stations):
        warnings.append("hover solution has at least one station with negative dT; tip unloading must remain positive")
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
        cl_limit_fraction_pct=float(validity_metrics["cl_limit_fraction_pct"]),
        alpha_limit_fraction_pct=float(validity_metrics["alpha_limit_fraction_pct"]),
        high_alpha_fraction_pct=float(validity_metrics["high_alpha_fraction_pct"]),
        negative_loading_fraction_pct=float(validity_metrics["negative_loading_fraction_pct"]),
        nonfinite_loading_fraction_pct=float(validity_metrics["nonfinite_loading_fraction_pct"]),
        manufacturable_twist_pass=bool(validity_metrics["manufacturable_twist_pass"]),
        valid_for_optimizer_summary=valid_for_optimizer_summary,
        valid_for_bemt_summary=valid_for_bemt_summary,
        valid_for_thesis_export=valid_for_thesis_export,
        failure_reason=primary_failure_reason(failure_reasons),
        failure_reasons=tuple(failure_reasons),
        aero_calibration_used=aero_calibration_used_for_config(config),
        calibration_mode=calibration_mode_for_config(config),
        requested_gmt_aggressiveness=config.gmt_aggressiveness,
        achieved_gmt_aggressiveness=config.gmt_aggressiveness,
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
    if config.manufacturable_twist:
        return {
            "base_alpha_deg": (-18.0, 10.0),
            "twist_bias_inner": (-6.0, 6.0),
            "twist_bias_outer": (-12.0, 12.0),
            "twist_curvature": (-8.0, 8.0),
            "tip_unload_strength": (0.0, 12.0),
            "loading_scale": (0.75, 1.35),
            "chord_scale": (1.0, 1.0) if config.lock_chord or config.optimize_twist_only else (0.90, 1.65),
        }
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


def alpha_sweep_values(bounds: tuple[float, float], step_deg: float, center_deg: float) -> List[float]:
    lower, upper = bounds
    step = max(abs(step_deg), 0.5)
    values = [lower, upper, clamp(center_deg, lower, upper)]
    value = lower
    while value <= upper + 1.0e-9:
        values.append(value)
        value += step
    rounded = sorted({round(clamp(value, lower, upper), 6) for value in values})
    return rounded


def alpha_match_invalid_key(result: DesignResult) -> tuple[float, float, float, float, float, float]:
    cl_excess = max(0.0, result.cl_limit_fraction_pct - result.config.max_cl_limit_fraction_pct)
    alpha_excess = max(0.0, result.alpha_limit_fraction_pct - result.config.max_alpha_limit_fraction_pct)
    twist_fail = 0.0 if (not twist_slope_constraints_active(result.config) or result.manufacturable_twist_pass) else 1.0
    loading_fail = result.negative_loading_fraction_pct + result.nonfinite_loading_fraction_pct
    return (
        cl_excess + alpha_excess + 100.0 * twist_fail + loading_fail,
        result.normalized_thrust_error,
        max(result.torque_nm, 0.0),
        result.spanload_error,
        result.cl_limit_fraction_pct,
        result.alpha_limit_fraction_pct,
    )


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
) -> AlphaMatchResult:
    if config.mode == "reproduce_reference":
        return AlphaMatchResult(
            parameters=parameters,
            selected_result=None,
            valid_candidate_found=True,
            target_met_with_valid_candidate=True,
            selected_candidate_valid=True,
        )

    bounds = parameter_bounds(config)["base_alpha_deg"]
    candidates: List[DesignResult] = []
    for alpha_value in alpha_sweep_values(bounds, config.alpha_search_step_deg, parameters.base_alpha_deg):
        trial_params = with_parameter(parameters, "base_alpha_deg", alpha_value)
        candidates.append(
            build_candidate_result(
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
        )

    valid_candidates = [candidate for candidate in candidates if alpha_match_constraints_pass(candidate)]
    if valid_candidates:
        if config.mode == "gmt_requirements":
            target_valid_candidates = [
                candidate
                for candidate in valid_candidates
                if candidate.normalized_thrust_error <= THESIS_THRUST_TOLERANCE_FRACTION
            ]
            if target_valid_candidates:
                selected = min(
                    target_valid_candidates,
                    key=lambda candidate: (
                        candidate.normalized_thrust_error,
                        candidate.torque_nm,
                        candidate.spanload_error,
                    ),
                )
            else:
                selected = min(
                    valid_candidates,
                    key=lambda candidate: (
                        candidate.normalized_thrust_error,
                        candidate.torque_nm,
                        candidate.spanload_error,
                    ),
                )
        else:
            selected = min(
                valid_candidates,
                key=lambda candidate: (
                    candidate.normalized_thrust_error,
                    candidate.torque_nm,
                    candidate.spanload_error,
                ),
            )
        target_met = selected.normalized_thrust_error <= THESIS_THRUST_TOLERANCE_FRACTION
        forced_reasons: List[str] = []
        if not target_met and locked_thesis_geometry(config):
            forced_reasons.append(FAILURE_TARGET_INFEASIBLE_LOCKED)
        return AlphaMatchResult(
            parameters=selected.parameters,
            selected_result=selected,
            valid_candidate_found=True,
            target_met_with_valid_candidate=target_met,
            selected_candidate_valid=True,
            failure_reasons=tuple(forced_reasons),
        )

    selected = min(candidates, key=alpha_match_invalid_key)
    forced_reasons = list(selected.failure_reasons)
    if locked_thesis_geometry(config):
        forced_reasons.append(FAILURE_TARGET_INFEASIBLE_LOCKED)
    return AlphaMatchResult(
        parameters=selected.parameters,
        selected_result=selected,
        valid_candidate_found=False,
        target_met_with_valid_candidate=False,
        selected_candidate_valid=False,
        failure_reasons=tuple(unique_failure_reasons(forced_reasons)),
    )


def initial_parameters(config: DesignInputs) -> OptimizationParameters:
    if config.manufacturable_twist:
        return OptimizationParameters(
            base_alpha_deg=0.0,
            twist_bias_inner=0.0,
            twist_bias_outer=-2.0,
            twist_curvature=0.0,
            tip_unload_strength=1.0,
            loading_scale=1.0,
            chord_scale=1.20,
        )
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
    alpha_match = match_thrust_with_base_alpha(
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
    parameters = alpha_match.parameters
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
        alpha_match.failure_reasons,
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
    if config.manufacturable_twist:
        steps.update(
            {
                "twist_bias_inner": 1.0,
                "twist_bias_outer": 2.0,
                "twist_curvature": 2.0,
                "tip_unload_strength": 1.5,
                "loading_scale": 0.08,
                "chord_scale": 0.12,
            }
        )
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


def clipped_station_ranges(stations: Sequence[StationResult]) -> list[dict[str, object]]:
    ranges: list[dict[str, object]] = []
    current: list[StationResult] = []
    for station in stations:
        if station.is_clipped:
            current.append(station)
        elif current:
            ranges.append(
                {
                    "mu_start": current[0].mu,
                    "mu_end": current[-1].mu,
                    "station_count": len(current),
                }
            )
            current = []
    if current:
        ranges.append(
            {
                "mu_start": current[0].mu,
                "mu_end": current[-1].mu,
                "station_count": len(current),
            }
        )
    return ranges


def cl_clipping_diagnostics(result: DesignResult) -> dict[str, object]:
    clipped = [station for station in result.stations if station.is_clipped]
    count = len(clipped)
    total = len(result.stations)
    return {
        "clipped_station_count": count,
        "clipped_fraction": safe_div(count, total),
        "clipped_mu_min": min((station.mu for station in clipped), default=None),
        "clipped_mu_max": max((station.mu for station in clipped), default=None),
        "clipped_mu_ranges": clipped_station_ranges(result.stations),
    }


def tip_loading_diagnostics(result: DesignResult, threshold_mu: float = 0.85) -> dict[str, float]:
    target_tip = sum(station.target_spanload_weight for station in result.stations if station.mu >= threshold_mu)
    actual_tip = sum(station.actual_spanload_weight for station in result.stations if station.mu >= threshold_mu)
    return {
        "threshold_mu": threshold_mu,
        "target_tip_loading_fraction": target_tip,
        "actual_tip_loading_fraction": actual_tip,
        "actual_minus_target": actual_tip - target_tip,
        "actual_to_target_ratio": safe_div(actual_tip, target_tip, fallback=1.0e99 if actual_tip > 0.0 else 0.0),
    }


def loading_fraction_diagnostics(
    result: DesignResult,
    tip_threshold_mu: float = 0.85,
    inboard_min_mu: float = 0.20,
    inboard_max_mu: float = 0.72,
) -> dict[str, float]:
    target_tip = sum(station.target_spanload_weight for station in result.stations if station.mu >= tip_threshold_mu)
    actual_tip = sum(station.actual_spanload_weight for station in result.stations if station.mu >= tip_threshold_mu)
    target_inboard = sum(
        station.target_spanload_weight for station in result.stations if inboard_min_mu <= station.mu <= inboard_max_mu
    )
    actual_inboard = sum(
        station.actual_spanload_weight for station in result.stations if inboard_min_mu <= station.mu <= inboard_max_mu
    )
    return {
        "tip_threshold_mu": tip_threshold_mu,
        "inboard_min_mu": inboard_min_mu,
        "inboard_max_mu": inboard_max_mu,
        "target_tip_loading_fraction": target_tip,
        "actual_tip_loading_fraction": actual_tip,
        "target_inboard_loading_fraction": target_inboard,
        "actual_inboard_loading_fraction": actual_inboard,
    }


def negative_loading_station_count(result: DesignResult) -> int:
    return sum(station.negative_force or station.impossible_force for station in result.stations)


def recommended_next_actions(result: DesignResult, failure_reasons: Sequence[str]) -> List[str]:
    if not failure_reasons:
        return [RECOMMEND_GEOMETRY_VALID]

    actions: List[str] = []
    if FAILURE_TARGET_INFEASIBLE_LOCKED in failure_reasons:
        if result.config.lock_chord or result.config.optimize_twist_only:
            actions.append(RECOMMEND_ALLOW_CHORD)
        if result.config.lock_airfoil or result.config.optimize_twist_only:
            actions.append(RECOMMEND_ALLOW_AIRFOIL)
        actions.extend([RECOMMEND_INCREASE_RPM, RECOMMEND_REDUCE_THRUST])
    elif FAILURE_THRUST_TARGET_NOT_MET in failure_reasons:
        if result.config.lock_chord or result.config.optimize_twist_only:
            actions.append(RECOMMEND_ALLOW_CHORD)
        else:
            actions.append(RECOMMEND_INCREASE_RPM)
        actions.append(RECOMMEND_REDUCE_THRUST)

    if FAILURE_CLIPPING_FRACTION in failure_reasons:
        if result.config.lock_airfoil or result.config.optimize_twist_only:
            actions.append(RECOMMEND_ALLOW_AIRFOIL)
        else:
            actions.append(RECOMMEND_EXPAND_POLARS)
        actions.append(RECOMMEND_REDUCE_GMT)
    if FAILURE_ALPHA_LIMIT_FRACTION in failure_reasons:
        actions.append(RECOMMEND_REDUCE_GMT)
    if FAILURE_NEGATIVE_OR_NONFINITE_LOADING in failure_reasons:
        actions.append(RECOMMEND_REDUCE_GMT)
    if FAILURE_TWIST_MANUFACTURABILITY in failure_reasons:
        actions.append(RECOMMEND_REDUCE_GMT)
    if FAILURE_AIRFOIL_SCHEDULE_INVALID in failure_reasons:
        actions.append(RECOMMEND_ALLOW_AIRFOIL)

    return unique_failure_reasons(actions) or [RECOMMEND_REDUCE_THRUST]


def validity_status_label(valid_for_thesis_export: bool, failure_reasons: Sequence[str]) -> str:
    if valid_for_thesis_export:
        return "thesis_valid"
    if FAILURE_TARGET_INFEASIBLE_LOCKED in failure_reasons:
        return FAILURE_TARGET_INFEASIBLE_LOCKED
    if FAILURE_THRUST_TARGET_NOT_MET in failure_reasons:
        return "target_thrust_infeasible_under_current_constraints"
    return "geometry_checks_not_met"


def convergence_diagnostics(result: DesignResult) -> dict[str, object]:
    history = list(result.convergence_history)
    if not history:
        return {
            "best_iteration": None,
            "last_iteration": None,
            "best_objective": result.objective,
            "last_objective": None,
            "reported_solution_source": "result_without_history",
        }
    best_record = min(history, key=lambda record: record.get("objective", math.inf))
    last_record = history[-1]
    best_objective = best_record.get("objective", result.objective)
    last_objective = last_record.get("objective")
    objective_tolerance = max(1.0e-10, 1.0e-8 * max(abs(result.objective), 1.0))
    if abs(result.objective - best_objective) <= objective_tolerance:
        source = "best_seen"
    elif last_objective is not None and abs(result.objective - last_objective) <= objective_tolerance:
        source = "last_iteration"
    else:
        source = "reported_result_not_in_history"
    return {
        "best_iteration": int(best_record.get("iteration", 0)),
        "last_iteration": int(last_record.get("iteration", 0)),
        "best_objective": best_objective,
        "last_objective": last_objective,
        "reported_solution_source": source,
    }


def validate_geometry_result(result: DesignResult) -> List[str]:
    warnings: List[str] = []
    stations = list(result.stations)
    for previous, current in zip(stations, stations[1:]):
        if current.mu <= previous.mu:
            warnings.append("mu stations are not strictly increasing after duplicate cleaning")
            break
    if any(station.chord_m <= 0.0 for station in stations):
        warnings.append("one or more stations have non-positive chord")
    if any(
        station.twist_deg < result.config.twist_min_deg or station.twist_deg > result.config.twist_max_deg
        for station in stations
    ):
        warnings.append(
            "one or more stations have twist outside the configured "
            f"{result.config.twist_min_deg:g} to {result.config.twist_max_deg:g} deg physical check range"
        )
    if any(station.alpha_deg < -5.0 or station.alpha_deg > 25.0 for station in stations):
        warnings.append("one or more stations have alpha outside the -5 to 25 deg sanity check range")
    if any(station.cl < -0.02 for station in stations):
        warnings.append("one or more stations have Cl < -0.02; tip unloading must approach zero from positive lift")
    if any(station.dthrust_n < -1.0e-6 for station in stations):
        warnings.append("one or more stations have negative thrust contribution")
    if abs(result.config.freestream_mps) <= 1.0e-9 and any(station.dthrust_n < -1.0e-9 for station in stations):
        warnings.append("hover solution has negative dT at one or more stations")
    if any(station.dtorque_nm < -1.0e-6 for station in stations):
        warnings.append("one or more stations have negative torque contribution")
    tip_alpha_negative = [station for station in stations if station.mu > 0.85 and station.alpha_deg < -1.0]
    if tip_alpha_negative:
        warnings.append("one or more tip stations have alpha < -1 deg")
    cumulative_thrust = 0.0
    cumulative_torque = 0.0
    for station in stations[:-1]:
        cumulative_thrust += station.dthrust_n
        cumulative_torque += station.dtorque_nm
        if safe_div(cumulative_thrust, result.thrust_n) > 1.02:
            warnings.append("cumulative thrust fraction exceeds 1.02 before the final station")
            break
    cumulative_torque = 0.0
    for station in stations[:-1]:
        cumulative_torque += station.dtorque_nm
        if safe_div(cumulative_torque, result.torque_nm) > 1.02:
            warnings.append("cumulative torque fraction exceeds 1.02 before the final station")
            break
    cl_clip_fraction = sum(station.hit_cl_limit for station in stations) / max(len(stations), 1)
    if cl_clip_fraction > result.config.max_cl_limit_fraction_pct / 100.0:
        warnings.append(f"{100.0 * cl_clip_fraction:.1f}% of stations are at the reduced-order Cl limit")
    alpha_clip_fraction = sum(station.hit_alpha_min or station.hit_alpha_max for station in stations) / max(len(stations), 1)
    if alpha_clip_fraction > result.config.max_alpha_limit_fraction_pct / 100.0:
        warnings.append(f"{100.0 * alpha_clip_fraction:.1f}% of stations are outside the alpha search range")
    twist_metrics = design_twist_metrics(result.config, stations)
    if not twist_metrics["manufacturable_twist_pass"]:
        warnings.append(
            "twist profile check: "
            f"max positive bump {twist_metrics['max_positive_twist_bump_deg']:.3f} deg, "
            f"max |d beta/dmu| {twist_metrics['max_abs_twist_gradient_deg_per_mu']:.1f}, "
            f"max curvature {twist_metrics['max_twist_curvature']:.1f}"
        )
    tip_diagnostics = tip_loading_diagnostics(result)
    if (
        tip_diagnostics["actual_minus_target"] > 0.05
        and tip_diagnostics["actual_to_target_ratio"] > 1.5
    ):
        warnings.append(
            "actual integrated tip loading from r/R >= "
            f"{tip_diagnostics['threshold_mu']:.2f} is {tip_diagnostics['actual_tip_loading_fraction']:.3f} "
            f"of thrust vs target {tip_diagnostics['target_tip_loading_fraction']:.3f}; "
            "do not claim ideal GMT tip unloading for this run"
        )
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


def _optimize_geometry_once(
    config: DesignInputs,
    reference_stations: Sequence[ReferenceStation] | None = None,
    initial_warnings: Sequence[str] = (),
    reference_source: str = "unknown reference",
) -> DesignResult:
    if reference_stations is None:
        reference_stations = builtin_reference_stations()
        reference_source = "built-in reference geometry"
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

    if config.mode == "reproduce_reference":
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

    if config.max_iterations == 0:
        result = evaluate_with_matched_alpha(
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
            scipy_unavailable = "scipy.optimize is unavailable" in str(exc) or "No module named 'scipy'" in str(exc)
            if config.optimizer == "scipy" and not scipy_unavailable:
                raise
            fallback_warning = (
                "scipy optimizer unavailable or failed; used coordinate search instead "
                f"({exc}). Install with: python -m pip install scipy"
            )
            result, history = optimize_with_coordinate_search(
                config,
                reference_stations,
                weights,
                q_reference,
                reference_source,
                calibration_warnings + [fallback_warning],
                aero_calibration_scale,
            )
            result = replace(
                result,
                optimizer_fallback_used=True,
                optimizer_fallback_reason=str(exc),
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


def gmt_aggressiveness_candidates(requested: float) -> List[float]:
    requested = clamp(requested, 0.0, 1.0)
    values = [requested, 0.85, 0.70, 0.55, 0.40, 0.25, 0.10, 0.0]
    unique = sorted({round(value, 6) for value in values if value <= requested + 1.0e-9}, reverse=True)
    return unique or [0.0]


def gmt_requirements_rank(result: DesignResult) -> tuple[float, float, float, float, float, float]:
    valid_rank = 0.0 if result.valid_for_thesis_export else 1.0
    constraint_excess = (
        max(0.0, result.cl_limit_fraction_pct - result.config.max_cl_limit_fraction_pct)
        + max(0.0, result.alpha_limit_fraction_pct - result.config.max_alpha_limit_fraction_pct)
        + result.negative_loading_fraction_pct
        + result.nonfinite_loading_fraction_pct
        + (0.0 if result.manufacturable_twist_pass else 100.0)
    )
    return (
        valid_rank,
        constraint_excess,
        result.normalized_thrust_error,
        result.torque_nm,
        result.spanload_error,
        -result.achieved_gmt_aggressiveness,
    )


def optimize_gmt_requirements(
    config: DesignInputs,
    reference_stations: Sequence[ReferenceStation] | None = None,
    initial_warnings: Sequence[str] = (),
    reference_source: str = "unknown reference",
) -> DesignResult:
    requested_aggressiveness = clamp(config.gmt_aggressiveness, 0.0, 1.0)
    attempts: List[DesignResult] = []
    for aggressiveness in gmt_aggressiveness_candidates(requested_aggressiveness):
        attempt_config = replace(config, gmt_aggressiveness=aggressiveness)
        attempt = _optimize_geometry_once(attempt_config, reference_stations, initial_warnings, reference_source)
        attempt = replace(
            attempt,
            requested_gmt_aggressiveness=requested_aggressiveness,
            achieved_gmt_aggressiveness=aggressiveness,
        )
        attempts.append(attempt)
        if attempt.valid_for_thesis_export:
            warnings = list(attempt.warnings)
            if aggressiveness < requested_aggressiveness - 1.0e-9:
                warnings.append(
                    "reduced GMT aggressiveness from "
                    f"{requested_aggressiveness:.3f} to {aggressiveness:.3f} to recover thesis validity"
                )
            return replace(attempt, warnings=dedupe_warnings(warnings))

    best = min(attempts, key=gmt_requirements_rank)
    warnings = list(best.warnings)
    if best.achieved_gmt_aggressiveness < requested_aggressiveness - 1.0e-9:
        warnings.append(
            "GMT requirements solve could not satisfy all hard validity checks even after reducing "
            f"aggressiveness to {best.achieved_gmt_aggressiveness:.3f}"
        )
    return replace(best, warnings=dedupe_warnings(warnings))


def optimize_geometry(
    config: DesignInputs,
    reference_stations: Sequence[ReferenceStation] | None = None,
    initial_warnings: Sequence[str] = (),
    reference_source: str = "unknown reference",
) -> DesignResult:
    if config.mode == "gmt_requirements":
        return optimize_gmt_requirements(config, reference_stations, initial_warnings, reference_source)
    return _optimize_geometry_once(config, reference_stations, initial_warnings, reference_source)


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


def solve_geometry_for_loading(
    config: DesignInputs,
    loading_scale: float,
    reference_stations: Sequence[ReferenceStation] | None = None,
) -> DesignResult:
    validate_user_inputs(config)
    if reference_stations is None:
        reference_stations = builtin_reference_stations()
    return build_geometry(config, reference_stations, base_alpha_deg=0.0, loading_scale=loading_scale)


def solve_geometry(
    config: DesignInputs,
    reference_stations: Sequence[ReferenceStation] | None = None,
) -> DesignResult:
    validate_user_inputs(config)
    if reference_stations is None:
        reference_stations = builtin_reference_stations()
    return optimize_geometry(config, reference_stations, reference_source="built-in reference geometry")


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


def _assert_output_path(output_path: Path, output_domain: str) -> None:
    if output_domain == "geometry":
        assert_geometry_output_path(output_path)
    elif output_domain == "verification":
        assert_verification_output_path(output_path)
    else:
        raise ValueError(f"unknown output domain: {output_domain}")


def _output_case_name(result: DesignResult) -> str:
    return optimized_case_name(result.config.spanload_type, result.config.manufacturable_twist)


def _base_output_metadata(
    *,
    case_name: str,
    geometry_source_csv: str | Path | None = None,
    station_source_csv: str | Path | None = None,
    input_config_path: str | Path | None = None,
    twist_column_used: str | None = None,
    aero_source: str | None = None,
) -> dict[str, Any]:
    return {
        "generating_script": GENERATING_SCRIPT,
        "model_source": MODEL_SOURCE_OPTIMIZER,
        "case_name": case_name,
        "geometry_source_csv": "" if geometry_source_csv is None else str(geometry_source_csv),
        "station_source_csv": "" if station_source_csv is None else str(station_source_csv),
        "input_config_path": "" if input_config_path is None else str(input_config_path),
        "twist_column_used": "" if twist_column_used is None else twist_column_used,
        "aero_source": "" if aero_source is None else aero_source,
    }


def optimizer_geometry_csv_name(case_name: str) -> str:
    return f"{case_name}_{OPTIMIZER_GEOMETRY_SUFFIX}.csv"


def write_csv(result: DesignResult, output_path: Path, *, output_domain: str = "geometry") -> None:
    _assert_output_path(output_path, output_domain)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = geometry_csv_metadata(result.config)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "station",
                "model_source",
                "generating_script",
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
                "Re",
                "alpha_stall_deg",
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
                "unclipped_cl_demand",
                "cl_max",
                "cl_margin",
                "is_clipped",
            ]
        )
        for station in result.stations:
            writer.writerow(
                [
                    station.station_index,
                    MODEL_SOURCE_OPTIMIZER,
                    GENERATING_SCRIPT,
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
                    f"{station.reynolds_number:.0f}",
                    f"{station.alpha_stall_deg:.4f}",
                    f"{station.dthrust_n:.10f}",
                    f"{station.dtorque_nm:.10f}",
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
                    f"{station.unclipped_cl_demand:.5f}",
                    f"{station.cl_max:.5f}",
                    f"{station.cl_margin:.5f}",
                    str(station.is_clipped),
                ]
            )
    write_metadata_sidecar(
        output_path,
        _base_output_metadata(
            case_name=_output_case_name(result),
            geometry_source_csv=output_path,
            station_source_csv=output_path,
            twist_column_used="twist_deg",
            aero_source=MODEL_SOURCE_OPTIMIZER,
        ),
    )


def write_diagnostic_csv(result: DesignResult, output_path: Path, *, output_domain: str = "geometry") -> None:
    _assert_output_path(output_path, output_domain)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    slopes, curvatures = station_twist_derivatives(
        [station.mu for station in result.stations],
        [station.twist_deg for station in result.stations],
    )
    objective_fields = [f"objective_{name}" for name in result.objective_breakdown]
    fields = [
        "station",
        "model_source",
        "generating_script",
        "r/R",
        "chord",
        "twist",
        "alpha",
        "Cl",
        "Cd",
        "Re",
        "airfoil",
        "Cl_max",
        "alpha_stall",
        "target_Cl_tip",
        "dT",
        "dQ",
        "hit_cl_limit",
        "hit_alpha_limit",
        "twist_slope",
        "twist_curvature",
        "total_objective",
        *objective_fields,
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for station, slope, curvature in zip(result.stations, slopes, curvatures):
            row: dict[str, object] = {
                "station": station.station_index,
                "model_source": MODEL_SOURCE_OPTIMIZER,
                "generating_script": GENERATING_SCRIPT,
                "r/R": f"{station.mu:.4f}",
                "chord": f"{station.chord_m:.8f}",
                "twist": f"{station.twist_deg:.6f}",
                "alpha": f"{station.alpha_deg:.6f}",
                "Cl": f"{station.cl:.6f}",
                "Cd": f"{station.cd:.6f}",
                "Re": f"{station.reynolds_number:.0f}",
                "airfoil": station.airfoil,
                "Cl_max": f"{station.cl_max:.6f}",
                "alpha_stall": f"{station.alpha_stall_deg:.6f}",
                "target_Cl_tip": f"{gmt_tip_target_cl(station.mu):.6f}" if station.mu >= TIP_UNLOAD_START_MU else "",
                "dT": f"{station.dthrust_n:.10f}",
                "dQ": f"{station.dtorque_nm:.10f}",
                "hit_cl_limit": station.hit_cl_limit,
                "hit_alpha_limit": station.hit_alpha_min or station.hit_alpha_max,
                "twist_slope": f"{slope:.6f}",
                "twist_curvature": f"{curvature:.6f}",
                "total_objective": f"{result.objective:.10f}",
            }
            for name, value in result.objective_breakdown.items():
                row[f"objective_{name}"] = f"{value:.10f}"
            writer.writerow(row)
    write_metadata_sidecar(
        output_path,
        _base_output_metadata(
            case_name=_output_case_name(result),
            geometry_source_csv=result.config.output_directory
            / optimizer_geometry_csv_name(_output_case_name(result)),
            station_source_csv=output_path,
            twist_column_used="twist_deg",
            aero_source=MODEL_SOURCE_OPTIMIZER,
        ),
    )


def write_reference_csv(
    reference_stations: Sequence[ReferenceStation],
    radius_m: float,
    output_path: Path,
    *,
    output_domain: str = "geometry",
) -> None:
    _assert_output_path(output_path, output_domain)
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
                "generating_script",
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
                "radius_m",
                "radius_in",
                "chord_in",
            ]
        )
        for station in reference_stations:
            radius_station_m = station.mu * radius_m
            chord_m = station.chord_fraction * radius_m
            writer.writerow(
                [
                    f"{station.mu:.4f}",
                    GENERATING_SCRIPT,
                    f"{station.chord_fraction:.6f}",
                    f"{chord_m:.6f}",
                    f"{station.twist_deg:.4f}",
                    f"{90.0 - station.twist_deg:.4f}",
                    station.airfoil,
                    "NASA/SciTech 18-inch geometry",
                    f"{scaled_to_diameter_in:.10g}",
                    f"{scaled_to_diameter_in / ORIGINAL_REFERENCE_DIAMETER_IN:.10f}",
                    operating_point,
                    "true",
                    f"{radius_station_m:.6f}",
                    f"{meters_to_inches(radius_station_m):.4f}",
                    f"{meters_to_inches(chord_m):.4f}",
                ]
            )
    write_metadata_sidecar(
        output_path,
        _base_output_metadata(
            case_name=output_path.stem,
            geometry_source_csv=output_path,
            station_source_csv=output_path,
            twist_column_used="twist_deg",
            aero_source=MODEL_SOURCE_OPTIMIZER,
        ),
    )


def write_convergence_history_csv(
    history: Sequence[dict[str, float]],
    output_path: Path,
    *,
    output_domain: str = "geometry",
) -> None:
    _assert_output_path(output_path, output_domain)
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
    write_metadata_sidecar(
        output_path,
        _base_output_metadata(
            case_name=output_path.stem.replace("_convergence_history", ""),
            geometry_source_csv="",
            station_source_csv=output_path,
        ),
    )


def write_methodology_statement(output_path: Path, config: DesignInputs, *, output_domain: str = "geometry") -> None:
    _assert_output_path(output_path, output_domain)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(methodology_statement_for_config(config) + "\n", encoding="utf-8")


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
        ["Air density", result.config.rho, "kg/m^3", ""],
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
        ["Disk induced velocity", result.induced_velocity_mps, "m/s", "Full-disk actuator formula using target thrust"],
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
    cl_diag = cl_clipping_diagnostics(result)
    conv_diag = convergence_diagnostics(result)
    tip_diag = tip_loading_diagnostics(result)
    summary_rows.extend(
        [
            ["Cl clipped station count", cl_diag["clipped_station_count"], "stations", ""],
            ["Cl clipped fraction", cl_diag["clipped_fraction"], "", ""],
            ["Best iteration", conv_diag["best_iteration"], "", ""],
            ["Last iteration", conv_diag["last_iteration"], "", ""],
            ["Reported solution source", conv_diag["reported_solution_source"], "", ""],
            ["Target tip loading fraction", tip_diag["target_tip_loading_fraction"], "", "r/R >= 0.85"],
            ["Actual tip loading fraction", tip_diag["actual_tip_loading_fraction"], "", "r/R >= 0.85"],
        ]
    )
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
        "Station", "model_source", "mu", "radius_m", "radius_in", "dr_m", "chord_m", "chord_in", "chord_over_R",
        "twist_deg", "cad_plane_angle_deg", "phi_deg", "alpha_deg", "cl", "cd",
        "dthrust_n", "dtorque_nm", "airfoil", "target_spanload_weight", "actual_spanload_weight",
        "reference_chord_over_R", "reference_twist_deg", "hit_chord_min", "hit_chord_max",
        "hit_alpha_min", "hit_alpha_max", "hit_cl_limit", "negative_force", "impossible_force",
        "unclipped_cl_demand", "cl_max", "cl_margin", "is_clipped"
    ]]
    for station in result.stations:
        optimized_rows.append(
            [
                station.station_index,
                MODEL_SOURCE_OPTIMIZER,
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
                station.unclipped_cl_demand,
                station.cl_max,
                station.cl_margin,
                station.is_clipped,
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
    assert_geometry_output_path(output_path)
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
        "gmt_requirements": "GMT requirements torque minimization",
    }[result.config.mode]
    cl_diag = cl_clipping_diagnostics(result)
    tip_diag = tip_loading_diagnostics(result)
    loading_diag = loading_fraction_diagnostics(result)
    conv_diag = convergence_diagnostics(result)
    twist_diag = design_twist_metrics(result.config, result.stations)
    validity_metrics = station_validity_metrics(result.config, result.stations, twist_diag)
    alpha_limit_fraction_pct = float(validity_metrics["alpha_limit_fraction_pct"])
    cl_limit_fraction_pct = float(validity_metrics["cl_limit_fraction_pct"])
    high_alpha_fraction_pct = float(validity_metrics["high_alpha_fraction_pct"])
    negative_loading_fraction_pct = float(validity_metrics["negative_loading_fraction_pct"])
    nonfinite_loading_fraction_pct = float(validity_metrics["nonfinite_loading_fraction_pct"])
    max_alpha_deg = max((station.alpha_deg for station in result.stations), default=0.0)
    min_alpha_deg = min((station.alpha_deg for station in result.stations), default=0.0)
    max_cl = max((station.cl for station in result.stations), default=0.0)
    negative_station_count = negative_loading_station_count(result)
    result_values = [result.objective, result.thrust_n, result.torque_nm, result.power_w, result.ct, result.cq, result.cp]
    result_finite = all(math.isfinite(value) for value in result_values)
    failure_reasons = validity_failure_reasons_from_metrics(
        result.config,
        result.normalized_thrust_error,
        validity_metrics,
        result.failure_reasons,
    )
    valid_for_optimizer_summary, valid_for_bemt_summary, valid_for_thesis_export = validity_flags_from_metrics(
        result_finite,
        result.torque_nm,
        result.normalized_thrust_error,
        failure_reasons,
    )
    recommendations = recommended_next_actions(result, failure_reasons)
    thesis_aero_pass = (
        cl_limit_fraction_pct <= result.config.max_cl_limit_fraction_pct
        and alpha_limit_fraction_pct <= result.config.max_alpha_limit_fraction_pct
        and not any(station.impossible_force or station.negative_force for station in result.stations)
    )
    optimized_geometry_csv = result.config.output_directory / optimizer_geometry_csv_name(
        optimized_case_name(result.config.spanload_type, result.config.manufacturable_twist)
    )
    aero_calibration_used = aero_calibration_used_for_config(result.config)
    calibration_mode = calibration_mode_for_config(result.config)
    return {
        "model_source": MODEL_SOURCE_OPTIMIZER,
        "generating_script": GENERATING_SCRIPT,
        "operating_point": active_operating_point_dict(result.config),
        "diameter_in": meters_to_inches(2.0 * result.config.radius_m),
        "rpm": result.config.rpm,
        "V_inf": result.config.freestream_mps,
        "V_inf_mps": result.config.freestream_mps,
        "target_thrust_N": result.config.thrust_target_n,
        "actual_thrust_N": result.thrust_n,
        "thrust_error_percent": 100.0 * result.normalized_thrust_error,
        "torque_Nm": result.torque_nm,
        "power_W": result.power_w,
        "CT": result.ct,
        "CQ": result.cq,
        "CP": result.cp,
        "geometry_source_csv": str(optimized_geometry_csv),
        "station_source_csv": str(optimized_geometry_csv),
        "twist_column_used": "twist_deg",
        "source_csv_modified_time": source_modified_time(optimized_geometry_csv),
        "geometry_source_csv_modified_time": source_modified_time(optimized_geometry_csv),
        "valid_for_optimizer_summary": valid_for_optimizer_summary,
        "valid_for_bemt_summary": valid_for_bemt_summary,
        "valid_for_thesis_export": valid_for_thesis_export,
        "validity_status": validity_status_label(valid_for_thesis_export, failure_reasons),
        "failure_reason": primary_failure_reason(failure_reasons),
        "failure_reasons": failure_reasons,
        "recommended_next_action": recommendations[0],
        "recommended_next_actions": recommendations,
        "required_design_freedom": [
            action
            for action in recommendations
            if action
            in {
                RECOMMEND_REDUCE_THRUST,
                RECOMMEND_INCREASE_RPM,
                RECOMMEND_ALLOW_CHORD,
                RECOMMEND_ALLOW_AIRFOIL,
            }
        ],
        "solver_mode": result.config.mode,
        "preset": result.config.preset,
        "solver_mode_label": mode_label,
        "optimizer": result.optimizer_name,
        "optimizer_fallback_used": result.optimizer_fallback_used,
        "optimizer_fallback_reason": result.optimizer_fallback_reason,
        "spanload_type": result.config.spanload_type,
        "requested_gmt_aggressiveness": result.requested_gmt_aggressiveness,
        "achieved_gmt_aggressiveness": result.achieved_gmt_aggressiveness,
        "target_tip_loading_fraction": loading_diag["target_tip_loading_fraction"],
        "actual_tip_loading_fraction": loading_diag["actual_tip_loading_fraction"],
        "target_inboard_loading_fraction": loading_diag["target_inboard_loading_fraction"],
        "actual_inboard_loading_fraction": loading_diag["actual_inboard_loading_fraction"],
        "aero_calibration_used": aero_calibration_used,
        "aero_calibration_scale": result.aero_calibration_scale,
        "calibration_mode": calibration_mode,
        "chord_locked": result.config.lock_chord,
        "airfoil_locked": result.config.lock_airfoil,
        "optimize_twist_only": result.config.optimize_twist_only,
        "free_design_enabled": free_design_enabled(result.config),
        "reference_geometry_used_as_initial_guess": result.config.mode != "reproduce_reference",
        "manufacturable_twist": result.config.manufacturable_twist,
        "Cl clipping fraction": cl_limit_fraction_pct,
        "Cl_clipping_fraction_pct": cl_limit_fraction_pct,
        "alpha clipping fraction": alpha_limit_fraction_pct,
        "alpha_clipping_fraction_pct": alpha_limit_fraction_pct,
        "negative_loading_station_count": negative_station_count,
        "max_alpha_deg": max_alpha_deg,
        "min_alpha_deg": min_alpha_deg,
        "max_Cl": max_cl,
        "manufacturable_twist_pass": twist_diag["manufacturable_twist_pass"],
        "airfoil_schedule_valid": bool(validity_metrics["airfoil_schedule_valid"]),
        "allowed_airfoils": list(allowed_airfoils_for_config(result.config)),
        "chord_bounds_in": {
            "minimum": result.config.chord_min_in,
            "maximum": result.config.chord_max_in,
        },
        "twist_bounds_deg": {
            "minimum": result.config.twist_min_deg,
            "maximum": result.config.twist_max_deg,
        },
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
            "thesis_case": active_operating_point_dict(result.config),
            "nasa_scitech_original_context": NASA_SCITECH_ORIGINAL_OPERATING_POINT,
            "statement": "35 N is only the thesis 22-inch static target, not the NASA/SciTech original operating point.",
        },
        "optimized_geometry_source": "Generated by the present Geometry_optimization.py run.",
        "case_name": optimized_case_name(result.config.spanload_type, result.config.manufacturable_twist),
        "reference_case_name": reference_case_name(result.config.spanload_type),
        "reference_regularization": {
            "twist_weight": result.weights.w_reference_twist,
            "chord_weight": result.weights.w_reference_chord,
            "statement": "Reference terms are calibration/regularization terms, not undisclosed hard-coded optimizer output.",
        },
        "aero_calibration": {
            "used": aero_calibration_used,
            "section_force_scale": result.aero_calibration_scale,
            "aero_calibration_scale": result.aero_calibration_scale,
            "calibration_mode": calibration_mode,
            "statement": "This scalar calibrates the reduced-order section-force model in reference-regularized runs and is reported explicitly.",
        },
        "locked_inputs": {
            "diameter_m": 2.0 * result.config.radius_m,
            "diameter_in": meters_to_inches(2.0 * result.config.radius_m),
            "chord_locked": result.config.lock_chord,
            "airfoil_locked": result.config.lock_airfoil,
            "optimize_twist_only": result.config.optimize_twist_only,
            "manufacturable_twist": result.config.manufacturable_twist,
            "rpm": result.config.rpm,
            "rho_kg_m3": result.config.rho,
            "v_inf_mps": result.config.freestream_mps,
            "advance_ratio": result.j_advance,
            "target_thrust_n": result.config.thrust_target_n,
            "hub_radius_fraction": result.config.hub_radius_fraction,
        },
        "induced_velocity_mps": result.induced_velocity_mps,
        "induced_velocity_formula_inputs": induced_velocity_formula_inputs(result),
        "static_efficiency_formula_inputs": {
            "formula": "eta_static = T^(3/2)/(P*sqrt(2*rho*A))",
            "thrust_n": result.thrust_n,
            "power_w": result.power_w,
            "rho_kg_m3": result.config.rho,
            "disk_area_m2": actuator_disk_area(result.config.radius_m),
            "disk_area_definition": "full propeller disk area pi*R^2",
        },
        "gmt_tip_unloading_verification": {
            "status": "reported_from_single_case_loading_fractions",
            "requested_gmt_aggressiveness": result.requested_gmt_aggressiveness,
            "achieved_gmt_aggressiveness": result.achieved_gmt_aggressiveness,
            "target_tip_loading_fraction": loading_diag["target_tip_loading_fraction"],
            "actual_tip_loading_fraction": loading_diag["actual_tip_loading_fraction"],
            "target_inboard_loading_fraction": loading_diag["target_inboard_loading_fraction"],
            "actual_inboard_loading_fraction": loading_diag["actual_inboard_loading_fraction"],
            "statement": "Do not claim ideal GMT tip unloading unless the actual integrated loading fractions match the target and all hard validity checks pass.",
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
            "induced_velocity_mps": result.induced_velocity_mps,
            "spanload_error": result.spanload_error,
            "reference_twist_rms_deg": result.reference_twist_rms_deg,
            "reference_twist_max_deg": result.reference_twist_max_deg,
            "reference_chord_rms_percent": result.reference_chord_rms_percent,
            "smoothness_penalty": result.smoothness_penalty,
            "bounds_penalty": result.bounds_penalty,
            "max_positive_twist_bump_deg": twist_diag["max_positive_twist_bump_deg"],
            "positive_twist_bump_count": twist_diag["positive_twist_bump_count"],
            "max_abs_twist_gradient_deg_per_mu": twist_diag["max_abs_twist_gradient_deg_per_mu"],
            "max_twist_curvature": twist_diag["max_twist_curvature"],
            "manufacturable_twist_pass": twist_diag["manufacturable_twist_pass"],
            "cl_limit_station_fraction_pct": cl_limit_fraction_pct,
            "alpha_limit_station_fraction_pct": alpha_limit_fraction_pct,
            "high_alpha_station_fraction_pct": high_alpha_fraction_pct,
            "negative_loading_station_fraction_pct": negative_loading_fraction_pct,
            "negative_loading_station_count": negative_station_count,
            "nonfinite_loading_station_fraction_pct": nonfinite_loading_fraction_pct,
            "max_alpha_deg": max_alpha_deg,
            "min_alpha_deg": min_alpha_deg,
            "max_Cl": max_cl,
            "clipped_station_count": cl_diag["clipped_station_count"],
            "target_tip_loading_fraction": loading_diag["target_tip_loading_fraction"],
            "actual_tip_loading_fraction": loading_diag["actual_tip_loading_fraction"],
            "target_inboard_loading_fraction": loading_diag["target_inboard_loading_fraction"],
            "actual_inboard_loading_fraction": loading_diag["actual_inboard_loading_fraction"],
        },
        "twist_manufacturability": twist_diag,
        "aero_validity": {
            "cl_limit_station_fraction_pct": cl_limit_fraction_pct,
            "alpha_limit_station_fraction_pct": alpha_limit_fraction_pct,
            "max_alpha_deg": max_alpha_deg,
            "min_alpha_deg": min_alpha_deg,
            "max_Cl": max_cl,
            "clipped_station_count": cl_diag["clipped_station_count"],
            "high_alpha_station_fraction_pct": high_alpha_fraction_pct,
            "negative_loading_station_fraction_pct": negative_loading_fraction_pct,
            "negative_loading_station_count": negative_station_count,
            "nonfinite_loading_station_fraction_pct": nonfinite_loading_fraction_pct,
            "cl_limit_threshold_pct": result.config.max_cl_limit_fraction_pct,
            "alpha_limit_threshold_pct": result.config.max_alpha_limit_fraction_pct,
            "outside_polar_range_station_count": sum(
                station.hit_alpha_min or station.hit_alpha_max or station.hit_cl_limit
                for station in result.stations
            ),
            "thesis_aero_pass": thesis_aero_pass,
        },
        "convergence_diagnostics": conv_diag,
        "clipping_diagnostics": cl_diag,
        "tip_loading_diagnostics": tip_diag,
        "objective_breakdown": result.objective_breakdown,
        "parameters": parameter_dict(result.parameters),
        "warnings": list(result.warnings),
        "methodology_statement": methodology_statement_for_config(result.config),
    }


def write_summary_json(result: DesignResult, output_path: Path, *, output_domain: str = "geometry") -> None:
    _assert_output_path(output_path, output_domain)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(result_summary_dict(result), handle, indent=2, default=_json_default)
        handle.write("\n")
    geometry_csv = result.config.output_directory / optimizer_geometry_csv_name(_output_case_name(result))
    write_metadata_sidecar(
        output_path,
        _base_output_metadata(
            case_name=_output_case_name(result),
            geometry_source_csv=geometry_csv,
            station_source_csv=geometry_csv,
            twist_column_used="twist_deg",
            aero_source=MODEL_SOURCE_OPTIMIZER,
        ),
    )


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def source_modified_time(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return ""


def write_optimizer_plot_metadata(
    result: DesignResult,
    output_dir: Path,
    stem: str,
    geometry_csv_path: Path | None = None,
) -> None:
    assert_geometry_output_path(output_dir)
    geometry_csv = geometry_csv_path or (
        result.config.output_directory / optimizer_geometry_csv_name(_output_case_name(result))
    )
    metadata = {
        "geometry_source_csv": str(geometry_csv),
        "station_source_csv": str(geometry_csv),
        "twist_column_used": "twist_deg",
        "model_source": MODEL_SOURCE_OPTIMIZER,
        "generating_script": GENERATING_SCRIPT,
        "source_csv_modified_time": source_modified_time(geometry_csv),
        "geometry_source_csv_modified_time": source_modified_time(geometry_csv),
        "valid_for_thesis_export": result_summary_dict(result)["valid_for_thesis_export"],
    }
    write_metadata_sidecar(output_dir / f"{stem}.png", metadata)


def _airfoil_indices(stations: Sequence[StationResult]) -> tuple[list[int], list[str]]:
    names = sorted({station.airfoil for station in stations})
    index = {name: idx for idx, name in enumerate(names)}
    return [index[station.airfoil] for station in stations], names


def _median(values: Sequence[float]) -> float:
    finite = sorted(abs(value) for value in values if math.isfinite(value))
    if not finite:
        return 0.0
    middle = len(finite) // 2
    if len(finite) % 2:
        return finite[middle]
    return 0.5 * (finite[middle - 1] + finite[middle])


def write_gmt_jump_alignment_report(result: DesignResult, output_dir: Path) -> tuple[Path, Path]:
    assert_geometry_output_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stations = list(result.stations)
    fields: dict[str, list[float]] = {
        "beta_deg": [station.twist_deg for station in stations],
        "phi_deg": [station.phi_deg for station in stations],
        "alpha_deg": [station.alpha_deg for station in stations],
        "Cl": [station.cl for station in stations],
        "Cd": [station.cd for station in stations],
        "dT_N": [station.dthrust_n for station in stations],
        "dQ_Nm": [station.dtorque_nm for station in stations],
        "Re": [station.reynolds_number for station in stations],
    }
    field_thresholds: dict[str, float] = {}
    for name, values in fields.items():
        deltas = [right - left for left, right in zip(values[:-1], values[1:])]
        absolute_floor = {
            "beta_deg": 1.0,
            "phi_deg": 1.0,
            "alpha_deg": 1.0,
            "Cl": 0.12,
            "Cd": 0.0015,
            "dT_N": 0.20,
            "dQ_Nm": 0.006,
            "Re": 5000.0,
        }[name]
        field_thresholds[name] = max(3.0 * _median(deltas), absolute_floor)

    rows: list[dict[str, object]] = []
    for idx, (left, right) in enumerate(zip(stations[:-1], stations[1:])):
        row: dict[str, object] = {
            "mu_left": f"{left.mu:.4f}",
            "mu_right": f"{right.mu:.4f}",
            "mu_mid": f"{0.5 * (left.mu + right.mu):.4f}",
            "airfoil_change": left.airfoil != right.airfoil,
            "airfoil_left": left.airfoil,
            "airfoil_right": right.airfoil,
            "cl_clipped_left": left.hit_cl_limit,
            "cl_clipped_right": right.hit_cl_limit,
            "clipping_adjacent": left.hit_cl_limit or right.hit_cl_limit,
            "clmax_change": f"{right.cl_max - left.cl_max:.6f}",
            "alpha_stall_change_deg": f"{right.alpha_stall_deg - left.alpha_stall_deg:.6f}",
            "optimizer_knot_alignment": "none: global optimizer basis, no station knots",
            "polar_boundary_alignment": "airfoil schedule change" if left.airfoil != right.airfoil else "none: reduced-order analytic polar",
        }
        sharp_fields: list[str] = []
        biggest_field = ""
        biggest_ratio = 0.0
        for name, values in fields.items():
            left_value = values[idx]
            right_value = values[idx + 1]
            delta = right_value - left_value
            threshold = field_thresholds[name]
            ratio = abs(delta) / max(threshold, 1.0e-12)
            row[f"delta_{name}"] = f"{delta:.8g}"
            row[f"sharp_{name}"] = ratio >= 1.0
            if ratio >= 1.0:
                sharp_fields.append(name)
            if ratio > biggest_ratio:
                biggest_ratio = ratio
                biggest_field = name
        row["sharp_fields"] = ";".join(sharp_fields)
        row["biggest_jump_field"] = biggest_field
        row["biggest_jump_ratio"] = f"{biggest_ratio:.3f}"
        rows.append(row)

    csv_path = output_dir / "gmt_jump_alignment_report.csv"
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        csv_path.write_text("", encoding="utf-8")

    sharp_rows = [row for row in rows if row.get("sharp_fields")]
    payload = {
        "case_name": _output_case_name(result),
        "sharp_interval_count": len(sharp_rows),
        "sharp_intervals": sharp_rows,
        "field_thresholds": field_thresholds,
        "interpolation_audit": {
            "cubic_splines_found_in_geometry_optimizer": False,
            "twist_alpha_cl_chord_interpolation": "none in optimizer; FEA/postprocess interpolation remains linear",
            "optimizer_control_layout": "global smooth basis parameters, not discrete knots",
        },
    }
    json_path = output_dir / "gmt_jump_alignment_report.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return csv_path, json_path


def plot_gmt_diagnostic_overlays(
    plt,
    result: DesignResult,
    reference_stations: Sequence[ReferenceStation],
    output_dir: Path,
) -> None:
    assert_geometry_output_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stations = list(result.stations)
    mu = [station.mu for station in stations]
    reference_twist = [station.twist_deg for station in reference_stations]

    def mark_airfoil_changes(ax) -> None:
        for left, right in zip(stations[:-1], stations[1:]):
            if left.airfoil != right.airfoil:
                ax.axvline(0.5 * (left.mu + right.mu), color="0.75", linestyle=":", linewidth=0.8)

    fig, ax = plt.subplots(figsize=(5.2, 3.2))
    ax.plot(mu, [station.twist_deg for station in stations], label="beta")
    ax.plot(mu, [station.phi_deg for station in stations], label="phi")
    ax.plot(mu, [station.alpha_deg for station in stations], label="alpha")
    ax.plot(mu, reference_twist, color="0.55", linestyle="--", label="reference beta")
    mark_airfoil_changes(ax)
    ax.set_xlabel("r/R [-]")
    ax.set_ylabel("angle [deg]")
    style_matplotlib_axes(ax)
    fig.tight_layout()
    save_matplotlib_figure(fig, output_dir, "gmt_diagnostic_angles", overwrite=True)
    plt.close(fig)

    fig, ax_cl = plt.subplots(figsize=(5.2, 3.2))
    ax_cd = ax_cl.twinx()
    ax_cl.plot(mu, [station.cl for station in stations], label="Cl")
    ax_cl.plot(mu, [station.cl_max for station in stations], color="0.35", linestyle="--", label="Cl_max")
    ax_cl.plot(mu, [gmt_tip_target_cl(station.mu) if station.mu >= TIP_UNLOAD_START_MU else math.nan for station in stations], color="0.20", linestyle=":", label="target Cl tip")
    ax_cd.plot(mu, [station.cd for station in stations], color="0.55", label="Cd")
    mark_airfoil_changes(ax_cl)
    ax_cl.set_xlabel("r/R [-]")
    ax_cl.set_ylabel("Cl [-]")
    ax_cd.set_ylabel("Cd [-]")
    lines_a, labels_a = ax_cl.get_legend_handles_labels()
    lines_b, labels_b = ax_cd.get_legend_handles_labels()
    ax_cl.legend(lines_a + lines_b, labels_a + labels_b, frameon=False)
    style_matplotlib_axes(ax_cl, legend=False)
    fig.tight_layout()
    save_matplotlib_figure(fig, output_dir, "gmt_diagnostic_cl_cd", overwrite=True)
    plt.close(fig)

    fig, ax_load = plt.subplots(figsize=(5.2, 3.2))
    ax_re = ax_load.twinx()
    ax_load.plot(mu, [station.dthrust_n for station in stations], label="dT")
    ax_load.plot(mu, [station.dtorque_nm for station in stations], label="dQ")
    ax_load.axhline(0.0, color="0.25", linestyle=":", linewidth=0.8)
    ax_re.plot(mu, [station.reynolds_number for station in stations], color="0.55", label="Re")
    mark_airfoil_changes(ax_load)
    ax_load.set_xlabel("r/R [-]")
    ax_load.set_ylabel("section contribution [N, N*m]")
    ax_re.set_ylabel("Re [-]")
    lines_a, labels_a = ax_load.get_legend_handles_labels()
    lines_b, labels_b = ax_re.get_legend_handles_labels()
    ax_load.legend(lines_a + lines_b, labels_a + labels_b, frameon=False)
    style_matplotlib_axes(ax_load, legend=False)
    fig.tight_layout()
    save_matplotlib_figure(fig, output_dir, "gmt_diagnostic_loading_re", overwrite=True)
    plt.close(fig)

    airfoil_ids, airfoil_names = _airfoil_indices(stations)
    fig, ax_airfoil = plt.subplots(figsize=(5.2, 3.2))
    ax_stall = ax_airfoil.twinx()
    ax_airfoil.step(mu, airfoil_ids, where="mid", label="airfoil ID")
    ax_stall.plot(mu, [station.alpha_stall_deg for station in stations], color="0.45", label="alpha_stall")
    ax_stall.plot(mu, [station.cl_max for station in stations], color="0.65", linestyle="--", label="Cl_max")
    ax_airfoil.set_xlabel("r/R [-]")
    ax_airfoil.set_ylabel("airfoil")
    ax_airfoil.set_yticks(range(len(airfoil_names)), airfoil_names)
    ax_stall.set_ylabel("alpha_stall [deg] / Cl_max")
    lines_a, labels_a = ax_airfoil.get_legend_handles_labels()
    lines_b, labels_b = ax_stall.get_legend_handles_labels()
    ax_airfoil.legend(lines_a + lines_b, labels_a + labels_b, frameon=False)
    style_matplotlib_axes(ax_airfoil, legend=False)
    fig.tight_layout()
    save_matplotlib_figure(fig, output_dir, "gmt_diagnostic_airfoil_schedule", overwrite=True)
    plt.close(fig)

    params = result.parameters
    control_base = [params.base_alpha_deg for _ in mu]
    inner = []
    outer = []
    curvature = []
    tip = []
    total = []
    for station_mu in mu:
        x = clamp((station_mu - result.config.hub_radius_fraction) / max(1.0 - result.config.hub_radius_fraction, 1.0e-9), 0.0, 1.0)
        inner_shape = math.exp(-((x - 0.45) / 0.24) ** 2)
        outer_linear = station_mu - 0.70
        curvature_shape = (station_mu - 0.70) ** 2 - 0.035
        tip_shape = -tip_unload_shape(station_mu, result.config.hub_radius_fraction)
        inner.append(params.twist_bias_inner * inner_shape)
        outer.append(params.twist_bias_outer * outer_linear)
        curvature.append(params.twist_curvature * curvature_shape)
        tip.append(params.tip_unload_strength * tip_shape)
        total.append(twist_correction(station_mu, result.config.hub_radius_fraction, params))

    fig, ax = plt.subplots(figsize=(5.2, 3.2))
    ax.plot(mu, control_base, label="base alpha")
    ax.plot(mu, inner, label="inner bias")
    ax.plot(mu, outer, label="outer bias")
    ax.plot(mu, curvature, label="curvature")
    ax.plot(mu, tip, label="tip unload")
    ax.plot(mu, total, color="black", linewidth=1.8, label="total correction")
    mark_airfoil_changes(ax)
    ax.set_xlabel("r/R [-]")
    ax.set_ylabel("control contribution [deg]")
    style_matplotlib_axes(ax)
    fig.tight_layout()
    save_matplotlib_figure(fig, output_dir, "gmt_diagnostic_optimizer_controls", overwrite=True)
    plt.close(fig)

    write_gmt_jump_alignment_report(result, output_dir)


def plot_thesis_outputs(
    result: DesignResult,
    reference_stations: Sequence[ReferenceStation],
    output_dir: Path,
    geometry_csv_path: Path | None = None,
) -> List[str]:
    assert_geometry_output_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        return [f"matplotlib unavailable; plots were not generated ({exc}). Install with: python -m pip install matplotlib"]

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
    dthrust = [station.dthrust_n for station in result.stations]
    dtorque = [station.dtorque_nm for station in result.stations]
    cumulative_thrust: List[float] = []
    cumulative_torque: List[float] = []
    running_thrust = 0.0
    running_torque = 0.0
    for station in result.stations:
        running_thrust += station.dthrust_n
        running_torque += station.dtorque_nm
        cumulative_thrust.append(running_thrust)
        cumulative_torque.append(running_torque)
    dr_values = [station.dr_m for station in result.stations]
    mil_weights = normalized_section_weights(mu, dr_values, "minimum_induced_loss", result.config.hub_radius_fraction)
    reference_weights = normalized_reference_loading(reference_stations, dr_values)
    mil_twist_proxy = [
        reference.twist_deg + 2.0 * math.log(max(mil_weight, 1.0e-9) / max(reference_weight, 1.0e-9))
        for reference, mil_weight, reference_weight in zip(reference_stations, mil_weights, reference_weights)
    ]

    single_run_plots = [
        ("twist_distribution", optimized_twist, "twist [deg]"),
        ("chord_distribution", optimized_chord, "c/R"),
        ("Cl_distribution", cl, "Cl"),
        ("alpha_distribution", alpha, "alpha [deg]"),
        ("thrust_loading", dthrust, "dT [N]"),
        ("torque_loading", dtorque, "dQ [N*m]"),
        ("cumulative_thrust", cumulative_thrust, "cumulative thrust [N]"),
        ("cumulative_torque", cumulative_torque, "cumulative torque [N*m]"),
    ]
    for stem, values, ylabel in single_run_plots:
        fig = plt.figure()
        plt.plot(mu, values)
        plt.xlabel("r/R")
        plt.ylabel(ylabel)
        fig.tight_layout()
        save_matplotlib_figure(fig, output_dir, stem, overwrite=True)
        write_optimizer_plot_metadata(result, output_dir, stem, geometry_csv_path)
        plt.close(fig)

    fig = plt.figure()
    plt.plot(mu, reference_twist, label="reference")
    plt.plot(mu, optimized_twist, label="optimized")
    plt.plot(mu, mil_twist_proxy, label="MIL proxy")
    plt.xlabel("r/R")
    plt.ylabel("twist beta [deg]")
    plt.legend()
    fig.tight_layout()
    save_matplotlib_figure(fig, output_dir, "twist_comparison", overwrite=True)
    write_optimizer_plot_metadata(result, output_dir, "twist_comparison", geometry_csv_path)
    plt.close(fig)

    fig = plt.figure()
    plt.plot(mu, reference_chord, label="reference")
    plt.plot(mu, optimized_chord, label="optimized")
    plt.xlabel("r/R")
    plt.ylabel("c/R")
    plt.legend()
    fig.tight_layout()
    save_matplotlib_figure(fig, output_dir, "chord_comparison", overwrite=True)
    write_optimizer_plot_metadata(result, output_dir, "chord_comparison", geometry_csv_path)
    plt.close(fig)

    fig = plt.figure()
    plt.plot(mu, target_spanload, label="target GMT")
    plt.plot(mu, actual_spanload, label="optimized")
    plt.plot(mu, mil_weights, label="MIL proxy")
    plt.xlabel("r/R")
    plt.ylabel("normalized dT")
    plt.legend()
    fig.tight_layout()
    save_matplotlib_figure(fig, output_dir, "spanload_comparison", overwrite=True)
    write_optimizer_plot_metadata(result, output_dir, "spanload_comparison", geometry_csv_path)
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
    save_matplotlib_figure(fig, output_dir, "alpha_cl_distribution", overwrite=True)
    write_optimizer_plot_metadata(result, output_dir, "alpha_cl_distribution", geometry_csv_path)
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
        save_matplotlib_figure(fig, output_dir, "convergence_objective", overwrite=True)
        write_optimizer_plot_metadata(result, output_dir, "convergence_objective", geometry_csv_path)
        plt.close(fig)

    if is_gmt_spanload_type(result.config.spanload_type):
        plot_gmt_diagnostic_overlays(plt, result, reference_stations, output_dir / "diagnostics")

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
    print(f"  aero calibration     : {result.calibration_mode} ({result.aero_calibration_scale:.4f})")
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
    cl_diag = cl_clipping_diagnostics(result)
    if chord_limit_fraction > 0.20 or alpha_limit_fraction > 0.20:
        print(
            "  limit warning        : "
            f"{100.0 * chord_limit_fraction:.1f}% chord, {100.0 * alpha_limit_fraction:.1f}% alpha stations at limits"
        )
    if cl_diag["clipped_station_count"]:
        print(
            "  Cl clipping          : "
            f"{cl_diag['clipped_station_count']} stations ({100.0 * cl_diag['clipped_fraction']:.1f}%)"
        )
    if result.failure_reason != FAILURE_NONE:
        print(f"  validity             : {result.failure_reason}")
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
    parser.add_argument(
        "--gmt-aggressiveness",
        default=None,
        help="GMT spanload aggressiveness: numeric 0..1, or mild/moderate/aggressive.",
    )
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
    parser.add_argument("--twist-min-deg", type=float, default=None)
    parser.add_argument("--twist-max-deg", type=float, default=None)
    parser.add_argument("--allowed-airfoils", default=None, help="Comma-separated airfoil names allowed in free design mode.")
    parser.add_argument("--chord-blend", type=float, default=None, help="0 keeps the aero planform estimate, 1 keeps the reference chord.")
    parser.add_argument("--twist-blend", type=float, default=None, help="0 applies all twist correction, 1 keeps reference twist.")
    parser.add_argument("--smooth-passes", type=int, default=None)
    parser.add_argument("--alpha-min", type=float, default=None, help="Lower section alpha sanity/penalty limit in degrees.")
    parser.add_argument("--alpha-max", type=float, default=None, help="Upper section alpha sanity/penalty limit in degrees.")
    parser.add_argument("--alpha-step", type=float, default=None, help="Legacy alpha-search step retained for compatibility.")
    parser.add_argument("--reference-csv", type=Path, default=None, help="Fallback reference geometry CSV or XLSX.")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--figures-dir", type=Path, default=None)
    parser.add_argument("--lock-chord", action="store_true")
    parser.add_argument("--lock-airfoil", action="store_true")
    parser.add_argument("--optimize-twist-only", action="store_true")
    parser.add_argument("--make-plots", action="store_true")
    parser.add_argument("--optimizer", choices=OPTIMIZER_CHOICES, default=None)
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--tolerance", type=float, default=None)
    parser.add_argument("--manufacturable-twist", action="store_true", help="Apply thesis manufacturable twist shaping and validity checks.")
    parser.add_argument("--max-positive-twist-bump", type=float, default=None, help="Maximum allowed positive station twist increase after the root transition in degrees.")
    parser.add_argument("--max-twist-gradient", type=float, default=None, help="Maximum allowed absolute twist gradient in deg per r/R after the root transition.")
    parser.add_argument("--max-twist-curvature", type=float, default=None, help="Maximum allowed absolute twist curvature in deg per (r/R)^2 after the root transition.")
    parser.add_argument("--max-cl-limit-fraction-pct", type=float, default=None, help="Maximum percent of stations allowed to hit the reduced-order Cl cap for thesis export.")
    parser.add_argument("--max-alpha-limit-fraction-pct", type=float, default=None, help="Maximum percent of stations allowed outside the alpha range for thesis export.")
    parser.add_argument("--thrust-validity-sweep", action="store_true", help="Audit locked thesis validity for target thrusts 15, 20, 25, 30, and 35 N.")


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
        args.manufacturable_twist = True
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

    if args.preset in {"thesis_gmt_requirements_22", "thesis_gmt_22_free_design"}:
        if args.diameter_in is None:
            args.diameter_in = 22.0
        if args.spanload is None:
            args.spanload = "gmt_bell"
        if args.mode is None:
            args.mode = "gmt_requirements"
        args.lock_chord = False
        args.lock_airfoil = False
        args.optimize_twist_only = False
        args.manufacturable_twist = True
        if args.smooth_passes is None:
            args.smooth_passes = 8
        if args.rpm is None:
            args.rpm = 4000.0
        if args.v_inf is None:
            args.v_inf = 0.0
        if args.thrust is None:
            args.thrust = 35.0
        if args.chord_blend is None:
            args.chord_blend = 0.25
        if args.twist_blend is None:
            args.twist_blend = 0.05
        if args.alpha_max is None:
            args.alpha_max = 15.0
        if args.chord_max_in is None:
            args.chord_max_in = 3.4
        if args.max_iterations is None:
            args.max_iterations = 85
        if args.optimizer is None:
            args.optimizer = "auto"
        if args.gmt_aggressiveness is None:
            args.gmt_aggressiveness = "aggressive"

    if args.preset in {"thesis_gmt_22_manufacturable", "thesis_mil_22_manufacturable"}:
        args.manufacturable_twist = True
        args.lock_chord = True
        args.lock_airfoil = True
        args.optimize_twist_only = True
        if args.diameter_in is None:
            args.diameter_in = 22.0
        if args.spanload is None:
            args.spanload = (
                "minimum_induced_loss"
                if args.preset == "thesis_mil_22_manufacturable"
                else "gmt_bell"
            )
        if args.mode is None:
            args.mode = "reference_regularized"
        if args.smooth_passes is None:
            args.smooth_passes = 8
        if args.rpm is None:
            args.rpm = 4000.0
        if args.v_inf is None:
            args.v_inf = 0.0
        if args.thrust is None:
            args.thrust = 35.0
        if args.chord_blend is None:
            args.chord_blend = 0.35
        if args.twist_blend is None:
            args.twist_blend = 0.10
        if args.alpha_max is None:
            args.alpha_max = 15.0
        if args.chord_max_in is None:
            args.chord_max_in = 3.0
        if args.max_iterations is None:
            args.max_iterations = 70
        if args.optimizer is None:
            args.optimizer = "coordinate_search"

    if args.spanload is None:
        args.spanload = "gmt_bell"
    if args.mode is None:
        args.mode = "reference_regularized"
    if args.rpm is None:
        args.rpm = 4000.0
    if args.thrust is None:
        args.thrust = 35.0
    if args.gmt_aggressiveness is None:
        args.gmt_aggressiveness = "aggressive"
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
    if args.twist_min_deg is None:
        args.twist_min_deg = 0.0
    if args.twist_max_deg is None:
        args.twist_max_deg = 65.0
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
        args.alpha_step = 0.25 if args.spanload in GMT_SPANLOAD_TYPES else 0.5
    if args.reference_csv is None:
        args.reference_csv = reference_path
    if args.output_dir is None:
        args.output_dir = (
            Path(__file__).resolve().parent.parent
            / "results"
            / "geometry_optimizer"
            / str(canonical_case_dir_name(optimized_case_name(args.spanload, args.manufacturable_twist)))
        )
    if args.optimizer is None:
        args.optimizer = "auto"
    if args.max_iterations is None:
        args.max_iterations = 35
    if args.tolerance is None:
        args.tolerance = 1.0e-4
    if args.max_positive_twist_bump is None:
        args.max_positive_twist_bump = 0.5
    if args.max_twist_gradient is None:
        args.max_twist_gradient = 70.0 if args.spanload in GMT_SPANLOAD_TYPES else 100.0
    if args.max_twist_curvature is None:
        args.max_twist_curvature = 1500.0 if args.spanload in GMT_SPANLOAD_TYPES else 1500.0
    if args.max_cl_limit_fraction_pct is None:
        args.max_cl_limit_fraction_pct = 10.0
    if args.max_alpha_limit_fraction_pct is None:
        args.max_alpha_limit_fraction_pct = 10.0
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


def optimized_case_name(spanload_type: str, manufacturable: bool = False) -> str:
    family = case_family_for_spanload(spanload_type)
    if manufacturable:
        return f"optimized_{family}_22_manufacturable"
    return OPTIMIZED_MIL_CASE if spanload_type == "minimum_induced_loss" else OPTIMIZED_GMT_CASE


def reference_case_name(spanload_type: str) -> str:
    return REFERENCE_MIL_CASE if spanload_type == "minimum_induced_loss" else REFERENCE_GMT_CASE


def output_spanload_name(spanload_type: str) -> str:
    return case_family_for_spanload(spanload_type)


def thrust_validity_sweep_row(result: DesignResult) -> dict[str, object]:
    summary = result_summary_dict(result)
    final_metrics = summary["final_metrics"]
    aero_validity = summary["aero_validity"]
    twist_metrics = summary["twist_manufacturability"]
    return {
        "target_thrust_N": result.config.thrust_target_n,
        "RPM": result.config.rpm,
        "actual_thrust_N": result.thrust_n,
        "thrust_error_percent": 100.0 * result.normalized_thrust_error,
        "thrust_error_pct": 100.0 * result.normalized_thrust_error,
        "torque_Nm": result.torque_nm,
        "power_W": result.power_w,
        "CT": result.ct,
        "CQ": result.cq,
        "CP": result.cp,
        "max_alpha_deg": final_metrics["max_alpha_deg"],
        "min_alpha_deg": final_metrics["min_alpha_deg"],
        "max_Cl": final_metrics["max_Cl"],
        "Cl_clipping_fraction_pct": aero_validity["cl_limit_station_fraction_pct"],
        "alpha_clipping_fraction_pct": aero_validity["alpha_limit_station_fraction_pct"],
        "negative_loading_station_count": aero_validity["negative_loading_station_count"],
        "tip_loading_fraction": summary["actual_tip_loading_fraction"],
        "inboard_loading_fraction": summary["actual_inboard_loading_fraction"],
        "max_positive_twist_bump_deg": twist_metrics["max_positive_twist_bump_deg"],
        "max_abs_twist_gradient_deg_per_mu": twist_metrics["max_abs_twist_gradient_deg_per_mu"],
        "max_twist_curvature": twist_metrics["max_twist_curvature"],
        "manufacturable_twist_pass": twist_metrics["manufacturable_twist_pass"],
        "valid_for_thesis_export": summary["valid_for_thesis_export"],
        "failure_reason": summary["failure_reason"],
        "failure_reasons": list(summary["failure_reasons"]),
        "requested_gmt_aggressiveness": summary["requested_gmt_aggressiveness"],
        "achieved_gmt_aggressiveness": summary["achieved_gmt_aggressiveness"],
        "recommended_next_action": summary["recommended_next_action"],
    }


def run_thrust_validity_sweep(
    base_config: DesignInputs,
    reference_stations: Sequence[ReferenceStation],
    initial_warnings: Sequence[str],
    reference_source: str,
    targets: Sequence[float] = THRUST_VALIDITY_SWEEP_TARGETS_N,
    rpms: Sequence[float] | None = None,
    audits_dir: Path | None = None,
    figure_dir: Path | None = None,
    make_plots: bool = False,
) -> dict[str, Any]:
    if rpms is None:
        rpms = THRUST_RPM_SWEEP_RPMS if tuple(targets) == THRUST_VALIDITY_SWEEP_TARGETS_N else (base_config.rpm,)
    output_dir = audits_dir or get_results_dir(
        "geometry_optimizer",
        model_source=MODEL_SOURCE_OPTIMIZER,
        case_name="audits",
    )
    assert_geometry_output_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: List[dict[str, object]] = []
    results: List[DesignResult] = []
    for rpm in rpms:
        for target in targets:
            target_label = str(target).replace(".", "p")
            rpm_label = str(rpm).replace(".", "p")
            sweep_config = replace(
                base_config,
                thrust_target_n=float(target),
                rpm=float(rpm),
                output_directory=output_dir / f"rpm_{rpm_label}_target_{target_label}N",
            )
            result = optimize_geometry(sweep_config, reference_stations, initial_warnings, reference_source)
            results.append(result)
            rows.append(thrust_validity_sweep_row(result))

    valid_rows = [row for row in rows if bool(row["valid_for_thesis_export"])]
    rows_at_4000 = [row for row in rows if abs(float(row["RPM"]) - 4000.0) <= 1.0e-9]
    valid_rows_at_4000 = [row for row in rows_at_4000 if bool(row["valid_for_thesis_export"])]
    highest_valid_4000 = max(valid_rows_at_4000, key=lambda row: float(row["actual_thrust_N"]), default=None)
    target_35_rows = [row for row in rows if abs(float(row["target_thrust_N"]) - 35.0) <= 1.0e-9]
    valid_target_35_rows = [row for row in target_35_rows if bool(row["valid_for_thesis_export"])]
    lowest_rpm_35 = min(valid_target_35_rows, key=lambda row: float(row["RPM"]), default=None)
    target_35_4000_row = next(
        (
            row
            for row in target_35_rows
            if abs(float(row["RPM"]) - 4000.0) <= 1.0e-9
        ),
        None,
    )
    feasible_35 = bool(target_35_4000_row and target_35_4000_row["valid_for_thesis_export"])
    highest_valid = max(valid_rows, key=lambda row: (float(row["actual_thrust_N"]), -float(row["RPM"])), default=None)
    summary_payload: dict[str, Any] = {
        "generating_script": GENERATING_SCRIPT,
        "model_source": MODEL_SOURCE_OPTIMIZER,
        "case_name": "thrust_rpm_feasibility_sweep",
        "targets_N": [float(target) for target in targets],
        "rpms": [float(rpm) for rpm in rpms],
        "chord_locked": base_config.lock_chord,
        "airfoil_locked": base_config.lock_airfoil,
        "optimize_twist_only": base_config.optimize_twist_only,
        "free_design_enabled": free_design_enabled(base_config),
        "manufacturable_twist": base_config.manufacturable_twist,
        "calibration_mode": calibration_mode_for_config(base_config),
        "aero_calibration_used": aero_calibration_used_for_config(base_config),
        "highest_valid_target_thrust_N": None if highest_valid is None else highest_valid["target_thrust_N"],
        "highest_valid_actual_thrust_N": None if highest_valid is None else highest_valid["actual_thrust_N"],
        "highest_valid_thrust_at_4000_rpm_N": None
        if highest_valid_4000 is None
        else highest_valid_4000["actual_thrust_N"],
        "lowest_RPM_that_can_meet_35N": None if lowest_rpm_35 is None else lowest_rpm_35["RPM"],
        "free_chord_or_airfoil_design_required": not feasible_35 and (
            base_config.lock_chord or base_config.lock_airfoil or base_config.optimize_twist_only
        ),
        "thirty_five_N_feasible_under_locked_chord_airfoil_constraints": feasible_35
        if base_config.lock_chord and base_config.lock_airfoil
        else None,
        "target_35_valid_for_thesis_export": feasible_35,
        "target_35_failure_reasons": [] if target_35_4000_row is None else target_35_4000_row["failure_reasons"],
        "rows": rows,
    }
    if not feasible_35:
        summary_payload["target_35_status"] = "infeasible_under_current_locked_constraints"
    else:
        summary_payload["target_35_status"] = "feasible_under_current_locked_constraints"

    csv_path = output_dir / "thrust_rpm_feasibility_sweep.csv"
    json_path = output_dir / "thrust_rpm_feasibility_sweep_summary.json"
    fields = [
        "target_thrust_N",
        "RPM",
        "actual_thrust_N",
        "thrust_error_percent",
        "thrust_error_pct",
        "torque_Nm",
        "power_W",
        "CT",
        "CQ",
        "CP",
        "max_alpha_deg",
        "min_alpha_deg",
        "max_Cl",
        "Cl_clipping_fraction_pct",
        "alpha_clipping_fraction_pct",
        "negative_loading_station_count",
        "tip_loading_fraction",
        "inboard_loading_fraction",
        "max_positive_twist_bump_deg",
        "max_abs_twist_gradient_deg_per_mu",
        "max_twist_curvature",
        "manufacturable_twist_pass",
        "valid_for_thesis_export",
        "failure_reason",
        "failure_reasons",
        "requested_gmt_aggressiveness",
        "achieved_gmt_aggressiveness",
        "recommended_next_action",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            csv_row = dict(row)
            csv_row["failure_reasons"] = ";".join(str(reason) for reason in row["failure_reasons"])
            writer.writerow(csv_row)
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary_payload, handle, indent=2, default=_json_default)
        handle.write("\n")
    plot_warnings: List[str] = []
    if make_plots:
        plot_warnings = plot_feasibility_sweep(rows, figure_dir or output_dir)
        if plot_warnings:
            summary_payload["plot_warnings"] = plot_warnings
            with json_path.open("w", encoding="utf-8") as handle:
                json.dump(summary_payload, handle, indent=2, default=_json_default)
                handle.write("\n")
    write_metadata_sidecar(
        csv_path,
        _base_output_metadata(
            case_name="thrust_rpm_feasibility_sweep",
            geometry_source_csv="",
            station_source_csv=csv_path,
            twist_column_used="twist_deg",
            aero_source=MODEL_SOURCE_OPTIMIZER,
        ),
    )
    write_metadata_sidecar(
        json_path,
        _base_output_metadata(
            case_name="thrust_rpm_feasibility_sweep",
            geometry_source_csv="",
            station_source_csv=csv_path,
            twist_column_used="twist_deg",
            aero_source=MODEL_SOURCE_OPTIMIZER,
        ),
    )
    return {
        "summary": summary_payload,
        "csv_path": csv_path,
        "json_path": json_path,
        "results": results,
        "plot_warnings": plot_warnings,
    }


def plot_feasibility_sweep(rows: Sequence[dict[str, object]], output_dir: Path) -> List[str]:
    assert_geometry_output_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        return [
            "matplotlib unavailable; feasibility plots were not generated "
            f"({exc}). Install with: python -m pip install matplotlib"
        ]

    apply_matplotlib_style(plt)
    valid_rows = [row for row in rows if bool(row.get("valid_for_thesis_export"))]
    plot_rows = valid_rows or list(rows)

    def series(field: str, source_rows: Sequence[dict[str, object]] = plot_rows) -> List[float]:
        values: List[float] = []
        for row in source_rows:
            try:
                values.append(float(row[field]))
            except (KeyError, TypeError, ValueError):
                values.append(math.nan)
        return values

    def save_scatter(stem: str, x_field: str, y_field: str, xlabel: str, ylabel: str) -> None:
        fig = plt.figure()
        plt.scatter(series(x_field), series(y_field))
        plt.xlabel(xlabel)
        plt.ylabel(ylabel)
        fig.tight_layout()
        save_matplotlib_figure(fig, output_dir, stem, overwrite=True)
        write_metadata_sidecar(
            output_dir / f"{stem}.png",
            _base_output_metadata(
                case_name="thrust_rpm_feasibility_sweep",
                geometry_source_csv="",
                station_source_csv="thrust_rpm_feasibility_sweep.csv",
                twist_column_used="twist_deg",
                aero_source=MODEL_SOURCE_OPTIMIZER,
            ),
        )
        plt.close(fig)

    save_scatter("valid_thrust_vs_RPM", "RPM", "actual_thrust_N", "RPM", "valid thrust [N]")
    save_scatter("torque_vs_thrust", "actual_thrust_N", "torque_Nm", "thrust [N]", "torque [N*m]")
    save_scatter("power_vs_thrust", "actual_thrust_N", "power_W", "thrust [N]", "power [W]")
    save_scatter(
        "Cl_clipping_fraction_vs_thrust",
        "actual_thrust_N",
        "Cl_clipping_fraction_pct",
        "thrust [N]",
        "Cl clipping fraction [%]",
    )
    save_scatter(
        "alpha_clipping_fraction_vs_thrust",
        "actual_thrust_N",
        "alpha_clipping_fraction_pct",
        "thrust [N]",
        "alpha clipping fraction [%]",
    )

    fig = plt.figure()
    validity = [1.0 if bool(row.get("valid_for_thesis_export")) else 0.0 for row in rows]
    gmt = []
    for row in rows:
        try:
            gmt.append(float(row["achieved_gmt_aggressiveness"]))
        except (KeyError, TypeError, ValueError):
            gmt.append(math.nan)
    plt.scatter(gmt, validity)
    plt.xlabel("achieved GMT aggressiveness")
    plt.ylabel("validity flag")
    fig.tight_layout()
    save_matplotlib_figure(fig, output_dir, "GMT_aggressiveness_vs_validity", overwrite=True)
    write_metadata_sidecar(
        output_dir / "GMT_aggressiveness_vs_validity.png",
        _base_output_metadata(
            case_name="thrust_rpm_feasibility_sweep",
            geometry_source_csv="",
            station_source_csv="thrust_rpm_feasibility_sweep.csv",
            twist_column_used="twist_deg",
            aero_source=MODEL_SOURCE_OPTIMIZER,
        ),
    )
    plt.close(fig)
    return []


def startup_dependency_warnings(args: argparse.Namespace) -> List[str]:
    warnings: List[str] = []
    if args.optimizer in {"auto", "scipy"}:
        try:
            import scipy.optimize  # noqa: F401
        except Exception as exc:
            warnings.append(
                "scipy is unavailable; the solver will fall back to coordinate_search "
                f"({exc}). Install with: python -m pip install scipy"
            )
    if args.make_plots:
        try:
            import matplotlib.pyplot  # noqa: F401
        except Exception as exc:
            warnings.append(
                "matplotlib is unavailable; plots will be skipped "
                f"({exc}). Install with: python -m pip install matplotlib"
            )
    return warnings


def main() -> None:
    args = parse_args()
    dependency_warnings = startup_dependency_warnings(args)
    for warning in dependency_warnings:
        print(warning)
    config = DesignInputs(
        preset=args.preset or "",
        blades=args.blades,
        radius_m=diameter_in_to_radius_m(args.diameter_in),
        hub_radius_fraction=args.hub_radius_fraction,
        rpm=args.rpm,
        freestream_mps=args.v_inf,
        thrust_target_n=args.thrust,
        rho=args.rho,
        spanload_type=args.spanload,
        mode=args.mode,
        gmt_aggressiveness=parse_gmt_aggressiveness(args.gmt_aggressiveness),
        chord_min_in=args.chord_min_in,
        chord_max_in=args.chord_max_in,
        twist_min_deg=args.twist_min_deg,
        twist_max_deg=args.twist_max_deg,
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
        manufacturable_twist=args.manufacturable_twist,
        max_positive_twist_bump_deg=args.max_positive_twist_bump,
        max_abs_twist_gradient_deg_per_mu_limit=args.max_twist_gradient,
        max_twist_curvature_limit=args.max_twist_curvature,
        max_cl_limit_fraction_pct=args.max_cl_limit_fraction_pct,
        max_alpha_limit_fraction_pct=args.max_alpha_limit_fraction_pct,
        allowed_airfoils=parse_allowed_airfoils(args.allowed_airfoils),
    )
    validate_user_inputs(config)

    fallback_reference_path = config.reference_csv_path
    reference_variant = "mil_22" if config.spanload_type == "minimum_induced_loss" else "gmt_22"
    reference_stations, reference_source, input_warnings = load_reference_geometry(
        config.reference_geometry_csv,
        fallback_reference_path,
        reference_variant,
    )
    input_warnings = dedupe_warnings(list(input_warnings) + dependency_warnings)
    result = optimize_geometry(config, reference_stations, input_warnings, reference_source)

    optimized_name = optimized_case_name(config.spanload_type, config.manufacturable_twist)
    reference_name = reference_case_name(config.spanload_type)
    output_dir = config.output_directory
    assert_geometry_output_path(output_dir)
    figure_dir = (
        resolve_project_output_path(args.figures_dir)
        if args.figures_dir is not None
        else get_figures_dir(
            "geometry_optimizer",
            model_source=MODEL_SOURCE_OPTIMIZER,
            case_name=optimized_name,
        )
    )
    assert_geometry_output_path(figure_dir)
    reference_dir = get_results_dir(
        "geometry_optimizer",
        model_source=MODEL_SOURCE_OPTIMIZER,
        case_name=reference_name,
    )
    audits_dir = get_results_dir(
        "geometry_optimizer",
        model_source=MODEL_SOURCE_OPTIMIZER,
        case_name="audits",
    )
    geometry_root = get_results_dir(
        "geometry_optimizer",
        model_source=MODEL_SOURCE_OPTIMIZER,
    )
    optimized_csv = output_dir / optimizer_geometry_csv_name(optimized_name)
    optimized_xlsx = output_dir / f"{optimized_name}.xlsx"
    diagnostic_csv = output_dir / f"{optimized_name}_diagnostics.csv"
    reference_csv = reference_dir / optimizer_geometry_csv_name(reference_name)
    history_csv = output_dir / f"{optimized_name}_convergence_history.csv"
    summary_json = output_dir / f"{optimized_name}_summary.json"
    case_history_csv = output_dir / "convergence_history.csv"
    case_summary_json = output_dir / "solver_summary.json"
    methodology_txt = output_dir / "methodology_statement.txt"

    write_csv(result, optimized_csv)
    write_diagnostic_csv(result, diagnostic_csv)
    write_reference_csv(reference_stations, config.radius_m, reference_csv)
    write_convergence_history_csv(result.convergence_history, history_csv)
    write_convergence_history_csv(result.convergence_history, case_history_csv)
    plot_warnings: List[str] = []
    if config.make_plots:
        plot_warnings = plot_thesis_outputs(result, reference_stations, figure_dir, optimized_csv)
        if plot_warnings:
            result = replace(result, warnings=dedupe_warnings(list(result.warnings) + plot_warnings))
    write_summary_json(result, summary_json)
    write_summary_json(result, case_summary_json)
    write_methodology_statement(methodology_txt, config)
    audit_reference_path = config.reference_csv_path
    if audit_reference_path.suffix.lower() not in {".xlsx", ".xlsm"}:
        audit_reference_path = default_reference_csv_path()
    if audit_reference_path.exists() and audit_reference_path.suffix.lower() in {".xlsx", ".xlsm"}:
        write_scaled_reference_geometry_audit(audits_dir, audit_reference_path, geometry_root)
    sweep_outputs: dict[str, Any] | None = None
    if args.thrust_validity_sweep:
        sweep_outputs = run_thrust_validity_sweep(
            config,
            reference_stations,
            input_warnings,
            reference_source,
            rpms=THRUST_RPM_SWEEP_RPMS,
            audits_dir=audits_dir,
            figure_dir=figure_dir / "feasibility_sweep",
            make_plots=config.make_plots,
        )
    try:
        write_xlsx(result, reference_stations, optimized_xlsx)
        xlsx_written = True
    except PermissionError as exc:
        xlsx_written = False
        result = replace(
            result,
            warnings=dedupe_warnings(
                list(result.warnings)
                + [f"could not write Excel workbook because the file is locked or not writable: {exc}"]
            ),
        )
        write_summary_json(result, summary_json)
        write_summary_json(result, case_summary_json)

    print_summary(result)
    print()
    print(f"Wrote optimized geometry CSV to: {optimized_csv}")
    print(f"Wrote diagnostic station CSV to: {diagnostic_csv}")
    if xlsx_written:
        print(f"Wrote optimized geometry Excel workbook to: {optimized_xlsx}")
    else:
        print(f"Skipped optimized geometry Excel workbook because it is locked: {optimized_xlsx}")
    print(f"Wrote reference geometry CSV to: {reference_csv}")
    print(f"Wrote convergence history CSV to: {history_csv}")
    print(f"Wrote solver summary JSON to: {summary_json}")
    print(f"Wrote case solver summary JSON to: {case_summary_json}")
    print(f"Wrote methodology statement to: {methodology_txt}")
    if sweep_outputs is not None:
        print(f"Wrote thrust/RPM feasibility sweep CSV to: {sweep_outputs['csv_path']}")
        print(f"Wrote thrust/RPM feasibility sweep summary JSON to: {sweep_outputs['json_path']}")
        if sweep_outputs.get("plot_warnings"):
            for warning in sweep_outputs["plot_warnings"]:
                print(f"Plot warning: {warning}")
    if config.make_plots and not plot_warnings:
        print(f"Wrote thesis plots to: {figure_dir}")


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
