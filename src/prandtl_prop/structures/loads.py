"""Map BEMT station results onto per-blade line loads in the blade frame.

Frame and sign conventions follow ``prandtl_prop.cad.sections``: +x span, +z thrust, the blade
moves toward +y. BEMT ``qz_N_per_m`` / ``qy_tangential_N_per_m`` are totals over all blades, so one
blade carries ``1/B`` of each. Per unit span, at the pitch axis (r, 0, 0):

- ``f_z = +qz / B``                 thrust
- ``f_y = -qy / B``                 in-plane drag-plus-induced force, opposing the blade's motion
- ``f_x = rho_m A omega^2 r``       centrifugal force (plus a small in-plane part rho_m A omega^2 y_c)
- ``m``                             moments that carry the offsets: aero forces act at the quarter
                                    chord, centrifugal force over the section (including the
                                    propeller moment), plus the airfoil
                                    pitching moment ``0.5 rho W^2 c^2 Cm`` (nose-up = +x).

These line loads are the shared interface for the beam screen and, next, CalculiX and CFD-mapped loads.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from prandtl_prop.cad.sections import BladeSection, polygon_properties

AERODYNAMIC_CENTER_CHORD_FRACTION = 0.25


@dataclass(frozen=True)
class LineLoads:
    """Per-blade distributed loads at each station, about the pitch axis (r, 0, 0). Units N/m and N*m/m."""

    r_m: np.ndarray
    f_x: np.ndarray
    f_y: np.ndarray
    f_z: np.ndarray
    m_x: np.ndarray
    m_y: np.ndarray
    m_z: np.ndarray

    def rows(self) -> list[dict[str, float]]:
        names = ("f_x_N_per_m", "f_y_N_per_m", "f_z_N_per_m", "m_x_Nm_per_m", "m_y_Nm_per_m", "m_z_Nm_per_m")
        arrays = (self.f_x, self.f_y, self.f_z, self.m_x, self.m_y, self.m_z)
        return [
            {"r_m": float(self.r_m[i]), **{name: float(array[i]) for name, array in zip(names, arrays, strict=True)}}
            for i in range(len(self.r_m))
        ]


def section_plane_properties(section: BladeSection):
    """Area, centroid (y, z), and second moments in the section plane x = r."""

    return polygon_properties(section.points_3d()[:, 1:])


def map_station_loads(
    station_rows: Sequence[Mapping[str, object]],
    sections: Sequence[BladeSection],
    *,
    blade_count: int,
    omega_rad_s: float,
    rho_air_kg_m3: float,
    material_density_kg_m3: float,
    include_centrifugal: bool = True,
    include_pitching_moment: bool = True,
) -> LineLoads:
    """Build per-blade line loads from BEMT station rows and matching CAD sections."""

    if len(station_rows) != len(sections):
        raise ValueError(f"{len(station_rows)} aero stations but {len(sections)} sections")
    count = len(sections)
    f = np.zeros((count, 3))
    m = np.zeros((count, 3))
    r = np.array([section.r_m for section in sections])
    for index, (row, section) in enumerate(zip(station_rows, sections, strict=True)):
        if not math.isclose(float(row["r_m"]), section.r_m, rel_tol=1.0e-6, abs_tol=1.0e-9):
            raise ValueError(f"Station {index}: aero r={row['r_m']} does not match section r={section.r_m}")
        aero_force = np.array([0.0, -float(row["qy_tangential_N_per_m"]) / blade_count, float(row["qz_N_per_m"]) / blade_count])
        beta = math.radians(section.beta_deg)
        s_ac = (AERODYNAMIC_CENTER_CHORD_FRACTION - section.pitch_axis_chord_fraction) * section.chord_m
        aero_offset = np.array([0.0, -s_ac * math.cos(beta), -s_ac * math.sin(beta)])
        f[index] += aero_force
        m[index] += np.cross(aero_offset, aero_force)
        if include_pitching_moment:
            cm = _number(row.get("Cm"), 0.0)
            w = float(row["Vrel_m_s"])
            m[index, 0] += 0.5 * rho_air_kg_m3 * w**2 * section.chord_m**2 * cm
        if include_centrifugal:
            # Body force rho omega^2 (x, y, 0) integrated over the section. Its moment about the
            # pitch axis includes the "propeller moment" -rho omega^2 integral(y z dA), which untwists the blade.
            props = section_plane_properties(section)
            scale = material_density_kg_m3 * omega_rad_s**2
            yc, zc, area = props.centroid_s_m, props.centroid_t_m, props.area_m2
            f[index] += scale * area * np.array([section.r_m, yc, 0.0])
            m[index] += scale * np.array([-(props.I_st_m4 + area * yc * zc), section.r_m * area * zc, -section.r_m * area * yc])
    return LineLoads(r, f[:, 0], f[:, 1], f[:, 2], m[:, 0], m[:, 1], m[:, 2])


def _number(value: object, default: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default
