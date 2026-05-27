from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import matplotlib.pyplot as plt
import numpy as np

from plot_style import case_style, save_figure, set_paper_style, style_axes


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1] if ROOT.parent.name.lower() == "code" else ROOT.parent
RESULTS_DIR = PROJECT_ROOT / "results" / "fea" / "beam_solver"
FIGURES_DIR = PROJECT_ROOT / "figures_paper" / "fea"


@dataclass(frozen=True)
class CaseData:
    key: str
    group: str
    family: str
    result_csv: Path
    modal_csv: Path
    data: dict[str, list[float]]
    modal: dict[str, list[float]]


def read_numeric_csv(path: Path) -> dict[str, list[float]]:
    columns: dict[str, list[float]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            for key, raw in row.items():
                if key is None:
                    continue
                columns.setdefault(key, [])
                try:
                    columns[key].append(float(raw))
                except (TypeError, ValueError):
                    columns[key].append(math.nan)
    return columns


def load_cases() -> list[CaseData]:
    specs = [
        (
            "reference_mil",
            "reference",
            "MIL",
            RESULTS_DIR / "reference_mil" / "reference_mil_results.csv",
            RESULTS_DIR / "reference_mil" / "reference_mil_modal_frequencies.csv",
        ),
        (
            "reference_gmt",
            "reference",
            "GMT",
            RESULTS_DIR / "reference_gmt" / "reference_gmt_results.csv",
            RESULTS_DIR / "reference_gmt" / "reference_gmt_modal_frequencies.csv",
        ),
        (
            "optimized_mil",
            "optimized",
            "MIL",
            RESULTS_DIR / "optimized_mil" / "optimized_mil_results.csv",
            RESULTS_DIR / "optimized_mil" / "optimized_mil_modal_frequencies.csv",
        ),
        (
            "optimized_gmt",
            "optimized",
            "GMT",
            RESULTS_DIR / "optimized_gmt" / "optimized_gmt_results.csv",
            RESULTS_DIR / "optimized_gmt" / "optimized_gmt_modal_frequencies.csv",
        ),
    ]
    cases: list[CaseData] = []
    for key, group, family, result_csv, modal_csv in specs:
        if not result_csv.exists():
            raise FileNotFoundError(f"Missing FEA result CSV: {result_csv}")
        cases.append(
            CaseData(
                key=key,
                group=group,
                family=family,
                result_csv=result_csv,
                modal_csv=modal_csv,
                data=read_numeric_csv(result_csv),
                modal=read_numeric_csv(modal_csv) if modal_csv.exists() else {},
            )
        )
    return cases


def group_cases(cases: Sequence[CaseData], group: str) -> tuple[CaseData, CaseData]:
    selected = {case.family: case for case in cases if case.group == group}
    try:
        return selected["MIL"], selected["GMT"]
    except KeyError as exc:
        raise ValueError(f"Missing MIL/GMT case for group {group}") from exc


def family_label(family: str) -> str:
    return "GMT" if family == "GMT" else family


def series(case: CaseData, column: str, scale: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray(case.data["r/R"], dtype=float), scale * np.asarray(case.data[column], dtype=float)


def modal_series(case: CaseData) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray(case.modal["mode_number"], dtype=float), np.asarray(case.modal["frequency_hz"], dtype=float)


def gmt_on_mil_grid(mil: CaseData, gmt: CaseData, column: str, scale: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x_mil, y_mil = series(mil, column, scale)
    x_gmt, y_gmt = series(gmt, column, scale)
    y_gmt_interp = np.interp(x_mil, x_gmt, y_gmt)
    return x_mil, y_mil, y_gmt_interp


def paired_delta(
    mil_values: np.ndarray,
    gmt_values: np.ndarray,
    mode: str,
    threshold_fraction: float = 0.01,
) -> np.ndarray:
    delta = gmt_values - mil_values
    if mode == "absolute":
        return delta
    finite_mil = np.abs(mil_values[np.isfinite(mil_values)])
    if finite_mil.size == 0:
        return np.full_like(delta, np.nan, dtype=float)
    threshold = max(float(np.nanmax(finite_mil)) * threshold_fraction, 1.0e-12)
    out = np.full_like(delta, np.nan, dtype=float)
    valid = np.abs(mil_values) > threshold
    out[valid] = 100.0 * delta[valid] / mil_values[valid]
    return out


def max_abs_finite(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return math.nan
    return float(np.nanmax(np.abs(finite)))


def annotate_max_delta(
    ax,
    values: np.ndarray,
    unit: str,
    threshold: float | None = None,
    label: str = r"max $|\Delta|$",
) -> None:
    max_value = max_abs_finite(values)
    if not math.isfinite(max_value):
        return
    if threshold is not None and max_value > threshold:
        return
    ax.set_title(f"{label} = {max_value:.3g}{unit}", loc="left", fontsize=11, pad=2)


def plot_family_pair(ax, mil: CaseData, gmt: CaseData, column: str, scale: float = 1.0) -> None:
    x_mil, y_mil = series(mil, column, scale)
    x_gmt, y_gmt = series(gmt, column, scale)
    ax.plot(x_mil, y_mil, label="MIL", **case_style("MIL"))
    ax.plot(x_gmt, y_gmt, label="GMT", **case_style("GMT"))


def plot_optional_validation(ax, case: CaseData, validation_kind: str, label_prefix: str = "") -> None:
    """Overlay Abaqus comparison points when a case-specific CSV is available."""
    validation = load_optional_validation(case, validation_kind)
    if validation is None:
        return
    x_values, y_values, label = validation
    if label_prefix:
        label = f"{label_prefix} {label}"
    ax.plot(x_values, y_values, linestyle="-", label=label, **case_style(case.family, source="abaqus"))


def load_optional_validation(case: CaseData, validation_kind: str) -> tuple[list[float], list[float], str] | None:
    candidates = [
        case.result_csv.with_name(f"{case.key}_abaqus.csv"),
        case.result_csv.with_name(f"{case.key}_comparison.csv"),
        ROOT / "abaqus_comparison" / f"{case.key}_abaqus.csv",
        ROOT / "abaqus_comparison" / f"{case.key}_comparison.csv",
    ]
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        return None

    data = read_numeric_csv(path)
    x_key = first_existing_column(data, ["r/R", "mu", "span_station", "r_over_R"])
    if x_key is None:
        return None
    if validation_kind == "stress":
        y_key = first_existing_column(data, ["sigma_abaqus_MPa", "abaqus_stress_MPa", "sigma_von_mises_MPa"])
        scale = 1.0
    else:
        y_key = first_existing_column(data, ["displacement_abaqus_mm", "abaqus_displacement_mm", "displacement_abaqus_m"])
        scale = 1000.0 if y_key and y_key.endswith("_m") else 1.0
    if y_key is None:
        return None
    return data[x_key], [value * scale for value in data[y_key]], "Abaqus"


def first_existing_column(data: dict[str, list[float]], names: Sequence[str]) -> str | None:
    for name in names:
        if name in data:
            return name
    return None


def nearly_same_curve(mil: CaseData, gmt: CaseData, column: str, scale: float = 1.0) -> bool:
    _, mil_values, gmt_values = gmt_on_mil_grid(mil, gmt, column, scale)
    return bool(np.allclose(mil_values, gmt_values, rtol=1.0e-3, atol=5.0e-5))


def plot_chord_comparison(cases: tuple[CaseData, CaseData]) -> tuple[plt.Figure, plt.Axes | Sequence[plt.Axes]]:
    mil, gmt = cases
    if nearly_same_curve(mil, gmt, "chord_m"):
        fig, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
        x_values, y_values = series(mil, "chord_m")
        ax.plot(x_values, y_values, color="#333333", linestyle="-", marker="o", markerfacecolor="white", label="Locked chord")
        ax.annotate(
            "Same chord used for\nMIL and GMT cases",
            xy=(0.03, 0.08),
            xycoords="axes fraction",
            ha="left",
            va="bottom",
            fontsize=11,
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.85, "pad": 1.5},
        )
        ax.set_xlabel("r/R [-]")
        ax.set_ylabel("Chord [m]")
        style_axes(ax, legend=False)
        return fig, ax
    return plot_pair_comparison(cases, "chord_m", "Chord [m]", "absolute", "Δ [m]", diff_unit=" m")


def plot_pair_comparison(
    cases: tuple[CaseData, CaseData],
    column: str,
    ylabel: str,
    diff_mode: str,
    diff_ylabel: str,
    scale: float = 1.0,
    diff_unit: str = "%",
    validation_kind: str | None = None,
    annotate_threshold: float | None = 5.0,
) -> tuple[plt.Figure, Sequence[plt.Axes]]:
    mil, gmt = cases
    fig, ax = plt.subplots(
        2,
        1,
        figsize=(3.5, 3.25),
        sharex=True,
        constrained_layout=True,
        gridspec_kw={"height_ratios": [2.1, 1.0]},
    )
    plot_family_pair(ax[0], mil, gmt, column, scale)
    if validation_kind:
        plot_optional_validation(ax[0], mil, validation_kind, "MIL")
        plot_optional_validation(ax[0], gmt, validation_kind, "GMT")

    x_values, mil_values, gmt_values = gmt_on_mil_grid(mil, gmt, column, scale)
    delta_values = paired_delta(mil_values, gmt_values, diff_mode)
    ax[1].plot(x_values, delta_values, color="#333333", linestyle="-", marker="o", markerfacecolor="white")
    ax[1].axhline(0.0, color="#777777", linewidth=0.8, linestyle=":")
    annotate_max_delta(ax[1], delta_values, diff_unit, annotate_threshold)

    ax[0].set_ylabel(ylabel)
    ax[1].set_xlabel("r/R [-]")
    ax[1].set_ylabel(diff_ylabel)
    style_axes(ax[0], legend=True)
    style_axes(ax[1], legend=False)
    ax[0].tick_params(labelbottom=False)
    return fig, ax


def plot_pair_getter_comparison(
    cases: tuple[CaseData, CaseData],
    getter: SeriesGetter,
    ylabel: str,
    diff_mode: str,
    diff_ylabel: str,
    diff_unit: str = "%",
    annotate_threshold: float | None = None,
) -> tuple[plt.Figure, Sequence[plt.Axes]]:
    mil, gmt = cases
    fig, ax = plt.subplots(
        2,
        1,
        figsize=(3.5, 3.25),
        sharex=True,
        constrained_layout=True,
        gridspec_kw={"height_ratios": [2.1, 1.0]},
    )
    x_mil, y_mil = getter(mil)
    x_gmt, y_gmt = getter(gmt)
    x_delta, y_mil_interp, y_gmt_interp = paired_on_mil_grid(mil, gmt, getter)
    delta = paired_delta(y_mil_interp, y_gmt_interp, diff_mode)

    ax[0].plot(x_mil, y_mil, label="MIL", **case_style("MIL"))
    ax[0].plot(x_gmt, y_gmt, label="GMT", **case_style("GMT"))
    ax[1].plot(x_delta, delta, color="#333333", linestyle="-", marker="o", markerfacecolor="white")
    ax[1].axhline(0.0, color="#777777", linewidth=0.8, linestyle=":")
    annotate_max_delta(ax[1], delta, diff_unit, annotate_threshold)

    ax[0].set_ylabel(ylabel)
    ax[1].set_xlabel("r/R [-]")
    ax[1].set_ylabel(diff_ylabel)
    style_axes(ax[0], legend=True)
    style_axes(ax[1], legend=False)
    ax[0].tick_params(labelbottom=False)
    return fig, ax


def plot_modal_frequency_comparison(cases: tuple[CaseData, CaseData]) -> tuple[plt.Figure, Sequence[plt.Axes]]:
    mil, gmt = cases
    fig, ax = plt.subplots(
        2,
        1,
        figsize=(3.5, 3.25),
        sharex=True,
        constrained_layout=True,
        gridspec_kw={"height_ratios": [2.1, 1.0]},
    )
    x_mil, f_mil = modal_series(mil)
    x_gmt, f_gmt = modal_series(gmt)
    f_gmt_interp = np.interp(x_mil, x_gmt, f_gmt)
    percent_delta = paired_delta(f_mil, f_gmt_interp, "percent")

    ax[0].plot(x_mil, f_mil, label="MIL", **case_style("MIL"))
    ax[0].plot(x_gmt, f_gmt, label="GMT", **case_style("GMT"))
    ax[1].plot(x_mil, percent_delta, color="#333333", linestyle="-", marker="o", markerfacecolor="white")
    ax[1].axhline(0.0, color="#777777", linewidth=0.8, linestyle=":")
    annotate_max_delta(ax[1], percent_delta, "%", None)

    ax[0].set_ylabel("Natural frequency [Hz]")
    ax[1].set_xlabel("Mode number [-]")
    ax[1].set_ylabel("Δ [%]")
    style_axes(ax[0], legend=True)
    style_axes(ax[1], legend=False)
    ax[0].tick_params(labelbottom=False)
    return fig, ax


def case_display_label(case: CaseData) -> str:
    group_label = "Scaled Reference" if case.group == "reference" else "Optimized"
    return f"{group_label} {family_label(case.family)}"


def group_display_label(group: str) -> str:
    return "Scaled Reference" if group == "reference" else "Optimized"


def campbell_csv_path(case: CaseData) -> Path:
    return case.result_csv.with_name(f"{case.key}_campbell.csv")


def plot_campbell_diagram(case: CaseData, tolerance: float = 0.10) -> tuple[plt.Figure, plt.Axes]:
    path = campbell_csv_path(case)
    if not path.exists():
        raise FileNotFoundError(f"Missing Campbell CSV: {path}")

    data = read_numeric_csv(path)
    rpm = np.asarray(data["rpm"], dtype=float)
    mode_colors = [
        "#0072BD",
        "#D95319",
        "#77AC30",
        "#7E2F8E",
        "#4DBEEE",
    ]
    excitation_color = "#595959"

    fig, ax = plt.subplots(figsize=(6.6, 3.8), constrained_layout=True)
    all_values: list[np.ndarray] = []
    mode_series: list[np.ndarray] = []
    excitation_series: list[np.ndarray] = []

    for mode in range(1, 6):
        column = f"mode_{mode}_Hz"
        if column not in data:
            mode_series.append(np.array([], dtype=float))
            continue
        values = np.asarray(data[column], dtype=float)
        mode_series.append(values)
        all_values.append(values)
        ax.plot(rpm, positive_log_values(values), color=mode_colors[mode - 1], linewidth=1.7, label=f"Mode {mode}")

    for harmonic in range(1, 5):
        column = f"f_{harmonic}x_Hz"
        if column not in data:
            excitation_series.append(np.array([], dtype=float))
            continue
        values = np.asarray(data[column], dtype=float)
        excitation_series.append(values)
        all_values.append(values)
        ax.plot(rpm, positive_log_values(values), color=excitation_color, linestyle="--", linewidth=1.0, label=f"{harmonic}x")

    if "f_bpf_Hz" in data:
        values = np.asarray(data["f_bpf_Hz"], dtype=float)
        excitation_series.append(values)
        all_values.append(values)
        ax.plot(rpm, positive_log_values(values), color="#000000", linestyle="--", linewidth=2.0, label="BPF")

    marker_rpm, marker_freq = campbell_close_approaches(rpm, mode_series, excitation_series, tolerance)
    if marker_rpm.size:
        ax.scatter(
            marker_rpm,
            marker_freq,
            s=36,
            facecolors="white",
            edgecolors="#B30000",
            linewidths=1.0,
            label="<10% separation",
        )

    ax.set_xlabel("RPM")
    ax.set_ylabel("Frequency [Hz]")
    ax.set_title(f"{case_display_label(case)} Campbell Diagram")
    ax.set_yscale("log")
    ax.grid(True, which="major")
    ax.grid(True, which="minor", alpha=0.18)
    ax.minorticks_on()
    ax.tick_params(direction="in", top=True, right=True, width=0.8)
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False, handlelength=2.7)

    finite_parts = [values[np.isfinite(values) & (values > 0.0)] for values in all_values if values.size]
    finite_values = np.concatenate(finite_parts) if finite_parts else np.array([], dtype=float)
    if finite_values.size:
        ymin = max(0.5, 0.8 * float(np.nanmin(finite_values)))
        ymax = float(np.nanmax(finite_values))
        if ymax > 0:
            ax.set_ylim(ymin, 1.25 * ymax)
    ax.set_xlim(float(np.nanmin(rpm)), float(np.nanmax(rpm)))
    return fig, ax


def positive_log_values(values: np.ndarray) -> np.ndarray:
    out = np.asarray(values, dtype=float).copy()
    out[~np.isfinite(out) | (out <= 0.0)] = np.nan
    return out


def campbell_close_approaches(
    rpm: np.ndarray,
    mode_series: Sequence[np.ndarray],
    excitation_series: Sequence[np.ndarray],
    tolerance: float,
) -> tuple[np.ndarray, np.ndarray]:
    marker_rpm: list[float] = []
    marker_freq: list[float] = []
    for mode_freq in mode_series:
        if mode_freq.size == 0:
            continue
        for excitation in excitation_series:
            if excitation.size == 0:
                continue
            separation = np.abs(mode_freq - excitation) / np.maximum(np.abs(mode_freq), np.finfo(float).eps)
            valid = np.isfinite(separation)
            if not np.any(valid):
                continue
            valid_indices = np.flatnonzero(valid)
            local_index = int(valid_indices[np.nanargmin(separation[valid])])
            if not np.isfinite(separation[local_index]) or separation[local_index] > tolerance:
                continue
            rpm_cross = campbell_crossing_rpm(rpm, mode_freq, excitation)
            if np.isfinite(rpm_cross):
                marker_rpm.append(float(rpm_cross))
                marker_freq.append(float(np.interp(rpm_cross, rpm, mode_freq)))
            else:
                marker_rpm.append(float(rpm[local_index]))
                marker_freq.append(float(excitation[local_index]))
    return np.asarray(marker_rpm, dtype=float), np.asarray(marker_freq, dtype=float)


def campbell_crossing_rpm(rpm: np.ndarray, mode_freq: np.ndarray, excitation: np.ndarray) -> float:
    difference = excitation - mode_freq
    valid = np.isfinite(rpm) & np.isfinite(difference) & np.isfinite(mode_freq)
    rpm = rpm[valid]
    difference = difference[valid]
    mode_freq = mode_freq[valid]
    if rpm.size < 2:
        return math.nan

    close = np.flatnonzero(np.abs(difference) <= 1.0e-10 * np.maximum(np.abs(mode_freq), 1.0))
    if close.size:
        return float(rpm[int(close[0])])

    for index in range(rpm.size - 1):
        d1 = difference[index]
        d2 = difference[index + 1]
        if d1 * d2 < 0:
            return float(rpm[index] + (0.0 - d1) * (rpm[index + 1] - rpm[index]) / (d2 - d1))
    return math.nan


SeriesGetter = Callable[[CaseData], tuple[np.ndarray, np.ndarray]]


def column_series(column: str, scale: float = 1.0) -> SeriesGetter:
    def _getter(case: CaseData) -> tuple[np.ndarray, np.ndarray]:
        return series(case, column, scale)

    return _getter


def cumulative_column_series(column: str, scale: float = 1.0) -> SeriesGetter:
    def _getter(case: CaseData) -> tuple[np.ndarray, np.ndarray]:
        x = np.asarray(case.data["r/R"], dtype=float)
        r = np.asarray(case.data["r_m"], dtype=float)
        y = scale * np.asarray(case.data[column], dtype=float)
        return x, cumulative_trapezoid(r, y)

    return _getter


def cumulative_trapezoid(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    out = np.zeros_like(y, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    if np.count_nonzero(valid) < 2:
        out[:] = np.nan
        return out
    x_valid = x[valid]
    y_valid = y[valid]
    cumulative = np.zeros_like(y_valid, dtype=float)
    cumulative[1:] = np.cumsum(0.5 * (y_valid[1:] + y_valid[:-1]) * np.diff(x_valid))
    out[:] = np.nan
    out[valid] = cumulative
    return out


def paired_on_mil_grid(mil: CaseData, gmt: CaseData, getter: SeriesGetter) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x_mil, y_mil = getter(mil)
    x_gmt, y_gmt = getter(gmt)
    y_gmt_interp = np.interp(x_mil, x_gmt, y_gmt)
    return x_mil, y_mil, y_gmt_interp


def delta_values(
    mil_values: np.ndarray,
    gmt_values: np.ndarray,
    delta_mode: str,
    threshold_fraction: float,
) -> tuple[np.ndarray, str]:
    delta = gmt_values - mil_values
    if delta_mode == "absolute":
        return delta, "absolute"
    percent = paired_delta(mil_values, gmt_values, "percent", threshold_fraction)
    finite_count = np.count_nonzero(np.isfinite(percent))
    if finite_count >= max(2, int(0.25 * percent.size)):
        return percent, "percent"
    return delta, "absolute"


def padded_limits(values: Sequence[np.ndarray], pad_fraction: float = 0.05) -> tuple[float, float]:
    finite_parts = [np.asarray(value, dtype=float)[np.isfinite(value)] for value in values]
    finite = np.concatenate([part for part in finite_parts if part.size]) if any(part.size for part in finite_parts) else np.array([])
    if finite.size == 0:
        return -1.0, 1.0
    vmin = float(np.nanmin(finite))
    vmax = float(np.nanmax(finite))
    if math.isclose(vmin, vmax):
        pad = max(abs(vmin), 1.0) * pad_fraction
    else:
        pad = (vmax - vmin) * pad_fraction
    return vmin - pad, vmax + pad


def set_grouped_limits(ax: np.ndarray, x_values: list[np.ndarray], top_values: list[np.ndarray], delta: list[np.ndarray]) -> None:
    xlim = padded_limits(x_values, 0.0)
    top_ylim = padded_limits(top_values, 0.06)
    delta_ylim = padded_limits(delta, 0.12)
    for col in range(2):
        ax[0][col].set_xlim(xlim)
        ax[1][col].set_xlim(xlim)
        ax[0][col].set_ylim(top_ylim)
        ax[1][col].set_ylim(delta_ylim)


def plot_grouped_comparison(
    cases: Sequence[CaseData],
    getter: SeriesGetter,
    ylabel: str,
    delta_mode: str,
    delta_ylabel: str,
    x_label: str = "r/R [-]",
    delta_unit: str = "%",
    absolute_delta_unit: str = "",
    absolute_delta_ylabel_text: str | None = None,
    delta_note_label: str = r"max $|\Delta|$",
    threshold_fraction: float = 0.01,
) -> tuple[plt.Figure, np.ndarray]:
    fig, ax = plt.subplots(
        2,
        2,
        figsize=(6.5, 4.0),
        sharex="col",
        constrained_layout=True,
        gridspec_kw={"height_ratios": [2.1, 1.0]},
    )
    top_values: list[np.ndarray] = []
    delta_series: list[np.ndarray] = []
    all_x: list[np.ndarray] = []
    actual_delta_modes: list[str] = []

    for col, group in enumerate(("reference", "optimized")):
        mil, gmt = group_cases(cases, group)
        x_mil, y_mil = getter(mil)
        x_gmt, y_gmt = getter(gmt)
        x_delta, y_mil_interp, y_gmt_interp = paired_on_mil_grid(mil, gmt, getter)
        d_values, actual_mode = delta_values(y_mil_interp, y_gmt_interp, delta_mode, threshold_fraction)
        actual_delta_modes.append(actual_mode)

        ax[0][col].plot(x_mil, y_mil, label="MIL", **case_style("MIL"))
        ax[0][col].plot(x_gmt, y_gmt, label="GMT", **case_style("GMT"))
        ax[1][col].plot(x_delta, d_values, color="#333333", linestyle="-", marker="o", markerfacecolor="white")
        ax[1][col].axhline(0.0, color="#777777", linewidth=0.8, linestyle=":")
        note_unit = delta_unit if actual_mode == "percent" else absolute_delta_unit
        annotate_max_delta(ax[1][col], d_values, note_unit, None, delta_note_label)

        ax[0][col].set_title(group_display_label(group))
        ax[1][col].set_xlabel(x_label)
        style_axes(ax[0][col], legend=True)
        style_axes(ax[1][col], legend=False)
        ax[0][col].tick_params(labelbottom=False)

        top_values.extend([y_mil, y_gmt])
        delta_series.append(d_values)
        all_x.extend([x_mil, x_gmt, x_delta])

    set_grouped_limits(ax, all_x, top_values, delta_series)
    ax[0][0].set_ylabel(ylabel)
    fallback_label = absolute_delta_ylabel_text if absolute_delta_ylabel_text is not None else delta_ylabel
    ax[1][0].set_ylabel(delta_ylabel if all(mode == delta_mode for mode in actual_delta_modes) else fallback_label)
    return fig, ax


def plot_grouped_modal_frequency_comparison(cases: Sequence[CaseData]) -> tuple[plt.Figure, np.ndarray]:
    return plot_grouped_comparison(
        cases,
        modal_series,
        "Natural frequency [Hz]",
        "percent",
        r"$\Delta$ frequency [%]",
        x_label="Mode number [-]",
        delta_unit="%",
        threshold_fraction=0.01,
    )


def plot_grouped_chord(cases: Sequence[CaseData]) -> tuple[plt.Figure, np.ndarray]:
    fig, ax = plt.subplots(1, 2, figsize=(6.5, 2.6), sharex=True, sharey=True, constrained_layout=True)
    all_x: list[np.ndarray] = []
    all_y: list[np.ndarray] = []
    for col, group in enumerate(("reference", "optimized")):
        mil, gmt = group_cases(cases, group)
        x_mil, y_mil = series(mil, "chord_m")
        all_x.append(x_mil)
        all_y.append(y_mil)
        ax[col].plot(x_mil, y_mil, color="#333333", linestyle="-", marker="o", markerfacecolor="white")
        if nearly_same_curve(mil, gmt, "chord_m"):
            ax[col].annotate(
                "Same chord used for MIL and GMT cases",
                xy=(0.03, 0.08),
                xycoords="axes fraction",
                ha="left",
                va="bottom",
                fontsize=11,
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.85, "pad": 1.5},
            )
        else:
            x_gmt, y_gmt = series(gmt, "chord_m")
            all_x.append(x_gmt)
            all_y.append(y_gmt)
            ax[col].plot(x_gmt, y_gmt, label="GMT", **case_style("GMT"))
        ax[col].set_title(group_display_label(group))
        ax[col].set_xlabel("r/R [-]")
        style_axes(ax[col], legend=False)
    ax[0].set_ylabel("Chord [m]")
    xlim = padded_limits(all_x, 0.0)
    ylim = padded_limits(all_y, 0.06)
    for axes in ax:
        axes.set_xlim(xlim)
        axes.set_ylim(ylim)
    return fig, ax


def plot_grouped_stress_comparison(cases: Sequence[CaseData]) -> tuple[plt.Figure, Sequence[Sequence[plt.Axes]]]:
    fig, ax = plt.subplots(
        2,
        2,
        figsize=(6.5, 4.0),
        sharex="col",
        sharey="row",
        constrained_layout=True,
        gridspec_kw={"height_ratios": [2.1, 1.0]},
    )
    for col, group in enumerate(("reference", "optimized")):
        mil, gmt = group_cases(cases, group)
        plot_family_pair(ax[0][col], mil, gmt, "sigma_combined", 1.0e-6)
        x_values, mil_values, gmt_values = gmt_on_mil_grid(mil, gmt, "sigma_combined", 1.0e-6)
        percent_delta = paired_delta(mil_values, gmt_values, "percent")
        ax[1][col].plot(x_values, percent_delta, color="#333333", linestyle="-", marker="o", markerfacecolor="white")
        ax[1][col].axhline(0.0, color="#777777", linewidth=0.8, linestyle=":")
        annotate_max_delta(ax[1][col], percent_delta, "%", 5.0)

        ax[0][col].set_title(group_display_label(group))
        ax[1][col].set_xlabel("r/R [-]")
        style_axes(ax[0][col], legend=True)
        style_axes(ax[1][col], legend=False)
        ax[0][col].tick_params(labelbottom=False)

    ax[0][0].set_ylabel("Stress [MPa]")
    ax[1][0].set_ylabel("Δ [%]")
    return fig, ax


def plot_convergence_from_csv(path: Path) -> tuple[plt.Figure, plt.Axes] | None:
    if not path.exists():
        return None
    data = read_numeric_csv(path)
    x_key = first_existing_column(data, ["number_of_elements", "elements", "element_size_m", "element_size"])
    y_key = first_existing_column(data, ["max_von_mises_stress_MPa", "max_stress_MPa", "tip_displacement_mm"])
    if x_key is None or y_key is None:
        return None
    fig, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
    ax.plot(
        data[x_key],
        data[y_key],
        color="#666666",
        linestyle="--",
        marker="None",
        label="Beam model",
    )
    if len(data[y_key]) >= 2 and abs(data[y_key][-2]) > 0:
        percent_change = 100.0 * (data[y_key][-1] - data[y_key][-2]) / data[y_key][-2]
        ax.annotate(
            f"last change = {percent_change:.2f}%",
            xy=(0.03, 0.90),
            xycoords="axes fraction",
            ha="left",
            va="top",
            fontsize=11,
        )
    ax.set_xlabel("Number of elements [-]" if "element" not in x_key or "size" not in x_key else "Element size [m]")
    ax.set_ylabel("Max von Mises stress [MPa]" if "stress" in y_key else "Tip displacement [mm]")
    style_axes(ax, legend=False)
    return fig, ax


def save_and_close(fig: plt.Figure, name: str, category: str) -> None:
    save_figure(fig, name, category)
    plt.close(fig)


def clear_figures_dir() -> None:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    for category in ("beam_solver", "abaqus", "comparisons"):
        out_dir = FIGURES_DIR / category
        out_dir.mkdir(parents=True, exist_ok=True)
        for pattern in ("*.svg", "*.png"):
            for path in out_dir.glob(pattern):
                path.unlink()
    for pattern in ("*.svg", "*.png"):
        for path in FIGURES_DIR.glob(pattern):
            path.unlink()


def _legacy_per_group_main() -> None:
    return
    set_paper_style()
    clear_figures_dir()
    cases = load_cases()
    outputs: list[str] = []

    for group in ("reference", "optimized"):
        selected = group_cases(cases, group)

        # Chord distribution: plot one curve when chord is locked between MIL and GMT.
        fig, _ = plot_chord_comparison(selected)
        outputs.append(f"{group}_chord_comparison")
        save_and_close(fig, outputs[-1])

        # Twist distribution: absolute twist plus GMT-minus-MIL twist difference.
        fig, _ = plot_pair_comparison(selected, "twist_deg", "Twist [deg]", "absolute", "Δ [deg]", diff_unit=" deg", annotate_threshold=None)
        outputs.append(f"{group}_twist_comparison")
        save_and_close(fig, outputs[-1])

        # Stress comparisons: absolute stress plus percent difference on a shared r/R axis.
        fig, _ = plot_pair_comparison(
            selected,
            "sigma_combined",
            "Stress [MPa]",
            "percent",
            "Δ [%]",
            scale=1.0e-6,
            validation_kind="stress",
        )
        outputs.append(f"{group}_max_normal_stress_comparison")
        save_and_close(fig, outputs[-1])

        fig, _ = plot_pair_comparison(
            selected,
            "sigma_von_mises",
            "Stress [MPa]",
            "percent",
            "Δ [%]",
            scale=1.0e-6,
            validation_kind="stress",
        )
        outputs.append(f"{group}_von_mises_stress_comparison")
        save_and_close(fig, outputs[-1])

        # Loading and internal moments: percent difference where loads are nonzero.
        fig, _ = plot_pair_comparison(selected, "q_thrust", "Thrust loading [N/m]", "percent", "Δ [%]")
        outputs.append(f"{group}_thrust_loading_comparison")
        save_and_close(fig, outputs[-1])

        fig, _ = plot_pair_comparison(selected, "My_bending", "Flapwise bending moment [N m]", "percent", "Δ [%]")
        outputs.append(f"{group}_flapwise_bending_moment_comparison")
        save_and_close(fig, outputs[-1])

        # Torque/torsion is zero in the current run, so absolute difference is clearer than percent.
        fig, _ = plot_pair_comparison(
            selected,
            "Mx_torsion",
            "Torsional moment [N m]",
            "absolute",
            "Δ [N m]",
            diff_unit=" N m",
            annotate_threshold=None,
        )
        outputs.append(f"{group}_torsional_moment_comparison")
        save_and_close(fig, outputs[-1])

        # Displacement comparisons in millimeters with percent-difference panel.
        fig, _ = plot_pair_comparison(
            selected,
            "displacement_magnitude",
            "Displacement [mm]",
            "percent",
            "Δ [%]",
            scale=1000.0,
            validation_kind="displacement",
        )
        outputs.append(f"{group}_displacement_magnitude_comparison")
        save_and_close(fig, outputs[-1])

        fig, _ = plot_pair_comparison(
            selected,
            "uz",
            "Flapwise displacement [mm]",
            "percent",
            "Δ [%]",
            scale=1000.0,
            validation_kind="displacement",
        )
        outputs.append(f"{group}_flapwise_displacement_comparison")
        save_and_close(fig, outputs[-1])

        # Modal frequencies by mode number with percent-difference panel.
        fig, _ = plot_modal_frequency_comparison(selected)
        outputs.append(f"{group}_modal_frequency_comparison")
        save_and_close(fig, outputs[-1])

    # Wide stress figure keeps reference and optimized comparisons separated by column.
    fig, _ = plot_grouped_stress_comparison(cases)
    outputs.append("reference_vs_optimized_stress_comparison")
    save_and_close(fig, outputs[-1])

    # Optional convergence plot: generated only when a convergence_study.csv file is present.
    convergence = plot_convergence_from_csv(RESULTS_DIR / "convergence_study.csv")
    if convergence is not None:
        fig, _ = convergence
        outputs.append("convergence_study")
        save_and_close(fig, outputs[-1])

    print(f"Wrote {len(outputs)} paper figure set(s) to {FIGURES_DIR}")
    for name in outputs:
        print(f"  - {name}.svg / .png")


def main() -> None:
    set_paper_style()
    clear_figures_dir()
    cases = load_cases()
    outputs: list[tuple[str, str]] = []

    for group in ("reference", "optimized"):
        selected = group_cases(cases, group)
        category = "comparisons"

        for case in selected:
            fig, _ = plot_campbell_diagram(case)
            outputs.append((f"{case.key}_campbell_diagram", category))
            save_and_close(fig, outputs[-1][0], outputs[-1][1])

        reference_optimized_specs = [
            (f"{group}_mil_vs_gmt_twist", "twist_deg", "Twist [deg]", "absolute", r"$\Delta$ twist [deg]", 1.0, " deg"),
            (f"{group}_mil_vs_gmt_stress_distribution", "sigma_combined", "Stress [MPa]", "absolute", r"$\Delta$ stress [MPa]", 1.0e-6, " MPa"),
            (f"{group}_mil_vs_gmt_von_mises_stress", "sigma_von_mises", "von Mises stress [MPa]", "absolute", r"$\Delta$ stress [MPa]", 1.0e-6, " MPa"),
            (f"{group}_mil_vs_gmt_max_normal_stress", "sigma_abs_normal_max", "Max normal stress [MPa]", "absolute", r"$\Delta$ stress [MPa]", 1.0e-6, " MPa"),
            (f"{group}_mil_vs_gmt_thrust_loading", "q_thrust", "Thrust loading [N/m]", "percent", r"$\Delta$ [%]", 1.0, "%"),
            (f"{group}_mil_vs_gmt_torque_loading", "m_torsion_dist", "Torque loading [N m/m]", "absolute", r"$\Delta$ torque loading [N m/m]", 1.0, " N m/m"),
            (f"{group}_mil_vs_gmt_displacement", "displacement_magnitude", "Displacement [mm]", "absolute", r"$\Delta$ displacement [mm]", 1000.0, " mm"),
            (f"{group}_mil_vs_gmt_flapwise_displacement", "uz", "Flapwise displacement [mm]", "absolute", r"$\Delta$ displacement [mm]", 1000.0, " mm"),
        ]

        fig, _ = plot_chord_comparison(selected)
        outputs.append((f"{group}_mil_vs_gmt_chord", category))
        save_and_close(fig, outputs[-1][0], outputs[-1][1])

        for name, column, ylabel, mode, delta_ylabel, scale, unit in reference_optimized_specs:
            fig, _ = plot_pair_comparison(
                selected,
                column,
                ylabel,
                mode,
                delta_ylabel,
                scale=scale,
                diff_unit=unit,
                annotate_threshold=None,
            )
            outputs.append((name, category))
            save_and_close(fig, outputs[-1][0], outputs[-1][1])

        fig, _ = plot_pair_getter_comparison(
            selected,
            cumulative_column_series("q_thrust"),
            "Cumulative thrust [N]",
            "percent",
            r"$\Delta$ [%]",
            diff_unit="%",
        )
        outputs.append((f"{group}_mil_vs_gmt_cumulative_thrust", category))
        save_and_close(fig, outputs[-1][0], outputs[-1][1])

        fig, _ = plot_modal_frequency_comparison(selected)
        outputs.append((f"{group}_mil_vs_gmt_modal_frequency", category))
        save_and_close(fig, outputs[-1][0], outputs[-1][1])

    comparison_specs: list[tuple[str, SeriesGetter, str, str, str, str, str, str | None, str]] = [
        ("reference_vs_optimized_mil_gmt_stress_distribution", column_series("sigma_combined", 1.0e-6), "Stress [MPa]", "absolute", r"$\Delta$ stress [MPa]", " MPa", " MPa", None, r"max $|\Delta\sigma|$"),
        ("reference_vs_optimized_mil_gmt_von_mises_stress", column_series("sigma_von_mises", 1.0e-6), "von Mises stress [MPa]", "absolute", r"$\Delta$ stress [MPa]", " MPa", " MPa", None, r"max $|\Delta\sigma|$"),
        ("reference_vs_optimized_mil_gmt_max_normal_stress", column_series("sigma_abs_normal_max", 1.0e-6), "Max normal stress [MPa]", "absolute", r"$\Delta$ stress [MPa]", " MPa", " MPa", None, r"max $|\Delta\sigma|$"),
        ("reference_vs_optimized_mil_gmt_thrust_loading", column_series("q_thrust"), "Thrust loading [N/m]", "percent", r"$\Delta$ [%]", "%", " N/m", r"$\Delta$ thrust loading [N/m]", r"max $|\Delta|$"),
        ("reference_vs_optimized_mil_gmt_torque_loading", column_series("m_torsion_dist"), "Torque loading [N m/m]", "percent", r"$\Delta$ [%]", "%", " N m/m", r"$\Delta$ torque loading [N m/m]", r"max $|\Delta|$"),
        ("reference_vs_optimized_mil_gmt_twist", column_series("twist_deg"), "Twist [deg]", "absolute", r"$\Delta$ twist [deg]", " deg", " deg", None, r"max $|\Delta|$"),
        ("reference_vs_optimized_mil_gmt_displacement", column_series("displacement_magnitude", 1000.0), "Displacement [mm]", "absolute", r"$\Delta$ displacement [mm]", " mm", " mm", None, r"max $|\Delta|$"),
        ("reference_vs_optimized_mil_gmt_cumulative_thrust", cumulative_column_series("q_thrust"), "Cumulative thrust [N]", "percent", r"$\Delta$ [%]", "%", " N", r"$\Delta$ cumulative thrust [N]", r"max $|\Delta|$"),
        ("reference_vs_optimized_mil_gmt_cumulative_torque", cumulative_column_series("m_torsion_dist"), "Cumulative torque [N m]", "percent", r"$\Delta$ [%]", "%", " N m", r"$\Delta$ cumulative torque [N m]", r"max $|\Delta|$"),
    ]

    for name, getter, ylabel, mode, delta_ylabel, unit, abs_unit, fallback_label, note_label in comparison_specs:
        fig, _ = plot_grouped_comparison(
            cases,
            getter,
            ylabel,
            mode,
            delta_ylabel,
            delta_unit=unit,
            absolute_delta_unit=abs_unit,
            absolute_delta_ylabel_text=fallback_label,
            delta_note_label=note_label,
        )
        outputs.append((name, "comparisons"))
        save_and_close(fig, outputs[-1][0], outputs[-1][1])

    fig, _ = plot_grouped_modal_frequency_comparison(cases)
    outputs.append(("reference_vs_optimized_mil_gmt_modal_frequency", "comparisons"))
    save_and_close(fig, outputs[-1][0], outputs[-1][1])

    fig, _ = plot_grouped_chord(cases)
    outputs.append(("reference_vs_optimized_mil_gmt_chord", "comparisons"))
    save_and_close(fig, outputs[-1][0], outputs[-1][1])

    convergence = plot_convergence_from_csv(RESULTS_DIR / "convergence_study.csv")
    if convergence is not None:
        fig, _ = convergence
        outputs.append(("convergence_study", "comparisons"))
        save_and_close(fig, outputs[-1][0], outputs[-1][1])

    print(f"Wrote {len(outputs)} paper figure set(s) to {FIGURES_DIR}")
    for name, category in outputs:
        print(f"  - {category}/{name}.svg / .png")


if __name__ == "__main__":
    main()
