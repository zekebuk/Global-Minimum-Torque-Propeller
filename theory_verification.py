from __future__ import annotations

import argparse
import csv
import html
import math
import struct
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence


ROOT_DIR = Path(__file__).resolve().parent
CODE_DIR = ROOT_DIR / "Code"
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
    reference_case_name,
    validate_user_inputs,
    write_convergence_history_csv,
    write_csv,
    write_summary_json,
)
from common_plot_style import apply_matplotlib_style, save_matplotlib_figure  # noqa: E402


DEFAULT_THRUST_MATCH_TOL_PCT = 5.0
DEFAULT_TORQUE_REDUCTION_TOL_PCT = 0.0
DEFAULT_TIP_REDUCTION_TOL_PCT = 10.0
DEFAULT_CHORD_TOL_M = 1.0e-6
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
        lock_chord=True,
        lock_airfoil=True,
        optimize_twist_only=True,
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
    output_dir.mkdir(parents=True, exist_ok=True)
    output_name = case_name or optimized_case_name(result.config.spanload_type)
    write_csv(result, output_dir / f"{output_name}.csv")
    write_summary_json(result, output_dir / "solver_summary.json")
    write_convergence_history_csv(result.convergence_history, output_dir / "convergence_history.csv")


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
    return checks


def coefficient_match(solver_value: float, recomputed_value: float) -> bool:
    return close_enough(solver_value, recomputed_value, 1.0e-8 * max(1.0, abs(recomputed_value)))


def build_warnings(
    mil: SolverCase,
    gmt: SolverCase,
    checks: Sequence[VerificationCheck],
    unit_warnings: Sequence[str],
    metrics: VerificationMetrics,
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


def run_verification(args: argparse.Namespace) -> VerificationResult:
    results_dir = args.results_dir
    figures_dir = args.figures_dir
    if figures_dir.name not in {"reference", "optimized", "comparison"}:
        figures_dir = figures_dir / "optimized"
    case_dir = results_dir.parent / "optimized" if results_dir.name == "comparison" else results_dir / "optimized"
    reference_dir = results_dir.parent / "reference" if results_dir.name == "comparison" else results_dir / "reference"
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

    mil_span = spanwise_from_result(mil.result)
    gmt_span = spanwise_from_result(gmt.result)
    common_grid, grids_identical = common_grid_for(mil_span.mu, gmt_span.mu)
    common_mil = interpolate_spanwise(mil_span, common_grid)
    common_gmt = interpolate_spanwise(gmt_span, common_grid)
    metrics = compute_metrics(mil, gmt, mil_span, gmt_span, common_grid, common_mil, common_gmt)
    checks = build_checks(mil, gmt, mil_span, gmt_span, metrics, grids_identical, args)
    warnings = build_warnings(mil, gmt, checks, unit_warnings, metrics)
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
        checks=checks,
        warnings=warnings,
        assumptions=assumptions,
        results_dir=results_dir,
        figures_dir=figures_dir,
    )
    write_summary_csv(verification, results_dir / "theory_verification_summary.csv")
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
        "Both cases are solved with chord_locked=True, airfoil_locked=True, optimize_twist_only=True.",
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    m = verification.metrics
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    m = verification.metrics
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
    save_line_pdf(figures_dir / f"{filename}.pdf", xlabel, ylabel, x_values, mil_y, gmt_y, geometry)
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


def save_line_pdf(
    path: Path,
    xlabel: str,
    ylabel: str,
    x_values: Sequence[float],
    mil_y: Sequence[float],
    gmt_y: Sequence[float],
    geometry: dict[str, object],
) -> None:
    width = float(geometry["width"])
    height = float(geometry["height"])
    commands: list[str] = []
    pdf_line(commands, 0, 0, width, 0, "#ffffff", 0.1)
    for tick in geometry["x_ticks"]:
        x = map_x(float(tick), geometry)
        pdf_line(commands, x, float(geometry["top"]), x, float(geometry["bottom"]), "#d9d9d9", 0.6, height)
        pdf_text(commands, x - 8, float(geometry["bottom"]) + 24, format_tick(float(tick)), 8, height)
    for tick in geometry["y_ticks"]:
        y = map_y(float(tick), geometry)
        pdf_line(commands, float(geometry["left"]), y, float(geometry["right"]), y, "#d9d9d9", 0.6, height)
        pdf_text(commands, float(geometry["left"]) - 44, y + 3, format_tick(float(tick)), 8, height)
    pdf_line(commands, float(geometry["left"]), float(geometry["bottom"]), float(geometry["right"]), float(geometry["bottom"]), "#000000", 1.0, height)
    pdf_line(commands, float(geometry["left"]), float(geometry["top"]), float(geometry["left"]), float(geometry["bottom"]), "#000000", 1.0, height)
    pdf_polyline(commands, x_values, mil_y, geometry, "#0072B2", height)
    pdf_polyline(commands, x_values, gmt_y, geometry, "#D55E00", height)
    pdf_text(commands, 0.5 * (float(geometry["left"]) + float(geometry["right"])) - 10, height - 24, xlabel, 10, height)
    pdf_text(commands, 18, 0.5 * (float(geometry["top"]) + float(geometry["bottom"])) + 50, ylabel, 10, height, rotate=True)
    pdf_line(commands, float(geometry["left"]) + 18, float(geometry["top"]) + 12, float(geometry["left"]) + 56, float(geometry["top"]) + 12, "#0072B2", 1.5, height)
    pdf_text(commands, float(geometry["left"]) + 64, float(geometry["top"]) + 16, "MIL", 9, height)
    pdf_line(commands, float(geometry["left"]) + 18, float(geometry["top"]) + 32, float(geometry["left"]) + 56, float(geometry["top"]) + 32, "#D55E00", 1.5, height)
    pdf_text(commands, float(geometry["left"]) + 64, float(geometry["top"]) + 36, "GMT", 9, height)
    write_pdf(path, width, height, "\n".join(commands))


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
    save_summary_pdf(figures_dir / "optimized_mil_vs_gmt_summary_bar_chart.pdf", metrics)
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


