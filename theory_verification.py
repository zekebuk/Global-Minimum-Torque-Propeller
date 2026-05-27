from __future__ import annotations

import argparse
import csv
import html
import json
import math
import struct
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence


CODE_DIR = Path(__file__).resolve().parent
ROOT_DIR = CODE_DIR.parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from Geometry_optimization import (  # noqa: E402
    AIRFOIL_LIBRARY,
    DEFAULT_REFERENCE_MAX_CHORD_MARGIN,
    DesignInputs,
    DesignResult,
    NASA_SCITECH_ORIGINAL_OPERATING_POINT,
    REFERENCE_VARIANT_CHOICES,
    SCALED_REFERENCE_STATEMENT,
    THESIS_22_STATIC_OPERATING_POINT,
    StationResult,
    builtin_reference_stations,
    default_reference_csv_path,
    diameter_in_to_radius_m,
    load_reference_geometry,
    meters_to_inches,
    normalize_reference_variant,
    optimize_geometry,
    optimized_case_name,
    optimizer_geometry_csv_name,
    reference_case_name,
    result_summary_dict,
    validate_user_inputs,
    write_convergence_history_csv,
    write_csv,
    write_summary_json,
)
from common_plot_style import apply_matplotlib_style, save_matplotlib_figure  # noqa: E402
from output_paths import get_figures_dir, get_results_dir, assert_verification_output_path, write_metadata_sidecar  # noqa: E402


DEFAULT_THRUST_MATCH_TOL_PCT = 5.0
DEFAULT_TORQUE_REDUCTION_TOL_PCT = 0.0
DEFAULT_TIP_REDUCTION_TOL_PCT = 10.0
DEFAULT_CHORD_TOL_M = 1.0e-6
GENERATING_SCRIPT = "theory_verification.py"
CORE_THEORY_CHECKS = {
    "same thrust target",
    "actual thrust match",
    "fixed rpm",
    "same diameter",
    "same air density",
    "lower total torque at same thrust and rpm",
    "lower shaft power at fixed rpm",
    "reduced integrated tip loading",
    "inboard thrust fraction increased",
    "inboard-shifted cumulative thrust",
    "spanload shape proof uses nonnegative thrust",
    "MIL closer to elliptical target than GMT",
    "GMT closer to bell target than MIL",
    "GMT lower center of thrust",
    "GMT lower tip thrust fraction",
    "GMT cumulative thrust builds earlier inboard",
    "valid hover induced velocity",
    "acceptable Cl clipping fraction",
}
VERIFICATION_OUTCOMES = {"PASS", "PARTIAL PASS", "FAIL"}


@dataclass(frozen=True)
class SolverCase:
    name: str
    spanload_type: str
    reference_variant: str
    result: DesignResult
    reference_source: str
    output_dir: Path


@dataclass(frozen=True)
class SpanwiseData:
    mu: list[float]
    edge_left: list[float]
    edge_right: list[float]
    dmu: list[float]
    radius_m: list[float]
    chord_m: list[float]
    twist_deg: list[float]
    cl: list[float]
    dthrust_n: list[float]
    dtorque_nm: list[float]
    thrust_loading_n: list[float]
    torque_loading_nm: list[float]
    cumulative_thrust_fraction: list[float]
    cumulative_torque_fraction: list[float]
    airfoils: list[str]


@dataclass(frozen=True)
class VerificationMetrics:
    thrust_match_error_pct: float
    torque_reduction_pct: float
    power_reduction_pct: float
    eta_change_pct: float
    tip_loading_reduction_pct: float
    inboard_loading_change_pct: float
    inboard_fraction_mil: float
    inboard_fraction_gmt: float
    tip_fraction_mil: float
    tip_fraction_gmt: float
    cumulative_50_radius_mil: float
    cumulative_50_radius_gmt: float
    max_chord_diff_m: float
    max_chord_diff_pct_radius: float
    max_twist_diff_deg: float
    ct_mil: float
    cq_mil: float
    cp_mil: float
    ct_gmt: float
    cq_gmt: float
    cp_gmt: float
    eta_mil: float
    eta_gmt: float
    cl_clipping_fraction_mil_pct: float
    cl_clipping_fraction_gmt_pct: float


@dataclass(frozen=True)
class SpanloadShapeCaseMetrics:
    source_csv: Path
    mu_root: float
    mu: list[float]
    dmu: list[float]
    normalized_loading: list[float]
    elliptical_target: list[float]
    bell_target: list[float]
    cumulative_thrust_fraction: list[float]
    rms_error_elliptical: float
    rms_error_bell: float
    correlation_elliptical: float
    correlation_bell: float
    center_of_thrust_mu: float
    tip_thrust_fraction_mu_gt_085: float
    cumulative_thrust_fraction_at_070: float
    cumulative_thrust_fraction_at_080: float
    cumulative_thrust_fraction_at_090: float
    negative_thrust_station_count: int
    min_dthrust_n: float
    negative_cl_station_count: int
    min_cl: float
    cumulative_thrust_overshoot_before_final: bool
    cumulative_thrust_overshoot_max_before_final: float


@dataclass(frozen=True)
class SpanloadShapeComparison:
    mil: SpanloadShapeCaseMetrics
    gmt: SpanloadShapeCaseMetrics
    common_mu: list[float]
    common_mil_normalized_loading: list[float]
    common_gmt_normalized_loading: list[float]
    common_elliptical_target: list[float]
    common_bell_target: list[float]
    mil_closer_to_elliptical_than_gmt: bool
    gmt_closer_to_bell_than_mil: bool
    gmt_lower_center_of_thrust_mu: bool
    gmt_lower_tip_thrust_fraction_mu_gt_085: bool
    gmt_cumulative_thrust_builds_earlier_inboard: bool
    valid_for_spanload_shape_proof: bool


@dataclass(frozen=True)
class VerificationCheck:
    name: str
    status: str
    value: str
    criterion: str
    notes: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "PASS"


@dataclass(frozen=True)
class VerificationResult:
    mil: SolverCase
    gmt: SolverCase
    mil_span: SpanwiseData
    gmt_span: SpanwiseData
    common_grid: list[float]
    common_mil: dict[str, list[float]]
    common_gmt: dict[str, list[float]]
    metrics: VerificationMetrics
    spanload_shape: SpanloadShapeComparison
    checks: list[VerificationCheck]
    warnings: list[str]
    assumptions: list[str]
    results_dir: Path
    figures_dir: Path

    @property
    def theory_verified(self) -> bool:
        for check in self.checks:
            if check.name in CORE_THEORY_CHECKS and check.status == "FAIL":
                return False
        return True

    @property
    def outcome(self) -> str:
        return classify_verification_checks(self.checks)


@dataclass(frozen=True)
class GeometryCsvMetrics:
    path: Path
    thrust_n: float
    torque_nm: float
    power_w: float
    eta_static: float
    ct: float
    cq: float
    cp: float
    cl_clipping_fraction: float
    tip_thrust_fraction: float
    inboard_thrust_fraction: float
    cumulative_50_thrust_radius: float


def classify_verification_checks(checks: Sequence[VerificationCheck]) -> str:
    statuses = {check.name: check.status for check in checks}
    hard_fail_checks = {
        "same thrust target",
        "actual thrust match",
        "fixed rpm",
        "same diameter",
        "same air density",
        "same blade count",
        "same chord distribution",
        "same airfoil schedule",
        "lower total torque at same thrust and rpm",
        "valid hover induced velocity",
    }
    if any(statuses.get(name) == "FAIL" for name in hard_fail_checks):
        return "FAIL"

    torque_only_checks = {
        "actual thrust match",
        "lower total torque at same thrust and rpm",
        "lower shaft power at fixed rpm",
    }
    shape_checks = {
        "inboard thrust fraction increased",
        "reduced integrated tip loading",
        "inboard-shifted cumulative thrust",
        "spanload shape proof uses nonnegative thrust",
        "MIL closer to elliptical target than GMT",
        "GMT closer to bell target than MIL",
        "GMT lower center of thrust",
        "GMT lower tip thrust fraction",
        "GMT cumulative thrust builds earlier inboard",
        "acceptable Cl clipping fraction",
    }
    if all(statuses.get(name) == "PASS" for name in torque_only_checks):
        if any(statuses.get(name) == "FAIL" for name in shape_checks):
            return "PARTIAL PASS"
        if all(statuses.get(name) == "PASS" for name in shape_checks):
            return "PASS"
    return "FAIL"


def rpm_to_rad_s(rpm: float) -> float:
    return rpm * 2.0 * math.pi / 60.0


def shaft_power_w(torque_nm: float, omega_rad_s: float) -> float:
    return torque_nm * omega_rad_s


def propeller_coefficients(
    thrust_n: float,
    torque_nm: float,
    rho_kg_m3: float,
    rpm: float,
    diameter_m: float,
) -> tuple[float, float, float]:
    n_rev_per_s = rpm / 60.0
    denominator_ct = rho_kg_m3 * n_rev_per_s**2 * diameter_m**4
    denominator_cq = rho_kg_m3 * n_rev_per_s**2 * diameter_m**5
    denominator_cp = rho_kg_m3 * n_rev_per_s**3 * diameter_m**5
    ct = safe_div(thrust_n, denominator_ct)
    cq = safe_div(torque_nm, denominator_cq)
    cp = safe_div(shaft_power_w(torque_nm, rpm_to_rad_s(rpm)), denominator_cp)
    return ct, cq, cp


def safe_div(numerator: float, denominator: float, fallback: float = 0.0) -> float:
    if abs(denominator) <= 1.0e-12:
        return fallback
    return numerator / denominator


def pct_change(new_value: float, reference_value: float) -> float:
    return safe_div(new_value - reference_value, reference_value) * 100.0


def reduction_pct(reference_value: float, candidate_value: float) -> float:
    return safe_div(reference_value - candidate_value, reference_value) * 100.0


def close_enough(a: float, b: float, tolerance: float) -> bool:
    return abs(a - b) <= tolerance


def station_edges(mu_values: Sequence[float], hub_mu: float, tip_mu: float = 1.0) -> tuple[list[float], list[float]]:
    if not mu_values:
        return [], []
    left_edges: list[float] = []
    right_edges: list[float] = []
    for idx, mu in enumerate(mu_values):
        left = hub_mu if idx == 0 else 0.5 * (mu_values[idx - 1] + mu)
        right = tip_mu if idx == len(mu_values) - 1 else 0.5 * (mu + mu_values[idx + 1])
        if right <= left:
            half_width = 0.5 * max(abs(right - left), 1.0e-6)
            left = mu - half_width
            right = mu + half_width
        left_edges.append(left)
        right_edges.append(right)
    return left_edges, right_edges


def cumulative_fraction(values: Sequence[float]) -> list[float]:
    total = sum(values)
    if abs(total) <= 1.0e-12:
        return [0.0 for _ in values]
    running = 0.0
    cumulative: list[float] = []
    for value in values:
        running += value
        cumulative.append(running / total)
    if cumulative:
        cumulative[-1] = 1.0
    return cumulative


def integrate_annular_band(
    mu_values: Sequence[float],
    station_integrals: Sequence[float],
    lower_mu: float,
    upper_mu: float,
    hub_mu: float = 0.2,
    tip_mu: float = 1.0,
) -> float:
    if lower_mu >= upper_mu:
        raise ValueError("lower_mu must be below upper_mu")
    left_edges, right_edges = station_edges(mu_values, hub_mu, tip_mu)
    total = 0.0
    for left, right, station_total in zip(left_edges, right_edges, station_integrals):
        width = max(right - left, 1.0e-12)
        overlap = max(0.0, min(right, upper_mu) - max(left, lower_mu))
        if overlap > 0.0:
            total += station_total * overlap / width
    return total


def cumulative_radius_at_fraction(
    mu_values: Sequence[float],
    station_integrals: Sequence[float],
    target_fraction: float,
    hub_mu: float = 0.2,
    tip_mu: float = 1.0,
) -> float:
    if not mu_values:
        raise ValueError("cannot compute cumulative radius without stations")
    total = sum(station_integrals)
    if total <= 0.0:
        raise ValueError("cumulative radius requires positive total loading")
    target = total * target_fraction
    left_edges, right_edges = station_edges(mu_values, hub_mu, tip_mu)
    running = 0.0
    for left, right, station_total in zip(left_edges, right_edges, station_integrals):
        next_running = running + station_total
        if next_running >= target:
            fraction_inside_station = safe_div(target - running, station_total)
            return left + max(0.0, min(1.0, fraction_inside_station)) * (right - left)
        running = next_running
    return tip_mu


def interpolate_series(
    source_x: Sequence[float],
    source_y: Sequence[float],
    target_x: Sequence[float],
) -> list[float]:
    if len(source_x) != len(source_y):
        raise ValueError("source_x and source_y must have the same length")
    if not source_x:
        raise ValueError("cannot interpolate an empty series")
    pairs = sorted(zip(source_x, source_y), key=lambda item: item[0])
    x_values = [item[0] for item in pairs]
    y_values = [item[1] for item in pairs]
    interpolated: list[float] = []
    for x in target_x:
        if x <= x_values[0]:
            interpolated.append(y_values[0])
            continue
        if x >= x_values[-1]:
            interpolated.append(y_values[-1])
            continue
        upper_idx = 1
        while upper_idx < len(x_values) and x_values[upper_idx] < x:
            upper_idx += 1
        x0 = x_values[upper_idx - 1]
        x1 = x_values[upper_idx]
        y0 = y_values[upper_idx - 1]
        y1 = y_values[upper_idx]
        weight = safe_div(x - x0, x1 - x0)
        interpolated.append(y0 + weight * (y1 - y0))
    return interpolated


def common_grid_for(a: Sequence[float], b: Sequence[float], tolerance: float = 1.0e-9) -> tuple[list[float], bool]:
    identical = len(a) == len(b) and all(abs(left - right) <= tolerance for left, right in zip(a, b))
    if identical:
        return list(a), True

    merged = sorted(float(value) for value in list(a) + list(b))
    unique: list[float] = []
    for value in merged:
        if not unique or abs(value - unique[-1]) > tolerance:
            unique.append(value)
    return unique, False


def loading_reduction_pct(
    mu_values: Sequence[float],
    mil_dthrust: Sequence[float],
    gmt_dthrust: Sequence[float],
    lower_mu: float,
    upper_mu: float,
    hub_mu: float = 0.2,
) -> float:
    mil_band = integrate_annular_band(mu_values, mil_dthrust, lower_mu, upper_mu, hub_mu)
    gmt_band = integrate_annular_band(mu_values, gmt_dthrust, lower_mu, upper_mu, hub_mu)
    return reduction_pct(mil_band, gmt_band)


def weighted_rms_error(actual: Sequence[float], target: Sequence[float], weights: Sequence[float]) -> float:
    denominator = sum(weights)
    if denominator <= 0.0:
        return float("nan")
    return math.sqrt(sum((left - right) ** 2 * weight for left, right, weight in zip(actual, target, weights)) / denominator)


def weighted_correlation(left: Sequence[float], right: Sequence[float], weights: Sequence[float]) -> float:
    denominator = sum(weights)
    if denominator <= 0.0:
        return float("nan")
    left_mean = sum(value * weight for value, weight in zip(left, weights)) / denominator
    right_mean = sum(value * weight for value, weight in zip(right, weights)) / denominator
    covariance = sum((a - left_mean) * (b - right_mean) * weight for a, b, weight in zip(left, right, weights))
    left_variance = sum((value - left_mean) ** 2 * weight for value, weight in zip(left, weights))
    right_variance = sum((value - right_mean) ** 2 * weight for value, weight in zip(right, weights))
    scale = math.sqrt(left_variance * right_variance)
    return safe_div(covariance, scale, float("nan"))


