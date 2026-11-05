"""Static rotor relations (momentum theory + constant CT/CP) used to set loads and excitation bands."""

from __future__ import annotations

import math

from .config import G


def rev_per_s_for_thrust(thrust_N: float, prop: dict) -> float:
    D = prop["prop_diameter"] / 1000.0
    return math.sqrt(thrust_N / (prop["ct_static"] * prop["air_density"] * D**4))


def torque_for_thrust(thrust_N: float, prop: dict) -> float:
    """Shaft torque in N*m: Q = CP/(2 pi) * rho n^2 D^5."""
    D = prop["prop_diameter"] / 1000.0
    n = rev_per_s_for_thrust(thrust_N, prop)
    return prop["cp_static"] / (2 * math.pi) * prop["air_density"] * n**2 * D**5


def induced_velocity(thrust_N: float, prop: dict) -> float:
    D = prop["prop_diameter"] / 1000.0
    A = math.pi * D**2 / 4
    return math.sqrt(thrust_N / (2 * prop["air_density"] * A))


def operating_points(auw_g: float, cfg: dict) -> dict:
    prop = cfg["propulsion"]
    loads = cfg["loads"]
    T_hover = auw_g * 1e-3 * G / 4.0
    T_max = prop["motor_max_thrust_N"]
    n_hover = rev_per_s_for_thrust(T_hover, prop)
    n_max = rev_per_s_for_thrust(T_max, prop)
    bp = prop["prop_blades"]
    n_cruise = rev_per_s_for_thrust(T_hover * loads["cruise_thrust_factor"], prop)
    m = loads["band_margin"]
    bands = [(k * n_hover / m, k * n_cruise * m) for k in loads["harmonics"]]
    bands.insert(0, (0.0, max(loads["min_modal_frequency_Hz"], bands[0][1])))  # nothing below the 1P band
    merged = []
    for lo, hi in sorted(bands):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    windows = [(merged[i][1], merged[i + 1][0]) for i in range(len(merged) - 1)] + [(merged[-1][1], float("inf"))]
    return {
        "auw_g": auw_g,
        "T_hover_N": T_hover,
        "T_max_N": T_max,
        "T_limit_N": T_max * loads["dynamic_factor"],
        "thrust_to_weight": 4 * T_max / (auw_g * 1e-3 * G),
        "rpm_hover": 60 * n_hover,
        "rpm_max": 60 * n_max,
        "f1P_hover_Hz": n_hover,
        "f1P_max_Hz": n_max,
        "fBP_hover_Hz": bp * n_hover,
        "fBP_max_Hz": bp * n_max,
        "Q_max_Nm": torque_for_thrust(T_max, prop),
        "v_induced_hover": induced_velocity(T_hover, prop),
        "f1P_cruise_Hz": n_cruise,
        "forbidden_bands_Hz": merged,
        "allowed_windows_Hz": windows,
    }


def modal_margin(f: float, op: dict) -> float:
    """Signed, normalised distance of f from the forbidden bands (>0 means allowed)."""
    best = -float("inf")
    for lo, hi in op["allowed_windows_Hz"]:
        if lo <= f <= hi:
            return min(f - lo, hi - f if hi != float("inf") else f) / f
        best = max(best, -min(abs(f - lo), abs(f - hi)) / f)
    return best
