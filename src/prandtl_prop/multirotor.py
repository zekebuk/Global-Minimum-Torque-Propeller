"""Multirotor dynamics for propeller design: motor and battery, forward flight, trim, and control.

Assumptions (first principles):

- **Motor.** Brushless DC motor as an ideal DC machine with SI torque constant Kt = 1/Kv
  (Kv in rad/s per V), lumped series resistance R (winding + ESC + this motor's share of the
  battery's internal resistance, which carries all n_rotors currents), and no-load current I0:
      V = omega / Kv + I R,   I = Q Kv + I0.
  The electrical power drawn from the cells is (omega / Kv + I R) I / eta_esc.
- **Motor sizing.** A brushless outrunner's mass scales with the torque it must deliver (stator
  volume at fixed magnetic and thermal loading): m_motor = m_0 + k Q_peak, with Q_peak the
  full-throttle torque. With a payload mass given, the all-up mass is payload + n (m_motor + m_prop)
  and is iterated to a fixed point with the hover and full-throttle thrust it implies. This is
  where torque, not only power, costs the vehicle: a high-torque prop needs heavier motors.
- **Motor matching.** At full throttle (V = battery voltage) the motor must reach the rpm at which
  the fixed-pitch prop makes the maximum thrust required by the thrust-to-weight ratio. Eliminating
  I gives R Q Kv^2 - (V - R I0) Kv + omega = 0; the smaller root (higher voltage constant, lower
  current) is used. No real root means the requirement exceeds the maximum power transfer
  (V - R I0)^2 / (4 R).
- **Rotor in forward flight.** The rotor axis is tilted forward by the body pitch theta, so the free
  stream V has an axial part V sin(theta) (through the disk, like climb) and an edgewise part
  V cos(theta). Each annulus obeys Glauert's momentum relation,
      dT = 4 pi rho r F v_i sqrt((V cos theta)^2 + (V sin theta + v_i)^2) dr,
  with v_i uniform around the azimuth, and the blade-element loads are averaged over the azimuth:
  U_T = omega r + V cos(theta) sin(psi), U_P = V sin(theta) + v_i. With no edgewise speed this is
  exactly the axial BEMT of ``aero/bemt.py``. The in-plane H-force (rearward) is the azimuth average
  of the tangential force times sin(psi). Reverse flow (U_T <= 0) carries no load. The polar has no
  stall drag rise, so stall is checked instead: the peak Cl over the loaded azimuth points
  (dynamic pressure >= 5% of the tip's) must stay below ``forward_cl_limit`` at ``max_speed_m_s``;
  the retreating blade (U_T = omega r - V cos(theta) |sin(psi)|) sets it. Blade flapping,
  wake skew, and rotor-rotor interference are neglected; this is a first-order performance model.
- **Trim.** Steady level flight with all rotors at the same rpm: n (T sin theta - H cos theta) = D
  and n (T cos theta + H sin theta) = W, with body drag D = 0.5 rho V^2 CdA.
- **Control.** Linearised motor-rotor speed response: J d(omega)/dt = Kt I - Q(omega) at fixed
  command voltage gives the time constant tau = J / (Kt^2 / R + dQ/domega). Yaw comes from the
  reaction torque, so dQ/dT (m) at hover sets the yaw moment per newton of differential thrust; a
  torque-minimised prop has less yaw authority per unit thrust.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from scipy.optimize import brentq

from prandtl_prop.aero.bemt import BladeStations, OperatingPoint, solve_rotor, trim_rpm_to_thrust
from prandtl_prop.aero.polars import AnalyticPolar

G = 9.80665


@dataclass(frozen=True)
class MultirotorSpec:
    """Vehicle, motor, battery, and requirements (defaults: a 10 in long-range FPV quad on 6S)."""

    rotor_count: int = 4
    mass_kg: float = 1.6  # all-up mass; set by ``size_vehicle`` when payload_mass_kg is given
    # Sizing (optional): AUW = payload + n (motor + prop); motor mass grows with the peak (full-throttle)
    # torque it must deliver, at the torque density of a 2810-class outrunner (~80 g at ~0.35 N m).
    payload_mass_kg: float | None = None  # everything except motors and props
    motor_base_mass_kg: float = 0.015
    motor_mass_per_peak_torque_kg_per_Nm: float = 0.186
    body_cda_m2: float = 0.020
    arm_length_m: float = 0.21  # motor to CG
    ixx_kg_m2: float = 0.011
    izz_kg_m2: float = 0.018
    thrust_to_weight: float = 4.0
    battery_voltage_V: float = 22.2  # 6S nominal
    battery_energy_Wh: float = 88.8  # 6S 4000 mAh
    battery_usable_fraction: float = 0.8
    battery_resistance_ohm: float = 0.012
    motor_kv_rpm_per_V: float | None = None  # None: matched to the thrust-to-weight requirement
    motor_resistance_ohm: float = 0.040
    esc_resistance_ohm: float = 0.005
    motor_no_load_current_A: float = 1.0
    motor_max_current_A: float = 50.0
    motor_rotor_inertia_kg_m2: float = 8.0e-6
    esc_efficiency: float = 0.97
    max_tip_mach: float = 0.70
    max_speed_m_s: float = 25.0  # level-flight speed the vehicle must trim without retreating-blade stall
    forward_cl_limit: float = 1.15  # peak loaded Cl allowed at max_speed_m_s (below the polar's Cl clamp)
    speed_of_sound_m_s: float = 343.0
    cruise_speeds_m_s: tuple[float, ...] = (0.0, 2.5, 5.0, 7.5, 10.0, 12.5, 15.0, 17.5, 20.0, 22.5, 25.0)
    azimuth_points: int = 36

    @property
    def weight_N(self) -> float:
        return self.mass_kg * G

    @property
    def hover_thrust_N(self) -> float:
        return self.weight_N / self.rotor_count

    @property
    def max_thrust_N(self) -> float:
        return self.thrust_to_weight * self.hover_thrust_N

    @property
    def series_resistance_ohm(self) -> float:
        return self.motor_resistance_ohm + self.esc_resistance_ohm + self.rotor_count * self.battery_resistance_ohm


# ------------------------------------------------------------------------------------------ motor


def kv_rad(kv_rpm_per_V: float) -> float:
    return kv_rpm_per_V * 2.0 * math.pi / 60.0


def matched_kv(spec: MultirotorSpec, torque_Nm: float, omega_rad_s: float) -> float | None:
    """Kv (rpm/V) that reaches ``omega`` at full throttle under ``torque``; None if beyond max power transfer."""

    R, V, I0 = spec.series_resistance_ohm, spec.battery_voltage_V, spec.motor_no_load_current_A
    a, b, c = R * torque_Nm, -(V - R * I0), omega_rad_s
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return None
    root = (-b - math.sqrt(disc)) / (2.0 * a) if a > 0 else c / -b
    return root * 60.0 / (2.0 * math.pi)


def motor_point(spec: MultirotorSpec, kv_rpm_per_V: float, torque_Nm: float, omega_rad_s: float) -> dict[str, float]:
    """Current, terminal voltage (incl. battery sag), electrical power, efficiency, and throttle at one operating point."""

    k = kv_rad(kv_rpm_per_V)
    current = torque_Nm * k + spec.motor_no_load_current_A
    voltage = omega_rad_s / k + current * spec.series_resistance_ohm
    electrical = voltage * current / spec.esc_efficiency
    shaft = torque_Nm * omega_rad_s
    return {
        "current_A": current,
        "voltage_V": voltage,
        "electrical_power_W": electrical,
        "shaft_power_W": shaft,
        "drive_efficiency": shaft / electrical if electrical > 0 else math.nan,
        "throttle": voltage / spec.battery_voltage_V,
        "copper_loss_W": current**2 * spec.series_resistance_ohm,
    }


# --------------------------------------------------------------------------- forward-flight rotor


def _tip_loss(blade_count: int, r_R: float, phi: float) -> float:
    if r_R >= 1.0:
        return 0.0
    exponent = 0.5 * blade_count * (1.0 - r_R) / (r_R * max(abs(math.sin(phi)), 1.0e-12))
    return 1.0 if exponent > 50.0 else (2.0 / math.pi) * math.acos(math.exp(-exponent))


def _coefficients(polar, airfoil: str, alpha: np.ndarray, reynolds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if isinstance(polar, AnalyticPolar):
        cl = np.clip(polar.cl_alpha_per_rad * np.radians(alpha - polar.alpha0_deg), polar.cl_min, polar.cl_max)
        return cl, polar.cd0 + polar.k * cl**2
    points = [polar.evaluate(airfoil, float(a), float(re)) for a, re in zip(alpha, reynolds, strict=True)]
    return np.array([p.cl for p in points]), np.array([p.cd for p in points])


def rotor_forward(
    stations: BladeStations,
    op: OperatingPoint,
    polar,
    *,
    v_edge_m_s: float,
    v_axial_m_s: float,
    tip_loss: bool = True,
    azimuth_points: int = 36,
    collective_offset_deg: float = 0.0,
    corrections: tuple[np.ndarray, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Thrust, torque, H-force, and power of a rotor in combined axial + edgewise flow (see module notes).

    ``corrections`` (lift factors, drag factors per station) scale Cl and Cd as in ``solve_rotor``.
    """

    lift, drag = corrections if corrections is not None else (np.ones(len(stations.r_m)), np.ones(len(stations.r_m)))

    psi = (np.arange(azimuth_points) + 0.5) * 2.0 * math.pi / azimuth_points
    sin_psi = np.sin(psi)
    omega = op.omega_rad_s
    B, rho = op.blade_count, op.rho_kg_m3
    thrust_ps, torque_ps, h_ps, vi_out = [], [], [], []
    peak_cl = -math.inf  # over azimuth points carrying >= 5% of the tip dynamic pressure (stall check)
    q_tip = (omega * op.radius_m) ** 2
    for i, r in enumerate(stations.r_m):
        r, c = float(r), float(stations.chord_m[i])
        beta = float(stations.twist_deg[i]) + collective_offset_deg
        ut = omega * r + v_edge_m_s * sin_psi
        live = ut > 0.0
        r_R = r / op.radius_m

        def loads(
            vi: float,
            ut=ut,
            live=live,
            r=r,
            c=c,
            beta=beta,
            r_R=r_R,
            airfoil=stations.airfoil[i],
            lift_i=float(lift[i]),
            drag_i=float(drag[i]),
        ):
            up = v_axial_m_s + vi
            phi = np.arctan2(up, ut)
            w2 = ut**2 + up**2
            alpha = beta - np.degrees(phi)
            reynolds = rho * np.sqrt(w2) * c / op.mu_air_kg_m_s
            cl, cd = _coefficients(polar, airfoil, alpha, reynolds)
            cl, cd = cl * lift_i, cd * drag_i
            q_c = 0.5 * rho * w2 * c * live
            dT = B * q_c * (cl * np.cos(phi) - cd * np.sin(phi))
            dF = B * q_c * (cl * np.sin(phi) + cd * np.cos(phi))  # tangential, opposing rotation
            mean_phi = math.atan2(up, omega * r)
            factor = _tip_loss(B, r_R, mean_phi) if tip_loss else 1.0
            momentum = 4.0 * math.pi * rho * r * factor * vi * math.sqrt(v_edge_m_s**2 + up**2)
            loaded = live & (w2 >= 0.05 * q_tip)
            cl_peak = float(cl[loaded].max()) if loaded.any() else -math.inf
            return float(dT.mean()) - momentum, float(dT.mean()), float(dF.mean()) * r, float((dF * sin_psi).mean()), cl_peak

        if loads(0.0)[0] <= 0.0:
            vi = 0.0
        else:
            upper = max(omega * r * 0.2, 1.0)
            while loads(upper)[0] > 0.0:
                upper *= 2.0
            vi = brentq(lambda v: loads(v)[0], 0.0, upper, xtol=1e-12, rtol=1e-12, maxiter=200)
        _, t, q, h, cl_peak = loads(vi)
        peak_cl = max(peak_cl, cl_peak)
        thrust_ps.append(t)
        torque_ps.append(q)
        h_ps.append(h)
        vi_out.append(vi)
    dr = stations.dr_m
    thrust = float((np.array(thrust_ps) * dr).sum())
    torque = float((np.array(torque_ps) * dr).sum())
    return {
        "thrust_N": thrust,
        "torque_Nm": torque,
        "h_force_N": float((np.array(h_ps) * dr).sum()),
        "power_W": torque * omega,
        "mean_induced_m_s": float(np.average(vi_out, weights=stations.r_m * dr)),
        "advance_ratio_mu": v_edge_m_s / (omega * op.radius_m),
        "peak_cl": peak_cl,
    }


