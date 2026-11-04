"""Identify motor model parameters from bench data.

Identifiability (derived in docs/motor_development.md): a stand that measures only DC-bus
voltage/current, speed and torque cannot separate Ke, R and I0 on its own. The bus-side
power balance

    V_bus * I_bus - Q * omega = R * I_m^2 + Ke * omega * I0,   I_m = Q/Ke + I0

is invariant under (Ke -> s*Ke, R -> s^2*R, I0 -> I0/s). The scaling is broken by any one of:

* an independent line-to-line resistance measurement (4-wire milliohm meter), or
* points where the effective motor voltage is known -- full throttle (duty = max duty),
  or a no-load run from a DC supply at 100 % throttle.

``fit_motor_from_stand`` therefore requires either ``r_ll_ohm`` or ``fit_resistance=True``
together with full-throttle / no-load points, and refuses otherwise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import curve_fit, least_squares

from bldc_motor import units
from bldc_motor.motor import MotorParams
from bldc_motor.powertrain import ESC
from bldc_motor.teststand import StandLog


def resistance_to_reference(
    r_measured_ohm: float, measured_temp_c: float, ref_temp_c: float = 25.0, alpha=units.COPPER_TEMP_COEFF_PER_K
) -> float:
    """Correct a milliohm-meter reading taken at ``measured_temp_c`` to ``ref_temp_c``."""

    return r_measured_ohm / (1.0 + alpha * (measured_temp_c - ref_temp_c))


def fit_kv_no_load(voltage_v, current_a, rpm, r_ohm: float, series_r_ohm: float = 0.0) -> dict:
    """Kv from a no-load run at 100 % throttle: back-EMF = V - I*R = Ke*omega (fit through origin)."""

    v = np.asarray(voltage_v, float)
    i = np.asarray(current_a, float)
    omega = np.asarray(rpm, float) * units.TWO_PI / 60.0
    emf = v - i * (r_ohm + series_r_ohm)
    ke = float(omega @ emf / (omega @ omega))
    residual = emf - ke * omega
    return {
        "kv_rpm_per_v": units.ke_to_kv(ke),
        "ke_v_s_per_rad": ke,
        "rms_residual_v": float(np.sqrt(np.mean(residual**2))),
        "points": int(v.size),
    }


def fit_no_load_current(rpm, current_a) -> dict:
    """Linear I0(rpm) = i0_a + i0_a_per_krpm * rpm/1000 from no-load points."""

    krpm = np.asarray(rpm, float) / 1000.0
    current = np.asarray(current_a, float)
    if krpm.size < 2 or np.ptp(krpm) == 0.0:
        return {"i0_a": float(np.mean(current)), "i0_a_per_krpm": 0.0, "points": int(krpm.size)}
    slope, intercept = np.polyfit(krpm, current, 1)
    return {"i0_a": float(intercept), "i0_a_per_krpm": float(slope), "points": int(krpm.size)}


def fit_ke_and_resistance(voltage_v, current_a, rpm) -> dict:
    """Joint linear fit V = Ke*omega + R*I for points with *known motor voltage* (e.g. DC supply, 100 %)."""

    omega = np.asarray(rpm, float) * units.TWO_PI / 60.0
    current = np.asarray(current_a, float)
    voltage = np.asarray(voltage_v, float)
    design = np.column_stack([omega, current])
    solution, *_ = np.linalg.lstsq(design, voltage, rcond=None)
    ke, resistance = (float(x) for x in solution)
    scaled = design / np.linalg.norm(design, axis=0)
    condition = float(np.linalg.cond(scaled))
    return {
        "kv_rpm_per_v": units.ke_to_kv(ke),
        "ke_v_s_per_rad": ke,
        "r_ohm": resistance,
        "condition_number": condition,
        "well_conditioned": condition < 30.0,
        "note": "" if condition < 30.0 else "current range too narrow relative to speed range; R poorly determined",
    }


@dataclass
class FitResult:
    motor: MotorParams
    diagnostics: dict = field(default_factory=dict)


def _column(log: StandLog, key: str, default: float | None = None) -> np.ndarray:
    if key in log.data:
        return np.asarray(log.data[key], float)
    if default is None:
        raise KeyError(f"Stand data is missing required column '{key}'")
    return np.full(len(log), default, dtype=float)


def fit_motor_from_stand(
    loaded: StandLog,
    name: str = "fitted_motor",
    r_ll_ohm: float | None = None,
    fit_resistance: bool = False,
    no_load: StandLog | None = None,
    esc: ESC | None = None,
    full_throttle_threshold: float = 0.995,
    trust_duty: bool = False,
    ref_temp_c: float = 25.0,
    template: MotorParams | None = None,
) -> FitResult:
    """Nonlinear least-squares fit of Ke, I0(omega) and optionally R to steady-state stand points.

    ``loaded`` needs ``voltage_v, current_a, rpm, torque_nm, throttle`` (bus-side, averaged
    steady-state points). ``no_load`` points (no propeller) may omit torque (taken as zero).
    The ESC duty at each point is inferred from the voltage equation unless the point is at
    full throttle or ``trust_duty`` is set (then duty = throttle * max_duty).
    ``temp_c``, if present, is the winding temperature used to correct R per point;
    ``magnet_temp_c`` (bell sensor) corrects Ke per point, falling back to ``temp_c``.
    Fitted Kv, R and I0 are reported at ``ref_temp_c``.
    """

    esc = esc or ESC()
    if r_ll_ohm is None and not fit_resistance:
        raise ValueError("Provide r_ll_ohm (measured) or set fit_resistance=True")

    sets = [loaded] + ([no_load] if no_load is not None else [])
    v_bus = np.concatenate([_column(s, "voltage_v") for s in sets])
    i_bus = np.concatenate([_column(s, "current_a") for s in sets])
    omega = np.concatenate([_column(s, "rpm") for s in sets]) * units.TWO_PI / 60.0
    torque = np.concatenate(
        [_column(loaded, "torque_nm")] + ([_column(no_load, "torque_nm", 0.0)] if no_load is not None else [])
    )
    throttle = np.concatenate([_column(s, "throttle", 1.0) for s in sets])
    temp = np.concatenate([_column(s, "temp_c", ref_temp_c) for s in sets])
    temp = np.where(np.isfinite(temp), temp, ref_temp_c)
    # Magnet temperature: dedicated bell sensor if logged, else the single motor sensor.
    magnet = np.concatenate(
        [
            _column(s, "magnet_temp_c", math.nan) if "magnet_temp_c" in s.data else _column(s, "temp_c", ref_temp_c)
            for s in sets
        ]
    )
    magnet = np.where(np.isfinite(magnet), magnet, ref_temp_c)

    known_duty = trust_duty | (throttle >= full_throttle_threshold)
    if fit_resistance and not known_duty.any():
        raise ValueError(
            "fit_resistance=True needs full-throttle or no-load points (known motor voltage); "
            "otherwise (Ke, R, I0) are not identifiable from bus-side data. Measure R instead."
        )
    keep = (omega > 1.0) & (v_bus > 0.0) & np.isfinite(torque)
    v_bus, i_bus, omega, torque, throttle, temp, magnet, known_duty = (
        a[keep] for a in (v_bus, i_bus, omega, torque, throttle, temp, magnet, known_duty)
    )
    i_net = np.maximum(i_bus - esc.i_quiescent_a, 1.0e-6)
    alpha = template.alpha_cu_per_k if template else units.COPPER_TEMP_COEFF_PER_K
    beta = template.beta_magnet_per_k if template else units.NDFEB_BR_TEMP_COEFF_PER_K
    temp_factor = 1.0 + alpha * (temp - ref_temp_c)
    flux_factor = 1.0 + beta * (magnet - ref_temp_c)

    duty_guess = np.where(known_duty, throttle * esc.max_duty, np.clip(throttle, 0.05, 1.0) * esc.max_duty)
    ke0 = float(np.median(duty_guess * v_bus / omega / flux_factor)) * 0.97
    r0 = r_ll_ohm if r_ll_ohm is not None else 0.05
    x0 = [ke0, 0.5, 0.0] + ([r0] if fit_resistance else [])
    krpm = omega * 60.0 / units.TWO_PI / 1000.0
    torque_scale = max(float(np.max(np.abs(torque))), 1.0e-3) * 0.01
    voltage_scale = max(float(np.max(v_bus)), 1.0) * 0.01

    def unpack(x):
        ke, i0a, i0b = x[0], x[1], x[2]
        r_ref = x[3] if fit_resistance else r_ll_ohm
        return ke, i0a, i0b, r_ref

    def residuals(x):
        ke_ref, i0a, i0b, r_ref = unpack(x)
        ke = ke_ref * flux_factor
        r_tot = r_ref * temp_factor + esc.r_series_ohm
        bemf = ke * omega
        d_inferred = (bemf + np.sqrt(bemf**2 + 4.0 * v_bus * r_tot * i_net)) / (2.0 * v_bus)
        duty = np.where(known_duty, throttle * esc.max_duty, d_inferred)
        i_motor = i_net / duty
        torque_pred = ke * (i_motor - i0a - i0b * krpm)
        res_q = (torque_pred - torque) / torque_scale
        res_v = np.where(known_duty, (duty * v_bus - bemf - r_tot * i_motor) / voltage_scale, 0.0)
        return np.concatenate([res_q, res_v[known_duty]])

    lower = [1.0e-5, 0.0, -10.0] + ([1.0e-4] if fit_resistance else [])
    upper = [1.0, 50.0, 10.0] + ([10.0] if fit_resistance else [])
    solution = least_squares(residuals, x0, bounds=(lower, upper), x_scale="jac", method="trf")
    ke, i0a, i0b, r_ref = unpack(solution.x)

    dof = max(solution.fun.size - solution.x.size, 1)
    s2 = float(solution.fun @ solution.fun) / dof
    try:
        cov = np.linalg.inv(solution.jac.T @ solution.jac) * s2
        std = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    except np.linalg.LinAlgError:
        std = np.full(solution.x.size, math.nan)
    kv = units.ke_to_kv(ke)
    final = residuals(solution.x)
    n_q = torque.size
    diagnostics = {
        "success": bool(solution.success),
        "message": str(solution.message),
        "points": int(n_q),
        "known_duty_points": int(known_duty.sum()),
        "kv_rpm_per_v": kv,
        "kv_std": kv * float(std[0]) / ke,
        "i0_a": i0a,
        "i0_a_std": float(std[1]),
        "i0_a_per_krpm": i0b,
        "i0_a_per_krpm_std": float(std[2]),
        "r_ll_ohm": r_ref,
        "r_ll_ohm_std": float(std[3]) if fit_resistance else 0.0,
        "r_fitted": fit_resistance,
        "torque_rms_nm": float(np.sqrt(np.mean((final[:n_q] * torque_scale) ** 2))),
        "torque_rms_rel": float(
            np.sqrt(np.mean((final[:n_q] * torque_scale) ** 2)) / max(np.mean(np.abs(torque)), 1e-9)
        ),
    }
    base = template or MotorParams(name=name, kv_rpm_per_v=kv, r_ll_ohm=r_ref, i0_a=max(i0a, 0.0))
    motor = base.with_updates(
        name=name,
        kv_rpm_per_v=kv,
        r_ll_ohm=r_ref,
        i0_a=max(i0a, 0.0),
        i0_a_per_krpm=i0b,
        ref_temp_c=ref_temp_c,
        source="fitted from thrust-stand data",
    )
    return FitResult(motor, diagnostics)


def fit_first_order_thermal(time_s, temp_c, power_loss_w: float | None = None) -> dict:
    """Fit T(t) = T0 + dT * (1 - exp(-(t - t0)/tau)) to a constant-load heating curve.

    With the (constant) loss power known, returns the lumped thermal resistance R_th = dT/P and
    capacitance C_th = tau/R_th. A single exponential is the dominant mode of the two-node
    network; use it to set the stator-ambient conductance at that speed.
    """

    t = np.asarray(time_s, float)
    temp = np.asarray(temp_c, float)
    t = t - t[0]

    def model(tt, t0, dt, tau):
        return t0 + dt * (1.0 - np.exp(-tt / tau))

    guess = (float(temp[0]), float(temp[-1] - temp[0]) * 1.2 + 1e-3, max(float(t[-1]) / 3.0, 1e-3))
    params, cov = curve_fit(model, t, temp, p0=guess, maxfev=20000)
    std = np.sqrt(np.diag(cov))
    result = {
        "t0_c": float(params[0]),
        "delta_t_k": float(params[1]),
        "tau_s": float(params[2]),
        "tau_std_s": float(std[2]),
        "steady_temp_c": float(params[0] + params[1]),
        "rms_residual_k": float(np.sqrt(np.mean((model(t, *params) - temp) ** 2))),
        "record_length_over_tau": float(t[-1] / params[2]),
    }
    if power_loss_w:
        r_th = params[1] / power_loss_w
        result["r_th_k_per_w"] = float(r_th)
        result["g_th_w_per_k"] = float(1.0 / r_th)
        result["c_th_j_per_k"] = float(params[2] / r_th)
    return result