def normalize_loading_density(dthrust_n: Sequence[float], dmu: Sequence[float]) -> list[float]:
    total_thrust = sum(dthrust_n)
    if abs(total_thrust) <= 1.0e-12:
        return [0.0 for _ in dthrust_n]
    return [safe_div(dthrust, width) / total_thrust for dthrust, width in zip(dthrust_n, dmu)]


def normalize_density_to_unit_integral(values: Sequence[float], dmu: Sequence[float]) -> list[float]:
    integral = sum(value * width for value, width in zip(values, dmu))
    if abs(integral) <= 1.0e-12:
        return [0.0 for _ in values]
    return [value / integral for value in values]


def normalized_target_shapes(mu_values: Sequence[float], dmu: Sequence[float], mu_root: float) -> tuple[list[float], list[float]]:
    ell_raw: list[float] = []
    bell_raw: list[float] = []
    active_span = max(1.0 - mu_root, 1.0e-12)
    for mu in mu_values:
        s = min(1.0, max(0.0, (mu - mu_root) / active_span))
        ell = math.sqrt(max(0.0, 1.0 - s * s))
        bell = max(0.0, 1.0 - s * s) ** 1.5
        ell_raw.append(ell)
        bell_raw.append(bell)
    return normalize_density_to_unit_integral(ell_raw, dmu), normalize_density_to_unit_integral(bell_raw, dmu)


def cumulative_thrust_fraction_at_mu(
    mu_values: Sequence[float],
    dthrust_n: Sequence[float],
    target_mu: float,
    mu_root: float,
) -> float:
    total = sum(dthrust_n)
    if abs(total) <= 1.0e-12:
        return 0.0
    if target_mu <= mu_root:
        return 0.0
    if target_mu >= 1.0:
        return 1.0
    return integrate_annular_band(mu_values, dthrust_n, mu_root, target_mu, mu_root) / total


def _csv_float(row: Mapping[str, object], *names: str, default: float = 0.0) -> float:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return float(value)
    return default


