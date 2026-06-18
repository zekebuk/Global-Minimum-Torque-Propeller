from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


INCH_TO_METER = 0.0254


@dataclass(frozen=True)
class PolarLookup:
    cl: float
    cd: float
    cm: float | None
    polar_Re_used: str
    polar_source: str
    alpha_outside_range: bool


def hover_induced_velocity(thrust_n: float, rho_kg_m3: float, radius_m: float) -> float:
    disk_area = math.pi * radius_m**2
    return math.sqrt(thrust_n / (2.0 * rho_kg_m3 * disk_area))


def load_polar_database(root: Path) -> dict[str, dict[float, dict[str, Any]]]:
    tables: dict[str, dict[float, dict[str, Any]]] = {}
    for path in sorted(root.rglob("*.csv")):
        for airfoil, re_value, rows in load_polar_csv(path):
            tables.setdefault(airfoil, {})[re_value] = {
                "rows": rows,
                "source": str(path),
                "alpha_min": rows[0]["alpha_deg"],
                "alpha_max": rows[-1]["alpha_deg"],
            }
    return tables


def load_polar_csv(path: Path) -> list[tuple[str, float, list[dict[str, float | None]]]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return []
            rows = list(reader)
    except UnicodeDecodeError:
        return []

    lookup = column_lookup(reader.fieldnames)
    alpha_col = optional_column(lookup, ("alpha_deg", "alpha"))
    cl_col = optional_column(lookup, ("Cl", "CL", "cl"))
    cd_col = optional_column(lookup, ("Cd", "CD", "cd"))
    if alpha_col is None or cl_col is None or cd_col is None:
        return []

    airfoil_col = optional_column(lookup, ("airfoil_name", "airfoil"))
    re_col = optional_column(lookup, ("Re", "re", "reynolds", "reynolds_number"))
    cm_col = optional_column(lookup, ("Cm", "CM", "cm"))
    converged_col = optional_column(lookup, ("converged", "valid"))

    grouped: dict[tuple[str, float], list[dict[str, float | None]]] = {}
    for row in rows:
        if converged_col and str(row.get(converged_col, "")).strip().lower() not in {"true", "1", "yes", "y"}:
            continue
        try:
            alpha = float(row[alpha_col])
            cl = float(row[cl_col])
            cd = float(row[cd_col])
            re_value = float(row[re_col]) if re_col and str(row.get(re_col, "")).strip() else math.nan
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(alpha) and math.isfinite(cl) and math.isfinite(cd) and math.isfinite(re_value)):
            continue
        cm = None
        if cm_col and str(row.get(cm_col, "")).strip():
            try:
                cm_candidate = float(row[cm_col])
                cm = cm_candidate if math.isfinite(cm_candidate) else None
            except ValueError:
                cm = None
        airfoil = normalize_airfoil(row[airfoil_col]) if airfoil_col else normalize_airfoil(path.stem.split("_Re")[0])
        grouped.setdefault((airfoil, re_value), []).append({"alpha_deg": alpha, "Cl": cl, "Cd": cd, "Cm": cm})

    loaded: list[tuple[str, float, list[dict[str, float | None]]]] = []
    for (airfoil, re_value), values in sorted(grouped.items()):
        sorted_values = sorted(values, key=lambda item: float(item["alpha_deg"]))
        if sorted_values:
            loaded.append((airfoil, re_value, sorted_values))
    return loaded


def lookup_polar(
    database: dict[str, dict[float, dict[str, Any]]],
    airfoil_name: str,
    reynolds_number: float,
    alpha_deg: float,
) -> PolarLookup:
    airfoil = normalize_airfoil(airfoil_name)
    if airfoil not in database:
        available = ", ".join(sorted(database))
        raise ValueError(f"No polar table found for airfoil {airfoil_name!r}. Available: {available}")

    re_tables = database[airfoil]
    re_values = sorted(re_tables)
    if reynolds_number <= re_values[0]:
        table = re_tables[re_values[0]]
        cl, cd, cm, alpha_outside = interpolate_alpha(table["rows"], alpha_deg)
        return PolarLookup(cl, cd, cm, f"{re_values[0]:g}", table["source"], alpha_outside)
    if reynolds_number >= re_values[-1]:
        table = re_tables[re_values[-1]]
        cl, cd, cm, alpha_outside = interpolate_alpha(table["rows"], alpha_deg)
        return PolarLookup(cl, cd, cm, f"{re_values[-1]:g}", table["source"], alpha_outside)

    lower = re_values[0]
    upper = re_values[-1]
    for index, re_value in enumerate(re_values[1:], start=1):
        if reynolds_number <= re_value:
            lower = re_values[index - 1]
            upper = re_value
            break

    lower_table = re_tables[lower]
    upper_table = re_tables[upper]
    lower_cl, lower_cd, lower_cm, lower_outside = interpolate_alpha(lower_table["rows"], alpha_deg)
    upper_cl, upper_cd, upper_cm, upper_outside = interpolate_alpha(upper_table["rows"], alpha_deg)
    fraction = (reynolds_number - lower) / max(upper - lower, 1.0e-12)
    cm = None
    if lower_cm is not None and upper_cm is not None:
        cm = lower_cm + fraction * (upper_cm - lower_cm)
    return PolarLookup(
        cl=lower_cl + fraction * (upper_cl - lower_cl),
        cd=lower_cd + fraction * (upper_cd - lower_cd),
        cm=cm,
        polar_Re_used=f"{lower:g}-{upper:g}",
        polar_source=f"{lower_table['source']};{upper_table['source']}",
        alpha_outside_range=lower_outside or upper_outside,
    )