def save_summary_pdf(path: Path, metrics: Sequence[tuple[str, float, float]]) -> None:
    width, height = 760.0, 560.0
    commands: list[str] = []
    for idx, (label, mil_value, gmt_value) in enumerate(metrics):
        x0, y0, w, h = panel_geometry(idx, int(width), int(height))
        ymax = max(mil_value, gmt_value) * 1.18
        pdf_text(commands, x0 + w / 2 - 35, y0 - 10, label, 9, height)
        pdf_line(commands, x0, y0 + h, x0 + w, y0 + h, "#000000", 0.8, height)
        pdf_line(commands, x0, y0, x0, y0 + h, "#000000", 0.8, height)
        for bar_idx, (name, value, color) in enumerate((("MIL", mil_value, "#0072B2"), ("GMT", gmt_value, "#D55E00"))):
            bw = 46
            bx = x0 + 70 + bar_idx * 86
            bh = safe_div(value, ymax) * h
            by = y0 + h - bh
            pdf_rect(commands, bx, by, bw, bh, color, height)
            pdf_text(commands, bx + 6, y0 + h + 18, name, 8, height)
            pdf_text(commands, bx + 4, by - 4, format_tick(value), 8, height)
    write_pdf(path, width, height, "\n".join(commands))


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


def pdf_color(color: str) -> tuple[float, float, float]:
    color = color.lstrip("#")
    return int(color[0:2], 16) / 255.0, int(color[2:4], 16) / 255.0, int(color[4:6], 16) / 255.0


def pdf_line(
    commands: list[str],
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    color: str,
    width: float,
    page_height: float = 480.0,
) -> None:
    r, g, b = pdf_color(color)
    commands.append(f"{r:.3f} {g:.3f} {b:.3f} RG {width:.2f} w {x1:.2f} {page_height - y1:.2f} m {x2:.2f} {page_height - y2:.2f} l S")


def pdf_polyline(
    commands: list[str],
    x_values: Sequence[float],
    y_values: Sequence[float],
    geometry: dict[str, object],
    color: str,
    page_height: float,
) -> None:
    r, g, b = pdf_color(color)
    points = [(map_x(x, geometry), page_height - map_y(y, geometry)) for x, y in zip(x_values, y_values)]
    if not points:
        return
    command = [f"{r:.3f} {g:.3f} {b:.3f} RG 1.6 w {points[0][0]:.2f} {points[0][1]:.2f} m"]
    command.extend(f"{x:.2f} {y:.2f} l" for x, y in points[1:])
    command.append("S")
    commands.append(" ".join(command))


def pdf_text(
    commands: list[str],
    x: float,
    y: float,
    text: str,
    size: int,
    page_height: float,
    rotate: bool = False,
) -> None:
    escaped = pdf_escape(text)
    if rotate:
        commands.append(f"BT /F1 {size} Tf 0 1 -1 0 {x:.2f} {page_height - y:.2f} Tm ({escaped}) Tj ET")
    else:
        commands.append(f"BT /F1 {size} Tf 1 0 0 1 {x:.2f} {page_height - y:.2f} Tm ({escaped}) Tj ET")


def pdf_rect(commands: list[str], x: float, y: float, width: float, height: float, color: str, page_height: float) -> None:
    r, g, b = pdf_color(color)
    commands.append(f"{r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {page_height - y - height:.2f} {width:.2f} {height:.2f} re f")


def pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path: Path, width: float, height: float, content: str) -> None:
    stream = content.encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.2f} {height:.2f}] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>".encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{idx} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(bytes(output))


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
    parser.add_argument("--twist-blend", type=float, default=0.0)
    parser.add_argument("--smooth-passes", type=int, default=3)
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
    parser.add_argument("--results-dir", type=Path, default=ROOT_DIR / "results" / "geometry" / "comparison")
    parser.add_argument("--figures-dir", type=Path, default=ROOT_DIR / "figures_paper")
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
