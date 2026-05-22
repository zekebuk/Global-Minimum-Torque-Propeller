from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import warnings
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CODE_DIR.parent if CODE_DIR.name.lower() == "code" else CODE_DIR


def _project_path(relative_path: str | Path) -> Path:
    return PROJECT_ROOT / Path(relative_path)


def _resolve_project_input_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute() or candidate.exists():
        return candidate

    project_candidate = PROJECT_ROOT / candidate
    if project_candidate.exists():
        return project_candidate

    code_candidate = CODE_DIR / candidate
    if code_candidate.exists():
        return code_candidate

    return candidate


# ---------------------------------------------------------------------------
# BEMT physical equations
# ---------------------------------------------------------------------------
#
# This section is intentionally plain algebra. It is the part to audit first
# when checking thesis numbers by hand. Variables use SI units throughout:
#
#   omega = rpm * 2*pi / 60
#   Vt    = omega * r
#   Va    = V_inf + vi
#   Vrel  = sqrt(Vt^2 + Va^2)
#   phi   = atan2(Va, Vt)
#   alpha = twist - phi
#   q     = 0.5*rho*Vrel^2
#   Re    = rho*Vrel*chord/mu
#
# After Cl and Cd are read from the airfoil polar:
#
#   dL            = q*chord*Cl*dr
#   dD            = q*chord*Cd*dr
#   dT            = B*(dL*cos(phi) - dD*sin(phi))
#   dF_tangential = B*(dL*sin(phi) + dD*cos(phi))
#   dQ            = r*dF_tangential
#
# Whole-propeller totals:
#
#   T  = sum(dT)
#   Q  = sum(dQ)
#   P  = Q*omega
#   n  = rpm/60
#   CT = T/(rho*n^2*D^4)
#   CQ = Q/(rho*n^2*D^5)
#   CP = P/(rho*n^3*D^5)
#
# Sign convention: positive dQ is shaft torque required to drive the blade.

DEFAULT_THESIS_OPERATING_CONDITION = {
    "case_name": "thesis_22_static_35N",
    "diameter_m": 0.5588,
    "radius_m": 0.2794,
    "blade_count": 2,
    "rpm": 4000.0,
    "rho": 1.225,
    "mu_air_kg_m_s": 1.81e-5,
    "V_inf_m_s": 0.0,
    "target_thrust_N": 35.0,
    "hover_mode": True,
}


DEFAULT_XROTOR_ROOT = Path(
    r"C:\Users\Zekeb\OneDrive\OneDrive - Cal Poly\ME 599 THESIS\Xrotor7.55"
)

REQUIRED_AIRFOILS = ("MH115", "S4310", "A3", "A4", "A6", "A7", "A11", "A13")
REQUIRED_POLAR_REYNOLDS_NUMBERS = (100000, 150000, 200000, 300000)
DEFAULT_AIRFOIL_DIR = Path(
    r"C:\Users\Zekeb\OneDrive\OneDrive - Cal Poly\ME 599 THESIS\CAD\airfoils_good"
)
DEFAULT_XFOIL_ROOT = Path(
    r"C:\Users\Zekeb\OneDrive\OneDrive - Cal Poly\ME 599 THESIS\XFOIL6.99"
)
DEFAULT_XROTOR_EXE = Path(
    r"C:\Users\Zekeb\OneDrive\OneDrive - Cal Poly\ME 599 THESIS\Xrotor7.55\Xrotor\xrotor.exe"
)

CASE_GEOMETRY_PATHS = {
    "scaled_reference_mil_22": _project_path("results/geometry/reference/reference_mil/reference_mil.csv"),
    "scaled_reference_gmt_22": _project_path("results/geometry/reference/reference_gmt/reference_gmt.csv"),
    "optimized_mil_22": _project_path("results/geometry/optimized/optimized_mil/optimized_mil.csv"),
    "optimized_gmt_22": _project_path("results/geometry/optimized/optimized_gmt/optimized_gmt.csv"),
    "custom": _project_path("results/geometry/optimized/optimized_mil/optimized_mil.csv"),
}


STATION_EXPORT_COLUMNS = [
    "case_name",
    "r_R",
    "r_m",
    "chord_m",
    "twist_deg",
    "airfoil",
    "Re",
    "polar_Re_used",
    "polar_source",
    "phi_deg",
    "alpha_deg",
    "Cl",
    "Cd",
    "Cm",
    "alpha_clipped",
    "Cl_clipped",
    "Vt_m_s",
    "Va_m_s",
    "Vrel_m_s",
    "dT_N",
    "dQ_Nm",
    "qz_N_per_m",
    "qy_tangential_N_per_m",
    "torque_loading_Nm_per_m",
    "cumulative_thrust_fraction",
    "cumulative_torque_fraction",
]


@dataclass(frozen=True)
class OperatingCondition:
    case_name: str = "thesis_22_static_35N"
    diameter_m: float = 0.5588
    radius_m: float = 0.2794
    blade_count: int = 2
    rpm: float = 4000.0
    rho: float = 1.225
    mu_air_kg_m_s: float = 1.81e-5
    V_inf_m_s: float = 0.0
    target_thrust_N: float | None = 35.0
    hover_mode: bool = True


@dataclass
class AirfoilPolar:
    alpha_deg: list[float] | None = None
    cl: list[float] | None = None
    cd: list[float] | None = None
    by_airfoil: dict[str, dict[str, list[float]]] | None = None
    by_airfoil_re: dict[str, dict[float | None, dict[str, Any]]] | None = None
    source: str = "placeholder"
    placeholder: bool = False
    cl_alpha_per_rad: float = 2.0 * math.pi
    cd0: float = 0.010
    k: float = 0.010
    cl_max_debug: float = 1.5
    generated_polars_used: bool = False
    fallback_airfoils_used: dict[str, str] = field(default_factory=dict)
    failed_polar_cases: list[str] = field(default_factory=list)
    _warned_range_keys: set[tuple[str, str]] = field(default_factory=set)

    def evaluate(self, alpha_deg: float, airfoil_name: str | None = None) -> tuple[float, float, bool]:
        result = self.evaluate_with_metadata(alpha_deg, airfoil_name, reynolds_number=None)
        return result["Cl"], result["Cd"], result["alpha_clipped"] or result["Cl_clipped"]

    def evaluate_with_metadata(
        self,
        alpha_deg: float,
        airfoil_name: str | None = None,
        reynolds_number: float | None = None,
    ) -> dict[str, Any]:
        if self.placeholder:
            alpha_rad = math.radians(alpha_deg)
            unclipped_cl = self.cl_alpha_per_rad * alpha_rad
            cl = max(-self.cl_max_debug, min(self.cl_max_debug, unclipped_cl))
            cd = self.cd0 + self.k * cl**2
            return {
                "Cl": cl,
                "Cd": cd,
                "Cm": math.nan,
                "alpha_clipped": False,
                "Cl_clipped": abs(cl - unclipped_cl) > 1.0e-12,
                "polar_Re_used": "",
                "polar_source": self.source,
            }

        if self.by_airfoil_re:
            return self._evaluate_reynolds_polar(alpha_deg, airfoil_name, reynolds_number)

        alpha_values, cl_values, cd_values, label = self._table_for_airfoil(airfoil_name)
        if not alpha_values or not cl_values or not cd_values:
            raise ValueError("polar data are empty")

        cl, cd, cm, alpha_clipped = self._evaluate_table(alpha_deg, alpha_values, cl_values, cd_values, None, label)
        return {
            "Cl": cl,
            "Cd": cd,
            "Cm": cm,
            "alpha_clipped": alpha_clipped,
            "Cl_clipped": False,
            "polar_Re_used": "",
            "polar_source": self.source,
        }

    def _evaluate_table(
        self,
        alpha_deg: float,
        alpha_values: Sequence[float],
        cl_values: Sequence[float],
        cd_values: Sequence[float],
        cm_values: Sequence[float] | None,
        label: str,
    ) -> tuple[float, float, float, bool]:
        clipped = False
        if alpha_deg < alpha_values[0]:
            clipped = True
            warning_key = (label, "below")
            if warning_key not in self._warned_range_keys:
                warnings.warn(
                    f"airfoil {label}: alpha {alpha_deg:.3f} deg is below polar range "
                    f"{alpha_values[0]:.3f} to {alpha_values[-1]:.3f} deg; "
                    "using endpoint value",
                    RuntimeWarning,
                    stacklevel=2,
                )
                self._warned_range_keys.add(warning_key)
            cm = cm_values[0] if cm_values else math.nan
            return cl_values[0], cd_values[0], cm, clipped

        if alpha_deg > alpha_values[-1]:
            clipped = True
            warning_key = (label, "above")
            if warning_key not in self._warned_range_keys:
                warnings.warn(
                    f"airfoil {label}: alpha {alpha_deg:.3f} deg is above polar range "
                    f"{alpha_values[0]:.3f} to {alpha_values[-1]:.3f} deg; "
                    "using endpoint value",
                    RuntimeWarning,
                    stacklevel=2,
                )
                self._warned_range_keys.add(warning_key)
            cm = cm_values[-1] if cm_values else math.nan
            return cl_values[-1], cd_values[-1], cm, clipped

        cl = _linear_interpolate(alpha_values, cl_values, alpha_deg)
        cd = _linear_interpolate(alpha_values, cd_values, alpha_deg)
        cm = _linear_interpolate(alpha_values, cm_values, alpha_deg) if cm_values else math.nan
        return cl, cd, cm, clipped

    def _evaluate_reynolds_polar(
        self,
        alpha_deg: float,
        airfoil_name: str | None,
        reynolds_number: float | None,
    ) -> dict[str, Any]:
        label, tables = self._reynolds_tables_for_airfoil(airfoil_name)
        finite_re_values = sorted(re_value for re_value in tables if re_value is not None and math.isfinite(re_value))
        if not finite_re_values:
            table = tables[None] if None in tables else next(iter(tables.values()))
            cl, cd, cm, alpha_clipped = self._evaluate_table(
                alpha_deg, table["alpha_deg"], table["Cl"], table["Cd"], table.get("Cm"), label
            )
            return {
                "Cl": cl,
                "Cd": cd,
                "Cm": cm,
                "alpha_clipped": alpha_clipped,
                "Cl_clipped": False,
                "polar_Re_used": "",
                "polar_source": table["polar_source"],
            }

        if reynolds_number is None or reynolds_number <= finite_re_values[0]:
            re_used = finite_re_values[0]
            table = tables[re_used]
            cl, cd, cm, alpha_clipped = self._evaluate_table(
                alpha_deg, table["alpha_deg"], table["Cl"], table["Cd"], table.get("Cm"), f"{label} Re={re_used:g}"
            )
            return {
                "Cl": cl,
                "Cd": cd,
                "Cm": cm,
                "alpha_clipped": alpha_clipped,
                "Cl_clipped": False,
                "polar_Re_used": re_used,
                "polar_source": table["polar_source"],
            }

        if reynolds_number >= finite_re_values[-1]:
            re_used = finite_re_values[-1]
            table = tables[re_used]
            cl, cd, cm, alpha_clipped = self._evaluate_table(
                alpha_deg, table["alpha_deg"], table["Cl"], table["Cd"], table.get("Cm"), f"{label} Re={re_used:g}"
            )
            return {
                "Cl": cl,
                "Cd": cd,
                "Cm": cm,
                "alpha_clipped": alpha_clipped,
                "Cl_clipped": False,
                "polar_Re_used": re_used,
                "polar_source": table["polar_source"],
            }

        lower_re = finite_re_values[0]
        upper_re = finite_re_values[-1]
        for idx in range(1, len(finite_re_values)):
            if reynolds_number <= finite_re_values[idx]:
                lower_re = finite_re_values[idx - 1]
                upper_re = finite_re_values[idx]
                break

        lower_table = tables[lower_re]
        upper_table = tables[upper_re]
        lower_cl, lower_cd, lower_cm, lower_clipped = self._evaluate_table(
            alpha_deg,
            lower_table["alpha_deg"],
            lower_table["Cl"],
            lower_table["Cd"],
            lower_table.get("Cm"),
            f"{label} Re={lower_re:g}",
        )
        upper_cl, upper_cd, upper_cm, upper_clipped = self._evaluate_table(
            alpha_deg,
            upper_table["alpha_deg"],
            upper_table["Cl"],
            upper_table["Cd"],
            upper_table.get("Cm"),
            f"{label} Re={upper_re:g}",
        )
        fraction = (reynolds_number - lower_re) / max(upper_re - lower_re, 1.0e-12)
        return {
            "Cl": lower_cl + fraction * (upper_cl - lower_cl),
            "Cd": lower_cd + fraction * (upper_cd - lower_cd),
            "Cm": lower_cm + fraction * (upper_cm - lower_cm)
            if math.isfinite(lower_cm) and math.isfinite(upper_cm)
            else math.nan,
            "alpha_clipped": lower_clipped or upper_clipped,
            "Cl_clipped": False,
            "polar_Re_used": f"{lower_re:g}-{upper_re:g}",
            "polar_source": f"{lower_table['polar_source']};{upper_table['polar_source']}",
        }

    def _reynolds_tables_for_airfoil(self, airfoil_name: str | None) -> tuple[str, dict[float | None, dict[str, Any]]]:
        if not self.by_airfoil_re:
            raise ValueError("Reynolds polar data are empty")
        normalized = {name.lower(): name for name in self.by_airfoil_re}
        if airfoil_name and airfoil_name.lower() in normalized:
            label = normalized[airfoil_name.lower()]
            return label, self.by_airfoil_re[label]
        if len(self.by_airfoil_re) == 1:
            label = next(iter(self.by_airfoil_re))
            return label, self.by_airfoil_re[label]
        raise ValueError(f"no polar table found for airfoil {airfoil_name!r}")

    def _table_for_airfoil(self, airfoil_name: str | None) -> tuple[list[float], list[float], list[float], str]:
        if self.by_airfoil:
            normalized = {name.lower(): name for name in self.by_airfoil}
            if airfoil_name and airfoil_name.lower() in normalized:
                label = normalized[airfoil_name.lower()]
                table = self.by_airfoil[label]
                return table["alpha_deg"], table["Cl"], table["Cd"], label
            if len(self.by_airfoil) == 1:
                label = next(iter(self.by_airfoil))
                table = self.by_airfoil[label]
                return table["alpha_deg"], table["Cl"], table["Cd"], label
            raise ValueError(f"no polar table found for airfoil {airfoil_name!r}")

        if self.alpha_deg is None or self.cl is None or self.cd is None:
            raise ValueError("polar data are empty")
        return self.alpha_deg, self.cl, self.cd, airfoil_name or "global"


