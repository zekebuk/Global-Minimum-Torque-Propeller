"""Material, wire and cost data for the analytical motor design model.

Physical constants are textbook values. Magnet grades use typical datasheet minima for
sintered NdFeB. Cost coefficients are ILLUSTRATIVE order-of-magnitude placeholders for
comparing designs against each other; they are not quotes. Replace them with supplier data
before making sourcing decisions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MU0 = 4.0e-7 * math.pi

# Copper
COPPER_RESISTIVITY_20C_OHM_M = 1.724e-8
COPPER_DENSITY_KG_M3 = 8960.0
COPPER_CP_J_KG_K = 385.0

# Steels / aluminium
STEEL_DENSITY_KG_M3 = 7650.0  # silicon electrical steel
STEEL_CP_J_KG_K = 460.0
BELL_STEEL_DENSITY_KG_M3 = 7850.0  # low-carbon steel back-iron ring
ALUMINIUM_DENSITY_KG_M3 = 2700.0
ALUMINIUM_CP_J_KG_K = 900.0
NDFEB_DENSITY_KG_M3 = 7500.0
NDFEB_CP_J_KG_K = 440.0

# Saturation knees used to limit flux densities (first-order saturation model)
B_SAT_LAMINATION_T = 1.75
B_SAT_BELL_T = 1.6

# Insulation / thermal
SLOT_LINER_THICKNESS_M = 0.2e-3  # epoxy powder coat on the stator
SLOT_LINER_CONDUCTIVITY_W_MK = 0.35  # coat + varnish-impregnated winding contact
# Effective convection coefficient of a vented FPV bell at 40 m/s tip speed, referred to
# twice the external bell area (internal through-flow). Calibrated to the size-class library
# values; replace with a measured value (bldc-motor fit-thermal) via design calibration.
CONVECTION_H_REF_W_M2K = 880.0
CONVECTION_V_REF_M_S = 40.0
CONVECTION_H_STILL_W_M2K = 15.0


@dataclass(frozen=True)
class MagnetGrade:
    name: str
    br_t: float  # remanence at 20 degC
    hcj_ka_m: float  # intrinsic coercivity at 20 degC
    mu_rec: float  # recoil permeability
    max_temp_c: float  # maximum operating temperature
    br_coeff_per_k: float
    hcj_coeff_per_k: float
    cost_usd_per_kg: float  # illustrative


MAGNET_GRADES: tuple[MagnetGrade, ...] = (
    MagnetGrade("N52", 1.43, 876.0, 1.05, 80.0, -0.0012, -0.0060, 55.0),
    MagnetGrade("N48H", 1.37, 1353.0, 1.05, 120.0, -0.0012, -0.0058, 65.0),
    MagnetGrade("N45SH", 1.33, 1592.0, 1.05, 150.0, -0.0011, -0.0055, 80.0),
    MagnetGrade("N42UH", 1.29, 1990.0, 1.05, 180.0, -0.0011, -0.0052, 105.0),
)


@dataclass(frozen=True)
class Lamination:
    name: str
    thickness_mm: float
    k_hyst: float  # W/(kg * Hz * T^2) Steinmetz hysteresis (beta = 2)
    resistivity_ohm_m: float
    cost_usd_per_kg: float  # illustrative, includes coating; thinner gauges cost more
    stamping_usd_per_lamination: float  # illustrative


LAMINATIONS: tuple[Lamination, ...] = (
    Lamination("0.10mm Si-steel", 0.10, 0.020, 0.52e-6, 12.0, 0.0030),
    Lamination("0.20mm Si-steel", 0.20, 0.018, 0.52e-6, 5.0, 0.0020),
    Lamination("0.35mm Si-steel", 0.35, 0.017, 0.50e-6, 2.5, 0.0018),
)
# Eddy losses in real laminations exceed the classical value (excess loss, non-sinusoidal tooth
# flux, PWM ripple, rotor/bell eddy). Lumped multiplier, calibrate against no-load tests.
IRON_LOSS_EXCESS_FACTOR = 3.0
STACKING_FACTOR = 0.95


def awg_diameter_mm(awg: int) -> float:
    """Bare copper diameter for an AWG gauge."""

    return 0.127 * 92.0 ** ((36 - awg) / 39.0)


def insulated_diameter_mm(awg: int) -> float:
    """Grade-2 (heavy build) magnet wire: insulation build ~ 0.016 + 0.035*d (mm)."""

    d = awg_diameter_mm(awg)
    return d + 0.016 + 0.035 * d


WIRE_AWG: tuple[int, ...] = tuple(range(18, 34))
SLOT_POLE_COMBOS: tuple[tuple[int, int], ...] = (
    (9, 12),
    (9, 8),
    (9, 10),
    (12, 10),
    (12, 14),
    (12, 16),
    (18, 16),
    (18, 20),
    (24, 22),
    (24, 28),
)
CONNECTIONS: tuple[str, ...] = ("wye", "delta")


# ---- illustrative unit-cost model (USD, ~1k pcs) -------------------------------------
COST = {
    "copper_usd_per_kg": 12.0,
    "lamination_utilization": 0.45,  # fraction of sheet ending up in the part
    "winding_usd_per_turn": 0.0006,
    "winding_usd_per_strand_turn": 0.0002,
    "winding_usd_per_coil": 0.03,
    "magnet_handling_usd_per_magnet": 0.02,
    "bell_machining_usd": 1.20,
    "bell_usd_per_cm3_al": 0.08,
    "bearings_shaft_usd": 0.70,
    "assembly_balance_test_usd": 1.00,
    "delta_termination_usd": 0.05,
}
