from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "Code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import aero_solver


FINAL_ROOT = ROOT / "results" / "FINAL_THESIS_USE_THESE"
GEOMETRY_DIR = FINAL_ROOT / "01_geometry"
OUTPUT_DIR = FINAL_ROOT / "02_aero_validation"

MIL_GEOMETRY = GEOMETRY_DIR / "final_mil_geometry.csv"
GMT_GEOMETRY = GEOMETRY_DIR / "final_gmt_geometry.csv"
LOCKED_DESIGN_SUMMARY = GEOMETRY_DIR / "design_summary_LOCKED.json"
DESIGN_SUMMARY = LOCKED_DESIGN_SUMMARY if LOCKED_DESIGN_SUMMARY.exists() else GEOMETRY_DIR / "design_summary.json"

MIL_STATION_OUT = OUTPUT_DIR / "mil_bemt_validation_LOCKED.csv"
GMT_STATION_OUT = OUTPUT_DIR / "gmt_bemt_validation_LOCKED.csv"
COMPARISON_OUT = OUTPUT_DIR / "locked_aero_comparison.csv"
SUMMARY_CSV_OUT = OUTPUT_DIR / "locked_aero_summary.csv"
SUMMARY_JSON_OUT = OUTPUT_DIR / "locked_aero_summary.json"
LOCKED_SUMMARY_COPY_OUT = OUTPUT_DIR / "design_summary_LOCKED.json"
COEFFICIENT_SUMMARY_CSV = OUTPUT_DIR / "ct_cq_cp_summary.csv"

EXPECTED_LOCKED = {
    "mil_thrust_N": 35.00000016969875,
    "gmt_thrust_N": 34.999994345585726,
    "mil_torque_Nm": 0.859265776418447,
    "gmt_torque_Nm": 0.8464030742167892,
    "gmt_torque_reduction_pct": 1.4969410576633924,
    "lambda_gmt": 0.425,
    "gmt_tip_loading_reduction_pct": 10.286824874431757,
    "gmt_center_shift_mu": 0.018959311812037583,
}

METRIC_TOL = {
    "thrust_N": 2.0e-5,
    "torque_Nm": 2.0e-6,
    "reduction_pct": 2.0e-3,
    "lambda": 1.0e-12,
    "fraction_pct": 2.0e-3,
    "center_mu": 2.0e-5,
}

LINE_PLOTS = [
    ("alpha_mil_vs_gmt", "alpha_deg", r"Angle of attack, $\alpha$ [deg]"),
    ("cl_mil_vs_gmt", "Cl", r"Lift coefficient, $C_l$ [-]"),
    ("cd_mil_vs_gmt", "Cd", r"Drag coefficient, $C_d$ [-]"),
    ("Re_mil_vs_gmt", "Re", r"Reynolds number, $Re$ [-]"),
    ("dTdr_mil_vs_gmt", "qz_N_per_m", r"Thrust loading, $dT/dr$ [N/m]"),
    ("dQdr_mil_vs_gmt", "torque_loading_Nm_per_m", r"Torque loading, $dQ/dr$ [N m/m]"),
    ("cumulative_thrust_mil_vs_gmt", "cumulative_thrust_N", r"Cumulative thrust, $T(r)$ [N]"),
    ("cumulative_torque_mil_vs_gmt", "cumulative_torque_Nm", r"Cumulative torque, $Q(r)$ [N m]"),
]

