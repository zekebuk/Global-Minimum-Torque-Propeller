from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pandas as pd


AIRFOIL_ALIASES = {
    "MH115": "MH115",
    "MH 115": "MH115",
    "S4310": "S4310",
    "A3": "A3",
    "A4": "A4",
    "A6": "A6",
    "A7": "A7",
    "A11": "A11",
    "A13": "A13",
}


def normalize_airfoil_name(name: object) -> str:
    raw = str(name).strip()
    compact = raw.replace(" ", "").replace("_", "").replace("-", "").upper()
    if compact == "MH115":
        return "MH115"
    if compact == "S4310":
        return "S4310"
    if compact in {"A3", "A4", "A6", "A7", "A11", "A13"}:
        return compact
    return raw.upper()


def _column_lookup(columns: Iterable[str]) -> dict[str, str]:
    return {column.strip().lower().replace(" ", "").replace("_", ""): column for column in columns}


def _find_column(lookup: dict[str, str], candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        key = candidate.strip().lower().replace(" ", "").replace("_", "")
        if key in lookup:
            return lookup[key]
    return None


def _load_polar_csv(path: Path) -> pd.DataFrame | None:
    try:
        frame = pd.read_csv(path)
    except Exception:
        return None
    lookup = _column_lookup(frame.columns)
    alpha_col = _find_column(lookup, ("alpha_deg", "alpha"))
    cl_col = _find_column(lookup, ("Cl", "CL", "cl"))
    cd_col = _find_column(lookup, ("Cd", "CD", "cd"))
    if alpha_col is None or cl_col is None or cd_col is None:
        return None

    airfoil_col = _find_column(lookup, ("airfoil_name", "airfoil"))
    re_col = _find_column(lookup, ("Re", "re", "reynolds", "reynolds_number"))
    cm_col = _find_column(lookup, ("Cm", "CM", "cm"))
    converged_col = _find_column(lookup, ("converged", "valid"))

    normalized = pd.DataFrame(
        {
            "alpha_deg": pd.to_numeric(frame[alpha_col], errors="coerce"),
            "Cl": pd.to_numeric(frame[cl_col], errors="coerce"),
            "Cd": pd.to_numeric(frame[cd_col], errors="coerce"),
            "Cm": pd.to_numeric(frame[cm_col], errors="coerce") if cm_col else float("nan"),
            "Re": pd.to_numeric(frame[re_col], errors="coerce") if re_col else float("nan"),
        }
    )
    if airfoil_col:
        normalized["airfoil"] = frame[airfoil_col].map(normalize_airfoil_name)
    else:
        normalized["airfoil"] = normalize_airfoil_name(path.stem.split("_Re")[0])
    if converged_col:
        normalized["converged"] = frame[converged_col].astype(str).str.lower().isin(("true", "1", "yes", "y"))
    else:
        normalized["converged"] = True
    normalized["polar_source"] = str(path)
    normalized = normalized[normalized["converged"]].dropna(subset=["alpha_deg", "Cl", "Cd"])
    if normalized.empty:
        return None
    return normalized


def load_airfoil_polars(airfoil_dir: str | Path) -> dict[str, pd.DataFrame]:
    root = Path(airfoil_dir)
    frames: list[pd.DataFrame] = []
    if root.exists():
        for path in root.glob("*.csv"):
            loaded = _load_polar_csv(path)
            if loaded is not None:
                frames.append(loaded)

    generated_root = root / "generated_polars"
    if generated_root.exists():
        for path in generated_root.rglob("*.csv"):
            loaded = _load_polar_csv(path)
            if loaded is not None:
                frames.append(loaded)

    if not frames:
        return {}

    combined = pd.concat(frames, ignore_index=True)
    polars: dict[str, pd.DataFrame] = {}
    for airfoil, group in combined.groupby("airfoil"):
        group = group.sort_values(["Re", "alpha_deg"], na_position="first").reset_index(drop=True)
        polars[str(airfoil)] = group[["airfoil", "Re", "alpha_deg", "Cl", "Cd", "Cm", "polar_source"]]
    return polars


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect available airfoil polar CSV files.")
    parser.add_argument("--airfoil-dir", type=Path, default=Path("CAD/airfoils_good"))
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    polars = load_airfoil_polars(args.airfoil_dir)
    if not polars:
        print("No alpha/Cl/Cd polar CSV files found.")
        return 1
    for airfoil, frame in polars.items():
        re_values = sorted(value for value in frame["Re"].dropna().unique())
        print(f"{airfoil}: {len(frame)} rows, Re={re_values}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
