"""Shared matplotlib style for engineering plots (light surface, recessive chrome, validated colours).

Colour jobs (see the data-viz palette):
- ordered series (mission speeds, rpm) use one blue ramp, light to dark (validated as an ordinal ramp);
- signed fields (pressure coefficient) use a blue <-> red diverging map with a grey zero;
- magnitudes (stress, deflection, speed) use a single-hue sequential map.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
ORDINAL_BLUE = ("#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281", "#0d366b")
SERIES = ("#2a78d6", "#eb6834", "#1baf7a")  # first three categorical slots (validated all-pairs)
REFERENCE = "#898781"


def ordinal(count: int) -> list[str]:
    """``count`` steps of the blue ramp, light to dark, evenly spread over the validated range."""

    if count <= len(ORDINAL_BLUE):
        index = np.round(np.linspace(0, len(ORDINAL_BLUE) - 1, count)).astype(int)
        return [ORDINAL_BLUE[i] for i in index]
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("blue", ORDINAL_BLUE)
    return [cmap(t) for t in np.linspace(0.0, 1.0, count)]


def diverging_cmap():
    from matplotlib.colors import LinearSegmentedColormap

    return LinearSegmentedColormap.from_list("blue_red", ["#104281", "#3987e5", "#cde2fb", "#f0efec", "#fbd3c7", "#e34948", "#9c1c1c"])


def sequential_cmap(hue: str = "blue"):
    from matplotlib.colors import LinearSegmentedColormap

    ramps = {
        "blue": ["#f0efec", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"],
        "orange": ["#f0efec", "#fbd3c7", "#f3a27e", "#eb6834", "#b8461b", "#7a2a0c"],
    }
    return LinearSegmentedColormap.from_list(hue, ramps[hue])


def style() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": MUTED,
            "axes.labelcolor": INK_2,
            "axes.titlecolor": INK,
            "axes.titlesize": 11,
            "axes.labelsize": 9,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "legend.frameon": False,
            "lines.linewidth": 2.0,
            "lines.markersize": 6,
            "font.size": 9,
        }
    )


def label_end(axes, x: Sequence[float], y: Sequence[float], text: str, color: str = INK_2) -> None:
    """Direct label at the right end of a line, in text ink (identity stays with the line colour)."""

    axes.annotate(text, (x[-1], y[-1]), xytext=(4, 0), textcoords="offset points", va="center", fontsize=8, color=color)
