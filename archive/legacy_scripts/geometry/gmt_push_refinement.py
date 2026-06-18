from __future__ import annotations

import csv
import json
import math
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


GEOMETRY_CODE_DIR = Path(__file__).resolve().parent
if str(GEOMETRY_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(GEOMETRY_CODE_DIR))

import operating_point_inverse_design as inv


ROOT = Path(__file__).resolve().parents[2]
GEOMETRY_DIR = ROOT / "results" / "FINAL_THESIS_USE_THESE" / "01_geometry"
LOCKED_MIL_GEOMETRY = GEOMETRY_DIR / "final_mil_geometry.csv"
LOCKED_GMT_GEOMETRY = GEOMETRY_DIR / "final_gmt_geometry.csv"
LOCKED_SUMMARY = GEOMETRY_DIR / "design_summary.json"
CANDIDATE_DIR = GEOMETRY_DIR / "gmt_push_candidates"
SELECTED_DIR = GEOMETRY_DIR / "aggressive_selected"

TORQUE_REDUCTION_MARGIN_PCT = 0.25
MIN_TARGET_TORQUE_REDUCTION_PCT = 1.75
MIN_TIP_TWIST_DELTA_DEG = -2.5
STRICT_CROSSING_MIN_MU = 0.75
STRICT_CROSSING_MAX_MU = 0.88
MAX_ADJACENT_TWIST_DROP_DEG = 2.0
MIN_OUTBOARD_DT_REDUCTION_FRACTION = 0.60
MAX_CANDIDATE_SECONDS = 60.0 * 60.0


@dataclass(frozen=True)
class ShapeParameters:
    shape_id: str
    inboard_boost_amplitude_deg: float
    outer_unload_amplitude_deg: float
    tip_unload_amplitude_deg: float
    outer_unload_start_mu: float
    tip_unload_start_mu: float
    inboard_center_mu: float
    inboard_width_mu: float


@dataclass(frozen=True)
class CandidateResult:
    candidate_id: str
    lambda_gmt: float
    shape: ShapeParameters
    beta_initial: list[float]
    beta_optimized: list[float]
    beta_smoothed: list[float]
    beta_final: list[float]
    pitch_offset_deg: float
    smoothing_delta_deg: float
    aero_rows: list[dict[str, Any]]
    summary: dict[str, Any]
    checks: dict[str, bool]
    failed_constraints: list[str]
    objective_score: float
    files: dict[str, str]


class BaselineCase:
    def __init__(self, summary: dict[str, Any], aero_rows: list[dict[str, Any]]) -> None:
        self.summary = summary
        self.aero_rows = aero_rows


