"""Plots for motor development: efficiency maps, model-vs-stand comparisons, thermal traces."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from bldc_motor import units
from bldc_motor.motor import MotorParams
from bldc_motor.powertrain import Powertrain


def efficiency_map(
    motor: MotorParams,
    max_voltage_v: float,
    path: str | Path,
    max_torque_nm: float | None = None,
    temp_c: float | None = None,
    grid: int = 160,
    operating_line: tuple[list[float], list[float]] | None = None,
    operating_label: str = "prop operating line",
) -> Path:
    """Motor efficiency contours over the torque-speed plane, bounded by the voltage limit.

    ``operating_line`` = (rpm values, torque values) is overlaid, e.g. a propeller throttle sweep.
    """

    omega_nl = motor.no_load_speed(max_voltage_v, 0.0, temp_c, temp_c)
    t_max = max_torque_nm or 0.5 * motor.stall_torque(max_voltage_v, 0.0, temp_c, temp_c)
    if motor.max_current_a:
        t_max = min(t_max, motor.ke_at(temp_c) * (motor.max_current_a - motor.i0_a) * 1.1)
    omega = np.linspace(omega_nl * 0.02, omega_nl, grid)
    torque = np.linspace(t_max * 0.01, t_max, grid)
    om, tq = np.meshgrid(omega, torque)
    eff = np.full(om.shape, np.nan)
    volt = np.full(om.shape, np.nan)
    for (i, j), _ in np.ndenumerate(om):
        point = motor.evaluate_at_torque(tq[i, j], om[i, j], temp_c, temp_c)
        volt[i, j] = point.voltage_v
        eff[i, j] = point.efficiency
    eff[volt > max_voltage_v] = np.nan

    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    levels = [0.5, 0.6, 0.7, 0.75, 0.8, 0.83, 0.86, 0.88, 0.9, 0.92]
    contour = ax.contourf(om * 60.0 / units.TWO_PI, tq, eff, levels=levels, cmap="viridis", extend="both")
    lines = ax.contour(om * 60.0 / units.TWO_PI, tq, eff, levels=levels, colors="k", linewidths=0.4)
    ax.clabel(lines, fmt="%.2f", fontsize=7)
    limit_torque = [
        motor.ke_at(temp_c)
        * ((max_voltage_v - motor.ke_at(temp_c) * w) / motor.resistance_at(temp_c) - motor.no_load_current(w))
        for w in omega
    ]
    ax.plot(omega * 60.0 / units.TWO_PI, limit_torque, "r-", lw=1.5, label=f"{max_voltage_v:.1f} V limit")
    if motor.max_current_a:
        ax.axhline(
            motor.ke_at(temp_c) * (motor.max_current_a - motor.i0_a),
            color="orange",
            ls="--",
            lw=1.2,
            label=f"{motor.max_current_a:g} A rating",
        )
    if operating_line is not None:
        ax.plot(operating_line[0], operating_line[1], "w-o", ms=3, lw=1.5, mec="k", label=operating_label)
    ax.set_ylim(0, t_max)
    ax.set_xlabel("Speed [rpm]")
    ax.set_ylabel("Shaft torque [N·m]")
    ax.set_title(f"{motor.name}: motor efficiency" + (f" (T = {temp_c:g} °C)" if temp_c is not None else ""))
    fig.colorbar(contour, ax=ax, label="Efficiency [-]")
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    return _save(fig, path)


def overlay_prop_line(powertrain: Powertrain, path: str | Path, soc: float = 1.0) -> Path:
    """Efficiency map with the propeller's throttle-sweep operating line overlaid."""

    sweep = powertrain.throttle_sweep(np.linspace(0.1, 1.0, 19), soc)
    line = ([p.rpm for p in sweep], [p.prop_torque_nm for p in sweep])
    max_torque = max(line[1]) * 1.3
    return efficiency_map(
        powertrain.motor,
        powertrain.battery.ocv(soc),
        path,
        max_torque_nm=max_torque,
        operating_line=line,
        operating_label=f"{powertrain.propeller.name} (throttle sweep, with sag)",
    )


def model_vs_stand(points, powertrain: Powertrain, path: str | Path) -> Path:
    """Compare steady-state stand points with the model evaluated at the same throttle/supply."""

    throttle = np.asarray(points["throttle"])
    n = throttle.size
    winding = np.asarray(points["temp_c"]) if "temp_c" in points.data else np.full(n, np.nan)
    magnet = np.asarray(points["magnet_temp_c"]) if "magnet_temp_c" in points.data else winding
    model = [
        powertrain.solve_at_throttle(float(t), 1.0, 0.0, _opt(tw), _opt(tm))
        for t, tw, tm in zip(throttle, winding, magnet, strict=False)
    ]
    fig, axes = plt.subplots(2, 2, figsize=(8.0, 6.0), sharex=True)
    pairs = [
        ("rpm", [m.rpm for m in model], "Speed [rpm]"),
        ("current_a", [m.i_bus_per_motor_a for m in model], "Bus current [A]"),
        ("thrust_n", [m.thrust_n for m in model], "Thrust [N]"),
        ("torque_nm", [m.prop_torque_nm for m in model], "Torque [N·m]"),
    ]
    for ax, (key, predicted, label) in zip(axes.flat, pairs, strict=False):
        if key in points.data:
            err = points.data.get(f"{key}_std")
            ax.errorbar(throttle, points[key], yerr=err, fmt="o", ms=4, label="stand (mean ± 1σ)")
        ax.plot(throttle, predicted, "-", label="model")
        ax.set_ylabel(label)
        ax.grid(alpha=0.3)
    for ax in axes[-1]:
        ax.set_xlabel("Throttle [-]")
    axes[0, 0].legend(fontsize=8)
    temps = "at logged temperatures" if np.isfinite(winding).any() else "at reference temperature"
    fig.suptitle(f"{powertrain.motor.name} + {powertrain.propeller.name}: model ({temps}) vs stand")
    return _save(fig, path)


def thermal_trace(sim: dict, path: str | Path, title: str = "", limits: tuple[float, float] | None = None) -> Path:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7.0, 5.5), sharex=True)
    ax1.plot(sim["time_s"], sim["winding_temp_c"], label="winding")
    ax1.plot(sim["time_s"], sim["stator_temp_c"], label="stator / magnets")
    if limits:
        ax1.axhline(limits[0], color="r", ls="--", lw=1, label="winding limit")
        ax1.axhline(limits[1], color="orange", ls=":", lw=1, label="magnet limit")
    ax1.set_ylabel("Temperature [°C]")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)
    ax2.plot(sim["time_s"], sim["throttle"], "k-", label="throttle")
    ax2b = ax2.twinx()
    ax2b.plot(sim["time_s"], sim["i_motor_a"], "C3-", lw=1, label="motor current")
    ax2.set_ylabel("Throttle [-]")
    ax2b.set_ylabel("Motor current [A]")
    ax2.set_xlabel("Time [s]")
    ax2.grid(alpha=0.3)
    fig.suptitle(title or "Electro-thermal transient")
    return _save(fig, path)


def _opt(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def _save(fig, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(target, dpi=150)
    plt.close(fig)
    return target