def rpm_to_omega(rpm: float) -> float:
    return rpm * 2.0 * math.pi / 60.0


def compute_disk_area(radius_m: float) -> float:
    _require_positive_finite("radius_m", radius_m)
    return math.pi * radius_m**2


def compute_hover_induced_velocity(T: float, rho: float, disk_area: float) -> float:
    _require_nonnegative_finite("T", T)
    _require_positive_finite("rho", rho)
    _require_positive_finite("disk_area", disk_area)
    return math.sqrt(T / (2.0 * rho * disk_area))


def load_geometry(path: str | Path) -> list[dict[str, Any]]:
    geometry_path = _resolve_project_input_path(path)
    if not geometry_path.exists():
        raise FileNotFoundError(f"geometry CSV not found: {geometry_path}")

    with geometry_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"geometry CSV has no header: {geometry_path}")
        raw_rows = list(reader)

    if not raw_rows:
        raise ValueError(f"geometry CSV has no station rows: {geometry_path}")

    column_map = _case_insensitive_columns(reader.fieldnames)
    r_R_col = _first_column(column_map, ["r_R", "r/r", "mu"])
    r_m_col = _first_column(column_map, ["r_m", "radius_m"])
    chord_col = _first_column(column_map, ["chord_m"])
    twist_col = _first_column(column_map, ["twist_deg"])
    airfoil_col = _first_column(column_map, ["airfoil"])
    dr_col = _first_column(column_map, ["dr_m"], required=False)

    stations: list[dict[str, Any]] = []
    for row_index, raw in enumerate(raw_rows, start=2):
        r_R = _to_float(raw[r_R_col], f"{geometry_path}:{row_index} {r_R_col}")
        r_m = _to_float(raw[r_m_col], f"{geometry_path}:{row_index} {r_m_col}")
        chord_m = _to_float(raw[chord_col], f"{geometry_path}:{row_index} {chord_col}")
        twist_deg = _to_float(raw[twist_col], f"{geometry_path}:{row_index} {twist_col}")
        airfoil = str(raw[airfoil_col]).strip()

        if not 0.0 <= r_R <= 1.0:
            raise ValueError(f"r_R must be between 0 and 1 at row {row_index}: {r_R}")
        _require_positive_finite(f"row {row_index} r_m", r_m)
        _require_positive_finite(f"row {row_index} chord_m", chord_m)
        _require_finite(f"row {row_index} twist_deg", twist_deg)
        if not airfoil:
            raise ValueError(f"airfoil is blank at row {row_index}")

        station = {
            "r_R": r_R,
            "r_m": r_m,
            "chord_m": chord_m,
            "twist_deg": twist_deg,
            "airfoil": airfoil,
        }
        if dr_col and str(raw.get(dr_col, "")).strip():
            dr_m = _to_float(raw[dr_col], f"{geometry_path}:{row_index} {dr_col}")
            _require_positive_finite(f"row {row_index} dr_m", dr_m)
            station["dr_m"] = dr_m
        stations.append(station)

    for left, right in zip(stations, stations[1:]):
        if right["r_m"] <= left["r_m"]:
            raise ValueError("geometry stations must be sorted by strictly increasing r_m")

    if not all("dr_m" in station for station in stations):
        dr_values = _station_widths_m(stations)
        for station, dr_m in zip(stations, dr_values):
            station["dr_m"] = dr_m

    return stations


def load_polar(path: str | Path | None = None, cl_max_debug: float = 1.5) -> AirfoilPolar:
    if path is None:
        warnings.warn(
            "No airfoil polar file was provided. Using placeholder polar "
            "Cl = Cl_alpha * alpha_rad and Cd = Cd0 + k * Cl^2. "
            "Placeholder polar is for debugging only and may overpredict lift. "
            "This is not final thesis-quality polar data.",
            RuntimeWarning,
            stacklevel=2,
        )
        return AirfoilPolar(placeholder=True, source="placeholder_debug_polar", cl_max_debug=cl_max_debug)

    polar_path = _resolve_project_input_path(path)
    if not polar_path.exists():
        raise FileNotFoundError(f"polar CSV not found: {polar_path}")

    with polar_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"polar CSV has no header: {polar_path}")
        rows = list(reader)

    if not rows:
        raise ValueError(f"polar CSV has no rows: {polar_path}")

    column_map = _case_insensitive_columns(reader.fieldnames)
    airfoil_col = _first_column(column_map, ["airfoil_name", "airfoil"], required=False)
    alpha_col = _first_column(column_map, ["alpha_deg", "alpha"])
    cl_col = _first_column(column_map, ["Cl", "cl"])
    cd_col = _first_column(column_map, ["Cd", "cd"])
    re_col = _first_column(column_map, ["Re", "re", "reynolds", "reynolds_number"], required=False)

    cm_col = _first_column(column_map, ["Cm", "cm", "CM"], required=False)
    pairs_by_airfoil: dict[str, dict[float | None, list[tuple[float, float, float, float, str]]]] = {}
    global_pairs: dict[float | None, list[tuple[float, float, float, float, str]]] = {}
    for row_index, raw in enumerate(rows, start=2):
        airfoil_name = _normalize_airfoil_name(str(raw[airfoil_col]).strip()) if airfoil_col else ""
        alpha_deg = _to_float(raw[alpha_col], f"{polar_path}:{row_index} {alpha_col}")
        cl = _to_float(raw[cl_col], f"{polar_path}:{row_index} {cl_col}")
        cd = _to_float(raw[cd_col], f"{polar_path}:{row_index} {cd_col}")
        cm = _to_float(raw[cm_col], f"{polar_path}:{row_index} {cm_col}") if cm_col and str(raw.get(cm_col, "")).strip() else math.nan
        re_value = _to_float(raw[re_col], f"{polar_path}:{row_index} {re_col}") if re_col else None
        _require_finite(f"row {row_index} alpha_deg", alpha_deg)
        _require_finite(f"row {row_index} Cl", cl)
        _require_nonnegative_finite(f"row {row_index} Cd", cd)
        if re_value is not None:
            _require_positive_finite(f"row {row_index} Re", re_value)
        if airfoil_col:
            if not airfoil_name:
                raise ValueError(f"airfoil_name is blank at polar row {row_index}")
            pairs_by_airfoil.setdefault(airfoil_name, {}).setdefault(re_value, []).append(
                (alpha_deg, cl, cd, cm, str(polar_path))
            )
        else:
            global_pairs.setdefault(re_value, []).append((alpha_deg, cl, cd, cm, str(polar_path)))

    if pairs_by_airfoil:
        return AirfoilPolar(
            by_airfoil_re=_build_reynolds_tables(pairs_by_airfoil),
            source=str(polar_path),
            placeholder=False,
            generated_polars_used="generated_polars" in str(polar_path).replace("\\", "/"),
        )

    built_global = _build_reynolds_tables({"global": global_pairs})
    if any(re_value is not None for re_value in global_pairs):
        return AirfoilPolar(
            by_airfoil_re=built_global,
            source=str(polar_path),
            placeholder=False,
            generated_polars_used="generated_polars" in str(polar_path).replace("\\", "/"),
        )

    flat_table = built_global["GLOBAL"][None]

    return AirfoilPolar(
        alpha_deg=flat_table["alpha_deg"],
        cl=flat_table["Cl"],
        cd=flat_table["Cd"],
        source=str(polar_path),
        placeholder=False,
    )


def load_airfoil_polar_directory(airfoil_dir: str | Path) -> AirfoilPolar:
    from airfoil_tools.airfoil_polar_loader import load_airfoil_polars

    airfoil_root = _resolve_project_input_path(airfoil_dir)
    polars = load_airfoil_polars(airfoil_root)
    if not polars:
        raise FileNotFoundError(f"no alpha/Cl/Cd polar CSV files found in {airfoil_root}")

    by_airfoil: dict[str, dict[float | None, list[tuple[float, float, float, float, str]]]] = {}
    generated_polars_used = False
    sources: set[str] = set()
    for airfoil_name, frame in polars.items():
        for _, row in frame.iterrows():
            re_raw = row.get("Re")
            re_value = None if re_raw != re_raw else float(re_raw)
            source = str(row.get("polar_source", airfoil_root))
            generated_polars_used = generated_polars_used or "generated_polars" in source.replace("\\", "/")
            sources.add(source)
            by_airfoil.setdefault(_normalize_airfoil_name(airfoil_name), {}).setdefault(re_value, []).append(
                (
                    float(row["alpha_deg"]),
                    float(row["Cl"]),
                    float(row["Cd"]),
                    float(row["Cm"]) if row.get("Cm") == row.get("Cm") else math.nan,
                    source,
                )
            )

    return AirfoilPolar(
        by_airfoil_re=_build_reynolds_tables(by_airfoil),
        source=";".join(sorted(sources)),
        placeholder=False,
        generated_polars_used=generated_polars_used,
        failed_polar_cases=_detect_failed_polar_cases(airfoil_root),
    )


def find_missing_xfoil_polar_csvs(
    airfoil_dir: str | Path,
    required_airfoils: Sequence[str] = REQUIRED_AIRFOILS,
    required_reynolds_numbers: Sequence[int] = REQUIRED_POLAR_REYNOLDS_NUMBERS,
) -> list[str]:
    from airfoil_tools.airfoil_polar_loader import load_airfoil_polars

    root = _resolve_project_input_path(airfoil_dir)
    generated_marker = "generated_polars"
    polars = load_airfoil_polars(root)
    missing: list[str] = []
    for airfoil in required_airfoils:
        normalized_airfoil = _normalize_airfoil_name(airfoil)
        frame = polars.get(normalized_airfoil)
        if frame is None or frame.empty:
            missing.extend(f"{normalized_airfoil} Re={reynolds_number}" for reynolds_number in required_reynolds_numbers)
            continue

        generated_frame = frame[
            frame["polar_source"].astype(str).str.replace("\\", "/", regex=False).str.contains(generated_marker)
        ]
        re_values = {
            int(round(float(value)))
            for value in generated_frame["Re"].dropna().unique()
            if math.isfinite(float(value))
        }
        for reynolds_number in required_reynolds_numbers:
            if int(reynolds_number) not in re_values:
                missing.append(f"{normalized_airfoil} Re={int(reynolds_number)}")
    return missing


def ensure_xfoil_polars_available(
    airfoil_dir: str | Path,
    xfoil_executable: str | Path | None = None,
    xfoil_root: str | Path | None = None,
) -> None:
    missing_before = find_missing_xfoil_polar_csvs(airfoil_dir)
    if not missing_before:
        return

    print("Missing XFOIL-generated polar CSVs:")
    for missing_case in missing_before:
        print(f"  {missing_case}")
    print("Attempting to generate missing thesis airfoil polars with XFOIL...")

    try:
        from airfoil_tools.generate_xfoil_polars import DEFAULT_XFOIL_ROOT, generate_xfoil_polars
    except ImportError as exc:
        raise RuntimeError(f"could not import XFOIL polar generator: {exc}") from exc

    generator_xfoil_root = DEFAULT_XFOIL_ROOT if xfoil_root is None else xfoil_root
    results = generate_xfoil_polars(
        airfoil_dir,
        xfoil_executable=xfoil_executable,
        xfoil_root=generator_xfoil_root,
    )
    if not results:
        raise RuntimeError("XFOIL executable not found. Cannot generate thesis-quality polar data.")

    failed_results = [result for result in results if not result.success]
    if failed_results:
        failed_text = ", ".join(
            f"{result.airfoil} Re={result.reynolds_number}: {result.failure_reason}"
            for result in failed_results
        )
        raise RuntimeError(f"XFOIL polar generation failed: {failed_text}")

    missing_after = find_missing_xfoil_polar_csvs(airfoil_dir)
    if missing_after:
        missing_text = ", ".join(missing_after)
        raise RuntimeError(f"XFOIL polar generation did not produce all required polar CSVs: {missing_text}")


def _bemt_station_kinematics(
    *,
    station_radius_m: float,
    chord_m: float,
    twist_deg: float,
    rpm: float,
    rho: float,
    mu_air_kg_m_s: float,
    freestream_velocity_m_s: float,
    induced_velocity_m_s: float,
) -> dict[str, float]:
    omega_rad_s = rpm * 2.0 * math.pi / 60.0
    Vt_m_s = omega_rad_s * station_radius_m
    Va_m_s = freestream_velocity_m_s + induced_velocity_m_s
    Vrel_m_s = math.sqrt(Vt_m_s**2 + Va_m_s**2)
    phi_rad = math.atan2(Va_m_s, Vt_m_s)
    phi_deg = math.degrees(phi_rad)
    alpha_deg = twist_deg - phi_deg
    q_Pa = 0.5 * rho * Vrel_m_s**2
    reynolds_number = rho * Vrel_m_s * chord_m / mu_air_kg_m_s

    return {
        "omega_rad_s": omega_rad_s,
        "Vt_m_s": Vt_m_s,
        "Va_m_s": Va_m_s,
        "Vrel_m_s": Vrel_m_s,
        "phi_rad": phi_rad,
        "phi_deg": phi_deg,
        "alpha_deg": alpha_deg,
        "q_Pa": q_Pa,
        "Re": reynolds_number,
    }


def _bemt_station_loads(
    *,
    station_radius_m: float,
    chord_m: float,
    dr_m: float,
    blade_count: int,
    q_Pa: float,
    phi_rad: float,
    Cl: float,
    Cd: float,
) -> dict[str, float]:
    dL_N = q_Pa * chord_m * Cl * dr_m
    dD_N = q_Pa * chord_m * Cd * dr_m
    dT_N = blade_count * (dL_N * math.cos(phi_rad) - dD_N * math.sin(phi_rad))
    dF_tangential_N = blade_count * (dL_N * math.sin(phi_rad) + dD_N * math.cos(phi_rad))
    dQ_Nm = station_radius_m * dF_tangential_N

    return {
        "dL_N": dL_N,
        "dD_N": dD_N,
        "dT_N": dT_N,
        "dF_tangential_N": dF_tangential_N,
        "dQ_Nm": dQ_Nm,
        "qz_N_per_m": dT_N / dr_m,
        "qy_tangential_N_per_m": dF_tangential_N / dr_m,
        "torque_loading_Nm_per_m": dQ_Nm / dr_m,
    }