def main() -> int:
    start_time = time.monotonic()
    op = inv.OperatingPoint()
    polar_database = inv.bemt.load_polar_database(inv.AIRFOIL_DIR)
    if not polar_database:
        raise RuntimeError(f"No airfoil polar CSV files found under {inv.AIRFOIL_DIR}")

    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    baseline_summary = read_json(LOCKED_SUMMARY)
    reference = inv.load_reference_pair(op)
    dr_values = inv.station_widths_m(reference, op)
    locked_mil_beta = read_beta_from_geometry(LOCKED_MIL_GEOMETRY)
    locked_gmt_beta = read_beta_from_geometry(LOCKED_GMT_GEOMETRY)
    validate_locked_geometry(reference, locked_mil_beta, LOCKED_MIL_GEOMETRY)
    validate_locked_geometry(reference, locked_gmt_beta, LOCKED_GMT_GEOMETRY)

    mil_rows, mil_summary = inv.evaluate_design_case("MIL", reference, dr_values, locked_mil_beta, op, polar_database)
    locked_gmt_rows, locked_gmt_summary = inv.evaluate_design_case(
        "GMT",
        reference,
        dr_values,
        locked_gmt_beta,
        op,
        polar_database,
    )
    locked_mil = BaselineCase(mil_summary, mil_rows)
    inv.apply_gmt_difference_metrics(
        locked_gmt_summary,
        locked_gmt_rows,
        locked_mil,
        {"max_abs_delta_deg": float(baseline_summary["gmt"].get("max_smoothing_delta_deg", 0.0))},
    )

    locked_torque_reduction = float(baseline_summary["gmt_torque_reduction_pct"])
    locked_center_shift = float(baseline_summary["gmt"]["center_of_thrust_shift_inboard_delta_mu"])
    required_torque_reduction = max(
        locked_torque_reduction + TORQUE_REDUCTION_MARGIN_PCT,
        MIN_TARGET_TORQUE_REDUCTION_PCT,
    )

    candidates: list[CandidateResult] = []
    for lambda_gmt in lambda_values():
        for shape in shape_parameter_grid():
            if time.monotonic() - start_time > MAX_CANDIDATE_SECONDS:
                break
            candidate = evaluate_candidate(
                lambda_gmt,
                shape,
                reference,
                dr_values,
                op,
                polar_database,
                locked_mil,
                locked_torque_reduction,
                locked_center_shift,
                required_torque_reduction,
            )
            write_candidate_outputs(candidate, reference, locked_mil)
            candidates.append(candidate)
        else:
            continue
        break

    selected = choose_selected_candidate(candidates)
    write_search_summary(candidates)
    write_search_report(candidates, selected, locked_torque_reduction, required_torque_reduction)
    if selected is not None:
        SELECTED_DIR.mkdir(parents=True, exist_ok=True)
        copy_selected_candidate(selected)

    print("GMT push refinement complete")
    print(f"  candidates tested: {len(candidates)}")
    if selected is None:
        print("  No stronger physically valid GMT candidate was found. Keep the locked baseline geometry.")
    else:
        print(f"  selected candidate: {selected.candidate_id}")
        print(f"  torque reduction: {selected.summary['torque_reduction_pct_vs_mil']:.3f}%")
        print(f"  thrust: {selected.summary['total_thrust_N']:.6f} N")
        print(f"  output folder: {SELECTED_DIR}")
    print(f"  candidate folder: {CANDIDATE_DIR}")
    return 0


def lambda_values() -> list[float]:
    return [0.425, 0.45, 0.475, 0.50, 0.55, 0.60, 0.70, 0.80, 0.90, 1.00, 1.10, 1.20]


def shape_parameter_grid() -> list[ShapeParameters]:
    return [
        ShapeParameters("s00", 4.0, 3.0, 5.0, 0.74, 0.88, 0.50, 0.26),
        ShapeParameters("s01", 4.0, 3.5, 6.0, 0.72, 0.86, 0.50, 0.26),
        ShapeParameters("s02", 4.5, 3.5, 6.5, 0.72, 0.86, 0.52, 0.28),
        ShapeParameters("s03", 3.5, 4.0, 7.0, 0.70, 0.84, 0.50, 0.24),
        ShapeParameters("s04", 4.0, 4.5, 8.0, 0.70, 0.84, 0.52, 0.26),
        ShapeParameters("s05", 5.0, 4.0, 7.0, 0.72, 0.86, 0.55, 0.28),
        ShapeParameters("s06", 4.5, 5.0, 8.5, 0.68, 0.84, 0.55, 0.26),
        ShapeParameters("s07", 3.5, 5.0, 9.0, 0.70, 0.82, 0.50, 0.24),
        ShapeParameters("s08", 5.0, 4.5, 8.0, 0.72, 0.86, 0.58, 0.30),
        ShapeParameters("s09", 4.0, 4.0, 10.0, 0.72, 0.82, 0.52, 0.26),
        ShapeParameters("s10", 5.5, 4.0, 7.5, 0.74, 0.86, 0.58, 0.30),
        ShapeParameters("s11", 4.5, 5.5, 9.0, 0.68, 0.82, 0.52, 0.28),
    ]


