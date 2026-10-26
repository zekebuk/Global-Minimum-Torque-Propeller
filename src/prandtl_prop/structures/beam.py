"""Fast structural screen: a clamped-root Euler-Bernoulli beam with true airfoil sections.

Use it every design iteration, and confirm finalists with CalculiX. Assumptions, stated so they can
be checked against the higher-fidelity model:

- Isotropic, solid sections; plane sections stay plane; small deflections.
- Unsymmetric bending about the section centroid, using I_yy, I_zz, I_yz in the section plane.
- Loads are lumped on the BEMT stations, and integrated with the trapezoid rule that the station
  widths already encode (BEMT ``dr_m`` sums to the span).
- Torsion uses Saint-Venant's solid-section approximation J ~ A^4 / (4 pi^2 I_p) and
  tau_max ~ T t_max / J. Treat it as an estimate.
- Centrifugal stiffening is neglected, so flap deflection is conservative (over-predicted). Stresses
  are unaffected to first order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from prandtl_prop.cad.sections import BladeSection
from prandtl_prop.structures.loads import LineLoads, section_plane_properties


@dataclass(frozen=True)
class BeamResult:
    rows: list[dict[str, float]]
    metrics: dict[str, float]


def _tail_integral(values: np.ndarray, r: np.ndarray) -> np.ndarray:
    """Integral of ``values`` from each station to the tip (trapezoid rule)."""

    segment = 0.5 * (values[1:] + values[:-1]) * np.diff(r)
    return np.concatenate([np.cumsum(segment[::-1])[::-1], [0.0]])


def _root_integral(values: np.ndarray, r: np.ndarray) -> np.ndarray:
    segment = 0.5 * (values[1:] + values[:-1]) * np.diff(r)
    return np.concatenate([[0.0], np.cumsum(segment)])


def solve_beam(
    sections: list[BladeSection],
    loads: LineLoads,
    *,
    E_Pa: float,
    nu: float,
    density_kg_m3: float,
    allowable_stress_Pa: float,
) -> BeamResult:
    r = loads.r_m
    if not np.allclose(r, [section.r_m for section in sections]):
        raise ValueError("Line loads and sections must share stations")
    G_Pa = E_Pa / (2.0 * (1.0 + nu))
    props = [section_plane_properties(section) for section in sections]

    # Resultants of everything outboard of each cut, then moments about the pitch axis at the cut.
    N, Vy, Vz = (_tail_integral(component, r) for component in (loads.f_x, loads.f_y, loads.f_z))
    Mx = _tail_integral(loads.m_x, r)
    My = -(_tail_integral(r * loads.f_z, r) - r * Vz) + _tail_integral(loads.m_y, r)
    Mz = (_tail_integral(r * loads.f_y, r) - r * Vy) + _tail_integral(loads.m_z, r)

    rows: list[dict[str, float]] = []
    curvature_v = np.zeros(len(r))
    curvature_w = np.zeros(len(r))
    twist_rate = np.zeros(len(r))
    for index, (section, p) in enumerate(zip(sections, props, strict=True)):
        yc, zc = p.centroid_s_m, p.centroid_t_m
        I_yy, I_zz, I_yz = p.I_ss_m4, p.I_tt_m4, p.I_st_m4
        # Moments about the centroid: M_c = M_p + (p - c) x F.
        My_c = My[index] - zc * N[index]
        Mz_c = Mz[index] + yc * N[index]
        Mx_c = Mx[index] - yc * Vz[index] + zc * Vy[index]
        determinant = I_yy * I_zz - I_yz**2
        outline = section.points_3d()[:, 1:] - np.array([yc, zc])
        y, z = outline[:, 0], outline[:, 1]
        sigma = N[index] / p.area_m2 + ((My_c * I_zz + Mz_c * I_yz) * z - (Mz_c * I_yy + My_c * I_yz) * y) / determinant
        polar_moment = I_yy + I_zz
        torsion_constant = p.area_m2**4 / (4.0 * math.pi**2 * polar_moment)
        thickness = section.airfoil.max_thickness * section.chord_m
        tau = abs(Mx_c) * thickness / torsion_constant
        von_mises = np.sqrt(sigma**2 + 3.0 * tau**2)
        # Curvatures from M_y = -E (I_yz v'' + I_yy w''), M_z = E (I_zz v'' + I_yz w'').
        curvature_v[index] = (Mz_c * I_yy + My_c * I_yz) / (E_Pa * determinant)
        curvature_w[index] = -(My_c * I_zz + Mz_c * I_yz) / (E_Pa * determinant)
        twist_rate[index] = Mx_c / (G_Pa * torsion_constant)
        rows.append(
            {
                "r_m": float(r[index]),
                "area_m2": p.area_m2,
                "I_yy_m4": I_yy,
                "I_zz_m4": I_zz,
                "I_yz_m4": I_yz,
                "J_approx_m4": torsion_constant,
                "N_N": float(N[index]),
                "Vy_N": float(Vy[index]),
                "Vz_N": float(Vz[index]),
                "Mx_centroid_Nm": float(Mx_c),
                "My_centroid_Nm": float(My_c),
                "Mz_centroid_Nm": float(Mz_c),
                "sigma_axial_max_Pa": float(sigma.max()),
                "sigma_axial_min_Pa": float(sigma.min()),
                "tau_torsion_approx_Pa": float(tau),
                "von_mises_max_Pa": float(von_mises.max()),
            }
        )

    # Clamped root: v = v' = w = w' = twist = 0 at the first station.
    slope_v = _root_integral(curvature_v, r)
    slope_w = _root_integral(curvature_w, r)
    deflection_v = _root_integral(slope_v, r)
    deflection_w = _root_integral(slope_w, r)
    elastic_twist = _root_integral(twist_rate, r)
    for index, row in enumerate(rows):
        row["deflection_y_m"] = float(deflection_v[index])
        row["deflection_z_m"] = float(deflection_w[index])
        row["elastic_twist_deg"] = float(np.degrees(elastic_twist[index]))

    areas = np.array([p.area_m2 for p in props])
    von_mises_max = max(row["von_mises_max_Pa"] for row in rows)
    critical = max(rows, key=lambda row: row["von_mises_max_Pa"])
    metrics = {
        "blade_mass_kg": float(density_kg_m3 * np.trapezoid(areas, r)),
        "root_centrifugal_force_N": float(N[0]),
        "root_flap_moment_Nm": float(rows[0]["My_centroid_Nm"]),
        "root_lag_moment_Nm": float(rows[0]["Mz_centroid_Nm"]),
        "max_von_mises_Pa": von_mises_max,
        "max_von_mises_r_m": critical["r_m"],
        "margin_of_safety": allowable_stress_Pa / von_mises_max - 1.0,
        "tip_deflection_z_m": float(deflection_w[-1]),
        "tip_deflection_y_m": float(deflection_v[-1]),
        "tip_elastic_twist_deg": float(np.degrees(elastic_twist[-1])),
    }
    return BeamResult(rows, metrics)
