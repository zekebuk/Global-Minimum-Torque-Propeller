"""First-principles checks of the DC-equivalent motor and propeller models."""

from __future__ import annotations

import math

import pytest

from bldc_motor import units
from bldc_motor.motor import MotorParams
from bldc_motor.propeller import PropellerModel
from prandtl_prop import config

MOTOR = MotorParams(name="test", kv_rpm_per_v=1950.0, r_ll_ohm=0.065, i0_a=0.9, i0_a_per_krpm=0.03)


def test_kv_ke_conversion() -> None:
    # 1000 rpm/V -> 1000*2pi/60 rad/s per V -> Ke = 60/(2pi*1000) V*s/rad
    assert units.kv_to_ke(1000.0) == pytest.approx(0.0095492966)
    assert units.ke_to_kv(units.kv_to_ke(1234.5)) == pytest.approx(1234.5)


@pytest.mark.parametrize("voltage, rpm", [(25.0, 30000.0), (12.0, 5000.0), (16.8, 28000.0), (5.0, 9000.0)])
def test_power_balance_closes(voltage: float, rpm: float) -> None:
    point = MOTOR.evaluate(voltage, units.rpm_to_rad_s(rpm), winding_temp_c=80.0, magnet_temp_c=60.0)
    assert point.p_elec_w == pytest.approx(point.p_copper_w + point.p_no_load_w + point.p_shaft_w, rel=1e-12, abs=1e-12)


def test_no_load_speed_has_zero_torque() -> None:
    omega = MOTOR.no_load_speed(24.0)
    assert MOTOR.evaluate(24.0, omega).torque_nm == pytest.approx(0.0, abs=1e-12)
    # Ideal limit: no-load speed approaches Kv*V as I0 -> 0.
    ideal = MOTOR.with_updates(i0_a=0.0, i0_a_per_krpm=0.0)
    assert units.rad_s_to_rpm(ideal.no_load_speed(24.0)) == pytest.approx(1950.0 * 24.0)


def test_evaluate_at_torque_is_inverse() -> None:
    omega = units.rpm_to_rad_s(25000.0)
    forward = MOTOR.evaluate(22.0, omega, 70.0, 50.0)
    inverse = MOTOR.evaluate_at_torque(forward.torque_nm, omega, 70.0, 50.0)
    assert inverse.voltage_v == pytest.approx(22.0)
    assert inverse.current_a == pytest.approx(forward.current_a)


def test_temperature_dependence() -> None:
    assert MOTOR.resistance_at(25.0 + 100.0) == pytest.approx(0.065 * (1 + 0.393))
    # Hot magnets -> weaker flux -> lower Ke (higher apparent Kv).
    assert MOTOR.ke_at(100.0) < MOTOR.ke_ref


def test_rewind_preserves_motor_constant() -> None:
    rewound = MOTOR.rewound(2550.0)
    ratio = 2550.0 / 1950.0
    assert rewound.r_ll_ohm == pytest.approx(MOTOR.r_ll_ohm / ratio**2)
    assert rewound.km_nm_per_sqrt_w == pytest.approx(MOTOR.km_nm_per_sqrt_w)
    # Same rpm -> same iron/friction loss after a rewind.
    omega = units.rpm_to_rad_s(20000.0)
    assert rewound.ke_ref * omega * rewound.no_load_current(omega) == pytest.approx(
        MOTOR.ke_ref * omega * MOTOR.no_load_current(omega)
    )


def test_invalid_parameters_rejected() -> None:
    with pytest.raises(ValueError):
        MotorParams(name="bad", kv_rpm_per_v=0.0, r_ll_ohm=0.1, i0_a=0.5)
    with pytest.raises(ValueError):
        MotorParams(name="bad", kv_rpm_per_v=1000.0, r_ll_ohm=-0.1, i0_a=0.5)


def test_propeller_coefficients_reproduce_thesis_locked_point() -> None:
    """Coefficients from the locked MIL point must reproduce it (ties the toolkit to the thesis data)."""

    prop = PropellerModel.from_static_point(
        "thesis_mil",
        config.DESIGN_DIAMETER_M,
        config.DESIGN_RPM,
        config.LOCKED_MIL_THRUST_N,
        config.LOCKED_MIL_TORQUE_NM,
        rho=config.AIR_DENSITY_KG_M3,
    )
    omega = units.rpm_to_rad_s(config.DESIGN_RPM)
    assert prop.thrust(omega, rho=config.AIR_DENSITY_KG_M3) == pytest.approx(config.LOCKED_MIL_THRUST_N)
    assert prop.torque(omega, rho=config.AIR_DENSITY_KG_M3) == pytest.approx(config.LOCKED_MIL_TORQUE_NM)
    assert prop.power(omega, rho=config.AIR_DENSITY_KG_M3) == pytest.approx(
        config.LOCKED_MIL_TORQUE_NM * config.DESIGN_OMEGA_RAD_S
    )


def test_figure_of_merit_matches_momentum_theory() -> None:
    prop = PropellerModel("p", 0.13, (0.15,), (0.1,))
    omega = units.rpm_to_rad_s(25000.0)
    thrust, power = prop.thrust(omega), prop.power(omega)
    fm_direct = thrust**1.5 / (power * math.sqrt(2.0 * units.SEA_LEVEL_AIR_DENSITY_KG_M3 * prop.disk_area_m2))
    assert prop.figure_of_merit() == pytest.approx(fm_direct)


def test_static_prop_fit_recovers_coefficients() -> None:
    prop = PropellerModel("p", 0.13, (0.155,), (0.105,))
    rpms = [8000.0, 15000.0, 22000.0, 30000.0]
    thrust = [prop.thrust(units.rpm_to_rad_s(r)) for r in rpms]
    torque = [prop.torque(units.rpm_to_rad_s(r)) for r in rpms]
    fitted, diag = PropellerModel.fit_static("fit", 0.13, rpms, thrust, torque)
    assert fitted.ct() == pytest.approx(0.155)
    assert fitted.cp() == pytest.approx(0.105)
    assert diag["thrust_r2"] == pytest.approx(1.0)