def evaluate_candidate(
    lambda_gmt: float,
    shape: ShapeParameters,
    reference: list[inv.ReferenceStation],
    dr_values: list[float],
    op: inv.OperatingPoint,
    polar_database: dict[str, dict[float, dict[str, Any]]],
    locked_mil: BaselineCase,
    locked_torque_reduction_pct: float,
    locked_center_shift_mu: float,
    required_torque_reduction_pct: float,
) -> CandidateResult:
    mil_phi = [float(row["phi_deg"]) for row in locked_mil.aero_rows]
    mil_alpha = [float(row["alpha_deg"]) for row in locked_mil.aero_rows]
    beta_initial = parameterized_gmt_beta(
        0.0,
        shape,
        mil_phi,
        mil_alpha,
        reference,
        polar_database,
        clamp_alpha=False,
    )
    beta_raw = parameterized_gmt_beta(
        lambda_gmt,
        shape,
        mil_phi,
        mil_alpha,
        reference,
        polar_database,
        clamp_alpha=True,
    )
    beta_optimized = inv.enforce_gmt_tip_unload_continuity(beta_raw, reference)
    beta_smoothed_raw, _ = inv.smooth_with_limited_delta(
        beta_optimized,
        max_delta_deg=inv.MAX_SMOOTHING_DELTA_DEG,
    )
    beta_smoothed = inv.enforce_gmt_tip_unload_continuity(beta_smoothed_raw, reference)
    smoothing_report = inv.smoothing_delta_report(
        beta_optimized,
        beta_smoothed,
        method="gmt_push_moving_average_plus_tip_unload_continuity_guard",
    )
    beta_final, pitch_offset, _ = inv.pitch_correct_beta(
        "GMT",
        reference,
        dr_values,
        beta_smoothed,
        op,
        polar_database,
        reason="GMT push candidate pitch correction",
    )
    aero_rows, summary = inv.evaluate_design_case("GMT", reference, dr_values, beta_final, op, polar_database)
    inv.apply_gmt_difference_metrics(summary, aero_rows, locked_mil, smoothing_report)
    add_push_metrics(summary, aero_rows, locked_mil.aero_rows, beta_final, reference)
    summary.update(
        {
            "candidate_id": candidate_id(lambda_gmt, shape),
            "lambda_gmt": lambda_gmt,
            "shape_parameters": asdict(shape),
            "pitch_offset_deg": pitch_offset,
            "max_smoothing_delta_deg": smoothing_report["max_abs_delta_deg"],
            "locked_baseline_torque_reduction_pct": locked_torque_reduction_pct,
            "locked_center_shift_inboard_delta_mu": locked_center_shift_mu,
            "required_torque_reduction_pct": required_torque_reduction_pct,
        }
    )
    checks = candidate_checks(summary, aero_rows, locked_torque_reduction_pct, required_torque_reduction_pct)
    failed_constraints = [name for name, passed in checks.items() if not passed]
    score = candidate_objective(summary, checks)
    return CandidateResult(
        candidate_id=candidate_id(lambda_gmt, shape),
        lambda_gmt=lambda_gmt,
        shape=shape,
        beta_initial=beta_initial,
        beta_optimized=beta_optimized,
        beta_smoothed=beta_smoothed,
        beta_final=beta_final,
        pitch_offset_deg=pitch_offset,
        smoothing_delta_deg=float(smoothing_report["max_abs_delta_deg"]),
        aero_rows=aero_rows,
        summary=summary,
        checks=checks,
        failed_constraints=failed_constraints,
        objective_score=score,
        files={},
    )


