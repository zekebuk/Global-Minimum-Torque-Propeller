"""Design-for-manufacture rules, unit-cost model and production tolerances.

Each DFM rule returns a penalty in [0, inf): 0 = comfortable, (0, 1) = harder / slower / lower
yield ("warn"), >= 1 = not producible with standard FPV-motor processes ("fail"). The rules
encode process limits of the usual small-outrunner route: laser-cut or stamped Si-steel
laminations, epoxy powder coat, needle (flyer) winding of tooth coils, sintered arc magnets
bonded into a steel ring, machined aluminium bell.

Thresholds are engineering rules of thumb, collected in ``DFM_LIMITS`` so they can be tuned to
a specific factory.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from bldc_motor.design import materials
from bldc_motor.design.model import DesignAnalysis

DFM_LIMITS = {
    "fill_comfortable": 0.38,  # bare-copper slot fill, needle winding
    "fill_max": 0.50,
    "needle_clearance_mm": 0.6,  # slot opening >= wire bundle + needle clearance
    "wire_max_mm": 0.8,  # stiffness / bend radius on small teeth
    "wire_min_mm": 0.15,  # breakage, handling
    "strands_comfortable": 3,
    "turns_min": 3,  # Kv quantisation: one turn = 1/N Kv step
    "turns_labour": 20,
    "airgap_min_mm": 0.25,  # runout + bearing play + coating tolerance stack
    "magnet_min_mm": 1.0,  # chipping / crash survival
    "tooth_min_mm": 1.2,  # laser / stamping feature, handling
    "yoke_min_mm": 0.8,
    "shoe_min_mm": 0.25,
    "magnets_max": 28,
}


@dataclass(frozen=True)
class RuleResult:
    rule: str
    value: float
    limit: str
    penalty: float

    @property
    def status(self) -> str:
        if self.penalty >= 1.0:
            return "FAIL"
        if self.penalty > 0.0:
            return "WARN"
        return "OK"


def _ramp(value: float, comfortable: float, limit: float) -> float:
    """0 at/below ``comfortable``, 1 at ``limit``, linear beyond (works for either direction)."""

    if comfortable == limit:
        return 0.0 if value <= comfortable else 1.0
    return max(0.0, (value - comfortable) / (limit - comfortable))


def dfm_rules(analysis: DesignAnalysis) -> list[RuleResult]:
    d = analysis.design
    i = analysis.internals
    lim = DFM_LIMITS
    wire_ins = i["wire_insulated_mm"]
    bundle = wire_ins * math.sqrt(d["strands"])  # strands-in-hand pass through the needle together
    needed_opening = bundle + lim["needle_clearance_mm"]
    rules = [
        RuleResult("slot_fill", i["fill_bare"], f"<= {lim['fill_comfortable']} ideal, {lim['fill_max']} max", _ramp(i["fill_bare"], lim["fill_comfortable"], lim["fill_max"])),
        RuleResult("needle_access", d["slot_opening_mm"], f">= {needed_opening:.2f} mm", _ramp(-d["slot_opening_mm"], -needed_opening, -needed_opening + 0.3)),
        RuleResult("wire_stiffness", i["wire_bare_mm"], f"<= {lim['wire_max_mm']} mm", _ramp(i["wire_bare_mm"], lim["wire_max_mm"] * 0.8, lim["wire_max_mm"])),
        RuleResult("wire_fragility", i["wire_bare_mm"], f">= {lim['wire_min_mm']} mm", _ramp(-i["wire_bare_mm"], -lim["wire_min_mm"] * 1.3, -lim["wire_min_mm"])),
        RuleResult("strands_in_hand", d["strands"], f"<= {lim['strands_comfortable']}", 0.25 * max(0, d["strands"] - lim["strands_comfortable"])),
        RuleResult("turn_count_low", d["turns"], f">= {lim['turns_min']}", _ramp(-d["turns"], -lim["turns_min"], -1.0)),
        RuleResult("turn_count_labour", d["turns"] * d["strands"], f"strand-turns <= {lim['turns_labour'] * 2}", 0.02 * max(0, d["turns"] * d["strands"] - 2 * lim["turns_labour"])),
        RuleResult("airgap", d["airgap_mm"], f">= {lim['airgap_min_mm']} mm", _ramp(-d["airgap_mm"], -lim["airgap_min_mm"], -lim["airgap_min_mm"] + 0.08)),
        RuleResult("magnet_thickness", d["magnet_mm"], f">= {lim['magnet_min_mm']} mm", _ramp(-d["magnet_mm"], -lim["magnet_min_mm"], -lim["magnet_min_mm"] + 0.35)),
        RuleResult("tooth_width", i["tooth_width_mm"], f">= {lim['tooth_min_mm']} mm", _ramp(-i["tooth_width_mm"], -lim["tooth_min_mm"], -lim["tooth_min_mm"] + 0.4)),
        RuleResult("yoke_thickness", i["yoke_mm"], f">= {lim['yoke_min_mm']} mm", _ramp(-i["yoke_mm"], -lim["yoke_min_mm"], -lim["yoke_min_mm"] + 0.3)),
        RuleResult("shoe_overhang", analysis.geometry.shoe_overhang * 1e3, f">= {lim['shoe_min_mm']} mm", _ramp(-analysis.geometry.shoe_overhang * 1e3, -lim["shoe_min_mm"], 0.0)),
        RuleResult("magnet_count", i["magnet_count"], f"<= {lim['magnets_max']}", 0.1 * max(0, i["magnet_count"] - lim["magnets_max"])),
        RuleResult("unbalanced_pull", float(analysis.layout.periodicity), "gcd(Q,2p) >= 2", 0.5 if analysis.layout.unbalanced_magnetic_pull else 0.0),
        RuleResult("delta_circulating", 1.0 if d["connection"] == "delta" else 0.0, "wye preferred (no 3rd-harmonic loop)", 0.15 if d["connection"] == "delta" else 0.0),
    ]  # fmt: skip
    return rules


def manufacturability_score(rules: list[RuleResult]) -> float:
    """exp(-sum of penalties): 1 = no concerns, ~0.37 = one hard failure-equivalent."""

    return float(math.exp(-sum(r.penalty for r in rules)))


def unit_cost_usd(analysis: DesignAnalysis) -> dict:
    """Illustrative bill-of-materials + process cost breakdown (USD, ~1k pcs)."""

    d = analysis.design
    i = analysis.internals
    c = materials.COST
    grade = next(g for g in materials.MAGNET_GRADES if g.name == d["magnet_grade"])
    lam = next(m for m in materials.LAMINATIONS if abs(m.thickness_mm - d["lamination_mm"]) < 1e-9)
    slots, poles = d["slot_pole"]
    breakdown = {
        "magnets": i["mass_magnets_g"] / 1000.0 * grade.cost_usd_per_kg + poles * c["magnet_handling_usd_per_magnet"],
        "laminations": i["mass_stator_iron_g"] / 1000.0 / c["lamination_utilization"] * lam.cost_usd_per_kg
        + i["lamination_count"] * lam.stamping_usd_per_lamination,
        "copper": i["mass_copper_g"] / 1000.0 * c["copper_usd_per_kg"],
        "winding_labour": slots * (d["turns"] * c["winding_usd_per_turn"] + d["turns"] * d["strands"] * c["winding_usd_per_strand_turn"] + c["winding_usd_per_coil"]),
        "bell": c["bell_machining_usd"] + (i["mass_bell_al_g"] / 2.7) * c["bell_usd_per_cm3_al"] + i["mass_bell_steel_g"] / 1000.0 * 3.0,
        "bearings_shaft": c["bearings_shaft_usd"],
        "assembly_test": c["assembly_balance_test_usd"] + (c["delta_termination_usd"] if d["connection"] == "delta" else 0.0),
    }  # fmt: skip
    breakdown["total"] = float(sum(breakdown.values()))
    return breakdown


# ---- production tolerances (1-sigma) -------------------------------------------------
TOLERANCES = {
    "airgap_mm": 0.03,  # bell runout + bearing play
    "magnet_mm": 0.03,
    "stack_mm": 0.08,
    "stator_od_mm": 0.02,
    "br_fraction": 0.015,  # magnet remanence lot variation
    "resistance_fraction": 0.02,  # wire diameter tolerance + termination
}


def perturbed_designs(design: dict, n: int, rng: np.random.Generator) -> list[tuple[dict, dict]]:
    """Monte Carlo samples of an as-built design: (geometry-perturbed design, material factors)."""

    samples = []
    for _ in range(n):
        built = dict(design)
        for key in ("airgap_mm", "magnet_mm", "stack_mm", "stator_od_mm"):
            built[key] = max(design[key] + rng.normal(0.0, TOLERANCES[key]), 0.05)
        factors = {
            "kv_factor": 1.0 / (1.0 + rng.normal(0.0, TOLERANCES["br_fraction"])),
            "resistance_factor": 1.0 + rng.normal(0.0, TOLERANCES["resistance_fraction"]),
        }
        samples.append((built, factors))
    return samples
