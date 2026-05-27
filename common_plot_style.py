from __future__ import annotations

from pathlib import Path


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


def save_matplotlib_figure(fig, out_dir: Path, stem: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / f"{stem}.svg")
    fig.savefig(out_dir / f"{stem}.png", dpi=300)
    return out_dir / f"{stem}.png"
