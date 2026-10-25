"""``prandtl cost``: cost per prop against annual volume for each design and production route.

Parts come from the design's cad and dfm runs (cached):

- one-piece props (``hub.variant: one_piece``): the whole prop is one molding. Its thickest wall is
  the hub boss when the boss is solid; a cored boss (ribbed, ``CORED_WALL_M`` walls) is costed as
  the DFM alternative, since cooling time grows with the wall squared.
- bolted or folding props: B identical blades, the thickest wall being the root tab or the thickest
  airfoil section, plus the machined hub (``cost.hub_cost``).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from prandtl_prop.design import DesignCase
from prandtl_prop.manufacturing.cost import CostAssumptions, Part, per_prop, projected_area_from_stl

CORED_WALL_M = 0.003
VOLUMES = (100.0, 1.0e3, 1.0e4, 1.0e5, 1.0e6)


def parts_for_design(design: DesignCase, runs: dict[str, Any]) -> list[tuple[str, list[Part], bool]]:
    """[(variant label, parts, folding hub?)] for a design, from its cad and dfm runs."""

    cad, dfm = runs["cad"], runs["dfm"]
    section_max = float(dfm.metrics["max_section_thickness_m"])
    if design.root.enabled and design.hub.variant == "one_piece":
        area = projected_area_from_stl(cad.output("rotor_m.stl"))
        volume = float(cad.metrics["rotor_volume_m3"])
        solid = Part(design.name, volume, area, max(design.hub.boss_thickness_m, section_max), 1, "solid boss")
        cored = Part(design.name, volume * 0.97, area, max(CORED_WALL_M, section_max), 1, f"cored boss ({CORED_WALL_M * 1e3:.0f} mm walls)")
        return [("solid boss", [solid], False), (cored.note, [cored], False)]
    area = projected_area_from_stl(cad.output("blade_m.stl"))
    wall = max(design.root.tab_thickness_m if design.root.enabled else 0.0, section_max)
    blade = Part(f"{design.name} blade", float(cad.metrics["blade_volume_m3"]), area, wall, design.operating_point.blade_count, "blade")
    return [("blades + machined hub", [blade], True)]


def cost_table(designs: list[DesignCase], runs_by_design: dict[str, dict[str, Any]], a: CostAssumptions) -> list[dict[str, Any]]:
    rows = []
    for design in designs:
        for label, parts, hub in parts_for_design(design, runs_by_design[design.name]):
            for volume in VOLUMES:
                for route, entry in per_prop(parts, volume, a, folding_hub=hub).items():
                    rows.append(
                        {
                            "design": design.name,
                            "variant": label,
                            "props_per_year": volume,
                            "route": route,
                            "detail": entry["detail"],
                            "feasible": entry["feasible"],
                            "usd_per_prop": entry["total"],
                            "material": entry.get("material", 0.0),
                            "machine": entry.get("machine", 0.0),
                            "tooling": entry.get("tooling", 0.0),
                            "finishing": entry.get("finishing", 0.0),
                            "hub": entry.get("hub", 0.0),
                            "note": entry["note"],
                            "part_volume_cm3": sum(p.volume_m3 * p.count_per_prop for p in parts) * 1e6,
                            "projected_area_cm2": parts[0].projected_area_m2 * 1e4,
                            "thickest_wall_mm": parts[0].thickest_wall_m * 1e3,
                        }
                    )
    return rows


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot(rows: list[dict[str, Any]], output: Path) -> None:
    """One panel per design variant: cost per prop against annual volume, one line per route (log-log)."""

    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import SERIES, style

    style()
    variants = list(dict.fromkeys((r["design"], r["variant"]) for r in rows))
    routes = list(dict.fromkeys(r["route"] for r in rows))
    colors = dict(zip(routes, [SERIES[0], SERIES[1], SERIES[2], "#8a63d2", "#52514e"], strict=False))
    figure, axes = plt.subplots(1, len(variants), figsize=(5.6 * len(variants), 4.9), sharey=True)
    for ax, (design, variant) in zip(list(axes) if len(variants) > 1 else [axes], variants, strict=True):
        for route in routes:
            pick = [r for r in rows if r["design"] == design and r["variant"] == variant and r["route"] == route]
            v = [r["props_per_year"] for r in pick]
            usd = [r["usd_per_prop"] if r["feasible"] else float("nan") for r in pick]
            ax.loglog(v, usd, marker="o", markersize=4, color=colors[route], label=route)
        ax.set_title(f"{design}\n{variant}", fontsize=10)
        ax.set_xlabel("props per year")
        ax.grid(True, which="both", alpha=0.3)
    (axes[0] if len(variants) > 1 else axes).set_ylabel("cost per prop (USD, ex-works)")
    (axes[0] if len(variants) > 1 else axes).legend(fontsize=8, loc="upper right")
    figure.suptitle("Production cost per prop by route (missing points: route infeasible, e.g. shot too large)", fontsize=12)
    figure.tight_layout()
    figure.savefig(output, dpi=150)
    plt.close(figure)


def plot_breakdown(rows: list[dict[str, Any]], output: Path, volume: float = 1.0e5) -> None:
    """Stacked cost per prop for injection molding at one annual volume, per design variant."""

    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import style

    style()
    pick = [r for r in rows if r["route"] == "injection" and r["props_per_year"] == volume]
    labels = [f"{r['design']}\n{r['variant']}" for r in pick]
    keys = ("material", "machine", "tooling", "finishing", "hub")
    colors = ("#2a78d6", "#eb6834", "#1baf7a", "#8a63d2", "#898781")
    figure, ax = plt.subplots(figsize=(2.6 * len(pick) + 3, 4.8))
    bottom = [0.0] * len(pick)
    for key, color in zip(keys, colors, strict=True):
        values = [r[key] for r in pick]
        ax.bar(range(len(pick)), values, bottom=bottom, color=color, label=key, edgecolor="white", linewidth=1.5)
        bottom = [b + v for b, v in zip(bottom, values, strict=True)]
    for i, total in enumerate(bottom):
        ax.text(i, total, f"${total:.2f}", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(range(len(pick)), labels, fontsize=8)
    ax.set_ylabel("USD per prop")
    ax.set_title(f"Injection molding at {volume:,.0f} props/year: where the cost goes")
    ax.legend(fontsize=8)
    figure.tight_layout()
    figure.savefig(output, dpi=150)
    plt.close(figure)