def parameterized_gmt_beta(
    lambda_gmt: float,
    shape: ShapeParameters,
    phi_values: list[float],
    mil_alpha: list[float],
    reference: list[inv.ReferenceStation],
    polar_database: dict[str, dict[float, dict[str, Any]]],
    *,
    clamp_alpha: bool,
) -> list[float]:
    beta: list[float] = []
    for phi, alpha_mil, station in zip(phi_values, mil_alpha, reference):
        inboard_shape = math.exp(-((station.mu - shape.inboard_center_mu) / shape.inboard_width_mu) ** 2)
        outer_shape = inv.smoothstep(
            (station.mu - shape.outer_unload_start_mu) / max(1.0 - shape.outer_unload_start_mu, 1.0e-9)
        )
        tip_shape = inv.smoothstep(
            (station.mu - shape.tip_unload_start_mu) / max(1.0 - shape.tip_unload_start_mu, 1.0e-9)
        )
        alpha = (
            alpha_mil
            + lambda_gmt * shape.inboard_boost_amplitude_deg * inboard_shape
            - lambda_gmt * shape.outer_unload_amplitude_deg * outer_shape
            - lambda_gmt * shape.tip_unload_amplitude_deg * tip_shape
        )
        if clamp_alpha:
            lower, upper = inv.bemt.polar_alpha_bounds(polar_database, station.airfoil)
            alpha = inv.clamp(
                alpha,
                max(inv.MIN_ALPHA_DESIGN_DEG + 0.1, lower + 0.1),
                min(inv.MAX_ALPHA_DESIGN_DEG, upper - 0.1),
            )
        beta.append(phi + alpha)
    return beta


def add_push_metrics(
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    mil_rows: list[dict[str, Any]],
    beta_final: list[float],
    reference: list[inv.ReferenceStation],
) -> None:
    outboard_rows = [(row, mil_row) for row, mil_row in zip(rows, mil_rows) if float(row["r_R"]) > 0.80]
    lower_dt_count = sum(float(row["qz_N_per_m"]) < float(mil_row["qz_N_per_m"]) for row, mil_row in outboard_rows)
    summary["outboard_dTdr_lower_than_mil_fraction"] = lower_dt_count / max(len(outboard_rows), 1)
    midspan_diffs = [
        float(row["cumulative_thrust_fraction"]) - float(mil_row["cumulative_thrust_fraction"])
        for row, mil_row in zip(rows, mil_rows)
        if float(row["r_R"]) <= 0.65
    ]
    summary["max_midspan_cumulative_thrust_lead_vs_mil"] = max(midspan_diffs, default=0.0)
    adjacent_drops = [beta_final[index] - beta_final[index + 1] for index in range(len(beta_final) - 1)]
    summary["max_adjacent_twist_drop_deg"] = max(adjacent_drops, default=0.0)
    summary["min_adjacent_twist_drop_deg"] = min(adjacent_drops, default=0.0)
    summary["strict_crossing_passed"] = (
        isinstance(summary.get("gmt_twist_crossing_mu"), float)
        and STRICT_CROSSING_MIN_MU <= float(summary["gmt_twist_crossing_mu"]) <= STRICT_CROSSING_MAX_MU
    )
    inboard_deltas = [
        float(row["twist_deg"]) - float(mil_row["twist_deg"])
        for row, mil_row in zip(rows, mil_rows)
        if 0.20 <= float(row["r_R"]) <= 0.65
    ]
    summary["gmt_above_mil_root_to_midspan_fraction"] = (
        sum(delta > 0.0 for delta in inboard_deltas) / max(len(inboard_deltas), 1)
    )


