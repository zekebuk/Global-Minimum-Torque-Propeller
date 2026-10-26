"""``prandtl section-polars``: XFOIL polars for a design's molded sections, and what they do to the rotor.

For each transition setting (free transition at several Ncrit, and tripped), generate the section
polar library (``aero.xfoil.section_polars``), solve the design's BEMT at its operating point and
trimmed to its thrust, and set the results beside the analytic polar and (when given) the 3D CFD.
Writes ``<out>/<setting>/`` polar files, ``stations_<setting>.csv`` (the design's stations with the
section keys, ready for ``geometry.stations_csv`` + ``aero.polar: {type: table, directory: ...}``),
``reconciliation.csv`` and ``polars.png``.
"""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from prandtl_prop.aero import xfoil
from prandtl_prop.aero.bemt import solve_rotor, trim_rpm_to_thrust

SETTINGS = {
    "free_ncrit9": xfoil.PolarSettings(ncrit=9.0),
    "free_ncrit7": xfoil.PolarSettings(ncrit=7.0),
    "free_ncrit4": xfoil.PolarSettings(ncrit=4.0),
    "tripped_5pct": xfoil.PolarSettings(ncrit=9.0, xtr_top=0.05, xtr_bot=0.05),
}


def reconcile(design, store, out_dir: Path, settings: list[str], cfd: dict[str, float] | None = None, echo=print) -> list[dict[str, Any]]:
    from prandtl_prop import mission
    from prandtl_prop.pipeline import run_design

    outcomes = run_design(design, ["aero"], store=store)
    stations, polar, corrections, collective, op = mission.bemt_context(design, outcomes["aero"].run)
    stations = replace(stations, twist_deg=stations.twist_deg + collective)
    op_spec = design.operating_point
    reynolds = op_spec.rho_kg_m3 * op.omega_rad_s * stations.r_m * stations.chord_m / op_spec.mu_air_kg_m_s
    echo(f"section Reynolds numbers at {op.rpm:.0f} rpm: {reynolds.min():,.0f} to {reynolds.max():,.0f}")
    target = design.operating_point.target_thrust_N
    rows = []

    def evaluate(label: str, st, pol, corr) -> dict[str, Any]:
        fixed = solve_rotor(st, op, pol, corrections=corr).summary
        trimmed = trim_rpm_to_thrust(
            st, op, pol, target, collective_offset_deg=0.0, tip_loss=design.aero.tip_loss, rpm_bracket=(300.0, 60000.0)
        ).summary
        row = {
            "polar": label,
            "thrust_at_design_rpm_N": float(fixed["thrust_N"]),
            "power_at_design_rpm_W": float(fixed["power_W"]),
            "rpm_for_target_thrust": float(trimmed["rpm"]),
            "power_at_target_thrust_W": float(trimmed["power_W"]),
            "figure_of_merit_at_target": float(trimmed["figure_of_merit"]),
        }
        echo(
            f"  {label:>22}: at {op.rpm:.0f} rpm T {row['thrust_at_design_rpm_N']:.3f} N, P {row['power_at_design_rpm_W']:.2f} W | "
            f"at T = {target:.3f} N: {row['rpm_for_target_thrust']:.0f} rpm, P {row['power_at_target_thrust_W']:.2f} W, FM {row['figure_of_merit_at_target']:.3f}"
        )
        return row

    rows.append(evaluate("analytic (design)", stations, polar, corrections))
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = {}
    for name in settings:
        keyed, table, _ = xfoil.section_polars(design, stations, out_dir / name, SETTINGS[name], echo=lambda *a: None)
        tables[name] = (keyed, table)
        _write_stations(out_dir / f"stations_{name}.csv", keyed, op.radius_m, collective)
        rows.append(evaluate(f"XFOIL {name}", keyed, table, None))
    if cfd:
        rows.append(
            {
                "polar": "3D CFD (k-omega SST, MRF)",
                "thrust_at_design_rpm_N": cfd.get("thrust_N"),
                "power_at_design_rpm_W": cfd.get("power_W"),
            }
        )
        echo(
            f"  {'3D CFD (SST, MRF)':>22}: T {cfd.get('thrust_N'):.3f} N, P {cfd.get('power_W'):.2f} W at {cfd.get('rpm', op.rpm):.0f} rpm"
        )
    with (out_dir / "reconciliation.csv").open("w", newline="", encoding="utf-8") as handle:
        keys = list(dict.fromkeys(k for r in rows for k in r))
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    if tables:
        _plot(tables, polar, float(np.median(reynolds)), out_dir / "polars.png", design.name)
    return rows


def _write_stations(path: Path, stations, radius_m: float, collective: float) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["r_R", "dr_m", "chord_m", "twist_deg", "airfoil"])
        for r, dr, c, b, a in zip(
            stations.r_m, stations.dr_m, stations.chord_m, stations.twist_deg - collective, stations.airfoil, strict=True
        ):
            writer.writerow([f"{r / radius_m:.6f}", f"{dr:.6e}", f"{c:.6e}", f"{b:.4f}", a])


def _plot(tables: dict, analytic, reynolds: float, output: Path, name: str) -> None:
    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import style

    style()
    alpha = np.arange(-6.0, 16.0, 0.25)
    figure, axes = plt.subplots(1, 3, figsize=(18, 5))
    colors = {"free_ncrit9": "#86b6ef", "free_ncrit7": "#2a78d6", "free_ncrit4": "#104281", "tripped_5pct": "#eb6834"}
    for label, (keyed, table) in tables.items():
        key = sorted(set(keyed.airfoil))[0]
        cl = np.array([table.evaluate(key, a, reynolds).cl for a in alpha])
        cd = np.array([table.evaluate(key, a, reynolds).cd for a in alpha])
        axes[0].plot(alpha, cl, color=colors.get(label, "#52514e"), label=f"XFOIL {label}")
        axes[1].plot(cd, cl, color=colors.get(label, "#52514e"))
        axes[2].plot(alpha, cl / cd, color=colors.get(label, "#52514e"))
    cl_a = np.clip(analytic.cl_alpha_per_rad * np.radians(alpha - analytic.alpha0_deg), analytic.cl_min, analytic.cl_max)
    cd_a = analytic.cd0 + analytic.k * cl_a**2
    for ax, x, y in ((axes[0], alpha, cl_a), (axes[1], cd_a, cl_a), (axes[2], alpha, cl_a / cd_a)):
        ax.plot(x, y, color="#0b0b0b", linestyle="--", label="analytic (design)" if ax is axes[0] else None)
    axes[0].set_xlabel("alpha (deg)")
    axes[0].set_ylabel("Cl")
    axes[0].legend(fontsize=8)
    axes[1].set_xlabel("Cd")
    axes[1].set_ylabel("Cl")
    axes[1].set_xlim(0, 0.08)
    axes[2].set_xlabel("alpha (deg)")
    axes[2].set_ylabel("L/D")
    figure.suptitle(f"{name}: section polars at Re {reynolds:,.0f} (unthickened tip section) - transition modelling", fontsize=12)
    figure.tight_layout()
    figure.savefig(output, dpi=150)
    plt.close(figure)
