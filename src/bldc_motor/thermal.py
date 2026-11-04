"""Electro-thermal coupling: steady-state temperatures and transient throttle profiles.

Two-node network (see ``ThermalParams``):

    C_w dT_w/dt = P_copper                     - G_ws (T_w - T_s)
    C_s dT_s/dt = P_no_load + G_ws (T_w - T_s) - G_sa(omega) (T_s - T_amb)

Copper loss heats the winding node; iron + friction loss heats the stator/bell node. The
magnet temperature is approximated by the stator/bell node (outrunner magnets sit on the bell
a fraction of a millimetre from the stator teeth). Temperatures feed back into R(T_w) and
Ke(T_s) at every step, so thermal runaway (R rising -> more copper loss) is captured.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from bldc_motor.motor import ThermalParams
from bldc_motor.powertrain import Powertrain, PowertrainPoint


def _require_thermal(powertrain: Powertrain) -> ThermalParams:
    if powertrain.motor.thermal is None:
        raise ValueError(f"Motor '{powertrain.motor.name}' has no thermal parameters")
    return powertrain.motor.thermal


@dataclass(frozen=True)
class SteadyStateThermal:
    point: PowertrainPoint
    winding_temp_c: float
    stator_temp_c: float
    iterations: int
    converged: bool
    within_limits: bool


def steady_state(
    powertrain: Powertrain,
    throttle: float,
    ambient_c: float = 25.0,
    soc: float = 1.0,
    v_inf_m_s: float = 0.0,
    max_iterations: int = 200,
    tolerance_k: float = 0.01,
    runaway_c: float = 400.0,
) -> SteadyStateThermal:
    """Equilibrium temperatures at a constant throttle (damped fixed-point iteration)."""

    thermal = _require_thermal(powertrain)
    t_w = t_s = ambient_c
    point = powertrain.solve_at_throttle(throttle, soc, v_inf_m_s, t_w, t_s)
    converged = False
    iterations = 0
    for _ in range(max_iterations):
        iterations += 1
        g_sa = thermal.g_stator_ambient(point.motor.omega_rad_s)
        new_t_s = ambient_c + point.motor.p_loss_w / g_sa
        new_t_w = new_t_s + point.motor.p_copper_w / thermal.g_winding_stator_w_per_k
        delta = max(abs(new_t_w - t_w), abs(new_t_s - t_s))
        t_w = 0.5 * t_w + 0.5 * new_t_w
        t_s = 0.5 * t_s + 0.5 * new_t_s
        if t_w > runaway_c:
            break
        point = powertrain.solve_at_throttle(throttle, soc, v_inf_m_s, t_w, t_s)
        if delta < tolerance_k:
            converged = True
            break
    motor = powertrain.motor
    within = converged and t_w <= motor.max_winding_temp_c and t_s <= motor.max_magnet_temp_c
    return SteadyStateThermal(point, t_w, t_s, iterations, converged, within)


# --------------------------------------------------------------------------------------
# Throttle profiles
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ThrottleProfile:
    """Throttle vs time from breakpoints; ``step`` holds each value until the next breakpoint."""

    times_s: tuple[float, ...]
    throttles: tuple[float, ...]
    step: bool = True

    def __post_init__(self) -> None:
        if len(self.times_s) != len(self.throttles) or not self.times_s:
            raise ValueError("Profile needs matching, non-empty time and throttle sequences")
        if any(b < a for a, b in zip(self.times_s, self.times_s[1:], strict=False)):
            raise ValueError("Profile times must be non-decreasing")

    def __call__(self, t: float) -> float:
        if self.step:
            index = int(np.searchsorted(self.times_s, t, side="right")) - 1
            return float(self.throttles[max(index, 0)])
        return float(np.interp(t, self.times_s, self.throttles))

    @property
    def end_time_s(self) -> float:
        return float(self.times_s[-1])

    @classmethod
    def parse(cls, text: str, step: bool = True) -> ThrottleProfile:
        """Parse ``"0:0.3, 10:1.0, 15:0.3"`` (time_s:throttle pairs)."""

        pairs = [chunk.split(":") for chunk in text.replace(" ", "").split(",") if chunk]
        times = tuple(float(t) for t, _ in pairs)
        throttles = tuple(float(v) for _, v in pairs)
        return cls(times, throttles, step)


# --------------------------------------------------------------------------------------
# Transient simulation
# --------------------------------------------------------------------------------------


def simulate(
    powertrain: Powertrain,
    throttle_profile: Callable[[float], float],
    duration_s: float,
    dt_s: float = 0.05,
    ambient_c: float = 25.0,
    soc_start: float = 1.0,
    track_soc: bool = True,
    v_inf_m_s: float = 0.0,
) -> dict[str, np.ndarray]:
    """Integrate the coupled electro-thermal model over a throttle profile (Heun / RK2).

    Rotor mechanical dynamics are assumed fast relative to thermal dynamics (ms vs s), so the
    electrical/mechanical state is solved quasi-statically at each step.
    """

    thermal = _require_thermal(powertrain)
    capacity_as = powertrain.battery.capacity_mah / 1000.0 * 3600.0
    steps = int(np.ceil(duration_s / dt_s)) + 1
    times = np.linspace(0.0, (steps - 1) * dt_s, steps)

    def derivatives(t: float, state: np.ndarray) -> tuple[np.ndarray, PowertrainPoint]:
        t_w, t_s, soc = state
        point = powertrain.solve_at_throttle(throttle_profile(t), max(soc, 0.0), v_inf_m_s, t_w, t_s)
        g_ws = thermal.g_winding_stator_w_per_k
        g_sa = thermal.g_stator_ambient(point.motor.omega_rad_s)
        dt_w = (point.motor.p_copper_w - g_ws * (t_w - t_s)) / thermal.c_winding_j_per_k
        dt_s_ = (point.motor.p_no_load_w + g_ws * (t_w - t_s) - g_sa * (t_s - ambient_c)) / thermal.c_stator_j_per_k
        dsoc = -point.i_bus_total_a / capacity_as if track_soc else 0.0
        return np.array([dt_w, dt_s_, dsoc]), point

    keys = [
        "time_s", "throttle", "rpm", "thrust_n", "torque_nm", "v_bus_v", "i_bus_per_motor_a", "i_motor_a",
        "p_bus_per_motor_w", "p_copper_w", "p_no_load_w", "winding_temp_c", "stator_temp_c", "soc",
    ]  # fmt: skip
    record: dict[str, list[float]] = {key: [] for key in keys}
    state = np.array([ambient_c, ambient_c, soc_start], dtype=float)
    for index, t in enumerate(times):
        k1, point = derivatives(t, state)
        _store(record, t, throttle_profile(t), point, state)
        if index == steps - 1:
            break
        predictor = state + dt_s * k1
        k2, _ = derivatives(t + dt_s, predictor)
        state = state + 0.5 * dt_s * (k1 + k2)
    return {key: np.asarray(values, dtype=float) for key, values in record.items()}


def _store(
    record: dict[str, list[float]], t: float, throttle: float, point: PowertrainPoint, state: np.ndarray
) -> None:
    record["time_s"].append(t)
    record["throttle"].append(throttle)
    record["rpm"].append(point.rpm)
    record["thrust_n"].append(point.thrust_n)
    record["torque_nm"].append(point.prop_torque_nm)
    record["v_bus_v"].append(point.v_bus)
    record["i_bus_per_motor_a"].append(point.i_bus_per_motor_a)
    record["i_motor_a"].append(point.motor.current_a)
    record["p_bus_per_motor_w"].append(point.p_bus_per_motor_w)
    record["p_copper_w"].append(point.motor.p_copper_w)
    record["p_no_load_w"].append(point.motor.p_no_load_w)
    record["winding_temp_c"].append(state[0])
    record["stator_temp_c"].append(state[1])
    record["soc"].append(state[2])