def candidate_checks(
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    locked_torque_reduction_pct: float,
    required_torque_reduction_pct: float,
) -> dict[str, bool]:
    return {
        "thrust_within_1pct": abs(float(summary["thrust_error_pct"])) <= 1.0,
        "thrust_within_0p1pct": abs(float(summary["thrust_error_pct"])) <= 0.1,
        "torque_lower_than_locked_mil": float(summary["torque_reduction_pct_vs_mil"]) > 0.0,
        "beats_locked_baseline_by_margin": float(summary["torque_reduction_pct_vs_mil"])
        >= locked_torque_reduction_pct + TORQUE_REDUCTION_MARGIN_PCT,
        "meets_min_target_torque_reduction": float(summary["torque_reduction_pct_vs_mil"])
        >= required_torque_reduction_pct,
        "tip_loading_reduction_at_least_10pct": float(summary["tip_loading_reduction_pct_vs_mil"]) >= 10.0,
        "center_shift_at_least_locked": float(summary["center_of_thrust_shift_inboard_delta_mu"])
        >= float(summary["locked_center_shift_inboard_delta_mu"]),
        "gmt_above_mil_root_to_midspan": float(summary["gmt_above_mil_root_to_midspan_fraction"]) >= 0.95,
        "gmt_crosses_mil_strict_outer_span": bool(summary["strict_crossing_passed"]),
        "gmt_below_mil_near_tip": bool(summary["gmt_twist_below_mil_near_tip"]),
        "tip_twist_at_least_2p5deg_below_mil": float(summary["tip_twist_delta_vs_mil_deg"]) <= MIN_TIP_TWIST_DELTA_DEG,
        "outboard_dTdr_lower_than_mil": float(summary["outboard_dTdr_lower_than_mil_fraction"])
        >= MIN_OUTBOARD_DT_REDUCTION_FRACTION,
        "midspan_cumulative_thrust_leads_mil": float(summary["max_midspan_cumulative_thrust_lead_vs_mil"]) > 0.0,
        "no_negative_thrust_stations": int(summary["negative_thrust_station_count"]) == 0,
        "no_polar_alpha_violations": int(summary["alpha_outside_polar_range_count"]) == 0,
        "smoothing_delta_below_1deg": float(summary["max_smoothing_delta_deg"]) <= 1.0,
        "no_tip_twist_cliff": bool(summary["gmt_tip_continuity_guard_passed"])
        and float(summary["max_adjacent_twist_drop_deg"]) <= MAX_ADJACENT_TWIST_DROP_DEG,
        "no_alpha_spikes": max(abs(float(row["alpha_deg"]) - float(prev["alpha_deg"])) for prev, row in zip(rows, rows[1:]))
        <= 4.0,
    }


def candidate_objective(summary: dict[str, Any], checks: dict[str, bool]) -> float:
    failures = sum(not value for value in checks.values())
    return (
        1000.0 * failures
        - 10.0 * float(summary["torque_reduction_pct_vs_mil"])
        - float(summary["tip_loading_reduction_pct_vs_mil"])
        - 10.0 * float(summary["center_of_thrust_shift_inboard_delta_mu"])
        + float(summary["max_smoothing_delta_deg"])
    )


def choose_selected_candidate(candidates: list[CandidateResult]) -> CandidateResult | None:
    passing = [candidate for candidate in candidates if all(candidate.checks.values())]
    if not passing:
        return None
    return max(
        passing,
        key=lambda candidate: (
            float(candidate.summary["torque_reduction_pct_vs_mil"]),
            float(candidate.summary["tip_loading_reduction_pct_vs_mil"]),
            float(candidate.summary["center_of_thrust_shift_inboard_delta_mu"]),
            -float(candidate.summary["max_smoothing_delta_deg"]),
        ),
    )


def write_candidate_outputs(
    candidate: CandidateResult,
    reference: list[inv.ReferenceStation],
    locked_mil: BaselineCase,
) -> None:
    geometry_path = CANDIDATE_DIR / f"candidate_gmt_geometry_{candidate.candidate_id}.csv"
    aero_path = CANDIDATE_DIR / f"candidate_aero_station_data_{candidate.candidate_id}.csv"
    summary_path = CANDIDATE_DIR / f"candidate_summary_{candidate.candidate_id}.json"
    write_geometry_csv(geometry_path, candidate, reference)
    write_aero_csv(aero_path, candidate.aero_rows)
    write_json(
        summary_path,
        {
            "candidate_id": candidate.candidate_id,
            "summary": candidate.summary,
            "checks": candidate.checks,
            "failed_constraints": candidate.failed_constraints,
            "shape_parameters": asdict(candidate.shape),
        },
    )
    plot_files = write_candidate_plots(candidate, reference, locked_mil)
    candidate.files.update(
        {
            "geometry": str(geometry_path),
            "aero": str(aero_path),
            "summary": str(summary_path),
            **plot_files,
        }
    )


