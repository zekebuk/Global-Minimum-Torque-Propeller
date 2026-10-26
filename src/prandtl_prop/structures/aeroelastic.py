"""Static aeroelastic coupling and once-per-revolution fatigue of the blade.

**Static aeroelasticity.** A molded PA6-CF30 blade (E ~ 13 GPa) twists under load. Two moments
act about the pitch axis: the aerodynamic moment (lift at the aerodynamic centre, offset from the
pitch axis, plus the section's Cm) and the centrifugal "propeller moment" -rho omega^2 int(y z dA),
which turns every section toward the plane of rotation (flattening the pitch). The flown twist is
the designed twist plus this elastic twist, and the loads depend on the flown twist, so the two are
solved together by fixed-point iteration:

    beta_k = beta_design + theta_e,k  ->  BEMT loads(beta_k)  ->  line loads + centrifugal body force
    ->  Euler-Bernoulli / Saint-Venant beam (``structures.beam``)  ->  theta_e,k+1   (relaxed 0.5)

until the tip twist changes by less than ``tolerance_deg``. The rotor is held either at fixed rpm
(the twist change shows up as thrust) or trimmed to a thrust (it shows up as rpm and power).
Assumptions: the beam's (isotropic solid section, Saint-Venant torsion constant, no centrifugal
stiffening: flap deflection is over-predicted, twist is not affected to first order).

**Once-per-revolution fatigue.** In edgewise flight the advancing and retreating blades see
different dynamic pressure, so the root flap moment of every blade varies once per revolution.
From an azimuth-resolved load history (the free-vortex-wake solver's dT/dr per azimuth), these
loads replace the BEMT line loads azimuth by azimuth, the beam is re-solved, and the extreme-fibre
stress at every station gives a mean and an alternating part; the modified Goodman criterion
    sigma_a / S_f + sigma_m / S_u <= 1 / SF
is checked at the design life. S_f is the fatigue strength at that cycle count and S_u the
ultimate strength of the conditioned material. Short-fibre PA6-CF30 data vary widely with fibre
orientation and moisture; the defaults are conservative literature-level values (state them in any
report) and should be replaced by coupon data from the molded material.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np


@dataclass(frozen=True)
class FatigueMaterial:
    ultimate_Pa: float = 120.0e6  # conditioned PA6-CF30, along flow (assumed)
    fatigue_strength_Pa: float = 30.0e6  # at the design life below, R = -1, conditioned (assumed)
    design_cycles: float = 2.0e8  # e.g. 500 h at 7000 rpm, one cycle per revolution
    safety_factor: float = 1.5


def static_aeroelastic(
    design,
    aero_run,
    *,
    rpm: float | None = None,
    thrust_N: float | None = None,
    polar=None,
    stations=None,
    iterations: int = 12,
    tolerance_deg: float = 0.005,
) -> dict[str, Any]:
    """Iterate BEMT and the beam to the flown twist. Returns the iteration history and the rigid vs elastic comparison."""

    from prandtl_prop import mission, pipeline
    from prandtl_prop.aero.bemt import solve_rotor, trim_rpm_to_thrust
    from prandtl_prop.structures.beam import solve_beam
    from prandtl_prop.structures.loads import map_station_loads

    base, polar0, corrections, collective, op = mission.bemt_context(design, aero_run)
    polar = polar or polar0
    if stations is not None:
        base, corrections = stations, None
    else:
        base = replace(base, twist_deg=base.twist_deg + collective)
    sections0 = pipeline.design_sections(design, aero_run)
    if len(sections0) != len(base.r_m):
        raise ValueError("sections and stations differ in count")
    material = design.structure.material
    if rpm is not None:
        op = replace(op, rpm=rpm)
    elastic = np.zeros(len(base.r_m))
    history = []
    rigid = None
    for k in range(iterations):
        st = replace(base, twist_deg=base.twist_deg + elastic)
        if thrust_N is not None:
            result = trim_rpm_to_thrust(
                st,
                op,
                polar,
                thrust_N,
                collective_offset_deg=0.0,
                tip_loss=design.aero.tip_loss,
                corrections=corrections,
                rpm_bracket=(300.0, 60000.0),
            )
        else:
            result = solve_rotor(st, op, polar, tip_loss=design.aero.tip_loss, corrections=corrections)
        rpm_k = float(result.summary["rpm"]) if "rpm" in result.summary else op.rpm
        sections = [replace(s, beta_deg=s.beta_deg + float(e)) for s, e in zip(sections0, elastic, strict=True)]
        loads = map_station_loads(
            result.station_rows(),
            sections,
            blade_count=op.blade_count,
            omega_rad_s=rpm_k * math.pi / 30.0,
            rho_air_kg_m3=op.rho_kg_m3,
            material_density_kg_m3=material.density_kg_m3,
        )
        beam = solve_beam(
            sections,
            loads,
            E_Pa=material.E_Pa,
            nu=material.nu,
            density_kg_m3=material.density_kg_m3,
            allowable_stress_Pa=material.allowable_stress_Pa,
        )
        new = np.array([row["elastic_twist_deg"] for row in beam.rows])
        entry = {
            "iteration": k,
            "rpm": rpm_k,
            "thrust_N": float(result.summary["thrust_N"]),
            "torque_Nm": float(result.summary["torque_Nm"]),
            "power_W": float(result.summary["power_W"]),
            "tip_elastic_twist_deg": float(new[-1]),
            "elastic_twist_r075_deg": float(np.interp(0.75 * op.radius_m, base.r_m, new)),
            "tip_deflection_z_mm": 1000.0 * beam.metrics["tip_deflection_z_m"],
            "max_von_mises_MPa": beam.metrics["max_von_mises_Pa"] / 1e6,
        }
        history.append(entry)
        if rigid is None:
            rigid = dict(entry, beam=beam)
        change = float(np.max(np.abs(new - elastic)))
        elastic = elastic + 0.5 * (new - elastic) if k else new * 0.5
        if change < tolerance_deg and k:
            break
    final = history[-1]
    return {
        "history": history,
        "elastic_twist_deg": elastic,
        "r_m": base.r_m,
        "beam": beam,
        "rigid": {k: v for k, v in rigid.items() if k != "beam"},
        "flown": final,
        "delta_thrust_pct": 100.0 * (final["thrust_N"] / rigid["thrust_N"] - 1.0),
        "delta_power_pct": 100.0 * (final["power_W"] / rigid["power_W"] - 1.0),
    }


def root_flap_moment_history(dT_dr: np.ndarray, r_m: np.ndarray, widths_m: np.ndarray, root_m: float) -> np.ndarray:
    """(steps, blades) root flap moment per blade from dT/dr per blade (steps, blades, panels)."""

    return np.einsum("sbp,p->sb", dT_dr, (r_m - root_m) * widths_m)


def once_per_rev_fatigue(
    design,
    aero_run,
    *,
    rpm: float,
    r_m: np.ndarray,
    dT_dr: np.ndarray,
    dF_dr: np.ndarray,
    material: FatigueMaterial | None = None,
) -> dict[str, Any]:
    """Goodman check from an azimuth-resolved load history of one blade.

    ``dT_dr`` and ``dF_dr`` (steps, panels) are the thrust and in-plane drag per span of one blade
    at each azimuth, on panels at ``r_m`` (e.g. the free wake's blade 0). At every azimuth they replace
    the BEMT line loads (interpolated to the beam stations; the centrifugal body force, which is steady,
    is kept), the beam is solved, and the extreme-fibre stresses give, at every station, a mean and an
    alternating stress. The root stress of this blade is dominated by the lag moment and by centrifugal
    relief of the flap moment, so the stress is not proportional to the flap moment alone; the beam is
    re-solved instead of scaled.
    """

    from prandtl_prop import mission, pipeline
    from prandtl_prop.aero.bemt import solve_rotor
    from prandtl_prop.structures.beam import solve_beam
    from prandtl_prop.structures.loads import map_station_loads

    material = material or FatigueMaterial()
    stations, polar, corrections, collective, op = mission.bemt_context(design, aero_run)
    stations = replace(stations, twist_deg=stations.twist_deg + collective)
    op = replace(op, rpm=rpm)
    rows = solve_rotor(stations, op, polar, tip_loss=design.aero.tip_loss, corrections=corrections).station_rows()
    sections = pipeline.design_sections(design, aero_run)
    solid = design.structure.material
    B = op.blade_count

    def beam_for(rows_k):
        loads = map_station_loads(
            rows_k,
            sections,
            blade_count=B,
            omega_rad_s=rpm * math.pi / 30.0,
            rho_air_kg_m3=op.rho_kg_m3,
            material_density_kg_m3=solid.density_kg_m3,
        )
        return solve_beam(
            sections, loads, E_Pa=solid.E_Pa, nu=solid.nu, density_kg_m3=solid.density_kg_m3, allowable_stress_Pa=solid.allowable_stress_Pa
        )

    sigma_max, sigma_min, flap = [], [], []
    for qz, qy in zip(dT_dr, dF_dr, strict=True):
        qz_s = np.interp(stations.r_m, r_m, qz, left=0.0, right=0.0)
        qy_s = np.interp(stations.r_m, r_m, qy, left=0.0, right=0.0)
        rows_k = [dict(row, qz_N_per_m=B * z, qy_tangential_N_per_m=B * y) for row, z, y in zip(rows, qz_s, qy_s, strict=True)]
        beam = beam_for(rows_k)
        sigma_max.append([row["sigma_axial_max_Pa"] for row in beam.rows])
        sigma_min.append([row["sigma_axial_min_Pa"] for row in beam.rows])
        flap.append(beam.metrics["root_flap_moment_Nm"])
    sigma_max, sigma_min = np.array(sigma_max), np.array(sigma_min)
    worst = None
    for name, sig in (("tension fibre", sigma_max), ("compression fibre", sigma_min)):
        mean, amp = sig.mean(axis=0), 0.5 * (sig.max(axis=0) - sig.min(axis=0))
        # Goodman with a compressive mean taken as zero mean (conservative for the alternating part)
        utilisation = material.safety_factor * (amp / material.fatigue_strength_Pa + np.maximum(mean, 0.0) / material.ultimate_Pa)
        i = int(np.argmax(utilisation))
        if worst is None or utilisation[i] > worst["goodman_utilisation"]:
            worst = {
                "location": name,
                "r_m": float(stations.r_m[i]),
                "stress_mean_MPa": float(mean[i] / 1e6),
                "stress_amplitude_MPa": float(amp[i] / 1e6),
                "goodman_utilisation": float(utilisation[i]),
            }
    flap = np.array(flap)
    worst.update(
        {
            "root_flap_moment_mean_Nm": float(flap.mean()),
            "root_flap_moment_amplitude_Nm": float(0.5 * (flap.max() - flap.min())),
            "fatigue_margin": 1.0 / worst["goodman_utilisation"] - 1.0,
            "steady_bemt_root_stress_max_MPa": float(beam_for(rows).rows[0]["sigma_axial_max_Pa"] / 1e6),
        }
    )
    return worst
