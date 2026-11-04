"""Unit conversions and physical constants for the BLDC motor toolkit.

All internal computation is SI: V, A, ohm, rad/s, N, N*m, W, K/degC, s.
Human-facing quantities (rpm, Kv in rpm/V, grams, mAh) are converted at the edges.
"""

from __future__ import annotations

import math
import re

TWO_PI = 2.0 * math.pi
STANDARD_GRAVITY_M_S2 = 9.80665
SEA_LEVEL_AIR_DENSITY_KG_M3 = 1.225
INCH_TO_M = 0.0254

# Annealed copper, IEC 60028: alpha at 20 degC.
COPPER_TEMP_COEFF_PER_K = 0.00393
# Reversible remanence coefficient of sintered NdFeB (typ. -0.10 ... -0.13 %/K).
NDFEB_BR_TEMP_COEFF_PER_K = -0.0012


def rpm_to_rad_s(rpm: float) -> float:
    return float(rpm) * TWO_PI / 60.0


def rad_s_to_rpm(omega: float) -> float:
    return float(omega) * 60.0 / TWO_PI


def kv_to_ke(kv_rpm_per_v: float) -> float:
    """Convert Kv [rpm/V] to the back-EMF constant Ke [V*s/rad].

    In consistent SI units the torque constant Kt [N*m/A] equals Ke, so this is also Kt
    for the DC-equivalent model.
    """

    if kv_rpm_per_v <= 0.0:
        raise ValueError("Kv must be positive")
    return 60.0 / (TWO_PI * float(kv_rpm_per_v))


def ke_to_kv(ke_v_s_per_rad: float) -> float:
    if ke_v_s_per_rad <= 0.0:
        raise ValueError("Ke must be positive")
    return 60.0 / (TWO_PI * float(ke_v_s_per_rad))


def grams_force_to_n(grams: float) -> float:
    return float(grams) * STANDARD_GRAVITY_M_S2 / 1000.0


def n_to_grams_force(newtons: float) -> float:
    return float(newtons) * 1000.0 / STANDARD_GRAVITY_M_S2


# --------------------------------------------------------------------------------------
# Header-unit conversion used by the thrust-stand importer.
# Each table maps a normalized unit token -> (scale, offset) so that SI = raw*scale + offset.
# --------------------------------------------------------------------------------------

_LBF_N = 4.4482216152605
_OZF_N = 0.27801385095378125

UNIT_TABLES: dict[str, dict[str, tuple[float, float]]] = {
    "force_n": {
        "": (1.0, 0.0),
        "n": (1.0, 0.0),
        "kn": (1000.0, 0.0),
        "kgf": (STANDARD_GRAVITY_M_S2, 0.0),
        "kg": (STANDARD_GRAVITY_M_S2, 0.0),
        "gf": (STANDARD_GRAVITY_M_S2 / 1000.0, 0.0),
        "g": (STANDARD_GRAVITY_M_S2 / 1000.0, 0.0),
        "lbf": (_LBF_N, 0.0),
        "lb": (_LBF_N, 0.0),
        "ozf": (_OZF_N, 0.0),
        "oz": (_OZF_N, 0.0),
    },
    "torque_nm": {
        "": (1.0, 0.0),
        "nm": (1.0, 0.0),
        "mnm": (1.0e-3, 0.0),
        "nmm": (1.0e-3, 0.0),
        "ncm": (1.0e-2, 0.0),
        "kgfm": (STANDARD_GRAVITY_M_S2, 0.0),
        "kgfcm": (STANDARD_GRAVITY_M_S2 / 100.0, 0.0),
        "gfcm": (STANDARD_GRAVITY_M_S2 / 1.0e5, 0.0),
        "ozfin": (_OZF_N * INCH_TO_M, 0.0),
        "ozin": (_OZF_N * INCH_TO_M, 0.0),
        "lbfin": (_LBF_N * INCH_TO_M, 0.0),
        "lbin": (_LBF_N * INCH_TO_M, 0.0),
        "lbfft": (_LBF_N * 0.3048, 0.0),
    },
    "voltage_v": {"": (1.0, 0.0), "v": (1.0, 0.0), "mv": (1.0e-3, 0.0)},
    "current_a": {"": (1.0, 0.0), "a": (1.0, 0.0), "ma": (1.0e-3, 0.0)},
    "time_s": {
        "": (1.0, 0.0),
        "s": (1.0, 0.0),
        "sec": (1.0, 0.0),
        "ms": (1.0e-3, 0.0),
        "us": (1.0e-6, 0.0),
        "min": (60.0, 0.0),
    },
    "speed_rpm": {
        "": (1.0, 0.0),
        "rpm": (1.0, 0.0),
        "krpm": (1000.0, 0.0),
        "rads": (60.0 / TWO_PI, 0.0),
        "hz": (60.0, 0.0),
        "rps": (60.0, 0.0),
    },
    "temperature_c": {
        "": (1.0, 0.0),
        "c": (1.0, 0.0),
        "degc": (1.0, 0.0),
        "k": (1.0, -273.15),
        "f": (5.0 / 9.0, -32.0 * 5.0 / 9.0),
        "degf": (5.0 / 9.0, -32.0 * 5.0 / 9.0),
    },
    "signal_us": {"": (1.0, 0.0), "us": (1.0, 0.0), "ms": (1000.0, 0.0)},
    "fraction": {"": (1.0, 0.0), "%": (0.01, 0.0), "pct": (0.01, 0.0), "percent": (0.01, 0.0)},
    "power_w": {"": (1.0, 0.0), "w": (1.0, 0.0), "kw": (1000.0, 0.0)},
}


def normalize_unit_token(unit: str) -> str:
    """Normalize a unit string such as ``'N·m'``, ``'µs'`` or ``'°C'`` to a lookup token."""

    token = unit.strip().lower()
    token = token.replace("µ", "u").replace("μ", "u").replace("°", "deg")
    token = re.sub(r"[\s·*.\-/^_]", "", token)
    if token in {"degc", "degreesc", "celsius"}:
        return "degc"
    if token in {"degf", "fahrenheit"}:
        return "degf"
    if token in {"kelvin"}:
        return "k"
    return token


def to_si(values, unit: str, quantity: str):
    """Convert raw values (scalar or numpy array) with ``unit`` into the SI unit of ``quantity``."""

    table = UNIT_TABLES[quantity]
    token = normalize_unit_token(unit)
    if token not in table:
        raise ValueError(f"Unsupported unit '{unit}' for quantity '{quantity}'. Known: {sorted(k for k in table if k)}")
    scale, offset = table[token]
    return values * scale + offset
