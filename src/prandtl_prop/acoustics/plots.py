"""Acoustic figures: spectrum by source, directivity, and the twist comparison."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from prandtl_prop.acoustics.rotor import P_REF, a_weighting_db

SOURCE_LABELS = {
    "thickness": "thickness (tonal)",
    "loading": "loading (tonal)",
    "tbl_te": "TBL trailing edge",
    "lbl_vs": "laminar vortex shedding",
    "bluntness": "TE bluntness",
    "tip": "tip vortex formation",
}
SOURCE_COLORS = {
    "thickness": "#8a63d2",
    "loading": "#2a78d6",
    "tbl_te": "#eb6834",
    "lbl_vs": "#1baf7a",
    "bluntness": "#c9a227",
    "tip": "#d6336c",
}


def _db(x):
    return 10.0 * np.log10(np.maximum(x, 1e-30))


def plot_design(noise: dict[str, Any], title: str, output: Path) -> Path:
    """Three panels: 1/3-octave spectrum by source at the reference observer, tones, and directivity."""

    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import INK_2, style

    style()
    s = noise["summary"]
    ref = int(np.argmin(np.abs(noise["elevations_deg"] - s["reference_elevation_deg"])))
    f = noise["bands_hz"]
    figure, axes = plt.subplots(1, 3, figsize=(19, 5.0))
    ax = axes[0]
    for name, power in noise["source_bands"].items():
        ax.semilogx(f, _db(power[ref] / P_REF**2), color=SOURCE_COLORS.get(name, INK_2), label=SOURCE_LABELS.get(name, name), linewidth=1.4)
    ax.semilogx(f, _db(noise["total_bands"][ref] / P_REF**2), color="#0b0b0b", linewidth=2.2, label="total")
    ax.set_ylim(bottom=max(0.0, float(np.nanmax(_db(noise["total_bands"][ref] / P_REF**2))) - 60.0))
    ax.set_xlabel("1/3-octave band centre (Hz)")
    ax.set_ylabel("SPL (dB re 20 uPa)")
    ax.set_title(f"Spectrum at {s['observer_distance_m']:.2f} m, {s['reference_elevation_deg']:.0f} deg elevation")
    ax.legend(fontsize=8)
    ax = axes[1]
    tones = noise["tones"]
    fh = tones["frequencies_hz"]
    for key, color, label in (("loading", "#2a78d6", "loading"), ("thickness", "#8a63d2", "thickness"), ("total", "#0b0b0b", "total")):
        ax.plot(fh / s["bpf_hz"], _db(tones[key][ref] ** 2 / P_REF**2), marker="o", markersize=4, color=color, label=label)
    ax.set_xlabel("harmonic of the blade-passing frequency")
    ax.set_ylabel("tone SPL (dB)")
    ax.set_title(f"Tonal harmonics (BPF = {s['bpf_hz']:.0f} Hz)")
    ax.legend(fontsize=8)
    ax = axes[2]
    el = noise["elevations_deg"]
    ax.plot(el, noise["oaspl"], color="#0b0b0b", linewidth=2.0, label="total OASPL")
    ax.plot(el, noise["tonal_oaspl"], color="#2a78d6", label="tonal")
    ax.plot(el, noise["broadband_oaspl"], color="#eb6834", label="broadband")
    ax.plot(el, noise["dba"], color="#0b0b0b", linestyle="--", label="total dBA")
    ax.set_xlabel("observer elevation (deg; - below the rotor plane, wake side)")
    ax.set_ylabel("level (dB)")
    ax.set_title(f"Directivity; sound power {s['pwl_dB']:.1f} dB ({s['pwl_A_dB']:.1f} dBA)")
    ax.legend(fontsize=8)
    figure.suptitle(title, fontsize=12)
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150)
    plt.close(figure)
    return output


def comparison_rows(compare: dict[str, Any]) -> list[dict[str, Any]]:
    reference = compare["rows"]["Betz/MIL"][1]["summary"]
    rows = []
    for name, (result, noise) in compare["rows"].items():
        s = noise["summary"]
        row = {
            "case": name,
            "torque_mNm": float(result.summary["torque_Nm"]) * 1e3,
            "center_of_thrust_r_R": float(result.summary["center_of_thrust_mu"]),
            "gamma_peak_r_R": s["gamma_peak_r_R"],
            "gamma_one_tip_chord_inboard_m2_s": s["gamma_one_tip_chord_inboard_m2_s"],
            "alpha_tip_effective_deg": s["alpha_tip_effective_deg"],
            "gamma_tip_vortex_m2_s": s["gamma_tip_vortex_m2_s"],
            "bvi_proxy_delta_dB_vs_MIL": 20.0 * np.log10(s["gamma_tip_vortex_m2_s"] / reference["gamma_tip_vortex_m2_s"]),
        }
        for key in (
            "pwl_dB",
            "pwl_A_dB",
            "tonal_pwl_dB",
            "broadband_pwl_dB",
            "loading_pwl_dB",
            "thickness_pwl_dB",
            "tbl_te_pwl_dB",
            "bluntness_pwl_dB",
            "tip_pwl_dB",
            "dba_ref_dB",
            "bpf1_ref_dB",
        ):
            if key in s:
                row[key] = s[key]
                row[f"delta_{key}_vs_MIL"] = s[key] - reference[key]
        rows.append(row)
    return rows


def plot_comparison(compare: dict[str, Any], title: str, output: Path) -> Path:
    """Loading and circulation per case, tonal harmonics at the reference observer, and dB deltas against MIL."""

    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import style

    style()
    rows = compare["rows"]
    names = list(rows)
    palette = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281", "#0d366b"]
    colors = {}
    gmt_names = [n for n in names if n.startswith("GMT")]
    for n, c in zip(gmt_names, palette[: len(gmt_names)], strict=False):
        colors[n] = c
    colors["Betz/MIL"] = "#eb6834"
    for n in names:
        if n.startswith("constant pitch"):
            colors[n] = "#1baf7a"
    colors["design as built"] = "#0b0b0b"
    figure, axes = plt.subplots(1, 4, figsize=(23, 5.0))
    for name, (result, noise) in rows.items():
        style_ = "--" if name == "design as built" else "-"
        x = noise["r_R"]
        axes[0].plot(x, [s.thrust_per_span_N_m for s in result.solutions], color=colors.get(name, "#52514e"), linestyle=style_, label=name)
        axes[1].plot(x, noise["gamma"], color=colors.get(name, "#52514e"), linestyle=style_)
        s = noise["summary"]
        ref = int(np.argmin(np.abs(noise["elevations_deg"] - s["reference_elevation_deg"])))
        tones = noise["tones"]
        axes[2].plot(
            tones["frequencies_hz"] / s["bpf_hz"],
            _db(tones["loading"][ref] ** 2 / P_REF**2),
            color=colors.get(name, "#52514e"),
            linestyle=style_,
            marker="o",
            markersize=3,
        )
    axes[0].set_title("Thrust loading (matched total thrust)")
    axes[0].set_xlabel("r / R")
    axes[0].set_ylabel("dT/dr, all blades (N/m)")
    axes[0].legend(fontsize=7)
    axes[1].set_title("Bound circulation per blade (feeds the tip vortex)")
    axes[1].set_xlabel("r / R")
    axes[1].set_ylabel("Gamma = W c Cl / 2 (m^2/s)")
    axes[2].set_title("Loading-noise harmonics at the reference observer")
    axes[2].set_xlabel("harmonic of BPF")
    axes[2].set_ylabel("tone SPL (dB)")
    table = comparison_rows(compare)
    keys = ("pwl_A_dB", "loading_pwl_dB", "tbl_te_pwl_dB", "broadband_pwl_dB", "bvi_proxy")
    labels = ("total (dBA power)", "loading tones", "TBL trailing edge", "all broadband", "BVI proxy (Gamma_v)")
    width = 0.8 / len(table)
    for i, row in enumerate(table):
        values = [row.get(f"delta_{k}_vs_MIL", row.get("bvi_proxy_delta_dB_vs_MIL") if k == "bvi_proxy" else np.nan) for k in keys]
        axes[3].bar(np.arange(len(keys)) + i * width, values, width=width, color=colors.get(row["case"], "#52514e"), label=row["case"])
    axes[3].axhline(0.0, color="#52514e", linewidth=0.8)
    axes[3].set_xticks(np.arange(len(keys)) + 0.4 - width / 2, labels, fontsize=8, rotation=15)
    axes[3].set_ylabel("sound power change vs Betz/MIL (dB)")
    axes[3].set_title("What the twist does to the noise (matched thrust, rpm, planform)")
    figure.suptitle(title, fontsize=12)
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150)
    plt.close(figure)
    return output


__all__ = ["plot_design", "plot_comparison", "comparison_rows", "a_weighting_db"]
