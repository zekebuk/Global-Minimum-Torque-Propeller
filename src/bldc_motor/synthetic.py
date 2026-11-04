"""Synthetic thrust-stand logs generated from the model, for testing the data pipeline.

Synthetic data are clearly labelled (``# SYNTHETIC`` header comment) and must never be
presented as measurements. Their purpose is to check that import -> steady-state extraction ->
identification recovers known parameters, and to rehearse a test plan before bench time.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np

from bldc_motor import thermal, units
from bldc_motor.powertrain import Battery, Powertrain
from bldc_motor.propeller import PropellerModel

DEFAULT_NOISE = {
    "voltage_v": 0.01,
    "current_a": 0.05,
    "rpm": 15.0,
    "thrust_n": 0.02,
    "torque_nm": 0.0004,
    "temp_c": 0.2,
    "magnet_temp_c": 0.2,
}

UNIT_HEADERS = {
    "time_s": ("Time (s)", 1.0),
    "esc_signal_us": ("ESC signal (µs)", 1.0),
    "voltage_v": ("Voltage (V)", 1.0),
    "current_a": ("Current (A)", 1.0),
    "rpm": ("Motor Optical Speed (RPM)", 1.0),
    "thrust_n": ("Thrust (kgf)", 1.0 / units.STANDARD_GRAVITY_M_S2),
    "torque_nm": ("Torque (N·m)", 1.0),
    "temp_c": ("Winding Temp (°C)", 1.0),
    "magnet_temp_c": ("Bell Temp (°C)", 1.0),
}


def bench_supply(voltage_v: float, r_ohm: float = 0.005, name: str = "bench_supply") -> Battery:
    """A stiff DC supply represented as a one-'cell' battery with a flat OCV curve."""

    return Battery(
        name=name,
        cells_series=1,
        capacity_mah=1.0e9,
        r_cell_ohm=r_ohm,
        ocv_table=((0.0, voltage_v), (1.0, voltage_v)),
        source="bench supply",
    )


def unloaded(prop: PropellerModel | None = None) -> PropellerModel:
    """A zero-load 'propeller' for no-load runs."""

    return PropellerModel(
        name="no_load", diameter_m=prop.diameter_m if prop else 0.1, ct_coeffs=(0.0,), cp_coeffs=(0.0,)
    )


def generate_stand_log(
    powertrain: Powertrain,
    throttle_steps=(0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    hold_s: float = 3.0,
    sample_hz: float = 20.0,
    response_tau_s: float = 0.15,
    noise: dict | None = None,
    ambient_c: float = 25.0,
    seed: int = 0,
) -> dict[str, np.ndarray]:
    """Simulate a stepped-throttle stand run on a single motor; returns canonical SI columns."""

    rng = np.random.default_rng(seed)
    single = replace(powertrain, n_motors=1)
    times = np.concatenate([[i * hold_s] for i in range(len(throttle_steps))])
    profile = thermal.ThrottleProfile(tuple(times), tuple(float(t) for t in throttle_steps))
    duration = hold_s * len(throttle_steps) - 1.0 / sample_hz
    dt = 1.0 / sample_hz
    if single.motor.thermal is not None:
        sim = thermal.simulate(single, profile, duration, dt, ambient_c, track_soc=False)
    else:
        sim = _isothermal(single, profile, duration, dt, ambient_c)

    columns = {
        "time_s": sim["time_s"],
        "throttle": sim["throttle"],
        "esc_signal_us": 1000.0 + 1000.0 * sim["throttle"],
        "voltage_v": sim["v_bus_v"],
        "current_a": sim["i_bus_per_motor_a"],
        "rpm": sim["rpm"],
        "thrust_n": sim["thrust_n"],
        "torque_nm": sim["torque_nm"],
        "temp_c": sim["winding_temp_c"],
        "magnet_temp_c": sim["stator_temp_c"],
    }
    alpha = dt / (response_tau_s + dt) if response_tau_s > 0 else 1.0
    for key in ("current_a", "rpm", "thrust_n", "torque_nm", "voltage_v"):
        columns[key] = _first_order_lag(columns[key], alpha)
    for key, sigma in (noise if noise is not None else DEFAULT_NOISE).items():
        if key in columns and sigma:
            columns[key] = columns[key] + rng.normal(0.0, sigma, size=columns[key].size)
    return columns


def _isothermal(single: Powertrain, profile, duration, dt, ambient_c) -> dict[str, np.ndarray]:
    times = np.arange(0.0, duration + 0.5 * dt, dt)
    rows = [single.solve_at_throttle(profile(t), 1.0, 0.0, ambient_c, ambient_c) for t in times]
    return {
        "time_s": times,
        "throttle": np.array([profile(t) for t in times]),
        "v_bus_v": np.array([r.v_bus for r in rows]),
        "i_bus_per_motor_a": np.array([r.i_bus_per_motor_a for r in rows]),
        "rpm": np.array([r.rpm for r in rows]),
        "thrust_n": np.array([r.thrust_n for r in rows]),
        "torque_nm": np.array([r.prop_torque_nm for r in rows]),
        "winding_temp_c": np.full(times.size, ambient_c),
        "stator_temp_c": np.full(times.size, ambient_c),
    }


def _first_order_lag(values: np.ndarray, alpha: float) -> np.ndarray:
    out = np.empty_like(values)
    out[0] = values[0]
    for index in range(1, values.size):
        out[index] = out[index - 1] + alpha * (values[index] - out[index - 1])
    return out


def write_stand_csv(
    columns: dict[str, np.ndarray], path: str | Path, unit_headers: bool = True, description: str = ""
) -> Path:
    """Write synthetic columns; ``unit_headers`` emits stand-software-style 'Name (unit)' headers."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    keys = [k for k in UNIT_HEADERS if k in columns] if unit_headers else list(columns)
    headers = [UNIT_HEADERS[k][0] for k in keys] if unit_headers else keys
    scales = [UNIT_HEADERS[k][1] for k in keys] if unit_headers else [1.0] * len(keys)
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.write("# SYNTHETIC thrust-stand log generated by bldc_motor.synthetic -- not measured data\n")
        if description:
            handle.write(f"# {description}\n")
        handle.write(",".join(headers) + "\n")
        for index in range(len(columns[keys[0]])):
            handle.write(",".join(f"{columns[k][index] * s:.8g}" for k, s in zip(keys, scales, strict=False)) + "\n")
    return target