def spanload_shape_case_from_csv(path: Path) -> SpanloadShapeCaseMetrics:
    if not path.exists():
        raise FileNotFoundError(f"spanload shape CSV not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"spanload shape CSV is empty: {path}")

    mu = [_csv_float(row, "mu", "r_R") for row in rows]
    dthrust = [_csv_float(row, "dthrust_n", "dT_N") for row in rows]
    cl = [_csv_float(row, "cl", "Cl") for row in rows]
    mu_root = mu[0]
    left_edges, right_edges = station_edges(mu, mu_root)
    dmu = [right - left for left, right in zip(left_edges, right_edges)]
    normalized_loading = normalize_loading_density(dthrust, dmu)
    elliptical_target, bell_target = normalized_target_shapes(mu, dmu, mu_root)
    cumulative = cumulative_fraction(dthrust)
    cumulative_before_final = cumulative[:-1]
    total_thrust = sum(dthrust)
    center_of_thrust = safe_div(sum(mu_i * dthrust_i for mu_i, dthrust_i in zip(mu, dthrust)), total_thrust, float("nan"))
    tip_thrust = integrate_annular_band(mu, dthrust, 0.85, 1.0, mu_root)
    return SpanloadShapeCaseMetrics(
        source_csv=path,
        mu_root=mu_root,
        mu=mu,
        dmu=dmu,
        normalized_loading=normalized_loading,
        elliptical_target=elliptical_target,
        bell_target=bell_target,
        cumulative_thrust_fraction=cumulative,
        rms_error_elliptical=weighted_rms_error(normalized_loading, elliptical_target, dmu),
        rms_error_bell=weighted_rms_error(normalized_loading, bell_target, dmu),
        correlation_elliptical=weighted_correlation(normalized_loading, elliptical_target, dmu),
        correlation_bell=weighted_correlation(normalized_loading, bell_target, dmu),
        center_of_thrust_mu=center_of_thrust,
        tip_thrust_fraction_mu_gt_085=safe_div(tip_thrust, total_thrust),
        cumulative_thrust_fraction_at_070=cumulative_thrust_fraction_at_mu(mu, dthrust, 0.70, mu_root),
        cumulative_thrust_fraction_at_080=cumulative_thrust_fraction_at_mu(mu, dthrust, 0.80, mu_root),
        cumulative_thrust_fraction_at_090=cumulative_thrust_fraction_at_mu(mu, dthrust, 0.90, mu_root),
        negative_thrust_station_count=sum(value < 0.0 for value in dthrust),
        min_dthrust_n=min(dthrust, default=0.0),
        negative_cl_station_count=sum(value < -0.02 for value in cl),
        min_cl=min(cl, default=0.0),
        cumulative_thrust_overshoot_before_final=any(value > 1.02 for value in cumulative_before_final),
        cumulative_thrust_overshoot_max_before_final=max(cumulative_before_final, default=0.0),
    )


def spanload_shape_comparison_from_csv(mil_csv: Path, gmt_csv: Path) -> SpanloadShapeComparison:
    mil = spanload_shape_case_from_csv(mil_csv)
    gmt = spanload_shape_case_from_csv(gmt_csv)
    common_mu, _ = common_grid_for(mil.mu, gmt.mu)
    common_mu_root = common_mu[0]
    common_left, common_right = station_edges(common_mu, common_mu_root)
    common_dmu = [right - left for left, right in zip(common_left, common_right)]
    mil_loading = normalize_density_to_unit_integral(
        interpolate_series(mil.mu, mil.normalized_loading, common_mu),
        common_dmu,
    )
    gmt_loading = normalize_density_to_unit_integral(
        interpolate_series(gmt.mu, gmt.normalized_loading, common_mu),
        common_dmu,
    )
    elliptical_target, bell_target = normalized_target_shapes(common_mu, common_dmu, common_mu_root)
    return SpanloadShapeComparison(
        mil=mil,
        gmt=gmt,
        common_mu=common_mu,
        common_mil_normalized_loading=mil_loading,
        common_gmt_normalized_loading=gmt_loading,
        common_elliptical_target=elliptical_target,
        common_bell_target=bell_target,
        mil_closer_to_elliptical_than_gmt=mil.rms_error_elliptical < gmt.rms_error_elliptical,
        gmt_closer_to_bell_than_mil=gmt.rms_error_bell < mil.rms_error_bell,
        gmt_lower_center_of_thrust_mu=gmt.center_of_thrust_mu < mil.center_of_thrust_mu,
        gmt_lower_tip_thrust_fraction_mu_gt_085=gmt.tip_thrust_fraction_mu_gt_085 < mil.tip_thrust_fraction_mu_gt_085,
        gmt_cumulative_thrust_builds_earlier_inboard=(
            gmt.cumulative_thrust_fraction_at_070 >= mil.cumulative_thrust_fraction_at_070
            and gmt.cumulative_thrust_fraction_at_080 >= mil.cumulative_thrust_fraction_at_080
            and gmt.cumulative_thrust_fraction_at_090 >= mil.cumulative_thrust_fraction_at_090
        ),
        valid_for_spanload_shape_proof=(
            mil.negative_thrust_station_count == 0
            and gmt.negative_thrust_station_count == 0
            and not mil.cumulative_thrust_overshoot_before_final
            and not gmt.cumulative_thrust_overshoot_before_final
        ),
    )


def spanwise_from_result(result: DesignResult) -> SpanwiseData:
    stations = sorted(result.stations, key=lambda station: station.mu)
    mu = [station.mu for station in stations]
    left_edges, right_edges = station_edges(mu, result.config.hub_radius_fraction)
    dmu = [right - left for left, right in zip(left_edges, right_edges)]
    dthrust = [station.dthrust_n for station in stations]
    dtorque = [station.dtorque_nm for station in stations]
    thrust_loading = [safe_div(value, width) for value, width in zip(dthrust, dmu)]
    torque_loading = [safe_div(value, width) for value, width in zip(dtorque, dmu)]
    return SpanwiseData(
        mu=mu,
        edge_left=left_edges,
        edge_right=right_edges,
        dmu=dmu,
        radius_m=[station.radius_m for station in stations],
        chord_m=[station.chord_m for station in stations],
        twist_deg=[station.twist_deg for station in stations],
        cl=[station.cl for station in stations],
        dthrust_n=dthrust,
        dtorque_nm=dtorque,
        thrust_loading_n=thrust_loading,
        torque_loading_nm=torque_loading,
        cumulative_thrust_fraction=cumulative_fraction(dthrust),
        cumulative_torque_fraction=cumulative_fraction(dtorque),
        airfoils=[station.airfoil for station in stations],
    )


def interpolate_spanwise(span: SpanwiseData, common_mu: Sequence[float]) -> dict[str, list[float]]:
    return {
        "chord_m": interpolate_series(span.mu, span.chord_m, common_mu),
        "twist_deg": interpolate_series(span.mu, span.twist_deg, common_mu),
        "cl": interpolate_series(span.mu, span.cl, common_mu),
        "thrust_loading_n": interpolate_series(span.mu, span.thrust_loading_n, common_mu),
        "torque_loading_nm": interpolate_series(span.mu, span.torque_loading_nm, common_mu),
        "cumulative_thrust_fraction": interpolate_series(span.mu, span.cumulative_thrust_fraction, common_mu),
        "cumulative_torque_fraction": interpolate_series(span.mu, span.cumulative_torque_fraction, common_mu),
    }


def select_efficiency(result: DesignResult) -> tuple[float, str]:
    if result.j_advance > 1.0e-9:
        return result.eta_dynamic, "dynamic efficiency J*CT/CP"
    return result.eta_static, "static ideal efficiency proxy"


def assert_positive_finite(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be positive and finite; got {value!r}")


def validate_units_and_station_data(case: SolverCase) -> list[str]:
    result = case.result
    assert_positive_finite(f"{case.name} radius_m", result.config.radius_m)
    assert_positive_finite(f"{case.name} rpm", result.config.rpm)
    assert_positive_finite(f"{case.name} rho", result.config.rho)
    assert_positive_finite(f"{case.name} thrust", result.thrust_n)
    assert_positive_finite(f"{case.name} torque", result.torque_nm)
    assert_positive_finite(f"{case.name} power", result.power_w)
    if result.config.blades < 1:
        raise ValueError(f"{case.name} blade count must be positive")
    if not result.stations:
        raise ValueError(f"{case.name} produced no radial stations")

    warnings: list[str] = []
    omega = rpm_to_rad_s(result.config.rpm)
    expected_power = shaft_power_w(result.torque_nm, omega)
    if not close_enough(result.power_w, expected_power, max(1.0e-8, 1.0e-8 * abs(expected_power))):
        warnings.append(
            f"{case.name} solver power {result.power_w:.9g} W does not equal torque*omega "
            f"{expected_power:.9g} W"
        )

    station_thrust = sum(station.dthrust_n for station in result.stations)
    station_torque = sum(station.dtorque_nm for station in result.stations)
    if not close_enough(station_thrust, result.thrust_n, max(1.0e-8, 1.0e-8 * abs(result.thrust_n))):
        warnings.append(f"{case.name} station thrust does not sum to total thrust")
    if not close_enough(station_torque, result.torque_nm, max(1.0e-8, 1.0e-8 * abs(result.torque_nm))):
        warnings.append(f"{case.name} station torque does not sum to total torque")

    for idx, station in enumerate(result.stations, start=1):
        assert_positive_finite(f"{case.name} station {idx} radius_m", station.radius_m)
        assert_positive_finite(f"{case.name} station {idx} dr_m", station.dr_m)
        assert_positive_finite(f"{case.name} station {idx} chord_m", station.chord_m)
        if not math.isfinite(station.twist_deg):
            raise ValueError(f"{case.name} station {idx} twist is not finite")
        if abs(station.twist_deg) > 120.0:
            warnings.append(f"{case.name} station {idx} twist magnitude is unusually large for degrees")
    return warnings


def build_design_config(args: argparse.Namespace, spanload_type: str, output_dir: Path) -> DesignInputs:
    radius_m = diameter_in_to_radius_m(args.diameter_in)
    max_chord_fraction = max(station.chord_fraction for station in builtin_reference_stations())
    chord_max_in = args.chord_max_in
    if chord_max_in is None:
        chord_max_in = max_chord_fraction * meters_to_inches(radius_m) * DEFAULT_REFERENCE_MAX_CHORD_MARGIN
    lock_geometry = args.comparison_geometry == "same"
    return DesignInputs(
        blades=args.blades,
        radius_m=radius_m,
        hub_radius_fraction=args.hub_radius_fraction,
        rpm=args.rpm,
        freestream_mps=args.v_inf,
        thrust_target_n=args.thrust,
        rho=args.rho,
        spanload_type=spanload_type,
        mode=args.mode,
        chord_min_in=args.chord_min_in,
        chord_max_in=chord_max_in,
        chord_blend=args.chord_blend,
        twist_blend=args.twist_blend,
        smooth_passes=args.smooth_passes,
        alpha_search_min_deg=args.alpha_min,
        alpha_search_max_deg=args.alpha_max,
        alpha_search_step_deg=args.alpha_step,
        reference_csv_path=args.reference_csv,
        reference_geometry_csv=args.reference_geometry_csv,
        output_directory=output_dir,
        lock_chord=lock_geometry,
        lock_airfoil=lock_geometry,
        optimize_twist_only=lock_geometry,
        make_plots=False,
        optimizer=args.optimizer,
        max_iterations=args.max_iterations,
        tolerance=args.tolerance,
    )


def run_solver_case(
    name: str,
    spanload_type: str,
    reference_variant: str,
    args: argparse.Namespace,
    output_dir: Path,
) -> SolverCase:
    config = build_design_config(args, spanload_type, output_dir)
    validate_user_inputs(config)
    reference_stations, reference_source, input_warnings = load_reference_geometry(
        config.reference_geometry_csv,
        config.reference_csv_path,
        reference_variant,
    )
    result = optimize_geometry(config, reference_stations, input_warnings, reference_source)
    write_case_outputs(result, output_dir)
    return SolverCase(
        name=name,
        spanload_type=spanload_type,
        reference_variant=reference_variant,
        result=result,
        reference_source=reference_source,
        output_dir=output_dir,
    )


def run_reference_case(
    name: str,
    spanload_type: str,
    reference_variant: str,
    args: argparse.Namespace,
    output_dir: Path,
) -> SolverCase:
    config = build_design_config(args, spanload_type, output_dir)
    config = dataclass_replace_reference_mode(config)
    validate_user_inputs(config)
    reference_stations, reference_source, input_warnings = load_reference_geometry(
        config.reference_geometry_csv,
        config.reference_csv_path,
        reference_variant,
    )
    result = optimize_geometry(config, reference_stations, input_warnings, reference_source)
    write_case_outputs(result, output_dir, reference_case_name(spanload_type))
    return SolverCase(
        name=name,
        spanload_type=spanload_type,
        reference_variant=reference_variant,
        result=result,
        reference_source=reference_source,
        output_dir=output_dir,
    )


def dataclass_replace_reference_mode(config: DesignInputs) -> DesignInputs:
    return DesignInputs(
        blades=config.blades,
        radius_m=config.radius_m,
        hub_radius_fraction=config.hub_radius_fraction,
        rpm=config.rpm,
        freestream_mps=config.freestream_mps,
        thrust_target_n=config.thrust_target_n,
        rho=config.rho,
        spanload_type=config.spanload_type,
        mode="reproduce_reference",
        chord_min_in=config.chord_min_in,
        chord_max_in=config.chord_max_in,
        chord_blend=config.chord_blend,
        twist_blend=config.twist_blend,
        smooth_passes=config.smooth_passes,
        alpha_search_min_deg=config.alpha_search_min_deg,
        alpha_search_max_deg=config.alpha_search_max_deg,
        alpha_search_step_deg=config.alpha_search_step_deg,
        reference_csv_path=config.reference_csv_path,
        reference_geometry_csv=config.reference_geometry_csv,
        output_directory=config.output_directory,
        lock_chord=True,
        lock_airfoil=True,
        optimize_twist_only=True,
        make_plots=False,
        optimizer=config.optimizer,
        max_iterations=0,
        tolerance=config.tolerance,
    )


def write_case_outputs(result: DesignResult, output_dir: Path, case_name: str | None = None) -> None:
    assert_verification_output_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_name = case_name or optimized_case_name(result.config.spanload_type)
    geometry_csv = output_dir / optimizer_geometry_csv_name(output_name)
    try:
        write_csv(result, geometry_csv, output_domain="verification")
        write_summary_json(result, output_dir / "solver_summary.json", output_domain="verification")
        write_convergence_history_csv(
            result.convergence_history,
            output_dir / "convergence_history.csv",
            output_domain="verification",
        )
    except PermissionError as exc:
        print(f"WARNING: skipped nested case output because the path is locked or not writable: {exc}")


def compute_metrics(
    mil: SolverCase,
    gmt: SolverCase,
    mil_span: SpanwiseData,
    gmt_span: SpanwiseData,
    common_grid: Sequence[float],
    common_mil: dict[str, list[float]],
    common_gmt: dict[str, list[float]],
) -> VerificationMetrics:
    eta_mil, _ = select_efficiency(mil.result)
    eta_gmt, _ = select_efficiency(gmt.result)
    hub_mu = mil.result.config.hub_radius_fraction

    mil_tip = integrate_annular_band(mil_span.mu, mil_span.dthrust_n, 0.85, 1.0, hub_mu)
    gmt_tip = integrate_annular_band(gmt_span.mu, gmt_span.dthrust_n, 0.85, 1.0, hub_mu)
    mil_inboard = integrate_annular_band(mil_span.mu, mil_span.dthrust_n, 0.2, 0.72, hub_mu)
    gmt_inboard = integrate_annular_band(gmt_span.mu, gmt_span.dthrust_n, 0.2, 0.72, hub_mu)

    diameter_m = 2.0 * mil.result.config.radius_m
    mil_ct, mil_cq, mil_cp = propeller_coefficients(
        mil.result.thrust_n,
        mil.result.torque_nm,
        mil.result.config.rho,
        mil.result.config.rpm,
        diameter_m,
    )
    gmt_ct, gmt_cq, gmt_cp = propeller_coefficients(
        gmt.result.thrust_n,
        gmt.result.torque_nm,
        gmt.result.config.rho,
        gmt.result.config.rpm,
        diameter_m,
    )

    chord_diffs = [
        abs(left - right)
        for left, right in zip(common_mil["chord_m"], common_gmt["chord_m"])
    ]
    twist_diffs = [
        abs(left - right)
        for left, right in zip(common_mil["twist_deg"], common_gmt["twist_deg"])
    ]

    return VerificationMetrics(
        thrust_match_error_pct=abs(gmt.result.thrust_n - mil.result.thrust_n)
        / max(abs(mil.result.thrust_n), 1.0e-12)
        * 100.0,
        torque_reduction_pct=reduction_pct(mil.result.torque_nm, gmt.result.torque_nm),
        power_reduction_pct=reduction_pct(mil.result.power_w, gmt.result.power_w),
        eta_change_pct=pct_change(eta_gmt, eta_mil),
        tip_loading_reduction_pct=reduction_pct(mil_tip, gmt_tip),
        inboard_loading_change_pct=pct_change(gmt_inboard, mil_inboard),
        inboard_fraction_mil=safe_div(mil_inboard, mil.result.thrust_n),
        inboard_fraction_gmt=safe_div(gmt_inboard, gmt.result.thrust_n),
        tip_fraction_mil=safe_div(mil_tip, mil.result.thrust_n),
        tip_fraction_gmt=safe_div(gmt_tip, gmt.result.thrust_n),
        cumulative_50_radius_mil=cumulative_radius_at_fraction(mil_span.mu, mil_span.dthrust_n, 0.5, hub_mu),
        cumulative_50_radius_gmt=cumulative_radius_at_fraction(gmt_span.mu, gmt_span.dthrust_n, 0.5, hub_mu),
        max_chord_diff_m=max(chord_diffs, default=0.0),
        max_chord_diff_pct_radius=safe_div(max(chord_diffs, default=0.0), mil.result.config.radius_m) * 100.0,
        max_twist_diff_deg=max(twist_diffs, default=0.0),
        ct_mil=mil_ct,
        cq_mil=mil_cq,
        cp_mil=mil_cp,
        ct_gmt=gmt_ct,
        cq_gmt=gmt_cq,
        cp_gmt=gmt_cp,
        eta_mil=eta_mil,
        eta_gmt=eta_gmt,
        cl_clipping_fraction_mil_pct=100.0
        * sum(station.hit_cl_limit for station in mil.result.stations)
        / max(len(mil.result.stations), 1),
        cl_clipping_fraction_gmt_pct=100.0
        * sum(station.hit_cl_limit for station in gmt.result.stations)
        / max(len(gmt.result.stations), 1),
    )


def build_checks(
    mil: SolverCase,
    gmt: SolverCase,
    mil_span: SpanwiseData,
    gmt_span: SpanwiseData,
    metrics: VerificationMetrics,
    shape: SpanloadShapeComparison,
    grids_identical: bool,
    args: argparse.Namespace,
) -> list[VerificationCheck]:
    checks: list[VerificationCheck] = []

    def pass_fail(name: str, passed: bool, value: str, criterion: str, notes: str = "") -> None:
        checks.append(VerificationCheck(name, "PASS" if passed else "FAIL", value, criterion, notes))

    def warn(name: str, value: str, criterion: str, notes: str = "") -> None:
        checks.append(VerificationCheck(name, "WARN", value, criterion, notes))

    pass_fail(
        "same thrust target",
        close_enough(mil.result.config.thrust_target_n, gmt.result.config.thrust_target_n, 1.0e-9),
        f"{mil.result.config.thrust_target_n:.6g} vs {gmt.result.config.thrust_target_n:.6g} N",
        "MIL and GMT target thrust equal",
    )
    pass_fail(
        "actual thrust match",
        metrics.thrust_match_error_pct < args.thrust_match_tol_pct,
        f"{metrics.thrust_match_error_pct:.3f}%",
        f"< {args.thrust_match_tol_pct:.3f}%",
        "Fair black-box torque comparison requires comparable total thrust.",
    )
    pass_fail(
        "fixed rpm",
        close_enough(mil.result.config.rpm, gmt.result.config.rpm, 1.0e-9),
        f"{mil.result.config.rpm:.3f} vs {gmt.result.config.rpm:.3f} rpm",
        "equal RPM",
    )
    pass_fail(
        "same diameter",
        close_enough(mil.result.config.radius_m, gmt.result.config.radius_m, 1.0e-12),
        f"{2.0 * mil.result.config.radius_m:.6f} vs {2.0 * gmt.result.config.radius_m:.6f} m",
        "equal diameter",
    )
    pass_fail(
        "same air density",
        close_enough(mil.result.config.rho, gmt.result.config.rho, 1.0e-12),
        f"{mil.result.config.rho:.6g} vs {gmt.result.config.rho:.6g} kg/m^3",
        "equal rho",
    )
    pass_fail(
        "same blade count",
        mil.result.config.blades == gmt.result.config.blades,
        f"{mil.result.config.blades} vs {gmt.result.config.blades}",
        "equal blade count",
    )
    if abs(mil.result.config.freestream_mps) <= 1.0e-9 and abs(gmt.result.config.freestream_mps) <= 1.0e-9:
        mil_vi = mil.result.induced_velocity_mps
        gmt_vi = gmt.result.induced_velocity_mps
        pass_fail(
            "valid hover induced velocity",
            math.isfinite(mil_vi) and math.isfinite(gmt_vi) and mil_vi > 0.0 and gmt_vi > 0.0,
            f"{mil_vi:.6g} vs {gmt_vi:.6g} m/s",
            "finite nonzero induced velocity in hover",
            "Hover inflow angle must use induced velocity rather than V_inf alone.",
        )
    if grids_identical:
        checks.append(
            VerificationCheck(
                "radial station grid",
                "PASS",
                f"{len(mil_span.mu)} stations",
                "identical r/R stations or interpolated common grid",
                "MIL and GMT stations are identical.",
            )
        )
    else:
        warn(
            "radial station grid",
            f"{len(mil_span.mu)} MIL, {len(gmt_span.mu)} GMT",
            "identical r/R stations or interpolated common grid",
            "Stations differ; all plotted comparisons were interpolated onto a common r/R grid.",
        )

    if args.comparison_geometry == "same":
        pass_fail(
            "same chord distribution",
            metrics.max_chord_diff_m <= args.chord_tolerance_m,
            f"{metrics.max_chord_diff_m:.6e} m",
            f"<= {args.chord_tolerance_m:.2e} m",
            "Required for the requested same-geometry/twist-only comparison.",
        )
    else:
        warn(
            "same chord distribution",
            f"max difference {metrics.max_chord_diff_m:.6e} m",
            "not required when comparing separate scaled-reference MIL/GMT variants",
            "Comparison is not a pure twist-only comparison.",
        )

    airfoils_match = mil_span.airfoils == gmt_span.airfoils
    pass_fail(
        "same airfoil schedule",
        airfoils_match,
        "same" if airfoils_match else "different",
        "same polar set unless intentionally changed and documented",
        "The solver uses reduced-order AirfoilModel polars from Geometry_optimization.py.",
    )

    pass_fail(
        "MIL power equals Q*omega",
        close_enough(
            mil.result.power_w,
            shaft_power_w(mil.result.torque_nm, rpm_to_rad_s(mil.result.config.rpm)),
            1.0e-8 * max(1.0, abs(mil.result.power_w)),
        ),
        f"{mil.result.power_w:.6f} W",
        "P = Q*omega",
    )
    pass_fail(
        "GMT power equals Q*omega",
        close_enough(
            gmt.result.power_w,
            shaft_power_w(gmt.result.torque_nm, rpm_to_rad_s(gmt.result.config.rpm)),
            1.0e-8 * max(1.0, abs(gmt.result.power_w)),
        ),
        f"{gmt.result.power_w:.6f} W",
        "P = Q*omega",
    )
    pass_fail(
        "MIL coefficient definitions",
        coefficient_match(mil.result.ct, metrics.ct_mil)
        and coefficient_match(mil.result.cq, metrics.cq_mil)
        and coefficient_match(mil.result.cp, metrics.cp_mil),
        f"CT/CQ/CP {metrics.ct_mil:.6f}/{metrics.cq_mil:.6f}/{metrics.cp_mil:.6f}",
        "standard propeller coefficient formulas",
    )
    pass_fail(
        "GMT coefficient definitions",
        coefficient_match(gmt.result.ct, metrics.ct_gmt)
        and coefficient_match(gmt.result.cq, metrics.cq_gmt)
        and coefficient_match(gmt.result.cp, metrics.cp_gmt),
        f"CT/CQ/CP {metrics.ct_gmt:.6f}/{metrics.cq_gmt:.6f}/{metrics.cp_gmt:.6f}",
        "standard propeller coefficient formulas",
    )
    pass_fail(
        "lower total torque at same thrust and rpm",
        metrics.torque_reduction_pct > args.torque_reduction_tol_pct,
        f"{metrics.torque_reduction_pct:.3f}%",
        f"> {args.torque_reduction_tol_pct:.3f}%",
        "This is the defining global-minimum-torque black-box check.",
    )
    pass_fail(
        "lower shaft power at fixed rpm",
        metrics.power_reduction_pct > 0.0,
        f"{metrics.power_reduction_pct:.3f}%",
        "> 0%",
        "At fixed RPM, P = Q*omega, so lower torque must lower power.",
    )
    pass_fail(
        "inboard thrust fraction increased",
        metrics.inboard_fraction_gmt > metrics.inboard_fraction_mil,
        f"{metrics.inboard_fraction_mil:.4f} -> {metrics.inboard_fraction_gmt:.4f}",
        "GMT inboard fraction greater than MIL",
    )
    pass_fail(
        "reduced integrated tip loading",
        metrics.tip_loading_reduction_pct > args.tip_loading_reduction_tol_pct,
        f"{metrics.tip_loading_reduction_pct:.3f}%",
        f"> {args.tip_loading_reduction_tol_pct:.3f}%",
        "Integrated thrust from r/R = 0.85 to 1.0.",
    )
    pass_fail(
        "inboard-shifted cumulative thrust",
        metrics.cumulative_50_radius_gmt < metrics.cumulative_50_radius_mil,
        f"{metrics.cumulative_50_radius_mil:.4f} -> {metrics.cumulative_50_radius_gmt:.4f}",
        "GMT 50% cumulative thrust radius below MIL",
    )
    pass_fail(
        "acceptable Cl clipping fraction",
        metrics.cl_clipping_fraction_gmt_pct <= args.cl_clipping_tol_pct,
        f"{metrics.cl_clipping_fraction_gmt_pct:.3f}%",
        f"<= {args.cl_clipping_tol_pct:.3f}%",
        "Reduced-order Cl clipping should remain a small fraction of the GMT stations.",
    )
    pass_fail(
        "spanload shape proof uses nonnegative thrust",
        shape.valid_for_spanload_shape_proof,
        f"MIL min dT {shape.mil.min_dthrust_n:.6g} N, GMT min dT {shape.gmt.min_dthrust_n:.6g} N",
        "no dthrust_n < 0 and no cumulative thrust fraction > 1.02 before final station",
        "Negative thrust loading invalidates spanload-shape proof.",
    )
    pass_fail(
        "MIL closer to elliptical target than GMT",
        shape.mil_closer_to_elliptical_than_gmt,
        f"MIL RMS {shape.mil.rms_error_elliptical:.6g}, GMT RMS {shape.gmt.rms_error_elliptical:.6g}",
        "MIL RMS error to normalized elliptical target < GMT RMS error",
    )
    pass_fail(
        "GMT closer to bell target than MIL",
        shape.gmt_closer_to_bell_than_mil,
        f"MIL RMS {shape.mil.rms_error_bell:.6g}, GMT RMS {shape.gmt.rms_error_bell:.6g}",
        "GMT RMS error to normalized bell target < MIL RMS error",
    )
    pass_fail(
        "GMT lower center of thrust",
        shape.gmt_lower_center_of_thrust_mu,
        f"MIL {shape.mil.center_of_thrust_mu:.6g}, GMT {shape.gmt.center_of_thrust_mu:.6g}",
        "GMT center_of_thrust_mu < MIL center_of_thrust_mu",
    )
    pass_fail(
        "GMT lower tip thrust fraction",
        shape.gmt_lower_tip_thrust_fraction_mu_gt_085,
        f"MIL {shape.mil.tip_thrust_fraction_mu_gt_085:.6g}, GMT {shape.gmt.tip_thrust_fraction_mu_gt_085:.6g}",
        "GMT tip_thrust_fraction_mu_gt_085 < MIL tip_thrust_fraction_mu_gt_085",
    )
    pass_fail(
        "GMT cumulative thrust builds earlier inboard",
        shape.gmt_cumulative_thrust_builds_earlier_inboard,
        (
            "mu=.70 "
            f"{shape.mil.cumulative_thrust_fraction_at_070:.4f}->{shape.gmt.cumulative_thrust_fraction_at_070:.4f}, "
            "mu=.80 "
            f"{shape.mil.cumulative_thrust_fraction_at_080:.4f}->{shape.gmt.cumulative_thrust_fraction_at_080:.4f}, "
            "mu=.90 "
            f"{shape.mil.cumulative_thrust_fraction_at_090:.4f}->{shape.gmt.cumulative_thrust_fraction_at_090:.4f}"
        ),
        "GMT cumulative thrust fraction >= MIL at mu=0.70, 0.80, and 0.90",
    )
    return checks


def coefficient_match(solver_value: float, recomputed_value: float) -> bool:
    return close_enough(solver_value, recomputed_value, 1.0e-8 * max(1.0, abs(recomputed_value)))


def build_warnings(
    mil: SolverCase,
    gmt: SolverCase,
    checks: Sequence[VerificationCheck],
    unit_warnings: Sequence[str],
    metrics: VerificationMetrics,
    shape: SpanloadShapeComparison,
) -> list[str]:
    warnings: list[str] = list(unit_warnings)
    warnings.extend(f"MIL solver warning: {warning}" for warning in mil.result.warnings)
    warnings.extend(f"GMT solver warning: {warning}" for warning in gmt.result.warnings)
    for check in checks:
        if check.status == "FAIL":
            warnings.append(f"FAILED CHECK - {check.name}: {check.value}; criterion {check.criterion}")
        elif check.status == "WARN":
            warnings.append(f"CHECK WARNING - {check.name}: {check.notes or check.value}")

    if metrics.torque_reduction_pct <= 0.0:
        warnings.append(
            "GMT has higher torque than MIL at the matched operating point; this does not verify "
            "global-minimum-torque behavior."
        )
    if metrics.tip_loading_reduction_pct <= 0.0:
        warnings.append(
            "GMT does not reduce integrated tip loading from r/R = 0.85 to 1.0 under this configuration."
        )
    if shape.mil.negative_cl_station_count > 0:
        warnings.append(
            f"MIL has {shape.mil.negative_cl_station_count} station(s) with Cl < -0.02; "
            f"minimum Cl is {shape.mil.min_cl:.6g}."
        )
    if shape.gmt.negative_cl_station_count > 0:
        warnings.append(
            f"GMT has {shape.gmt.negative_cl_station_count} station(s) with Cl < -0.02; "
            f"minimum Cl is {shape.gmt.min_cl:.6g}."
        )
    if shape.mil.negative_thrust_station_count > 0:
        warnings.append(
            f"MIL has {shape.mil.negative_thrust_station_count} station(s) with dthrust_n < 0; "
            "spanload-shape proof is invalid for this case."
        )
    if shape.gmt.negative_thrust_station_count > 0:
        warnings.append(
            f"GMT has {shape.gmt.negative_thrust_station_count} station(s) with dthrust_n < 0; "
            "spanload-shape proof is invalid for this case."
        )
    if shape.mil.cumulative_thrust_overshoot_before_final:
        warnings.append(
            "MIL cumulative thrust fraction exceeds 1.02 before the final station; "
            f"maximum before final is {shape.mil.cumulative_thrust_overshoot_max_before_final:.6g}."
        )
    if shape.gmt.cumulative_thrust_overshoot_before_final:
        warnings.append(
            "GMT cumulative thrust fraction exceeds 1.02 before the final station; "
            f"maximum before final is {shape.gmt.cumulative_thrust_overshoot_max_before_final:.6g}."
        )
    if shape.gmt.tip_thrust_fraction_mu_gt_085 > shape.mil.tip_thrust_fraction_mu_gt_085:
        warnings.append(
            "GMT has larger tip thrust fraction than MIL "
            f"({shape.gmt.tip_thrust_fraction_mu_gt_085:.6g} vs {shape.mil.tip_thrust_fraction_mu_gt_085:.6g})."
        )
    if shape.gmt.center_of_thrust_mu > shape.mil.center_of_thrust_mu:
        warnings.append(
            "GMT has higher center_of_thrust_mu than MIL "
            f"({shape.gmt.center_of_thrust_mu:.6g} vs {shape.mil.center_of_thrust_mu:.6g})."
        )

    core_failed = any(check.name in CORE_THEORY_CHECKS and check.status == "FAIL" for check in checks)
    if core_failed:
        warnings.append(
            "Likely reasons to investigate: not same thrust condition; wrong unit conversion; wrong twist sign "
            "convention; induced velocity model issue; GMT geometry not being used; off-design RPM/advance "
            "ratio; chord/airfoil differences contaminating comparison."
        )
    return dedupe(warnings)


def dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def spanload_shape_case_summary_dict(case: SpanloadShapeCaseMetrics) -> dict[str, object]:
    return {
        "source_csv": str(case.source_csv),
        "mu_root": case.mu_root,
        "rms_error_to_elliptical_target": case.rms_error_elliptical,
        "rms_error_to_bell_target": case.rms_error_bell,
        "correlation_with_elliptical_target": case.correlation_elliptical,
        "correlation_with_bell_target": case.correlation_bell,
        "center_of_thrust_mu": case.center_of_thrust_mu,
        "tip_thrust_fraction_mu_gt_085": case.tip_thrust_fraction_mu_gt_085,
        "cumulative_thrust_fraction_at_mu_070": case.cumulative_thrust_fraction_at_070,
        "cumulative_thrust_fraction_at_mu_080": case.cumulative_thrust_fraction_at_080,
        "cumulative_thrust_fraction_at_mu_090": case.cumulative_thrust_fraction_at_090,
        "negative_thrust_station_count": case.negative_thrust_station_count,
        "min_dthrust_n": case.min_dthrust_n,
        "negative_cl_station_count": case.negative_cl_station_count,
        "min_cl": case.min_cl,
        "cumulative_thrust_overshoot_before_final": case.cumulative_thrust_overshoot_before_final,
        "cumulative_thrust_overshoot_max_before_final": case.cumulative_thrust_overshoot_max_before_final,
    }


def spanload_shape_summary_dict(shape: SpanloadShapeComparison) -> dict[str, object]:
    return {
        "definitions": {
            "mu": "r/R",
            "active_span_coordinate": "s = (mu - mu_root)/(1 - mu_root)",
            "elliptical_target": "w_ell = sqrt(1 - s^2), normalized so integral over mu equals 1",
            "bell_target": "w_bell = (1 - s^2)^(3/2), normalized so integral over mu equals 1",
            "actual_loading": "dthrust_n/dmu from the exported optimized CSV, normalized so integral over mu equals 1",
        },
        "mil": spanload_shape_case_summary_dict(shape.mil),
        "gmt": spanload_shape_case_summary_dict(shape.gmt),
        "acceptance": {
            "mil_closer_to_elliptical_than_gmt": shape.mil_closer_to_elliptical_than_gmt,
            "gmt_closer_to_bell_than_mil": shape.gmt_closer_to_bell_than_mil,
            "gmt_lower_center_of_thrust_mu": shape.gmt_lower_center_of_thrust_mu,
            "gmt_lower_tip_thrust_fraction_mu_gt_085": shape.gmt_lower_tip_thrust_fraction_mu_gt_085,
            "gmt_cumulative_thrust_builds_earlier_inboard": shape.gmt_cumulative_thrust_builds_earlier_inboard,
            "valid_for_spanload_shape_proof": shape.valid_for_spanload_shape_proof,
        },
    }


def write_comparison_solver_summary(
    result: DesignResult,
    output_path: Path,
    verification: VerificationResult,
) -> None:
    assert_verification_output_path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = result_summary_dict(result)
    payload["generating_script"] = GENERATING_SCRIPT
    payload["spanload_shape_comparison"] = spanload_shape_summary_dict(verification.spanload_shape)
    payload["verification_outcome"] = verification.outcome
    payload["warnings"] = dedupe(list(payload.get("warnings", [])) + list(verification.warnings))
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    write_metadata_sidecar(
        output_path,
        {
            "generating_script": GENERATING_SCRIPT,
            "model_source": "theory_verification",
            "case_name": output_path.stem,
            "geometry_source_csv": "",
            "station_source_csv": "",
            "input_config_path": "",
            "twist_column_used": "twist_deg",
            "aero_source": "optimizer_reduced_order",
        },
    )


def run_verification(args: argparse.Namespace) -> VerificationResult:
    results_dir = args.results_dir
    figures_dir = args.figures_dir
    case_dir = results_dir / "cases"
    reference_dir = results_dir / "reference_cases"
    assert_verification_output_path(results_dir)
    assert_verification_output_path(figures_dir)
    assert_verification_output_path(case_dir)
    assert_verification_output_path(reference_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    if args.comparison_geometry == "same":
        mil_variant = args.shared_reference_variant
        gmt_variant = args.shared_reference_variant
    else:
        mil_variant = "mil_22"
        gmt_variant = "gmt_22"

    if args.write_reference_cases:
        run_reference_case(
            "reference_mil",
            "minimum_induced_loss",
            "reference_mil",
            args,
            reference_dir / "reference_mil",
        )
        run_reference_case(
            "reference_gmt",
            "gmt_bell",
            "reference_gmt",
            args,
            reference_dir / "reference_gmt",
        )

    mil = run_solver_case(
        "MIL",
        "minimum_induced_loss",
        mil_variant,
        args,
        case_dir / "optimized_mil",
    )
    gmt = run_solver_case(
        "GMT",
        "gmt_bell",
        gmt_variant,
        args,
        case_dir / "optimized_gmt",
    )

    unit_warnings = validate_units_and_station_data(mil)
    unit_warnings.extend(validate_units_and_station_data(gmt))

    mil_top_csv = case_dir / optimizer_geometry_csv_name("theory_verification_mil")
    gmt_top_csv = case_dir / optimizer_geometry_csv_name("theory_verification_gmt")
    case_dir.mkdir(parents=True, exist_ok=True)
    write_csv(mil.result, mil_top_csv, output_domain="verification")
    write_csv(gmt.result, gmt_top_csv, output_domain="verification")

    mil_span = spanwise_from_result(mil.result)
    gmt_span = spanwise_from_result(gmt.result)
    common_grid, grids_identical = common_grid_for(mil_span.mu, gmt_span.mu)
    common_mil = interpolate_spanwise(mil_span, common_grid)
    common_gmt = interpolate_spanwise(gmt_span, common_grid)
    metrics = compute_metrics(mil, gmt, mil_span, gmt_span, common_grid, common_mil, common_gmt)
    spanload_shape = spanload_shape_comparison_from_csv(mil_top_csv, gmt_top_csv)
    checks = build_checks(mil, gmt, mil_span, gmt_span, metrics, spanload_shape, grids_identical, args)
    warnings = build_warnings(mil, gmt, checks, unit_warnings, metrics, spanload_shape)
    assumptions = build_assumptions(args, mil, gmt)

    verification = VerificationResult(
        mil=mil,
        gmt=gmt,
        mil_span=mil_span,
        gmt_span=gmt_span,
        common_grid=common_grid,
        common_mil=common_mil,
        common_gmt=common_gmt,
        metrics=metrics,
        spanload_shape=spanload_shape,
        checks=checks,
        warnings=warnings,
        assumptions=assumptions,
        results_dir=results_dir,
        figures_dir=figures_dir,
    )

    mil_summary_json = case_dir / "solver_summary_mil.json"
    gmt_summary_json = case_dir / "solver_summary.json"
    write_comparison_solver_summary(mil.result, mil_summary_json, verification)
    write_comparison_solver_summary(gmt.result, gmt_summary_json, verification)
    write_convergence_history_csv(
        gmt.result.convergence_history,
        case_dir / "convergence_history.csv",
        output_domain="verification",
    )
    mil_csv_metrics = geometry_csv_metrics_from_file(mil_top_csv, mil.result)
    gmt_csv_metrics = geometry_csv_metrics_from_file(gmt_top_csv, gmt.result)
    validate_csv_metrics_against_result("MIL", mil_csv_metrics, mil.result)
    validate_csv_metrics_against_result("GMT", gmt_csv_metrics, gmt.result)
    validate_csv_metrics_against_summary("MIL", mil_csv_metrics, mil_summary_json)
    validate_csv_metrics_against_summary("GMT", gmt_csv_metrics, gmt_summary_json)

    write_summary_csv(verification, results_dir / "theory_verification_summary.csv")
    result_table_csv = case_dir / "optimized_mil_vs_gmt_result_table.csv"
    write_result_table_csv(verification, result_table_csv, mil_csv_metrics, gmt_csv_metrics)
    write_result_table_markdown(
        verification,
        case_dir / "optimized_mil_vs_gmt_result_table.md",
        mil_csv_metrics,
        gmt_csv_metrics,
    )
    validate_result_table_against_csv(result_table_csv, mil_csv_metrics, gmt_csv_metrics)
    if not args.skip_plots:
        write_plots(verification, figures_dir)
    write_report(verification, results_dir / "theory_verification_report.txt")
    return verification


def build_assumptions(args: argparse.Namespace, mil: SolverCase, gmt: SolverCase) -> list[str]:
    efficiency_label = select_efficiency(mil.result)[1]
    geometry_mode = (
        f"same-geometry comparison using shared workbook/reference variant {args.shared_reference_variant!r}"
        if args.comparison_geometry == "same"
        else "scaled-reference comparison using separate MIL and GMT workbook/reference variants"
    )
    return [
        f"Diameter is hardcoded/defaulted to {args.diameter_in:.6g} in unless changed by CLI.",
        f"RPM is hardcoded/defaulted to {args.rpm:.6g} rpm unless changed by CLI.",
        f"Target thrust is hardcoded/defaulted to {args.thrust:.6g} N unless changed by CLI.",
        f"Air density is hardcoded/defaulted to {args.rho:.6g} kg/m^3 unless changed by CLI.",
        f"Blade count is hardcoded/defaulted to {args.blades} unless changed by CLI.",
        f"Freestream velocity is hardcoded/defaulted to {args.v_inf:.6g} m/s unless changed by CLI.",
        "MIL case locks: "
        f"chord_locked={mil.result.config.lock_chord}, airfoil_locked={mil.result.config.lock_airfoil}, "
        f"optimize_twist_only={mil.result.config.optimize_twist_only}.",
        "GMT case locks: "
        f"chord_locked={gmt.result.config.lock_chord}, airfoil_locked={gmt.result.config.lock_airfoil}, "
        f"optimize_twist_only={gmt.result.config.optimize_twist_only}.",
        f"Comparison geometry mode: {geometry_mode}.",
        f"Solver mode is {args.mode!r}; optimizer is {args.optimizer!r}; max_iterations={args.max_iterations}.",
        "Twist values are read from the solver as degrees and plotted as beta [deg].",
        "RPM is converted to omega with omega = rpm*2*pi/60.",
        "Power is checked as P = Q*omega in watts.",
        "Station dT and dQ are treated as annulus-integrated loads; dT/d(r/R) and dQ/d(r/R) divide by station dmu.",
        f"Efficiency comparison uses {efficiency_label}.",
        f"MIL spanload type is {mil.spanload_type!r}; GMT spanload type is {gmt.spanload_type!r}.",
    ]


def write_summary_csv(verification: VerificationResult, output_path: Path) -> None:
    assert_verification_output_path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    m = verification.metrics
    shape = verification.spanload_shape
    shape = verification.spanload_shape
    rows = [
        ("total_thrust", verification.mil.result.thrust_n, verification.gmt.result.thrust_n, "", "N", "", "", ""),
        ("total_torque", verification.mil.result.torque_nm, verification.gmt.result.torque_nm, "", "N*m", "", "", ""),
        ("power", verification.mil.result.power_w, verification.gmt.result.power_w, "", "W", "", "", ""),
        ("efficiency", m.eta_mil, m.eta_gmt, "", "-", "", "", select_efficiency(verification.mil.result)[1]),
        ("CT", m.ct_mil, m.ct_gmt, "", "-", "", "", ""),
        ("CQ", m.cq_mil, m.cq_gmt, "", "-", "", "", ""),
        ("CP", m.cp_mil, m.cp_gmt, "", "-", "", "", ""),
        ("thrust_match_error_pct", "", "", m.thrust_match_error_pct, "%", status_for_check(verification, "actual thrust match"), "< 5", ""),
        ("torque_reduction_pct", "", "", m.torque_reduction_pct, "%", status_for_check(verification, "lower total torque at same thrust and rpm"), "> 0", ""),
        ("power_reduction_pct", "", "", m.power_reduction_pct, "%", status_for_check(verification, "lower shaft power at fixed rpm"), "> 0", ""),
        ("eta_change_pct", "", "", m.eta_change_pct, "%", "", "", ""),
        ("tip_loading_reduction_pct", "", "", m.tip_loading_reduction_pct, "%", status_for_check(verification, "reduced integrated tip loading"), "> 10", "r/R 0.85 to 1.0"),
        ("inboard_loading_change_pct", "", "", m.inboard_loading_change_pct, "%", status_for_check(verification, "inboard thrust fraction increased"), "> 0", "r/R 0.2 to 0.72"),
        ("inboard_thrust_fraction", m.inboard_fraction_mil, m.inboard_fraction_gmt, "", "-", "", "", "r/R 0.2 to 0.72"),
        ("tip_thrust_fraction", m.tip_fraction_mil, m.tip_fraction_gmt, "", "-", "", "", "r/R 0.85 to 1.0"),
        ("rms_error_to_elliptical_target", shape.mil.rms_error_elliptical, shape.gmt.rms_error_elliptical, shape.gmt.rms_error_elliptical - shape.mil.rms_error_elliptical, "-", status_for_check(verification, "MIL closer to elliptical target than GMT"), "MIL < GMT", "normalized dT/dmu shape"),
        ("rms_error_to_bell_target", shape.mil.rms_error_bell, shape.gmt.rms_error_bell, shape.gmt.rms_error_bell - shape.mil.rms_error_bell, "-", status_for_check(verification, "GMT closer to bell target than MIL"), "GMT < MIL", "normalized dT/dmu shape"),
        ("correlation_with_elliptical_target", shape.mil.correlation_elliptical, shape.gmt.correlation_elliptical, shape.gmt.correlation_elliptical - shape.mil.correlation_elliptical, "-", "", "", "weighted Pearson correlation"),
        ("correlation_with_bell_target", shape.mil.correlation_bell, shape.gmt.correlation_bell, shape.gmt.correlation_bell - shape.mil.correlation_bell, "-", "", "", "weighted Pearson correlation"),
        ("center_of_thrust_mu", shape.mil.center_of_thrust_mu, shape.gmt.center_of_thrust_mu, shape.gmt.center_of_thrust_mu - shape.mil.center_of_thrust_mu, "mu", status_for_check(verification, "GMT lower center of thrust"), "GMT < MIL", ""),
        ("tip_thrust_fraction_mu_gt_085", shape.mil.tip_thrust_fraction_mu_gt_085, shape.gmt.tip_thrust_fraction_mu_gt_085, shape.gmt.tip_thrust_fraction_mu_gt_085 - shape.mil.tip_thrust_fraction_mu_gt_085, "-", status_for_check(verification, "GMT lower tip thrust fraction"), "GMT < MIL", ""),
        ("cumulative_thrust_fraction_at_mu_070", shape.mil.cumulative_thrust_fraction_at_070, shape.gmt.cumulative_thrust_fraction_at_070, shape.gmt.cumulative_thrust_fraction_at_070 - shape.mil.cumulative_thrust_fraction_at_070, "-", status_for_check(verification, "GMT cumulative thrust builds earlier inboard"), "GMT >= MIL", ""),
        ("cumulative_thrust_fraction_at_mu_080", shape.mil.cumulative_thrust_fraction_at_080, shape.gmt.cumulative_thrust_fraction_at_080, shape.gmt.cumulative_thrust_fraction_at_080 - shape.mil.cumulative_thrust_fraction_at_080, "-", status_for_check(verification, "GMT cumulative thrust builds earlier inboard"), "GMT >= MIL", ""),
        ("cumulative_thrust_fraction_at_mu_090", shape.mil.cumulative_thrust_fraction_at_090, shape.gmt.cumulative_thrust_fraction_at_090, shape.gmt.cumulative_thrust_fraction_at_090 - shape.mil.cumulative_thrust_fraction_at_090, "-", status_for_check(verification, "GMT cumulative thrust builds earlier inboard"), "GMT >= MIL", ""),
        ("cumulative_50_thrust_radius", m.cumulative_50_radius_mil, m.cumulative_50_radius_gmt, "", "r/R", status_for_check(verification, "inboard-shifted cumulative thrust"), "GMT < MIL", ""),
        ("cl_clipping_fraction", m.cl_clipping_fraction_mil_pct, m.cl_clipping_fraction_gmt_pct, "", "%", status_for_check(verification, "acceptable Cl clipping fraction"), "<= configured tolerance", ""),
        ("max_chord_difference", "", "", m.max_chord_diff_m, "m", status_for_check(verification, "same chord distribution"), f"<= {DEFAULT_CHORD_TOL_M}", ""),
        ("max_twist_difference", "", "", m.max_twist_diff_deg, "deg", "", "", ""),
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "mil_value", "gmt_value", "comparison_value", "units", "status", "criterion", "notes"])
        for row in rows:
            writer.writerow([format_csv_value(value) for value in row])
    write_metadata_sidecar(
        output_path,
        {
            "generating_script": GENERATING_SCRIPT,
            "model_source": "theory_verification",
            "case_name": "optimized_mil_vs_gmt",
            "geometry_source_csv": "",
            "station_source_csv": "",
            "input_config_path": "",
            "twist_column_used": "twist_deg",
            "aero_source": "optimizer_reduced_order",
        },
    )


def geometry_csv_metrics_from_file(path: Path, result: DesignResult) -> GeometryCsvMetrics:
    if not path.exists():
        raise FileNotFoundError(f"comparison source CSV not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"comparison source CSV is empty: {path}")

    mu = [float(row["mu"]) for row in rows]
    dthrust = [float(row["dthrust_n"]) for row in rows]
    dtorque = [float(row["dtorque_nm"]) for row in rows]
    thrust = sum(dthrust)
    torque = sum(dtorque)
    rpm = result.config.rpm
    rho = result.config.rho
    diameter_m = 2.0 * result.config.radius_m
    disk_area = math.pi * result.config.radius_m**2
    power = shaft_power_w(torque, rpm_to_rad_s(rpm))
    eta_static = 0.0
    if result.config.freestream_mps <= 1.0e-6 and power > 0.0 and thrust > 0.0:
        eta_static = thrust**1.5 / (power * math.sqrt(2.0 * rho * disk_area))
    ct, cq, cp = propeller_coefficients(thrust, torque, rho, rpm, diameter_m)
    clipped = [
        row for row in rows
        if row.get("is_clipped", row.get("hit_cl_limit", "")).strip().lower() == "true"
        or row.get("hit_cl_limit", "").strip().lower() == "true"
    ]
    hub_mu = result.config.hub_radius_fraction
    tip_thrust = integrate_annular_band(mu, dthrust, 0.85, 1.0, hub_mu)
    inboard_thrust = integrate_annular_band(mu, dthrust, 0.2, 0.72, hub_mu)
    return GeometryCsvMetrics(
        path=path,
        thrust_n=thrust,
        torque_nm=torque,
        power_w=power,
        eta_static=eta_static,
        ct=ct,
        cq=cq,
        cp=cp,
        cl_clipping_fraction=safe_div(len(clipped), len(rows)),
        tip_thrust_fraction=safe_div(tip_thrust, thrust),
        inboard_thrust_fraction=safe_div(inboard_thrust, thrust),
        cumulative_50_thrust_radius=cumulative_radius_at_fraction(mu, dthrust, 0.5, hub_mu),
    )


def assert_close_for_output_sync(label: str, csv_value: float, expected_value: float, rel_tol: float = 1.0e-5) -> None:
    tolerance = rel_tol * max(abs(expected_value), 1.0)
    if abs(csv_value - expected_value) > tolerance:
        raise RuntimeError(
            f"output synchronization check failed for {label}: "
            f"CSV/table value {csv_value:.12g} vs expected {expected_value:.12g} "
            f"(abs diff {abs(csv_value - expected_value):.3g}, tolerance {tolerance:.3g})"
        )


def validate_csv_metrics_against_result(case_name: str, metrics: GeometryCsvMetrics, result: DesignResult) -> None:
    assert_close_for_output_sync(f"{case_name} thrust", metrics.thrust_n, result.thrust_n)
    assert_close_for_output_sync(f"{case_name} torque", metrics.torque_nm, result.torque_nm)
    assert_close_for_output_sync(f"{case_name} power", metrics.power_w, result.power_w)
    assert_close_for_output_sync(f"{case_name} eta_static", metrics.eta_static, result.eta_static)


def validate_csv_metrics_against_summary(case_name: str, metrics: GeometryCsvMetrics, summary_path: Path) -> None:
    if not summary_path.exists():
        raise FileNotFoundError(f"{case_name} solver summary not found for output sync check: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    final_metrics = summary["final_metrics"]
    assert_close_for_output_sync(f"{case_name} summary thrust", metrics.thrust_n, float(final_metrics["thrust_n"]))
    assert_close_for_output_sync(f"{case_name} summary torque", metrics.torque_nm, float(final_metrics["torque_nm"]))
    assert_close_for_output_sync(f"{case_name} summary power", metrics.power_w, float(final_metrics["power_w"]))
    assert_close_for_output_sync(f"{case_name} summary eta_static", metrics.eta_static, float(final_metrics["eta_static"]))


def table_row_by_metric(table_path: Path) -> dict[str, dict[str, str]]:
    with table_path.open(newline="", encoding="utf-8") as handle:
        return {row["metric"]: row for row in csv.DictReader(handle)}


def validate_result_table_against_csv(
    table_path: Path,
    mil_metrics: GeometryCsvMetrics,
    gmt_metrics: GeometryCsvMetrics,
) -> None:
    rows = table_row_by_metric(table_path)
    checks = [
        ("total thrust", "optimized_mil", mil_metrics.thrust_n),
        ("total thrust", "optimized_gmt", gmt_metrics.thrust_n),
        ("total torque", "optimized_mil", mil_metrics.torque_nm),
        ("total torque", "optimized_gmt", gmt_metrics.torque_nm),
        ("shaft power", "optimized_mil", mil_metrics.power_w),
        ("shaft power", "optimized_gmt", gmt_metrics.power_w),
        ("efficiency", "optimized_mil", mil_metrics.eta_static),
        ("efficiency", "optimized_gmt", gmt_metrics.eta_static),
    ]
    for metric, column, expected in checks:
        if metric not in rows:
            raise RuntimeError(f"comparison table missing metric row: {metric}")
        assert_close_for_output_sync(f"comparison table {metric} {column}", float(rows[metric][column]), expected)


def comparison_result_table_rows(
    verification: VerificationResult,
    mil_csv: GeometryCsvMetrics,
    gmt_csv: GeometryCsvMetrics,
) -> list[tuple[object, ...]]:
    mil = verification.mil.result
    gmt = verification.gmt.result
    torque_reduction = reduction_pct(mil_csv.torque_nm, gmt_csv.torque_nm)
    power_reduction = reduction_pct(mil_csv.power_w, gmt_csv.power_w)
    eta_change = pct_change(gmt_csv.eta_static, mil_csv.eta_static)
    shape = verification.spanload_shape
    return [
        ("source MIL output", "", str(mil_csv.path), "", "", "top-level exported geometry CSV"),
        ("source GMT output", "", "", str(gmt_csv.path), "", "top-level exported geometry CSV"),
        ("rpm", "rpm", mil.config.rpm, gmt.config.rpm, gmt.config.rpm - mil.config.rpm, "must match for power comparison"),
        ("air density", "kg/m^3", mil.config.rho, gmt.config.rho, gmt.config.rho - mil.config.rho, "must match for coefficient comparison"),
        ("target thrust", "N", mil.config.thrust_target_n, gmt.config.thrust_target_n, gmt.config.thrust_target_n - mil.config.thrust_target_n, "must match"),
        ("total thrust", "N", mil_csv.thrust_n, gmt_csv.thrust_n, gmt_csv.thrust_n - mil_csv.thrust_n, "GMT - MIL"),
        ("total torque", "N*m", mil_csv.torque_nm, gmt_csv.torque_nm, gmt_csv.torque_nm - mil_csv.torque_nm, "GMT - MIL"),
        ("shaft power", "W", mil_csv.power_w, gmt_csv.power_w, gmt_csv.power_w - mil_csv.power_w, "P = Q*2*pi*rpm/60 from source CSV torque"),
        ("efficiency", "-", mil_csv.eta_static, gmt_csv.eta_static, gmt_csv.eta_static - mil_csv.eta_static, f"{select_efficiency(mil)[1]}; advance ratio is zero"),
        ("CT", "-", mil_csv.ct, gmt_csv.ct, gmt_csv.ct - mil_csv.ct, "same rho, rpm, and diameter definitions"),
        ("CQ", "-", mil_csv.cq, gmt_csv.cq, gmt_csv.cq - mil_csv.cq, "same rho, rpm, and diameter definitions"),
        ("CP", "-", mil_csv.cp, gmt_csv.cp, gmt_csv.cp - mil_csv.cp, "same rho, rpm, and diameter definitions"),
        ("torque reduction", "%", "", "", torque_reduction, "100*(MIL-GMT)/MIL"),
        ("power reduction", "%", "", "", power_reduction, "100*(MIL-GMT)/MIL"),
        ("efficiency change", "%", "", "", eta_change, "100*(GMT-MIL)/MIL"),
        ("RMS error to elliptical target", "-", shape.mil.rms_error_elliptical, shape.gmt.rms_error_elliptical, shape.gmt.rms_error_elliptical - shape.mil.rms_error_elliptical, "normalized spanload shape; lower is closer"),
        ("RMS error to bell target", "-", shape.mil.rms_error_bell, shape.gmt.rms_error_bell, shape.gmt.rms_error_bell - shape.mil.rms_error_bell, "normalized spanload shape; lower is closer"),
        ("correlation with elliptical target", "-", shape.mil.correlation_elliptical, shape.gmt.correlation_elliptical, shape.gmt.correlation_elliptical - shape.mil.correlation_elliptical, "weighted Pearson correlation over mu"),
        ("correlation with bell target", "-", shape.mil.correlation_bell, shape.gmt.correlation_bell, shape.gmt.correlation_bell - shape.mil.correlation_bell, "weighted Pearson correlation over mu"),
        ("center_of_thrust_mu", "mu", shape.mil.center_of_thrust_mu, shape.gmt.center_of_thrust_mu, shape.gmt.center_of_thrust_mu - shape.mil.center_of_thrust_mu, "sum(mu*dT)/sum(dT); lower means more inboard"),
        ("tip_thrust_fraction_mu_gt_085", "-", shape.mil.tip_thrust_fraction_mu_gt_085, shape.gmt.tip_thrust_fraction_mu_gt_085, shape.gmt.tip_thrust_fraction_mu_gt_085 - shape.mil.tip_thrust_fraction_mu_gt_085, "integrated fraction from mu=0.85 to 1.0"),
        ("cumulative thrust fraction at mu = 0.70", "-", shape.mil.cumulative_thrust_fraction_at_070, shape.gmt.cumulative_thrust_fraction_at_070, shape.gmt.cumulative_thrust_fraction_at_070 - shape.mil.cumulative_thrust_fraction_at_070, "GMT should be greater for earlier inboard build"),
        ("cumulative thrust fraction at mu = 0.80", "-", shape.mil.cumulative_thrust_fraction_at_080, shape.gmt.cumulative_thrust_fraction_at_080, shape.gmt.cumulative_thrust_fraction_at_080 - shape.mil.cumulative_thrust_fraction_at_080, "GMT should be greater for earlier inboard build"),
        ("cumulative thrust fraction at mu = 0.90", "-", shape.mil.cumulative_thrust_fraction_at_090, shape.gmt.cumulative_thrust_fraction_at_090, shape.gmt.cumulative_thrust_fraction_at_090 - shape.mil.cumulative_thrust_fraction_at_090, "GMT should be greater for earlier inboard build"),
        ("valid for spanload-shape proof", "-", shape.valid_for_spanload_shape_proof, shape.valid_for_spanload_shape_proof, "", "false if any dthrust_n < 0 or cumulative thrust fraction exceeds 1.02 before the final station"),
        ("tip thrust fraction from r/R = 0.85 to 1.0", "-", mil_csv.tip_thrust_fraction, gmt_csv.tip_thrust_fraction, gmt_csv.tip_thrust_fraction - mil_csv.tip_thrust_fraction, "fraction of case total"),
        ("inboard thrust fraction from r/R = 0.2 to 0.72", "-", mil_csv.inboard_thrust_fraction, gmt_csv.inboard_thrust_fraction, gmt_csv.inboard_thrust_fraction - mil_csv.inboard_thrust_fraction, "fraction of case total"),
        ("cumulative 50% thrust radius", "r/R", mil_csv.cumulative_50_thrust_radius, gmt_csv.cumulative_50_thrust_radius, gmt_csv.cumulative_50_thrust_radius - mil_csv.cumulative_50_thrust_radius, "lower means more inboard cumulative loading"),
        ("Cl clipping fraction", "-", mil_csv.cl_clipping_fraction, gmt_csv.cl_clipping_fraction, gmt_csv.cl_clipping_fraction - mil_csv.cl_clipping_fraction, "station-count fraction with hit_cl_limit/is_clipped = True"),
    ]


def write_result_table_csv(
    verification: VerificationResult,
    output_path: Path,
    mil_csv: GeometryCsvMetrics,
    gmt_csv: GeometryCsvMetrics,
) -> None:
    assert_verification_output_path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Comparison table MIL CSV source: {mil_csv.path}")
    print(f"Comparison table GMT CSV source: {gmt_csv.path}")
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "units", "optimized_mil", "optimized_gmt", "gmt_vs_mil", "notes"])
        for row in comparison_result_table_rows(verification, mil_csv, gmt_csv):
            writer.writerow([format_csv_value(value) for value in row])
    write_metadata_sidecar(
        output_path,
        {
            "generating_script": GENERATING_SCRIPT,
            "model_source": "theory_verification",
            "case_name": "optimized_mil_vs_gmt",
            "geometry_source_csv": {"MIL": str(mil_csv.path), "GMT": str(gmt_csv.path)},
            "station_source_csv": "",
            "input_config_path": "",
            "twist_column_used": "twist_deg",
            "aero_source": "optimizer_reduced_order",
        },
    )


def write_result_table_markdown(
    verification: VerificationResult,
    output_path: Path,
    mil_csv: GeometryCsvMetrics,
    gmt_csv: GeometryCsvMetrics,
) -> None:
    assert_verification_output_path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "| metric | units | optimized_mil | optimized_gmt | gmt_vs_mil | notes |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in comparison_result_table_rows(verification, mil_csv, gmt_csv):
        lines.append("| " + " | ".join(str(format_csv_value(value)) for value in row) + " |")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_csv_value(value: object) -> object:
    if isinstance(value, float):
        return f"{value:.10g}"
    return value


def status_for_check(verification: VerificationResult, check_name: str) -> str:
    for check in verification.checks:
        if check.name == check_name:
            return check.status
    return ""


def write_report(verification: VerificationResult, output_path: Path) -> None:
    assert_verification_output_path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    m = verification.metrics
    shape = verification.spanload_shape
    mil = verification.mil.result
    gmt = verification.gmt.result
    omega = rpm_to_rad_s(mil.config.rpm)
    eta_label = select_efficiency(mil)[1]
    torque_pass = status_for_check(verification, "lower total torque at same thrust and rpm") == "PASS"
    tip_loading_pass = status_for_check(verification, "reduced integrated tip loading") == "PASS"
    cumulative_pass = status_for_check(verification, "inboard-shifted cumulative thrust") == "PASS"
    cl_pass = status_for_check(verification, "acceptable Cl clipping fraction") == "PASS"
    gmt_tip_unloading_pass = tip_loading_pass and cumulative_pass
    if torque_pass and not gmt_tip_unloading_pass:
        interpretation_result = (
            "The optimizer found a lower-torque solution, but the resulting spanload does not show the expected GMT tip unloading. "
            "This should be interpreted as torque optimization, not full Prandtl/GMT verification."
        )
    elif verification.theory_verified:
        interpretation_result = "Full GMT verification passed for this configuration."
    else:
        interpretation_result = "Full GMT verification failed for this configuration."
    lines = [
        "Theory Verification Report",
        "==========================",
        f"Timestamp: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        "Purpose",
        "-------",
        SCALED_REFERENCE_STATEMENT,
        "The optimized cases are generated by the present code and compared against the scaled reference cases to evaluate whether the optimizer reproduces or improves the expected lower-torque and tip-unloaded behavior.",
        "",
        "This report verifies the current propeller analysis code against the defining",
        "global-minimum-torque theory checks: fixed RPM, same thrust target,",
        "lower total torque, inboard-shifted loading, and reduced tip loading.",
        "The 35 N target is only the thesis 22-inch static target, not the NASA/SciTech original operating point.",
        "",
        "Solver Configuration",
        "--------------------",
        f"MIL spanload: {verification.mil.spanload_type}",
        f"GMT spanload: {verification.gmt.spanload_type}",
        f"MIL reference variant: {verification.mil.reference_variant}",
        f"GMT reference variant: {verification.gmt.reference_variant}",
        f"Solver mode: {mil.config.mode}",
        f"MIL optimizer: {verification.mil.result.optimizer_name}",
        f"GMT optimizer: {verification.gmt.result.optimizer_name}",
        f"Chord locked: {mil.config.lock_chord or mil.config.optimize_twist_only}",
        f"Airfoil locked: {mil.config.lock_airfoil or mil.config.optimize_twist_only}",
        f"Optimize twist only: {mil.config.optimize_twist_only}",
        f"GMT tip unloading passes verification: {gmt_tip_unloading_pass}",
        f"GMT Cl clipping fraction acceptable: {cl_pass}",
        f"Verification outcome: {verification.outcome}",
        "",
        "Operating Condition",
        "-------------------",
        f"Radius: {mil.config.radius_m:.6f} m",
        f"Diameter: {2.0 * mil.config.radius_m:.6f} m ({meters_to_inches(2.0 * mil.config.radius_m):.3f} in)",
        f"RPM: {mil.config.rpm:.3f}",
        f"Omega: {omega:.6f} rad/s",
        f"Air density: {mil.config.rho:.6g} kg/m^3",
        f"Blades: {mil.config.blades}",
        f"Freestream: {mil.config.freestream_mps:.6g} m/s",
        f"Target thrust: {mil.config.thrust_target_n:.6g} N",
        f"Efficiency metric: {eta_label}",
        f"Thesis operating point definition: {THESIS_22_STATIC_OPERATING_POINT}",
        f"NASA/SciTech original context kept separate: {NASA_SCITECH_ORIGINAL_OPERATING_POINT}",
        "",
        "Geometry Files Used",
        "-------------------",
        f"Reference geometry source, MIL: {verification.mil.reference_source}",
        f"Reference geometry source, GMT: {verification.gmt.reference_source}",
        f"Optimized geometry source, MIL: present code output in {verification.mil.output_dir}",
        f"Optimized geometry source, GMT: present code output in {verification.gmt.output_dir}",
        "",
        "Airfoil Polar Files Used",
        "------------------------",
        "No external polar files were loaded by this reduced-order solver. The polar set",
        "is Geometry_optimization.py:AIRFOIL_LIBRARY with these AirfoilModel entries:",
        ", ".join(sorted(AIRFOIL_LIBRARY)),
        f"MIL airfoil schedule: {', '.join(unique_in_order(verification.mil_span.airfoils))}",
        f"GMT airfoil schedule: {', '.join(unique_in_order(verification.gmt_span.airfoils))}",
        "",
        "Pass/Fail Table",
        "---------------",
        format_check_table(verification.checks),
        "",
        "Key Numerical Metrics",
        "---------------------",
        f"MIL thrust: {mil.thrust_n:.6f} N",
        f"GMT thrust: {gmt.thrust_n:.6f} N",
        f"Thrust match error: {m.thrust_match_error_pct:.3f} %",
        f"MIL torque: {mil.torque_nm:.6f} N*m",
        f"GMT torque: {gmt.torque_nm:.6f} N*m",
        f"Torque reduction: {m.torque_reduction_pct:.3f} %",
        f"MIL power: {mil.power_w:.6f} W",
        f"GMT power: {gmt.power_w:.6f} W",
        f"Power reduction: {m.power_reduction_pct:.3f} %",
        f"MIL efficiency: {m.eta_mil:.6f}",
        f"GMT efficiency: {m.eta_gmt:.6f}",
        f"Efficiency change: {m.eta_change_pct:.3f} %",
        f"MIL CT/CQ/CP: {m.ct_mil:.8f} / {m.cq_mil:.8f} / {m.cp_mil:.8f}",
        f"GMT CT/CQ/CP: {m.ct_gmt:.8f} / {m.cq_gmt:.8f} / {m.cp_gmt:.8f}",
        f"GMT Cl clipping fraction: {m.cl_clipping_fraction_gmt_pct:.3f} %",
        f"Tip loading reduction, r/R 0.85 to 1.0: {m.tip_loading_reduction_pct:.3f} %",
        f"Inboard loading change, r/R 0.2 to 0.72: {m.inboard_loading_change_pct:.3f} %",
        f"Inboard thrust fraction MIL -> GMT: {m.inboard_fraction_mil:.6f} -> {m.inboard_fraction_gmt:.6f}",
        f"Tip thrust fraction MIL -> GMT: {m.tip_fraction_mil:.6f} -> {m.tip_fraction_gmt:.6f}",
        f"MIL RMS error to elliptical target: {shape.mil.rms_error_elliptical:.6f}",
        f"MIL RMS error to bell target: {shape.mil.rms_error_bell:.6f}",
        f"GMT RMS error to elliptical target: {shape.gmt.rms_error_elliptical:.6f}",
        f"GMT RMS error to bell target: {shape.gmt.rms_error_bell:.6f}",
        f"MIL correlation with elliptical target: {shape.mil.correlation_elliptical:.6f}",
        f"MIL correlation with bell target: {shape.mil.correlation_bell:.6f}",
        f"GMT correlation with elliptical target: {shape.gmt.correlation_elliptical:.6f}",
        f"GMT correlation with bell target: {shape.gmt.correlation_bell:.6f}",
        f"Center of thrust mu MIL -> GMT: {shape.mil.center_of_thrust_mu:.6f} -> {shape.gmt.center_of_thrust_mu:.6f}",
        f"Tip thrust fraction mu > 0.85 MIL -> GMT: {shape.mil.tip_thrust_fraction_mu_gt_085:.6f} -> {shape.gmt.tip_thrust_fraction_mu_gt_085:.6f}",
        f"Cumulative thrust fraction at mu=0.70 MIL -> GMT: {shape.mil.cumulative_thrust_fraction_at_070:.6f} -> {shape.gmt.cumulative_thrust_fraction_at_070:.6f}",
        f"Cumulative thrust fraction at mu=0.80 MIL -> GMT: {shape.mil.cumulative_thrust_fraction_at_080:.6f} -> {shape.gmt.cumulative_thrust_fraction_at_080:.6f}",
        f"Cumulative thrust fraction at mu=0.90 MIL -> GMT: {shape.mil.cumulative_thrust_fraction_at_090:.6f} -> {shape.gmt.cumulative_thrust_fraction_at_090:.6f}",
        f"Valid for spanload-shape proof: {shape.valid_for_spanload_shape_proof}",
        f"50 percent cumulative thrust radius MIL -> GMT: {m.cumulative_50_radius_mil:.6f} -> {m.cumulative_50_radius_gmt:.6f} r/R",
        f"Maximum chord difference: {m.max_chord_diff_m:.9f} m ({m.max_chord_diff_pct_radius:.6f} %R)",
        f"Maximum twist difference: {m.max_twist_diff_deg:.6f} deg",
        "",
        "Hardcoded Assumptions",
        "---------------------",
        "\n".join(f"- {assumption}" for assumption in verification.assumptions),
        "",
        "Warnings",
        "--------",
        "\n".join(f"- {warning}" for warning in verification.warnings) if verification.warnings else "None.",
        "",
        "Interpretation",
        "--------------",
        "The GMT case is considered consistent with theory if it achieves comparable",
        "thrust at the same RPM while reducing total torque, shifting cumulative thrust",
        "inboard, and reducing integrated tip loading. This verification treats the propeller",
        "as a black box for the torque and power checks, then uses spanwise distributions to",
        "check whether the lower-torque result is accompanied by the expected GMT",
        "inboard loading shift and tip unloading.",
        "",
        interpretation_result,
        "",
        f"Overall result: {verification.outcome}",
    ]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_check_table(checks: Sequence[VerificationCheck]) -> str:
    header = f"{'Status':<6}  {'Check':<43}  {'Value':<24}  Criterion"
    divider = "-" * len(header)
    rows = [header, divider]
    for check in checks:
        rows.append(f"{check.status:<6}  {check.name:<43}  {check.value:<24}  {check.criterion}")
        if check.notes:
            rows.append(f"{'':<6}  {'note: ' + check.notes}")
    return "\n".join(rows)


def unique_in_order(values: Sequence[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def write_plots(verification: VerificationResult, figures_dir: Path) -> None:
    assert_verification_output_path(figures_dir)
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        verification.warnings.append(
            f"matplotlib unavailable; used built-in stdlib plot renderer instead ({exc})"
        )
        write_plots_fallback(verification, figures_dir)
        return

    set_plot_style(plt)
    common_mu = verification.common_grid

    plot_two_series(
        plt,
        common_mu,
        verification.common_mil["twist_deg"],
        verification.common_gmt["twist_deg"],
        "r/R",
        "twist beta [deg]",
        figures_dir,
        "optimized_mil_vs_gmt_twist",
    )
    plot_two_series(
        plt,
        common_mu,
        verification.common_mil["chord_m"],
        verification.common_gmt["chord_m"],
        "r/R",
        "chord [m]",
        figures_dir,
        "optimized_mil_vs_gmt_chord",
    )
    plot_two_series(
        plt,
        common_mu,
        verification.common_mil["cl"],
        verification.common_gmt["cl"],
        "r/R",
        "Cl",
        figures_dir,
        "optimized_mil_vs_gmt_cl_distribution",
    )
    plot_two_series(
        plt,
        common_mu,
        verification.common_mil["thrust_loading_n"],
        verification.common_gmt["thrust_loading_n"],
        "r/R",
        "dT/d(r/R) [N]",
        figures_dir,
        "optimized_mil_vs_gmt_thrust_loading",
    )
    plot_normalized_spanload_shape_comparison(plt, verification, figures_dir)
    plot_cumulative_thrust_comparison(plt, verification, figures_dir)
    plot_two_series(
        plt,
        common_mu,
        verification.common_mil["torque_loading_nm"],
        verification.common_gmt["torque_loading_nm"],
        "r/R",
        "dQ/d(r/R) [N*m]",
        figures_dir,
        "optimized_mil_vs_gmt_torque_loading",
    )
    plot_two_series(
        plt,
        common_mu,
        verification.common_mil["cumulative_thrust_fraction"],
        verification.common_gmt["cumulative_thrust_fraction"],
        "r/R",
        "cumulative thrust fraction [-]",
        figures_dir,
        "optimized_mil_vs_gmt_cumulative_thrust",
        ylim=(0.0, 1.02),
    )
    plot_two_series(
        plt,
        common_mu,
        verification.common_mil["cumulative_torque_fraction"],
        verification.common_gmt["cumulative_torque_fraction"],
        "r/R",
        "cumulative torque fraction [-]",
        figures_dir,
        "optimized_mil_vs_gmt_cumulative_torque",
        ylim=(0.0, 1.02),
    )
    plot_summary_bars(plt, verification, figures_dir)


def plot_normalized_spanload_shape_comparison(plt, verification: VerificationResult, figures_dir: Path) -> None:
    shape = verification.spanload_shape
    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    ax.plot(
        shape.common_mu,
        shape.common_mil_normalized_loading,
        color="#0072B2",
        marker="o",
        markevery=max(1, len(shape.common_mu) // 12),
        label="MIL actual",
    )
    ax.plot(shape.common_mu, shape.common_elliptical_target, color="#0072B2", linestyle="--", label="elliptical target")
    ax.plot(
        shape.common_mu,
        shape.common_gmt_normalized_loading,
        color="#D55E00",
        marker="s",
        markevery=max(1, len(shape.common_mu) // 12),
        label="GMT actual",
    )
    ax.plot(shape.common_mu, shape.common_bell_target, color="#D55E00", linestyle="--", label="bell target")
    ax.set_xlabel("r/R [-]")
    ax.set_ylabel("normalized dT/dmu [-]")
    style_axes(ax)
    save_figure(fig, figures_dir, "normalized_spanload_shape_comparison")
    plt.close(fig)


def plot_cumulative_thrust_comparison(plt, verification: VerificationResult, figures_dir: Path) -> None:
    shape = verification.spanload_shape
    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    ax.plot(
        shape.mil.mu,
        shape.mil.cumulative_thrust_fraction,
        color="#0072B2",
        marker="o",
        markevery=max(1, len(shape.mil.mu) // 12),
        label="MIL cumulative thrust",
    )
    ax.plot(
        shape.gmt.mu,
        shape.gmt.cumulative_thrust_fraction,
        color="#D55E00",
        marker="s",
        markevery=max(1, len(shape.gmt.mu) // 12),
        label="GMT cumulative thrust",
    )
    ax.set_xlabel("r/R [-]")
    ax.set_ylabel("cumulative thrust fraction [-]")
    ax.set_ylim(0.0, 1.02)
    style_axes(ax)
    save_figure(fig, figures_dir, "cumulative_thrust_comparison")
    plt.close(fig)


def set_plot_style(plt) -> None:
    apply_matplotlib_style(plt)
    plt.rcParams.update({"lines.linewidth": 1.7})


def plot_two_series(
    plt,
    x: Sequence[float],
    mil_y: Sequence[float],
    gmt_y: Sequence[float],
    xlabel: str,
    ylabel: str,
    figures_dir: Path,
    filename: str,
    ylim: tuple[float, float] | None = None,
) -> None:
    fig, ax = plt.subplots(figsize=(4.9, 3.25))
    ax.plot(x, mil_y, color="#0072B2", marker="o", markevery=max(1, len(x) // 12), label="MIL")
    ax.plot(x, gmt_y, color="#D55E00", marker="s", markevery=max(1, len(x) // 12), label="GMT")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if ylim is not None:
        ax.set_ylim(*ylim)
    style_axes(ax)
    save_figure(fig, figures_dir, filename)
    plt.close(fig)


def style_axes(ax) -> None:
    ax.grid(True, which="major")
    ax.grid(True, which="minor", alpha=0.18)
    ax.minorticks_on()
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
    ax.tick_params(direction="in", top=True, right=True, width=0.8)
    ax.legend(frameon=False, handlelength=2.7)


def save_figure(fig, figures_dir: Path, filename: str) -> None:
    assert_verification_output_path(figures_dir)
    fig.tight_layout()
    save_matplotlib_figure(fig, figures_dir, filename)


def plot_summary_bars(plt, verification: VerificationResult, figures_dir: Path) -> None:
    metrics = [
        ("total thrust [N]", verification.mil.result.thrust_n, verification.gmt.result.thrust_n),
        ("total torque [N*m]", verification.mil.result.torque_nm, verification.gmt.result.torque_nm),
        ("power [W]", verification.mil.result.power_w, verification.gmt.result.power_w),
        ("efficiency [-]", verification.metrics.eta_mil, verification.metrics.eta_gmt),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(5.8, 4.4))
    for ax, (label, mil_value, gmt_value) in zip(axes.flatten(), metrics):
        ax.bar([0, 1], [mil_value, gmt_value], color=["#0072B2", "#D55E00"], width=0.62)
        ax.set_xticks([0, 1], ["MIL", "GMT"])
        ax.set_ylabel(label)
        ax.grid(True, axis="y", which="major")
        ax.tick_params(direction="in", top=True, right=True, width=0.8)
        for spine in ax.spines.values():
            spine.set_linewidth(0.8)
    save_figure(fig, figures_dir, "optimized_mil_vs_gmt_summary_bar_chart")
    plt.close(fig)


def write_plots_fallback(verification: VerificationResult, figures_dir: Path) -> None:
    assert_verification_output_path(figures_dir)
    common_mu = verification.common_grid
    specs = [
        (
            "optimized_mil_vs_gmt_twist",
            "r/R",
            "twist beta [deg]",
            common_mu,
            verification.common_mil["twist_deg"],
            verification.common_gmt["twist_deg"],
            None,
        ),
        (
            "optimized_mil_vs_gmt_chord",
            "r/R",
            "chord [m]",
            common_mu,
            verification.common_mil["chord_m"],
            verification.common_gmt["chord_m"],
            None,
        ),
        (
            "optimized_mil_vs_gmt_cl_distribution",
            "r/R",
            "Cl",
            common_mu,
            verification.common_mil["cl"],
            verification.common_gmt["cl"],
            None,
        ),
        (
            "optimized_mil_vs_gmt_thrust_loading",
            "r/R",
            "dT/d(r/R) [N]",
            common_mu,
            verification.common_mil["thrust_loading_n"],
            verification.common_gmt["thrust_loading_n"],
            None,
        ),
        (
            "optimized_mil_vs_gmt_torque_loading",
            "r/R",
            "dQ/d(r/R) [N*m]",
            common_mu,
            verification.common_mil["torque_loading_nm"],
            verification.common_gmt["torque_loading_nm"],
            None,
        ),
        (
            "optimized_mil_vs_gmt_cumulative_thrust",
            "r/R",
            "cumulative thrust fraction [-]",
            common_mu,
            verification.common_mil["cumulative_thrust_fraction"],
            verification.common_gmt["cumulative_thrust_fraction"],
            (0.0, 1.02),
        ),
        (
            "optimized_mil_vs_gmt_cumulative_torque",
            "r/R",
            "cumulative torque fraction [-]",
            common_mu,
            verification.common_mil["cumulative_torque_fraction"],
            verification.common_gmt["cumulative_torque_fraction"],
            (0.0, 1.02),
        ),
    ]
    for filename, xlabel, ylabel, x, mil_y, gmt_y, ylim in specs:
        save_fallback_line_plot(figures_dir, filename, xlabel, ylabel, x, mil_y, gmt_y, ylim)
    save_fallback_summary_bars(verification, figures_dir)


def save_fallback_line_plot(
    figures_dir: Path,
    filename: str,
    xlabel: str,
    ylabel: str,
    x_values: Sequence[float],
    mil_y: Sequence[float],
    gmt_y: Sequence[float],
    ylim: tuple[float, float] | None,
) -> None:
    width = 720
    height = 480
    geometry = chart_geometry(x_values, list(mil_y) + list(gmt_y), width, height, ylim)
    save_line_svg(figures_dir / f"{filename}.svg", xlabel, ylabel, x_values, mil_y, gmt_y, geometry)
    save_line_png(figures_dir / f"{filename}.png", xlabel, ylabel, x_values, mil_y, gmt_y, geometry)


def chart_geometry(
    x_values: Sequence[float],
    y_values: Sequence[float],
    width: int,
    height: int,
    ylim: tuple[float, float] | None = None,
) -> dict[str, object]:
    x_min = min(x_values)
    x_max = max(x_values)
    if abs(x_max - x_min) < 1.0e-12:
        x_min -= 0.5
        x_max += 0.5
    if ylim is None:
        y_min = min(y_values)
        y_max = max(y_values)
        if abs(y_max - y_min) < 1.0e-12:
            pad = max(abs(y_max) * 0.1, 1.0)
            y_min -= pad
            y_max += pad
        else:
            pad = 0.06 * (y_max - y_min)
            y_min -= pad
            y_max += pad
    else:
        y_min, y_max = ylim
    return {
        "width": width,
        "height": height,
        "left": 92,
        "right": width - 34,
        "top": 34,
        "bottom": height - 70,
        "x_min": x_min,
        "x_max": x_max,
        "y_min": y_min,
        "y_max": y_max,
        "x_ticks": linear_ticks(x_min, x_max, 5),
        "y_ticks": linear_ticks(y_min, y_max, 5),
    }


def linear_ticks(min_value: float, max_value: float, count: int) -> list[float]:
    if count <= 1:
        return [min_value]
    return [min_value + idx * (max_value - min_value) / (count - 1) for idx in range(count)]


def map_x(value: float, geometry: dict[str, object]) -> float:
    left = float(geometry["left"])
    right = float(geometry["right"])
    x_min = float(geometry["x_min"])
    x_max = float(geometry["x_max"])
    return left + safe_div(value - x_min, x_max - x_min) * (right - left)


def map_y(value: float, geometry: dict[str, object]) -> float:
    top = float(geometry["top"])
    bottom = float(geometry["bottom"])
    y_min = float(geometry["y_min"])
    y_max = float(geometry["y_max"])
    return bottom - safe_div(value - y_min, y_max - y_min) * (bottom - top)


def save_line_svg(
    path: Path,
    xlabel: str,
    ylabel: str,
    x_values: Sequence[float],
    mil_y: Sequence[float],
    gmt_y: Sequence[float],
    geometry: dict[str, object],
) -> None:
    width = int(geometry["width"])
    height = int(geometry["height"])
    left = float(geometry["left"])
    right = float(geometry["right"])
    top = float(geometry["top"])
    bottom = float(geometry["bottom"])
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    for tick in geometry["x_ticks"]:
        x = map_x(float(tick), geometry)
        elements.append(f'<line x1="{x:.2f}" y1="{top:.2f}" x2="{x:.2f}" y2="{bottom:.2f}" stroke="#d9d9d9" stroke-width="0.8"/>')
        elements.append(f'<text x="{x:.2f}" y="{bottom + 22:.2f}" text-anchor="middle" font-family="serif" font-size="12">{html.escape(format_tick(float(tick)))}</text>')
    for tick in geometry["y_ticks"]:
        y = map_y(float(tick), geometry)
        elements.append(f'<line x1="{left:.2f}" y1="{y:.2f}" x2="{right:.2f}" y2="{y:.2f}" stroke="#d9d9d9" stroke-width="0.8"/>')
        elements.append(f'<text x="{left - 10:.2f}" y="{y + 4:.2f}" text-anchor="end" font-family="serif" font-size="12">{html.escape(format_tick(float(tick)))}</text>')
    elements.extend(
        [
            f'<line x1="{left:.2f}" y1="{bottom:.2f}" x2="{right:.2f}" y2="{bottom:.2f}" stroke="black" stroke-width="1.2"/>',
            f'<line x1="{left:.2f}" y1="{top:.2f}" x2="{left:.2f}" y2="{bottom:.2f}" stroke="black" stroke-width="1.2"/>',
            polyline_svg(x_values, mil_y, geometry, "#0072B2"),
            polyline_svg(x_values, gmt_y, geometry, "#D55E00"),
        ]
    )
    for x, y in sample_markers(x_values, mil_y):
        px = map_x(x, geometry)
        py = map_y(y, geometry)
        elements.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3.2" fill="white" stroke="#0072B2" stroke-width="1.4"/>')
    for x, y in sample_markers(x_values, gmt_y):
        px = map_x(x, geometry)
        py = map_y(y, geometry)
        elements.append(f'<rect x="{px - 3:.2f}" y="{py - 3:.2f}" width="6" height="6" fill="white" stroke="#D55E00" stroke-width="1.4"/>')
    elements.extend(
        [
            f'<text x="{0.5 * (left + right):.2f}" y="{height - 22:.2f}" text-anchor="middle" font-family="serif" font-size="13">{html.escape(xlabel)}</text>',
            f'<text transform="translate(22 {0.5 * (top + bottom):.2f}) rotate(-90)" text-anchor="middle" font-family="serif" font-size="13">{html.escape(ylabel)}</text>',
            f'<line x1="{left + 18:.2f}" y1="{top + 12:.2f}" x2="{left + 56:.2f}" y2="{top + 12:.2f}" stroke="#0072B2" stroke-width="2"/>',
            f'<text x="{left + 64:.2f}" y="{top + 16:.2f}" font-family="serif" font-size="12">MIL</text>',
            f'<line x1="{left + 18:.2f}" y1="{top + 32:.2f}" x2="{left + 56:.2f}" y2="{top + 32:.2f}" stroke="#D55E00" stroke-width="2"/>',
            f'<text x="{left + 64:.2f}" y="{top + 36:.2f}" font-family="serif" font-size="12">GMT</text>',
            "</svg>",
        ]
    )
    path.write_text("\n".join(elements), encoding="utf-8")


def polyline_svg(x_values: Sequence[float], y_values: Sequence[float], geometry: dict[str, object], color: str) -> str:
    points = " ".join(f"{map_x(x, geometry):.2f},{map_y(y, geometry):.2f}" for x, y in zip(x_values, y_values))
    return f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.0"/>'


def sample_markers(x_values: Sequence[float], y_values: Sequence[float]) -> list[tuple[float, float]]:
    step = max(1, len(x_values) // 12)
    return [(x, y) for idx, (x, y) in enumerate(zip(x_values, y_values)) if idx % step == 0 or idx == len(x_values) - 1]


def save_line_png(
    path: Path,
    xlabel: str,
    ylabel: str,
    x_values: Sequence[float],
    mil_y: Sequence[float],
    gmt_y: Sequence[float],
    geometry: dict[str, object],
) -> None:
    image = new_image(int(geometry["width"]), int(geometry["height"]), (255, 255, 255))
    for tick in geometry["x_ticks"]:
        x = int(round(map_x(float(tick), geometry)))
        draw_line(image, x, int(geometry["top"]), x, int(geometry["bottom"]), (217, 217, 217))
        draw_text(image, x - 10, int(geometry["bottom"]) + 20, format_tick(float(tick)), (0, 0, 0), 1)
    for tick in geometry["y_ticks"]:
        y = int(round(map_y(float(tick), geometry)))
        draw_line(image, int(geometry["left"]), y, int(geometry["right"]), y, (217, 217, 217))
        draw_text(image, int(geometry["left"]) - 54, y - 4, format_tick(float(tick)), (0, 0, 0), 1)
    draw_line(image, int(geometry["left"]), int(geometry["bottom"]), int(geometry["right"]), int(geometry["bottom"]), (0, 0, 0))
    draw_line(image, int(geometry["left"]), int(geometry["top"]), int(geometry["left"]), int(geometry["bottom"]), (0, 0, 0))
    draw_png_polyline(image, x_values, mil_y, geometry, (0, 114, 178))
    draw_png_polyline(image, x_values, gmt_y, geometry, (213, 94, 0))
    draw_text(image, int(0.5 * (float(geometry["left"]) + float(geometry["right"]))) - 20, int(geometry["height"]) - 30, xlabel, (0, 0, 0), 2)
    draw_text(image, 12, 12, ylabel, (0, 0, 0), 1)
    draw_text(image, int(geometry["left"]) + 18, int(geometry["top"]) + 8, "MIL", (0, 114, 178), 2)
    draw_text(image, int(geometry["left"]) + 18, int(geometry["top"]) + 32, "GMT", (213, 94, 0), 1)
    write_png(path, image)


def format_tick(value: float) -> str:
    if abs(value) >= 100.0:
        return f"{value:.0f}"
    if abs(value) >= 10.0:
        return f"{value:.1f}"
    if abs(value) >= 1.0:
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{value:.3f}".rstrip("0").rstrip(".")


def save_fallback_summary_bars(verification: VerificationResult, figures_dir: Path) -> None:
    metrics = [
        ("total thrust [N]", verification.mil.result.thrust_n, verification.gmt.result.thrust_n),
        ("total torque [N*m]", verification.mil.result.torque_nm, verification.gmt.result.torque_nm),
        ("power [W]", verification.mil.result.power_w, verification.gmt.result.power_w),
        ("efficiency [-]", verification.metrics.eta_mil, verification.metrics.eta_gmt),
    ]
    save_summary_svg(figures_dir / "optimized_mil_vs_gmt_summary_bar_chart.svg", metrics)
    save_summary_png(figures_dir / "optimized_mil_vs_gmt_summary_bar_chart.png", metrics)


def panel_geometry(panel_idx: int, width: int, height: int) -> tuple[int, int, int, int]:
    col = panel_idx % 2
    row = panel_idx // 2
    panel_w = width // 2
    panel_h = height // 2
    return col * panel_w + 72, row * panel_h + 36, panel_w - 108, panel_h - 86


def save_summary_svg(path: Path, metrics: Sequence[tuple[str, float, float]]) -> None:
    width, height = 760, 560
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    for idx, (label, mil_value, gmt_value) in enumerate(metrics):
        x0, y0, w, h = panel_geometry(idx, width, height)
        ymax = max(mil_value, gmt_value) * 1.18
        elements.append(f'<text x="{x0 + w / 2:.1f}" y="{y0 - 10:.1f}" text-anchor="middle" font-family="serif" font-size="12">{html.escape(label)}</text>')
        elements.append(f'<line x1="{x0}" y1="{y0 + h}" x2="{x0 + w}" y2="{y0 + h}" stroke="black"/>')
        elements.append(f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y0 + h}" stroke="black"/>')
        for bar_idx, (name, value, color) in enumerate((("MIL", mil_value, "#0072B2"), ("GMT", gmt_value, "#D55E00"))):
            bw = 46
            bx = x0 + 70 + bar_idx * 86
            bh = safe_div(value, ymax) * h
            by = y0 + h - bh
            elements.append(f'<rect x="{bx}" y="{by:.1f}" width="{bw}" height="{bh:.1f}" fill="{color}"/>')
            elements.append(f'<text x="{bx + bw / 2}" y="{y0 + h + 18}" text-anchor="middle" font-family="serif" font-size="11">{name}</text>')
            elements.append(f'<text x="{bx + bw / 2}" y="{by - 4:.1f}" text-anchor="middle" font-family="serif" font-size="10">{format_tick(value)}</text>')
    elements.append("</svg>")
    path.write_text("\n".join(elements), encoding="utf-8")


def save_summary_png(path: Path, metrics: Sequence[tuple[str, float, float]]) -> None:
    width, height = 760, 560
    image = new_image(width, height, (255, 255, 255))
    for idx, (label, mil_value, gmt_value) in enumerate(metrics):
        x0, y0, w, h = panel_geometry(idx, width, height)
        ymax = max(mil_value, gmt_value) * 1.18
        draw_text(image, x0 + 8, y0 - 24, label, (0, 0, 0), 1)
        draw_line(image, x0, y0 + h, x0 + w, y0 + h, (0, 0, 0))
        draw_line(image, x0, y0, x0, y0 + h, (0, 0, 0))
        for bar_idx, (name, value, color) in enumerate((("MIL", mil_value, (0, 114, 178)), ("GMT", gmt_value, (213, 94, 0)))):
            bw = 46
            bx = x0 + 70 + bar_idx * 86
            bh = int(safe_div(value, ymax) * h)
            by = y0 + h - bh
            fill_rect(image, bx, by, bw, bh, color)
            draw_text(image, bx, y0 + h + 16, name, (0, 0, 0), 1)
            draw_text(image, bx, by - 12, format_tick(value), (0, 0, 0), 1)
    write_png(path, image)


def new_image(width: int, height: int, color: tuple[int, int, int]) -> dict[str, object]:
    return {"width": width, "height": height, "pixels": bytearray(color * (width * height))}


def set_pixel(image: dict[str, object], x: int, y: int, color: tuple[int, int, int]) -> None:
    width = int(image["width"])
    height = int(image["height"])
    if x < 0 or y < 0 or x >= width or y >= height:
        return
    idx = (y * width + x) * 3
    pixels = image["pixels"]
    pixels[idx : idx + 3] = bytes(color)


def draw_line(image: dict[str, object], x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        set_pixel(image, x0, y0, color)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def draw_png_polyline(
    image: dict[str, object],
    x_values: Sequence[float],
    y_values: Sequence[float],
    geometry: dict[str, object],
    color: tuple[int, int, int],
) -> None:
    points = [(int(round(map_x(x, geometry))), int(round(map_y(y, geometry)))) for x, y in zip(x_values, y_values)]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        draw_line(image, x0, y0, x1, y1, color)
        draw_line(image, x0, y0 + 1, x1, y1 + 1, color)


def fill_rect(image: dict[str, object], x: int, y: int, width: int, height: int, color: tuple[int, int, int]) -> None:
    for yy in range(y, y + height):
        for xx in range(x, x + width):
            set_pixel(image, xx, yy, color)


FONT_5X7 = {
    "0": ["111", "101", "101", "101", "101", "101", "111"],
    "1": ["010", "110", "010", "010", "010", "010", "111"],
    "2": ["111", "001", "001", "111", "100", "100", "111"],
    "3": ["111", "001", "001", "111", "001", "001", "111"],
    "4": ["101", "101", "101", "111", "001", "001", "001"],
    "5": ["111", "100", "100", "111", "001", "001", "111"],
    "6": ["111", "100", "100", "111", "101", "101", "111"],
    "7": ["111", "001", "001", "010", "010", "010", "010"],
    "8": ["111", "101", "101", "111", "101", "101", "111"],
    "9": ["111", "101", "101", "111", "001", "001", "111"],
    "A": ["010", "101", "101", "111", "101", "101", "101"],
    "B": ["110", "101", "101", "110", "101", "101", "110"],
    "C": ["111", "100", "100", "100", "100", "100", "111"],
    "D": ["110", "101", "101", "101", "101", "101", "110"],
    "E": ["111", "100", "100", "111", "100", "100", "111"],
    "F": ["111", "100", "100", "111", "100", "100", "100"],
    "G": ["111", "100", "100", "101", "101", "101", "111"],
    "H": ["101", "101", "101", "111", "101", "101", "101"],
    "I": ["111", "010", "010", "010", "010", "010", "111"],
    "J": ["001", "001", "001", "001", "101", "101", "111"],
    "K": ["101", "101", "110", "100", "110", "101", "101"],
    "L": ["100", "100", "100", "100", "100", "100", "111"],
    "M": ["101", "111", "111", "101", "101", "101", "101"],
    "N": ["101", "111", "111", "111", "101", "101", "101"],
    "O": ["111", "101", "101", "101", "101", "101", "111"],
    "P": ["111", "101", "101", "111", "100", "100", "100"],
    "Q": ["111", "101", "101", "101", "111", "001", "001"],
    "R": ["110", "101", "101", "110", "101", "101", "101"],
    "S": ["111", "100", "100", "111", "001", "001", "111"],
    "T": ["111", "010", "010", "010", "010", "010", "010"],
    "U": ["101", "101", "101", "101", "101", "101", "111"],
    "V": ["101", "101", "101", "101", "101", "101", "010"],
    "W": ["101", "101", "101", "101", "111", "111", "101"],
    "X": ["101", "101", "101", "010", "101", "101", "101"],
    "Y": ["101", "101", "101", "010", "010", "010", "010"],
    "Z": ["111", "001", "001", "010", "100", "100", "111"],
    "-": ["000", "000", "000", "111", "000", "000", "000"],
    ".": ["000", "000", "000", "000", "000", "110", "110"],
    "/": ["001", "001", "010", "010", "010", "100", "100"],
    "[": ["110", "100", "100", "100", "100", "100", "110"],
    "]": ["011", "001", "001", "001", "001", "001", "011"],
    "(": ["010", "100", "100", "100", "100", "100", "010"],
    ")": ["010", "001", "001", "001", "001", "001", "010"],
    "*": ["000", "101", "010", "111", "010", "101", "000"],
    "%": ["101", "001", "010", "010", "010", "100", "101"],
    " ": ["000", "000", "000", "000", "000", "000", "000"],
}


def draw_text(image: dict[str, object], x: int, y: int, text: str, color: tuple[int, int, int], scale: int = 1) -> None:
    cursor = x
    for char in text.upper():
        glyph = FONT_5X7.get(char, FONT_5X7[" "])
        for row_idx, row in enumerate(glyph):
            for col_idx, bit in enumerate(row):
                if bit == "1":
                    fill_rect(image, cursor + col_idx * scale, y + row_idx * scale, scale, scale, color)
        cursor += (len(glyph[0]) + 1) * scale


def write_png(path: Path, image: dict[str, object]) -> None:
    width = int(image["width"])
    height = int(image["height"])
    pixels = image["pixels"]
    rows = [b"\x00" + bytes(pixels[y * width * 3 : (y + 1) * width * 3]) for y in range(height)]
    compressed = zlib.compress(b"".join(rows), level=9)
    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png.extend(png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
    png.extend(png_chunk(b"IDAT", compressed))
    png.extend(png_chunk(b"IEND", b""))
    path.write_bytes(bytes(png))


def png_chunk(kind: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify MIL and GMT propeller solver outputs against global-minimum-torque theory.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--preset", choices=["thesis_gmt_22_static"], default=None)
    parser.add_argument("--diameter-in", type=float, default=22.0)
    parser.add_argument("--rpm", type=float, default=4000.0)
    parser.add_argument("--thrust", type=float, default=35.0, help="Target thrust in newtons.")
    parser.add_argument("--rho", type=float, default=1.225, help="Air density in kg/m^3.")
    parser.add_argument("--v-inf", type=float, default=0.0, help="Freestream velocity in m/s.")
    parser.add_argument("--blades", type=int, default=2)
    parser.add_argument("--hub-radius-fraction", type=float, default=0.20)
    parser.add_argument("--mode", choices=["reference_regularized", "free_optimize", "reproduce_reference"], default="reference_regularized")
    parser.add_argument("--optimizer", choices=["coordinate_search", "auto", "scipy"], default="coordinate_search")
    parser.add_argument("--max-iterations", type=int, default=35)
    parser.add_argument("--tolerance", type=float, default=1.0e-4)
    parser.add_argument("--chord-min-in", type=float, default=0.50)
    parser.add_argument("--chord-max-in", type=float, default=None)
    parser.add_argument("--chord-blend", type=float, default=0.58)
    parser.add_argument("--twist-blend", type=float, default=0.20)
    parser.add_argument("--smooth-passes", type=int, default=2)
    parser.add_argument("--alpha-min", type=float, default=-2.0)
    parser.add_argument("--alpha-max", type=float, default=18.0)
    parser.add_argument("--alpha-step", type=float, default=0.5)
    parser.add_argument("--reference-csv", type=Path, default=default_reference_csv_path())
    parser.add_argument("--reference-geometry-csv", type=Path, default=None)
    parser.add_argument("--comparison-geometry", choices=["same", "scaled_reference"], default="same")
    parser.add_argument("--shared-reference-variant", choices=REFERENCE_VARIANT_CHOICES, default="mil_22")
    parser.add_argument("--thrust-match-tol-pct", type=float, default=DEFAULT_THRUST_MATCH_TOL_PCT)
    parser.add_argument("--torque-reduction-tol-pct", type=float, default=DEFAULT_TORQUE_REDUCTION_TOL_PCT)
    parser.add_argument("--tip-loading-reduction-tol-pct", type=float, default=DEFAULT_TIP_REDUCTION_TOL_PCT)
    parser.add_argument("--cl-clipping-tol-pct", type=float, default=20.0)
    parser.add_argument("--chord-tolerance-m", type=float, default=DEFAULT_CHORD_TOL_M)
    parser.add_argument("--results-dir", type=Path, default=ROOT_DIR / "results" / "verification" / "theory")
    parser.add_argument("--figures-dir", type=Path, default=ROOT_DIR / "figures_paper" / "verification" / "theory")
    parser.add_argument("--skip-plots", action="store_true")
    parser.add_argument("--write-reference-cases", action="store_true")
    parser.add_argument("--strict-exit-code", action="store_true", help="Return exit code 1 when theory checks fail.")
    return parser


def apply_verification_preset(args: argparse.Namespace) -> argparse.Namespace:
    if args.preset == "thesis_gmt_22_static":
        args.diameter_in = 22.0
        args.rpm = 4000.0
        args.thrust = 35.0
        args.v_inf = 0.0
        args.blades = 2
        args.mode = "reference_regularized"
    return args


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = apply_verification_preset(parser.parse_args(argv))
    verification = run_verification(args)
    print(f"Wrote summary CSV to: {verification.results_dir / 'theory_verification_summary.csv'}")
    print(f"Wrote report to: {verification.results_dir / 'theory_verification_report.txt'}")
    if not args.skip_plots:
        print(f"Wrote plots to: {verification.figures_dir}")
    print(f"Verification outcome: {verification.outcome}")
    if not verification.theory_verified:
        print("WARNING: GMT behavior not verified under this configuration.")
        return 1 if args.strict_exit_code else 0
    print("GMT behavior verified under this configuration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