# ------------------------------------------------------------------------------------------- trim


def trim_level_flight(
    stations: BladeStations,
    op: OperatingPoint,
    polar,
    spec: MultirotorSpec,
    v_m_s: float,
    *,
    tip_loss: bool = True,
    corrections: tuple[np.ndarray, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Body pitch and rpm for steady level flight at ``v_m_s``; per-rotor loads at that trim."""

    n, W = spec.rotor_count, spec.weight_N
    drag = 0.5 * op.rho_kg_m3 * v_m_s**2 * spec.body_cda_m2

    def rotor(theta: float, rpm: float) -> dict[str, Any]:
        return rotor_forward(
            stations,
            replace(op, rpm=rpm),
            polar,
            v_edge_m_s=v_m_s * math.cos(theta),
            v_axial_m_s=v_m_s * math.sin(theta),
            tip_loss=tip_loss,
            azimuth_points=spec.azimuth_points,
            corrections=corrections,
        )

    def rpm_for_lift(theta: float) -> tuple[float, dict[str, Any]]:
        def vertical(rpm: float) -> float:
            out = rotor(theta, rpm)
            return n * (out["thrust_N"] * math.cos(theta) + out["h_force_N"] * math.sin(theta)) - W

        low, high = 0.3 * op.rpm, 1.5 * op.rpm
        while vertical(high) < 0.0:
            high *= 1.4
            if high > 60000.0:
                raise ValueError(f"cannot lift the vehicle at V = {v_m_s} m/s, theta = {math.degrees(theta):.1f} deg")
        while vertical(low) > 0.0:
            low *= 0.7
        rpm = brentq(vertical, low, high, xtol=0.05)
        return rpm, rotor(theta, rpm)

    if v_m_s == 0.0:
        rpm, out = rpm_for_lift(0.0)
        theta = 0.0
    else:

        def horizontal(theta: float) -> float:
            _, out = rpm_for_lift(theta)
            return n * (out["thrust_N"] * math.sin(theta) - out["h_force_N"] * math.cos(theta)) - drag

        theta = brentq(horizontal, 1.0e-4, math.radians(75.0), xtol=1.0e-5)
        rpm, out = rpm_for_lift(theta)
    return {
        "v_m_s": v_m_s,
        "pitch_deg": math.degrees(theta),
        "rpm": rpm,
        "body_drag_N": drag,
        **out,
    }


# ---------------------------------------------------------------------------------------- vehicle


def evaluate_vehicle(
    stations: BladeStations,
    op: OperatingPoint,
    polar,
    spec: MultirotorSpec,
    *,
    tip_loss: bool = True,
    prop_inertia_kg_m2: float | None = None,
    forward_flight: bool = True,
    corrections: tuple[np.ndarray, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Hover, full throttle, motor match, forward-flight sweep, endurance and range, and control metrics."""

    hover = trim_rpm_to_thrust(stations, op, polar, spec.hover_thrust_N, collective_offset_deg=0.0, tip_loss=tip_loss)
    top = trim_rpm_to_thrust(
        stations, op, polar, spec.max_thrust_N, collective_offset_deg=0.0, tip_loss=tip_loss, rpm_bracket=(500.0, 60000.0)
    )
    h, t = hover.summary, top.summary
    omega_h, omega_max = float(h["rpm"]) * math.pi / 30.0, float(t["rpm"]) * math.pi / 30.0
    kv = spec.motor_kv_rpm_per_V or matched_kv(spec, float(t["torque_Nm"]), omega_max)
    out: dict[str, Any] = {
        "hover_rpm": float(h["rpm"]),
        "hover_torque_Nm": float(h["torque_Nm"]),
        "hover_shaft_power_W": float(h["power_W"]),
        "hover_figure_of_merit": float(h["figure_of_merit"]),
        "max_thrust_rpm": float(t["rpm"]),
        "max_thrust_torque_Nm": float(t["torque_Nm"]),
        "max_thrust_shaft_power_W": float(t["power_W"]),
        "max_tip_mach": omega_max * op.radius_m / spec.speed_of_sound_m_s,
        "motor_kv_rpm_per_V": kv,
        "feasible_power_transfer": kv is not None,
    }
    if kv is None:
        return out
    hover_motor = motor_point(spec, kv, float(h["torque_Nm"]), omega_h)
    top_motor = motor_point(spec, kv, float(t["torque_Nm"]), omega_max)
    total_hover = spec.rotor_count * hover_motor["electrical_power_W"]
    usable_Wh = spec.battery_energy_Wh * spec.battery_usable_fraction
    out.update({f"hover_{k}": v for k, v in hover_motor.items()})
    out.update({f"max_thrust_{k}": v for k, v in top_motor.items()})
    out["hover_total_electrical_power_W"] = total_hover
    out["hover_endurance_min"] = 60.0 * usable_Wh / total_hover
    out["current_within_limit"] = top_motor["current_A"] <= spec.motor_max_current_A
    out["tip_mach_within_limit"] = out["max_tip_mach"] <= spec.max_tip_mach

    # Control: thrust and torque sensitivity to rpm at hover, motor-rotor time constant, authority.
    d_rpm = 0.01 * float(h["rpm"])
    plus = solve_rotor(stations, replace(op, rpm=float(h["rpm"]) + d_rpm), polar, tip_loss=tip_loss, corrections=corrections).summary
    minus = solve_rotor(stations, replace(op, rpm=float(h["rpm"]) - d_rpm), polar, tip_loss=tip_loss, corrections=corrections).summary
    d_omega = 2.0 * d_rpm * math.pi / 30.0
    dT = (float(plus["thrust_N"]) - float(minus["thrust_N"])) / d_omega
    dQ = (float(plus["torque_Nm"]) - float(minus["torque_Nm"])) / d_omega
    kt = 1.0 / kv_rad(kv)
    inertia = (prop_inertia_kg_m2 or 0.0) + spec.motor_rotor_inertia_kg_m2
    tau = inertia / (kt**2 / spec.series_resistance_ohm + dQ)
    dQdT = dQ / dT
    margin = spec.max_thrust_N - spec.hover_thrust_N
    out.update(
        {
            "dT_domega_N_s": dT,
            "dQ_domega_Nm_s": dQ,
            "yaw_torque_per_thrust_m": dQdT,
            "rotor_inertia_kg_m2": inertia,
            "motor_time_constant_ms": 1000.0 * tau,
            "thrust_bandwidth_Hz": 1.0 / (2.0 * math.pi * tau),
            # differential thrust at constant total: two rotors +dT, two -dT (limited by the hover-to-max margin)
            "max_yaw_accel_rad_s2": spec.rotor_count * dQdT * min(margin, spec.hover_thrust_N) / spec.izz_kg_m2,
            "max_roll_accel_rad_s2": spec.rotor_count
            * min(margin, spec.hover_thrust_N)
            * spec.arm_length_m
            / math.sqrt(2.0)
            / spec.ixx_kg_m2,
        }
    )

    if forward_flight:
        sweep = []
        for v in spec.cruise_speeds_m_s:
            try:
                trim = trim_level_flight(stations, op, polar, spec, v, tip_loss=tip_loss, corrections=corrections)
            except ValueError:
                break
            motor = motor_point(spec, kv, trim["torque_Nm"], trim["rpm"] * math.pi / 30.0)
            total = spec.rotor_count * motor["electrical_power_W"]
            sweep.append(
                {
                    **trim,
                    **{f"motor_{k}": val for k, val in motor.items()},
                    "total_electrical_power_W": total,
                    "endurance_min": 60.0 * usable_Wh / total,
                    "range_km": usable_Wh / total * v * 3.6 if v > 0 else 0.0,
                    "energy_Wh_per_km": total / (v * 3.6) if v > 0 else math.inf,
                }
            )
        for row in sweep:
            row["retreating_stall"] = row["peak_cl"] > spec.forward_cl_limit
        out["forward_flight"] = sweep
        clean = [row["v_m_s"] for row in sweep if not row["retreating_stall"]]
        out["stall_free_speed_m_s"] = max(clean) if clean else 0.0
        moving = [row for row in sweep if row["v_m_s"] > 0]
        if moving:
            best = max(moving, key=lambda row: row["range_km"])
            out["best_range_speed_m_s"] = best["v_m_s"]
            out["best_range_km"] = best["range_km"]
            out["best_endurance_min"] = max(row["endurance_min"] for row in sweep)
    return out


def plot_forward_flight(vehicles: dict[str, dict[str, Any]], spec: MultirotorSpec, title: str, output) -> None:
    """Battery power, range, body pitch, and rpm against flight speed; one line per prop."""

    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import SERIES, style

    style()
    figure, axes = plt.subplots(1, 4, figsize=(20, 4.6))
    for (label, vehicle), color, line in zip(vehicles.items(), SERIES, ("-", "--", ":"), strict=False):
        rows = vehicle.get("forward_flight", [])
        v = [row["v_m_s"] for row in rows]
        for ax, key in zip(axes, ("total_electrical_power_W", "range_km", "pitch_deg", "rpm"), strict=True):
            ax.plot(v, [row[key] for row in rows], color=color, linestyle=line, marker="o", markersize=4, label=label)
    usable = spec.battery_energy_Wh * spec.battery_usable_fraction
    for ax, (ylabel, name) in zip(
        axes,
        (
            ("power (W)", "Battery power in level flight (all rotors)"),
            ("range (km)", f"Range on {usable:.0f} Wh usable"),
            ("nose-down pitch (deg)", "Body pitch to trim"),
            ("rpm", "Rotor speed"),
        ),
        strict=True,
    ):
        ax.set_xlabel("flight speed (m/s)")
        ax.set_ylabel(ylabel)
        ax.set_title(name)
        ax.legend(fontsize=8)
    figure.suptitle(title, fontsize=12)
    figure.tight_layout()
    figure.savefig(output, dpi=150)
    plt.close(figure)


def size_vehicle(spec: MultirotorSpec, peak_torque_Nm: float, prop_mass_kg: float) -> MultirotorSpec:
    """All-up mass from the payload, the torque-sized motors, and the props (no-op without a payload mass)."""

    if spec.payload_mass_kg is None:
        return spec
    motor = spec.motor_base_mass_kg + spec.motor_mass_per_peak_torque_kg_per_Nm * peak_torque_Nm
    return replace(spec, mass_kg=spec.payload_mass_kg + spec.rotor_count * (motor + prop_mass_kg))


def motor_mass(spec: MultirotorSpec, peak_torque_Nm: float) -> float:
    return spec.motor_base_mass_kg + spec.motor_mass_per_peak_torque_kg_per_Nm * peak_torque_Nm
