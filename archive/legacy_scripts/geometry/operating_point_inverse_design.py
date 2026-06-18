from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
GEOMETRY_CODE_DIR = Path(__file__).resolve().parent
if str(GEOMETRY_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(GEOMETRY_CODE_DIR))

import bemt


INCH_TO_METER = 0.0254
DIAMETER_IN = 22.0
DIAMETER_M = DIAMETER_IN * INCH_TO_METER
RADIUS_M = 0.5 * DIAMETER_M
BLADE_COUNT = 2
RPM = 4000.0
TARGET_THRUST_N = 35.0
RHO = 1.225
MU_AIR_KG_M_S = 1.81e-5
V_INF_M_S = 0.0

REFERENCE_MIL = ROOT / "results" / "geometry_optimizer" / "final_exports" / "final_reference_mil_geometry.csv"
REFERENCE_GMT = ROOT / "results" / "geometry_optimizer" / "final_exports" / "final_reference_gmt_geometry.csv"
AIRFOIL_DIR = ROOT / "CAD" / "airfoils_good"

FINAL_ROOT = ROOT / "results" / "FINAL_THESIS_USE_THESE"
GEOMETRY_DIR = FINAL_ROOT / "01_geometry"
AERO_DIR = FINAL_ROOT / "02_aero_validation"
DIAGNOSTIC_DIR = FINAL_ROOT / "03_diagnostics"
LEGACY_FINAL_GEOMETRY_DIR = ROOT / "results" / "final_geometry"
REPORT_PATH = ROOT / "results" / "geometry_design_report.md"

MAX_FINAL_THRUST_ERROR_PCT = 1.0
MAX_SMOOTHING_DELTA_DEG = 1.0
MAX_FINAL_WRAPPER_DELTA_DEG = 0.25
MIN_MIL_TWIST_DROP_DEG = 0.015
MIN_ALPHA_DESIGN_DEG = 0.0
MAX_ALPHA_DESIGN_DEG = 14.0
TIP_REGION_MU = 0.75
GMT_INBOARD_MIDSPAN_MIN_MU = 0.25
GMT_INBOARD_MIDSPAN_MAX_MU = 0.65
GMT_OUTER_CROSSING_MIN_MU = 0.70
GMT_OUTER_CROSSING_MAX_MU = 0.92
MIN_GMT_INBOARD_ABOVE_FRACTION = 0.65
GMT_TIP_CONTINUITY_START_MU = 0.80
GMT_TIP_CONTINUITY_MIN_DROP_DEG = 0.03
MAX_GMT_OUTER_TWIST_INCREASE_DEG = 0.25
GMT_LAMBDA_MIN = 0.0
GMT_LAMBDA_MAX = 2.5
GMT_LAMBDA_GRID_STEP = 0.025
GMT_INBOARD_BOOST_AMPLITUDE_DEG = 4.0
GMT_OUTER_UNLOAD_AMPLITUDE_DEG = 3.0
GMT_TIP_UNLOAD_AMPLITUDE_DEG = 5.0
GMT_INBOARD_CENTER_MU = 0.50
GMT_INBOARD_WIDTH_MU = 0.26
GMT_OUTER_UNLOAD_START_MU = 0.74
GMT_TIP_UNLOAD_START_MU = 0.88
MIN_DESIRED_TIP_LOADING_REDUCTION_PCT = 10.0
MIN_DESIRED_CENTER_SHIFT_MU = 0.015
MIN_DESIRED_TWIST_DIFFERENCE_DEG = 1.5
MIN_STRONG_TORQUE_REDUCTION_PCT = 1.0
TIP_LOSS_FACTOR_FLOOR = 0.05
INDUCED_VELOCITY_RELAXATION = 0.35
MAX_INDUCED_VELOCITY_ITERATIONS = 80
INDUCED_VELOCITY_TOL_M_S = 1.0e-5
INFLOW_MODEL = "stationwise_annular_momentum_with_prandtl_tip_loss"


@dataclass(frozen=True)
class OperatingPoint:
    diameter_m: float = DIAMETER_M
    radius_m: float = RADIUS_M
    blade_count: int = BLADE_COUNT
    rpm: float = RPM
    target_thrust_n: float = TARGET_THRUST_N
    rho: float = RHO
    mu_air_kg_m_s: float = MU_AIR_KG_M_S
    v_inf_m_s: float = V_INF_M_S

    @property
    def omega_rad_s(self) -> float:
        return self.rpm * 2.0 * math.pi / 60.0

    @property
    def n_rev_per_s(self) -> float:
        return self.rpm / 60.0

    @property
    def disk_area_m2(self) -> float:
        return math.pi * self.radius_m**2

    @property
    def target_induced_velocity_m_s(self) -> float:
        return bemt.hover_induced_velocity(self.target_thrust_n, self.rho, self.radius_m)


@dataclass(frozen=True)
class ReferenceStation:
    mu: float
    radius_m: float
    chord_m: float
    airfoil: str
    mil_reference_twist_deg: float
    gmt_reference_twist_deg: float


@dataclass(frozen=True)
class DesignStation:
    case_label: str
    mu: float
    radius_m: float
    dr_m: float
    chord_m: float
    airfoil: str
    beta_initial_deg: float
    beta_optimized_deg: float
    beta_smoothed_deg: float
    beta_final_deg: float
    reference_twist_deg: float
    alpha_design_deg: float
    phi_design_deg: float
    design_method: str

    @property
    def beta_delta_from_reference_deg(self) -> float:
        return self.beta_final_deg - self.reference_twist_deg


@dataclass(frozen=True)
class AeroRow:
    values: dict[str, Any]


@dataclass(frozen=True)
class CaseDesign:
    label: str
    stations: list[DesignStation]
    aero_rows: list[dict[str, Any]]
    summary: dict[str, Any]
    warnings: list[str]
    optimization_history: list[dict[str, Any]]


@dataclass(frozen=True)
class DesignBundle:
    mil: CaseDesign
    gmt: CaseDesign
    summary: dict[str, Any]
    validation: dict[str, Any]


@dataclass(frozen=True)
class GmtLambdaModel:
    lambda_gmt: float
    inboard_boost_amplitude_deg: float = GMT_INBOARD_BOOST_AMPLITUDE_DEG
    outer_unload_amplitude_deg: float = GMT_OUTER_UNLOAD_AMPLITUDE_DEG
    tip_unload_amplitude_deg: float = GMT_TIP_UNLOAD_AMPLITUDE_DEG
    inboard_center_mu: float = GMT_INBOARD_CENTER_MU
    inboard_width_mu: float = GMT_INBOARD_WIDTH_MU
    outer_unload_start_mu: float = GMT_OUTER_UNLOAD_START_MU
    tip_unload_start_mu: float = GMT_TIP_UNLOAD_START_MU


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate operating-point inverse-designed MIL/GMT geometries.")
    parser.add_argument("--no-plots", action="store_true", help="Write CSV/JSON/report outputs but skip figures.")
    args = parser.parse_args(argv)

    bundle = run_design(write_outputs=True, make_plots=not args.no_plots)
    print("Operating-point inverse design complete")
    print(f"  MIL thrust: {bundle.mil.summary['total_thrust_N']:.6f} N")
    print(f"  GMT thrust: {bundle.gmt.summary['total_thrust_N']:.6f} N")
    print(f"  MIL torque: {bundle.mil.summary['total_torque_Nm']:.6f} N*m")
    print(f"  GMT torque: {bundle.gmt.summary['total_torque_Nm']:.6f} N*m")
    print(f"  GMT torque reduction: {bundle.summary['gmt_torque_reduction_pct']:.3f}%")
    for warning in bundle.summary["warnings"]:
        print(f"  WARNING: {warning}")
    print(f"  report: {REPORT_PATH}")
    return 0


def run_design(*, write_outputs: bool = True, make_plots: bool = True) -> DesignBundle:
    op = OperatingPoint()
    polar_database = bemt.load_polar_database(AIRFOIL_DIR)
    if not polar_database:
        raise RuntimeError(f"No airfoil polar CSV files found under {AIRFOIL_DIR}")

    reference = load_reference_pair(op)
    mil = design_mil(reference, op, polar_database)
    gmt = design_gmt(reference, op, polar_database, mil)
    validation = validation_report(mil, gmt, op)
    summary = design_summary(mil, gmt, op, validation)
    bundle = DesignBundle(mil=mil, gmt=gmt, summary=summary, validation=validation)

    if write_outputs:
        write_outputs_bundle(bundle, op, make_plots=make_plots)
    return bundle


def load_reference_pair(op: OperatingPoint) -> list[ReferenceStation]:
    mil_rows = read_geometry_rows(REFERENCE_MIL)
    gmt_rows = read_geometry_rows(REFERENCE_GMT)
    if len(mil_rows) != len(gmt_rows):
        raise ValueError(f"Reference station count mismatch: MIL={len(mil_rows)}, GMT={len(gmt_rows)}")

    stations: list[ReferenceStation] = []
    for index, (mil, gmt) in enumerate(zip(mil_rows, gmt_rows), start=1):
        mu = float(mil["mu"])
        gmt_mu = float(gmt["mu"])
        if abs(mu - gmt_mu) > 1.0e-8:
            raise ValueError(f"Reference mu mismatch at row {index}: MIL={mu}, GMT={gmt_mu}")
        airfoil = bemt.normalize_airfoil(mil["airfoil"])
        gmt_airfoil = bemt.normalize_airfoil(gmt["airfoil"])
        if airfoil != gmt_airfoil:
            raise ValueError(f"Reference airfoil mismatch at row {index}: MIL={airfoil}, GMT={gmt_airfoil}")
        stations.append(
            ReferenceStation(
                mu=mu,
                radius_m=mu * op.radius_m,
                chord_m=float(mil["chord_m"]),
                airfoil=airfoil,
                mil_reference_twist_deg=float(mil["twist_deg"]),
                gmt_reference_twist_deg=float(gmt["twist_deg"]),
            )
        )
    require_monotonic_mu([station.mu for station in stations], "reference geometry")
    return stations


