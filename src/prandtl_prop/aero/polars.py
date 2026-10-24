"""Airfoil polar models used by the BEMT solver.

Two implementations share one interface, ``evaluate(airfoil, alpha_deg, reynolds) -> PolarPoint``:

- ``TablePolar`` reads XFOIL polar CSVs in the thesis format ``<AIRFOIL>_Re<N>_polar.csv`` with
  ``alpha``/``Cl``/``Cd`` (and optional ``Cm``, ``converged``) columns, interpolating linearly in
  alpha and Reynolds number. Out-of-range queries are clamped to the table edge and flagged, never
  silently extrapolated.
- ``AnalyticPolar`` is a thin-airfoil lift slope with a parabolic drag polar. It exists for
  verification against closed-form rotor theory and for smoke tests when no XFOIL data are present.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

POLAR_FILE_PATTERN = re.compile(r"^(?P<airfoil>.+?)_Re(?P<re>\d+(?:\.\d+)?)_polar$", re.IGNORECASE)


@dataclass(frozen=True)
class PolarPoint:
    """Section coefficients at one query point, with range flags."""

    cl: float
    cd: float
    cm: float
    alpha_clipped: bool = False
    re_clipped: bool = False


class Polar(Protocol):
    """Interface every polar model implements."""

    def evaluate(self, airfoil: str, alpha_deg: float, reynolds: float) -> PolarPoint: ...

    def fingerprint(self) -> str: ...


def normalize_airfoil_name(name: str) -> str:
    """Return the canonical airfoil key, e.g. ``"mh 115"`` -> ``"MH115"``."""

    return re.sub(r"[\s_\-]", "", str(name)).upper()


@dataclass(frozen=True)
class AnalyticPolar:
    """Linear lift with stall clamp and parabolic drag: Cl = a(alpha - alpha0), Cd = cd0 + k Cl^2."""

    cl_alpha_per_rad: float = 2.0 * math.pi
    alpha0_deg: float = 0.0
    cd0: float = 0.0
    k: float = 0.0
    cl_max: float = 1.4
    cl_min: float = -1.0

    def evaluate(self, airfoil: str, alpha_deg: float, reynolds: float) -> PolarPoint:
        raw_cl = self.cl_alpha_per_rad * math.radians(alpha_deg - self.alpha0_deg)
        cl = min(max(raw_cl, self.cl_min), self.cl_max)
        return PolarPoint(cl=cl, cd=self.cd0 + self.k * cl**2, cm=0.0, alpha_clipped=cl != raw_cl)

    def fingerprint(self) -> str:
        return f"analytic:{self.cl_alpha_per_rad!r}:{self.alpha0_deg!r}:{self.cd0!r}:{self.k!r}:{self.cl_max!r}:{self.cl_min!r}"


@dataclass(frozen=True)
class _PolarTable:
    reynolds: float
    alpha_deg: np.ndarray
    cl: np.ndarray
    cd: np.ndarray
    cm: np.ndarray
    source: Path


@dataclass
class TablePolar:
    """XFOIL polar tables keyed by airfoil and Reynolds number."""

    tables: dict[str, list[_PolarTable]] = field(default_factory=dict)

    @classmethod
    def from_directory(cls, directory: Path) -> TablePolar:
        """Load every ``*_Re*_polar.csv`` file below ``directory``."""

        directory = Path(directory)
        if not directory.is_dir():
            raise FileNotFoundError(f"Polar directory not found: {directory}")
        polar = cls()
        for path in sorted(directory.rglob("*.csv")):
            match = POLAR_FILE_PATTERN.match(path.stem)
            if match is None:
                continue
            table = _read_polar_csv(path, float(match["re"]))
            if table is not None:
                polar.tables.setdefault(normalize_airfoil_name(match["airfoil"]), []).append(table)
        for entries in polar.tables.values():
            entries.sort(key=lambda table: table.reynolds)
        if not polar.tables:
            raise FileNotFoundError(f"No '<AIRFOIL>_Re<N>_polar.csv' files found under {directory}")
        return polar

    def airfoils(self) -> list[str]:
        return sorted(self.tables)

    def source_files(self) -> list[Path]:
        return sorted(table.source for entries in self.tables.values() for table in entries)

    def fingerprint(self) -> str:
        return "table:" + ";".join(f"{name}@{table.reynolds:g}" for name in self.airfoils() for table in self.tables[name])

    def evaluate(self, airfoil: str, alpha_deg: float, reynolds: float) -> PolarPoint:
        key = normalize_airfoil_name(airfoil)
        if key not in self.tables:
            raise KeyError(f"No polar data for airfoil {airfoil!r}; available: {self.airfoils()}")
        entries = self.tables[key]
        re_values = [table.reynolds for table in entries]
        re_clipped = reynolds < re_values[0] or reynolds > re_values[-1]
        if len(entries) == 1 or reynolds <= re_values[0]:
            return _evaluate_table(entries[0], alpha_deg, re_clipped)
        if reynolds >= re_values[-1]:
            return _evaluate_table(entries[-1], alpha_deg, re_clipped)
        upper = int(np.searchsorted(re_values, reynolds))
        low, high = entries[upper - 1], entries[upper]
        fraction = (reynolds - low.reynolds) / (high.reynolds - low.reynolds)
        a = _evaluate_table(low, alpha_deg, False)
        b = _evaluate_table(high, alpha_deg, False)
        return PolarPoint(
            cl=a.cl + fraction * (b.cl - a.cl),
            cd=a.cd + fraction * (b.cd - a.cd),
            cm=a.cm + fraction * (b.cm - a.cm),
            alpha_clipped=a.alpha_clipped or b.alpha_clipped,
            re_clipped=False,
        )


def _evaluate_table(table: _PolarTable, alpha_deg: float, re_clipped: bool) -> PolarPoint:
    alpha_clipped = alpha_deg < table.alpha_deg[0] or alpha_deg > table.alpha_deg[-1]
    return PolarPoint(
        cl=float(np.interp(alpha_deg, table.alpha_deg, table.cl)),
        cd=float(np.interp(alpha_deg, table.alpha_deg, table.cd)),
        cm=float(np.interp(alpha_deg, table.alpha_deg, table.cm)),
        alpha_clipped=bool(alpha_clipped),
        re_clipped=re_clipped,
    )


def _read_polar_csv(path: Path, reynolds: float) -> _PolarTable | None:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return None
    columns = {name.strip().lower().replace("_", ""): name for name in rows[0]}

    def column(*names: str) -> str | None:
        return next((columns[name] for name in names if name in columns), None)

    alpha_col, cl_col, cd_col = column("alphadeg", "alpha"), column("cl"), column("cd")
    if alpha_col is None or cl_col is None or cd_col is None:
        return None
    cm_col, converged_col = column("cm"), column("converged", "valid")

    points: dict[float, tuple[float, float, float]] = {}
    for row in rows:
        if converged_col and str(row[converged_col]).strip().lower() not in ("true", "1", "yes", "y"):
            continue
        try:
            alpha, cl, cd = float(row[alpha_col]), float(row[cl_col]), float(row[cd_col])
            cm = float(row[cm_col]) if cm_col and row[cm_col] != "" else 0.0
        except ValueError:
            continue
        if all(math.isfinite(value) for value in (alpha, cl, cd, cm)):
            points[alpha] = (cl, cd, cm)
    if len(points) < 2:
        return None
    alphas = np.array(sorted(points))
    values = np.array([points[alpha] for alpha in alphas])
    return _PolarTable(reynolds, alphas, values[:, 0], values[:, 1], values[:, 2], path)