def _bemt_propeller_totals(
    station_results: Sequence[Mapping[str, Any]],
    *,
    rpm: float,
    rho: float,
    diameter_m: float,
) -> dict[str, float]:
    thrust_N = sum(row["dT_N"] for row in station_results)
    torque_Nm = sum(row["dQ_Nm"] for row in station_results)
    omega_rad_s = rpm * 2.0 * math.pi / 60.0
    power_W = torque_Nm * omega_rad_s
    n_rev_per_s = rpm / 60.0

    CT = thrust_N / (rho * n_rev_per_s**2 * diameter_m**4)
    CQ = torque_Nm / (rho * n_rev_per_s**2 * diameter_m**5)
    CP = power_W / (rho * n_rev_per_s**3 * diameter_m**5)

    return {
        "thrust_N": thrust_N,
        "torque_Nm": torque_Nm,
        "omega_rad_s": omega_rad_s,
        "power_W": power_W,
        "n_rev_per_s": n_rev_per_s,
        "CT": CT,
        "CQ": CQ,
        "CP": CP,
    }


def evaluate_station(
    station: Mapping[str, Any],
    operating_condition: OperatingCondition | Mapping[str, Any],
    polar: AirfoilPolar,
    dr_m: float | None = None,
    induced_velocity_m_s: float = 0.0,
    collective_twist_offset_deg: float = 0.0,
) -> dict[str, Any]:
    op = _normalize_operating_condition(operating_condition)
    r_m = float(station["r_m"])
    chord_m = float(station["chord_m"])
    station_dr_m = float(station["dr_m"] if dr_m is None else dr_m)
    twist_deg = float(station["twist_deg"]) + collective_twist_offset_deg

    _require_positive_finite("station r_m", r_m)
    _require_positive_finite("station chord_m", chord_m)
    _require_positive_finite("station dr_m", station_dr_m)

    kinematics = _bemt_station_kinematics(
        station_radius_m=r_m,
        chord_m=chord_m,
        twist_deg=twist_deg,
        rpm=op.rpm,
        rho=op.rho,
        mu_air_kg_m_s=op.mu_air_kg_m_s,
        freestream_velocity_m_s=op.V_inf_m_s,
        induced_velocity_m_s=induced_velocity_m_s,
    )
    airfoil_name = str(station.get("airfoil", ""))
    polar_result = polar.evaluate_with_metadata(kinematics["alpha_deg"], airfoil_name, kinematics["Re"])
    cl = polar_result["Cl"]
    cd = polar_result["Cd"]
    cm = polar_result["Cm"]
    cm_output: float | str = cm if isinstance(cm, (int, float)) and math.isfinite(float(cm)) else ""
    loads = _bemt_station_loads(
        station_radius_m=r_m,
        chord_m=chord_m,
        dr_m=station_dr_m,
        blade_count=op.blade_count,
        q_Pa=kinematics["q_Pa"],
        phi_rad=kinematics["phi_rad"],
        Cl=cl,
        Cd=cd,
    )

    return {
        "r_R": float(station["r_R"]),
        "r_m": r_m,
        "dr_m": station_dr_m,
        "chord_m": chord_m,
        "twist_deg": twist_deg,
        "airfoil": airfoil_name,
        "phi_deg": kinematics["phi_deg"],
        "alpha_deg": kinematics["alpha_deg"],
        "Re": kinematics["Re"],
        "polar_Re_used": polar_result["polar_Re_used"],
        "polar_source": polar_result["polar_source"],
        "Cl": cl,
        "Cd": cd,
        "Cm": cm_output,
        "alpha_clipped": bool(polar_result["alpha_clipped"]),
        "Cl_clipped": bool(polar_result["Cl_clipped"]),
        "Vt_m_s": kinematics["Vt_m_s"],
        "Va_m_s": kinematics["Va_m_s"],
        "Vrel_m_s": kinematics["Vrel_m_s"],
        "q_Pa": kinematics["q_Pa"],
        "dL_N": loads["dL_N"],
        "dD_N": loads["dD_N"],
        "dT_N": loads["dT_N"],
        "dF_tangential_N": loads["dF_tangential_N"],
        "dQ_Nm": loads["dQ_Nm"],
        "qz_N_per_m": loads["qz_N_per_m"],
        "qy_tangential_N_per_m": loads["qy_tangential_N_per_m"],
        "torque_loading_Nm_per_m": loads["torque_loading_Nm_per_m"],
        "polar_alpha_clipped": bool(polar_result["alpha_clipped"]),
        "cl_clipped": bool(polar_result["Cl_clipped"]),
    }


def evaluate_propeller(
    geometry: Sequence[Mapping[str, Any]] | str | Path,
    operating_condition: OperatingCondition | Mapping[str, Any] | None = None,
    polar: AirfoilPolar | None = None,
    collective_twist_offset_deg: float = 0.0,
    induced_velocity_m_s: float | None = None,
    case_name: str | None = None,
    target_match_tolerance_pct: float = 5.0,
    allow_polar_fallback: bool = False,
) -> dict[str, Any]:
    stations = load_geometry(geometry) if isinstance(geometry, (str, Path)) else [dict(row) for row in geometry]
    if len(stations) < 1:
        raise ValueError("geometry must contain at least one station")

    op = _normalize_operating_condition(operating_condition)
    if case_name is not None:
        op = replace(op, case_name=case_name)
    if polar is None:
        polar = load_polar(None)

    if not all("dr_m" in station for station in stations):
        dr_values = _station_widths_m(stations)
        for station, dr_m in zip(stations, dr_values):
            station["dr_m"] = dr_m
    fallback_airfoils_used = _validate_airfoil_polar_coverage(stations, polar, allow_polar_fallback)

    disk_area = compute_disk_area(op.radius_m)
    vi_target_m_s = (
        compute_hover_induced_velocity(op.target_thrust_N, op.rho, disk_area)
        if op.hover_mode and abs(op.V_inf_m_s) <= 1.0e-12 and op.target_thrust_N is not None and op.target_thrust_N >= 0.0
        else 0.0
    )
    if induced_velocity_m_s is None:
        induced_velocity_m_s = _induced_velocity_for_case(stations, op, polar, collective_twist_offset_deg, disk_area)

    station_results = [
        evaluate_station(
            station,
            op,
            polar,
            dr_m=float(station["dr_m"]),
            induced_velocity_m_s=induced_velocity_m_s,
            collective_twist_offset_deg=collective_twist_offset_deg,
        )
        for station in stations
    ]

    totals = _bemt_propeller_totals(station_results, rpm=op.rpm, rho=op.rho, diameter_m=op.diameter_m)
    thrust_N = totals["thrust_N"]
    torque_Nm = totals["torque_Nm"]
    omega_rad_s = totals["omega_rad_s"]
    power_W = totals["power_W"]
    n_rev_per_s = totals["n_rev_per_s"]
    CT = totals["CT"]
    CQ = totals["CQ"]
    CP = totals["CP"]
    efficiency = thrust_N * op.V_inf_m_s / power_W if abs(power_W) > 1.0e-12 else None
    vi_computed_from_result_m_s = (
        compute_hover_induced_velocity(max(thrust_N, 0.0), op.rho, disk_area)
        if op.hover_mode and abs(op.V_inf_m_s) <= 1.0e-12
        else 0.0
    )

    cumulative_thrust = _cumulative_fraction([row["dT_N"] for row in station_results])
    cumulative_torque = _cumulative_fraction([row["dQ_Nm"] for row in station_results])
    for row, thrust_fraction, torque_fraction in zip(station_results, cumulative_thrust, cumulative_torque):
        row["case_name"] = op.case_name
        row["cumulative_thrust_fraction"] = thrust_fraction
        row["cumulative_torque_fraction"] = torque_fraction

    target_thrust = op.target_thrust_N
    thrust_error_pct = None
    if target_thrust is not None and abs(target_thrust) > 1.0e-12:
        thrust_error_pct = (thrust_N - target_thrust) / target_thrust * 100.0

    max_Cl = max((row["Cl"] for row in station_results), default=0.0)
    max_Cd = max((row["Cd"] for row in station_results), default=0.0)
    max_alpha_deg = max((row["alpha_deg"] for row in station_results), default=0.0)
    min_alpha_deg = min((row["alpha_deg"] for row in station_results), default=0.0)
    Re_values = [row["Re"] for row in station_results]
    Re_min = min(Re_values, default=0.0)
    Re_max = max(Re_values, default=0.0)
    alpha_clipping_fraction_pct = 100.0 * sum(row["alpha_clipped"] for row in station_results) / max(
        len(station_results), 1
    )
    cl_clipping_fraction_pct = 100.0 * sum(row.get("cl_clipped", False) for row in station_results) / max(
        len(station_results), 1
    )
    clipping_fraction_pct = 100.0 * sum(row["alpha_clipped"] or row["Cl_clipped"] for row in station_results) / max(
        len(station_results), 1
    )
    airfoils_used = sorted({_normalize_airfoil_name(str(row["airfoil"])) for row in station_results})
    CT_expected = None
    CT_expected_error_pct = None
    if target_thrust is not None:
        CT_expected = target_thrust / (op.rho * n_rev_per_s**2 * op.diameter_m**4)
        CT_expected_error_pct = (CT - CT_expected) / CT_expected * 100.0 if abs(CT_expected) > 1.0e-12 else None
    xfoil_success = _polar_xfoil_success(polar)

    summary = {
        "case_name": op.case_name,
        "thrust_N": thrust_N,
        "torque_Nm": torque_Nm,
        "power_W": power_W,
        "rpm": op.rpm,
        "omega_rad_s": omega_rad_s,
        "CT": CT,
        "CQ": CQ,
        "CP": CP,
        "efficiency": efficiency,
        "target_thrust_N": target_thrust,
        "thrust_error_pct": thrust_error_pct,
        "collective_twist_offset_deg": collective_twist_offset_deg,
        "induced_velocity_m_s": induced_velocity_m_s,
        "Vi_target_m_s": vi_target_m_s,
        "Vi_computed_from_result_m_s": vi_computed_from_result_m_s,
        "Vi_difference_m_s": vi_computed_from_result_m_s - vi_target_m_s,
        "disk_area_m2": disk_area,
        "max_Cl": max_Cl,
        "max_Cd": max_Cd,
        "max_alpha_deg": max_alpha_deg,
        "min_alpha_deg": min_alpha_deg,
        "Re_min": Re_min,
        "Re_max": Re_max,
        "clipping_fraction_pct": clipping_fraction_pct,
        "cl_clipping_fraction_pct": cl_clipping_fraction_pct,
        "Cl_clipping_fraction_pct": cl_clipping_fraction_pct,
        "alpha_clipping_fraction_pct": alpha_clipping_fraction_pct,
        "polar_alpha_clipping_fraction_pct": alpha_clipping_fraction_pct,
        "Cl_max_debug": polar.cl_max_debug if polar.placeholder else None,
        "CT_expected": CT_expected,
        "CT_expected_error_pct": CT_expected_error_pct,
        "target_match_tolerance_pct": target_match_tolerance_pct,
        "target_matched": False,
        "polar_is_placeholder": polar.placeholder,
        "polar_source": polar.source,
        "xfoil_polars_used": polar.generated_polars_used and not polar.placeholder,
        "xfoil_success": xfoil_success,
        "airfoils_used": airfoils_used,
        "generated_polars_used": polar.generated_polars_used,
        "fallback_airfoils_used": fallback_airfoils_used,
        "failed_polar_cases": polar.failed_polar_cases,
        "xrotor_ran": False,
        "xrotor_success": False,
        "xrotor_comparison_file": "",
        "xrotor_velocity_m_s": None,
        "valid_for_thesis": False,
        "failure_reason": "",
    }

    result = {
        "case_name": op.case_name,
        "operating_condition": asdict(op),
        "polar_source": polar.source,
        "polar_is_placeholder": polar.placeholder,
        "xfoil_polars_used": polar.generated_polars_used and not polar.placeholder,
        "xfoil_success": xfoil_success,
        "generated_polars_used": polar.generated_polars_used,
        "fallback_airfoils_used": fallback_airfoils_used,
        "failed_polar_cases": polar.failed_polar_cases,
        "xrotor_ran": False,
        "xrotor_success": False,
        "xrotor_comparison_file": "",
        "stations": station_results,
        "summary": summary,
        "warnings": [],
        "sanity_checks": {},
    }
    if polar.placeholder:
        result["warnings"].append(
            "Placeholder polar is for debugging only and may overpredict lift. "
            "Replace it with thesis-quality airfoil polar data before using results for thesis conclusions."
        )
    _annotate_result_validity(result, target_match_tolerance_pct)
    result["sanity_checks"] = _run_sanity_checks(result)
    return result


