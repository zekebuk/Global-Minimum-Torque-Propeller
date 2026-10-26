"""Replay a flown mission with any prop: the wrench the vehicle needed, re-made by the candidate rotors.

The idea, and its assumption. A mission log fixes what the airframe needed at every instant: the
body air velocity and rates, and the wrench [total thrust, roll, pitch, yaw moment] the rotors
delivered (from the logged rpm through the flown prop's tables, or straight from a simulator's
truth). A different prop flying the *same trajectory* must deliver the same wrench from the same
inflow. That "frozen trajectory" holds while the controller tracks well; saturation is reported so
the cases where it would not are visible, and the real check is to fly the exported tables in SITL.

Per sample:

1. Rotor speeds. Solve wrench4(rpm) = W for the n rotor speeds by damped Newton iteration; each
   rotor's loads depend on its own speed only, so the Jacobian columns come from one perturbed
   evaluation. The full nonlinear rotor (torque, hub roll moment, H-force) enters, so a prop with
   less torque per thrust needs a larger thrust split for the same yaw moment: yaw authority is
   priced in. More than four rotors take the minimum-norm step.
2. Motor. Q_motor = Q_aero + J d(omega)/dt; current and terminal voltage from the DC-machine model;
   the shared bus voltage from the battery's state of charge and internal resistance; duty
   u = V_terminal / V_bus.
3. Battery. State of charge and the energy drawn from the cells (V_oc I_bus) are integrated in time.

A sample is saturated when any motor needs u > ``max_throttle`` (thrust, slew rate, or yaw), when the
wrench cannot be met inside the speed limits (a rotor would need to stop or exceed its no-load
speed), when the battery cannot supply the power, or when a non-braking ESC would need negative
current to decelerate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from prandtl_prop.sitl import vehicle as vh
from prandtl_prop.sitl.motor import Battery, Motor, bus_voltage, required_drive
from prandtl_prop.sitl.tables import RotorTable


@dataclass(frozen=True)
class Trace:
    """What a mission demanded, sample by sample (N samples, n rotors)."""

    name: str
    t_s: np.ndarray  # (N,)
    v_air_body_m_s: np.ndarray  # (N, 3) vehicle velocity relative to the air, body FRD
    body_rate_rad_s: np.ndarray  # (N, 3)
    wrench: np.ndarray  # (N, 4) total thrust along -z_b (N), roll, pitch, yaw moment (N m)
    source: str = ""
    rpm: np.ndarray | None = None  # (N, n) when logged
    v_bus_V: np.ndarray | None = None

    @property
    def duration_s(self) -> float:
        return float(self.t_s[-1] - self.t_s[0])

    @property
    def dt_s(self) -> np.ndarray:
        dt = np.diff(self.t_s)
        return np.concatenate([dt, dt[-1:]])


def from_rpm(name, t_s, v_air_body, body_rate, rpm, table: RotorTable, vehicle: vh.Vehicle, *, source="", v_bus=None) -> Trace:
    """Trace from logged rotor speeds: the wrench is what the flown prop (``table``) made at that inflow."""

    flow = vh.inflow(vehicle, v_air_body, body_rate)
    loads = table.evaluate(rpm, flow["v_axial"], flow["v_edge"])
    return Trace(name, np.asarray(t_s, float), v_air_body, body_rate, vh.wrench4(vehicle, loads, flow["e_f"]), source, rpm, v_bus)


def solve_rpm(table: RotorTable, thrust_N, v_axial, v_edge, rpm_max: float, rpm_min: float = 50.0, iterations: int = 36):
    """rpm giving ``thrust_N`` at each inflow (vectorised bisection), and a flag where even ``rpm_max`` falls short."""

    low = np.full(np.shape(thrust_N), rpm_min)
    high = np.full(np.shape(thrust_N), rpm_max)
    short = table.evaluate(high, v_axial, v_edge)["thrust_N"] < thrust_N
    for _ in range(iterations):
        mid = 0.5 * (low + high)
        above = table.evaluate(mid, v_axial, v_edge)["thrust_N"] > thrust_N
        low, high = np.where(above, low, mid), np.where(above, mid, high)
    return 0.5 * (low + high), short


def replay(
    trace: Trace,
    vehicle: vh.Vehicle,
    table: RotorTable,
    motor: Motor,
    battery: Battery,
    *,
    max_throttle: float = 1.0,
    soc_start: float = 1.0,
    newton_iterations: int = 12,
) -> dict[str, Any]:
    n = vehicle.rotor_count
    flow = vh.inflow(vehicle, trace.v_air_body_m_s, trace.body_rate_rad_s)
    v_ax, v_edge, e_f = flow["v_axial"], flow["v_edge"], flow["e_f"]
    rpm_max = 1.2 * motor.kv_rpm_per_V * float(battery.open_circuit_V(1.0))
    rpm_min = 0.03 * rpm_max
    thrust = np.repeat(trace.wrench[:, :1] / n, n, axis=1)
    rpm, _ = solve_rpm(table, np.maximum(thrust, 0.0), v_ax, v_edge, rpm_max, rpm_min)
    scale = np.array([1.0, 1.0, 1.0, 1.0])
    for _ in range(newton_iterations):
        loads = table.evaluate(rpm, v_ax, v_edge)
        parts = vh.rotor_wrench4(vehicle, loads, e_f)
        step = 0.002 * rpm + 1.0
        jac = (vh.rotor_wrench4(vehicle, table.evaluate(rpm + step, v_ax, v_edge), e_f) - parts) / step[..., None]  # (N, n, 4)
        residual = trace.wrench - parts.sum(axis=1)
        delta = np.einsum("kij,kj->ki", np.linalg.pinv(np.transpose(jac, (0, 2, 1)) * scale[None, :, None]), residual * scale)
        limit = 0.3 * rpm + 300.0
        rpm = np.clip(rpm + np.clip(delta, -limit, limit), rpm_min, rpm_max)
    loads = table.evaluate(rpm, v_ax, v_edge)
    achieved = vh.wrench4(vehicle, loads, e_f)
    error = np.abs(achieved - trace.wrench)
    tolerance = np.array([0.01, 0.002, 0.002, 0.002]) * float(np.median(trace.wrench[:, 0])) * np.array([1.0, 1.0, 1.0, 1.0])
    unmet = np.any(error > tolerance, axis=1)
    at_limit = np.any((rpm <= rpm_min * 1.0001) | (rpm >= rpm_max * 0.9999), axis=1)

    omega = rpm * math.pi / 30.0
    omega_dot = np.gradient(omega, trace.t_s, axis=0)
    motor_torque = loads["torque_Nm"] + motor.inertia_kg_m2 * omega_dot
    current, terminal = required_drive(motor, omega, motor_torque)
    coast_limited = (current < 0.0) & (not motor.active_braking)

    dt = trace.dt_s
    N = len(dt)
    soc, energy = soc_start, 0.0
    v_bus = np.empty(N)
    duty = np.empty((N, n))
    bus_current = np.empty(N)
    for k in range(N):
        v = bus_voltage(battery, soc, motor, terminal[k], current[k])
        v_bus[k] = v
        duty[k] = terminal[k] / v if v == v else np.inf
        i_bus = float(np.sum(motor.bus_current(np.clip(duty[k], 0.0, None), current[k]))) if v == v else math.nan
        bus_current[k] = i_bus
        if i_bus == i_bus:
            energy += float(battery.open_circuit_V(soc)) * i_bus * dt[k]
            soc -= i_bus * dt[k] / (battery.capacity_Ah * 3600.0)
    battery_limited = ~np.isfinite(v_bus)
    saturated = np.any(duty > max_throttle, axis=1) | unmet | battery_limited | np.any(coast_limited, axis=1)
    time = float(dt.sum())
    vrs = np.any(loads["vrs"], axis=1)
    finite = np.isfinite(bus_current)
    electrical = v_bus * bus_current
    return {
        "mission": trace.name,
        "duration_s": time,
        "energy_Wh": energy / 3600.0,
        "mean_battery_power_W": energy / time,
        "end_state_of_charge": soc,
        "saturated_fraction": float((saturated * dt).sum() / time),
        "max_duty": float(np.nanmax(np.where(np.isfinite(duty), duty, np.nan))),
        "duty_p99": float(np.nanpercentile(np.where(np.isfinite(duty), duty, np.nan).max(axis=1), 99)),
        "unmet_wrench_fraction": float((unmet * dt).sum() / time),
        "speed_limit_fraction": float((at_limit * dt).sum() / time),
        "battery_limited_fraction": float((battery_limited * dt).sum() / time),
        "peak_motor_current_A": float(np.nanmax(current)),
        "over_current_fraction": float((np.any(current > motor.max_current_A, axis=1) * dt).sum() / time),
        "vrs_fraction": float((vrs * dt).sum() / time),
        "min_bus_voltage_V": float(np.nanmin(v_bus)),
        "peak_rpm": float(rpm.max()),
        "wrench_rms_error": [float(e) for e in np.sqrt(np.mean((achieved - trace.wrench) ** 2, axis=0))],
        "_series": {
            "t_s": trace.t_s,
            "rpm": rpm,
            "duty": duty,
            "thrust_N": loads["thrust_N"],
            "torque_Nm": loads["torque_Nm"],
            "current_A": current,
            "v_bus_V": v_bus,
            "battery_power_W": np.where(finite, electrical, np.nan),
            "v_axial_m_s": v_ax,
            "v_edge_m_s": v_edge,
            "saturated": saturated,
            "vrs": vrs,
        },
    }


def duty_histogram(result: dict[str, Any], v_axial_edges=None, v_edge_edges=None) -> dict[str, np.ndarray]:
    """Time and battery-energy shares over (mean V_axial, mean V_edge) across the rotors: where the prop really works."""

    s = result["_series"]
    v_axial_edges = np.array([-15.0, -6.0, -3.0, -1.0, 1.0, 3.0, 6.0, 15.0]) if v_axial_edges is None else v_axial_edges
    v_edge_edges = np.array([0.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0]) if v_edge_edges is None else v_edge_edges
    dt = np.gradient(s["t_s"])
    va, ve = s["v_axial_m_s"].mean(axis=1), s["v_edge_m_s"].mean(axis=1)
    power = np.nan_to_num(s["battery_power_W"])
    time, _, _ = np.histogram2d(va, ve, bins=[v_axial_edges, v_edge_edges], weights=dt)
    energy, _, _ = np.histogram2d(va, ve, bins=[v_axial_edges, v_edge_edges], weights=power * dt)
    return {
        "v_axial_edges": v_axial_edges,
        "v_edge_edges": v_edge_edges,
        "time_fraction": time / dt.sum(),
        "energy_fraction": energy / energy.sum(),
    }
