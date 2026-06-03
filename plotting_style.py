from __future__ import annotations

from pathlib import Path

from common_plot_style import apply_matplotlib_style, style_matplotlib_axes
from output_paths import safe_filename_stem


DPI = 300
FONT_SIZE = 11
LINE_WIDTH = 1.5
MARKER_SIZE = 4
GRID_ALPHA = 0.18
GRID_WIDTH = 0.35
FIGSIZE_SINGLE = (5.5, 3.4)
FIGSIZE_DOUBLE = (6.5, 4.0)

THESIS_DPI = DPI
THESIS_FORMATS = ("png", "svg", "pdf")
THESIS_FIGSIZE = FIGSIZE_DOUBLE


def apply_thesis_style(plt) -> None:
    apply_matplotlib_style(plt)
    plt.rcParams.update(
        {
            "font.family": "Times New Roman",
            "font.size": FONT_SIZE,
            "axes.titlesize": FONT_SIZE,
            "axes.labelsize": FONT_SIZE,
            "legend.fontsize": 9,
            "lines.linewidth": LINE_WIDTH,
            "lines.markersize": MARKER_SIZE,
            "figure.figsize": THESIS_FIGSIZE,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "savefig.dpi": DPI,
            "grid.alpha": GRID_ALPHA,
            "grid.linewidth": GRID_WIDTH,
        }
    )


def style_axes(ax, *, legend: bool = True, grid: bool = True) -> None:
    style_matplotlib_axes(ax, legend=legend)
    ax.grid(grid, which="major", alpha=GRID_ALPHA, linewidth=GRID_WIDTH)
    ax.grid(False, which="minor")


def save_figure(
    fig,
    output_dir_or_path_base: Path,
    stem: str | None = None,
    *,
    formats: tuple[str, ...] = THESIS_FORMATS,
) -> dict[str, Path]:
    if stem is None:
        output_dir = output_dir_or_path_base.parent
        clean = safe_filename_stem(output_dir_or_path_base.stem)
    else:
        output_dir = output_dir_or_path_base
        clean = safe_filename_stem(stem)
    paths: dict[str, Path] = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    for fmt in formats:
        suffix = fmt.lower().lstrip(".")
        path = output_dir / f"{clean}.{suffix}"
        kwargs = {"dpi": DPI} if suffix == "png" else {}
        fig.savefig(path, **kwargs)
        paths[suffix] = path
    return paths