def design_mil(
    reference: list[ReferenceStation],
    op: OperatingPoint,
    polar_database: dict[str, dict[float, dict[str, Any]]],
) -> CaseDesign:
    dr_values = station_widths_m(reference, op)
    phi_values = [phi_deg(station.radius_m, op, op.target_induced_velocity_m_s) for station in reference]
    alpha_initial = [
        efficient_alpha_target(station, op, polar_database, op.target_induced_velocity_m_s)
        for station in reference
    ]
    beta_initial = [phi + alpha for phi, alpha in zip(phi_values, alpha_initial)]
    beta_optimized, pitch_offset, history = pitch_correct_beta(
        "MIL",
        reference,
        dr_values,
        beta_initial,
        op,
        polar_database,
        reason="MIL local-efficiency pitch correction",
    )
    beta_smoothed, smoothing_report = smooth_monotonic_decreasing_twist(
        beta_optimized,
        max_delta_deg=MAX_SMOOTHING_DELTA_DEG,
        min_drop_deg=MIN_MIL_TWIST_DROP_DEG,
    )
    beta_final, final_offset, final_history = pitch_correct_beta(
        "MIL",
        reference,
        dr_values,
        beta_smoothed,
        op,
        polar_database,
        reason="MIL post-smoothing pitch correction",
    )
    rows, summary = evaluate_design_case("MIL", reference, dr_values, beta_final, op, polar_database)
    warnings = case_warnings(summary, smoothing_report)
    stations = make_design_stations(
        "MIL",
        reference,
        dr_values,
        beta_initial,
        beta_optimized,
        beta_smoothed,
        beta_final,
        summary["station_alpha_deg"],
        phi_values,
        reference_selector="mil",
        design_method="local_efficiency_alpha_plus_bemt_pitch_match",
    )
    summary.update(
        {
            "case_name": "operating_point_inverse_mil",
            "pitch_offset_deg": pitch_offset + final_offset,
            "initial_pitch_offset_deg": pitch_offset,
            "post_smoothing_pitch_offset_deg": final_offset,
            "max_smoothing_delta_deg": smoothing_report["max_abs_delta_deg"],
            "smoothing_method": smoothing_report["method"],
            "design_method": "MIL: local max-Cl/Cd alpha schedule, stationwise hover induced velocity, monotonic constrained smoothing, BEMT thrust pitch correction.",
        }
    )
    return CaseDesign(
        label="MIL",
        stations=stations,
        aero_rows=rows,
        summary=summary,
        warnings=warnings,
        optimization_history=history + final_history,
    )


def design_gmt(
    reference: list[ReferenceStation],
    op: OperatingPoint,
    polar_database: dict[str, dict[float, dict[str, Any]]],
    mil: CaseDesign,
) -> CaseDesign:
    dr_values = station_widths_m(reference, op)
    phi_values = [float(row["phi_deg"]) for row in mil.aero_rows]
    mil_alpha = [float(row["alpha_deg"]) for row in mil.aero_rows]
    mil_twist = [float(row["twist_deg"]) for row in mil.aero_rows]
    target_weights = gmt_target_weights(reference, dr_values)

    history: list[dict[str, Any]] = []
    for lambda_gmt in gmt_lambda_values():
        candidate = evaluate_gmt_lambda_candidate(
            lambda_gmt,
            reference,
            dr_values,
            phi_values,
            mil_alpha,
            mil_twist,
            op,
            polar_database,
            mil,
            target_weights,
        )
        history.append(candidate)

    if not history:
        raise RuntimeError("GMT lambda search did not evaluate any candidates")

    selected = choose_gmt_lambda_candidate(history)
    lambda_gmt = float(selected["lambda_gmt"])
    beta_initial = gmt_beta_from_lambda(0.0, phi_values, mil_alpha, reference, polar_database, clamp_alpha=False)
    beta_optimized = enforce_gmt_tip_unload_continuity(
        gmt_beta_from_lambda(
            lambda_gmt,
            phi_values,
            mil_alpha,
            reference,
            polar_database,
            clamp_alpha=True,
        ),
        reference,
    )
    beta_smoothed_raw, smoothing_report = smooth_with_limited_delta(
        beta_optimized,
        max_delta_deg=MAX_SMOOTHING_DELTA_DEG,
    )
    beta_smoothed = enforce_gmt_tip_unload_continuity(beta_smoothed_raw, reference)
    smoothing_report = smoothing_delta_report(
        beta_optimized,
        beta_smoothed,
        method="moving_average_plus_tip_unload_continuity_guard",
    )
    beta_final, final_offset, final_history = pitch_correct_beta(
        "GMT",
        reference,
        dr_values,
        beta_smoothed,
        op,
        polar_database,
        reason="GMT final post-smoothing pitch correction",
    )
    rows, summary = evaluate_design_case("GMT", reference, dr_values, beta_final, op, polar_database)
    apply_gmt_difference_metrics(summary, rows, mil, smoothing_report)
    actual_weights = normalized_loading_weights(rows)
    summary["spanload_error"] = math.sqrt(sum((a - t) ** 2 for a, t in zip(actual_weights, target_weights)))
    summary.update(
        {
            "case_name": "operating_point_inverse_gmt",
            "objective": selected["objective"],
            "lambda_gmt": lambda_gmt,
            "gmt_nominal_inboard_boost_deg": GMT_INBOARD_BOOST_AMPLITUDE_DEG,
            "gmt_nominal_outer_unload_deg": GMT_OUTER_UNLOAD_AMPLITUDE_DEG,
            "gmt_nominal_tip_unload_deg": GMT_TIP_UNLOAD_AMPLITUDE_DEG,
            "gmt_effective_inboard_boost_deg": lambda_gmt * GMT_INBOARD_BOOST_AMPLITUDE_DEG,
            "gmt_effective_outer_unload_deg": lambda_gmt * GMT_OUTER_UNLOAD_AMPLITUDE_DEG,
            "gmt_effective_tip_unload_deg": lambda_gmt * GMT_TIP_UNLOAD_AMPLITUDE_DEG,
            "lambda_search_method": "bounded scalar loading-shape search over lambda_gmt in [0, 2.5]",
            "lambda_selection_reason": selected["selection_reason"],
            "post_smoothing_pitch_offset_deg": final_offset,
            "max_smoothing_delta_deg": smoothing_report["max_abs_delta_deg"],
            "smoothing_method": smoothing_report["method"],
            "design_method": "GMT: low-dimensional lambda_gmt loading-shape optimization with inboard loading and tip unloading.",
        }
    )
    warnings = case_warnings(summary, smoothing_report)
    stations = make_design_stations(
        "GMT",
        reference,
        dr_values,
        beta_initial,
        beta_optimized,
        beta_smoothed,
        beta_final,
        summary["station_alpha_deg"],
        phi_values,
        reference_selector="gmt",
        design_method="lambda_gmt_loading_shape_to_bemt_twist",
    )
    return CaseDesign(
        label="GMT",
        stations=stations,
        aero_rows=rows,
        summary=summary,
        warnings=warnings,
        optimization_history=history + final_history,
    )


def evaluate_gmt_lambda_candidate(
    lambda_gmt: float,
    reference: list[ReferenceStation],
    dr_values: list[float],
    phi_values: list[float],
    mil_alpha: list[float],
    mil_twist: list[float],
    op: OperatingPoint,
    polar_database: dict[str, dict[float, dict[str, Any]]],
    mil: CaseDesign,
    target_weights: list[float],
) -> dict[str, Any]:
    beta = enforce_gmt_tip_unload_continuity(
        gmt_beta_from_lambda(lambda_gmt, phi_values, mil_alpha, reference, polar_database, clamp_alpha=True),
        reference,
    )
    beta_smoothed_raw, _ = smooth_with_limited_delta(beta, max_delta_deg=MAX_SMOOTHING_DELTA_DEG)
    beta_smoothed = enforce_gmt_tip_unload_continuity(beta_smoothed_raw, reference)
    smoothing_report = smoothing_delta_report(
        beta,
        beta_smoothed,
        method="moving_average_plus_tip_unload_continuity_guard",
    )
    beta_final, pitch_offset, _ = pitch_correct_beta(
        "GMT",
        reference,
        dr_values,
        beta_smoothed,
        op,
        polar_database,
        reason="GMT candidate pitch correction",
    )
    rows, summary = evaluate_design_case("GMT", reference, dr_values, beta_final, op, polar_database)
    actual_weights = normalized_loading_weights(rows)
    spanload_error = math.sqrt(sum((a - t) ** 2 for a, t in zip(actual_weights, target_weights)))
    summary["spanload_error"] = spanload_error
    summary["smoothing_delta_for_objective_deg"] = smoothing_report["max_abs_delta_deg"]
    apply_gmt_difference_metrics(summary, rows, mil, smoothing_report)
    objective = gmt_objective(summary, rows, mil, spanload_error)
    return {
        "stage": "lambda_search",
        "lambda_gmt": lambda_gmt,
        "objective": objective,
        "pitch_offset_deg": pitch_offset,
        "thrust_N": summary["total_thrust_N"],
        "thrust_error_pct": summary["thrust_error_pct"],
        "torque_Nm": summary["total_torque_Nm"],
        "torque_reduction_pct": summary["torque_reduction_pct_vs_mil"],
        "power_W": summary["power_W"],
        "tip_loading_fraction": summary["tip_loading_fraction"],
        "tip_loading_reduction_pct": summary["tip_loading_reduction_pct_vs_mil"],
        "center_of_thrust_mu": summary["center_of_thrust_mu"],
        "center_shift_inboard_delta_mu": summary["center_of_thrust_shift_inboard_delta_mu"],
        "max_abs_twist_delta_vs_mil_deg": summary["max_abs_twist_delta_vs_mil_deg"],
        "rms_twist_delta_vs_mil_deg": summary["rms_twist_delta_vs_mil_deg"],
        "tip_twist_delta_vs_mil_deg": summary["tip_twist_delta_vs_mil_deg"],
        "max_alpha_delta_vs_mil_deg": summary["max_alpha_delta_vs_mil_deg"],
        "midspan_alpha_delta_vs_mil_deg": summary["midspan_alpha_delta_vs_mil_deg"],
        "min_alpha_deg": summary["min_alpha_deg"],
        "max_alpha_deg": summary["max_alpha_deg"],
        "negative_thrust_station_count": summary["negative_thrust_station_count"],
        "alpha_outside_polar_range_count": summary["alpha_outside_polar_range_count"],
        "max_smoothing_delta_deg": smoothing_report["max_abs_delta_deg"],
        "spanload_error": spanload_error,
        "physically_valid": physically_valid_lambda_candidate(summary),
        "meets_tip_unload_target": summary["tip_loading_reduction_pct_vs_mil"]
        >= MIN_DESIRED_TIP_LOADING_REDUCTION_PCT,
        "meets_center_shift_target": summary["center_of_thrust_shift_inboard_delta_mu"]
        >= MIN_DESIRED_CENTER_SHIFT_MU,
        "meets_visible_twist_target": summary["max_abs_twist_delta_vs_mil_deg"]
        >= MIN_DESIRED_TWIST_DIFFERENCE_DEG,
        "meets_twist_relationship": bool(summary["gmt_twist_above_mil_inboard_midspan"])
        and bool(summary["gmt_twist_crosses_mil_outer_span"])
        and bool(summary["gmt_twist_below_mil_near_tip"]),
        "meets_tip_continuity_target": summary["gmt_max_outer_twist_increase_deg"]
        <= MAX_GMT_OUTER_TWIST_INCREASE_DEG,
        "torque_lower_than_mil": summary["total_torque_Nm"] < float(mil.summary["total_torque_Nm"]),
    }


