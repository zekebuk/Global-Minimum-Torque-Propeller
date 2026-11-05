"""Close the loop: correct the analytical model with measurements of built prototypes.

Each prototype gives measured Kv, R_ll and I0(omega) (from ``bldc-motor fit``) and optionally a
measured stator-to-ambient conductance (from ``bldc-motor fit-thermal``). The ratio
measured / *uncalibrated* prediction is stored per sample, and each calibration factor is the
geometric mean over the samples. That assumes the model error is systematic (the same sign and
similar size across designs of this family), which is the usual situation for first-order
sizing models.

No-load loss separation: the constant part of I0 maps to ``hysteresis + friction`` and the
speed-proportional part maps to eddy loss, so two loss factors are identified (eddy -> iron
loss factor, remainder -> friction factor).
"""

from __future__ import annotations

import math

from bldc_motor import units
from bldc_motor.design import model
from bldc_motor.motor import MotorParams


def prototype_ratios(
    design: dict, measured: MotorParams, g_sa_measured_w_per_k: float | None = None, omega_rad_s: float | None = None
) -> dict:
    predicted_analysis = model.analyze(design, model.Calibration())
    predicted = predicted_analysis.motor
    if predicted is None:
        raise ValueError("Design is not analysable")
    internals = predicted_analysis.internals
    ke_meas = measured.ke_ref
    ratios = {
        "kv_factor": measured.kv_rpm_per_v / predicted.kv_rpm_per_v,
        "resistance_factor": measured.r_ll_ohm / predicted.r_ll_ohm,
    }
    # Loss coefficients in W/(rad/s) and W/(rad/s)^2, from the measured DC-equivalent model.
    meas_const_w = measured.i0_a * ke_meas
    meas_eddy_w = measured.i0_a_per_krpm / units.rpm_to_rad_s(1000.0) * ke_meas
    pred_eddy = internals["eddy_w_per_rad_s2"]
    pred_hyst = internals["hysteresis_w_per_rad_s"]
    pred_fric = internals["friction_torque_nm"]
    if pred_eddy > 0.0 and meas_eddy_w > 0.0:
        ratios["iron_loss_factor"] = meas_eddy_w / pred_eddy
        iron = ratios["iron_loss_factor"]
    else:
        iron = 1.0
    friction_part = meas_const_w - iron * pred_hyst
    ratios["friction_factor"] = max(friction_part / pred_fric, 0.1) if pred_fric > 0 else 1.0
    if g_sa_measured_w_per_k is not None:
        omega = omega_rad_s if omega_rad_s is not None else predicted.thermal.omega_ref_rad_s
        ratios["convection_factor"] = g_sa_measured_w_per_k / predicted.thermal.g_stator_ambient(omega)
    return ratios


def update_calibration(calibration: model.Calibration, new_ratios: dict) -> model.Calibration:
    """Add one prototype's ratios and recompute every factor as a geometric mean."""

    history = [*calibration.history, new_ratios]
    factors = {}
    for key in ("kv_factor", "resistance_factor", "iron_loss_factor", "friction_factor", "convection_factor"):
        values = [h[key] for h in history if key in h and h[key] > 0]
        factors[key] = math.exp(sum(math.log(v) for v in values) / len(values)) if values else getattr(calibration, key)
    return model.Calibration(
        **factors, samples=len(history), source="geometric mean of prototype / model ratios", history=history
    )
