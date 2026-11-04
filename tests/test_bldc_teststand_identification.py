"""Thrust-stand import and parameter-identification round trips on synthetic data."""

from __future__ import annotations

import numpy as np
import pytest

from bldc_motor import identification, synthetic, teststand, units
from bldc_motor.library import load_library
from bldc_motor.powertrain import Powertrain

LIB = load_library()
MOTOR = LIB.motor("generic_2207_1950kv")
PROP = LIB.propeller("generic_5.1x4.6_tri")
ESC = LIB.esc("generic_esc")
SUPPLY = synthetic.bench_supply(24.0, 0.01)


def _stand_files(tmp_path, seed=1):
    loaded = synthetic.generate_stand_log(Powertrain(MOTOR, PROP, SUPPLY, ESC, n_motors=1), seed=seed)
    no_load = synthetic.generate_stand_log(
        Powertrain(MOTOR, synthetic.unloaded(PROP), SUPPLY, ESC, n_motors=1), (1.0,), seed=seed + 10
    )
    return synthetic.write_stand_csv(loaded, tmp_path / "loaded.csv"), synthetic.write_stand_csv(
        no_load, tmp_path / "no_load.csv"
    )


def test_unit_conversion() -> None:
    assert units.to_si(1.0, "kgf", "force_n") == pytest.approx(9.80665)
    assert units.to_si(100.0, "gf", "force_n") == pytest.approx(0.980665)
    assert units.to_si(1.0, "ozf·in", "torque_nm") == pytest.approx(0.0070615518, rel=1e-6)
    assert units.to_si(1.0, "N·m", "torque_nm") == 1.0
    assert units.to_si(212.0, "°F", "temperature_c") == pytest.approx(100.0)
    assert units.to_si(1500.0, "µs", "signal_us") == 1500.0
    with pytest.raises(ValueError):
        units.to_si(1.0, "furlong", "force_n")


def test_header_aliases_units_and_sign(tmp_path) -> None:
    csv_path = tmp_path / "stand.csv"
    csv_path.write_text(
        "Time (ms),Throttle (%),Voltage (V),Current (mA),eRPM,Thrust (gf),Torque (N·m)\n"
        "0,50,24.0,10000,140000,-650,-0.09\n"
        "100,50,24.0,10000,140000,-651,-0.09\n",
        encoding="utf-8",
    )
    log = teststand.load_stand_csv(csv_path, column_map={"rpm": "eRPM"}, rpm_is_electrical=True, pole_pairs=7)
    assert log["time_s"][1] == pytest.approx(0.1)
    assert log["throttle"][0] == pytest.approx(0.5)
    assert log["current_a"][0] == pytest.approx(10.0)
    assert log["rpm"][0] == pytest.approx(20000.0)
    assert log["thrust_n"][0] == pytest.approx(0.650 * 9.80665)  # sign flipped
    assert any("sign flipped" in w for w in log.warnings)