def gmt_objective(
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    mil: CaseDesign,
    spanload_error: float,
) -> float:
    thrust_error = abs(float(summary["thrust_error_pct"])) / 100.0
    mil_torque = float(mil.summary["total_torque_Nm"])
    torque_norm = float(summary["total_torque_Nm"]) / max(mil_torque, 1.0e-9)
    torque_failure_penalty = max(0.0, torque_norm - 1.0) ** 2
    tip_deficit = max(0.0, MIN_DESIRED_TIP_LOADING_REDUCTION_PCT - summary["tip_loading_reduction_pct_vs_mil"])
    center_deficit = max(0.0, MIN_DESIRED_CENTER_SHIFT_MU - summary["center_of_thrust_shift_inboard_delta_mu"])
    twist_deficit = max(0.0, MIN_DESIRED_TWIST_DIFFERENCE_DEG - summary["max_abs_twist_delta_vs_mil_deg"])
    alpha_penalty = 0.0
    for row in rows:
        alpha = float(row["alpha_deg"])
        alpha_penalty += (max(0.0, MIN_ALPHA_DESIGN_DEG - alpha) / 2.0) ** 2
        alpha_penalty += (max(0.0, alpha - MAX_ALPHA_DESIGN_DEG) / 2.0) ** 2
        if parse_bool(row["alpha_outside_polar_range"]):
            alpha_penalty += 2.0
        if float(row["dT_N"]) < -1.0e-8:
            alpha_penalty += 10.0
    alpha_penalty /= max(len(rows), 1)
    curvature_penalty = twist_curvature_penalty([float(row["twist_deg"]) for row in rows])
    return (
        700.0 * thrust_error**2
        + torque_norm
        + 20.0 * torque_failure_penalty
        + 0.5 * spanload_error
        + 0.03 * tip_deficit**2
        + 800.0 * center_deficit**2
        + 0.5 * twist_deficit**2
        + 35.0 * alpha_penalty
        + 0.02 * curvature_penalty
    )


def gmt_beta_from_lambda(
    lambda_gmt: float,
    phi_values: list[float],
    mil_alpha: list[float],
    reference: list[ReferenceStation],
    polar_database: dict[str, dict[float, dict[str, Any]]],
    *,
    clamp_alpha: bool,
) -> list[float]:
    beta: list[float] = []
    for phi, alpha_mil, station in zip(phi_values, mil_alpha, reference):
        inboard_shape, outer_shape, tip_shape = gmt_loading_weights(station.mu)
        alpha = (
            alpha_mil
            + lambda_gmt * GMT_INBOARD_BOOST_AMPLITUDE_DEG * inboard_shape
            - lambda_gmt * GMT_OUTER_UNLOAD_AMPLITUDE_DEG * outer_shape
            - lambda_gmt * GMT_TIP_UNLOAD_AMPLITUDE_DEG * tip_shape
        )
        if clamp_alpha:
            lower, upper = bemt.polar_alpha_bounds(polar_database, station.airfoil)
            alpha = clamp(alpha, max(MIN_ALPHA_DESIGN_DEG + 0.1, lower + 0.1), min(MAX_ALPHA_DESIGN_DEG, upper - 0.1))
        beta.append(phi + alpha)
    return beta


def gmt_loading_weights(mu: float) -> tuple[float, float, float]:
    inboard = math.exp(-((mu - GMT_INBOARD_CENTER_MU) / GMT_INBOARD_WIDTH_MU) ** 2)
    outer = smoothstep((mu - GMT_OUTER_UNLOAD_START_MU) / max(1.0 - GMT_OUTER_UNLOAD_START_MU, 1.0e-9))
    tip = smoothstep((mu - GMT_TIP_UNLOAD_START_MU) / max(1.0 - GMT_TIP_UNLOAD_START_MU, 1.0e-9))
    return inboard, outer, tip


def gmt_lambda_values() -> list[float]:
    count = int(round((GMT_LAMBDA_MAX - GMT_LAMBDA_MIN) / GMT_LAMBDA_GRID_STEP))
    return [round(GMT_LAMBDA_MIN + idx * GMT_LAMBDA_GRID_STEP, 6) for idx in range(count + 1)]


def physically_valid_lambda_candidate(summary: dict[str, Any]) -> bool:
    return (
        abs(float(summary["thrust_error_pct"])) <= MAX_FINAL_THRUST_ERROR_PCT
        and int(summary["negative_thrust_station_count"]) == 0
        and int(summary["alpha_outside_polar_range_count"]) == 0
        and float(summary["smoothing_delta_for_objective_deg"]) <= MAX_SMOOTHING_DELTA_DEG + 1.0e-9
    )


def choose_gmt_lambda_candidate(history: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in history if parse_bool(row["physically_valid"])]
    if not valid:
        best_invalid = min(history, key=lambda row: float(row["objective"]))
        best_invalid["selection_reason"] = "no physically valid lambda candidate was found; selected lowest objective"
        return best_invalid

    full_target = [
        row
        for row in valid
        if parse_bool(row["torque_lower_than_mil"])
        and parse_bool(row["meets_tip_unload_target"])
        and parse_bool(row["meets_center_shift_target"])
        and parse_bool(row["meets_visible_twist_target"])
        and parse_bool(row["meets_twist_relationship"])
        and parse_bool(row["meets_tip_continuity_target"])
    ]
    if full_target:
        selected = min(full_target, key=lambda row: float(row["torque_Nm"]))
        selected["selection_reason"] = "selected lowest-torque physically valid lambda meeting all GMT shape targets"
        return selected

    strong_shape = [
        row
        for row in valid
        if parse_bool(row["meets_tip_unload_target"])
        and parse_bool(row["meets_center_shift_target"])
        and parse_bool(row["meets_visible_twist_target"])
        and parse_bool(row["meets_twist_relationship"])
        and parse_bool(row["meets_tip_continuity_target"])
    ]
    if strong_shape:
        selected = min(strong_shape, key=lambda row: float(row["torque_Nm"]))
        selected["selection_reason"] = (
            "selected strongest physically valid GMT shape; no strong-shape candidate also reduced torque"
        )
        return selected

    lower_torque = [row for row in valid if parse_bool(row["torque_lower_than_mil"])]
    if lower_torque:
        selected = max(
            lower_torque,
            key=lambda row: (
                float(row["max_abs_twist_delta_vs_mil_deg"]),
                float(row["tip_loading_reduction_pct"]),
                float(row["center_shift_inboard_delta_mu"]),
            ),
        )
        selected["selection_reason"] = (
            "selected strongest lower-torque physically valid lambda; full GMT shape targets were infeasible"
        )
        return selected

    selected = min(valid, key=lambda row: float(row["torque_Nm"]))
    selected["selection_reason"] = "selected lowest-torque physically valid lambda; GMT torque target was infeasible"
    return selected


