"""Parametric outrunner design space and its encoding as a unit hypercube.

A design is a dict of physical parameters. The optimizers (RL policy, differential evolution,
random search) work on ``u`` in [0, 1]^D. Continuous parameters are mapped linearly; discrete
parameters are binned. ``snap`` rounds continuous parameters to manufacturable increments.

Cross-section (radial, outrunner, from shaft outward):
    bore (bearing tube) | yoke | teeth + slots | tooth shoe | airgap | magnets | bell back-iron
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from bldc_motor.design import materials

TOOTH_TIP_HEIGHT_MM = 0.45  # shoe thickness at the airgap
TOOTH_TIP_TAPER_MM = 0.35  # shoe-to-tooth transition


@dataclass(frozen=True)
class Parameter:
    name: str
    low: float = 0.0
    high: float = 1.0
    choices: tuple | None = None
    snap: float | None = None
    unit: str = ""
    description: str = ""

    @property
    def discrete(self) -> bool:
        return self.choices is not None

    def decode(self, u: float):
        u = min(max(float(u), 0.0), 1.0)
        if self.choices is not None:
            index = min(int(u * len(self.choices)), len(self.choices) - 1)
            return self.choices[index]
        return self.low + u * (self.high - self.low)

    def encode(self, value) -> float:
        if self.choices is not None:
            index = self.choices.index(value)
            return (index + 0.5) / len(self.choices)
        return (float(value) - self.low) / (self.high - self.low)


PARAMETERS: tuple[Parameter, ...] = (
    Parameter("stator_od_mm", 12.0, 36.0, snap=0.5, unit="mm", description="stator outer diameter"),
    Parameter("stack_mm", 3.0, 14.0, snap=0.5, unit="mm", description="lamination stack length"),
    Parameter("inner_ratio", 0.25, 0.55, snap=0.01, description="stator ID / OD"),
    Parameter("yoke_frac", 0.04, 0.20, snap=0.005, description="yoke thickness / stator radius"),
    Parameter("tooth_frac", 0.30, 0.75, snap=0.01, description="tooth width / slot pitch"),
    Parameter("slot_opening_mm", 0.6, 3.0, snap=0.1, unit="mm", description="slot opening between shoes"),
    Parameter("airgap_mm", 0.15, 0.80, snap=0.05, unit="mm", description="mechanical airgap"),
    Parameter("magnet_mm", 0.6, 3.0, snap=0.1, unit="mm", description="magnet radial thickness"),
    Parameter("magnet_arc", 0.60, 0.95, snap=0.01, description="magnet arc / pole pitch"),
    Parameter("bell_mm", 0.5, 2.5, snap=0.1, unit="mm", description="bell steel back-iron thickness"),
    Parameter("slot_pole", choices=materials.SLOT_POLE_COMBOS, description="slots, poles"),
    Parameter("turns", choices=tuple(range(1, 31)), description="turns per tooth coil"),
    Parameter("awg", choices=materials.WIRE_AWG, description="magnet wire gauge"),
    Parameter("strands", choices=(1, 2, 3, 4, 5, 6), description="strands in hand"),
    Parameter("connection", choices=materials.CONNECTIONS, description="phase connection"),
    Parameter("magnet_grade", choices=tuple(g.name for g in materials.MAGNET_GRADES), description="NdFeB grade"),
    Parameter(
        "lamination_mm",
        choices=tuple(lam.thickness_mm for lam in materials.LAMINATIONS),
        unit="mm",
        description="lamination thickness",
    ),
)
PARAMETER_NAMES = tuple(p.name for p in PARAMETERS)
DIMENSION = len(PARAMETERS)


def decode(u: np.ndarray) -> dict:
    return {p.name: p.decode(x) for p, x in zip(PARAMETERS, np.asarray(u, dtype=float), strict=True)}


def encode(design: dict) -> np.ndarray:
    return np.array([p.encode(design[p.name]) for p in PARAMETERS], dtype=float)


def snap(design: dict) -> dict:
    """Round continuous parameters to manufacturable increments (within bounds)."""

    out = dict(design)
    for p in PARAMETERS:
        if p.snap and not p.discrete:
            value = round(out[p.name] / p.snap) * p.snap
            out[p.name] = float(min(max(value, p.low), p.high))
    return out


def reference_design() -> dict:
    """A conventional 2207-class 12N14P starting point (not an optimum)."""

    return {
        "stator_od_mm": 22.0,
        "stack_mm": 7.0,
        "inner_ratio": 0.42,
        "yoke_frac": 0.10,
        "tooth_frac": 0.50,
        "slot_opening_mm": 1.5,
        "airgap_mm": 0.30,
        "magnet_mm": 1.3,
        "magnet_arc": 0.85,
        "bell_mm": 1.0,
        "slot_pole": (12, 14),
        "turns": 7,
        "awg": 25,
        "strands": 2,
        "connection": "wye",
        "magnet_grade": "N48H",
        "lamination_mm": 0.20,
    }


@dataclass(frozen=True)
class Geometry:
    """Derived dimensions in metres (SI) plus validity violations (mm-scale, >0 = violated)."""

    r_s: float  # stator outer radius
    r_i: float  # stator inner (bore) radius
    r1: float  # slot bottom radius (yoke outer)
    r2: float  # slot top radius (under shoe)
    h_y: float
    w_t: float  # tooth width
    slot_pitch: float  # at airgap
    slot_opening: float
    slot_area: float  # per slot, m^2
    slot_root_width: float
    shoe_overhang: float
    airgap: float
    r_m_in: float  # magnet inner radius
    r_m_out: float
    r_bell_out: float
    stack: float
    violations: dict


def derive(design: dict) -> Geometry:
    mm = 1.0e-3
    slots, _poles = design["slot_pole"]
    r_s = design["stator_od_mm"] / 2.0 * mm
    r_i = design["inner_ratio"] * r_s
    h_y = design["yoke_frac"] * r_s
    r1 = r_i + h_y
    r2 = r_s - (TOOTH_TIP_HEIGHT_MM + TOOTH_TIP_TAPER_MM) * mm
    slot_pitch = 2.0 * math.pi * r_s / slots
    w_t = design["tooth_frac"] * slot_pitch
    slot_opening = design["slot_opening_mm"] * mm
    slot_area = (math.pi * (r2**2 - r1**2) - slots * w_t * (r2 - r1)) / slots
    slot_root_width = 2.0 * math.pi * r1 / slots - w_t
    shoe_overhang = (slot_pitch - slot_opening - w_t) / 2.0
    airgap = design["airgap_mm"] * mm
    r_m_in = r_s + airgap
    r_m_out = r_m_in + design["magnet_mm"] * mm
    r_bell_out = r_m_out + design["bell_mm"] * mm
    violations = {
        "slot_depth": max(0.0, 1.0 - (r2 - r1) / mm),  # need >= 1 mm of slot depth
        "slot_root": max(0.0, 0.4 - slot_root_width / mm),  # slot bottom must be open
        "shoe": max(0.0, -shoe_overhang / mm),  # opening wider than the inter-tooth gap
        "bore": max(0.0, 4.0 - 2.0 * r_i / mm),  # bearing tube needs >= 4 mm ID
    }
    return Geometry(
        r_s, r_i, r1, r2, h_y, w_t, slot_pitch, slot_opening, max(slot_area, 0.0), slot_root_width,
        shoe_overhang, airgap, r_m_in, r_m_out, r_bell_out, design["stack_mm"] * mm, violations,
    )  # fmt: skip
