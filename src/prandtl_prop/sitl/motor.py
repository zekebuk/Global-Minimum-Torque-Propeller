"""Motor, ESC, and battery for the simulator: the electrical half of the rotor model.

First principles (one motor; every rotor has its own):

- **ESC.** An open-loop duty-cycle drive: the motor sees the average voltage u V_bus, u in [0, 1]
  (PX4's normalised motor command, after ``THR_MDL_FAC``). The bus current is u I / eta_esc. With
  synchronous rectification (``active_braking``; BLHeli_32 / AM32 default under DShot) the current
  reverses when the back-EMF exceeds u V_bus, so the motor brakes and regenerates; without it the
  current is clamped at zero and the rotor coasts.
- **Motor.** Ideal DC machine with Kt = 1 / Kv (SI), winding plus ESC resistance R_m, and a no-load
  current I0 that stands for friction and iron loss:
      I = (u V_bus - omega / Kv) / R_m,   Q_motor = Kt (I - I0),   J d(omega)/dt = Q_motor - Q_aero.
  Datasheets give I0 at one test voltage (spin speed omega_t = Kv V_t). Hysteresis loss is a constant
  drag torque (I0 independent of speed), eddy-current loss a torque growing with speed (I0 ~ omega);
  with ``no_load_test_V`` set, I0(omega) = I0_t (omega / omega_t)^``no_load_exponent`` (0.5: an even
  split), otherwise I0 is constant.
- **Battery.** n_s Li-ion polymer cells in series: open-circuit voltage from a typical LiPo
  discharge curve (per cell, against state of charge) and an internal resistance, so
      V_bus = V_oc(SoC) - R_b sum_i I_bus,i,   dSoC/dt = -sum_i I_bus,i / capacity.
  The energy drawn from the cells is the integral of V_oc I_bus.

The steady lumped model in ``multirotor.motor_point`` charges every motor with n R_b at full duty;
here the bus is shared and scaled by each duty cycle, which is what an ESC actually does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Typical LiPo open-circuit voltage per cell against state of charge 0, 0.05, ..., 1 (rested, 25 C).
LIPO_SOC = np.linspace(0.0, 1.0, 21)
LIPO_OCV = np.array(
    [3.27, 3.61, 3.69, 3.71, 3.73, 3.75, 3.77, 3.79, 3.80, 3.82, 3.84, 3.85, 3.87, 3.91, 3.95, 3.98, 4.02, 4.08, 4.11, 4.15, 4.20]
)


@dataclass(frozen=True)
class Motor:
    kv_rpm_per_V: float
    resistance_ohm: float  # winding + ESC
    no_load_current_A: float
    inertia_kg_m2: float  # motor bell + prop
    max_current_A: float
    esc_efficiency: float = 0.97
    active_braking: bool = True
    no_load_test_V: float | None = None  # datasheet test voltage of no_load_current_A (None: constant I0)
    no_load_exponent: float = 0.5

    @property
    def kv_rad(self) -> float:
        return self.kv_rpm_per_V * 2.0 * math.pi / 60.0

    @property
    def kt(self) -> float:
        return 1.0 / self.kv_rad

    def current(self, duty, v_bus, omega):
        current = (np.asarray(duty) * v_bus - np.asarray(omega) / self.kv_rad) / self.resistance_ohm
        return current if self.active_braking else np.maximum(current, 0.0)

    def no_load_current(self, omega=None):
        if self.no_load_test_V is None or omega is None:
            return self.no_load_current_A
        ratio = np.maximum(np.asarray(omega, float), 0.0) / (self.kv_rad * self.no_load_test_V)
        return self.no_load_current_A * ratio**self.no_load_exponent

    def torque(self, current, omega=None):
        return self.kt * (np.asarray(current) - self.no_load_current(omega))

    def bus_current(self, duty, current):
        current = np.asarray(current)
        return np.asarray(duty) * np.where(current >= 0.0, current / self.esc_efficiency, current * self.esc_efficiency)


@dataclass(frozen=True)
class Battery:
    cells: int
    capacity_Ah: float
    resistance_ohm: float

    def open_circuit_V(self, soc):
        return self.cells * np.interp(np.clip(soc, 0.0, 1.0), LIPO_SOC, LIPO_OCV)

    @property
    def energy_Wh(self) -> float:
        """Energy from full to empty at the open-circuit voltage."""

        return self.capacity_Ah * self.cells * float(np.sum(0.5 * (LIPO_OCV[1:] + LIPO_OCV[:-1]) * np.diff(LIPO_SOC)))


def from_vehicle(spec, kv_rpm_per_V: float, prop_inertia_kg_m2: float, sitl) -> tuple[Motor, Battery]:
    """Motor and battery from a ``MultirotorSpec`` (the same numbers the multirotor stage uses)."""

    cells = sitl.cell_count or max(1, round(spec.battery_voltage_V / 3.7))
    motor = Motor(
        kv_rpm_per_V=kv_rpm_per_V,
        resistance_ohm=spec.motor_resistance_ohm + spec.esc_resistance_ohm,
        no_load_current_A=spec.motor_no_load_current_A,
        inertia_kg_m2=spec.motor_rotor_inertia_kg_m2 + prop_inertia_kg_m2,
        max_current_A=spec.motor_max_current_A,
        esc_efficiency=spec.esc_efficiency,
        active_braking=sitl.active_braking,
    )
    battery = Battery(cells, spec.battery_energy_Wh / spec.battery_voltage_V, spec.battery_resistance_ohm)
    return motor, battery


def required_drive(motor: Motor, omega, motor_torque_Nm):
    """Current and motor terminal voltage (u V_bus) that hold ``omega`` against ``motor_torque_Nm``."""

    current = np.asarray(motor_torque_Nm) / motor.kt + motor.no_load_current(omega)
    return current, np.asarray(omega) / motor.kv_rad + current * motor.resistance_ohm


def bus_voltage(battery: Battery, soc: float, motor: Motor, terminal_V: np.ndarray, current_A: np.ndarray) -> float:
    """Bus voltage that delivers the motors' terminal power: V^2 - V_oc V + R_b P_bus = 0 (larger root); NaN if beyond the battery."""

    terminal = np.asarray(terminal_V) * np.asarray(current_A)  # u V_bus I per motor; the ESC loss sits on the bus side
    power = float(np.sum(np.where(terminal >= 0.0, terminal / motor.esc_efficiency, terminal * motor.esc_efficiency)))
    v_oc = float(battery.open_circuit_V(soc))
    disc = v_oc * v_oc - 4.0 * battery.resistance_ohm * power
    return 0.5 * (v_oc + math.sqrt(disc)) if disc >= 0.0 else math.nan


def steady_speed(motor: Motor, duty: float, v_bus: float, aero_torque, omega_max: float = 6000.0) -> float:
    """Rotor speed (rad/s) where the motor torque at ``duty`` balances ``aero_torque(omega)`` (bisection)."""

    def residual(omega: float) -> float:
        return float(motor.torque(motor.current(duty, v_bus, omega), omega)) - float(aero_torque(omega))

    low, high = 1.0e-3, omega_max
    if residual(low) <= 0.0:
        return 0.0
    for _ in range(80):
        mid = 0.5 * (low + high)
        low, high = (mid, high) if residual(mid) > 0.0 else (low, mid)
    return 0.5 * (low + high)
