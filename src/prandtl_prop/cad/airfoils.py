"""Airfoil coordinate loading, NACA generation, and resampling to a common topology.

Accepted files in the airfoil directory (names match case-insensitively, ignoring spaces/_/-):

- ``<NAME>.dat``      Selig format: a title line, then ``x y`` pairs (TE -> upper -> LE -> lower -> TE).
- ``<NAME>_YZ.txt``   thesis CAD export: three columns ``index  y/c  x/c``.

Every airfoil is resampled onto the same cosine-spaced chordwise stations on both surfaces, so all
blade sections share one point topology and loft cleanly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from prandtl_prop.aero.polars import normalize_airfoil_name

NACA4_PATTERN = re.compile(r"^NACA(\d)(\d)(\d{2})$")


@dataclass(frozen=True)
class Airfoil:
    """Unit-chord airfoil on shared stations ``x`` (0 at LE, 1 at TE)."""

    name: str
    x: np.ndarray
    upper: np.ndarray
    lower: np.ndarray
    source: str

    def loop(self) -> np.ndarray:
        """Closed-order outline, TE upper -> LE -> TE lower, without repeating the LE point."""

        upper = np.column_stack([self.x[::-1], self.upper[::-1]])
        lower = np.column_stack([self.x[1:], self.lower[1:]])
        return np.vstack([upper, lower])

    @property
    def max_thickness(self) -> float:
        return float(np.max(self.upper - self.lower))

    @property
    def trailing_edge_gap(self) -> float:
        return float(self.upper[-1] - self.lower[-1])


def cosine_stations(points_per_side: int) -> np.ndarray:
    return 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, points_per_side)))


def naca4(code: str, points_per_side: int = 80) -> Airfoil:
    """NACA 4-digit airfoil with a closed trailing edge (``code`` like ``"NACA4412"``)."""

    match = NACA4_PATTERN.match(normalize_airfoil_name(code))
    if match is None:
        raise ValueError(f"Not a NACA 4-digit designation: {code!r}")
    camber, position, thickness = int(match[1]) / 100.0, int(match[2]) / 10.0, int(match[3]) / 100.0
    x = cosine_stations(points_per_side)
    half_thickness = 5.0 * thickness * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1036 * x**4)
    if camber == 0.0 or position == 0.0:
        camber_line = np.zeros_like(x)
        slope = np.zeros_like(x)
    else:
        front = x < position
        camber_line = np.where(
            front,
            camber / position**2 * (2 * position * x - x**2),
            camber / (1 - position) ** 2 * (1 - 2 * position + 2 * position * x - x**2),
        )
        slope = np.where(front, 2 * camber / position**2 * (position - x), 2 * camber / (1 - position) ** 2 * (position - x))
    theta = np.arctan(slope)
    xu, yu = x - half_thickness * np.sin(theta), camber_line + half_thickness * np.cos(theta)
    xl, yl = x + half_thickness * np.sin(theta), camber_line - half_thickness * np.cos(theta)
    return _resample(normalize_airfoil_name(code), xu, yu, xl, yl, points_per_side, "generated NACA 4-digit")


def find_airfoil_file(name: str, directory: Path) -> Path | None:
    key = normalize_airfoil_name(name)
    if not Path(directory).is_dir():
        return None
    for path in sorted(Path(directory).rglob("*")):
        if not path.is_file():
            continue
        stem = path.stem
        if path.suffix.lower() == ".dat" and normalize_airfoil_name(stem) == key:
            return path
        if path.suffix.lower() == ".txt" and stem.upper().endswith("_YZ") and normalize_airfoil_name(stem[:-3]) == key:
            return path
    return None


def load_airfoil(name: str, directory: Path, points_per_side: int = 80, substitutes: dict[str, str] | None = None) -> Airfoil:
    """Load ``name`` from ``directory``; use an explicit substitute only if the design names one."""

    path = find_airfoil_file(name, directory)
    if path is not None:
        return read_airfoil_file(path, name, points_per_side)
    substitute = {normalize_airfoil_name(key): value for key, value in (substitutes or {}).items()}.get(normalize_airfoil_name(name)) or (
        substitutes or {}
    ).get("*")
    if substitute is None:
        raise FileNotFoundError(
            f"No coordinates for airfoil {name!r} in {directory} (expected {name}.dat or {name}_YZ.txt). "
            "Add the file, or set airfoils.substitutes in the design to use a stand-in explicitly."
        )
    path = find_airfoil_file(substitute, directory)
    airfoil = read_airfoil_file(path, substitute, points_per_side) if path else naca4(substitute, points_per_side)
    return Airfoil(airfoil.name, airfoil.x, airfoil.upper, airfoil.lower, f"SUBSTITUTE for {name}: {airfoil.source}")


def read_airfoil_file(path: Path, name: str, points_per_side: int = 80) -> Airfoil:
    points = _read_points(Path(path))
    if len(points) < 10:
        raise ValueError(f"{path} has too few coordinate points")
    points = (points - points[np.argmin(points[:, 0])]) / np.ptp(points[:, 0])
    leading = int(np.argmin(points[:, 0]))
    first, second = points[: leading + 1][::-1], points[leading:]
    if np.mean(first[:, 1]) < np.mean(second[:, 1]):
        first, second = second, first
    return _resample(normalize_airfoil_name(name), first[:, 0], first[:, 1], second[:, 0], second[:, 1], points_per_side, str(path))


def _read_points(path: Path) -> np.ndarray:
    yz_format = path.suffix.lower() == ".txt"
    points: list[tuple[float, float]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.replace(",", " ").split()
        try:
            values = [float(part) for part in parts]
        except ValueError:
            continue
        if yz_format and len(values) >= 3:
            points.append((values[2], values[1]))
        elif not yz_format and len(values) == 2:
            points.append((values[0], values[1]))
    array = np.array(points, dtype=float)
    keep = np.ones(len(array), dtype=bool)
    keep[1:] = np.any(np.abs(np.diff(array, axis=0)) > 1.0e-12, axis=1)
    return array[keep]


def _resample(name: str, xu: np.ndarray, yu: np.ndarray, xl: np.ndarray, yl: np.ndarray, count: int, source: str) -> Airfoil:
    x = cosine_stations(count)
    upper = _interp_monotonic(x, xu, yu)
    lower = _interp_monotonic(x, xl, yl)
    if np.any(upper[1:-1] - lower[1:-1] <= 0.0):
        raise ValueError(f"Airfoil {name} from {source} has crossing or zero-thickness surfaces")
    return Airfoil(name, x, upper, lower, source)


def _interp_monotonic(x: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    order = np.argsort(xs, kind="stable")
    xs, ys = np.asarray(xs)[order], np.asarray(ys)[order]
    unique = np.concatenate([[True], np.diff(xs) > 1.0e-12])
    return np.interp(x, xs[unique], ys[unique])