def polar_alpha_bounds(database: dict[str, dict[float, dict[str, Any]]], airfoil_name: str) -> tuple[float, float]:
    airfoil = normalize_airfoil(airfoil_name)
    tables = database.get(airfoil)
    if not tables:
        raise ValueError(f"No polar tables for airfoil {airfoil_name}")
    alpha_min = max(float(table["alpha_min"]) for table in tables.values())
    alpha_max = min(float(table["alpha_max"]) for table in tables.values())
    return alpha_min, alpha_max


def interpolate_alpha(rows: list[dict[str, float | None]], alpha_deg: float) -> tuple[float, float, float | None, bool]:
    alpha_values = [float(row["alpha_deg"]) for row in rows]
    clipped = alpha_deg < alpha_values[0] or alpha_deg > alpha_values[-1]
    if alpha_deg <= alpha_values[0]:
        row = rows[0]
        return float(row["Cl"]), float(row["Cd"]), none_or_float(row["Cm"]), clipped
    if alpha_deg >= alpha_values[-1]:
        row = rows[-1]
        return float(row["Cl"]), float(row["Cd"]), none_or_float(row["Cm"]), clipped

    for index in range(1, len(rows)):
        if alpha_deg <= float(rows[index]["alpha_deg"]):
            left = rows[index - 1]
            right = rows[index]
            alpha_left = float(left["alpha_deg"])
            alpha_right = float(right["alpha_deg"])
            fraction = (alpha_deg - alpha_left) / max(alpha_right - alpha_left, 1.0e-12)
            cl = float(left["Cl"]) + fraction * (float(right["Cl"]) - float(left["Cl"]))
            cd = float(left["Cd"]) + fraction * (float(right["Cd"]) - float(left["Cd"]))
            left_cm = none_or_float(left["Cm"])
            right_cm = none_or_float(right["Cm"])
            cm = None
            if left_cm is not None and right_cm is not None:
                cm = left_cm + fraction * (right_cm - left_cm)
            return cl, cd, cm, clipped

    row = rows[-1]
    return float(row["Cl"]), float(row["Cd"]), none_or_float(row["Cm"]), clipped


def evaluate_station(
    *,
    case_label: str,
    r_R: float,
    radius_m: float,
    dr_m: float,
    chord_m: float,
    twist_deg: float,
    airfoil: str,
    rotor_radius_m: float,
    blade_count: int,
    rpm: float,
    rho_kg_m3: float,
    mu_air_kg_m_s: float,
    v_inf_m_s: float,
    induced_velocity_m_s: float,
    polar_database: dict[str, dict[float, dict[str, Any]]],
) -> dict[str, Any]:
    omega = rpm * 2.0 * math.pi / 60.0
    v_tan = omega * radius_m
    v_axial = v_inf_m_s + induced_velocity_m_s
    v_rel = math.hypot(v_tan, v_axial)
    phi_rad = math.atan2(v_axial, max(v_tan, 1.0e-12))
    phi = math.degrees(phi_rad)
    alpha = twist_deg - phi
    reynolds = rho_kg_m3 * v_rel * chord_m / mu_air_kg_m_s
    q = 0.5 * rho_kg_m3 * v_rel**2
    polar = lookup_polar(polar_database, airfoil, reynolds, alpha)
    d_l = q * chord_m * polar.cl * dr_m
    d_d = q * chord_m * polar.cd * dr_m
    d_t = blade_count * (d_l * math.cos(phi_rad) - d_d * math.sin(phi_rad))
    d_f_tangential = blade_count * (d_l * math.sin(phi_rad) + d_d * math.cos(phi_rad))
    d_q = radius_m * d_f_tangential
    return {
        "case_label": case_label,
        "r_R": r_R,
        "r_m": radius_m,
        "dr_m": dr_m,
        "chord_m": chord_m,
        "chord_in": chord_m / INCH_TO_METER,
        "chord_over_R": chord_m / rotor_radius_m,
        "twist_deg": twist_deg,
        "beta_final_deg": twist_deg,
        "airfoil": airfoil,
        "omega_rad_s": omega,
        "Vi_m_s": induced_velocity_m_s,
        "Vt_m_s": v_tan,
        "Va_m_s": v_axial,
        "Vrel_m_s": v_rel,
        "phi_deg": phi,
        "alpha_deg": alpha,
        "Re": reynolds,
        "polar_Re_used": polar.polar_Re_used,
        "polar_source": polar.polar_source,
        "Cl": polar.cl,
        "Cd": polar.cd,
        "Cm": "" if polar.cm is None else polar.cm,
        "alpha_outside_polar_range": polar.alpha_outside_range,
        "q_Pa": q,
        "dL_N": d_l,
        "dD_N": d_d,
        "dT_N": d_t,
        "dQ_Nm": d_q,
        "qz_N_per_m": d_t / dr_m,
        "qy_tangential_N_per_m": d_f_tangential / dr_m,
        "torque_loading_Nm_per_m": d_q / dr_m,
    }


def column_lookup(columns: Iterable[str]) -> dict[str, str]:
    return {column_key(column): column for column in columns}


def column_key(column: str) -> str:
    return column.strip().lower().replace(" ", "").replace("_", "")


def optional_column(lookup: dict[str, str], candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        key = column_key(candidate)
        if key in lookup:
            return lookup[key]
    return None


def normalize_airfoil(name: Any) -> str:
    raw = str(name).strip()
    compact = raw.replace(" ", "").replace("_", "").replace("-", "").upper()
    if compact in {"MH115", "S4310", "A3", "A4", "A6", "A7", "A11", "A13"}:
        return compact
    return compact


def none_or_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