def write_geometry_csv(path: Path, candidate: CandidateResult, reference: list[inv.ReferenceStation]) -> None:
    rows: list[dict[str, Any]] = []
    for index, (station, initial, optimized, smoothed, final) in enumerate(
        zip(reference, candidate.beta_initial, candidate.beta_optimized, candidate.beta_smoothed, candidate.beta_final)
    ):
        rows.append(
            {
                "case": "GMT_push_candidate",
                "candidate_id": candidate.candidate_id,
                "lambda_gmt": candidate.lambda_gmt,
                "mu": station.mu,
                "radius_m": station.radius_m,
                "radius_in": station.radius_m / inv.INCH_TO_METER,
                "dr_m": inv.station_widths_m(reference, inv.OperatingPoint())[index],
                "chord_m": station.chord_m,
                "chord_in": station.chord_m / inv.INCH_TO_METER,
                "chord_over_R": station.chord_m / inv.RADIUS_M,
                "airfoil": station.airfoil,
                "beta_initial_deg": initial,
                "beta_optimized_deg": optimized,
                "beta_smoothed_deg": smoothed,
                "beta_final_deg": final,
                "twist_deg": final,
                "pitch_offset_deg": candidate.pitch_offset_deg,
                "max_smoothing_delta_deg": candidate.smoothing_delta_deg,
                **asdict(candidate.shape),
            }
        )
    write_csv(path, rows, list(rows[0].keys()))


def write_aero_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    write_csv(path, rows, fieldnames)


def write_candidate_plots(
    candidate: CandidateResult,
    reference: list[inv.ReferenceStation],
    locked_mil: BaselineCase,
) -> dict[str, str]:
    specs = [
        ("twist", "twist_deg", "Twist beta [deg]", "geometry"),
        ("alpha", "alpha_deg", "Angle of attack [deg]", "aero"),
        ("Cl", "Cl", "Cl [-]", "aero"),
        ("dTdr", "qz_N_per_m", "dT/dr [N/m]", "aero"),
        ("dQdr", "torque_loading_Nm_per_m", "dQ/dr [N*m/m]", "aero"),
        ("cumulative_thrust", "cumulative_thrust_fraction", "Cumulative thrust fraction [-]", "aero"),
        ("cumulative_torque", "cumulative_torque_fraction", "Cumulative torque fraction [-]", "aero"),
    ]
    files: dict[str, str] = {}
    mu_values = [station.mu for station in reference]
    for stem, field, ylabel, source in specs:
        fig, ax = plt.subplots(figsize=(7.0, 4.4), constrained_layout=True)
        if source == "geometry":
            mil_y = [float(row["twist_deg"]) for row in locked_mil.aero_rows]
            gmt_y = candidate.beta_final
        else:
            mil_y = [float(row[field]) for row in locked_mil.aero_rows]
            gmt_y = [float(row[field]) for row in candidate.aero_rows]
        ax.plot(mu_values, mil_y, color="#1f77b4", marker="o", markersize=3.0, linewidth=1.8, label="Locked MIL")
        ax.plot(mu_values, gmt_y, color="#d62728", marker="o", markersize=3.0, linewidth=1.8, label="Candidate GMT")
        ax.set_title(f"{candidate.candidate_id}: 22 in, 4000 rpm, 35 N, static hover")
        ax.set_xlabel("r/R [-]")
        ax.set_ylabel(ylabel)
        ax.grid(True, color="#d8d8d8", linewidth=0.8)
        ax.legend(frameon=False)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        path = CANDIDATE_DIR / f"candidate_{stem}_{candidate.candidate_id}.png"
        fig.savefig(path, dpi=220)
        plt.close(fig)
        files[stem] = str(path)
    return files