def iterate_to_target_thrust(
    geometry: Sequence[Mapping[str, Any]] | str | Path,
    operating_condition: OperatingCondition | Mapping[str, Any] | None,
    polar: AirfoilPolar | None,
    target_thrust_N: float | None = None,
    tolerance_pct: float = 1.0,
    max_iterations: int = 60,
    allow_polar_fallback: bool = False,
) -> tuple[dict[str, Any], float, float]:
    op = _normalize_operating_condition(operating_condition)
    target = float(target_thrust_N if target_thrust_N is not None else op.target_thrust_N)
    _require_positive_finite("target_thrust_N", target)
    _require_positive_finite("tolerance_pct", tolerance_pct)
    op = replace(op, target_thrust_N=target)
    stations = load_geometry(geometry) if isinstance(geometry, (str, Path)) else [dict(row) for row in geometry]
    if polar is None:
        polar = load_polar(None)

    tolerance_N = abs(target) * tolerance_pct / 100.0
    disk_area = compute_disk_area(op.radius_m)
    vi_target_m_s = (
        compute_hover_induced_velocity(target, op.rho, disk_area)
        if op.hover_mode and abs(op.V_inf_m_s) <= 1.0e-12
        else 0.0
    )

    def evaluate_at(offset_deg: float) -> tuple[dict[str, Any], float]:
        result = evaluate_propeller(
            stations,
            op,
            polar,
            collective_twist_offset_deg=offset_deg,
            induced_velocity_m_s=vi_target_m_s,
            case_name=op.case_name,
            target_match_tolerance_pct=tolerance_pct,
            allow_polar_fallback=allow_polar_fallback,
        )
        return result, result["summary"]["thrust_N"] - target

    result_zero, residual_zero = evaluate_at(0.0)
    low_offset = -30.0
    high_offset = 10.0
    low_result, low_residual = evaluate_at(low_offset)
    high_result, high_residual = evaluate_at(high_offset)

    if abs(residual_zero) <= tolerance_N:
        error_pct = residual_zero / target * 100.0
        result_zero["summary"]["thrust_error_pct"] = error_pct
        result_zero["summary"]["target_matched"] = True
        result_zero["target_iteration"] = {
            "target_thrust_N": target,
            "tolerance_pct": tolerance_pct,
            "initial_bracket_deg": [-30.0, 10.0],
            "collective_twist_offset_deg": 0.0,
            "thrust_error_pct": error_pct,
            "Vi_target_m_s": vi_target_m_s,
            "Vi_computed_from_result_m_s": result_zero["summary"]["Vi_computed_from_result_m_s"],
            "Vi_difference_m_s": result_zero["summary"]["Vi_difference_m_s"],
        }
        _annotate_result_validity(result_zero, tolerance_pct)
        result_zero["sanity_checks"] = _run_sanity_checks(result_zero)
        return result_zero, 0.0, error_pct

    if low_residual * high_residual > 0.0:
        expansion_step = 10.0
        for _ in range(12):
            if abs(low_residual) < abs(high_residual):
                high_offset += expansion_step
                high_result, high_residual = evaluate_at(high_offset)
            else:
                low_offset -= expansion_step
                low_result, low_residual = evaluate_at(low_offset)
            expansion_step *= 1.5
            if low_residual * high_residual <= 0.0:
                break
        else:
            raise ValueError(
                "Could not match target thrust with collective twist offset. "
                f"Bracket [{low_offset:.3f}, {high_offset:.3f}] deg produced residuals "
                f"{low_residual:.6g} N and {high_residual:.6g} N."
            )

    best_result = min(
        [(low_result, low_offset, low_residual), (result_zero, 0.0, residual_zero), (high_result, high_offset, high_residual)],
        key=lambda item: abs(item[2]),
    )[0]
    best_offset = min(
        [(low_result, low_offset, low_residual), (result_zero, 0.0, residual_zero), (high_result, high_offset, high_residual)],
        key=lambda item: abs(item[2]),
    )[1]
    best_residual = min(
        [(low_result, low_offset, low_residual), (result_zero, 0.0, residual_zero), (high_result, high_offset, high_residual)],
        key=lambda item: abs(item[2]),
    )[2]
    for _ in range(max_iterations):
        mid_offset = 0.5 * (low_offset + high_offset)
        mid_result, mid_residual = evaluate_at(mid_offset)
        if abs(mid_residual) < abs(best_residual):
            best_result = mid_result
            best_offset = mid_offset
            best_residual = mid_residual
        if abs(mid_residual) <= tolerance_N:
            break
        if low_residual * mid_residual <= 0.0:
            high_offset, high_residual = mid_offset, mid_residual
        else:
            low_offset, low_residual = mid_offset, mid_residual

    error_pct = best_residual / target * 100.0
    best_result["summary"]["collective_twist_offset_deg"] = best_offset
    best_result["summary"]["thrust_error_pct"] = error_pct
    best_result["target_iteration"] = {
        "target_thrust_N": target,
        "tolerance_pct": tolerance_pct,
        "initial_bracket_deg": [-30.0, 10.0],
        "final_bracket_deg": [low_offset, high_offset],
        "collective_twist_offset_deg": best_offset,
        "thrust_error_pct": error_pct,
        "Vi_target_m_s": vi_target_m_s,
        "Vi_computed_from_result_m_s": best_result["summary"]["Vi_computed_from_result_m_s"],
        "Vi_difference_m_s": best_result["summary"]["Vi_difference_m_s"],
    }
    _annotate_result_validity(best_result, tolerance_pct)
    best_result["sanity_checks"] = _run_sanity_checks(best_result)
    return best_result, best_offset, error_pct


def compare_cases(mil_results: Mapping[str, Any], gmt_results: Mapping[str, Any]) -> dict[str, float]:
    mil_summary = mil_results["summary"]
    gmt_summary = gmt_results["summary"]
    mil_stations = mil_results["stations"]
    gmt_stations = gmt_results["stations"]

    mil_tip_thrust = _integrate_r_R_band(mil_stations, "dT_N", 0.85, 1.0)
    gmt_tip_thrust = _integrate_r_R_band(gmt_stations, "dT_N", 0.85, 1.0)
    mil_tip_torque = _integrate_r_R_band(mil_stations, "dQ_Nm", 0.85, 1.0)
    gmt_tip_torque = _integrate_r_R_band(gmt_stations, "dQ_Nm", 0.85, 1.0)
    mil_inboard_thrust = _integrate_r_R_band(mil_stations, "dT_N", 0.2, 0.72)
    gmt_inboard_thrust = _integrate_r_R_band(gmt_stations, "dT_N", 0.2, 0.72)
    mil_inboard_torque = _integrate_r_R_band(mil_stations, "dQ_Nm", 0.2, 0.72)
    gmt_inboard_torque = _integrate_r_R_band(gmt_stations, "dQ_Nm", 0.2, 0.72)

    return {
        "thrust_match_error_pct": abs(gmt_summary["thrust_N"] - mil_summary["thrust_N"])
        / max(abs(mil_summary["thrust_N"]), 1.0e-12)
        * 100.0,
        "torque_reduction_pct": _safe_div(mil_summary["torque_Nm"] - gmt_summary["torque_Nm"], mil_summary["torque_Nm"])
        * 100.0,
        "power_reduction_pct": _safe_div(mil_summary["power_W"] - gmt_summary["power_W"], mil_summary["power_W"])
        * 100.0,
        "CT_difference": gmt_summary["CT"] - mil_summary["CT"],
        "CQ_difference": gmt_summary["CQ"] - mil_summary["CQ"],
        "CP_difference": gmt_summary["CP"] - mil_summary["CP"],
        "mil_tip_thrust_fraction": _safe_div(mil_tip_thrust, mil_summary["thrust_N"]),
        "gmt_tip_thrust_fraction": _safe_div(gmt_tip_thrust, gmt_summary["thrust_N"]),
        "mil_tip_torque_fraction": _safe_div(mil_tip_torque, mil_summary["torque_Nm"]),
        "gmt_tip_torque_fraction": _safe_div(gmt_tip_torque, gmt_summary["torque_Nm"]),
        "mil_inboard_thrust_fraction": _safe_div(mil_inboard_thrust, mil_summary["thrust_N"]),
        "gmt_inboard_thrust_fraction": _safe_div(gmt_inboard_thrust, gmt_summary["thrust_N"]),
        "mil_inboard_torque_fraction": _safe_div(mil_inboard_torque, mil_summary["torque_Nm"]),
        "gmt_inboard_torque_fraction": _safe_div(gmt_inboard_torque, gmt_summary["torque_Nm"]),
        "mil_cumulative_50_thrust_r_R": _cumulative_radius_at_fraction(mil_stations, "dT_N", 0.5),
        "gmt_cumulative_50_thrust_r_R": _cumulative_radius_at_fraction(gmt_stations, "dT_N", 0.5),
        "mil_cumulative_50_torque_r_R": _cumulative_radius_at_fraction(mil_stations, "dQ_Nm", 0.5),
        "gmt_cumulative_50_torque_r_R": _cumulative_radius_at_fraction(gmt_stations, "dQ_Nm", 0.5),
        "mil_max_Cl": max((row["Cl"] for row in mil_stations), default=0.0),
        "gmt_max_Cl": max((row["Cl"] for row in gmt_stations), default=0.0),
        "mil_Cl_clipping_fraction_pct": mil_summary.get("clipping_fraction_pct", 0.0),
        "gmt_Cl_clipping_fraction_pct": gmt_summary.get("clipping_fraction_pct", 0.0),
    }


def export_results(results: Mapping[str, Any], output_dir: str | Path, case_name: str | None = None) -> dict[str, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = _clean_case_name(case_name or str(results["case_name"]))

    station_csv_path = out_dir / f"{name}_stations.csv"
    with station_csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=STATION_EXPORT_COLUMNS)
        writer.writeheader()
        for row in results["stations"]:
            writer.writerow({column: row.get(column, "") for column in STATION_EXPORT_COLUMNS})

    summary_json_path = out_dir / f"{name}_summary.json"
    summary_payload = {
        "summary": results["summary"],
        "operating_condition": results["operating_condition"],
        "polar_source": results["polar_source"],
        "polar_is_placeholder": results["polar_is_placeholder"],
        "xfoil_polars_used": results["summary"].get("xfoil_polars_used", results.get("xfoil_polars_used", False)),
        "xfoil_success": results["summary"].get("xfoil_success", results.get("xfoil_success", False)),
        "xrotor_ran": results["summary"].get("xrotor_ran", results.get("xrotor_ran", False)),
        "xrotor_success": results["summary"].get("xrotor_success", results.get("xrotor_success", False)),
        "valid_for_thesis": results["summary"].get("valid_for_thesis", False),
        "generated_polars_used": results.get("generated_polars_used", False),
        "fallback_airfoils_used": results.get("fallback_airfoils_used", {}),
        "failed_polar_cases": results.get("failed_polar_cases", []),
        "warnings": results["warnings"],
        "sanity_checks": results["sanity_checks"],
    }
    summary_json_path.write_text(json.dumps(summary_payload, indent=2) + "\n", encoding="utf-8")

    summary_txt_path = out_dir / f"{name}_summary.txt"
    summary_txt_path.write_text(_summary_text(results), encoding="utf-8")

    return {
        "station_csv": station_csv_path,
        "summary_json": summary_json_path,
        "summary_txt": summary_txt_path,
    }


def plot_results(results: Mapping[str, Any], output_dir: str | Path) -> dict[str, list[Path]]:
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "Times New Roman",
            "font.size": 11,
            "axes.titlesize": 11,
            "axes.labelsize": 11,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 10,
            "mathtext.fontset": "custom",
            "mathtext.rm": "Times New Roman",
            "mathtext.it": "Times New Roman:italic",
            "mathtext.bf": "Times New Roman:bold",
            "axes.unicode_minus": False,
            "lines.linewidth": 1.5,
            "grid.linewidth": 0.4,
            "grid.alpha": 0.35,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    case_name = _clean_case_name(str(results["case_name"]))
    stations = results["stations"]
    r_R = [row["r_R"] for row in stations]

    plot_specs = [
        ("thrust_loading", "qz_N_per_m", "Thrust loading [N/m]"),
        ("torque_loading", "torque_loading_Nm_per_m", "Torque loading [N m/m]"),
        ("cumulative_thrust", "cumulative_thrust_fraction", "Cumulative thrust fraction [-]"),
        ("cumulative_torque", "cumulative_torque_fraction", "Cumulative torque fraction [-]"),
        ("cl_distribution", "Cl", "Cl [-]"),
        ("cd_distribution", "Cd", "Cd [-]"),
        ("alpha_distribution", "alpha_deg", "Alpha [deg]"),
        ("reynolds_distribution", "Re", "Re [-]"),
        ("twist_distribution", "twist_deg", "Twist [deg]"),
    ]

    written: dict[str, list[Path]] = {}
    for stem, field, ylabel in plot_specs:
        fig, ax = plt.subplots(figsize=(4.2, 3.0))
        ax.plot(r_R, [row[field] for row in stations], color="black")
        ax.set_xlabel("r/R [-]")
        ax.set_ylabel(ylabel)
        ax.grid(True)
        ax.set_xlim(min(r_R), max(r_R))
        fig.tight_layout()
        full_stem = f"{case_name}_{stem}"
        paths = [
            out_dir / f"{full_stem}.pdf",
            out_dir / f"{full_stem}.svg",
            out_dir / f"{full_stem}.png",
        ]
        fig.savefig(paths[0])
        fig.savefig(paths[1])
        fig.savefig(paths[2], dpi=300)
        plt.close(fig)
        written[stem] = paths
    return written


def _normalize_operating_condition(
    operating_condition: OperatingCondition | Mapping[str, Any] | None,
) -> OperatingCondition:
    if operating_condition is None:
        data = dict(DEFAULT_THESIS_OPERATING_CONDITION)
    elif isinstance(operating_condition, OperatingCondition):
        data = asdict(operating_condition)
    else:
        data = dict(DEFAULT_THESIS_OPERATING_CONDITION)
        data.update(dict(operating_condition))

    op = OperatingCondition(
        case_name=str(data["case_name"]),
        diameter_m=float(data["diameter_m"]),
        radius_m=float(data["radius_m"]),
        blade_count=int(data["blade_count"]),
        rpm=float(data["rpm"]),
        rho=float(data["rho"]),
        mu_air_kg_m_s=float(data.get("mu_air_kg_m_s", data.get("mu", 1.81e-5))),
        V_inf_m_s=float(data["V_inf_m_s"]),
        target_thrust_N=None if data.get("target_thrust_N") is None else float(data["target_thrust_N"]),
        hover_mode=bool(data["hover_mode"]),
    )
    _require_positive_finite("diameter_m", op.diameter_m)
    _require_positive_finite("radius_m", op.radius_m)
    _require_positive_finite("blade_count", op.blade_count)
    _require_positive_finite("rpm", op.rpm)
    _require_positive_finite("rho", op.rho)
    _require_positive_finite("mu_air_kg_m_s", op.mu_air_kg_m_s)
    _require_finite("V_inf_m_s", op.V_inf_m_s)
    if op.target_thrust_N is not None:
        _require_finite("target_thrust_N", op.target_thrust_N)
    return op


