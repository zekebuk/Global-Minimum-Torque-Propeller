from __future__ import annotations

from pathlib import Path

from output_paths import safe_filename_stem


def apply_matplotlib_style(plt) -> None:
    plt.rcParams.update(
        {
            "font.family": "Times New Roman",
            "font.size": 11,
            "axes.titlesize": 11,
            "axes.labelsize": 11,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 10,
            "mathtext.fontset": "custom",
            "mathtext.rm": "Times New Roman",
            "mathtext.it": "Times New Roman:italic",
            "mathtext.bf": "Times New Roman:bold",
            "mathtext.sf": "Times New Roman",
            "axes.unicode_minus": False,
            "lines.linewidth": 1.5,
            "lines.markersize": 4,
            "axes.linewidth": 0.8,
            "grid.linewidth": 0.4,
            "grid.alpha": 0.35,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
            "figure.dpi": 150,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )


def style_matplotlib_axes(ax, legend: bool = True) -> None:
    ax.grid(True, which="major")
    ax.grid(True, which="minor", alpha=0.18)
    ax.minorticks_on()
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
    ax.tick_params(direction="in", top=True, right=True, width=0.8)
    if legend:
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(frameon=False, handlelength=2.7)


def _available_stem(out_dir: Path, stem: str, formats: tuple[str, ...]) -> str:
    extensions = tuple(fmt.lower().lstrip(".") for fmt in formats)
    if not any((out_dir / f"{stem}.{extension}").exists() for extension in extensions):
        return stem

    index = 1
    while True:
        candidate = f"{stem}_{index:03d}"
        if not any((out_dir / f"{candidate}.{extension}").exists() for extension in extensions):
            return candidate
        index += 1


def save_matplotlib_figure(
    fig,
    out_dir: Path,
    stem: str,
    *,
    dpi: int = 300,
    formats: tuple[str, ...] = ("svg", "png"),
    overwrite: bool = False,
    log: bool = True,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    clean_stem = safe_filename_stem(stem)
    if not overwrite:
        clean_stem = _available_stem(out_dir, clean_stem, formats)
    saved_paths: list[Path] = []
    for fmt in formats:
        extension = fmt.lower().lstrip(".")
        target = out_dir / f"{clean_stem}.{extension}"
        kwargs = {"dpi": dpi} if extension == "png" else {}
        existed = target.exists()
        fig.savefig(target, **kwargs)
        saved_paths.append(target)
        if log:
            action = "Overwrote plot" if existed and overwrite else "Saved plot"
            print(f"{action}: {target}")

    png_paths = [path for path in saved_paths if path.suffix.lower() == ".png"]
    return png_paths[0] if png_paths else saved_paths[0]
