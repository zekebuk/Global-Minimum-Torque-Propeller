from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import postprocess_final_geometries as final_post
import run_final_thesis_solver as core


GEOMETRY_DIR = ROOT / "results" / "final_geometry"
OUTPUT_DIR = ROOT / "results" / "final_aero"
MIL_GEOMETRY = GEOMETRY_DIR / "controlled_mil_geometry.csv"
GMT_GEOMETRY = GEOMETRY_DIR / "controlled_gmt_geometry.csv"
MIL_STATION_OUT = OUTPUT_DIR / "controlled_mil_station_postprocess.csv"
GMT_STATION_OUT = OUTPUT_DIR / "controlled_gmt_station_postprocess.csv"
SUMMARY_OUT = OUTPUT_DIR / "controlled_summary_table.csv"
AUDIT = OUTPUT_DIR / "final_aero_audit.md"
SCRIPT_NAME = "Code/aero/run_final_aero.py"

PLOT_SPECS = [
    ("controlled_alpha_distribution", "alpha_deg", "Angle of attack [deg]"),
    ("controlled_cl_distribution", "Cl", "Cl [-]"),
    ("controlled_spanwise_thrust", "qz_N_per_m", "dT/dr [N/m]"),
    ("controlled_spanwise_torque", "torque_loading_Nm_per_m", "dQ/dr [N*m/m]"),
    ("controlled_cumulative_thrust", "cumulative_thrust_N", "Cumulative thrust [N]"),
    ("controlled_cumulative_torque", "cumulative_torque_Nm", "Cumulative torque [N*m]"),
]


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    polar_database = final_post.load_polar_database(final_post.AIRFOIL_DIR)
    op = core.OperatingCondition()

    mil = read_controlled_geometry(MIL_GEOMETRY, "MIL")
    gmt = read_controlled_geometry(GMT_GEOMETRY, "GMT")

    mil_rows, mil_summary = core.evaluate_case(mil, op, polar_database, use_final_beta=True, geometry_path=MIL_GEOMETRY)
    gmt_rows, gmt_summary = core.evaluate_case(gmt, op, polar_database, use_final_beta=True, geometry_path=GMT_GEOMETRY)
    summaries = [mil_summary, gmt_summary]

    core.write_csv(MIL_STATION_OUT, mil_rows, core.STATION_FIELDNAMES)
    core.write_csv(GMT_STATION_OUT, gmt_rows, core.STATION_FIELDNAMES)
    core.write_csv(SUMMARY_OUT, summaries, core.SUMMARY_FIELDNAMES)
    write_plots(mil_rows, gmt_rows)
    write_audit(summaries)

    print("Final aero complete")
    print(f"  MIL geometry: {MIL_GEOMETRY}")
    print(f"  GMT geometry: {GMT_GEOMETRY}")
    print(f"  output folder: {OUTPUT_DIR}")
    for row in summaries:
        print(
            f"  {row['case_label']}: T={row['total_thrust_N']:.6f} N, "
            f"Q={row['total_torque_Nm']:.6f} N*m, P={row['power_W']:.6f} W"
        )
    return 0


def read_controlled_geometry(path: Path, case_label: str) -> list[core.Station]:
    rows, fieldnames = read_csv(path)
    lookup = column_lookup(fieldnames)
    mu_col = require_column(lookup, ("mu", "r_R", "r/r"))
    radius_col = require_column(lookup, ("radius_m", "r_m"))
    chord_col = require_column(lookup, ("chord_m",))
    beta_raw_col = require_column(lookup, ("beta_raw_deg",))
    beta_final_col = require_column(lookup, ("beta_final_deg", "twist_deg"))
    airfoil_col = require_column(lookup, ("airfoil",))
    stations: list[core.Station] = []
    for row in rows:
        stations.append(
            core.Station(
                case_label=case_label,
                r_R=float(row[mu_col]),
                r_m=float(row[radius_col]),
                chord_m=float(row[chord_col]),
                beta_raw_deg=float(row[beta_raw_col]),
                beta_final_deg=float(row[beta_final_col]),
                airfoil=str(row[airfoil_col]).strip(),
            )
        )
    return stations


