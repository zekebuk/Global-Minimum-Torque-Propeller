"""Feed a prandtl design's rotor and vehicle results into the frame requirements.

The standalone frame config carries placeholders (constant CT/CP of a generic 10x5 two-blade prop,
a guessed max thrust and motor mass). Inside the prandtl pipeline those come from the stages that
actually model this prop:

    blade count, diameter, air density  <- design.operating_point
    CT, CP (static, at hover)           <- multirotor stage: hover rpm and shaft power at hover thrust
    max thrust per rotor                <- multirotor spec: thrust_to_weight x hover thrust
    torque at max thrust                <- multirotor stage (BEMT, trimmed to max thrust)
    motor mass                          <- multirotor.motor_mass(): torque-sized outrunner
    prop mass                           <- cad stage: one-piece prop solid (or blades)
    excitation orders                   <- 1P + multiples of blade-pass (B, 2B, 3B)
"""

from __future__ import annotations

import copy
import math
from typing import Any

from prandtl_prop import multirotor


def vehicle_inputs(design, cad_metrics: dict[str, Any], vehicle_metrics: dict[str, Any]) -> dict[str, Any]:
    """Frame-config values derived from upstream stage metrics (all SI except the frame's g / mm)."""

    op = design.operating_point
    spec = design.multirotor
    D = op.diameter_in * 0.0254
    rho = op.rho_kg_m3
    B = int(op.blade_count)
    n_hover = float(vehicle_metrics["hover_rpm"]) / 60.0
    T_hover = spec.hover_thrust_N
    P_hover = float(vehicle_metrics["hover_shaft_power_W"])
    ct = T_hover / (rho * n_hover**2 * D**4)
    cp = P_hover / (rho * n_hover**3 * D**5)
    q_max = float(vehicle_metrics["max_thrust_torque_Nm"])
    if "prop_mass_kg" in cad_metrics:
        prop_kg = float(cad_metrics["prop_mass_kg"])
        prop_src = "cad.prop_mass_kg (one-piece prop solid)"
    else:
        prop_kg = B * float(cad_metrics["blade_mass_kg"])
        prop_src = f"cad.blade_mass_kg x {B} blades"
    motor_kg = multirotor.motor_mass(spec, q_max)
    return {
        "prop_diameter_in": op.diameter_in,
        "prop_blades": B,
        "air_density": rho,
        "ct_static": ct,
        "cp_static": cp,
        "motor_max_thrust_N": spec.max_thrust_N,
        "max_thrust_torque_Nm": q_max,
        "max_thrust_rpm_bemt": float(vehicle_metrics["max_thrust_rpm"]),
        "motor_mass_g": 1e3 * motor_kg,
        "prop_mass_g": 1e3 * prop_kg,
        "harmonics": [1, B, 2 * B, 3 * B],
        "sources": {
            "ct_static": f"multirotor hover: {T_hover:.3f} N at {60 * n_hover:.0f} rpm",
            "cp_static": f"multirotor hover shaft power {P_hover:.1f} W",
            "motor_max_thrust_N": f"multirotor.thrust_to_weight {spec.thrust_to_weight:g} x hover thrust",
            "max_thrust_torque_Nm": "multirotor stage (BEMT trimmed to max thrust)",
            "motor_mass_g": "multirotor.motor_mass(peak torque)",
            "prop_mass_g": prop_src,
            "harmonics": f"1P + blade-pass multiples for {B} blades",
        },
    }


def apply_vehicle_inputs(cfg: dict[str, Any], vin: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the frame config with the propulsion placeholders replaced by ``vin``."""

    out = copy.deepcopy(cfg)
    p = out["propulsion"]
    for key in ("prop_diameter_in", "prop_blades", "air_density", "ct_static", "cp_static", "motor_max_thrust_N",
                "max_thrust_torque_Nm", "max_thrust_rpm_bemt", "motor_mass_g", "prop_mass_g"):
        p[key] = vin[key]
    p["prop_diameter"] = p["prop_diameter_in"] * 25.4
    out["loads"]["harmonics"] = list(vin["harmonics"])
    out["_provenance"] = dict(vin["sources"])
    return out


def consistency(design, analysis: dict[str, Any], frame_spec) -> tuple[dict[str, Any], list[str]]:
    """Compare the frame-closed vehicle with the multirotor spec that sized the prop and motor.

    The multirotor stage runs before the frame stage, so a mismatch cannot be closed inside one run;
    the warning gives the values to put back into the design (or its ``design-gmt`` spec).
    """

    spec = design.multirotor
    auw_kg = analysis["auw_g"] / 1e3
    motor_radius_m = analysis["layout"]["motor_radius"] / 1e3
    mass_err = auw_kg / spec.mass_kg - 1.0
    arm_err = motor_radius_m / spec.arm_length_m - 1.0
    p = analysis["_cfg_propulsion"]
    payload = auw_kg - spec.rotor_count * (p["motor_mass_g"] + p["prop_mass_g"]) / 1e3
    metrics = {
        "vehicle_mass_spec_kg": spec.mass_kg,
        "vehicle_mass_frame_kg": auw_kg,
        "vehicle_mass_error_fraction": mass_err,
        "arm_length_spec_m": spec.arm_length_m,
        "motor_radius_frame_m": motor_radius_m,
        "arm_length_error_fraction": arm_err,
        "suggested_payload_mass_kg": payload,
    }
    warnings = []
    if abs(mass_err) > frame_spec.mass_tolerance_fraction:
        warnings.append(
            f"Frame-closed AUW {auw_kg:.3f} kg vs multirotor.mass_kg {spec.mass_kg:.3f} kg ({100 * mass_err:+.0f} %): the prop and motor "
            f"were sized for the wrong vehicle. Set multirotor.payload_mass_kg = {payload:.3f} (everything except motors and props) "
            "in the design-gmt spec and regenerate the design."
        )
    if abs(arm_err) > frame_spec.arm_tolerance_fraction:
        warnings.append(
            f"Frame motor radius {1e3 * motor_radius_m:.0f} mm vs multirotor.arm_length_m {1e3 * spec.arm_length_m:.0f} mm "
            f"({100 * arm_err:+.0f} %): set multirotor.arm_length_m = {motor_radius_m:.3f} (control authority, inertia)."
        )
    return metrics, warnings


def rpm_check(analysis: dict[str, Any]) -> tuple[float, float]:
    """(frame constant-CT max-thrust rpm, BEMT max-thrust rpm): how far constant CT drifts at full throttle."""

    p = analysis["_cfg_propulsion"]
    return analysis["operating_point"]["rpm_max"], p.get("max_thrust_rpm_bemt", math.nan)