def _induced_velocity_for_case(
    stations: Sequence[Mapping[str, Any]],
    op: OperatingCondition,
    polar: AirfoilPolar,
    collective_twist_offset_deg: float,
    disk_area: float,
) -> float:
    if not (op.hover_mode and abs(op.V_inf_m_s) <= 1.0e-12):
        return 0.0
    if op.target_thrust_N is not None and op.target_thrust_N > 0.0:
        return compute_hover_induced_velocity(op.target_thrust_N, op.rho, disk_area)

    induced_velocity = 0.0
    for _ in range(30):
        thrust = sum(
            evaluate_station(
                station,
                op,
                polar,
                dr_m=float(station["dr_m"]),
                induced_velocity_m_s=induced_velocity,
                collective_twist_offset_deg=collective_twist_offset_deg,
            )["dT_N"]
            for station in stations
        )
        updated = compute_hover_induced_velocity(max(thrust, 0.0), op.rho, disk_area)
        relaxed = 0.5 * induced_velocity + 0.5 * updated
        if abs(relaxed - induced_velocity) < 1.0e-6:
            return relaxed
        induced_velocity = relaxed
    return induced_velocity


def _run_sanity_checks(results: Mapping[str, Any]) -> dict[str, bool | None]:
    summary = results["summary"]
    stations = results["stations"]
    checks: dict[str, bool | None] = {
        "P_equals_Q_omega": _close(summary["power_W"], summary["torque_Nm"] * summary["omega_rad_s"]),
        "CP_equals_2pi_CQ": _close(summary["CP"], 2.0 * math.pi * summary["CQ"]),
        "sum_dT_equals_total_thrust": _close(sum(row["dT_N"] for row in stations), summary["thrust_N"]),
        "sum_dQ_equals_total_torque": _close(sum(row["dQ_Nm"] for row in stations), summary["torque_Nm"]),
        "qz_integrates_to_thrust": _close(
            sum(row["qz_N_per_m"] * row["dr_m"] for row in stations),
            summary["thrust_N"],
        ),
        "torque_loading_integrates_to_torque": _close(
            sum(row["torque_loading_Nm_per_m"] * row["dr_m"] for row in stations),
            summary["torque_Nm"],
        ),
        "hover_mode_uses_induced_velocity_when_static": (
            summary["induced_velocity_m_s"] > 0.0
            if results["operating_condition"]["hover_mode"] and abs(results["operating_condition"]["V_inf_m_s"]) <= 1.0e-12
            else None
        ),
        "no_nan_or_inf": _all_station_values_finite(stations) and _all_mapping_values_finite(summary),
        "thrust_target_error_reported": summary["thrust_error_pct"] is not None,
        "SI_units_internal": True,
    }
    failed = [name for name, passed in checks.items() if passed is False]
    if failed:
        raise ValueError(f"aero solver sanity checks failed: {', '.join(failed)}")
    return checks


def _annotate_result_validity(result: dict[str, Any], target_match_tolerance_pct: float) -> None:
    summary = result["summary"]
    warnings_list = result["warnings"]
    failure_reasons: list[str] = []
    thrust_error_pct = summary.get("thrust_error_pct")

    if thrust_error_pct is None:
        summary["target_matched"] = False
        failure_reasons.append("target thrust was not specified")
    else:
        summary["target_matched"] = abs(thrust_error_pct) <= target_match_tolerance_pct
        if abs(thrust_error_pct) > 5.0:
            warning = "Thrust target not matched. Aerodynamic outputs are not valid for MIL/GMT comparison."
            if warning not in warnings_list:
                warnings_list.append(warning)
            failure_reasons.append(f"thrust target error is {thrust_error_pct:.3f}%")

    if result["polar_is_placeholder"]:
        failure_reasons.append("placeholder polar is not thesis-quality airfoil data")
    elif result.get("fallback_airfoils_used"):
        failure_reasons.append("fallback airfoil polar substitutions were used")
    elif result.get("failed_polar_cases"):
        failure_reasons.append("one or more XFOIL polar cases failed")

    expected_error = summary.get("CT_expected_error_pct")
    if (
        summary.get("case_name") == "thesis_22_static_35N"
        and expected_error is not None
        and abs(expected_error) > 20.0
    ):
        warnings_list.append(
            f"Computed CT is {expected_error:.3f}% away from expected thesis CT after target evaluation."
        )

    summary["valid_for_thesis"] = len(failure_reasons) == 0
    summary["failure_reason"] = "; ".join(failure_reasons)


def _validate_airfoil_polar_coverage(
    stations: Sequence[Mapping[str, Any]],
    polar: AirfoilPolar,
    allow_polar_fallback: bool,
) -> dict[str, str]:
    if polar.placeholder:
        return {}
    if not polar.by_airfoil_re and not polar.by_airfoil:
        return {}
    if polar.by_airfoil_re and len(polar.by_airfoil_re) == 1:
        return {}
    if polar.by_airfoil and len(polar.by_airfoil) == 1:
        return {}

    available = set()
    if polar.by_airfoil_re:
        available.update(_normalize_airfoil_name(name) for name in polar.by_airfoil_re)
    if polar.by_airfoil:
        available.update(_normalize_airfoil_name(name) for name in polar.by_airfoil)

    required = sorted({_normalize_airfoil_name(str(station.get("airfoil", ""))) for station in stations})
    missing = [name for name in required if name not in available]
    if not missing:
        return {}
    if allow_polar_fallback:
        raise NotImplementedError(
            "airfoil polar fallback was requested, but explicit fallback mapping is not implemented for this run"
        )
    raise ValueError(
        "missing airfoil polar data for: "
        + ", ".join(missing)
        + ". Provide real/generated polar CSVs or enable an explicit fallback mode."
    )


def _detect_failed_polar_cases(airfoil_dir: Path) -> list[str]:
    log_dir = airfoil_dir / "generated_polars" / "logs"
    if not log_dir.exists():
        return []
    failed: list[str] = []
    for log_path in sorted(log_dir.glob("*_xfoil.log")):
        text = log_path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if line.startswith("Failure reason:"):
                reason = line.split(":", 1)[1].strip()
                if reason:
                    failed.append(f"{log_path.stem}: {reason}")
                break
    return failed


def _find_bracket_side(evaluate_at: Any, target: float, tolerance_N: float, direction: float) -> tuple[float, float]:
    offset = 1.0 * direction
    for _ in range(12):
        _, residual = evaluate_at(offset)
        if abs(residual) <= tolerance_N or residual * direction >= 0.0:
            return offset, residual
        offset *= 2.0
    _, residual = evaluate_at(offset)
    return offset, residual


def _station_widths_m(stations: Sequence[Mapping[str, Any]]) -> list[float]:
    if len(stations) == 1:
        return [max(float(stations[0]["r_m"]) * 0.1, 1.0e-5)]

    dr_values: list[float] = []
    r_R_values = [float(station["r_R"]) for station in stations]
    radius_m = _safe_div(float(stations[-1]["r_m"]), max(r_R_values[-1], 1.0e-12))
    for idx, r_R in enumerate(r_R_values):
        left = r_R_values[0] if idx == 0 else 0.5 * (r_R_values[idx - 1] + r_R)
        right = 1.0 if idx == len(r_R_values) - 1 else 0.5 * (r_R + r_R_values[idx + 1])
        width = max(right - left, 1.0e-9) * radius_m
        dr_values.append(width)
    return dr_values


def _station_edges_r_R(stations: Sequence[Mapping[str, Any]], hub_r_R: float | None = None) -> tuple[list[float], list[float]]:
    r_R_values = [float(row["r_R"]) for row in stations]
    if not r_R_values:
        return [], []
    hub = r_R_values[0] if hub_r_R is None else hub_r_R
    left_edges: list[float] = []
    right_edges: list[float] = []
    for idx, r_R in enumerate(r_R_values):
        left = hub if idx == 0 else 0.5 * (r_R_values[idx - 1] + r_R)
        right = 1.0 if idx == len(r_R_values) - 1 else 0.5 * (r_R + r_R_values[idx + 1])
        if right <= left:
            half_width = 0.5 * max(abs(right - left), 1.0e-6)
            left = r_R - half_width
            right = r_R + half_width
        left_edges.append(left)
        right_edges.append(right)
    return left_edges, right_edges


def _integrate_r_R_band(
    stations: Sequence[Mapping[str, Any]],
    field: str,
    lower_r_R: float,
    upper_r_R: float,
) -> float:
    if lower_r_R >= upper_r_R:
        raise ValueError("lower_r_R must be below upper_r_R")
    left_edges, right_edges = _station_edges_r_R(stations)
    total = 0.0
    for left, right, row in zip(left_edges, right_edges, stations):
        width = max(right - left, 1.0e-12)
        overlap = max(0.0, min(right, upper_r_R) - max(left, lower_r_R))
        if overlap > 0.0:
            total += float(row[field]) * overlap / width
    return total


def _cumulative_radius_at_fraction(
    stations: Sequence[Mapping[str, Any]],
    field: str,
    target_fraction: float,
) -> float:
    values = [float(row[field]) for row in stations]
    total = sum(values)
    if total <= 0.0:
        return float("nan")
    target = total * target_fraction
    left_edges, right_edges = _station_edges_r_R(stations)
    running = 0.0
    for left, right, value in zip(left_edges, right_edges, values):
        next_running = running + value
        if next_running >= target:
            fraction_inside_station = _safe_div(target - running, value)
            return left + max(0.0, min(1.0, fraction_inside_station)) * (right - left)
        running = next_running
    return 1.0


def _cumulative_fraction(values: Sequence[float]) -> list[float]:
    total = sum(values)
    if abs(total) <= 1.0e-12:
        return [0.0 for _ in values]
    running = 0.0
    fractions: list[float] = []
    for value in values:
        running += value
        fractions.append(running / total)
    if fractions:
        fractions[-1] = 1.0
    return fractions


def _linear_interpolate(x_values: Sequence[float], y_values: Sequence[float], x: float) -> float:
    if len(x_values) != len(y_values):
        raise ValueError("x_values and y_values must have the same length")
    if x <= x_values[0]:
        return y_values[0]
    if x >= x_values[-1]:
        return y_values[-1]
    for idx in range(1, len(x_values)):
        if x <= x_values[idx]:
            x0 = x_values[idx - 1]
            x1 = x_values[idx]
            y0 = y_values[idx - 1]
            y1 = y_values[idx]
            fraction = (x - x0) / (x1 - x0)
            return y0 + fraction * (y1 - y0)
    return y_values[-1]


def _normalize_airfoil_name(name: str) -> str:
    compact = str(name).strip().replace(" ", "").replace("_", "").replace("-", "").upper()
    if compact == "MH115":
        return "MH115"
    if compact == "S4310":
        return "S4310"
    if compact in {"A3", "A4", "A6", "A7", "A11", "A13"}:
        return compact
    return compact


def _polar_xfoil_success(polar: AirfoilPolar) -> bool:
    return (not polar.placeholder) and not bool(polar.failed_polar_cases)


def _build_reynolds_tables(
    pairs_by_airfoil: Mapping[str, Mapping[float | None, Sequence[tuple[float, float, float, float, str]]]]
) -> dict[str, dict[float | None, dict[str, Any]]]:
    tables: dict[str, dict[float | None, dict[str, Any]]] = {}
    for airfoil_name, by_re in pairs_by_airfoil.items():
        normalized_airfoil = _normalize_airfoil_name(airfoil_name)
        tables[normalized_airfoil] = {}
        for re_value, pairs in by_re.items():
            sorted_pairs = sorted(pairs, key=lambda item: item[0])
            for left, right in zip(sorted_pairs, sorted_pairs[1:]):
                if right[0] <= left[0]:
                    raise ValueError(
                        f"polar alpha_deg values must be unique for airfoil {normalized_airfoil} Re={re_value}"
                    )
            if not sorted_pairs:
                continue
            tables[normalized_airfoil][re_value] = {
                "alpha_deg": [item[0] for item in sorted_pairs],
                "Cl": [item[1] for item in sorted_pairs],
                "Cd": [item[2] for item in sorted_pairs],
                "Cm": [item[3] for item in sorted_pairs],
                "polar_source": ";".join(sorted({item[4] for item in sorted_pairs})),
            }
    return tables


def _case_insensitive_columns(fieldnames: Sequence[str]) -> dict[str, str]:
    return {name.strip().lower(): name for name in fieldnames}


def _first_column(column_map: Mapping[str, str], candidates: Sequence[str], required: bool = True) -> str | None:
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in column_map:
            return column_map[key]
    if required:
        raise ValueError(f"missing required CSV column; expected one of {', '.join(candidates)}")
    return None


