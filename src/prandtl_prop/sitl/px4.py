"""PX4 side: parameters fitted to the rotor + motor model, and ULog ingestion.

Parameters (PX4 v1.14+ control allocation; check the names against your PX4 version):

- ``CA_ROTORi_CT``: thrust (N) at full command, static, nominal bus voltage. PX4's effectiveness
  model is T = CT u^2 with u the normalised motor command.
- ``THR_MDL_FAC``: PX4 linearises the command with rel_thrust = f u^2 + (1 - f) u. f is the least
  squares fit of the model's static T(u) / T(1); a pure-square law would give f = 1.
- ``CA_ROTORi_KM``: torque per thrust Q / T at hover (m); positive for counter-clockwise rotors.
- ``MPC_THR_HOVER``: the hover collective in PX4's normalised (linearised) thrust, T_hover / CT.
- ``CA_ROTORi_PX/PY``: rotor positions (m, FRD) from the layout: placeholders until the frame exists.
- ``BAT1_*``: cell count, charged and empty cell voltage, internal resistance, capacity.

The static curve solves J d(omega)/dt = 0 at each command: Kt (I - I0) = Q_aero(omega), with
I = (u V_bus - omega / Kv) / R_m and V_bus the mid-charge open-circuit voltage (sag ignored).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from prandtl_prop.sitl import vehicle as vh
from prandtl_prop.sitl.motor import Battery, Motor
from prandtl_prop.sitl.replay import Trace, from_rpm
from prandtl_prop.sitl.tables import RotorTable


def steady_speed_vec(motor: Motor, table: RotorTable, duty, v_bus, v_axial, v_edge, iterations: int = 60):
    """rpm where the motor torque at ``duty`` balances the rotor torque at the given inflow (vectorised)."""

    duty, v_bus, v_axial, v_edge = np.broadcast_arrays(*(np.asarray(x, float) for x in (duty, v_bus, v_axial, v_edge)))
    low = np.full(duty.shape, 1.0)
    high = np.full(duty.shape, 1.5 * motor.kv_rpm_per_V * float(np.max(v_bus)))
    for _ in range(iterations):
        mid = 0.5 * (low + high)
        omega = mid * math.pi / 30.0
        excess = motor.torque(motor.current(duty, v_bus, omega)) - table.evaluate(mid, v_axial, v_edge)["torque_Nm"]
        low, high = np.where(excess > 0.0, mid, low), np.where(excess > 0.0, high, mid)
    return 0.5 * (low + high)


def static_curve(motor: Motor, table: RotorTable, battery: Battery, duties=None) -> dict[str, np.ndarray]:
    duties = np.linspace(0.05, 1.0, 20) if duties is None else np.asarray(duties)
    v_bus = float(battery.open_circuit_V(0.5))
    rpm = steady_speed_vec(motor, table, duties, v_bus, 0.0, 0.0)
    loads = table.evaluate(rpm, 0.0, 0.0)
    return {"duty": duties, "rpm": rpm, "thrust_N": loads["thrust_N"], "torque_Nm": loads["torque_Nm"], "v_bus_V": v_bus}


def fit_parameters(motor: Motor, table: RotorTable, battery: Battery, vehicle: vh.Vehicle, hover_thrust_N: float) -> dict[str, Any]:
    curve = static_curve(motor, table, battery)
    u, thrust = curve["duty"], curve["thrust_N"]
    ct = float(thrust[-1])
    y = thrust / ct
    basis = u * u - u
    f = float(np.sum((y - u) * basis) / np.sum(basis * basis))
    fit_error = float(np.max(np.abs(f * u * u + (1.0 - f) * u - y)))
    hover_rpm = float(np.interp(hover_thrust_N, thrust, curve["rpm"]))
    hover = table.evaluate(hover_rpm, 0.0, 0.0)
    km = float(hover["torque_Nm"] / hover["thrust_N"])
    omega = hover_rpm * math.pi / 30.0
    d = 0.01 * omega
    dq = float((table.evaluate((omega + d) * 30 / math.pi, 0, 0)["torque_Nm"] - table.evaluate((omega - d) * 30 / math.pi, 0, 0)["torque_Nm"]) / (2 * d))
    tau = motor.inertia_kg_m2 / (motor.kt**2 / motor.resistance_ohm + dq)
    params: dict[str, float | int] = {"CA_AIRFRAME": 0, "CA_ROTOR_COUNT": vehicle.rotor_count}
    for i, ((x, y_), spin) in enumerate(zip(vehicle.positions_m, vehicle.spins, strict=True)):
        params[f"CA_ROTOR{i}_PX"] = round(float(x), 4)
        params[f"CA_ROTOR{i}_PY"] = round(float(y_), 4)
        params[f"CA_ROTOR{i}_CT"] = round(ct, 3)
        params[f"CA_ROTOR{i}_KM"] = round(float(spin) * km, 5)
    params.update(
        {
            "THR_MDL_FAC": round(min(max(f, 0.0), 1.0), 3),
            "MPC_THR_HOVER": round(hover_thrust_N / ct, 3),
            "BAT1_N_CELLS": battery.cells,
            "BAT1_V_CHARGED": 4.20,
            "BAT1_V_EMPTY": 3.50,
            "BAT1_R_INTERNAL": round(battery.resistance_ohm, 4),
            "BAT1_CAPACITY": round(battery.capacity_Ah * 1000.0),
        }
    )
    return {
        "params": params,
        "thr_mdl_fac_fit_max_error": fit_error,
        "static_curve": {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in curve.items()},
        "hover_rpm_static": hover_rpm,
        "hover_duty": float(np.interp(hover_thrust_N, thrust, u)),
        "torque_per_thrust_m": km,
        "motor_time_constant_ms": 1000.0 * tau,
    }


def write_params(params: dict[str, float | int], path: Path, header: str) -> Path:
    lines = [f"# {line}" for line in header.splitlines()] + [f"param set-default {k} {v}" for k, v in params.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------------------------------ logs


def read_truth_csv(path: Path, table: RotorTable, vehicle: vh.Vehicle) -> Trace:
    """A simulator's truth log. Columns: t_s; vx_b, vy_b, vz_b (velocity relative to the air, body FRD, m/s);
    optional p, q, r (rad/s) and v_bus; then either rpm_0..rpm_{n-1} or thrust_total, L, M, N (the wrench)."""

    data = np.genfromtxt(path, delimiter=",", names=True)
    names = data.dtype.names
    t = data["t_s"]
    v_air = np.column_stack([data["vx_b"], data["vy_b"], data["vz_b"]])
    rate = np.column_stack([data[k] if k in names else np.zeros(len(t)) for k in ("p", "q", "r")])
    v_bus = data["v_bus"] if "v_bus" in names else None
    if "rpm_0" in names:
        rpm = np.column_stack([data[f"rpm_{i}"] for i in range(vehicle.rotor_count)])
        return from_rpm(Path(path).stem, t, v_air, rate, rpm, table, vehicle, source=f"truth CSV {path}", v_bus=v_bus)
    wrench = np.column_stack([data[k] for k in ("thrust_total", "L", "M", "N")])
    return Trace(Path(path).stem, t, v_air, rate, wrench, source=f"truth CSV (wrench) {path}", v_bus=v_bus)


def _series(ulog, topic: str, fields: list[str], t: np.ndarray, required: bool = True) -> np.ndarray | None:
    try:
        data = ulog.get_dataset(topic).data
    except (KeyError, IndexError, ValueError):
        if required:
            raise ValueError(f"ULog has no '{topic}' topic") from None
        return None
    ts = data["timestamp"] * 1.0e-6
    return np.column_stack([np.interp(t, ts, data[f]) for f in fields])


def read_ulog(path: Path, table: RotorTable, vehicle: vh.Vehicle, motor: Motor) -> Trace:
    """PX4 ULog: actuator_motors (time base), vehicle_attitude, vehicle_local_position, vehicle_angular_velocity,
    battery_status, and optionally wind (estimated wind) and esc_status (rotor rpm).

    Rotor speeds come from esc_status when it reports them; otherwise from the motor model at the
    logged command and bus voltage (steady state at the logged inflow), which assumes the simulator
    runs this same rotor and motor model.
    """

    from pyulog import ULog

    ulog = ULog(str(path))
    n = vehicle.rotor_count
    motors = ulog.get_dataset("actuator_motors").data
    t = motors["timestamp"] * 1.0e-6
    duty = np.column_stack([motors[f"control[{i}]"] for i in range(n)])
    keep = np.all(np.isfinite(duty), axis=1) & (duty.max(axis=1) > 0.0)  # armed and spinning
    t, duty = t[keep], np.clip(duty[keep], 0.0, 1.0)
    q = _series(ulog, "vehicle_attitude", ["q[0]", "q[1]", "q[2]", "q[3]"], t)
    vel = _series(ulog, "vehicle_local_position", ["vx", "vy", "vz"], t)
    rate = _series(ulog, "vehicle_angular_velocity", ["xyz[0]", "xyz[1]", "xyz[2]"], t)
    v_bus = _series(ulog, "battery_status", ["voltage_v"], t)[:, 0]
    wind = _series(ulog, "wind", ["windspeed_north", "windspeed_east"], t, required=False)
    if wind is not None:
        vel = vel - np.column_stack([wind, np.zeros(len(t))])
    rot = vh.rotation_from_quaternion(q)
    v_air = np.einsum("kji,kj->ki", rot, vel)
    esc = _series(ulog, "esc_status", [f"esc[{i}].esc_rpm" for i in range(n)], t, required=False)
    if esc is not None and np.mean(esc > 0) > 0.9:
        rpm, source = np.abs(esc), "PX4 ULog (esc_status rpm)"
    else:
        flow = vh.inflow(vehicle, v_air, rate)
        rpm = steady_speed_vec(motor, table, duty, v_bus[:, None], flow["v_axial"], flow["v_edge"])
        source = "PX4 ULog (rpm from command and bus voltage through the motor model)"
    t = t - t[0]
    return from_rpm(Path(path).stem, t, v_air, rate, rpm, table, vehicle, source=source, v_bus=v_bus)


def read_log(path: Path, table: RotorTable, vehicle: vh.Vehicle, motor: Motor) -> Trace:
    path = Path(path)
    if path.suffix.lower() == ".ulg":
        return read_ulog(path, table, vehicle, motor)
    if path.suffix.lower() == ".csv":
        return read_truth_csv(path, table, vehicle)
    raise ValueError(f"{path}: logs are PX4 .ulg or truth .csv")