def write_search_summary(candidates: list[CandidateResult]) -> None:
    rows = []
    for candidate in candidates:
        rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "lambda_gmt": candidate.lambda_gmt,
                **asdict(candidate.shape),
                "thrust_N": candidate.summary["total_thrust_N"],
                "thrust_error_pct": candidate.summary["thrust_error_pct"],
                "torque_Nm": candidate.summary["total_torque_Nm"],
                "torque_reduction_pct_vs_mil": candidate.summary["torque_reduction_pct_vs_mil"],
                "tip_loading_reduction_pct_vs_mil": candidate.summary["tip_loading_reduction_pct_vs_mil"],
                "center_shift_inboard_delta_mu": candidate.summary["center_of_thrust_shift_inboard_delta_mu"],
                "tip_twist_delta_vs_mil_deg": candidate.summary["tip_twist_delta_vs_mil_deg"],
                "crossing_mu": candidate.summary["gmt_twist_crossing_mu"],
                "outboard_dTdr_lower_than_mil_fraction": candidate.summary["outboard_dTdr_lower_than_mil_fraction"],
                "midspan_cumulative_thrust_lead": candidate.summary["max_midspan_cumulative_thrust_lead_vs_mil"],
                "max_smoothing_delta_deg": candidate.summary["max_smoothing_delta_deg"],
                "max_adjacent_twist_drop_deg": candidate.summary["max_adjacent_twist_drop_deg"],
                "negative_thrust_station_count": candidate.summary["negative_thrust_station_count"],
                "alpha_outside_polar_range_count": candidate.summary["alpha_outside_polar_range_count"],
                "all_constraints_pass": all(candidate.checks.values()),
                "failed_constraints": "; ".join(candidate.failed_constraints),
            }
        )
    fieldnames = list(rows[0].keys()) if rows else []
    write_csv(CANDIDATE_DIR / "gmt_push_search_summary.csv", rows, fieldnames)


