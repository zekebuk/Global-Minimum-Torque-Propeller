"""Quasi-steady DC-equivalent model of a BLDC / PMSM outrunner.

Model (time-averaged over many electrical cycles, see docs/motor_development.md):

    V   = Ke(T_mag) * omega + R(T_wdg) * I
    Q   = Ke(T_mag) * (I - I0(omega))
    I0  = i0_a + i0_slope * omega                      (no-load loss current)
    R   = R_ref * (1 + alpha_cu * (T_wdg - T_ref))
    Ke  = Ke_ref * (1 + beta_mag * (T_mag - T_ref))

``V`` is the effective DC-equivalent voltage applied by the ESC and ``R`` is the
line-to-line resistance (two phases conduct in series for six-step commutation, and the
line-to-line value is also the correct DC-equivalent for sinusoidal FOC when Kv is
measured the hobby way). Power balance is exact by construction:

    V*I = I^2*R  +  Ke*omega*I0  +  Q*omega
          copper    no-load (iron + friction)   shaft
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace

from bldc_motor import units


@dataclass(frozen=True)
class ThermalParams:
    """Two-node lumped thermal network: winding -> stator/bell -> ambient.

    The stator-to-ambient conductance grows with rotor speed because the spinning bell and
    prop wash drive forced convection: ``G_sa = g_static + g_ref * (omega/omega_ref)**n``.
    ``n`` = 0.8 corresponds to a turbulent-boundary-layer Nusselt scaling (Nu ~ Re^0.8).
    """

    c_winding_j_per_k: float
    c_stator_j_per_k: float
    g_winding_stator_w_per_k: float
    g_stator_ambient_static_w_per_k: float
    g_stator_ambient_ref_w_per_k: float
    omega_ref_rad_s: float = 3000.0
    convection_exponent: float = 0.8

    def g_stator_ambient(self, omega_rad_s: float) -> float:
        speed_ratio = max(float(omega_rad_s), 0.0) / self.omega_ref_rad_s
        return (
            self.g_stator_ambient_static_w_per_k
            + self.g_stator_ambient_ref_w_per_k * speed_ratio**self.convection_exponent
        )


@dataclass(frozen=True)
class MotorParams:
    """Parameters of one motor.

    ``i0_a`` and ``i0_a_per_krpm`` describe the no-load current as a function of speed:
    constant current <=> loss proportional to speed (bearing friction + hysteresis);
    linear-in-speed current <=> loss proportional to speed^2 (eddy currents).
    """

    name: str
    kv_rpm_per_v: float
    r_ll_ohm: float
    i0_a: float
    i0_a_per_krpm: float = 0.0
    ref_temp_c: float = 25.0
    alpha_cu_per_k: float = units.COPPER_TEMP_COEFF_PER_K
    beta_magnet_per_k: float = units.NDFEB_BR_TEMP_COEFF_PER_K
    pole_pairs: int = 7
    mass_g: float | None = None
    max_current_a: float | None = None
    max_winding_temp_c: float = 150.0
    max_magnet_temp_c: float = 120.0
    thermal: ThermalParams | None = None
    source: str = "unspecified"
    notes: str = ""

    def __post_init__(self) -> None:
        if self.kv_rpm_per_v <= 0.0:
            raise ValueError(f"{self.name}: Kv must be positive")
        if self.r_ll_ohm <= 0.0:
            raise ValueError(f"{self.name}: resistance must be positive")
        if self.i0_a < 0.0:
            raise ValueError(f"{self.name}: no-load current must be non-negative")

    # ---- constants -------------------------------------------------------------------
    @property
    def ke_ref(self) -> float:
        """Back-EMF constant at the reference temperature [V*s/rad] (= Kt [N*m/A])."""

        return units.kv_to_ke(self.kv_rpm_per_v)

    @property
    def i0_slope_a_per_rad_s(self) -> float:
        return self.i0_a_per_krpm / units.rpm_to_rad_s(1000.0)

    @property
    def km_nm_per_sqrt_w(self) -> float:
        """Motor constant Km = Kt / sqrt(R): torque per square-root watt of copper loss."""

        return self.ke_ref / math.sqrt(self.r_ll_ohm)

    def ke_at(self, magnet_temp_c: float | None = None) -> float:
        if magnet_temp_c is None:
            return self.ke_ref
        return self.ke_ref * (1.0 + self.beta_magnet_per_k * (magnet_temp_c - self.ref_temp_c))

    def resistance_at(self, winding_temp_c: float | None = None) -> float:
        if winding_temp_c is None:
            return self.r_ll_ohm
        return self.r_ll_ohm * (1.0 + self.alpha_cu_per_k * (winding_temp_c - self.ref_temp_c))

    def no_load_current(self, omega_rad_s: float) -> float:
        return self.i0_a + self.i0_slope_a_per_rad_s * max(float(omega_rad_s), 0.0)

    # ---- characteristic points -------------------------------------------------------
    def no_load_speed(
        self, voltage_v: float, series_r_ohm: float = 0.0, winding_temp_c=None, magnet_temp_c=None
    ) -> float:
        """Speed [rad/s] where shaft torque is zero at a given applied voltage."""

        ke = self.ke_at(magnet_temp_c)
        r_total = self.resistance_at(winding_temp_c) + series_r_ohm
        omega = (voltage_v - r_total * self.i0_a) / (ke + r_total * self.i0_slope_a_per_rad_s)
        return max(omega, 0.0)

    def stall_torque(
        self, voltage_v: float, series_r_ohm: float = 0.0, winding_temp_c=None, magnet_temp_c=None
    ) -> float:
        ke = self.ke_at(magnet_temp_c)
        r_total = self.resistance_at(winding_temp_c) + series_r_ohm
        return ke * (voltage_v / r_total - self.i0_a)

    # ---- evaluation ------------------------------------------------------------------
    def evaluate(self, voltage_v: float, omega_rad_s: float, winding_temp_c=None, magnet_temp_c=None) -> MotorPoint:
        """Evaluate the motor at a known terminal voltage and speed."""

        ke = self.ke_at(magnet_temp_c)
        resistance = self.resistance_at(winding_temp_c)
        current = (voltage_v - ke * omega_rad_s) / resistance
        return self._point(voltage_v, current, omega_rad_s, ke, resistance, winding_temp_c, magnet_temp_c)

    def evaluate_at_torque(
        self, torque_nm: float, omega_rad_s: float, winding_temp_c=None, magnet_temp_c=None
    ) -> MotorPoint:
        """Evaluate the motor at a required shaft torque and speed (inverse problem)."""

        ke = self.ke_at(magnet_temp_c)
        resistance = self.resistance_at(winding_temp_c)
        current = torque_nm / ke + self.no_load_current(omega_rad_s)
        voltage = ke * omega_rad_s + current * resistance
        return self._point(voltage, current, omega_rad_s, ke, resistance, winding_temp_c, magnet_temp_c)

    def _point(self, voltage, current, omega, ke, resistance, winding_temp_c, magnet_temp_c) -> MotorPoint:
        i0 = self.no_load_current(omega)
        torque = ke * (current - i0)
        p_elec = voltage * current
        p_shaft = torque * omega
        p_copper = current**2 * resistance
        p_no_load = ke * omega * i0
        efficiency = p_shaft / p_elec if p_elec > 0.0 and p_shaft > 0.0 else 0.0
        return MotorPoint(
            voltage_v=voltage,
            current_a=current,
            omega_rad_s=omega,
            torque_nm=torque,
            p_elec_w=p_elec,
            p_shaft_w=p_shaft,
            p_copper_w=p_copper,
            p_no_load_w=p_no_load,
            efficiency=efficiency,
            resistance_ohm=resistance,
            ke_v_s_per_rad=ke,
            winding_temp_c=winding_temp_c,
            magnet_temp_c=magnet_temp_c,
        )

    # ---- serialization ---------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, name: str, data: dict) -> MotorParams:
        payload = dict(data)
        payload.pop("name", None)
        thermal = payload.pop("thermal", None)
        return cls(name=name, thermal=ThermalParams(**thermal) if thermal else None, **payload)

    def with_updates(self, **changes) -> MotorParams:
        return replace(self, **changes)

    def rewound(self, new_kv_rpm_per_v: float, name: str | None = None) -> MotorParams:
        """Same stator and magnets, different turn count N, same copper slot fill.

        Kv ~ 1/N; wire length ~ N and wire area ~ 1/N give R ~ N^2 ~ 1/Kv^2. Iron and friction
        loss at a given rpm are unchanged, so the equivalent no-load current scales ~ Kv.
        Km = Kt/sqrt(R) is invariant: a rewind trades voltage for current, not torque per loss.
        """

        ratio = new_kv_rpm_per_v / self.kv_rpm_per_v
        max_current = self.max_current_a * ratio if self.max_current_a is not None else None
        return replace(
            self,
            name=name or f"{self.name}_rewound_{new_kv_rpm_per_v:g}kv",
            kv_rpm_per_v=new_kv_rpm_per_v,
            r_ll_ohm=self.r_ll_ohm / ratio**2,
            i0_a=self.i0_a * ratio,
            i0_a_per_krpm=self.i0_a_per_krpm * ratio,
            max_current_a=max_current,
            source=f"rewind of {self.name}",
        )


@dataclass(frozen=True)
class MotorPoint:
    voltage_v: float
    current_a: float
    omega_rad_s: float
    torque_nm: float
    p_elec_w: float
    p_shaft_w: float
    p_copper_w: float
    p_no_load_w: float
    efficiency: float
    resistance_ohm: float
    ke_v_s_per_rad: float
    winding_temp_c: float | None = None
    magnet_temp_c: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def rpm(self) -> float:
        return units.rad_s_to_rpm(self.omega_rad_s)

    @property
    def p_loss_w(self) -> float:
        return self.p_copper_w + self.p_no_load_w