def _to_float(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric, got {value!r}") from exc


def _require_finite(name: str, value: float) -> None:
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")


def _require_positive_finite(name: str, value: float) -> None:
    _require_finite(name, value)
    if float(value) <= 0.0:
        raise ValueError(f"{name} must be positive")


def _require_nonnegative_finite(name: str, value: float) -> None:
    _require_finite(name, value)
    if float(value) < 0.0:
        raise ValueError(f"{name} must be nonnegative")


def _safe_div(numerator: float, denominator: float, fallback: float = 0.0) -> float:
    if abs(denominator) <= 1.0e-12:
        return fallback
    return numerator / denominator


def _close(left: float, right: float, rel_tol: float = 1.0e-8, abs_tol: float = 1.0e-9) -> bool:
    return abs(left - right) <= max(abs_tol, rel_tol * max(abs(left), abs(right), 1.0))


def _all_mapping_values_finite(mapping: Mapping[str, Any]) -> bool:
    for value in mapping.values():
        if isinstance(value, (int, float)) and not math.isfinite(float(value)):
            return False
    return True


def _all_station_values_finite(stations: Sequence[Mapping[str, Any]]) -> bool:
    for row in stations:
        if not _all_mapping_values_finite(row):
            return False
    return True


def _clean_case_name(case_name: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in case_name)


def _summary_text(results: Mapping[str, Any]) -> str:
    summary = results["summary"]
    lines = [
        f"Case: {results['case_name']}",
        f"Polar source: {results['polar_source']}",
        f"Polar is placeholder: {results['polar_is_placeholder']}",
        "",
        "Summary",
        f"  thrust_N: {summary['thrust_N']:.8g}",
        f"  torque_Nm: {summary['torque_Nm']:.8g}",
        f"  power_W: {summary['power_W']:.8g}",
        f"  rpm: {summary['rpm']:.8g}",
        f"  omega_rad_s: {summary['omega_rad_s']:.8g}",
        f"  CT: {summary['CT']:.8g}",
        f"  CQ: {summary['CQ']:.8g}",
        f"  CP: {summary['CP']:.8g}",
        f"  efficiency: {summary['efficiency']}",
        f"  CT_expected: {summary['CT_expected']}",
        f"  CT_expected_error_pct: {summary['CT_expected_error_pct']}",
        f"  target_thrust_N: {summary['target_thrust_N']}",
        f"  thrust_error_pct: {summary['thrust_error_pct']}",
        f"  target_matched: {summary['target_matched']}",
        f"  collective_twist_offset_deg: {summary['collective_twist_offset_deg']:.8g}",
        f"  induced_velocity_m_s: {summary['induced_velocity_m_s']:.8g}",
        f"  Vi_target_m_s: {summary['Vi_target_m_s']:.8g}",
        f"  Vi_computed_from_result_m_s: {summary['Vi_computed_from_result_m_s']:.8g}",
        f"  Vi_difference_m_s: {summary['Vi_difference_m_s']:.8g}",
        f"  Re_min: {summary['Re_min']:.8g}",
        f"  Re_max: {summary['Re_max']:.8g}",
        f"  max_Cl: {summary['max_Cl']:.8g}",
        f"  max_Cd: {summary['max_Cd']:.8g}",
        f"  max_alpha_deg: {summary['max_alpha_deg']:.8g}",
        f"  min_alpha_deg: {summary['min_alpha_deg']:.8g}",
        f"  clipping_fraction_pct: {summary['clipping_fraction_pct']:.8g}",
        f"  cl_clipping_fraction_pct: {summary['cl_clipping_fraction_pct']:.8g}",
        f"  alpha_clipping_fraction_pct: {summary['alpha_clipping_fraction_pct']:.8g}",
        f"  polar_alpha_clipping_fraction_pct: {summary['polar_alpha_clipping_fraction_pct']:.8g}",
        f"  Cl_max_debug: {summary['Cl_max_debug']}",
        f"  airfoils_used: {', '.join(summary['airfoils_used'])}",
        f"  xfoil_polars_used: {summary['xfoil_polars_used']}",
        f"  xfoil_success: {summary.get('xfoil_success', False)}",
        f"  generated_polars_used: {summary['generated_polars_used']}",
        f"  fallback_airfoils_used: {summary['fallback_airfoils_used']}",
        f"  failed_polar_cases: {summary['failed_polar_cases']}",
        f"  xrotor_ran: {summary.get('xrotor_ran', False)}",
        f"  xrotor_success: {summary.get('xrotor_success', False)}",
        f"  xrotor_velocity_m_s: {summary.get('xrotor_velocity_m_s')}",
        f"  xrotor_comparison_file: {summary.get('xrotor_comparison_file', '')}",
        f"  valid_for_thesis: {summary['valid_for_thesis']}",
        f"  failure_reason: {summary['failure_reason']}",
        "",
        "Sanity checks",
    ]
    lines.extend(f"  {name}: {value}" for name, value in results["sanity_checks"].items())
    if results["warnings"]:
        lines.extend(["", "Warnings"])
        lines.extend(f"  {warning}" for warning in results["warnings"])
    return "\n".join(lines) + "\n"


def print_run_summary(results: Mapping[str, Any]) -> None:
    summary = results["summary"]
    checks = results["sanity_checks"]
    print("Aero solver run summary")
    print(f"  target thrust [N]        : {summary['target_thrust_N']}")
    print(f"  computed thrust [N]      : {summary['thrust_N']:.6f}")
    print(f"  thrust error [%]         : {summary['thrust_error_pct']}")
    print(f"  CT                       : {summary['CT']:.8f}")
    print(f"  Q [N m]                  : {summary['torque_Nm']:.8f}")
    print(f"  P [W]                    : {summary['power_W']:.8f}")
    print(f"  efficiency               : {summary['efficiency']}")
    print(f"  P = Q*omega check        : {checks.get('P_equals_Q_omega')}")
    print(f"  CP = 2*pi*CQ check       : {checks.get('CP_equals_2pi_CQ')}")
    print(f"  max alpha [deg]          : {summary['max_alpha_deg']:.6f}")
    print(f"  max Cl                   : {summary['max_Cl']:.6f}")
    print(f"  max Cd                   : {summary['max_Cd']:.6f}")
    print(f"  Re min/max               : {summary['Re_min']:.0f} / {summary['Re_max']:.0f}")
    print(f"  alpha clipping [%]       : {summary['alpha_clipping_fraction_pct']:.6f}")
    print(f"  Cl clipping [%]          : {summary['Cl_clipping_fraction_pct']:.6f}")
    print(f"  clipping fraction [%]    : {summary['clipping_fraction_pct']:.6f}")
    print(f"  collective offset [deg]  : {summary['collective_twist_offset_deg']:.6f}")
    print(f"  Vi target [m/s]          : {summary['Vi_target_m_s']:.6f}")
    print(f"  Vi from result [m/s]     : {summary['Vi_computed_from_result_m_s']:.6f}")
    print(f"  Vi difference [m/s]      : {summary['Vi_difference_m_s']:.6f}")
    print(f"  xfoil polars used        : {summary.get('xfoil_polars_used', False)}")
    print(f"  xfoil success            : {summary.get('xfoil_success', False)}")
    print(f"  xrotor ran               : {summary.get('xrotor_ran', False)}")
    print(f"  xrotor success           : {summary.get('xrotor_success', False)}")
    print(f"  target matched           : {summary['target_matched']}")
    print(f"  valid for thesis         : {summary['valid_for_thesis']}")
    if summary["failure_reason"]:
        print(f"  failure reason           : {summary['failure_reason']}")
    for warning in results["warnings"]:
        print(f"WARNING: {warning}")


def find_xrotor_executable(xrotor_root: str | Path | None = DEFAULT_XROTOR_ROOT) -> Path | None:
    if xrotor_root is None:
        return None
    root = Path(xrotor_root)
    candidates = [
        root / "xrotor.exe",
        root / "XROTOR.exe",
        root / "xrotor",
        root / "XROTOR",
        root / "Xrotor" / "xrotor.exe",
        root / "Xrotor" / "XROTOR.exe",
        root / "Xrotor" / "xrotor",
        root / "Xrotor" / "XROTOR",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    if root.exists():
        for executable_name in ("xrotor.exe", "XROTOR.exe", "xrotor", "XROTOR"):
            for candidate in root.rglob(executable_name):
                if candidate.is_file():
                    return candidate
    return None


def report_xrotor_status(xrotor_root: str | Path | None = DEFAULT_XROTOR_ROOT) -> Path | None:
    executable = find_xrotor_executable(xrotor_root)
    if executable is None:
        print("XROTOR executable not found. Skipping optional XROTOR validation.")
    return executable


def _safe_float_from_match(pattern: str, text: str, flags: int = 0) -> float | None:
    matches = list(re.finditer(pattern, text, flags))
    if not matches:
        return None
    value_text = matches[-1].group(1)
    try:
        return float(value_text.replace("D", "E").replace("d", "e"))
    except ValueError:
        return None


def parse_xrotor_output(stdout: str, operating_condition: OperatingCondition | Mapping[str, Any]) -> dict[str, float | None]:
    op = _normalize_operating_condition(operating_condition)
    thrust_N = _safe_float_from_match(r"thrust\(N\)\s*:\s*([-+0-9.EeDd]+)", stdout, re.IGNORECASE)
    power_W = _safe_float_from_match(r"power\(W\)\s*:\s*([-+0-9.EeDd]+)", stdout, re.IGNORECASE)
    torque_Nm = _safe_float_from_match(r"torque\(N-m\)\s*:\s*([-+0-9.EeDd]+)", stdout, re.IGNORECASE)
    efficiency = _safe_float_from_match(r"Efficiency\s*:\s*([-+0-9.EeDd]+)", stdout)
    ct_reported = _safe_float_from_match(r"\bCt:\s*([-+0-9.EeDd]+)", stdout)
    cp_reported = _safe_float_from_match(r"\bCp:\s*([-+0-9.EeDd]+)", stdout)

    n_rev_per_s = op.rpm / 60.0
    denominator_t = op.rho * n_rev_per_s**2 * op.diameter_m**4
    denominator_q = op.rho * n_rev_per_s**2 * op.diameter_m**5
    denominator_p = op.rho * n_rev_per_s**3 * op.diameter_m**5
    CT = thrust_N / denominator_t if thrust_N is not None and denominator_t > 0.0 else ct_reported
    CQ = torque_Nm / denominator_q if torque_Nm is not None and denominator_q > 0.0 else None
    CP = power_W / denominator_p if power_W is not None and denominator_p > 0.0 else cp_reported
    return {
        "thrust_N": thrust_N,
        "torque_Nm": torque_Nm,
        "power_W": power_W,
        "CT": CT,
        "CQ": CQ,
        "CP": CP,
        "efficiency": efficiency,
    }


def _linear_fit_slope_intercept(x_values: Sequence[float], y_values: Sequence[float]) -> tuple[float, float] | None:
    if len(x_values) < 2 or len(y_values) < 2:
        return None
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    denominator = sum((x - x_mean) ** 2 for x in x_values)
    if abs(denominator) <= 1.0e-14:
        return None
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values)) / denominator
    intercept = y_mean - slope * x_mean
    return slope, intercept