STATION_FIELDNAMES = [
    "case_label",
    "case_name",
    "source_geometry_csv",
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
    "inflow_model",
    "actuator_disk_initial_Vi_m_s",
    "tip_loss_factor",
    "induced_velocity_converged",
    "induced_velocity_iterations",
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

SUMMARY_FIELDNAMES = [
    "case_label",
    "geometry_csv",
    "total_thrust_N",
    "total_torque_Nm",
    "power_W",
    "CT",
    "CQ",
    "CP",
    "thrust_error_pct",
    "min_alpha_deg",
    "max_alpha_deg",
    "min_Re",
    "max_Re",
    "min_Cl",
    "max_Cl",
    "min_Cd",
    "max_Cd",
    "min_dT_N",
    "negative_thrust_station_count",
    "alpha_outside_polar_range_count",
    "tip_loading_fraction",
    "center_of_thrust_mu",
    "mean_induced_velocity_m_s",
    "min_induced_velocity_m_s",
    "max_induced_velocity_m_s",
    "induced_velocity_nonconverged_count",
    "polar_endpoint_alpha_count",
    "polar_re_endpoint_count",
    "polar_re_interpolation_count",
    "referenced_polar_file_count",
    "nonconverged_polar_table_rows",
    "warnings",
]


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    require_locked_paths()

    locked_summary = load_locked_design_summary(DESIGN_SUMMARY)
    verify_summary_matches_locked_case(locked_summary)

    mil_geometry = aero_solver.load_geometry(MIL_GEOMETRY)
    gmt_geometry = aero_solver.load_geometry(GMT_GEOMETRY)
    verify_geometry_pair(mil_geometry, gmt_geometry)

    polar = aero_solver.load_airfoil_polar_directory(aero_solver.DEFAULT_AIRFOIL_DIR)
    op = locked_operating_condition(locked_summary)

    mil_result = aero_solver.evaluate_propeller(
        MIL_GEOMETRY,
        {**op, "case_name": "locked_final_mil"},
        polar,
        case_name="locked_final_mil",
        target_match_tolerance_pct=1.0,
    )
    gmt_result = aero_solver.evaluate_propeller(
        GMT_GEOMETRY,
        {**op, "case_name": "locked_final_gmt"},
        polar,
        case_name="locked_final_gmt",
        target_match_tolerance_pct=1.0,
    )

    mil_rows = normalize_rows(mil_result["stations"], "MIL", MIL_GEOMETRY, op["radius_m"])
    gmt_rows = normalize_rows(gmt_result["stations"], "GMT", GMT_GEOMETRY, op["radius_m"])
    mil_summary = final_summary_from_aero_solver(mil_result, mil_rows, "MIL", MIL_GEOMETRY, polar)
    gmt_summary = final_summary_from_aero_solver(gmt_result, gmt_rows, "GMT", GMT_GEOMETRY, polar)

    verify_computed_metrics_match_locked_case(mil_summary, gmt_summary, locked_summary)
    fail_if_gmt_not_lower_torque(mil_summary, gmt_summary)

    comparison_rows = build_comparison_rows(mil_rows, gmt_rows)
    coefficient_rows = build_coefficient_rows(mil_summary, gmt_summary)
    warnings = build_warnings(mil_summary, gmt_summary)

    write_csv(MIL_STATION_OUT, mil_rows, STATION_FIELDNAMES)
    write_csv(GMT_STATION_OUT, gmt_rows, STATION_FIELDNAMES)
    write_csv(COMPARISON_OUT, comparison_rows, comparison_fieldnames())
    write_csv(SUMMARY_CSV_OUT, [mil_summary, gmt_summary], SUMMARY_FIELDNAMES)
    write_csv(COEFFICIENT_SUMMARY_CSV, coefficient_rows, ["coefficient", "MIL", "GMT", "GMT_minus_MIL"])
    write_json(SUMMARY_JSON_OUT, build_summary_json(locked_summary, mil_summary, gmt_summary, warnings))
    write_json(LOCKED_SUMMARY_COPY_OUT, locked_summary)
    write_plots(mil_rows, gmt_rows, mil_summary, gmt_summary)

    print_terminal_summary(mil_summary, gmt_summary, warnings)
    return 0


def require_locked_paths() -> None:
    for path in (MIL_GEOMETRY, GMT_GEOMETRY, DESIGN_SUMMARY):
        if not path.exists():
            raise FileNotFoundError(f"Required locked input is missing: {path}")
        lowered = path.as_posix().lower()
        if "candidate" in lowered or "gmt_push_candidates" in lowered:
            raise RuntimeError(f"Refusing to use candidate geometry or summary path: {path}")
    if DESIGN_SUMMARY.parent != GEOMETRY_DIR:
        raise RuntimeError(f"Design summary must come from locked geometry folder: {DESIGN_SUMMARY}")


def load_locked_design_summary(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        summary = json.load(handle)
    if "mil" not in summary or "gmt" not in summary:
        raise ValueError(f"Design summary is missing MIL/GMT sections: {path}")
    return summary


def locked_operating_condition(summary: dict[str, Any]) -> dict[str, Any]:
    op = summary["operating_point"]
    return {
        "case_name": "locked_final_aero",
        "diameter_m": float(op["diameter_m"]),
        "radius_m": float(op["radius_m"]),
        "blade_count": int(op["blade_count"]),
        "rpm": float(op["rpm"]),
        "rho": float(op.get("rho_kg_m3", 1.225)),
        "mu_air_kg_m_s": 1.81e-5,
        "V_inf_m_s": float(op["V_inf_m_s"]),
        "target_thrust_N": float(op["target_thrust_N"]),
        "hover_mode": True,
        "inflow_model": aero_solver.INFLOW_MODEL_STATIONWISE_PRANDTL,
    }


def verify_summary_matches_locked_case(summary: dict[str, Any]) -> None:
    checks = [
        ("summary MIL thrust", float(summary["mil"]["total_thrust_N"]), EXPECTED_LOCKED["mil_thrust_N"], METRIC_TOL["thrust_N"]),
        ("summary GMT thrust", float(summary["gmt"]["total_thrust_N"]), EXPECTED_LOCKED["gmt_thrust_N"], METRIC_TOL["thrust_N"]),
        ("summary MIL torque", float(summary["mil"]["total_torque_Nm"]), EXPECTED_LOCKED["mil_torque_Nm"], METRIC_TOL["torque_Nm"]),
        ("summary GMT torque", float(summary["gmt"]["total_torque_Nm"]), EXPECTED_LOCKED["gmt_torque_Nm"], METRIC_TOL["torque_Nm"]),
        (
            "summary GMT torque reduction",
            float(summary["gmt_torque_reduction_pct"]),
            EXPECTED_LOCKED["gmt_torque_reduction_pct"],
            METRIC_TOL["reduction_pct"],
        ),
        ("summary lambda_gmt", float(summary["gmt"]["lambda_gmt"]), EXPECTED_LOCKED["lambda_gmt"], METRIC_TOL["lambda"]),
        (
            "summary GMT tip loading reduction",
            float(summary["gmt"]["tip_loading_reduction_pct_vs_mil"]),
            EXPECTED_LOCKED["gmt_tip_loading_reduction_pct"],
            METRIC_TOL["fraction_pct"],
        ),
        (
            "summary GMT center shift",
            float(summary["gmt"]["center_of_thrust_shift_inboard_delta_mu"]),
            EXPECTED_LOCKED["gmt_center_shift_mu"],
            METRIC_TOL["center_mu"],
        ),
    ]
    for label, actual, expected, tolerance in checks:
        assert_close(label, actual, expected, tolerance)
    if float(summary["gmt"]["total_torque_Nm"]) >= float(summary["mil"]["total_torque_Nm"]):
        raise RuntimeError("Locked design summary fails: GMT torque is not lower than MIL.")


def verify_geometry_pair(mil: list[dict[str, Any]], gmt: list[dict[str, Any]]) -> None:
    if len(mil) != len(gmt):
        raise ValueError(f"Locked geometry station count mismatch: MIL={len(mil)}, GMT={len(gmt)}")
    for index, (m_station, g_station) in enumerate(zip(mil, gmt), start=1):
        for field in ("r_R", "r_m", "chord_m"):
            if abs(float(m_station[field]) - float(g_station[field])) > 1.0e-12:
                raise ValueError(f"Station {index} {field} mismatch: {m_station[field]} vs {g_station[field]}")
        if aero_solver._normalize_airfoil_name(str(m_station["airfoil"])) != aero_solver._normalize_airfoil_name(str(g_station["airfoil"])):
            raise ValueError(f"Station {index} airfoil mismatch: {m_station['airfoil']} vs {g_station['airfoil']}")


def normalize_rows(rows: list[dict[str, Any]], case_label: str, geometry_path: Path, rotor_radius_m: float) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        output = dict(row)
        output["case_label"] = case_label
        output["source_geometry_csv"] = str(geometry_path)
        output["beta_final_deg"] = output["twist_deg"]
        output["chord_in"] = float(output["chord_m"]) / 0.0254
        output["chord_over_R"] = float(output["chord_m"]) / rotor_radius_m
        output["alpha_outside_polar_range"] = bool(output.get("alpha_clipped", False) or output.get("polar_alpha_clipped", False))
        normalized.append(output)
    return normalized


def final_summary_from_aero_solver(
    result: dict[str, Any],
    rows: list[dict[str, Any]],
    case_label: str,
    geometry_path: Path,
    polar: aero_solver.AirfoilPolar,
) -> dict[str, Any]:
    summary = result["summary"]
    convergence = polar_table_convergence(rows)
    polar_usage = polar_usage_summary(rows, polar)
    alpha_endpoint_count = sum(parse_bool(row.get("alpha_outside_polar_range", False)) for row in rows)
    warnings: list[str] = []
    if alpha_endpoint_count:
        warnings.append(f"{alpha_endpoint_count} station(s) used alpha endpoint polar fallback")
    if int(summary.get("induced_velocity_nonconverged_count", 0)) > 0:
        warnings.append(f"{summary['induced_velocity_nonconverged_count']} station(s) did not converge stationwise induced velocity")
    if convergence["nonconverged_polar_table_rows"] > 0:
        warnings.append(f"{convergence['nonconverged_polar_table_rows']} referenced polar-table row(s) are marked unconverged")
    if polar_usage["re_endpoint_count"] > 0:
        warnings.append(f"{polar_usage['re_endpoint_count']} station(s) used Reynolds endpoint polar fallback")

    return {
        "case_label": case_label,
        "geometry_csv": str(geometry_path),
        "total_thrust_N": float(summary["thrust_N"]),
        "total_torque_Nm": float(summary["torque_Nm"]),
        "power_W": float(summary["power_W"]),
        "CT": float(summary["CT"]),
        "CQ": float(summary["CQ"]),
        "CP": float(summary["CP"]),
        "thrust_error_pct": float(summary["thrust_error_pct"]),
        "min_alpha_deg": float(summary["min_alpha_deg"]),
        "max_alpha_deg": float(summary["max_alpha_deg"]),
        "min_Re": float(summary["Re_min"]),
        "max_Re": float(summary["Re_max"]),
        "min_Cl": min(float(row["Cl"]) for row in rows),
        "max_Cl": max(float(row["Cl"]) for row in rows),
        "min_Cd": min(float(row["Cd"]) for row in rows),
        "max_Cd": max(float(row["Cd"]) for row in rows),
        "min_dT_N": min(float(row["dT_N"]) for row in rows),
        "negative_thrust_station_count": sum(float(row["dT_N"]) < -1.0e-8 for row in rows),
        "alpha_outside_polar_range_count": alpha_endpoint_count,
        "tip_loading_fraction": loading_fraction(rows, "dT_N", lower_mu=0.75),
        "center_of_thrust_mu": center_of_loading_mu(rows, "dT_N"),
        "mean_induced_velocity_m_s": float(summary["mean_induced_velocity_m_s"]),
        "min_induced_velocity_m_s": float(summary["min_induced_velocity_m_s"]),
        "max_induced_velocity_m_s": float(summary["max_induced_velocity_m_s"]),
        "induced_velocity_nonconverged_count": int(summary.get("induced_velocity_nonconverged_count", 0)),
        "polar_endpoint_alpha_count": alpha_endpoint_count,
        "polar_re_endpoint_count": polar_usage["re_endpoint_count"],
        "polar_re_interpolation_count": polar_usage["re_interpolation_count"],
        "referenced_polar_file_count": convergence["referenced_polar_file_count"],
        "nonconverged_polar_table_rows": convergence["nonconverged_polar_table_rows"],
        "warnings": "; ".join(warnings),
    }


def verify_computed_metrics_match_locked_case(
    mil: dict[str, Any],
    gmt: dict[str, Any],
    locked_summary: dict[str, Any],
) -> None:
    reduction = torque_reduction_pct(mil, gmt)
    tip_reduction = tip_loading_reduction_pct(mil, gmt)
    center_shift = center_shift_mu(mil, gmt)
    computed_checks = [
        ("computed MIL thrust", float(mil["total_thrust_N"]), EXPECTED_LOCKED["mil_thrust_N"], METRIC_TOL["thrust_N"]),
        ("computed GMT thrust", float(gmt["total_thrust_N"]), EXPECTED_LOCKED["gmt_thrust_N"], METRIC_TOL["thrust_N"]),
        ("computed MIL torque", float(mil["total_torque_Nm"]), EXPECTED_LOCKED["mil_torque_Nm"], METRIC_TOL["torque_Nm"]),
        ("computed GMT torque", float(gmt["total_torque_Nm"]), EXPECTED_LOCKED["gmt_torque_Nm"], METRIC_TOL["torque_Nm"]),
        ("computed GMT torque reduction", reduction, EXPECTED_LOCKED["gmt_torque_reduction_pct"], METRIC_TOL["reduction_pct"]),
        ("computed GMT tip loading reduction", tip_reduction, EXPECTED_LOCKED["gmt_tip_loading_reduction_pct"], METRIC_TOL["fraction_pct"]),
        ("computed GMT center shift", center_shift, EXPECTED_LOCKED["gmt_center_shift_mu"], METRIC_TOL["center_mu"]),
    ]
    for label, actual, expected, tolerance in computed_checks:
        assert_close(label, actual, expected, tolerance)
    assert_close(
        "computed vs summary torque reduction",
        reduction,
        float(locked_summary["gmt_torque_reduction_pct"]),
        METRIC_TOL["reduction_pct"],
    )


def fail_if_gmt_not_lower_torque(mil: dict[str, Any], gmt: dict[str, Any]) -> None:
    if float(gmt["total_torque_Nm"]) >= float(mil["total_torque_Nm"]):
        raise RuntimeError(
            "FAIL LOUDLY: GMT torque is not lower than MIL for the locked final geometry. "
            f"MIL={float(mil['total_torque_Nm']):.9f} N*m, GMT={float(gmt['total_torque_Nm']):.9f} N*m"
        )


def build_comparison_rows(mil_rows: list[dict[str, Any]], gmt_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for mil_row, gmt_row in zip(mil_rows, gmt_rows):
        rows.append(
            {
                "mu": mil_row["r_R"],
                "radius_m": mil_row["r_m"],
                "chord_m": mil_row["chord_m"],
                "airfoil": mil_row["airfoil"],
                "MIL_beta_final_deg": mil_row["beta_final_deg"],
                "GMT_beta_final_deg": gmt_row["beta_final_deg"],
                "GMT_minus_MIL_beta_deg": float(gmt_row["beta_final_deg"]) - float(mil_row["beta_final_deg"]),
                "MIL_alpha_deg": mil_row["alpha_deg"],
                "GMT_alpha_deg": gmt_row["alpha_deg"],
                "GMT_minus_MIL_alpha_deg": float(gmt_row["alpha_deg"]) - float(mil_row["alpha_deg"]),
                "MIL_Cl": mil_row["Cl"],
                "GMT_Cl": gmt_row["Cl"],
                "GMT_minus_MIL_Cl": float(gmt_row["Cl"]) - float(mil_row["Cl"]),
                "MIL_Cd": mil_row["Cd"],
                "GMT_Cd": gmt_row["Cd"],
                "GMT_minus_MIL_Cd": float(gmt_row["Cd"]) - float(mil_row["Cd"]),
                "MIL_Re": mil_row["Re"],
                "GMT_Re": gmt_row["Re"],
                "MIL_dT_N": mil_row["dT_N"],
                "GMT_dT_N": gmt_row["dT_N"],
                "GMT_minus_MIL_dT_N": float(gmt_row["dT_N"]) - float(mil_row["dT_N"]),
                "MIL_dQ_Nm": mil_row["dQ_Nm"],
                "GMT_dQ_Nm": gmt_row["dQ_Nm"],
                "GMT_minus_MIL_dQ_Nm": float(gmt_row["dQ_Nm"]) - float(mil_row["dQ_Nm"]),
            }
        )
    return rows


def comparison_fieldnames() -> list[str]:
    return [
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
    ]


def build_coefficient_rows(mil: dict[str, Any], gmt: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for coefficient in ("CT", "CQ", "CP"):
        mil_value = float(mil[coefficient])
        gmt_value = float(gmt[coefficient])
        rows.append(
            {
                "coefficient": coefficient,
                "MIL": mil_value,
                "GMT": gmt_value,
                "GMT_minus_MIL": gmt_value - mil_value,
            }
        )
    return rows


def build_warnings(mil: dict[str, Any], gmt: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for row in (mil, gmt):
        if row["warnings"]:
            for warning in str(row["warnings"]).split("; "):
                warnings.append(f"{row['case_label']}: {warning}")
    if not warnings:
        warnings.append("No alpha endpoint fallback, no induced-velocity nonconvergence, and no unconverged referenced polar-table rows.")
    return warnings


def build_summary_json(
    locked_summary: dict[str, Any],
    mil: dict[str, Any],
    gmt: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "generation_timestamp": datetime.now().isoformat(timespec="seconds"),
        "generating_script": "Code/aero/run_final_aero.py",
        "locked_inputs": {
            "mil_geometry": str(MIL_GEOMETRY),
            "gmt_geometry": str(GMT_GEOMETRY),
            "design_summary": str(DESIGN_SUMMARY),
            "stale_outputs_not_used": [
                str(OUTPUT_DIR / "design_summary.json"),
                str(OUTPUT_DIR / "mil_bemt_validation.csv"),
                str(OUTPUT_DIR / "gmt_bemt_validation.csv"),
            ],
        },
        "model": {
            "aero_solver_module": "Code/aero_solver.py",
            "aero_solver_model_source": aero_solver.MODEL_SOURCE_BEMT_XFOIL,
            "inflow_model": aero_solver.INFLOW_MODEL_STATIONWISE_PRANDTL,
            "polar_source": str(aero_solver.DEFAULT_AIRFOIL_DIR),
            "xfoil_status_basis": "precomputed XFOIL polar CSV convergence flags loaded by aero_solver.py",
        },
        "operating_point": locked_summary["operating_point"],
        "mil": mil,
        "gmt": {
            **gmt,
            "lambda_gmt": locked_summary["gmt"]["lambda_gmt"],
            "torque_reduction_pct_vs_mil": torque_reduction_pct(mil, gmt),
            "tip_loading_reduction_pct_vs_mil": tip_loading_reduction_pct(mil, gmt),
            "center_of_thrust_shift_inboard_delta_mu": center_shift_mu(mil, gmt),
        },
        "validation": {
            "locked_summary_matches_expected": True,
            "computed_postprocess_matches_locked_summary": True,
            "gmt_torque_lower_than_mil": float(gmt["total_torque_Nm"]) < float(mil["total_torque_Nm"]),
            "warnings": warnings,
        },
    }


def write_plots(
    mil_rows: list[dict[str, Any]],
    gmt_rows: list[dict[str, Any]],
    mil_summary: dict[str, Any],
    gmt_summary: dict[str, Any],
) -> None:
    configure_plot_style()
    for stem, field, ylabel in LINE_PLOTS:
        fig, ax = plt.subplots(figsize=(6.5, 4.1), constrained_layout=True)
        plot_case(ax, mil_rows, field, "MIL", "#1f4e79", "-", "o")
        plot_case(ax, gmt_rows, field, "GMT", "#b23a34", "--", "s")
        ax.set_xlabel(r"Normalized radius, $r/R$ [-]")
        ax.set_ylabel(ylabel)
        ax.set_xlim(min(float(row["r_R"]) for row in mil_rows), max(float(row["r_R"]) for row in mil_rows))
        y_values = [float(row[field]) for row in mil_rows] + [float(row[field]) for row in gmt_rows]
        set_padded_ylim(ax, y_values)
        finish_axes(ax)
        save_figure(fig, stem)
        plt.close(fig)
    write_coefficient_summary_plot(mil_summary, gmt_summary)


def configure_plot_style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "font.family": "serif",
            "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
            "mathtext.fontset": "stix",
            "axes.labelsize": 13,
            "xtick.labelsize": 11,
            "ytick.labelsize": 11,
            "legend.fontsize": 11,
            "axes.linewidth": 0.9,
            "svg.fonttype": "none",
        }
    )


def plot_case(
    ax: plt.Axes,
    rows: list[dict[str, Any]],
    field: str,
    label: str,
    color: str,
    linestyle: str,
    marker: str,
) -> None:
    ax.plot(
        [float(row["r_R"]) for row in rows],
        [float(row[field]) for row in rows],
        color=color,
        linestyle=linestyle,
        marker=marker,
        linewidth=2.25,
        markersize=5.2,
        markerfacecolor="white",
        markeredgewidth=1.2,
        label=label,
    )


def finish_axes(ax: plt.Axes) -> None:
    ax.minorticks_on()
    ax.grid(True, which="major", color="#c8c8c8", linewidth=0.7, alpha=0.85)
    ax.grid(True, which="minor", color="#e8e8e8", linewidth=0.45, alpha=0.75)
    ax.tick_params(axis="both", which="major", direction="out", length=4.5, width=0.8)
    ax.tick_params(axis="both", which="minor", direction="out", length=2.5, width=0.6)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="best", frameon=True, framealpha=0.95, facecolor="white", edgecolor="#bbbbbb")


def set_padded_ylim(ax: plt.Axes, values: list[float]) -> None:
    finite_values = [value for value in values if math.isfinite(value)]
    if not finite_values:
        return
    ymin = min(finite_values)
    ymax = max(finite_values)
    if math.isclose(ymin, ymax):
        pad = max(abs(ymin) * 0.05, 1.0e-6)
    else:
        pad = 0.06 * (ymax - ymin)
    ax.set_ylim(ymin - pad, ymax + pad)


def save_figure(fig: plt.Figure, stem: str) -> None:
    for suffix in ("", "_thesis"):
        fig.savefig(OUTPUT_DIR / f"{stem}{suffix}.png", dpi=350, bbox_inches="tight")
        fig.savefig(OUTPUT_DIR / f"{stem}{suffix}.svg", bbox_inches="tight")


def write_coefficient_summary_plot(mil: dict[str, Any], gmt: dict[str, Any]) -> None:
    coefficients = ["CT", "CQ", "CP"]
    fig, ax = plt.subplots(figsize=(6.5, 2.4), constrained_layout=True)
    ax.axis("off")
    cell_text = [
        [f"{float(mil[name]):.8f}" for name in coefficients],
        [f"{float(gmt[name]):.8f}" for name in coefficients],
    ]
    table = ax.table(
        cellText=cell_text,
        rowLabels=["MIL", "GMT"],
        colLabels=[r"$C_T$ [-]", r"$C_Q$ [-]", r"$C_P$ [-]"],
        loc="center",
        cellLoc="center",
        rowLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(12)
    table.scale(1.0, 1.5)
    for cell in table.get_celld().values():
        cell.set_edgecolor("#bdbdbd")
        cell.set_linewidth(0.7)
        cell.set_facecolor("white")
    save_figure(fig, "ct_cq_cp_summary")
    plt.close(fig)


def polar_table_convergence(rows: list[dict[str, Any]]) -> dict[str, int]:
    paths = referenced_polar_paths(rows)
    total = 0
    nonconverged = 0
    for path in paths:
        csv_rows, fieldnames = read_csv_rows(path)
        lookup = column_lookup(fieldnames)
        converged_col = optional_column(lookup, ("converged", "valid"))
        if converged_col is None:
            continue
        for row in csv_rows:
            total += 1
            if not parse_bool(row.get(converged_col, False)):
                nonconverged += 1
    return {
        "referenced_polar_file_count": len(paths),
        "polar_table_rows_checked": total,
        "nonconverged_polar_table_rows": nonconverged,
    }


def polar_usage_summary(rows: list[dict[str, Any]], polar: aero_solver.AirfoilPolar) -> dict[str, int]:
    endpoint_count = 0
    interpolation_count = 0
    for row in rows:
        re_used = str(row["polar_Re_used"])
        if "-" in re_used:
            interpolation_count += 1
            continue
        airfoil = aero_solver._normalize_airfoil_name(str(row["airfoil"]))
        tables = (polar.by_airfoil_re or {}).get(airfoil, {})
        re_values = sorted(re_value for re_value in tables if re_value is not None and math.isfinite(float(re_value)))
        if not re_values:
            continue
        re_actual = float(row["Re"])
        if re_actual < re_values[0] or re_actual > re_values[-1]:
            endpoint_count += 1
    return {"re_endpoint_count": endpoint_count, "re_interpolation_count": interpolation_count}


def referenced_polar_paths(rows: list[dict[str, Any]]) -> list[Path]:
    paths: set[Path] = set()
    for row in rows:
        for token in str(row["polar_source"]).split(";"):
            token = token.strip()
            if token:
                paths.add(Path(token))
    return sorted(paths)


def loading_fraction(rows: list[dict[str, Any]], field: str, *, lower_mu: float) -> float:
    total = sum(float(row[field]) for row in rows)
    selected = sum(float(row[field]) for row in rows if float(row["r_R"]) >= lower_mu)
    return selected / max(total, 1.0e-12)


def center_of_loading_mu(rows: list[dict[str, Any]], field: str) -> float:
    total = sum(float(row[field]) for row in rows)
    moment = sum(float(row["r_R"]) * float(row[field]) for row in rows)
    return moment / max(total, 1.0e-12)


def torque_reduction_pct(mil: dict[str, Any], gmt: dict[str, Any]) -> float:
    return (float(mil["total_torque_Nm"]) - float(gmt["total_torque_Nm"])) / float(mil["total_torque_Nm"]) * 100.0


def tip_loading_reduction_pct(mil: dict[str, Any], gmt: dict[str, Any]) -> float:
    return (float(mil["tip_loading_fraction"]) - float(gmt["tip_loading_fraction"])) / float(
        mil["tip_loading_fraction"]
    ) * 100.0


def center_shift_mu(mil: dict[str, Any], gmt: dict[str, Any]) -> float:
    return float(mil["center_of_thrust_mu"]) - float(gmt["center_of_thrust_mu"])


def assert_close(label: str, actual: float, expected: float, tolerance: float) -> None:
    if abs(actual - expected) > tolerance:
        raise RuntimeError(
            f"{label} does not match locked final case: actual={actual:.12g}, "
            f"expected={expected:.12g}, tolerance={tolerance:.3g}"
        )


def print_terminal_summary(mil: dict[str, Any], gmt: dict[str, Any], warnings: list[str]) -> None:
    reduction = torque_reduction_pct(mil, gmt)
    tip_reduction = tip_loading_reduction_pct(mil, gmt)
    center_shift = center_shift_mu(mil, gmt)
    alpha_min = min(float(mil["min_alpha_deg"]), float(gmt["min_alpha_deg"]))
    alpha_max = max(float(mil["max_alpha_deg"]), float(gmt["max_alpha_deg"]))
    re_min = min(float(mil["min_Re"]), float(gmt["min_Re"]))
    re_max = max(float(mil["max_Re"]), float(gmt["max_Re"]))
    xfoil_status = (
        "all referenced polar CSV rows are marked converged"
        if int(mil["nonconverged_polar_table_rows"]) == 0 and int(gmt["nonconverged_polar_table_rows"]) == 0
        else "one or more referenced polar CSV rows are marked unconverged"
    )
    print("Locked final aero postprocessing complete")
    print(f"  aero solver module: {ROOT / 'Code' / 'aero_solver.py'}")
    print(f"  inflow model: {aero_solver.INFLOW_MODEL_STATIONWISE_PRANDTL}")
    print(f"  MIL geometry: {MIL_GEOMETRY}")
    print(f"  GMT geometry: {GMT_GEOMETRY}")
    print(f"  design summary used: {DESIGN_SUMMARY}")
    print(f"  output folder: {OUTPUT_DIR}")
    print(f"  MIL thrust: {float(mil['total_thrust_N']):.6f} N")
    print(f"  GMT thrust: {float(gmt['total_thrust_N']):.6f} N")
    print(f"  MIL torque: {float(mil['total_torque_Nm']):.6f} N*m")
    print(f"  GMT torque: {float(gmt['total_torque_Nm']):.6f} N*m")
    print(f"  GMT torque reduction: {reduction:.6f}%")
    print(f"  tip loading fraction: MIL={float(mil['tip_loading_fraction']):.6f}, GMT={float(gmt['tip_loading_fraction']):.6f}")
    print(f"  GMT tip loading reduction: {tip_reduction:.6f}%")
    print(f"  center of thrust: MIL={float(mil['center_of_thrust_mu']):.6f} r/R, GMT={float(gmt['center_of_thrust_mu']):.6f} r/R")
    print(f"  GMT center-of-thrust shift: {center_shift:.6f} r/R")
    print(f"  alpha range: {alpha_min:.6f} to {alpha_max:.6f} deg")
    print(f"  Reynolds range: {re_min:.3f} to {re_max:.3f}")
    print(f"  XFOIL convergence status: {xfoil_status}")
    print("  polar fallback/interpolation warnings:")
    for warning in warnings:
        print(f"    - {warning}")


def read_csv_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"CSV has no rows: {path}")
    return rows, list(reader.fieldnames)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(row.get(key, "")) for key in fieldnames})


def write_json(path: Path, payload: dict[str, Any]) -> None:
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
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def column_lookup(columns: Iterable[str]) -> dict[str, str]:
    return {column_key(column): column for column in columns}


def column_key(column: str) -> str:
    return column.strip().lower().replace(" ", "").replace("_", "")


def optional_column(lookup: dict[str, str], candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        key = column_key(candidate)
        if key in lookup:
            return lookup[key]
    return None


if __name__ == "__main__":
    raise SystemExit(main())
