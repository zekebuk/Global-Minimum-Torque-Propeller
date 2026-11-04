"""Powertrain operating-point and electro-thermal checks against analytic solutions."""

from __future__ import annotations

import math

import pytest

from bldc_motor import synthetic, thermal, units
from bldc_motor.library import load_library
from bldc_motor.motor import MotorParams
from bldc_motor.powertrain import ESC, Battery, Powertrain
from bldc_motor.propeller import PropellerModel

LIB = load_library()


@pytest.fixture
def five_inch() -> Powertrain:
    return Powertrain(
        LIB.motor("generic_2207_1950kv"),
        LIB.propeller("generic_5.1x4.6_tri"),
        LIB.battery("lipo_6s_1300"),
        LIB.esc("generic_esc"),
    )


def test_quadratic_closed_form() -> None:
    """With constant I0, ideal ESC and stiff supply the balance is a quadratic in omega."""

    motor = MotorParams("m", 1950.0, 0.065, 0.9)
    prop = PropellerModel("p", 0.13, (0.155,), (0.105,))
    supply = synthetic.bench_supply(24.0, r_ohm=1e-12)
    pt = Powertrain(motor, prop, supply, ESC(r_series_ohm=0.0, i_quiescent_a=0.0, r_leads_ohm=0.0), n_motors=1)
    duty = 0.6
    point = pt.solve_at_throttle(duty)
    ke, r = motor.ke_ref, motor.r_ll_ohm
    k = prop.cq() * units.SEA_LEVEL_AIR_DENSITY_KG_M3 * prop.diameter_m**5 / units.TWO_PI**2  # Q = k*omega^2
    a, b, c = k, ke**2 / r, -ke * (duty * 24.0 / r - motor.i0_a)
    omega = (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)
    assert point.motor.omega_rad_s == pytest.approx(omega, rel=1e-9)


def test_operating_point_consistency(five_inch: Powertrain) -> None:
    point = five_inch.solve_at_throttle(0.7, soc=0.8)
    omega = point.motor.omega_rad_s
    # Torque balance at the shaft
    assert point.motor.torque_nm == pytest.approx(five_inch.propeller.torque(omega), rel=1e-8)
    # Pack sag: V_bus = V_oc - R * I_total
    r_src = five_inch.battery.resistance_ohm + five_inch.esc.r_leads_ohm
    assert point.v_bus == pytest.approx(point.v_oc - r_src * point.i_bus_total_a, rel=1e-8)
    # ESC energy balance: input = motor input + conduction loss + quiescent draw
    esc_loss = point.motor.current_a**2 * five_inch.esc.r_series_ohm + point.v_bus * five_inch.esc.i_quiescent_a
    assert point.p_bus_per_motor_w == pytest.approx(point.motor.p_elec_w + esc_loss, rel=1e-8)


def test_throttle_monotonic(five_inch: Powertrain) -> None:
    sweep = five_inch.throttle_sweep([0.1, 0.3, 0.5, 0.7, 0.9, 1.0])
    for low, high in zip(sweep, sweep[1:], strict=False):
        assert high.rpm > low.rpm
        assert high.thrust_n > low.thrust_n
        assert high.v_bus < low.v_bus


def test_solve_for_thrust(five_inch: Powertrain) -> None:
    target = units.grams_force_to_n(175.0)
    point = five_inch.solve_for_thrust(target, soc=0.6)
    assert point is not None and point.thrust_n == pytest.approx(target, rel=1e-5)
    assert five_inch.solve_for_thrust(units.grams_force_to_n(5000.0)) is None


def test_zero_throttle_is_stalled(five_inch: Powertrain) -> None:
    point = five_inch.solve_at_throttle(0.0)
    assert point.stalled and point.rpm == 0.0 and point.thrust_n == 0.0


def test_unloaded_runs_at_no_load_speed() -> None:
    motor = LIB.motor("generic_2207_1950kv")
    esc = ESC(r_series_ohm=0.0, i_quiescent_a=0.0, r_leads_ohm=0.0)
    pt = Powertrain(motor, synthetic.unloaded(), synthetic.bench_supply(16.0, 1e-12), esc, n_motors=1)
    point = pt.solve_at_throttle(1.0)
    assert point.motor.omega_rad_s == pytest.approx(motor.no_load_speed(16.0), rel=1e-6)
    assert point.motor.current_a == pytest.approx(motor.no_load_current(point.motor.omega_rad_s), rel=1e-6)


def test_battery_ocv_and_resistance() -> None:
    pack = Battery("b", cells_series=6, capacity_mah=1300, r_cell_ohm=0.004, cells_parallel=2)
    assert pack.ocv(1.0) == pytest.approx(25.2)
    assert pack.ocv(0.0) == pytest.approx(6 * 3.27)
    assert pack.resistance_ohm == pytest.approx(0.012)


def test_steady_state_matches_network_equations(five_inch: Powertrain) -> None:
    ss = thermal.steady_state(five_inch, 0.4)
    assert ss.converged
    th = five_inch.motor.thermal
    m = ss.point.motor
    g_sa = th.g_stator_ambient(m.omega_rad_s)
    assert ss.stator_temp_c == pytest.approx(25.0 + m.p_loss_w / g_sa, abs=0.05)
    assert ss.winding_temp_c == pytest.approx(ss.stator_temp_c + m.p_copper_w / th.g_winding_stator_w_per_k, abs=0.05)


def test_transient_converges_to_steady_state(five_inch: Powertrain) -> None:
    ss = thermal.steady_state(five_inch, 0.4)
    sim = thermal.simulate(five_inch, thermal.ThrottleProfile((0.0,), (0.4,)), 400.0, 0.2, track_soc=False)
    assert sim["winding_temp_c"][-1] == pytest.approx(ss.winding_temp_c, abs=0.1)
    assert sim["stator_temp_c"][-1] == pytest.approx(ss.stator_temp_c, abs=0.1)


def test_transient_soc_matches_coulomb_count(five_inch: Powertrain) -> None:
    sim = thermal.simulate(five_inch, thermal.ThrottleProfile((0.0,), (0.5,)), 20.0, 0.1)
    charge_as = sum(
        0.5 * (a + b) * 0.1
        for a, b in zip(sim["i_bus_per_motor_a"][:-1] * 4, sim["i_bus_per_motor_a"][1:] * 4, strict=False)
    )
    expected = 1.0 - charge_as / (1.3 * 3600.0)
    assert sim["soc"][-1] == pytest.approx(expected, abs=2e-3)


def test_throttle_profile_parse() -> None:
    profile = thermal.ThrottleProfile.parse("0:0.3, 10:1.0, 15:0.2")
    assert profile(5.0) == 0.3 and profile(10.0) == 1.0 and profile(99.0) == 0.2
    ramp = thermal.ThrottleProfile.parse("0:0,10:1", step=False)
    assert ramp(5.0) == pytest.approx(0.5)
