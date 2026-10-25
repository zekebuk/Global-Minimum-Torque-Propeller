"""Production cost per prop against annual volume, for the processes a molded prop can be made by.

Every number that is not geometry is an assumption, collected in ``CostAssumptions`` with its basis
(docs/production.md lists the sources). Geometry comes from the pipeline's cad stage: part volume,
mass, the projected area on the parting plane (from the STL), and the thickest wall.

Process models (first principles where the physics sets the cost):

- **Injection molding.** Cooling dominates the cycle: the 1-D slab solution gives
      t_c = s^2 / (pi^2 alpha) ln[(4/pi) (T_melt - T_mold) / (T_eject - T_mold)]
  for the thickest wall s. Cycle = t_c + fill/pack + open/eject. Clamp force = cavity pressure x
  projected area x cavities x safety, which sets the press class and its hourly rate. Tooling:
  aluminum, P20, or hardened steel, sized by cavity count and part area, replaced every tool life
  (carbon fiber is abrasive, so aluminum lives are short with CF30). Runner scrap adds material.
- **Benchtop injection** (electric desktop press): the same physics, limited by shot volume and
  clamp, manual cycle, one aluminum cavity made in-house.
- **FDM printing** (PA6-CF filament): deposition-rate-limited print time, printer amortized per
  hour, filament at retail price, operator time per part.
- **SLS/MJF service** (PA12 class): price per cm^3 plus a per-order setup share.
- **Forged-carbon compression molding** (chopped carbon prepreg/SMC): cure-time-limited cycle in a
  heated press, matched steel die, deflash labor.

Every route adds balancing and inspection. Hub hardware for the folding prop is costed separately
(CNC plates + COTS) by ``hub_cost``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Part:
    name: str
    volume_m3: float
    projected_area_m2: float  # on the parting plane (the rotor disk plane)
    thickest_wall_m: float  # sets the injection cooling time
    count_per_prop: int = 1
    note: str = ""


@dataclass(frozen=True)
class CostAssumptions:
    # Material
    pa6_cf30_density_kg_m3: float = 1280.0
    resin_usd_per_kg: float = 10.0  # compounded PA6-CF30 pellets, Asia: about $5-13/kg (marketplace listings)
    filament_usd_per_kg: float = 60.0  # PA6-CF filament retail: about $35-85/kg
    runner_scrap_fraction: float = 0.15  # cold runner; regrind of CF30 is limited (fiber attrition)
    # Injection physics (PA6-CF30, conditioned grades: melt 250-300 C, mold 80-90 C for crystallinity)
    melt_C: float = 280.0
    mold_C: float = 85.0
    eject_C: float = 170.0
    diffusivity_m2_s: float = 1.5e-7  # carbon fiber raises conductivity above unfilled PA6 (~1.0e-7)
    fill_pack_s: float = 3.0
    open_eject_s: float = 4.0
    cavity_pressure_Pa: float = 50.0e6  # fiber-filled nylon, long thin flow paths
    clamp_safety: float = 1.2
    # Press economics: fully burdened in-house rate (depreciation, power, maintenance, floor) excluding labor.
    # Molder surveys quote $9-35/h for 25-100 t presses including margin.
    press_rate_base_usd_h: float = 18.0
    press_rate_per_tonne_usd_h: float = 0.12
    labor_usd_h: float = 25.0  # set ~6 for an Asian contract molder
    operator_share_automatic: float = 0.15  # one operator tends several automatic presses
    # Tooling (single-cavity aluminum small part from ~$1.5-5k; steel 2-4x aluminum)
    tool_base_usd: float = 2500.0
    tool_per_cavity_usd: float = 2000.0
    tool_reference_area_m2: float = 0.01  # tool cost grows with sqrt(projected area / this)
    tool_class_factor: dict[str, float] = field(default_factory=lambda: {"aluminum": 1.0, "P20": 2.5, "hardened": 4.0})
    tool_life_shots: dict[str, float] = field(default_factory=lambda: {"aluminum": 5.0e3, "P20": 1.5e5, "hardened": 1.0e6})
    # Benchtop electric injection (desktop press, ~30 cm^3 shot, ~315 C max; ~$13.5k)
    benchtop_shot_m3: float = 30.0e-6
    benchtop_price_usd: float = 13500.0
    benchtop_cycle_overhead_s: float = 60.0  # manual mold handling per shot
    benchtop_life_h: float = 6000.0
    # FDM
    printer_price_usd: float = 2500.0
    printer_life_h: float = 6000.0
    printer_running_usd_h: float = 0.30
    deposition_cm3_h: float = 15.0  # dense, strong settings with a 0.4-0.6 mm hardened nozzle
    print_operator_min: float = 5.0
    # SLS/MJF service (PA12 class; ~EUR 1.5-2.0 per cm^3 plus setup)
    sls_usd_per_cm3: float = 1.9
    sls_setup_usd_per_order: float = 9.0
    sls_parts_per_order: int = 20
    # Forged carbon (chopped carbon prepreg / SMC; fast-cure grades 40-120 s at 130-150 C)
    forged_density_kg_m3: float = 1500.0
    forged_material_usd_per_kg: float = 45.0
    forged_cure_s: float = 120.0
    forged_handling_s: float = 150.0  # charge placement, demold, mold clean
    forged_press_rate_usd_h: float = 22.0
    forged_tool_usd: float = 9000.0  # matched steel die, one cavity (scaled by sqrt(area))
    forged_tool_life: float = 5.0e4
    forged_deflash_min: float = 2.0
    forged_yield: float = 0.9
    # Finishing (every route)
    balance_inspect_manual_s: float = 45.0  # static balance on a magnetic stand, weigh, visual
    balance_inspect_auto_s: float = 6.0  # automated weigh + CG measure + mark (high volume)
    balance_station_rate_usd_h: float = 30.0
    auto_balance_from_volume: float = 5.0e4
    molding_yield: float = 0.97


def projected_area_from_stl(path: Path) -> float:
    """Area of the part's shadow on the xy plane: sum |A_xy| / 2 over the closed surface's triangles."""

    import struct

    data = Path(path).read_bytes()
    if data[:5] == b"solid" and b"facet" in data[:400]:
        vertices = [list(map(float, line.split()[1:4])) for line in data.decode().splitlines() if line.strip().startswith("vertex")]
        tri = np.array(vertices).reshape(-1, 3, 3)
    else:
        count = struct.unpack("<I", data[80:84])[0]
        raw = np.frombuffer(data[84 : 84 + 50 * count], dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
        tri = raw["v"].astype(float)
    a, b, c = tri[:, 0, :2], tri[:, 1, :2], tri[:, 2, :2]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    return float(np.abs(cross).sum() / 4.0)


# ------------------------------------------------------------------------------------- processes


def cooling_time_s(wall_m: float, a: CostAssumptions) -> float:
    ratio = (4.0 / math.pi) * (a.melt_C - a.mold_C) / (a.eject_C - a.mold_C)
    return wall_m**2 / (math.pi**2 * a.diffusivity_m2_s) * math.log(ratio)


def _tool_cost(part: Part, cavities: int, tool: str, a: CostAssumptions, base: float | None = None) -> float:
    size = math.sqrt(max(part.projected_area_m2, 1e-6) / a.tool_reference_area_m2)
    core = (a.tool_base_usd if base is None else base) + a.tool_per_cavity_usd * cavities
    return a.tool_class_factor[tool] * core * size


def _finishing(volume: float, a: CostAssumptions) -> float:
    seconds = a.balance_inspect_auto_s if volume >= a.auto_balance_from_volume else a.balance_inspect_manual_s
    return seconds / 3600.0 * (a.balance_station_rate_usd_h + a.labor_usd_h)


def _amortised_tool(tool_cost: float, life: float, volume: float, cavities: int) -> float:
    shots = volume / cavities
    return tool_cost * max(1, math.ceil(shots / life)) / volume


def injection(part: Part, volume: float, a: CostAssumptions, *, tool: str, cavities: int) -> dict[str, Any]:
    t_cool = cooling_time_s(part.thickest_wall_m, a)
    cycle = t_cool + a.fill_pack_s + a.open_eject_s
    force_N = a.cavity_pressure_Pa * part.projected_area_m2 * cavities * a.clamp_safety
    tonnes = force_N / 9806.65
    press_rate = a.press_rate_base_usd_h + a.press_rate_per_tonne_usd_h * tonnes + a.operator_share_automatic * a.labor_usd_h
    material = part.volume_m3 * a.pa6_cf30_density_kg_m3 * (1.0 + a.runner_scrap_fraction) * a.resin_usd_per_kg
    machine = cycle / cavities / 3600.0 * press_rate
    tooling = _amortised_tool(_tool_cost(part, cavities, tool, a), a.tool_life_shots[tool], volume, cavities)
    finishing = _finishing(volume, a)
    total = (material + machine) / a.molding_yield + tooling + finishing
    return {
        "process": f"injection, {tool}, {cavities} cav",
        "material": material,
        "machine": machine,
        "tooling": tooling,
        "finishing": finishing,
        "total": total,
        "cycle_s": cycle,
        "cooling_s": t_cool,
        "press_tonnes": tonnes,
        "feasible": True,
        "note": "",
    }


def benchtop(part: Part, volume: float, a: CostAssumptions) -> dict[str, Any]:
    shot = part.volume_m3 * (1.0 + a.runner_scrap_fraction)
    t_cool = cooling_time_s(part.thickest_wall_m, a)
    cycle = t_cool + a.fill_pack_s + a.benchtop_cycle_overhead_s
    rate = a.benchtop_price_usd / a.benchtop_life_h + 1.0 + a.labor_usd_h  # operator fully on the press
    material = part.volume_m3 * a.pa6_cf30_density_kg_m3 * (1.0 + a.runner_scrap_fraction) * a.resin_usd_per_kg
    machine = cycle / 3600.0 * rate
    tooling = _amortised_tool(_tool_cost(part, 1, "aluminum", a, base=1000.0), a.tool_life_shots["aluminum"], volume, 1)
    finishing = _finishing(volume, a)
    feasible = shot <= a.benchtop_shot_m3
    return {
        "process": "benchtop injection, aluminum",
        "material": material,
        "machine": machine,
        "tooling": tooling,
        "finishing": finishing,
        "total": (material + machine) / a.molding_yield + tooling + finishing,
        "cycle_s": cycle,
        "cooling_s": t_cool,
        "press_tonnes": math.nan,
        "feasible": feasible,
        "note": "" if feasible else f"shot {shot * 1e6:.0f} cm^3 > {a.benchtop_shot_m3 * 1e6:.0f} cm^3",
    }


def fdm(part: Part, volume: float, a: CostAssumptions) -> dict[str, Any]:
    hours = part.volume_m3 * 1e6 / a.deposition_cm3_h
    machine = hours * (a.printer_price_usd / a.printer_life_h + a.printer_running_usd_h)
    material = part.volume_m3 * 1.20e3 * 1.10 * a.filament_usd_per_kg  # PA6-CF filament ~1.2 g/cm^3, 10% supports/purge
    labor = a.print_operator_min / 60.0 * a.labor_usd_h
    finishing = _finishing(volume, a)
    return {
        "process": "FDM PA6-CF",
        "material": material,
        "machine": machine + labor,
        "tooling": 0.0,
        "finishing": finishing,
        "total": material + machine + labor + finishing,
        "cycle_s": hours * 3600.0,
        "cooling_s": math.nan,
        "press_tonnes": math.nan,
        "feasible": True,
        "note": "layered: weak between layers; thin TE limited by the nozzle",
    }


def sls(part: Part, volume: float, a: CostAssumptions) -> dict[str, Any]:
    material = part.volume_m3 * 1e6 * a.sls_usd_per_cm3 + a.sls_setup_usd_per_order / a.sls_parts_per_order
    finishing = _finishing(volume, a)
    return {
        "process": "SLS/MJF PA12 service",
        "material": material,
        "machine": 0.0,
        "tooling": 0.0,
        "finishing": finishing,
        "total": material + finishing,
        "cycle_s": math.nan,
        "cooling_s": math.nan,
        "press_tonnes": math.nan,
        "feasible": True,
        "note": "PA12 E ~1.7 GPa vs 12.75 GPa: structure must be re-checked (prototype use)",
    }


def forged_carbon(part: Part, volume: float, a: CostAssumptions) -> dict[str, Any]:
    material = part.volume_m3 * a.forged_density_kg_m3 * 1.08 * a.forged_material_usd_per_kg  # 8% flash
    cycle = a.forged_cure_s + a.forged_handling_s
    machine = cycle / 3600.0 * (a.forged_press_rate_usd_h + a.labor_usd_h)
    tool = a.forged_tool_usd * math.sqrt(max(part.projected_area_m2, 1e-6) / a.tool_reference_area_m2)
    tooling = _amortised_tool(tool, a.forged_tool_life, volume, 1)
    deflash = a.forged_deflash_min / 60.0 * a.labor_usd_h
    finishing = _finishing(volume, a)
    return {
        "process": "forged-carbon compression",
        "material": material,
        "machine": machine + deflash,
        "tooling": tooling,
        "finishing": finishing,
        "total": (material + machine + deflash) / a.forged_yield + tooling + finishing,
        "cycle_s": cycle,
        "cooling_s": math.nan,
        "press_tonnes": math.nan,
        "feasible": True,
        "note": "stiffer and stronger than PA6-CF30 (E ~30-40 GPa): allows thinner blades",
    }


def injection_options(volume: float) -> list[tuple[str, int]]:
    """Tool class and cavity count a molder would pick for an annual volume."""

    if volume <= 2.0e3:
        return [("aluminum", 1)]
    if volume <= 3.0e4:
        return [("aluminum", 1), ("P20", 1), ("P20", 2)]
    if volume <= 3.0e5:
        return [("P20", 2), ("hardened", 2), ("hardened", 4)]
    return [("hardened", 4), ("hardened", 8)]


def part_cost(part: Part, volume: float, a: CostAssumptions) -> list[dict[str, Any]]:
    """Every route at one annual part volume; the injection entry is the cheapest sensible tool for that volume."""

    shots = [injection(part, volume, a, tool=t, cavities=c) for t, c in injection_options(volume)]
    return [
        min(shots, key=lambda r: r["total"]),
        benchtop(part, volume, a),
        fdm(part, volume, a),
        sls(part, volume, a),
        forged_carbon(part, volume, a),
    ]


def hub_cost(volume_props: float, a: CostAssumptions) -> dict[str, float]:
    """Folding hub per prop: two 2.5D CNC 6061 plates (outsourced job-shop model) plus COTS hardware.

    Per plate: setup $150 per batch of min(volume, 500), 6 min machining at $60/h, $2 stock; COTS: two
    dowels, two DU bushings, four M3 button heads, about $4.
    """

    batch = min(max(volume_props, 1.0), 500.0)
    plate = 150.0 / batch + 6.0 / 60.0 * 60.0 + 2.0
    return {
        "plates": 2.0 * plate,
        "cots": 4.0,
        "assembly": 3.0 / 60.0 * a.labor_usd_h,
        "total": 2.0 * plate + 4.0 + 3.0 / 60.0 * a.labor_usd_h,
    }


def per_prop(parts: list[Part], volume_props: float, a: CostAssumptions, *, folding_hub: bool) -> dict[str, dict[str, float]]:
    """Cost per prop by route at an annual prop volume (each part's volume = props x count per prop)."""

    out: dict[str, dict[str, float]] = {}
    for part in parts:
        for row in part_cost(part, volume_props * part.count_per_prop, a):
            key = row["process"].split(",")[0]
            entry = out.setdefault(key, {"total": 0.0, "feasible": True, "detail": row["process"], "note": row["note"]})
            entry["total"] += part.count_per_prop * row["total"]
            entry["feasible"] = entry["feasible"] and row["feasible"]
            for k in ("material", "machine", "tooling", "finishing"):
                entry[k] = entry.get(k, 0.0) + part.count_per_prop * row[k]
    if folding_hub:
        hub = hub_cost(volume_props, a)
        for entry in out.values():
            entry["total"] += hub["total"]
            entry["hub"] = hub["total"]
    return out