def write_search_report(
    candidates: list[CandidateResult],
    selected: CandidateResult | None,
    locked_torque_reduction_pct: float,
    required_torque_reduction_pct: float,
) -> None:
    valid_shape = [candidate for candidate in candidates if not candidate.failed_constraints]
    best_by_torque = max(candidates, key=lambda c: float(c.summary["torque_reduction_pct_vs_mil"]), default=None)
    best_clean = max(
        [candidate for candidate in candidates if candidate.checks["thrust_within_1pct"] and candidate.checks["no_negative_thrust_stations"] and candidate.checks["no_polar_alpha_violations"]],
        key=lambda c: float(c.summary["torque_reduction_pct_vs_mil"]),
        default=None,
    )
    lines = [
        "# GMT Push Search Report",
        "",
        "The locked MIL geometry was held fixed. The locked final geometry files were not overwritten.",
        "",
        f"- Candidates tested: {len(candidates)}",
        f"- Locked baseline torque reduction: {locked_torque_reduction_pct:.6f}%",
        f"- Required replacement torque reduction: {required_torque_reduction_pct:.6f}%",
        f"- Fully passing candidates: {len(valid_shape)}",
        "",
    ]
    if best_by_torque:
        lines.extend(candidate_report_block("Best torque candidate", best_by_torque))
    if best_clean and best_clean is not best_by_torque:
        lines.extend(candidate_report_block("Best clean aerodynamic candidate", best_clean))
    if selected is None:
        lines.extend(
            [
                "## Selection",
                "",
                "No stronger physically valid GMT candidate was found. Keep the locked baseline geometry.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Selection",
                "",
                f"Selected candidate: `{selected.candidate_id}`",
                "",
                f"- Torque reduction: {selected.summary['torque_reduction_pct_vs_mil']:.6f}%",
                f"- Thrust: {selected.summary['total_thrust_N']:.6f} N",
                f"- Tip loading reduction: {selected.summary['tip_loading_reduction_pct_vs_mil']:.6f}%",
                f"- Center-of-thrust shift: {selected.summary['center_of_thrust_shift_inboard_delta_mu']:.6f} r/R",
                f"- lambda_gmt: {selected.lambda_gmt:.6f}",
                f"- Shape parameters: `{json.dumps(asdict(selected.shape), sort_keys=True)}`",
                "",
                "This candidate should replace the locked baseline only after manual review of the candidate folder.",
                "",
            ]
        )
    lines.extend(
        [
            "## Output Files",
            "",
            f"- Candidate folder: `{relative(CANDIDATE_DIR)}`",
            f"- Search summary: `{relative(CANDIDATE_DIR / 'gmt_push_search_summary.csv')}`",
            "",
        ]
    )
    (CANDIDATE_DIR / "gmt_push_search_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def candidate_report_block(title: str, candidate: CandidateResult) -> list[str]:
    return [
        f"## {title}",
        "",
        f"- Candidate: `{candidate.candidate_id}`",
        f"- Torque reduction: {candidate.summary['torque_reduction_pct_vs_mil']:.6f}%",
        f"- Thrust: {candidate.summary['total_thrust_N']:.6f} N",
        f"- Tip loading reduction: {candidate.summary['tip_loading_reduction_pct_vs_mil']:.6f}%",
        f"- Center shift: {candidate.summary['center_of_thrust_shift_inboard_delta_mu']:.6f} r/R",
        f"- Crossing r/R: {candidate.summary['gmt_twist_crossing_mu']}",
        f"- Tip twist delta: {candidate.summary['tip_twist_delta_vs_mil_deg']:.6f} deg",
        f"- Failed constraints: {', '.join(candidate.failed_constraints) if candidate.failed_constraints else 'none'}",
        "",
    ]


def copy_selected_candidate(selected: CandidateResult) -> None:
    for source in selected.files.values():
        source_path = Path(source)
        if source_path.exists():
            shutil.copy2(source_path, SELECTED_DIR / source_path.name)
    write_json(
        SELECTED_DIR / "selected_candidate_summary.json",
        {
            "candidate_id": selected.candidate_id,
            "summary": selected.summary,
            "checks": selected.checks,
            "shape_parameters": asdict(selected.shape),
            "files": selected.files,
        },
    )


def read_beta_from_geometry(path: Path) -> list[float]:
    rows = read_csv(path)
    lookup = column_lookup(rows[0].keys())
    beta_col = require_column(lookup, ("beta_final_deg", "twist_deg"))
    return [float(row[beta_col]) for row in rows]


def validate_locked_geometry(reference: list[inv.ReferenceStation], beta_values: list[float], path: Path) -> None:
    rows = read_csv(path)
    if len(rows) != len(reference) or len(beta_values) != len(reference):
        raise ValueError(f"Station count mismatch for {path}")
    lookup = column_lookup(rows[0].keys())
    mu_col = require_column(lookup, ("mu", "r_R"))
    chord_col = require_column(lookup, ("chord_m",))
    airfoil_col = require_column(lookup, ("airfoil",))
    for index, (row, station) in enumerate(zip(rows, reference), start=1):
        if abs(float(row[mu_col]) - station.mu) > 1.0e-10:
            raise ValueError(f"{path} mu mismatch at row {index}")
        if abs(float(row[chord_col]) - station.chord_m) > 1.0e-10:
            raise ValueError(f"{path} chord mismatch at row {index}")
        if inv.bemt.normalize_airfoil(row[airfoil_col]) != station.airfoil:
            raise ValueError(f"{path} airfoil mismatch at row {index}")


def candidate_id(lambda_gmt: float, shape: ShapeParameters) -> str:
    return f"lambda_{str(f'{lambda_gmt:.3f}').replace('.', 'p')}_{shape.shape_id}"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise ValueError(f"No rows in {path}")
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
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def csv_value(value: Any) -> Any:
    if isinstance(value, float):
        return f"{value:.12g}"
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def column_lookup(columns: Iterable[str]) -> dict[str, str]:
    return {column.strip().lower().replace(" ", "").replace("_", ""): column for column in columns}


def require_column(lookup: dict[str, str], candidates: Iterable[str]) -> str:
    for candidate in candidates:
        key = candidate.strip().lower().replace(" ", "").replace("_", "")
        if key in lookup:
            return lookup[key]
    raise ValueError(f"Missing required column. Expected one of: {', '.join(candidates)}")


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
