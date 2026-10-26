"""Fast rotating-beam modal model, for optimisation loops. CalculiX (``modal`` stage) confirms finalists.

Euler-Bernoulli beam along +x, clamped at the root station, with Hermite cubic elements between the
BEMT stations. Each node carries four degrees of freedom: lag deflection v (y, in the rotation
plane), its slope v', flap deflection w (z, out of plane), and w'. Energies per unit length:

    strain       0.5 E (I_zz v''^2 + 2 I_yz v'' w'' + I_yy w''^2)   unsymmetric bending couples flap and lag
    centrifugal  0.5 N(x) (v'^2 + w'^2),  N(x) = integral(rho A omega^2 r dr) from x to the tip
    spin         -0.5 rho A omega^2 v^2   spin softening of the in-plane (lag) motion
    kinetic      0.5 rho A (v_dot^2 + w_dot^2)

Section properties come from the true section outlines in the plane x = r. Rotary inertia,
torsion, and Coriolis coupling are neglected.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.linalg import eigh

from prandtl_prop.cad.sections import BladeSection, polygon_properties
from prandtl_prop.structures.loads import section_plane_properties


@dataclass(frozen=True)
class BeamMode:
    frequency_hz: float
    kind: str  # "flap" or "lag", by which bending energy dominates
    flap_fraction: float


def _element_matrices(length: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    L = length
    k = (
        np.array(
            [[12, 6 * L, -12, 6 * L], [6 * L, 4 * L**2, -6 * L, 2 * L**2], [-12, -6 * L, 12, -6 * L], [6 * L, 2 * L**2, -6 * L, 4 * L**2]]
        )
        / L**3
    )
    g = np.array(
        [[36, 3 * L, -36, 3 * L], [3 * L, 4 * L**2, -3 * L, -(L**2)], [-36, -3 * L, 36, -3 * L], [3 * L, -(L**2), -3 * L, 4 * L**2]]
    ) / (30 * L)
    m = (
        np.array(
            [
                [156, 22 * L, 54, -13 * L],
                [22 * L, 4 * L**2, 13 * L, -3 * L**2],
                [54, 13 * L, 156, -22 * L],
                [-13 * L, -3 * L**2, -22 * L, 4 * L**2],
            ]
        )
        * L
        / 420
    )
    return k, g, m


def section_arrays(sections: Sequence[BladeSection]) -> dict[str, np.ndarray]:
    props = [section_plane_properties(section) for section in sections]
    return {
        "r": np.array([section.r_m for section in sections]),
        "A": np.array([p.area_m2 for p in props]),
        "I_yy": np.array([p.I_ss_m4 for p in props]),
        "I_zz": np.array([p.I_tt_m4 for p in props]),
        "I_yz": np.array([p.I_st_m4 for p in props]),
    }


def outline_arrays(outlines: Sequence[np.ndarray]) -> dict[str, np.ndarray]:
    """Same as ``section_arrays`` for explicit 3D outlines at constant x (e.g. the root tab and transition)."""

    props = [polygon_properties(np.asarray(outline)[:, 1:]) for outline in outlines]
    return {
        "r": np.array([float(outline[0, 0]) for outline in outlines]),
        "A": np.array([p.area_m2 for p in props]),
        "I_yy": np.array([p.I_ss_m4 for p in props]),
        "I_zz": np.array([p.I_tt_m4 for p in props]),
        "I_yz": np.array([p.I_st_m4 for p in props]),
    }


def concat_arrays(*parts: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {key: np.concatenate([part[key] for part in parts]) for key in parts[0]}


def blade_arrays(sections: Sequence[BladeSection], root_geometry=None, clamp_radius_m: float | None = None) -> dict[str, np.ndarray]:
    """Section properties for the modal beam, clamped where the hub grips the blade.

    With a root tab (``prandtl_prop.cad.root.RootGeometry``), the beam starts at the clamp radius
    (default: the tab's outer edge, where the hub plates end; the boss edge for a one-piece prop) and
    runs through the transition outlines before the aero stations. Clamping at the first aero station
    instead overestimated flap 1 by ~14% against CalculiX (159 vs 139 Hz).
    """

    data = section_arrays(sections)
    if root_geometry is None:
        return data
    from prandtl_prop.cad.root import root_outlines, tab_outline_3d

    clamp = root_geometry.tab_outer_radius_m if clamp_radius_m is None else clamp_radius_m
    outlines = [o for o in root_outlines(sections[0], root_geometry) if float(o[0, 0]) >= clamp - 1.0e-9]
    if clamp < root_geometry.tab_outer_radius_m - 1.0e-9:
        tab = tab_outline_3d(sections, root_geometry).copy()
        tab[:, 0] = clamp
        outlines.insert(0, tab)
    return concat_arrays(outline_arrays(outlines), data)


def rotating_modes(
    sections: Sequence[BladeSection] | dict[str, np.ndarray],
    *,
    E_Pa: float,
    density_kg_m3: float,
    omega_rad_s: float,
    count: int = 4,
) -> list[BeamMode]:
    """Lowest ``count`` modes of the clamped rotating blade."""

    data = sections if isinstance(sections, dict) else section_arrays(sections)
    r, A = data["r"], data["A"]
    nodes = len(r)
    mass_per_length = density_kg_m3 * A
    segment = 0.5 * (mass_per_length[1:] * r[1:] + mass_per_length[:-1] * r[:-1]) * np.diff(r) * omega_rad_s**2
    tension = np.concatenate([np.cumsum(segment[::-1])[::-1], [0.0]])

    def element_average(values: np.ndarray) -> np.ndarray:
        return 0.5 * (values[1:] + values[:-1])

    EIzz_e, EIyy_e, EIyz_e = (E_Pa * element_average(data[name]) for name in ("I_zz", "I_yy", "I_yz"))
    tension_e, mass_e = element_average(tension), element_average(mass_per_length)
    size = 4 * nodes
    K = np.zeros((size, size))
    M = np.zeros((size, size))
    for e in range(nodes - 1):
        length = r[e + 1] - r[e]
        k, g, m = _element_matrices(length)
        EIzz, EIyy, EIyz, n_e, mu = EIzz_e[e], EIyy_e[e], EIyz_e[e], tension_e[e], mass_e[e]
        v = [4 * e, 4 * e + 1, 4 * e + 4, 4 * e + 5]
        w = [4 * e + 2, 4 * e + 3, 4 * e + 6, 4 * e + 7]
        K[np.ix_(v, v)] += EIzz * k + n_e * g - omega_rad_s**2 * mu * m
        K[np.ix_(w, w)] += EIyy * k + n_e * g
        K[np.ix_(v, w)] += EIyz * k
        K[np.ix_(w, v)] += EIyz * k
        M[np.ix_(v, v)] += mu * m
        M[np.ix_(w, w)] += mu * m
    free = np.arange(4, size)  # clamp v, v', w, w' at the root node
    values, vectors = eigh(K[np.ix_(free, free)], M[np.ix_(free, free)], subset_by_index=[0, count - 1])
    modes = []
    for value, vector in zip(values, vectors.T, strict=True):
        full = np.zeros(size)
        full[free] = vector
        w_dofs = np.concatenate([np.arange(2, size, 4), np.arange(3, size, 4)])
        v_dofs = np.concatenate([np.arange(0, size, 4), np.arange(1, size, 4)])
        flap_energy = full[w_dofs] @ (K[np.ix_(w_dofs, w_dofs)] @ full[w_dofs])
        lag_energy = full[v_dofs] @ (K[np.ix_(v_dofs, v_dofs)] @ full[v_dofs])
        fraction = float(flap_energy / max(flap_energy + lag_energy, 1.0e-300))
        modes.append(BeamMode(float(np.sqrt(max(value, 0.0)) / (2.0 * np.pi)), "flap" if fraction >= 0.5 else "lag", fraction))
    return modes


def campbell(
    sections: Sequence[BladeSection] | dict[str, np.ndarray],
    *,
    E_Pa: float,
    density_kg_m3: float,
    rpm_values: Sequence[float],
    count: int = 4,
) -> list[list[BeamMode]]:
    data = sections if isinstance(sections, dict) else section_arrays(sections)
    return [rotating_modes(data, E_Pa=E_Pa, density_kg_m3=density_kg_m3, omega_rad_s=rpm * np.pi / 30.0, count=count) for rpm in rpm_values]


def excitation_margin(modes_by_rpm: Sequence[Sequence[BeamMode]], rpm_values: Sequence[float], harmonics: int = 4) -> float:
    """Smallest separation |f - k n| / (k n), as a fraction of the excitation frequency, over all modes, speeds, and k = 1..harmonics."""

    worst = np.inf
    for modes, rpm in zip(modes_by_rpm, rpm_values, strict=True):
        n = rpm / 60.0
        for mode in modes:
            for k in range(1, harmonics + 1):
                worst = min(worst, abs(mode.frequency_hz - k * n) / (k * n))
    return float(worst)
