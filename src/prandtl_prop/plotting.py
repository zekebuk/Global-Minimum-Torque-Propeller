"""Thesis-ready plotting utilities for the locked MIL/GMT comparison."""

from __future__ import annotations

import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from prandtl_prop import config, geometry, locked_stations, metrics, structural
from prandtl_prop.external import xrotor

CASE_STYLES = {
    "MIL": {"color": "black", "linestyle": "-", "marker": "o", "hatch": ""},
    "GMT": {"color": "0.35", "linestyle": "--", "marker": "s", "hatch": "///"},
}


def apply_thesis_style() -> None:
    """Apply consistent plot styling for thesis figures."""

    plt.rcParams.update(
        {
            "figure.figsize": (6.5, 4.0),
            "figure.dpi": 120,
            "savefig.dpi": 350,
            "font.family": "DejaVu Sans",
            "axes.labelsize": 11,
            "axes.titlesize": 12,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 10,
            "axes.linewidth": 0.9,
            "lines.linewidth": 2.0,
            "lines.markersize": 4.8,
        }
    )


def save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> list[Path]:
    """Save a figure as PNG and PDF."""

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = [output_dir / f"{stem}.png", output_dir / f"{stem}.pdf"]
    for output in outputs:
        # Drop timestamps so unchanged figures stay byte-identical across runs.
        metadata = {"CreationDate": None} if output.suffix == ".pdf" else {}
        fig.savefig(output, bbox_inches="tight", metadata=metadata)
    return outputs


def make_all_thesis_figures(output_dir: Path = config.OUTPUT_FIGURES_DIR) -> list[Path]:
    """Regenerate clean thesis figures from locked processed data."""

    apply_thesis_style()
    outputs: list[Path] = []
    outputs.extend(plot_geometry_distribution("twist_deg", "Twist [deg]", "fig_twist_distribution", output_dir))
    outputs.extend(plot_geometry_distribution("chord_m", "Chord [m]", "fig_chord_distribution", output_dir))
    outputs.extend(plot_station_distribution("alpha_deg", "Angle of attack [deg]", "fig_angle_of_attack_distribution", output_dir))
    outputs.extend(plot_station_distribution("Cl", "Lift coefficient [-]", "fig_lift_coefficient_distribution", output_dir))
    outputs.extend(plot_station_distribution("Cd", "Drag coefficient [-]", "fig_drag_coefficient_distribution", output_dir))
    outputs.extend(plot_station_distribution("Re", "Reynolds number [-]", "fig_reynolds_distribution", output_dir))
    outputs.extend(
        plot_station_distribution("qz_N_per_m", "Sectional thrust loading dT/dr [N/m]", "fig_sectional_thrust_distribution", output_dir)
    )
    outputs.extend(
        plot_station_distribution(
            "torque_loading_Nm_per_m", "Sectional torque loading dQ/dr [N*m/m]", "fig_sectional_torque_distribution", output_dir
        )
    )
    outputs.extend(
        plot_station_distribution("cumulative_thrust_N", "Cumulative thrust [N]", "fig_cumulative_thrust_distribution", output_dir)
    )
    outputs.extend(
        plot_station_distribution("cumulative_torque_Nm", "Cumulative torque [N*m]", "fig_cumulative_torque_distribution", output_dir)
    )
    outputs.extend(plot_summary_bar(output_dir))
    outputs.extend(xrotor.copy_xrotor_figure(output_dir))
    outputs.extend(structural.copy_structural_figures(output_dir))
    return outputs


def plot_geometry_distribution(column: str, ylabel: str, stem: str, output_dir: Path) -> list[Path]:
    """Plot one geometry distribution for MIL and GMT."""

    rows_by_case = {"MIL": geometry.load_case_geometry("MIL"), "GMT": geometry.load_case_geometry("GMT")}
    fig, ax = plt.subplots()
    for case, rows in rows_by_case.items():
        style = CASE_STYLES[case]
        ax.plot(
            geometry.radial_stations(rows),
            geometry.numeric_column(rows, column),
            label=case,
            color=style["color"],
            linestyle=style["linestyle"],
            marker=style["marker"],
            markerfacecolor="white",
        )
    finish_axes(ax, ylabel)
    outputs = save_figure(fig, output_dir, stem)
    plt.close(fig)
    return outputs


def plot_station_distribution(column: str, ylabel: str, stem: str, output_dir: Path) -> list[Path]:
    """Plot one locked BEMT station distribution for MIL and GMT."""

    rows_by_case = {"MIL": locked_stations.load_station_data("MIL"), "GMT": locked_stations.load_station_data("GMT")}
    fig, ax = plt.subplots()
    for case, rows in rows_by_case.items():
        style = CASE_STYLES[case]
        ax.plot(
            locked_stations.column_values(rows, "r_R"),
            locked_stations.column_values(rows, column),
            label=case,
            color=style["color"],
            linestyle=style["linestyle"],
            marker=style["marker"],
            markerfacecolor="white",
        )
    finish_axes(ax, ylabel)
    outputs = save_figure(fig, output_dir, stem)
    plt.close(fig)
    return outputs


def plot_summary_bar(output_dir: Path) -> list[Path]:
    """Plot a compact modeled thrust/torque/power summary."""

    cases = metrics.locked_performance_cases()
    labels = [case.case_label for case in cases]
    x_positions = range(len(cases))

    fig, axes = plt.subplots(1, 3, figsize=(8.5, 3.2))
    series = [
        ("Thrust [N]", [case.thrust_n for case in cases]),
        ("Torque [N*m]", [case.torque_nm for case in cases]),
        ("Power [W]", [case.power_w for case in cases]),
    ]
    for ax, (ylabel, values) in zip(axes, series, strict=True):
        bars = ax.bar(
            x_positions,
            values,
            color=["white", "0.80"],
            edgecolor="black",
            linewidth=1.0,
        )
        bars[1].set_hatch(CASE_STYLES["GMT"]["hatch"])
        ax.set_xticks(list(x_positions), labels)
        ax.set_ylabel(ylabel)
        ax.grid(True, axis="y", color="0.82", linewidth=0.7)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    fig.tight_layout()
    outputs = save_figure(fig, output_dir, "fig_mil_gmt_summary_bar")
    plt.close(fig)
    return outputs


def finish_axes(ax: plt.Axes, ylabel: str) -> None:
    """Apply common labels, grid, and legend to a line plot."""

    ax.set_xlabel("Normalized radius r/R [-]")
    ax.set_ylabel(ylabel)
    ax.grid(True, which="major", color="0.82", linewidth=0.7)
    ax.grid(True, which="minor", color="0.92", linewidth=0.45)
    ax.minorticks_on()
    ax.legend(loc="best", frameon=True, edgecolor="0.7")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def mirror_existing_final_figures(output_dir: Path = config.OUTPUT_FIGURES_DIR) -> list[Path]:
    """Copy existing final thesis figures into the clean output folder."""

    output_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for source in (config.FINAL_GEOMETRY_DIR).glob("*.png"):
        destination = output_dir / source.name
        shutil.copy2(source, destination)
        copied.append(destination)
    return copied
