"""Battery -> ESC -> motor -> propeller operating-point solver.

Assumptions (all quasi-steady, time-averaged):

* The ESC is an ideal buck stage with duty ``d`` = throttle (linear throttle mapping, as in
  BLHeli_32 / AM32 / Bluejay defaults with no throttle curve). Its conduction losses appear as
  a series resistance ``r_series_ohm`` (two conducting MOSFETs + PCB + motor leads):
  ``V_motor = d * V_bus - I_motor * r_series``.
* Bus current by power conservation of the ideal switch: ``I_bus = d * I_motor + I_quiescent``.
* ``n_motors`` identical motors run at the same throttle from one pack (symmetric hover /
  full-throttle punch-out); the pack sags as ``V_bus = V_oc(SoC) - (R_pack + R_leads) * I_total``.
* Regeneration (negative bus current) is not modeled.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy.optimize import brentq

from bldc_motor import units
from bldc_motor.motor import MotorParams, MotorPoint
from bldc_motor.propeller import PropellerModel

# Generic resting open-circuit voltage of a LiPo cell vs state of charge. Approximate,
# chemistry- and age-dependent; override with a measured table for real packs.
LIPO_OCV_TABLE: tuple[tuple[float, float], ...] = (
    (0.00, 3.27),
    (0.05, 3.61),
    (0.10, 3.69),
    (0.15, 3.71),
    (0.20, 3.73),
    (0.25, 3.75),
    (0.30, 3.77),
    (0.35, 3.79),
    (0.40, 3.80),
    (0.45, 3.82),
    (0.50, 3.84),
    (0.55, 3.85),
    (0.60, 3.87),
    (0.65, 3.91),
    (0.70, 3.95),
    (0.75, 3.98),
    (0.80, 4.02),
    (0.85, 4.08),
    (0.90, 4.11),
    (0.95, 4.15),
    (1.00, 4.20),
)
# LiHV: same shape, scaled to a 4.35 V full charge.
LIHV_OCV_TABLE: tuple[tuple[float, float], ...] = tuple((soc, v * 4.35 / 4.20) for soc, v in LIPO_OCV_TABLE)
OCV_TABLES = {"lipo": LIPO_OCV_TABLE, "lihv": LIHV_OCV_TABLE}


@dataclass(frozen=True)
class Battery:
    """Battery pack. ``capacity_mah`` is the whole-pack label capacity; ``r_cell_ohm`` is per cell."""

    name: str
    cells_series: int
    capacity_mah: float
    r_cell_ohm: float
    cells_parallel: int = 1
    chemistry: str = "lipo"
    mass_g: float | None = None
    usable_fraction: float = 0.8
    ocv_table: tuple[tuple[float, float], ...] | None = None
    min_cell_v_under_load: float = 3.3
    source: str = "unspecified"
    notes: str = ""

    def _table(self) -> tuple[tuple[float, float], ...]:
        if self.ocv_table is not None:
            return self.ocv_table
        if self.chemistry not in OCV_TABLES:
            raise ValueError(f"No OCV table for chemistry '{self.chemistry}'; pass ocv_table explicitly")
        return OCV_TABLES[self.chemistry]

    def cell_ocv(self, soc: float) -> float:
        table = self._table()
        soc_points = [p[0] for p in table]
        volts = [p[1] for p in table]
        return float(np.interp(min(max(soc, 0.0), 1.0), soc_points, volts))

    def ocv(self, soc: float = 1.0) -> float:
        return self.cells_series * self.cell_ocv(soc)

    @property
    def resistance_ohm(self) -> float:
        return self.r_cell_ohm * self.cells_series / self.cells_parallel

    @property
    def nominal_voltage(self) -> float:
        return self.ocv(0.5)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, name: str, data: dict) -> Battery:
        payload = dict(data)
        payload.pop("name", None)
        if payload.get("ocv_table") is not None:
            payload["ocv_table"] = tuple(tuple(p) for p in payload["ocv_table"])
        return cls(name=name, **payload)


@dataclass(frozen=True)
class ESC:
    name: str = "generic_esc"
    r_series_ohm: float = 0.008
    i_quiescent_a: float = 0.05
    max_duty: float = 1.0
    max_current_a: float | None = None
    r_leads_ohm: float = 0.004
    source: str = "unspecified"
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, name: str, data: dict) -> ESC:
        payload = dict(data)
        payload.pop("name", None)
        return cls(name=name, **payload)


@dataclass(frozen=True)
class PowertrainPoint:
    throttle: float
    duty: float
    v_oc: float
    v_bus: float
    i_bus_per_motor_a: float
    i_bus_total_a: float
    motor: MotorPoint
    thrust_n: float
    prop_torque_nm: float
    p_bus_per_motor_w: float
    p_esc_loss_w: float
    p_battery_loss_w: float
    n_motors: int
    stalled: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def rpm(self) -> float:
        return self.motor.rpm

    @property
    def drive_efficiency(self) -> float:
        """Shaft power / ESC input power (motor + ESC)."""

        return self.motor.p_shaft_w / self.p_bus_per_motor_w if self.p_bus_per_motor_w > 0.0 else 0.0

    @property
    def grams_per_watt(self) -> float:
        """Thrust per ESC-input watt, the usual FPV thrust-stand efficiency metric."""

        return units.n_to_grams_force(self.thrust_n) / self.p_bus_per_motor_w if self.p_bus_per_motor_w > 0.0 else 0.0

    def as_row(self) -> dict:
        return {
            "throttle": self.throttle,
            "duty": self.duty,
            "v_oc_v": self.v_oc,
            "v_bus_v": self.v_bus,
            "v_motor_v": self.motor.voltage_v,
            "i_motor_a": self.motor.current_a,
            "i_bus_per_motor_a": self.i_bus_per_motor_a,
            "i_bus_total_a": self.i_bus_total_a,
            "rpm": self.rpm,
            "thrust_n": self.thrust_n,
            "thrust_gf": units.n_to_grams_force(self.thrust_n),
            "torque_nm": self.prop_torque_nm,
            "p_bus_per_motor_w": self.p_bus_per_motor_w,
            "p_shaft_w": self.motor.p_shaft_w,
            "p_copper_w": self.motor.p_copper_w,
            "p_no_load_w": self.motor.p_no_load_w,
            "p_esc_loss_w": self.p_esc_loss_w,
            "motor_efficiency": self.motor.efficiency,
            "drive_efficiency": self.drive_efficiency,
            "grams_per_watt": self.grams_per_watt,
            "winding_temp_c": self.motor.winding_temp_c,
            "magnet_temp_c": self.motor.magnet_temp_c,
            "stalled": self.stalled,
        }


@dataclass(frozen=True)
class Powertrain:
    motor: MotorParams
    propeller: PropellerModel
    battery: Battery
    esc: ESC = ESC()
    n_motors: int = 4
    rho: float = units.SEA_LEVEL_AIR_DENSITY_KG_M3

    # ---- inner solve: fixed bus voltage ------------------------------------------------
    def solve_at_bus_voltage(
        self,
        throttle: float,
        v_bus: float,
        v_inf_m_s: float = 0.0,
        winding_temp_c: float | None = None,
        magnet_temp_c: float | None = None,
    ) -> tuple[MotorPoint, float, bool]:
        """Solve the motor/prop torque balance at a fixed bus voltage.

        Returns (motor point, thrust, stalled flag).
        """

        duty = min(max(throttle, 0.0), 1.0) * self.esc.max_duty
        source_v = duty * v_bus
        motor = self.motor
        ke = motor.ke_at(magnet_temp_c)
        r_total = motor.resistance_at(winding_temp_c) + self.esc.r_series_ohm

        def motor_torque(omega: float) -> float:
            current = (source_v - ke * omega) / r_total
            return ke * (current - motor.no_load_current(omega))

        def residual(omega: float) -> float:
            return motor_torque(omega) - self.propeller.torque(omega, v_inf_m_s, self.rho)

        if source_v <= 0.0 or motor_torque(0.0) <= 0.0:
            # Applied voltage cannot overcome friction: rotor stays at rest, current limited by R.
            current = max(source_v, 0.0) / r_total
            point = motor.evaluate(current * motor.resistance_at(winding_temp_c), 0.0, winding_temp_c, magnet_temp_c)
            return _zero_shaft(point), 0.0, True

        omega_max = motor.no_load_speed(source_v, self.esc.r_series_ohm, winding_temp_c, magnet_temp_c)
        if residual(omega_max) >= 0.0:  # unloaded (or negligible load): runs at no-load speed
            omega = omega_max
        else:
            omega = brentq(residual, 0.0, omega_max, xtol=1.0e-9, rtol=1.0e-12, maxiter=200)
        current = (source_v - ke * omega) / r_total
        motor_voltage = source_v - current * self.esc.r_series_ohm
        point = motor.evaluate(motor_voltage, omega, winding_temp_c, magnet_temp_c)
        return point, self.propeller.thrust(omega, v_inf_m_s, self.rho), False

    # ---- outer solve: battery sag -------------------------------------------------------
    def solve_at_throttle(
        self,
        throttle: float,
        soc: float = 1.0,
        v_inf_m_s: float = 0.0,
        winding_temp_c: float | None = None,
        magnet_temp_c: float | None = None,
    ) -> PowertrainPoint:
        """Operating point at a commanded throttle including pack sag shared by ``n_motors``."""

        v_oc = self.battery.ocv(soc)
        r_source = self.battery.resistance_ohm + self.esc.r_leads_ohm
        duty = min(max(throttle, 0.0), 1.0) * self.esc.max_duty

        def bus_current_per_motor(v_bus: float) -> tuple[float, MotorPoint, float, bool]:
            point, thrust, stalled = self.solve_at_bus_voltage(
                throttle, v_bus, v_inf_m_s, winding_temp_c, magnet_temp_c
            )
            i_bus = duty * max(point.current_a, 0.0) + self.esc.i_quiescent_a
            return i_bus, point, thrust, stalled

        def residual(v_bus: float) -> float:
            i_bus, *_ = bus_current_per_motor(v_bus)
            return v_bus - v_oc + r_source * self.n_motors * i_bus

        lower = 1.0e-6 * v_oc
        if residual(lower) >= 0.0:
            raise RuntimeError("Battery cannot supply the load: pack resistance too high for this operating point")
        v_bus = brentq(residual, lower, v_oc, xtol=1.0e-9, rtol=1.0e-12, maxiter=200)
        i_bus, point, thrust, stalled = bus_current_per_motor(v_bus)
        p_bus = v_bus * i_bus
        p_esc_loss = p_bus - point.p_elec_w
        i_total = self.n_motors * i_bus
        return PowertrainPoint(
            throttle=throttle,
            duty=duty,
            v_oc=v_oc,
            v_bus=v_bus,
            i_bus_per_motor_a=i_bus,
            i_bus_total_a=i_total,
            motor=point,
            thrust_n=thrust,
            prop_torque_nm=point.torque_nm,
            p_bus_per_motor_w=p_bus,
            p_esc_loss_w=p_esc_loss,
            p_battery_loss_w=i_total**2 * r_source,
            n_motors=self.n_motors,
            stalled=stalled,
        )

    def solve_for_thrust(
        self,
        thrust_n: float,
        soc: float = 1.0,
        v_inf_m_s: float = 0.0,
        winding_temp_c: float | None = None,
        magnet_temp_c: float | None = None,
    ) -> PowertrainPoint | None:
        """Throttle that produces ``thrust_n`` per motor; ``None`` if unreachable at full throttle."""

        def residual(throttle: float) -> float:
            return self.solve_at_throttle(throttle, soc, v_inf_m_s, winding_temp_c, magnet_temp_c).thrust_n - thrust_n

        if residual(1.0) < 0.0:
            return None
        if thrust_n <= 0.0:
            return self.solve_at_throttle(0.0, soc, v_inf_m_s, winding_temp_c, magnet_temp_c)
        throttle = brentq(residual, 0.0, 1.0, xtol=1.0e-7, maxiter=200)
        return self.solve_at_throttle(throttle, soc, v_inf_m_s, winding_temp_c, magnet_temp_c)

    def solve_at_speed(
        self,
        omega_rad_s: float,
        soc: float = 1.0,
        v_inf_m_s: float = 0.0,
        winding_temp_c: float | None = None,
        magnet_temp_c: float | None = None,
    ) -> PowertrainPoint | None:
        """Inverse solve: the throttle that spins the prop at ``omega``; ``None`` if duty > max.

        Closed form. The motor needs ``V_src = V_m + I*R_esc`` at current ``I``. With ``d*V_bus = V_src``
        and ``V_bus = V_oc - R_s*n*(d*I + I_q)``:
        ``V_bus^2 - (V_oc - R_s*n*I_q)*V_bus + R_s*n*V_src*I = 0`` (take the larger, stable root).
        """

        torque = self.propeller.torque(omega_rad_s, v_inf_m_s, self.rho)
        point = self.motor.evaluate_at_torque(torque, omega_rad_s, winding_temp_c, magnet_temp_c)
        v_src = point.voltage_v + point.current_a * self.esc.r_series_ohm
        v_oc = self.battery.ocv(soc)
        r_source = self.battery.resistance_ohm + self.esc.r_leads_ohm
        n = self.n_motors
        b = v_oc - r_source * n * self.esc.i_quiescent_a
        disc = b * b - 4.0 * r_source * n * v_src * point.current_a
        if disc < 0.0:
            return None
        v_bus = 0.5 * (b + math.sqrt(disc))
        duty = v_src / v_bus
        if duty > self.esc.max_duty + 1e-12:
            return None
        i_bus = duty * point.current_a + self.esc.i_quiescent_a
        p_bus = v_bus * i_bus
        return PowertrainPoint(
            throttle=duty / self.esc.max_duty,
            duty=duty,
            v_oc=v_oc,
            v_bus=v_bus,
            i_bus_per_motor_a=i_bus,
            i_bus_total_a=n * i_bus,
            motor=point,
            thrust_n=self.propeller.thrust(omega_rad_s, v_inf_m_s, self.rho),
            prop_torque_nm=torque,
            p_bus_per_motor_w=p_bus,
            p_esc_loss_w=p_bus - point.p_elec_w,
            p_battery_loss_w=(n * i_bus) ** 2 * r_source,
            n_motors=n,
        )

    def solve_for_thrust_direct(
        self,
        thrust_n: float,
        soc: float = 1.0,
        v_inf_m_s: float = 0.0,
        winding_temp_c: float | None = None,
        magnet_temp_c: float | None = None,
    ) -> PowertrainPoint | None:
        """Fast equivalent of ``solve_for_thrust``: invert the prop for speed, then ``solve_at_speed``."""

        if thrust_n <= 0.0:
            return self.solve_at_throttle(0.0, soc, v_inf_m_s, winding_temp_c, magnet_temp_c)
        prop = self.propeller
        if v_inf_m_s == 0.0:
            ct = prop.ct(0.0)
            if ct <= 0.0:
                return None
            omega = units.TWO_PI * math.sqrt(thrust_n / (ct * self.rho * prop.diameter_m**4))
        else:
            hi = units.rpm_to_rad_s(1000.0)
            while prop.thrust(hi, v_inf_m_s, self.rho) < thrust_n:
                hi *= 2.0
                if hi > 1.0e6:
                    return None
            omega = brentq(lambda w: prop.thrust(w, v_inf_m_s, self.rho) - thrust_n, 1e-3, hi, xtol=1e-9)
        return self.solve_at_speed(omega, soc, v_inf_m_s, winding_temp_c, magnet_temp_c)

    def throttle_sweep(self, throttles=None, soc: float = 1.0, **kwargs) -> list[PowertrainPoint]:
        values = np.linspace(0.05, 1.0, 20) if throttles is None else throttles
        return [self.solve_at_throttle(float(t), soc, **kwargs) for t in values]


def _zero_shaft(point: MotorPoint) -> MotorPoint:
    """Return a stalled motor point: all electrical input is dissipated in copper."""

    return MotorPoint(
        voltage_v=point.voltage_v,
        current_a=point.current_a,
        omega_rad_s=0.0,
        torque_nm=0.0,
        p_elec_w=point.p_elec_w,
        p_shaft_w=0.0,
        p_copper_w=point.current_a**2 * point.resistance_ohm,
        p_no_load_w=0.0,
        efficiency=0.0,
        resistance_ohm=point.resistance_ohm,
        ke_v_s_per_rad=point.ke_v_s_per_rad,
        winding_temp_c=point.winding_temp_c,
        magnet_temp_c=point.magnet_temp_c,
    )


def hover_endurance_min(battery: Battery, i_total_a: float) -> float:
    """Constant-current endurance estimate using the battery's usable capacity fraction."""

    if i_total_a <= 0.0:
        return math.inf
    usable_ah = battery.capacity_mah / 1000.0 * battery.usable_fraction
    return usable_ah / i_total_a * 60.0