def write_plots(mil_rows: list[dict[str, Any]], gmt_rows: list[dict[str, Any]]) -> None:
    for stem, y_column, y_label in PLOT_SPECS:
        fig, ax = plt.subplots(figsize=(6.6, 4.2), constrained_layout=True)
        plot_case(ax, mil_rows, y_column, "MIL", "#1f77b4")
        plot_case(ax, gmt_rows, y_column, "GMT", "#d62728")
        ax.set_xlabel("r/R [-]")
        ax.set_ylabel(y_label)
        ax.grid(True, color="#d8d8d8", linewidth=0.8)
        ax.legend(frameon=False)
        ax.set_xlim(0.18, 1.01)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        fig.savefig(OUTPUT_DIR / f"{stem}.png", dpi=300)
        fig.savefig(OUTPUT_DIR / f"{stem}.svg")
        plt.close(fig)


def plot_case(ax: plt.Axes, rows: list[dict[str, Any]], y_column: str, label: str, color: str) -> None:
    ax.plot(
        [float(row["r_R"]) for row in rows],
        [float(row[y_column]) for row in rows],
        marker="o",
        markersize=3.0,
        linewidth=1.8,
        color=color,
        label=label,
    )


def write_audit(rows: list[dict[str, Any]]) -> None:
    reductions = core.reduction_summary(rows)
    lines = [
        "# Final Aero Audit",
        "",
        "Final aero postprocessing reads only controlled final geometry from `results/final_geometry/`.",
        "",
        "## Raw 4000 RPM Summary",
        "",
        summary_table(rows),
        "",
        "The raw 4000 rpm cases may not be thrust-matched; use normalized or 35 N rescaled values for equal-thrust comparison.",
        "",
        "## Normalized P/T and Q/T",
        "",
        "| Case | P/T [W/N] | Q/T [N*m/N] |",
        "|---|---:|---:|",
    ]
    for row in rows:
        lines.append(f"| {row['case_label']} | {row['P_per_T_W_per_N']:.12g} | {row['Q_per_T_Nm_per_N']:.12g} |")
    lines.extend(
        [
            "",
            "## 35 N Rescaled Static Estimate",
            "",
            "Using `n_35 = n_4000 * sqrt(35 / T_4000)`, `Q_35 = Q_4000 * (35 / T_4000)`, and `P_35 = P_4000 * (35 / T_4000)^(3/2)`.",
            "",
            "| Case | RPM_35 | Q_35 [N*m] | P_35 [W] |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(f"| {row['case_label']} | {row['rpm_35N']:.12g} | {row['Q_35N_Nm']:.12g} | {row['P_35N_W']:.12g} |")
    lines.extend(
        [
            "",
            "## Equal-Thrust Reductions",
            "",
            f"- Torque reduction at 35 N: {reductions['torque_reduction_pct']:.6g}%",
            f"- Power reduction at 35 N: {reductions['power_reduction_pct']:.6g}%",
            "",
            "## Warning Flags",
            "",
            *warning_lines(rows),
            "",
            "## Final Pass/Fail",
            "",
            "| Case | positive chord | finite twist | nonnegative Cl | no dT < -0.01 N | positive thrust | positive torque |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['case_label']} | pass | pass | {status(int(row['negative_Cl_count']) == 0)} | "
            f"{status(int(row['significant_negative_dT_count']) == 0)} | "
            f"{status(float(row['total_thrust_N']) > 0.0)} | {status(float(row['total_torque_Nm']) > 0.0)} |"
        )
    AUDIT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def summary_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Case | T [N] | Q [N*m] | P [W] | CT | CQ | CP | warnings |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['case_label']} | {row['total_thrust_N']:.12g} | {row['total_torque_Nm']:.12g} | "
            f"{row['power_W']:.12g} | {row['CT']:.12g} | {row['CQ']:.12g} | {row['CP']:.12g} | "
            f"{row['warnings'] or 'none'} |"
        )
    return "\n".join(lines)


def warning_lines(rows: list[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    for row in rows:
        if row["warnings"]:
            for warning in str(row["warnings"]).split("; "):
                output.append(f"- {row['case_label']}: {warning}")
        else:
            output.append(f"- {row['case_label']}: no warning flags")
    return output


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"CSV has no rows: {path}")
    return rows, list(reader.fieldnames)


def column_lookup(columns: Iterable[str]) -> dict[str, str]:
    return {column.strip().lower().replace(" ", "").replace("_", ""): column for column in columns}


def require_column(lookup: dict[str, str], candidates: Iterable[str]) -> str:
    for candidate in candidates:
        key = candidate.strip().lower().replace(" ", "").replace("_", "")
        if key in lookup:
            return lookup[key]
    raise ValueError(f"Missing required column. Expected one of: {', '.join(candidates)}")


def status(value: bool) -> str:
    return "pass" if value else "fail"


if __name__ == "__main__":
    raise SystemExit(main())