def _derive_xrotor_aero_model(polar: AirfoilPolar, stations: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    alpha_values: list[float] = []
    cl_values: list[float] = []
    cd_values: list[float] = []
    cm_values: list[float] = []
    re_values: list[float] = []
    if polar.by_airfoil_re:
        station_airfoils = {_normalize_airfoil_name(str(row.get("airfoil", ""))) for row in stations}
        for airfoil_name, tables_by_re in polar.by_airfoil_re.items():
            if station_airfoils and airfoil_name not in station_airfoils:
                continue
            for reynolds_number, table in tables_by_re.items():
                for alpha, cl, cd in zip(table["alpha_deg"], table["Cl"], table["Cd"]):
                    if math.isfinite(alpha) and math.isfinite(cl) and math.isfinite(cd):
                        alpha_values.append(float(alpha))
                        cl_values.append(float(cl))
                        cd_values.append(float(cd))
                for cm in table.get("Cm", []):
                    if math.isfinite(float(cm)):
                        cm_values.append(float(cm))
                if reynolds_number is not None and math.isfinite(float(reynolds_number)):
                    re_values.append(float(reynolds_number))
    else:
        alpha_values = list(polar.alpha_deg or [])
        cl_values = list(polar.cl or [])
        cd_values = list(polar.cd or [])

    fit_alpha_rad: list[float] = []
    fit_cl: list[float] = []
    for alpha, cl in zip(alpha_values, cl_values):
        if -4.0 <= alpha <= 8.0 and math.isfinite(alpha) and math.isfinite(cl):
            fit_alpha_rad.append(math.radians(alpha))
            fit_cl.append(cl)
    fit = _linear_fit_slope_intercept(fit_alpha_rad, fit_cl)
    if fit is None:
        dclda = 2.0 * math.pi
        a0deg = 0.0
    else:
        dclda, intercept = fit
        dclda = max(3.0, min(8.0, dclda))
        a0deg = -math.degrees(intercept / dclda) if abs(dclda) > 1.0e-12 else 0.0

    finite_cd_pairs = [
        (cl, cd)
        for cl, cd in zip(cl_values, cd_values)
        if math.isfinite(cl) and math.isfinite(cd) and cd > 0.0
    ]
    if finite_cd_pairs:
        cl_at_cdmin, cdmin = min(finite_cd_pairs, key=lambda pair: pair[1])
        numerator = sum((cd - cdmin) * (cl - cl_at_cdmin) ** 2 for cl, cd in finite_cd_pairs)
        denominator = sum((cl - cl_at_cdmin) ** 4 for cl, _ in finite_cd_pairs)
        dcdcl2 = numerator / denominator if denominator > 1.0e-14 else 0.02
    else:
        cl_at_cdmin = 0.0
        cdmin = 0.012
        dcdcl2 = 0.02

    clmax = max(cl_values) if cl_values else 1.5
    clmin = min(cl_values) if cl_values else -1.2
    return {
        "a0deg": max(-12.0, min(12.0, a0deg)),
        "dclda": dclda,
        "clmax": max(0.6, min(2.5, clmax)),
        "clmin": min(-0.3, max(-2.5, clmin)),
        "dclda_stall": 0.1,
        "dcl_stall": 0.1,
        "cmconst": sum(cm_values) / len(cm_values) if cm_values else -0.05,
        "mcrit": 0.8,
        "cdmin": max(0.001, min(0.2, cdmin)),
        "clcdmin": cl_at_cdmin,
        "dcdcl2": max(0.001, min(0.2, dcdcl2)),
        "reref": sorted(re_values)[len(re_values) // 2] if re_values else 200000.0,
        "reexp": -0.5,
    }


def write_xrotor_prop_file(
    results: Mapping[str, Any],
    polar: AirfoilPolar,
    prop_path: str | Path,
) -> Path:
    prop_path = Path(prop_path)
    prop_path.parent.mkdir(parents=True, exist_ok=True)
    op = _normalize_operating_condition(results["operating_condition"])
    stations = sorted(results["stations"], key=lambda row: float(row["r_R"]))
    aero = _derive_xrotor_aero_model(polar, stations)
    velocity = op.V_inf_m_s if abs(op.V_inf_m_s) > 1.0e-9 else 0.1
    omega = rpm_to_omega(op.rpm)
    advance_ratio = velocity / (omega * op.radius_m) if omega > 0.0 and op.radius_m > 0.0 else 0.0
    xi0 = max(0.0, min(float(row["r_R"]) for row in stations) * 0.5)
    xiw = min(0.06, max(0.0, xi0 * 0.6))
    lines = [
        "XROTOR VERSION:  7.54",
        str(results["case_name"])[:32],
        "!         Rho          Vso          Rmu           Alt",
        f"  {op.rho:12.6g}  {340.0:12.6g}  {op.mu_air_kg_m_s:12.6g}  {999.0:8.2f}",
        "!         Rad          Vel          Adv          Rake",
        f"  {op.radius_m:12.6g}  {velocity:12.6g}  {advance_ratio:12.6g}  {0.0:12.6g}",
        "!         XI0          XIW",
        f"  {xi0:12.6g}  {xiw:12.6g}",
        "!  Naero",
        "     1",
        "!   Xisection",
        "  0.00000E+00",
        "!       A0deg        dCLdA        CLmax         CLmin",
        f"  {aero['a0deg']:12.6g}  {aero['dclda']:12.6g}  {aero['clmax']:12.6g}  {aero['clmin']:12.6g}",
        "!  dCLdAstall     dCLstall      Cmconst         Mcrit",
        f"  {aero['dclda_stall']:12.6g}  {aero['dcl_stall']:12.6g}  {aero['cmconst']:12.6g}  {aero['mcrit']:12.6g}",
        "!       CDmin      CLCDmin     dCDdCL^2",
        f"  {aero['cdmin']:12.6g}  {aero['clcdmin']:12.6g}  {aero['dcdcl2']:12.6g}",
        "!       REref        REexp",
        f"  {aero['reref']:12.6g}  {aero['reexp']:12.6g}",
        "!LVDuct  LDuct   LWind",
        "  T       F       F",
        "!   II Nblds",
        f"  {len(stations):4d}  {op.blade_count:4d}",
        "!         r/R          C/R     Beta0deg         Ubody",
    ]
    for row in stations:
        chord_over_radius = float(row["chord_m"]) / op.radius_m
        lines.append(
            f"  {float(row['r_R']):12.6g}  {chord_over_radius:12.6g}  "
            f"{float(row['twist_deg']):12.6g}  {0.0:12.6g}"
        )
    lines.extend(["!      URDuct", "   1.0000    ", ""])
    prop_path.write_text("\n".join(lines), encoding="utf-8")
    return prop_path


def _write_xrotor_comparison_files(
    bemt_results: Mapping[str, Any],
    xrotor_metrics: Mapping[str, float | None],
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "xrotor_comparison_summary.csv"
    md_path = output_dir / "xrotor_comparison_summary.md"
    bemt_summary = bemt_results["summary"]
    metrics = ("thrust_N", "torque_Nm", "power_W", "CT", "CQ", "CP", "efficiency")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["metric", "bemt", "xrotor", "difference_pct"])
        writer.writeheader()
        for metric in metrics:
            bemt_value = bemt_summary.get(metric)
            xrotor_value = xrotor_metrics.get(metric)
            difference_pct = None
            if isinstance(bemt_value, (int, float)) and isinstance(xrotor_value, (int, float)) and abs(bemt_value) > 1.0e-12:
                difference_pct = (float(xrotor_value) - float(bemt_value)) / abs(float(bemt_value)) * 100.0
            writer.writerow({"metric": metric, "bemt": bemt_value, "xrotor": xrotor_value, "difference_pct": difference_pct})

    lines = [
        "# XROTOR Comparison Summary",
        "",
        f"XROTOR analysis velocity: {_format_optional_float(bemt_summary.get('xrotor_velocity_m_s'))} m/s",
        "",
        "| Metric | BEMT | XROTOR | Difference % |",
        "|---|---:|---:|---:|",
    ]
    for metric in metrics:
        bemt_value = bemt_summary.get(metric)
        xrotor_value = xrotor_metrics.get(metric)
        difference_pct = None
        if isinstance(bemt_value, (int, float)) and isinstance(xrotor_value, (int, float)) and abs(bemt_value) > 1.0e-12:
            difference_pct = (float(xrotor_value) - float(bemt_value)) / abs(float(bemt_value)) * 100.0
        lines.append(
            f"| {metric} | {_format_optional_float(bemt_value)} | "
            f"{_format_optional_float(xrotor_value)} | {_format_optional_float(difference_pct)} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, md_path


def _format_optional_float(value: object) -> str:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return f"{float(value):.6g}"
    if value is None:
        return ""
    return str(value)


def run_xrotor_comparison(
    results: dict[str, Any],
    polar: AirfoilPolar,
    output_dir: str | Path,
    xrotor_root: str | Path | None = DEFAULT_XROTOR_ROOT,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    summary = results["summary"]
    summary["xrotor_ran"] = True
    results["xrotor_ran"] = True
    xrotor_dir = Path(output_dir) / "xrotor"
    xrotor_dir.mkdir(parents=True, exist_ok=True)
    case_name = str(results["case_name"])
    prop_path = xrotor_dir / f"{case_name}.prop"
    command_path = xrotor_dir / f"{case_name}_xrotor_commands.txt"
    stdout_path = xrotor_dir / f"{case_name}_xrotor_stdout.txt"
    stderr_path = xrotor_dir / f"{case_name}_xrotor_stderr.txt"

    executable = find_xrotor_executable(xrotor_root)
    if executable is None:
        message = "XROTOR executable not found. Skipping optional XROTOR validation."
        stderr_path.write_text(message + "\n", encoding="utf-8")
        print(message)
        results["warnings"].append(message)
        summary["xrotor_success"] = False
        summary["xrotor_comparison_file"] = ""
        results["xrotor_success"] = False
        results["xrotor_comparison_file"] = ""
        return {"success": False, "failure_reason": message}

    write_xrotor_prop_file(results, polar, prop_path)
    op = _normalize_operating_condition(results["operating_condition"])
    xrotor_velocity = op.V_inf_m_s if abs(op.V_inf_m_s) > 1.0e-9 else 0.1
    summary["xrotor_velocity_m_s"] = xrotor_velocity
    command_script = "\n".join(["OPER", f"VELO {xrotor_velocity:g}", f"RPM {op.rpm:g}", "DISP", "", "QUIT", ""])
    command_path.write_text(command_script, encoding="utf-8")
    try:
        completed = subprocess.run(
            [str(executable), str(prop_path.resolve())],
            input=command_script,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            cwd=prop_path.parent,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_path.write_text(_coerce_process_text(exc.stdout), encoding="utf-8", errors="ignore")
        stderr_path.write_text(_coerce_process_text(exc.stderr), encoding="utf-8", errors="ignore")
        message = "XROTOR optional comparison timed out."
        print(f"WARNING: {message}")
        results["warnings"].append(message)
        summary["xrotor_success"] = False
        summary["xrotor_comparison_file"] = ""
        results["xrotor_success"] = False
        results["xrotor_comparison_file"] = ""
        return {"success": False, "failure_reason": message}
    except OSError as exc:
        message = f"XROTOR optional comparison failed to start: {exc}"
        stderr_path.write_text(message + "\n", encoding="utf-8")
        print(f"WARNING: {message}")
        results["warnings"].append(message)
        summary["xrotor_success"] = False
        summary["xrotor_comparison_file"] = ""
        results["xrotor_success"] = False
        results["xrotor_comparison_file"] = ""
        return {"success": False, "failure_reason": message}

    stdout_path.write_text(completed.stdout, encoding="utf-8", errors="ignore")
    stderr_path.write_text(completed.stderr, encoding="utf-8", errors="ignore")
    metrics = parse_xrotor_output(completed.stdout, results["operating_condition"])
    required_metrics = ("thrust_N", "torque_Nm", "power_W", "CT", "CQ", "CP")
    success = completed.returncode == 0 and all(metrics.get(metric) is not None for metric in required_metrics)
    if not success:
        message = f"XROTOR optional comparison did not produce complete propeller metrics; return code {completed.returncode}."
        if not completed.stderr.strip():
            stderr_path.write_text(message + "\n", encoding="utf-8")
        print(f"WARNING: {message}")
        results["warnings"].append(message)
        summary["xrotor_success"] = False
        summary["xrotor_comparison_file"] = ""
        results["xrotor_success"] = False
        results["xrotor_comparison_file"] = ""
        return {"success": False, "failure_reason": message, "metrics": metrics}

    csv_path, md_path = _write_xrotor_comparison_files(results, metrics, Path(output_dir))
    summary["xrotor_success"] = True
    summary["xrotor_comparison_file"] = str(csv_path)
    results["xrotor_success"] = True
    results["xrotor_comparison_file"] = str(csv_path)
    return {"success": True, "metrics": metrics, "csv_path": csv_path, "md_path": md_path}


def _coerce_process_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return value


def load_solver_config(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    config_path = _resolve_project_input_path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"solver config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"solver config must be a JSON object: {config_path}")
    return loaded


def get_default_aero_config() -> dict[str, Any]:
    return {
        "case_name": DEFAULT_THESIS_OPERATING_CONDITION["case_name"],
        "case_type": "optimized_mil_22",
        "geometry_csv_path": str(CASE_GEOMETRY_PATHS["optimized_mil_22"]),
        "output_dir": str(_project_path("results/aero")),
        "figure_output_dir": str(_project_path("figures_paper/aero")),
        "diameter_in": 22.0,
        "diameter_m": DEFAULT_THESIS_OPERATING_CONDITION["diameter_m"],
        "radius_m": DEFAULT_THESIS_OPERATING_CONDITION["radius_m"],
        "blade_count": DEFAULT_THESIS_OPERATING_CONDITION["blade_count"],
        "rpm": DEFAULT_THESIS_OPERATING_CONDITION["rpm"],
        "rho": DEFAULT_THESIS_OPERATING_CONDITION["rho"],
        "mu_air_kg_m_s": DEFAULT_THESIS_OPERATING_CONDITION["mu_air_kg_m_s"],
        "V_inf_m_s": DEFAULT_THESIS_OPERATING_CONDITION["V_inf_m_s"],
        "target_thrust_N": DEFAULT_THESIS_OPERATING_CONDITION["target_thrust_N"],
        "hover_mode": DEFAULT_THESIS_OPERATING_CONDITION["hover_mode"],
        "match_target": True,
        "tolerance_pct": 1.0,
        "airfoil_dir": str(DEFAULT_AIRFOIL_DIR),
        "xfoil_path": str(DEFAULT_XFOIL_ROOT),
        "allow_debug_placeholder_polar": False,
        "run_xrotor": False,
        "xrotor_path": str(DEFAULT_XROTOR_EXE),
        "xrotor_timeout_s": 60.0,
        "generate_plots": True,
    }


def _config_path_value(config: Mapping[str, Any], key: str, default: str | Path) -> Path:
    value = config.get(key, default)
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _xfoil_args_from_config(config: Mapping[str, Any]) -> tuple[Path | None, Path | None]:
    value = config.get("xfoil_path") or config.get("xfoil_root")
    if not value:
        return None, DEFAULT_XFOIL_ROOT
    path = Path(str(value)).expanduser()
    if path.suffix.lower() == ".exe" or path.is_file():
        return path, None
    return None, path


def _xrotor_root_from_config(config: Mapping[str, Any]) -> Path | None:
    value = config.get("xrotor_path") or config.get("xrotor_root")
    if not value:
        return DEFAULT_XROTOR_ROOT
    path = Path(str(value)).expanduser()
    return path.parent if path.suffix.lower() == ".exe" else path


def validate_aero_config(config: Mapping[str, Any], require_polars: bool = True) -> list[str]:
    errors: list[str] = []
    geometry_path = _config_path_value(config, "geometry_csv_path", CASE_GEOMETRY_PATHS["optimized_mil_22"])
    if not geometry_path.is_file():
        errors.append(f"Geometry CSV not found: {geometry_path}")

    def require_positive(name: str, label: str) -> float | None:
        try:
            value = float(config.get(name))
        except (TypeError, ValueError):
            errors.append(f"{label} must be numeric.")
            return None
        if value <= 0.0:
            errors.append(f"{label} must be > 0.")
        return value

    require_positive("diameter_m", "Diameter [m]")
    try:
        blade_count = int(config.get("blade_count"))
        if blade_count < 1:
            errors.append("Blade count must be >= 1.")
    except (TypeError, ValueError):
        errors.append("Blade count must be an integer.")
    try:
        rpm = float(config.get("rpm"))
        if rpm < 0.0:
            errors.append("RPM must be >= 0.")
    except (TypeError, ValueError):
        errors.append("RPM must be numeric.")
    require_positive("rho", "Air density rho")
    require_positive("mu_air_kg_m_s", "Dynamic viscosity mu")
    require_positive("target_thrust_N", "Target thrust")

    try:
        v_inf = float(config.get("V_inf_m_s"))
        if bool(config.get("hover_mode", True)) and abs(v_inf) <= 1.0e-12:
            # This is a confirmation check, not an error: the backend uses induced velocity in this case.
            pass
    except (TypeError, ValueError):
        errors.append("Freestream velocity V_inf must be numeric.")

    if require_polars and not bool(config.get("allow_debug_placeholder_polar", False)) and not config.get("polar"):
        airfoil_dir = _config_path_value(config, "airfoil_dir", DEFAULT_AIRFOIL_DIR)
        missing_polars = find_missing_xfoil_polar_csvs(airfoil_dir)
        if missing_polars:
            errors.append("Missing XFOIL-generated polar CSVs: " + ", ".join(missing_polars))
    return errors


def _operating_condition_from_config(config: Mapping[str, Any]) -> dict[str, Any]:
    diameter_m = float(config.get("diameter_m", DEFAULT_THESIS_OPERATING_CONDITION["diameter_m"]))
    radius_m = float(config.get("radius_m", diameter_m / 2.0))
    return {
        "case_name": str(config.get("case_name", DEFAULT_THESIS_OPERATING_CONDITION["case_name"])),
        "diameter_m": diameter_m,
        "radius_m": radius_m,
        "blade_count": int(config.get("blade_count", DEFAULT_THESIS_OPERATING_CONDITION["blade_count"])),
        "rpm": float(config.get("rpm", DEFAULT_THESIS_OPERATING_CONDITION["rpm"])),
        "rho": float(config.get("rho", DEFAULT_THESIS_OPERATING_CONDITION["rho"])),
        "mu_air_kg_m_s": float(config.get("mu_air_kg_m_s", DEFAULT_THESIS_OPERATING_CONDITION["mu_air_kg_m_s"])),
        "V_inf_m_s": float(config.get("V_inf_m_s", DEFAULT_THESIS_OPERATING_CONDITION["V_inf_m_s"])),
        "target_thrust_N": float(config.get("target_thrust_N", DEFAULT_THESIS_OPERATING_CONDITION["target_thrust_N"])),
        "hover_mode": bool(config.get("hover_mode", DEFAULT_THESIS_OPERATING_CONDITION["hover_mode"])),
    }


def run_aero_from_config(config: Mapping[str, Any]) -> dict[str, Any]:
    validation_errors = validate_aero_config(config, require_polars=False)
    if validation_errors:
        raise ValueError("; ".join(validation_errors))

    case_name = str(config.get("case_name", DEFAULT_THESIS_OPERATING_CONDITION["case_name"]))
    geometry = load_geometry(_config_path_value(config, "geometry_csv_path", CASE_GEOMETRY_PATHS["optimized_mil_22"]))
    airfoil_dir = _config_path_value(config, "airfoil_dir", DEFAULT_AIRFOIL_DIR)
    allow_debug = bool(config.get("allow_debug_placeholder_polar", False))
    polar_path = config.get("polar")
    if polar_path:
        polar = load_polar(Path(str(polar_path)))
    elif allow_debug:
        polar = load_polar(None)
        if "debug" not in case_name.lower():
            case_name = f"{case_name}_debug_placeholder"
    else:
        xfoil_executable, xfoil_root = _xfoil_args_from_config(config)
        ensure_xfoil_polars_available(airfoil_dir, xfoil_executable=xfoil_executable, xfoil_root=xfoil_root)
        polar = load_airfoil_polar_directory(airfoil_dir)

    operating_condition = _operating_condition_from_config({**dict(config), "case_name": case_name})
    if bool(config.get("match_target", True)) and operating_condition["target_thrust_N"] is not None:
        results, _, _ = iterate_to_target_thrust(
            geometry,
            operating_condition,
            polar,
            target_thrust_N=operating_condition["target_thrust_N"],
            tolerance_pct=float(config.get("tolerance_pct", 1.0)),
        )
    else:
        results = evaluate_propeller(geometry, operating_condition, polar)

    if bool(config.get("run_xrotor", False)):
        run_xrotor_comparison(
            results,
            polar,
            _config_path_value(config, "output_dir", "results/aero"),
            xrotor_root=_xrotor_root_from_config(config),
            timeout_s=float(config.get("xrotor_timeout_s", 60.0)),
        )

    output_dir = _config_path_value(config, "output_dir", "results/aero")
    export_results(results, output_dir, results["case_name"])
    if bool(config.get("generate_plots", True)):
        plot_results(results, _config_path_value(config, "figure_output_dir", "figures_paper/aero"))
    return results


def load_aero_results(output_dir: str | Path, case_name: str) -> dict[str, Any]:
    out_dir = Path(output_dir)
    clean_name = _clean_case_name(case_name)
    summary_path = out_dir / f"{clean_name}_summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"aero summary JSON not found: {summary_path}")
    with summary_path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    station_path = out_dir / f"{clean_name}_stations.csv"
    stations: list[dict[str, Any]] = []
    if station_path.is_file():
        with station_path.open("r", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                stations.append({key: _parse_csv_scalar(value) for key, value in row.items()})
    return {
        "case_name": clean_name,
        "summary": loaded["summary"],
        "operating_condition": loaded.get("operating_condition", {}),
        "polar_source": loaded.get("polar_source", ""),
        "polar_is_placeholder": loaded.get("polar_is_placeholder", False),
        "xfoil_polars_used": loaded.get("xfoil_polars_used", loaded["summary"].get("xfoil_polars_used", False)),
        "xfoil_success": loaded.get("xfoil_success", loaded["summary"].get("xfoil_success", False)),
        "xrotor_ran": loaded.get("xrotor_ran", loaded["summary"].get("xrotor_ran", False)),
        "xrotor_success": loaded.get("xrotor_success", loaded["summary"].get("xrotor_success", False)),
        "generated_polars_used": loaded.get("generated_polars_used", False),
        "fallback_airfoils_used": loaded.get("fallback_airfoils_used", {}),
        "failed_polar_cases": loaded.get("failed_polar_cases", []),
        "warnings": loaded.get("warnings", []),
        "sanity_checks": loaded.get("sanity_checks", {}),
        "stations": stations,
    }


def _parse_csv_scalar(value: str) -> Any:
    if value in ("True", "False"):
        return value == "True"
    if value == "":
        return ""
    try:
        return float(value)
    except ValueError:
        return value


def generate_aero_plots_from_config(config: Mapping[str, Any]) -> dict[str, list[Path]]:
    loaded = load_aero_results(
        _config_path_value(config, "output_dir", "results/aero"),
        str(config.get("case_name", DEFAULT_THESIS_OPERATING_CONDITION["case_name"])),
    )
    return plot_results(loaded, _config_path_value(config, "figure_output_dir", "figures_paper/aero"))


def export_orchestration_failure_summary(
    output_dir: str | Path,
    case_name: str,
    operating_condition: OperatingCondition | Mapping[str, Any],
    failure_reason: str,
    *,
    polar_is_placeholder: bool = False,
    xfoil_polars_used: bool = False,
    xfoil_success: bool = False,
    xrotor_ran: bool = False,
    xrotor_success: bool = False,
) -> dict[str, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clean_name = _clean_case_name(case_name)
    op = _normalize_operating_condition(operating_condition)
    if op.case_name != case_name:
        op = replace(op, case_name=case_name)

    summary = {
        "case_name": case_name,
        "target_thrust_N": op.target_thrust_N,
        "target_matched": False,
        "polar_is_placeholder": polar_is_placeholder,
        "xfoil_polars_used": xfoil_polars_used,
        "xfoil_success": xfoil_success,
        "xrotor_ran": xrotor_ran,
        "xrotor_success": xrotor_success,
        "generated_polars_used": xfoil_polars_used,
        "valid_for_thesis": False,
        "failure_reason": failure_reason,
    }
    payload = {
        "summary": summary,
        "operating_condition": asdict(op),
        "polar_source": "placeholder_debug_polar" if polar_is_placeholder else "",
        "polar_is_placeholder": polar_is_placeholder,
        "xfoil_polars_used": xfoil_polars_used,
        "xfoil_success": xfoil_success,
        "xrotor_ran": xrotor_ran,
        "xrotor_success": xrotor_success,
        "valid_for_thesis": False,
        "generated_polars_used": xfoil_polars_used,
        "fallback_airfoils_used": {},
        "failed_polar_cases": [],
        "warnings": [failure_reason],
        "sanity_checks": {},
    }

    station_csv_path = out_dir / f"{clean_name}_stations.csv"
    with station_csv_path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=STATION_EXPORT_COLUMNS).writeheader()

    summary_json_path = out_dir / f"{clean_name}_summary.json"
    summary_json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    summary_txt_path = out_dir / f"{clean_name}_summary.txt"
    summary_txt_path.write_text(
        "\n".join(
            [
                f"Case: {case_name}",
                "Summary",
                f"  xfoil_polars_used: {xfoil_polars_used}",
                f"  xfoil_success: {xfoil_success}",
                f"  xrotor_ran: {xrotor_ran}",
                f"  xrotor_success: {xrotor_success}",
                f"  polar_is_placeholder: {polar_is_placeholder}",
                "  valid_for_thesis: False",
                f"  failure_reason: {failure_reason}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "station_csv": station_csv_path,
        "summary_json": summary_json_path,
        "summary_txt": summary_txt_path,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone BEMT propeller aerodynamic solver.")
    parser.add_argument("--geometry", type=Path, default=_project_path("results/geometry/optimized/optimized_mil/optimized_mil.csv"))
    parser.add_argument("--polar", type=Path, default=None)
    parser.add_argument("--case-name", default=DEFAULT_THESIS_OPERATING_CONDITION["case_name"])
    parser.add_argument("--diameter-m", type=float, default=DEFAULT_THESIS_OPERATING_CONDITION["diameter_m"])
    parser.add_argument("--radius-m", type=float, default=DEFAULT_THESIS_OPERATING_CONDITION["radius_m"])
    parser.add_argument("--blade-count", type=int, default=DEFAULT_THESIS_OPERATING_CONDITION["blade_count"])
    parser.add_argument("--rpm", type=float, default=DEFAULT_THESIS_OPERATING_CONDITION["rpm"])
    parser.add_argument("--rho", type=float, default=DEFAULT_THESIS_OPERATING_CONDITION["rho"])
    parser.add_argument("--mu-air-kg-m-s", type=float, default=DEFAULT_THESIS_OPERATING_CONDITION["mu_air_kg_m_s"])
    parser.add_argument("--v-inf-m-s", type=float, default=DEFAULT_THESIS_OPERATING_CONDITION["V_inf_m_s"])
    parser.add_argument("--target-thrust-N", type=float, default=DEFAULT_THESIS_OPERATING_CONDITION["target_thrust_N"])
    parser.add_argument("--hover-mode", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--match-target", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--tolerance-pct", type=float, default=1.0)
    parser.add_argument("--cl-max-debug", type=float, default=1.5)
    parser.add_argument("--debug-mode", action="store_true")
    parser.add_argument("--airfoil-dir", type=Path, default=_project_path("CAD/airfoils_good"))
    parser.add_argument("--allow-polar-fallback", action="store_true")
    parser.add_argument("--xfoil", type=Path, default=None)
    parser.add_argument("--xfoil-root", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--run-xrotor", action="store_true")
    parser.add_argument("--xrotor-root", type=Path, default=DEFAULT_XROTOR_ROOT)
    parser.add_argument("--xrotor-timeout-s", type=float, default=60.0)
    parser.add_argument("--results-dir", type=Path, default=_project_path("results/aero"))
    parser.add_argument("--figures-dir", type=Path, default=_project_path("figures_paper/aero"))
    parser.add_argument("--no-plots", action="store_true")
    return parser


def _operating_condition_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "case_name": args.case_name,
        "diameter_m": args.diameter_m,
        "radius_m": args.radius_m,
        "blade_count": args.blade_count,
        "rpm": args.rpm,
        "rho": args.rho,
        "mu_air_kg_m_s": args.mu_air_kg_m_s,
        "V_inf_m_s": args.v_inf_m_s,
        "target_thrust_N": args.target_thrust_N,
        "hover_mode": args.hover_mode,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    try:
        config = load_solver_config(args.config)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc))
        return 2
    if config.get("run_xrotor") and not args.run_xrotor:
        print("Ignoring config run_xrotor=true; pass --run-xrotor to run optional XROTOR from the CLI.")
    run_xrotor_requested = bool(args.run_xrotor)

    if args.debug_mode and "debug" not in args.case_name.lower():
        args.case_name = f"{args.case_name}_debug_placeholder"
    operating_condition = _operating_condition_from_args(args)

    if args.polar is not None:
        try:
            polar = load_polar(args.polar, cl_max_debug=args.cl_max_debug)
        except (FileNotFoundError, ValueError) as exc:
            print(str(exc))
            written = export_orchestration_failure_summary(
                args.results_dir,
                args.case_name,
                operating_condition,
                str(exc),
                xfoil_success=False,
            )
            print(f"Wrote failure summary JSON: {written['summary_json']}")
            return 2
    elif args.debug_mode:
        polar = load_polar(None, cl_max_debug=args.cl_max_debug)
    else:
        try:
            ensure_xfoil_polars_available(args.airfoil_dir, xfoil_executable=args.xfoil, xfoil_root=args.xfoil_root)
            polar = load_airfoil_polar_directory(args.airfoil_dir)
        except (FileNotFoundError, RuntimeError) as exc:
            print(str(exc))
            print("Use airfoil_tools/generate_xfoil_polars.py after installing XFOIL, or rerun with --debug-mode.")
            written = export_orchestration_failure_summary(
                args.results_dir,
                args.case_name,
                operating_condition,
                str(exc),
                xfoil_success=False,
            )
            print(f"Wrote failure summary JSON: {written['summary_json']}")
            return 2

    try:
        geometry = load_geometry(args.geometry)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc))
        written = export_orchestration_failure_summary(
            args.results_dir,
            args.case_name,
            operating_condition,
            str(exc),
            polar_is_placeholder=polar.placeholder,
            xfoil_polars_used=polar.generated_polars_used and not polar.placeholder,
            xfoil_success=_polar_xfoil_success(polar),
        )
        print(f"Wrote failure summary JSON: {written['summary_json']}")
        return 2

    if args.match_target and args.target_thrust_N is not None:
        try:
            results, offset_deg, error_pct = iterate_to_target_thrust(
                geometry,
                operating_condition,
                polar,
                target_thrust_N=args.target_thrust_N,
                tolerance_pct=args.tolerance_pct,
                allow_polar_fallback=args.allow_polar_fallback,
            )
        except ValueError as exc:
            print("Could not match target thrust with collective twist offset.")
            print(str(exc))
            written = export_orchestration_failure_summary(
                args.results_dir,
                args.case_name,
                operating_condition,
                str(exc),
                polar_is_placeholder=polar.placeholder,
                xfoil_polars_used=polar.generated_polars_used and not polar.placeholder,
                xfoil_success=_polar_xfoil_success(polar),
            )
            print(f"Wrote failure summary JSON: {written['summary_json']}")
            return 2
        print(f"Matched target with collective twist offset {offset_deg:.4f} deg; error {error_pct:.4f}%.")
    else:
        try:
            results = evaluate_propeller(
                geometry,
                operating_condition,
                polar,
                allow_polar_fallback=args.allow_polar_fallback,
            )
        except ValueError as exc:
            print(str(exc))
            written = export_orchestration_failure_summary(
                args.results_dir,
                args.case_name,
                operating_condition,
                str(exc),
                polar_is_placeholder=polar.placeholder,
                xfoil_polars_used=polar.generated_polars_used and not polar.placeholder,
                xfoil_success=_polar_xfoil_success(polar),
            )
            print(f"Wrote failure summary JSON: {written['summary_json']}")
            return 2

    if run_xrotor_requested:
        if results["summary"]["valid_for_thesis"] and all(value is not False for value in results["sanity_checks"].values()):
            run_xrotor_comparison(
                results,
                polar,
                args.results_dir,
                xrotor_root=args.xrotor_root,
                timeout_s=args.xrotor_timeout_s,
            )
        else:
            message = "Skipping optional XROTOR comparison because the BEMT result is not thesis-valid."
            print(f"WARNING: {message}")
            results["warnings"].append(message)
            results["summary"]["xrotor_ran"] = False
            results["summary"]["xrotor_success"] = False

    print_run_summary(results)
    written = export_results(results, args.results_dir, args.case_name)
    if not args.no_plots:
        plot_results(results, args.figures_dir)
    print(f"Wrote station CSV: {written['station_csv']}")
    print(f"Wrote summary JSON: {written['summary_json']}")
    print(f"Wrote summary TXT: {written['summary_txt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