def apply_gmt_difference_metrics(
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    mil: CaseDesign,
    smoothing_report: dict[str, float],
) -> None:
    mil_torque = float(mil.summary["total_torque_Nm"])
    mil_tip_fraction = float(mil.summary["tip_loading_fraction"])
    mil_center = float(mil.summary["center_of_thrust_mu"])
    mil_twist = [float(row["twist_deg"]) for row in mil.aero_rows]
    mil_alpha = [float(row["alpha_deg"]) for row in mil.aero_rows]
    mil_cl = [float(row["Cl"]) for row in mil.aero_rows]
    gmt_twist = [float(row["twist_deg"]) for row in rows]
    gmt_alpha = [float(row["alpha_deg"]) for row in rows]
    gmt_cl = [float(row["Cl"]) for row in rows]
    twist_deltas = [gmt - base for gmt, base in zip(gmt_twist, mil_twist)]
    alpha_deltas = [gmt - base for gmt, base in zip(gmt_alpha, mil_alpha)]
    cl_deltas = [gmt - base for gmt, base in zip(gmt_cl, mil_cl)]
    mu_values = [float(row["r_R"]) for row in rows]
    inboard_deltas = [
        delta
        for mu, delta in zip(mu_values, twist_deltas)
        if GMT_INBOARD_MIDSPAN_MIN_MU <= mu <= GMT_INBOARD_MIDSPAN_MAX_MU
    ]
    inboard_above_count = sum(delta > 0.0 for delta in inboard_deltas)
    crossing_mu = ""
    for index in range(1, len(twist_deltas)):
        if twist_deltas[index - 1] >= 0.0 and twist_deltas[index] < 0.0:
            crossing_mu = mu_values[index]
            break
    outer_twist_increases = [
        gmt_twist[index + 1] - gmt_twist[index]
        for index in range(len(gmt_twist) - 1)
        if mu_values[index] >= GMT_TIP_CONTINUITY_START_MU
    ]
    tip_fraction = loading_fraction(rows, "dT_N", lower_mu=TIP_REGION_MU)
    center = float(summary["center_of_thrust_mu"])
    summary["tip_loading_fraction"] = tip_fraction
    summary["torque_reduction_pct_vs_mil"] = (mil_torque - float(summary["total_torque_Nm"])) / max(mil_torque, 1.0e-12) * 100.0
    summary["tip_loading_reduction_pct_vs_mil"] = (mil_tip_fraction - tip_fraction) / max(mil_tip_fraction, 1.0e-12) * 100.0
    summary["center_of_thrust_shift_inboard_delta_mu"] = mil_center - center
    summary["max_abs_twist_delta_vs_mil_deg"] = max((abs(value) for value in twist_deltas), default=0.0)
    summary["rms_twist_delta_vs_mil_deg"] = rms(twist_deltas)
    summary["tip_twist_delta_vs_mil_deg"] = twist_deltas[-1] if twist_deltas else 0.0
    summary["max_alpha_delta_vs_mil_deg"] = max(alpha_deltas, default=0.0)
    summary["min_alpha_delta_vs_mil_deg"] = min(alpha_deltas, default=0.0)
    summary["midspan_alpha_delta_vs_mil_deg"] = alpha_deltas[len(alpha_deltas) // 2] if alpha_deltas else 0.0
    summary["max_Cl_delta_vs_mil"] = max(cl_deltas, default=0.0)
    summary["min_Cl_delta_vs_mil"] = min(cl_deltas, default=0.0)
    summary["smoothing_delta_for_objective_deg"] = smoothing_report["max_abs_delta_deg"]
    summary["gmt_inboard_midspan_above_mil_fraction"] = (
        inboard_above_count / max(len(inboard_deltas), 1)
    )
    summary["gmt_twist_above_mil_inboard_midspan"] = (
        summary["gmt_inboard_midspan_above_mil_fraction"] >= MIN_GMT_INBOARD_ABOVE_FRACTION
    )
    summary["gmt_twist_crossing_mu"] = crossing_mu
    summary["gmt_twist_crosses_mil_outer_span"] = (
        isinstance(crossing_mu, float)
        and GMT_OUTER_CROSSING_MIN_MU <= crossing_mu <= GMT_OUTER_CROSSING_MAX_MU
    )
    summary["gmt_twist_below_mil_near_tip"] = twist_deltas[-1] < 0.0 if twist_deltas else False
    summary["gmt_max_outer_twist_increase_deg"] = max(outer_twist_increases, default=0.0)
    summary["gmt_tip_continuity_guard_passed"] = (
        summary["gmt_max_outer_twist_increase_deg"] <= MAX_GMT_OUTER_TWIST_INCREASE_DEG
    )


def pitch_correct_beta(
    label: str,
    reference: list[ReferenceStation],
    dr_values: list[float],
    beta_base: list[float],
    op: OperatingPoint,
    polar_database: dict[str, dict[float, dict[str, Any]]],
    *,
    reason: str,
) -> tuple[list[float], float, list[dict[str, Any]]]:
    history: list[dict[str, Any]] = []

    def thrust_at(offset: float) -> float:
        beta = [value + offset for value in beta_base]
        _, summary = evaluate_design_case(label, reference, dr_values, beta, op, polar_database)
        history.append({"reason": reason, "offset_deg": offset, "thrust_N": summary["total_thrust_N"]})
        return float(summary["total_thrust_N"])

    lower = -12.0
    upper = 16.0
    t_lower = thrust_at(lower)
    t_upper = thrust_at(upper)
    expand_count = 0
    while not (t_lower <= op.target_thrust_n <= t_upper) and expand_count < 5:
        if t_lower > op.target_thrust_n:
            upper = lower
            t_upper = t_lower
            lower -= 8.0
            t_lower = thrust_at(lower)
        else:
            lower = upper
            t_lower = t_upper
            upper += 8.0
            t_upper = thrust_at(upper)
        expand_count += 1

    if not (t_lower <= op.target_thrust_n <= t_upper):
        lower_error = abs(t_lower - op.target_thrust_n)
        upper_error = abs(t_upper - op.target_thrust_n)
        offset = lower if lower_error <= upper_error else upper
        return [value + offset for value in beta_base], offset, history

    for _ in range(44):
        mid = 0.5 * (lower + upper)
        t_mid = thrust_at(mid)
        if abs(t_mid - op.target_thrust_n) / op.target_thrust_n <= 1.0e-5:
            return [value + mid for value in beta_base], mid, history
        if t_mid < op.target_thrust_n:
            lower = mid
        else:
            upper = mid
    offset = 0.5 * (lower + upper)
    return [value + offset for value in beta_base], offset, history


def evaluate_design_case(
    label: str,
    reference: list[ReferenceStation],
    dr_values: list[float],
    beta_values: list[float],
    op: OperatingPoint,
    polar_database: dict[str, dict[float, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    vi = op.target_induced_velocity_m_s
    for station, dr_m, beta in zip(reference, dr_values, beta_values):
        rows.append(evaluate_station(label, station, dr_m, beta, op, polar_database, vi))

    total_thrust = sum(float(row["dT_N"]) for row in rows)
    total_torque = sum(float(row["dQ_Nm"]) for row in rows)
    if not math.isfinite(total_thrust) or not math.isfinite(total_torque):
        raise ValueError(f"{label}: nonfinite thrust or torque")
    cumulative_t = 0.0
    cumulative_q = 0.0
    for row in rows:
        cumulative_t += float(row["dT_N"])
        cumulative_q += float(row["dQ_Nm"])
        row["cumulative_thrust_N"] = cumulative_t
        row["cumulative_torque_Nm"] = cumulative_q
        row["cumulative_thrust_fraction"] = cumulative_t / max(total_thrust, 1.0e-12)
        row["cumulative_torque_fraction"] = cumulative_q / max(total_torque, 1.0e-12)
    if rows:
        rows[-1]["cumulative_thrust_fraction"] = 1.0
        rows[-1]["cumulative_torque_fraction"] = 1.0

    omega = op.omega_rad_s
    power = total_torque * omega
    ct = total_thrust / (op.rho * op.n_rev_per_s**2 * op.diameter_m**4)
    cq = total_torque / (op.rho * op.n_rev_per_s**2 * op.diameter_m**5)
    cp = power / (op.rho * op.n_rev_per_s**3 * op.diameter_m**5)
    alpha_values = [float(row["alpha_deg"]) for row in rows]
    re_values = [float(row["Re"]) for row in rows]
    cl_values = [float(row["Cl"]) for row in rows]
    dt_values = [float(row["dT_N"]) for row in rows]
    vi_values = [float(row["Vi_m_s"]) for row in rows]
    summary = {
        "case_label": label,
        "diameter_m": op.diameter_m,
        "diameter_in": op.diameter_m / INCH_TO_METER,
        "radius_m": op.radius_m,
        "blade_count": op.blade_count,
        "rpm": op.rpm,
        "target_thrust_N": op.target_thrust_n,
        "rho_kg_m3": op.rho,
        "V_inf_m_s": op.v_inf_m_s,
        "inflow_model": INFLOW_MODEL,
        "actuator_disk_initial_induced_velocity_m_s": op.target_induced_velocity_m_s,
        "induced_velocity_m_s": sum(vi_values) / max(len(vi_values), 1),
        "mean_induced_velocity_m_s": sum(vi_values) / max(len(vi_values), 1),
        "min_induced_velocity_m_s": min(vi_values),
        "max_induced_velocity_m_s": max(vi_values),
        "total_thrust_N": total_thrust,
        "total_torque_Nm": total_torque,
        "power_W": power,
        "CT": ct,
        "CQ": cq,
        "CP": cp,
        "thrust_error_pct": (total_thrust - op.target_thrust_n) / op.target_thrust_n * 100.0,
        "min_alpha_deg": min(alpha_values),
        "max_alpha_deg": max(alpha_values),
        "min_Re": min(re_values),
        "max_Re": max(re_values),
        "min_Cl": min(cl_values),
        "max_Cl": max(cl_values),
        "min_dT_N": min(dt_values),
        "negative_thrust_station_count": sum(value < -1.0e-8 for value in dt_values),
        "alpha_outside_polar_range_count": sum(parse_bool(row["alpha_outside_polar_range"]) for row in rows),
        "station_alpha_deg": alpha_values,
        "tip_loading_fraction": loading_fraction(rows, "dT_N", lower_mu=TIP_REGION_MU),
        "center_of_thrust_mu": center_of_loading_mu(rows, "dT_N"),
        "spanload_error": 0.0,
    }
    return rows, summary


def evaluate_station(
    label: str,
    station: ReferenceStation,
    dr_m: float,
    beta_deg: float,
    op: OperatingPoint,
    polar_database: dict[str, dict[float, dict[str, Any]]],
    vi_m_s: float,
) -> dict[str, Any]:
    return evaluate_station_with_stationwise_induction(
        label,
        station,
        dr_m,
        beta_deg,
        op,
        polar_database,
        vi_m_s,
    )


def evaluate_station_fixed_induced_velocity(
    label: str,
    station: ReferenceStation,
    dr_m: float,
    beta_deg: float,
    op: OperatingPoint,
    polar_database: dict[str, dict[float, dict[str, Any]]],
    vi_m_s: float,
) -> dict[str, Any]:
    return bemt.evaluate_station(
        case_label=label,
        r_R=station.mu,
        radius_m=station.radius_m,
        dr_m=dr_m,
        chord_m=station.chord_m,
        twist_deg=beta_deg,
        airfoil=station.airfoil,
        rotor_radius_m=op.radius_m,
        blade_count=op.blade_count,
        rpm=op.rpm,
        rho_kg_m3=op.rho,
        mu_air_kg_m_s=op.mu_air_kg_m_s,
        v_inf_m_s=op.v_inf_m_s,
        induced_velocity_m_s=vi_m_s,
        polar_database=polar_database,
    )


def evaluate_station_with_stationwise_induction(
    label: str,
    station: ReferenceStation,
    dr_m: float,
    beta_deg: float,
    op: OperatingPoint,
    polar_database: dict[str, dict[float, dict[str, Any]]],
    initial_vi_m_s: float,
) -> dict[str, Any]:
    vi = max(initial_vi_m_s, 0.0)
    converged = False
    iterations = 0
    for iterations in range(1, MAX_INDUCED_VELOCITY_ITERATIONS + 1):
        row = evaluate_station_fixed_induced_velocity(label, station, dr_m, beta_deg, op, polar_database, vi)
        tip_loss = prandtl_tip_loss_factor(op.blade_count, station.mu, math.radians(float(row["phi_deg"])))
        d_t = max(float(row["dT_N"]), 0.0)
        if d_t <= 0.0:
            target_vi = 0.0
        else:
            denominator = 4.0 * math.pi * op.rho * tip_loss * max(station.radius_m, 1.0e-9) * max(dr_m, 1.0e-9)
            target_vi = math.sqrt(d_t / max(denominator, 1.0e-12))
        next_vi = (1.0 - INDUCED_VELOCITY_RELAXATION) * vi + INDUCED_VELOCITY_RELAXATION * target_vi
        if abs(next_vi - vi) <= INDUCED_VELOCITY_TOL_M_S:
            vi = next_vi
            converged = True
            break
        vi = next_vi

    row = evaluate_station_fixed_induced_velocity(label, station, dr_m, beta_deg, op, polar_database, vi)
    tip_loss = prandtl_tip_loss_factor(op.blade_count, station.mu, math.radians(float(row["phi_deg"])))
    row["inflow_model"] = INFLOW_MODEL
    row["actuator_disk_initial_Vi_m_s"] = op.target_induced_velocity_m_s
    row["tip_loss_factor"] = tip_loss
    row["induced_velocity_converged"] = converged
    row["induced_velocity_iterations"] = iterations
    return row


def prandtl_tip_loss_factor(blade_count: int, mu: float, phi_rad: float) -> float:
    sin_phi = max(abs(math.sin(phi_rad)), 1.0e-6)
    argument = 0.5 * blade_count * (1.0 - mu) / max(mu * sin_phi, 1.0e-9)
    if argument >= 50.0:
        return 1.0
    exponential = clamp(math.exp(-argument), 0.0, 1.0)
    return clamp((2.0 / math.pi) * math.acos(exponential), TIP_LOSS_FACTOR_FLOOR, 1.0)


def efficient_alpha_target(
    station: ReferenceStation,
    op: OperatingPoint,
    polar_database: dict[str, dict[float, dict[str, Any]]],
    vi_m_s: float,
) -> float:
    reynolds = station_reynolds(station, op, vi_m_s)
    lower, upper = bemt.polar_alpha_bounds(polar_database, station.airfoil)
    sample_min = max(MIN_ALPHA_DESIGN_DEG, lower + 0.25)
    sample_max = min(10.5, upper - 0.25)
    if sample_max <= sample_min:
        return clamp(4.0, lower, upper)
    best_alpha = sample_min
    best_score = -1.0e99
    steps = max(int(round((sample_max - sample_min) / 0.25)), 1)
    for idx in range(steps + 1):
        alpha = sample_min + (sample_max - sample_min) * idx / steps
        polar = bemt.lookup_polar(polar_database, station.airfoil, reynolds, alpha)
        if polar.cd <= 0.0 or polar.cl <= 0.0:
            continue
        score = polar.cl / polar.cd - 0.05 * max(0.0, alpha - 7.0) ** 2
        if score > best_score:
            best_score = score
            best_alpha = alpha
    return best_alpha


def station_reynolds(station: ReferenceStation, op: OperatingPoint, vi_m_s: float) -> float:
    v_tan = op.omega_rad_s * station.radius_m
    v_axial = op.v_inf_m_s + vi_m_s
    v_rel = math.hypot(v_tan, v_axial)
    return op.rho * v_rel * station.chord_m / op.mu_air_kg_m_s


def phi_deg(radius_m: float, op: OperatingPoint, vi_m_s: float) -> float:
    return math.degrees(math.atan2(op.v_inf_m_s + vi_m_s, op.omega_rad_s * radius_m))


def station_widths_m(reference: list[ReferenceStation], op: OperatingPoint) -> list[float]:
    widths: list[float] = []
    for idx, station in enumerate(reference):
        left = station.mu if idx == 0 else 0.5 * (reference[idx - 1].mu + station.mu)
        right = 1.0 if idx == len(reference) - 1 else 0.5 * (station.mu + reference[idx + 1].mu)
        widths.append(max(right - left, 1.0e-9) * op.radius_m)
    return widths


def smooth_with_limited_delta(values: list[float], *, max_delta_deg: float) -> tuple[list[float], dict[str, float]]:
    if len(values) < 3:
        return list(values), {"max_abs_delta_deg": 0.0}
    smoothed = list(values)
    for _ in range(2):
        updated = list(smoothed)
        for idx in range(1, len(values) - 1):
            candidate = 0.25 * smoothed[idx - 1] + 0.5 * smoothed[idx] + 0.25 * smoothed[idx + 1]
            delta = clamp(candidate - values[idx], -max_delta_deg, max_delta_deg)
            updated[idx] = values[idx] + delta
        smoothed = updated
    deltas = [smooth - raw for smooth, raw in zip(smoothed, values)]
    return smoothed, {"max_abs_delta_deg": max((abs(delta) for delta in deltas), default=0.0)}


def smoothing_delta_report(values: list[float], smoothed: list[float], *, method: str) -> dict[str, float | str]:
    deltas = [smooth - raw for smooth, raw in zip(smoothed, values)]
    return {
        "max_abs_delta_deg": max((abs(delta) for delta in deltas), default=0.0),
        "method": method,
    }


def enforce_gmt_tip_unload_continuity(
    values: list[float],
    reference: list[ReferenceStation],
) -> list[float]:
    guarded = list(values)
    for index in range(1, len(guarded)):
        if reference[index - 1].mu < GMT_TIP_CONTINUITY_START_MU:
            continue
        maximum_allowed = guarded[index - 1] - GMT_TIP_CONTINUITY_MIN_DROP_DEG
        if guarded[index] > maximum_allowed:
            guarded[index] = maximum_allowed
    return guarded


def smooth_monotonic_decreasing_twist(
    values: list[float],
    *,
    max_delta_deg: float,
    min_drop_deg: float,
) -> tuple[list[float], dict[str, float]]:
    if len(values) < 2:
        return list(values), {"max_abs_delta_deg": 0.0, "method": "not_needed"}

    shifted = [value + index * min_drop_deg for index, value in enumerate(values)]
    fitted_shifted = isotonic_nonincreasing_fit(shifted)
    fitted = [value - index * min_drop_deg for index, value in enumerate(fitted_shifted)]
    smoothed, smoothing_report = smooth_with_limited_delta(fitted, max_delta_deg=max_delta_deg)
    fitted_shifted = [value + index * min_drop_deg for index, value in enumerate(smoothed)]
    final_shifted = isotonic_nonincreasing_fit(fitted_shifted)
    final = [value - index * min_drop_deg for index, value in enumerate(final_shifted)]
    deltas = [smooth - raw for smooth, raw in zip(final, values)]
    return final, {
        "max_abs_delta_deg": max((abs(delta) for delta in deltas), default=0.0),
        "pre_monotonic_smoothing_delta_deg": smoothing_report["max_abs_delta_deg"],
        "method": "pava_nonincreasing_with_minimum_station_drop",
    }


def isotonic_nonincreasing_fit(values: list[float]) -> list[float]:
    blocks: list[dict[str, float | int]] = []
    for index, value in enumerate(values):
        blocks.append({"start": index, "end": index, "weight": 1.0, "mean": float(value)})
        while len(blocks) >= 2 and float(blocks[-2]["mean"]) < float(blocks[-1]["mean"]):
            right = blocks.pop()
            left = blocks.pop()
            weight = float(left["weight"]) + float(right["weight"])
            mean = (float(left["mean"]) * float(left["weight"]) + float(right["mean"]) * float(right["weight"])) / weight
            blocks.append({"start": int(left["start"]), "end": int(right["end"]), "weight": weight, "mean": mean})
    fitted = [0.0 for _ in values]
    for block in blocks:
        for index in range(int(block["start"]), int(block["end"]) + 1):
            fitted[index] = float(block["mean"])
    return fitted


def make_design_stations(
    label: str,
    reference: list[ReferenceStation],
    dr_values: list[float],
    beta_initial: list[float],
    beta_optimized: list[float],
    beta_smoothed: list[float],
    beta_final: list[float],
    alpha_design: list[float],
    phi_design: list[float],
    *,
    reference_selector: str,
    design_method: str,
) -> list[DesignStation]:
    stations: list[DesignStation] = []
    for station, dr_m, initial, optimized, smoothed, final, alpha, phi in zip(
        reference,
        dr_values,
        beta_initial,
        beta_optimized,
        beta_smoothed,
        beta_final,
        alpha_design,
        phi_design,
    ):
        reference_twist = station.mil_reference_twist_deg if reference_selector == "mil" else station.gmt_reference_twist_deg
        stations.append(
            DesignStation(
                case_label=label,
                mu=station.mu,
                radius_m=station.radius_m,
                dr_m=dr_m,
                chord_m=station.chord_m,
                airfoil=station.airfoil,
                beta_initial_deg=initial,
                beta_optimized_deg=optimized,
                beta_smoothed_deg=smoothed,
                beta_final_deg=final,
                reference_twist_deg=reference_twist,
                alpha_design_deg=alpha,
                phi_design_deg=phi,
                design_method=design_method,
            )
        )
    return stations


def case_warnings(summary: dict[str, Any], smoothing_report: dict[str, float]) -> list[str]:
    warnings: list[str] = []
    if abs(float(summary["thrust_error_pct"])) > MAX_FINAL_THRUST_ERROR_PCT:
        warnings.append(
            f"target thrust error exceeds {MAX_FINAL_THRUST_ERROR_PCT:.1f}%: {summary['thrust_error_pct']:.3f}%"
        )
    if int(summary["alpha_outside_polar_range_count"]) > 0:
        warnings.append(f"{summary['alpha_outside_polar_range_count']} station(s) outside polar alpha range")
    if int(summary["negative_thrust_station_count"]) > 0:
        warnings.append(f"{summary['negative_thrust_station_count']} station(s) have negative thrust")
    if smoothing_report["max_abs_delta_deg"] > MAX_SMOOTHING_DELTA_DEG + 1.0e-9:
        warnings.append("twist smoothing exceeded configured tolerance")
    return warnings


def validation_report(mil: CaseDesign, gmt: CaseDesign, op: OperatingPoint) -> dict[str, Any]:
    chord_deltas = [g.chord_m - m.chord_m for m, g in zip(mil.stations, gmt.stations)]
    same_airfoil = all(m.airfoil == g.airfoil for m, g in zip(mil.stations, gmt.stations))
    same_mu = all(abs(m.mu - g.mu) <= 1.0e-12 for m, g in zip(mil.stations, gmt.stations))
    mil_tip = loading_fraction(mil.aero_rows, "dT_N", lower_mu=TIP_REGION_MU)
    gmt_tip = loading_fraction(gmt.aero_rows, "dT_N", lower_mu=TIP_REGION_MU)
    mil_torque = float(mil.summary["total_torque_Nm"])
    gmt_torque = float(gmt.summary["total_torque_Nm"])
    center_shift = float(gmt.summary.get("center_of_thrust_shift_inboard_delta_mu", 0.0))
    tip_reduction_pct = float(gmt.summary.get("tip_loading_reduction_pct_vs_mil", 0.0))
    twist_difference = float(gmt.summary.get("max_abs_twist_delta_vs_mil_deg", 0.0))
    torque_reduction_pct = (mil_torque - gmt_torque) / max(mil_torque, 1.0e-12) * 100.0
    mil_twist_values = [station.beta_final_deg for station in mil.stations]
    gmt_twist_values = [station.beta_final_deg for station in gmt.stations]
    checks = {
        "same_radius_stations": same_mu,
        "same_chord_distribution": max((abs(delta) for delta in chord_deltas), default=0.0) <= 1.0e-12,
        "same_airfoil_schedule": same_airfoil,
        "mil_thrust_within_1pct": abs(float(mil.summary["thrust_error_pct"])) <= MAX_FINAL_THRUST_ERROR_PCT,
        "gmt_thrust_within_1pct": abs(float(gmt.summary["thrust_error_pct"])) <= MAX_FINAL_THRUST_ERROR_PCT,
        "static_hover_uses_induced_velocity": op.v_inf_m_s == 0.0 and op.target_induced_velocity_m_s > 0.0,
        "mil_no_negative_station_thrust": int(mil.summary["negative_thrust_station_count"]) == 0,
        "gmt_no_negative_station_thrust": int(gmt.summary["negative_thrust_station_count"]) == 0,
        "gmt_tip_unloaded_relative_to_mil": gmt_tip < mil_tip,
        "gmt_tip_loading_reduction_target": tip_reduction_pct >= MIN_DESIRED_TIP_LOADING_REDUCTION_PCT,
        "gmt_center_of_thrust_inboard_of_mil": center_shift > 0.0,
        "gmt_center_shift_target": center_shift >= MIN_DESIRED_CENTER_SHIFT_MU,
        "gmt_torque_lower_than_mil": gmt_torque < mil_torque,
        "gmt_strong_modeled_torque_benefit": torque_reduction_pct >= MIN_STRONG_TORQUE_REDUCTION_PCT,
        "gmt_geometry_not_barely_different": twist_difference >= MIN_DESIRED_TWIST_DIFFERENCE_DEG,
        "mil_twist_monotonic_decreasing": is_monotonic_decreasing(mil_twist_values),
        "gmt_twist_above_mil_inboard_midspan": bool(gmt.summary.get("gmt_twist_above_mil_inboard_midspan", False)),
        "gmt_twist_crosses_mil_outer_span": bool(gmt.summary.get("gmt_twist_crosses_mil_outer_span", False)),
        "gmt_twist_below_mil_near_tip": bool(gmt.summary.get("gmt_twist_below_mil_near_tip", False)),
        "gmt_tip_region_has_no_twist_cliff": bool(gmt.summary.get("gmt_tip_continuity_guard_passed", False)),
        "mil_no_artificial_plateau": no_artificial_plateau(mil_twist_values),
        "gmt_no_artificial_plateau": no_artificial_plateau(gmt_twist_values),
    }
    warnings: list[str] = []
    if not checks["mil_twist_monotonic_decreasing"]:
        warnings.append("MIL twist is not monotonically decreasing from root to tip")
    if not checks["gmt_tip_unloaded_relative_to_mil"]:
        warnings.append("GMT outboard loading fraction is not lower than MIL")
    if not checks["gmt_tip_loading_reduction_target"]:
        warnings.append(
            f"GMT tip loading reduction is below {MIN_DESIRED_TIP_LOADING_REDUCTION_PCT:.1f}% target: {tip_reduction_pct:.3f}%"
        )
    if not checks["gmt_center_shift_target"]:
        warnings.append(
            f"GMT center of thrust did not move inboard by {MIN_DESIRED_CENTER_SHIFT_MU:.3f} r/R: {center_shift:.6f}"
        )
    if not checks["gmt_torque_lower_than_mil"]:
        warnings.append(
            "Under this hover BEMT model, the physically valid GMT loading shift did not reduce torque relative to the cleaned MIL baseline."
        )
    if not checks["gmt_strong_modeled_torque_benefit"]:
        warnings.append("GMT geometry is physically valid but does not show a strong modeled torque benefit at this operating point.")
    if not checks["gmt_geometry_not_barely_different"]:
        warnings.append(
            f"GMT geometry is barely different from MIL; max twist delta is {twist_difference:.3f} deg"
        )
    if not checks["gmt_twist_above_mil_inboard_midspan"]:
        warnings.append("GMT twist is not sufficiently above MIL over the inboard/midspan region")
    if not checks["gmt_twist_crosses_mil_outer_span"]:
        warnings.append("GMT twist does not cross MIL in the required outer-span region")
    if not checks["gmt_twist_below_mil_near_tip"]:
        warnings.append("GMT twist is not below MIL near the tip")
    if not checks["gmt_tip_region_has_no_twist_cliff"]:
        warnings.append(
            f"GMT outer-span twist increase exceeds {MAX_GMT_OUTER_TWIST_INCREASE_DEG:.2f} deg tolerance"
        )
    if mil.warnings:
        warnings.extend(f"MIL: {warning}" for warning in mil.warnings)
    if gmt.warnings:
        warnings.extend(f"GMT: {warning}" for warning in gmt.warnings)
    return {
        "checks": checks,
        "warnings": warnings,
        "mil_tip_loading_fraction": mil_tip,
        "gmt_tip_loading_fraction": gmt_tip,
        "gmt_tip_loading_reduction_pct": tip_reduction_pct,
        "mil_center_of_thrust_mu": mil.summary["center_of_thrust_mu"],
        "gmt_center_of_thrust_mu": gmt.summary["center_of_thrust_mu"],
        "gmt_center_of_thrust_shift_inboard_delta_mu": center_shift,
    }


def design_summary(mil: CaseDesign, gmt: CaseDesign, op: OperatingPoint, validation: dict[str, Any]) -> dict[str, Any]:
    mil_torque = float(mil.summary["total_torque_Nm"])
    gmt_torque = float(gmt.summary["total_torque_Nm"])
    return {
        "generation_timestamp": datetime.now().isoformat(timespec="seconds"),
        "generating_script": "Code/geometry/operating_point_inverse_design.py",
        "operating_point": {
            "diameter_in": DIAMETER_IN,
            "diameter_m": op.diameter_m,
            "radius_m": op.radius_m,
            "blade_count": op.blade_count,
            "rpm": op.rpm,
            "V_inf_m_s": op.v_inf_m_s,
            "target_thrust_N": op.target_thrust_n,
            "rho_kg_m3": op.rho,
            "actuator_disk_initial_hover_induced_velocity_m_s": op.target_induced_velocity_m_s,
            "inflow_model": INFLOW_MODEL,
        },
        "fixed_variables": {
            "same_chord_distribution": True,
            "same_airfoil_schedule": True,
            "same_radial_stations": True,
        },
        "design_variable": "twist distribution beta(r)",
        "gmt_loading_shape_model": {
            "lambda_range": [GMT_LAMBDA_MIN, GMT_LAMBDA_MAX],
            "lambda_grid_step": GMT_LAMBDA_GRID_STEP,
            "selected_lambda_gmt": gmt.summary.get("lambda_gmt", ""),
            "inboard_boost_amplitude_deg_at_lambda_1": GMT_INBOARD_BOOST_AMPLITUDE_DEG,
            "outer_unload_amplitude_deg_at_lambda_1": GMT_OUTER_UNLOAD_AMPLITUDE_DEG,
            "tip_unload_amplitude_deg_at_lambda_1": GMT_TIP_UNLOAD_AMPLITUDE_DEG,
            "inboard_center_mu": GMT_INBOARD_CENTER_MU,
            "inboard_width_mu": GMT_INBOARD_WIDTH_MU,
            "outer_unload_start_mu": GMT_OUTER_UNLOAD_START_MU,
            "tip_unload_start_mu": GMT_TIP_UNLOAD_START_MU,
        },
        "reference_geometry_role": "NASA/Robb scaled geometries provide chord, airfoil schedule, radial stations, and reference-shape diagnostics; final twist is generated by operating-point BEMT inverse design.",
        "output_scope": [
            "final_mil_geometry.csv",
            "final_gmt_geometry.csv",
            "final_geometry_comparison.csv",
            "design_summary.json",
            "twist/alpha/Cl/dTdr/dQdr/cumulative thrust/cumulative torque plots",
        ],
        "mil": public_summary(mil.summary),
        "gmt": public_summary(gmt.summary),
        "summary_table": [summary_table_json_row(mil), summary_table_json_row(gmt)],
        "gmt_torque_reduction_pct": (mil_torque - gmt_torque) / max(mil_torque, 1.0e-12) * 100.0,
        "validation": validation,
        "warnings": validation["warnings"],
    }


def public_summary(summary: dict[str, Any]) -> dict[str, Any]:
    exclude = {"station_alpha_deg"}
    return {key: value for key, value in summary.items() if key not in exclude}


def summary_table_json_row(case: CaseDesign) -> dict[str, Any]:
    summary = case.summary
    return {
        "case_label": case.label,
        "thrust_N": summary["total_thrust_N"],
        "thrust_error_pct": summary["thrust_error_pct"],
        "torque_Nm": summary["total_torque_Nm"],
        "power_W": summary["power_W"],
        "CT": summary["CT"],
        "CQ": summary["CQ"],
        "CP": summary["CP"],
        "min_alpha_deg": summary["min_alpha_deg"],
        "max_alpha_deg": summary["max_alpha_deg"],
        "min_Re": summary["min_Re"],
        "max_Re": summary["max_Re"],
        "tip_loading_fraction": summary["tip_loading_fraction"],
        "center_of_thrust_r_R": summary["center_of_thrust_mu"],
        "lambda_gmt": summary.get("lambda_gmt", ""),
        "pitch_offset_deg": summary.get("pitch_offset_deg", summary.get("post_smoothing_pitch_offset_deg", "")),
        "max_smoothing_delta_deg": summary.get("max_smoothing_delta_deg", ""),
    }


def write_outputs_bundle(bundle: DesignBundle, op: OperatingPoint, *, make_plots: bool) -> None:
    GEOMETRY_DIR.mkdir(parents=True, exist_ok=True)

    write_geometry_csv(GEOMETRY_DIR / "final_mil_geometry.csv", bundle.mil)
    write_geometry_csv(GEOMETRY_DIR / "final_gmt_geometry.csv", bundle.gmt)
    write_comparison_csv(GEOMETRY_DIR / "final_geometry_comparison.csv", bundle)
    write_json(GEOMETRY_DIR / "design_summary.json", bundle.summary)
    if make_plots:
        write_plots(bundle)


def write_geometry_csv(path: Path, case: CaseDesign) -> None:
    fieldnames = [
        "case",
        "mu",
        "radius_m",
        "radius_in",
        "dr_m",
        "chord_m",
        "chord_in",
        "chord_over_R",
        "airfoil",
        "beta_initial_deg",
        "beta_optimized_deg",
        "beta_smoothed_deg",
        "beta_final_deg",
        "twist_deg",
        "beta_delta_from_reference_deg",
        "reference_twist_deg",
        "alpha_design_deg",
        "phi_design_deg",
        "design_method",
        "geometry_role",
        "notes",
    ]
    rows: list[dict[str, Any]] = []
    for station in case.stations:
        rows.append(
            {
                "case": case.label,
                "mu": station.mu,
                "radius_m": station.radius_m,
                "radius_in": station.radius_m / INCH_TO_METER,
                "dr_m": station.dr_m,
                "chord_m": station.chord_m,
                "chord_in": station.chord_m / INCH_TO_METER,
                "chord_over_R": station.chord_m / RADIUS_M,
                "airfoil": station.airfoil,
                "beta_initial_deg": station.beta_initial_deg,
                "beta_optimized_deg": station.beta_optimized_deg,
                "beta_smoothed_deg": station.beta_smoothed_deg,
                "beta_final_deg": station.beta_final_deg,
                "twist_deg": station.beta_final_deg,
                "beta_delta_from_reference_deg": station.beta_delta_from_reference_deg,
                "reference_twist_deg": station.reference_twist_deg,
                "alpha_design_deg": station.alpha_design_deg,
                "phi_design_deg": station.phi_design_deg,
                "design_method": station.design_method,
                "geometry_role": "operating point inverse-designed final thesis geometry",
                "notes": "Chord and airfoil schedule fixed between MIL and GMT; beta_final_deg is the BEMT inverse-designed twist.",
            }
        )
    write_csv(path, rows, fieldnames)


def write_aero_csv(path: Path, case: CaseDesign) -> None:
    fieldnames = [
        "case_label",
        "r_R",
        "r_m",
        "dr_m",
        "chord_m",
        "chord_in",
        "chord_over_R",
        "twist_deg",
        "beta_final_deg",
        "airfoil",
        "omega_rad_s",
        "Vi_m_s",
        "Vt_m_s",
        "Va_m_s",
        "Vrel_m_s",
        "phi_deg",
        "alpha_deg",
        "Re",
        "polar_Re_used",
        "polar_source",
        "Cl",
        "Cd",
        "Cm",
        "alpha_outside_polar_range",
        "q_Pa",
        "dL_N",
        "dD_N",
        "dT_N",
        "dQ_Nm",
        "qz_N_per_m",
        "qy_tangential_N_per_m",
        "torque_loading_Nm_per_m",
        "cumulative_thrust_N",
        "cumulative_torque_Nm",
        "cumulative_thrust_fraction",
        "cumulative_torque_fraction",
    ]
    write_csv(path, case.aero_rows, fieldnames)


def write_lambda_search_csv(path: Path, history: list[dict[str, Any]]) -> None:
    rows = [row for row in history if "lambda_gmt" in row]
    fieldnames = [
        "lambda_gmt",
        "objective",
        "pitch_offset_deg",
        "thrust_N",
        "thrust_error_pct",
        "torque_Nm",
        "torque_reduction_pct",
        "power_W",
        "tip_loading_fraction",
        "tip_loading_reduction_pct",
        "center_of_thrust_mu",
        "center_shift_inboard_delta_mu",
        "max_abs_twist_delta_vs_mil_deg",
        "rms_twist_delta_vs_mil_deg",
        "tip_twist_delta_vs_mil_deg",
        "max_alpha_delta_vs_mil_deg",
        "midspan_alpha_delta_vs_mil_deg",
        "min_alpha_deg",
        "max_alpha_deg",
        "negative_thrust_station_count",
        "alpha_outside_polar_range_count",
        "max_smoothing_delta_deg",
        "spanload_error",
        "physically_valid",
        "meets_tip_unload_target",
        "meets_center_shift_target",
        "meets_visible_twist_target",
        "torque_lower_than_mil",
        "selection_reason",
    ]
    write_csv(path, rows, fieldnames)


def write_summary_table_csv(path: Path, bundle: DesignBundle) -> None:
    write_csv(path, [summary_table_row(bundle, bundle.mil), summary_table_row(bundle, bundle.gmt)], SUMMARY_TABLE_FIELDNAMES)


def write_comparison_csv(path: Path, bundle: DesignBundle) -> None:
    rows: list[dict[str, Any]] = []
    for mil_station, gmt_station, mil_aero, gmt_aero in zip(
        bundle.mil.stations,
        bundle.gmt.stations,
        bundle.mil.aero_rows,
        bundle.gmt.aero_rows,
    ):
        rows.append(
            {
                "mu": mil_station.mu,
                "radius_m": mil_station.radius_m,
                "chord_m": mil_station.chord_m,
                "airfoil": mil_station.airfoil,
                "MIL_beta_final_deg": mil_station.beta_final_deg,
                "GMT_beta_final_deg": gmt_station.beta_final_deg,
                "GMT_minus_MIL_beta_deg": gmt_station.beta_final_deg - mil_station.beta_final_deg,
                "MIL_alpha_deg": mil_aero["alpha_deg"],
                "GMT_alpha_deg": gmt_aero["alpha_deg"],
                "GMT_minus_MIL_alpha_deg": float(gmt_aero["alpha_deg"]) - float(mil_aero["alpha_deg"]),
                "MIL_Cl": mil_aero["Cl"],
                "GMT_Cl": gmt_aero["Cl"],
                "GMT_minus_MIL_Cl": float(gmt_aero["Cl"]) - float(mil_aero["Cl"]),
                "MIL_Cd": mil_aero["Cd"],
                "GMT_Cd": gmt_aero["Cd"],
                "GMT_minus_MIL_Cd": float(gmt_aero["Cd"]) - float(mil_aero["Cd"]),
                "MIL_Re": mil_aero["Re"],
                "GMT_Re": gmt_aero["Re"],
                "MIL_dT_N": mil_aero["dT_N"],
                "GMT_dT_N": gmt_aero["dT_N"],
                "GMT_minus_MIL_dT_N": float(gmt_aero["dT_N"]) - float(mil_aero["dT_N"]),
                "MIL_dQ_Nm": mil_aero["dQ_Nm"],
                "GMT_dQ_Nm": gmt_aero["dQ_Nm"],
                "GMT_minus_MIL_dQ_Nm": float(gmt_aero["dQ_Nm"]) - float(mil_aero["dQ_Nm"]),
                "MIL_reference_twist_deg": mil_station.reference_twist_deg,
                "GMT_reference_twist_deg": gmt_station.reference_twist_deg,
            }
        )
    write_csv(
        path,
        rows,
        [
            "mu",
            "radius_m",
            "chord_m",
            "airfoil",
            "MIL_beta_final_deg",
            "GMT_beta_final_deg",
            "GMT_minus_MIL_beta_deg",
            "MIL_alpha_deg",
            "GMT_alpha_deg",
            "GMT_minus_MIL_alpha_deg",
            "MIL_Cl",
            "GMT_Cl",
            "GMT_minus_MIL_Cl",
            "MIL_Cd",
            "GMT_Cd",
            "GMT_minus_MIL_Cd",
            "MIL_Re",
            "GMT_Re",
            "MIL_dT_N",
            "GMT_dT_N",
            "GMT_minus_MIL_dT_N",
            "MIL_dQ_Nm",
            "GMT_dQ_Nm",
            "GMT_minus_MIL_dQ_Nm",
            "MIL_reference_twist_deg",
            "GMT_reference_twist_deg",
        ],
    )


def write_plots(bundle: DesignBundle) -> None:
    plot_specs = [
        ("twist_mil_vs_gmt", "beta_final_deg", "Twist beta [deg]", "stations"),
        ("alpha_mil_vs_gmt", "alpha_deg", "Angle of attack [deg]", "aero"),
        ("cl_mil_vs_gmt", "Cl", "Cl [-]", "aero"),
        ("dTdr_mil_vs_gmt", "qz_N_per_m", "dT/dr [N/m]", "aero"),
        ("dQdr_mil_vs_gmt", "torque_loading_Nm_per_m", "dQ/dr [N*m/m]", "aero"),
        ("cumulative_thrust_mil_vs_gmt", "cumulative_thrust_fraction", "Cumulative thrust fraction [-]", "aero"),
        ("cumulative_torque_mil_vs_gmt", "cumulative_torque_fraction", "Cumulative torque fraction [-]", "aero"),
    ]
    for stem, field, ylabel, source in plot_specs:
        fig, ax = plt.subplots(figsize=(7.0, 4.4), constrained_layout=True)
        for case, color in ((bundle.mil, "#1f77b4"), (bundle.gmt, "#d62728")):
            x = [station.mu for station in case.stations]
            if source == "stations":
                y = [float(getattr(station, field)) for station in case.stations]
            else:
                y = [float(row[field]) for row in case.aero_rows]
            ax.plot(x, y, marker="o", linewidth=1.8, markersize=3.5, label=case.label, color=color)
        ax.set_xlabel("r/R [-]")
        ax.set_ylabel(ylabel)
        ax.set_title("22 in, 4000 rpm, 35 N, static hover")
        ax.grid(True, color="#d8d8d8", linewidth=0.8)
        ax.legend(frameon=False)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        fig.savefig(GEOMETRY_DIR / f"{stem}.png", dpi=300)
        plt.close(fig)


def write_report(path: Path, bundle: DesignBundle, op: OperatingPoint) -> None:
    summary = bundle.summary
    lines = [
        "# Operating-Point Geometry Design Report",
        "",
        "## Operating Point",
        "",
        f"- Diameter: {DIAMETER_IN:.3f} in ({op.diameter_m:.6f} m)",
        f"- Radius: {op.radius_m:.6f} m",
        f"- Blade count: {op.blade_count}",
        f"- RPM: {op.rpm:.1f}",
        f"- Freestream: {op.v_inf_m_s:.3f} m/s",
        f"- Target thrust: {op.target_thrust_n:.3f} N",
        f"- Air density: {op.rho:.4f} kg/m^3",
        f"- Actuator-disk hover induced velocity: {op.target_induced_velocity_m_s:.6f} m/s",
        "",
        "## Fixed Variables",
        "",
        "- MIL and GMT use identical radial stations, chord distribution, diameter, blade count, RPM, freestream, density, target thrust, and airfoil schedule.",
        "- Twist beta(r) is the primary design variable.",
        "",
        "## Method",
        "",
        "MIL is generated from a station-wise local-efficiency target alpha found from the XFOIL polar tables. Static hover requires an induced velocity term; using phi = atan(V_inf/(omega r)) would make phi collapse to zero because V_inf = 0. The local hover inflow angle therefore uses Va = V_inf + Vi, with Vi from actuator-disk target thrust. A scalar pitch correction is solved with BEMT section loads to match 35 N.",
        "",
        "GMT is generated by solving a low-dimensional loading-shape problem before converting loading to twist. The scalar lambda_gmt modifies the MIL alpha schedule with a smooth inboard boost, smooth outer unloading, and stronger tip unloading. For each candidate lambda_gmt, the target alpha distribution is converted to beta(r) = alpha(r) + phi(r), pitch-corrected to 35 N, and evaluated with BEMT/XFOIL section forces.",
        "",
        "NASA/Robb scaled reference geometry is used for radial stations, fixed chord/airfoil schedule, and reference-shape diagnostics. It is not copied as the final twist distribution.",
        "",
        "Legacy final-wrapper twist repair is quarantined as passive validation for this workflow. The exported final beta values come from the inverse-design result, not from a post-optimization monotonic repair rule.",
        "",
        "## GMT Loading-Shape Search",
        "",
        f"- lambda_gmt search range: {GMT_LAMBDA_MIN:.3f} to {GMT_LAMBDA_MAX:.3f}",
        f"- selected lambda_gmt: {bundle.gmt.summary.get('lambda_gmt', '')}",
        f"- lambda = 1 nominal inboard boost: +{GMT_INBOARD_BOOST_AMPLITUDE_DEG:.3f} deg centered at r/R = {GMT_INBOARD_CENTER_MU:.3f}",
        f"- lambda = 1 nominal outer unload: -{GMT_OUTER_UNLOAD_AMPLITUDE_DEG:.3f} deg beginning near r/R = {GMT_OUTER_UNLOAD_START_MU:.3f}",
        f"- lambda = 1 nominal tip unload: -{GMT_TIP_UNLOAD_AMPLITUDE_DEG:.3f} deg beginning near r/R = {GMT_TIP_UNLOAD_START_MU:.3f}",
        f"- selection reason: {bundle.gmt.summary.get('lambda_selection_reason', '')}",
        "",
        "## Final Results",
        "",
        "| Case | T [N] | Q [N*m] | P [W] | CT | CQ | CP | thrust error [%] | tip load fraction | center of thrust r/R |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        result_row(bundle.mil),
        result_row(bundle.gmt),
        "",
        f"GMT torque reduction relative to MIL: {summary['gmt_torque_reduction_pct']:.3f}%.",
        f"GMT tip loading reduction relative to MIL: {bundle.gmt.summary.get('tip_loading_reduction_pct_vs_mil', 0.0):.3f}%.",
        f"GMT center of thrust shift inboard relative to MIL: {bundle.gmt.summary.get('center_of_thrust_shift_inboard_delta_mu', 0.0):.6f} r/R.",
        f"Maximum absolute GMT-MIL twist difference: {bundle.gmt.summary.get('max_abs_twist_delta_vs_mil_deg', 0.0):.3f} deg.",
        f"GMT tip twist difference relative to MIL: {bundle.gmt.summary.get('tip_twist_delta_vs_mil_deg', 0.0):.3f} deg.",
        "",
        "The torque benefit is considered strong only if the modeled GMT torque reduction exceeds 1%. Values below that threshold are reported as weak modeled torque benefit even when the geometry is physically valid.",
        "",
        "## Validation",
        "",
        "| Check | Status |",
        "|---|---|",
    ]
    for key, value in bundle.validation["checks"].items():
        lines.append(f"| {key} | {'pass' if value else 'fail'} |")
    lines.extend(
        [
            "",
            "## Warnings And Limitations",
            "",
        ]
    )
    if bundle.summary["warnings"]:
        lines.extend(f"- {warning}" for warning in bundle.summary["warnings"])
    else:
        lines.append("- No validation warnings were triggered.")
    lines.append(
        "- The GMT torque reduction is small because chord and airfoil schedule are fixed; forcing a stronger bell-shaped unload increased profile drag in the BEMT/XFOIL calculation."
    )
    lines.extend(
        [
            "",
            "## Output Files",
            "",
            f"- `{relative(GEOMETRY_DIR / 'final_mil_geometry.csv')}`",
            f"- `{relative(GEOMETRY_DIR / 'final_gmt_geometry.csv')}`",
            f"- `{relative(GEOMETRY_DIR / 'final_geometry_comparison.csv')}`",
            f"- `{relative(GEOMETRY_DIR / 'gmt_lambda_search_summary.csv')}`",
            f"- `{relative(GEOMETRY_DIR / 'final_aero_station_data_mil.csv')}`",
            f"- `{relative(GEOMETRY_DIR / 'final_aero_station_data_gmt.csv')}`",
            f"- `{relative(GEOMETRY_DIR / 'summary_table.csv')}`",
            f"- `{relative(GEOMETRY_DIR / 'design_summary.json')}`",
            f"- `{relative(AERO_DIR / 'mil_bemt_validation.csv')}`",
            f"- `{relative(AERO_DIR / 'gmt_bemt_validation.csv')}`",
            f"- `{relative(DIAGNOSTIC_DIR)}`",
            "",
            "## Thesis Wording Supported",
            "",
            "MIL and GMT geometries were generated for the 22-inch multirotor operating point using a station-based inverse design workflow. The MIL case used a local-efficiency target, while the GMT case used a low-dimensional loading-shape optimization to minimize total torque at fixed thrust. Both cases were evaluated at matched diameter, blade count, RPM, airfoil schedule, chord distribution, and target thrust. The GMT geometry was constrained to shift loading inboard and unload the tip, consistent with the global minimum torque design philosophy.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def result_row(case: CaseDesign) -> str:
    s = case.summary
    return (
        f"| {case.label} | {s['total_thrust_N']:.6f} | {s['total_torque_Nm']:.6f} | {s['power_W']:.3f} | "
        f"{s['CT']:.8f} | {s['CQ']:.8f} | {s['CP']:.8f} | {s['thrust_error_pct']:.4f} | "
        f"{s['tip_loading_fraction']:.6f} | {s['center_of_thrust_mu']:.6f} |"
    )


SUMMARY_TABLE_FIELDNAMES = [
    "case_label",
    "thrust_N",
    "thrust_error_pct",
    "torque_Nm",
    "power_W",
    "CT",
    "CQ",
    "CP",
    "min_alpha_deg",
    "max_alpha_deg",
    "min_Re",
    "max_Re",
    "tip_loading_fraction",
    "center_of_thrust_r_R",
    "lambda_gmt",
    "pitch_offset_deg",
    "max_smoothing_delta_deg",
    "validation_status",
]


def summary_table_row(bundle: DesignBundle, case: CaseDesign) -> dict[str, Any]:
    summary = case.summary
    validation_status = "pass"
    if case.label == "GMT" and bundle.validation["warnings"]:
        validation_status = "warn"
    if case.warnings:
        validation_status = "warn"
    return {
        "case_label": case.label,
        "thrust_N": summary["total_thrust_N"],
        "thrust_error_pct": summary["thrust_error_pct"],
        "torque_Nm": summary["total_torque_Nm"],
        "power_W": summary["power_W"],
        "CT": summary["CT"],
        "CQ": summary["CQ"],
        "CP": summary["CP"],
        "min_alpha_deg": summary["min_alpha_deg"],
        "max_alpha_deg": summary["max_alpha_deg"],
        "min_Re": summary["min_Re"],
        "max_Re": summary["max_Re"],
        "tip_loading_fraction": summary["tip_loading_fraction"],
        "center_of_thrust_r_R": summary["center_of_thrust_mu"],
        "lambda_gmt": summary.get("lambda_gmt", ""),
        "pitch_offset_deg": summary.get("pitch_offset_deg", summary.get("post_smoothing_pitch_offset_deg", "")),
        "max_smoothing_delta_deg": summary.get("max_smoothing_delta_deg", ""),
        "validation_status": validation_status,
    }


def gmt_target_weights(reference: list[ReferenceStation], dr_values: list[float]) -> list[float]:
    raw: list[float] = []
    hub = reference[0].mu
    for station, dr_m in zip(reference, dr_values):
        x = clamp((station.mu - hub) / max(1.0 - hub, 1.0e-9), 0.0, 1.0)
        root = max(x, 1.0e-9) ** 0.75
        mid = 1.0 + 0.55 * math.exp(-((x - 0.50) / 0.24) ** 2)
        tip_unload = max(1.0 - x**2.5, 0.0) ** 2.2
        raw.append(root * mid * tip_unload * dr_m)
    total = sum(raw)
    return [value / total for value in raw]


def normalized_loading_weights(rows: list[dict[str, Any]]) -> list[float]:
    values = [float(row["dT_N"]) for row in rows]
    total = sum(values)
    return [value / max(total, 1.0e-12) for value in values]


def normalized_loading_density(rows: list[dict[str, Any]]) -> list[float]:
    total = sum(float(row["dT_N"]) for row in rows)
    return [float(row["dT_N"]) / max(float(row["dr_m"]) * total, 1.0e-12) for row in rows]


def loading_fraction(rows: list[dict[str, Any]], field: str, *, lower_mu: float) -> float:
    total = sum(float(row[field]) for row in rows)
    if abs(total) <= 1.0e-12:
        return 0.0
    return sum(float(row[field]) for row in rows if float(row["r_R"]) >= lower_mu) / total


def center_of_loading_mu(rows: list[dict[str, Any]], field: str) -> float:
    total = sum(float(row[field]) for row in rows)
    if abs(total) <= 1.0e-12:
        return float("nan")
    return sum(float(row["r_R"]) * float(row[field]) for row in rows) / total


def twist_curvature_penalty(values: list[float]) -> float:
    if len(values) < 3:
        return 0.0
    return sum((values[idx + 1] - 2.0 * values[idx] + values[idx - 1]) ** 2 for idx in range(1, len(values) - 1)) / (
        len(values) - 2
    )


def no_artificial_plateau(values: list[float], *, threshold_deg: float = 0.01) -> bool:
    repeated = 0
    for left, right in zip(values, values[1:]):
        if abs(right - left) < threshold_deg:
            repeated += 1
            if repeated >= 3:
                return False
        else:
            repeated = 0
    return True


def is_monotonic_decreasing(values: list[float], *, tolerance: float = 1.0e-8) -> bool:
    return all(right <= left + tolerance for left, right in zip(values, values[1:]))


def read_geometry_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"no rows in {path}")
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(row.get(key, "")) for key in fieldnames})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=json_default) + "\n", encoding="utf-8")


def csv_value(value: Any) -> Any:
    if isinstance(value, float):
        return f"{value:.12g}"
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def rms(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(value * value for value in values) / len(values))


def smoothstep(value: float) -> float:
    x = clamp(value, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def require_monotonic_mu(values: list[float], label: str) -> None:
    for left, right in zip(values, values[1:]):
        if right <= left:
            raise ValueError(f"{label} must be strictly increasing in r/R")


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
