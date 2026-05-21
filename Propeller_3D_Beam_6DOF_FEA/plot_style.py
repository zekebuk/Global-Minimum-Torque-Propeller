from __future__ import annotations

from pathlib import Path
import sys


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from common_plot_style import apply_matplotlib_style, save_matplotlib_figure  # noqa: E402


FIGURES_DIR = Path(__file__).resolve().parent / "figures_paper"


def set_paper_style() -> None:
    import matplotlib.pyplot as plt

    apply_matplotlib_style(plt)


def save_figure(fig, name: str, category: str) -> Path:
    valid_categories = {"reference", "optimized", "comparison"}
    if category not in valid_categories:
        raise ValueError(f"Unknown figure category: {category}")

    out_dir = Path("figures_paper") / category
    return save_matplotlib_figure(fig, out_dir, name)


def style_axes(ax, legend: bool = True) -> None:
    ax.grid(True, which="major")
    ax.grid(True, which="minor", alpha=0.18)
    ax.minorticks_on()
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
    ax.tick_params(direction="in", top=True, right=True, width=0.8)
    if legend:
        handles, labels = ax.get_legend_handles_labels()
        unique_handles = []
        unique_labels = []
        for handle, label in zip(handles, labels):
            if not label or label in unique_labels:
                continue
            unique_handles.append(handle)
            unique_labels.append(label)
        if unique_handles:
            ax.legend(unique_handles, unique_labels, frameon=False, handlelength=2.7)


def case_style(family: str, source: str = "design") -> dict[str, object]:
    colors = {"MIL": "#0072B2", "GMT": "#D55E00"}
    linestyles = {"MIL": "-", "GMT": "--"}
    markers = {"MIL": "o", "GMT": "s"}
    if source == "abaqus":
        return {
            "color": "#000000",
            "linestyle": "-",
            "marker": "^",
            "markerfacecolor": "white",
            "markeredgewidth": 0.8,
        }
    if source == "beam_model":
        return {
            "color": "#666666",
            "linestyle": "--",
            "marker": "None",
            "markerfacecolor": "white",
            "markeredgewidth": 0.8,
        }
    return {
        "color": colors.get(family, "#000000"),
        "linestyle": linestyles.get(family, "-"),
        "marker": markers.get(family, "o"),
        "markerfacecolor": "white",
        "markeredgewidth": 0.8,
    }