def test_steady_state_extraction_removes_transients(tmp_path) -> None:
    loaded_csv, _ = _stand_files(tmp_path)
    log = teststand.load_stand_csv(loaded_csv)
    points = teststand.steady_state_points(log, settle_s=1.0)
    assert len(points) == 9
    np.testing.assert_allclose(points["throttle"], [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    # Plateau scatter must be at noise level, i.e. the rpm transient was excluded.
    assert np.nanmax(points["rpm_std"]) < 200.0


def test_canonical_round_trip(tmp_path) -> None:
    loaded_csv, _ = _stand_files(tmp_path)
    log = teststand.add_derived_metrics(teststand.load_stand_csv(loaded_csv), diameter_m=PROP.diameter_m)
    out = log.to_csv(tmp_path / "processed.csv")
    again = teststand.load_stand_csv(out)
    np.testing.assert_allclose(again["thrust_n"], log["thrust_n"], rtol=1e-8)
    assert "magnet_temp_c" in again and not again.warnings


def test_derived_metrics_recover_prop_coefficients(tmp_path) -> None:
    loaded_csv, _ = _stand_files(tmp_path)
    points = teststand.add_derived_metrics(
        teststand.steady_state_points(teststand.load_stand_csv(loaded_csv)), PROP.diameter_m
    )
    np.testing.assert_allclose(points["ct"], PROP.ct(), rtol=0.01)
    np.testing.assert_allclose(points["cp"], PROP.cp(), rtol=0.01)


def test_bus_side_scaling_degeneracy() -> None:
    """Documents why Kv needs a known-voltage anchor: (s*Ke, s^2*R, I0/s) leaves bus losses unchanged."""

    s = 1.15
    omega, torque = units.rpm_to_rad_s(25000.0), 0.12
    ke, r, i0 = MOTOR.ke_ref, MOTOR.r_ll_ohm, MOTOR.i0_a

    def losses(ke_, r_, i0_):
        i_m = torque / ke_ + i0_
        return r_ * i_m**2 + ke_ * omega * i0_

    assert losses(ke, r, i0) == pytest.approx(losses(s * ke, s**2 * r, i0 / s))


def test_fit_with_measured_resistance(tmp_path) -> None:
    loaded_csv, _ = _stand_files(tmp_path)
    points = teststand.steady_state_points(teststand.load_stand_csv(loaded_csv))
    result = identification.fit_motor_from_stand(points, r_ll_ohm=MOTOR.r_ll_ohm, esc=ESC, template=MOTOR)
    assert result.diagnostics["success"]
    assert result.motor.kv_rpm_per_v == pytest.approx(MOTOR.kv_rpm_per_v, rel=0.005)
    assert result.motor.i0_a == pytest.approx(MOTOR.i0_a, rel=0.15)
    assert result.motor.thermal == MOTOR.thermal  # template properties kept


def test_fit_resistance_with_no_load_anchor(tmp_path) -> None:
    loaded_csv, no_load_csv = _stand_files(tmp_path, seed=3)
    points = teststand.steady_state_points(teststand.load_stand_csv(loaded_csv))
    no_load = teststand.steady_state_points(teststand.load_stand_csv(no_load_csv))
    result = identification.fit_motor_from_stand(points, fit_resistance=True, no_load=no_load, esc=ESC)
    assert result.motor.kv_rpm_per_v == pytest.approx(MOTOR.kv_rpm_per_v, rel=0.01)
    assert result.motor.r_ll_ohm == pytest.approx(MOTOR.r_ll_ohm, rel=0.03)


def test_fit_refuses_unidentifiable_problem(tmp_path) -> None:
    loaded_csv, _ = _stand_files(tmp_path)
    points = teststand.steady_state_points(teststand.load_stand_csv(loaded_csv))
    partial = points.select(points["throttle"] < 0.95)
    with pytest.raises(ValueError, match="not identifiable"):
        identification.fit_motor_from_stand(partial, fit_resistance=True, esc=ESC)
    with pytest.raises(ValueError):
        identification.fit_motor_from_stand(points, esc=ESC)


def test_linear_no_load_fits_exact() -> None:
    motor = MOTOR.with_updates(beta_magnet_per_k=0.0)
    volts = np.array([8.0, 12.0, 16.0, 20.0, 24.0])
    omegas = np.array([motor.no_load_speed(v) for v in volts])
    currents = np.array([motor.no_load_current(w) for w in omegas])
    rpm = omegas * 60.0 / units.TWO_PI
    kv = identification.fit_kv_no_load(volts, currents, rpm, motor.r_ll_ohm)
    assert kv["kv_rpm_per_v"] == pytest.approx(motor.kv_rpm_per_v, rel=1e-9)
    i0 = identification.fit_no_load_current(rpm, currents)
    assert i0["i0_a"] == pytest.approx(motor.i0_a) and i0["i0_a_per_krpm"] == pytest.approx(motor.i0_a_per_krpm)


def test_resistance_reference_correction() -> None:
    assert identification.resistance_to_reference(0.065 * (1 + 0.00393 * 20.0), 45.0) == pytest.approx(0.065)


def test_first_order_thermal_fit() -> None:
    t = np.linspace(0.0, 200.0, 401)
    temp = 25.0 + 40.0 * (1.0 - np.exp(-t / 35.0)) + np.random.default_rng(0).normal(0, 0.1, t.size)
    fit = identification.fit_first_order_thermal(t, temp, power_loss_w=20.0)
    assert fit["tau_s"] == pytest.approx(35.0, rel=0.02)
    assert fit["r_th_k_per_w"] == pytest.approx(2.0, rel=0.02)
    assert fit["c_th_j_per_k"] == pytest.approx(17.5, rel=0.03)
