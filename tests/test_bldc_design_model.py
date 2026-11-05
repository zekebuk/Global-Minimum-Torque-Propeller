"""First-principles checks of the analytical motor design model, windings, DFM and mission objective."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bldc_motor import thermal, units
from bldc_motor.design import calibration, geometry, manufacturing, materials, mission, model, winding
from bldc_motor.powertrain import Powertrain

REF = {**geometry.reference_design(), "tooth_frac": 0.36, "awg": 26}
MISSIONS = mission.preset_missions()


@pytest.mark.parametrize(
    "slots, poles, kw",
    [
        (9, 12, 0.866),
        (12, 14, 0.933),
        (12, 10, 0.933),
        (9, 8, 0.945),
        (18, 16, 0.945),
        (24, 22, 0.949),
        (24, 28, 0.933),
    ],
)
def test_winding_factors_match_literature(slots, poles, kw) -> None:
    lay = winding.layout(slots, poles)
    assert lay.balanced
    assert lay.winding_factor == pytest.approx(kw, abs=1e-3)
    assert sorted(lay.notation().upper().count(ph) for ph in "ABC") == [slots // 3] * 3


def test_winding_symmetry_flags() -> None:
    assert winding.layout(9, 8).unbalanced_magnetic_pull  # single section
    assert not winding.layout(12, 14).unbalanced_magnetic_pull  # two anti-symmetric sections
    with pytest.raises(ValueError):
        winding.layout(12, 12)


def test_encoding_round_trip() -> None:
    decoded = geometry.decode(geometry.encode(REF))
    for p in geometry.PARAMETERS:
        assert decoded[p.name] == pytest.approx(REF[p.name]) if not p.discrete else decoded[p.name] == REF[p.name]


def test_reference_design_is_valid_and_plausible() -> None:
    a = model.analyze(REF)
    assert a.valid
    assert 1000 < a.motor.kv_rpm_per_v < 3000
    assert 0.02 < a.motor.r_ll_ohm < 0.2
    assert 20.0 < a.mass_g < 50.0
    assert a.internals["carter"] >= 1.0
    parts = [k for k in a.internals if k.startswith("mass_") and k not in ("mass_total_g", "mass_stator_iron_g")]
    assert sum(a.internals[k] for k in parts) == pytest.approx(a.mass_g)


def test_back_emf_scales_with_turns_and_stack() -> None:
    """Ke = k_w N_ph B1 D L: Kv is inversely proportional to turns and to stack length."""

    base = model.analyze(REF).motor.kv_rpm_per_v
    assert model.analyze({**REF, "turns": 14}).motor.kv_rpm_per_v == pytest.approx(base / 2.0)
    assert model.analyze({**REF, "stack_mm": 14.0}).motor.kv_rpm_per_v == pytest.approx(base / 2.0)


def test_wye_delta_equivalence() -> None:
    """Delta: Kv x sqrt(3), R_ll / 3 -> identical motor constant Km."""

    wye = model.analyze({**REF, "connection": "wye"}).motor
    delta = model.analyze({**REF, "connection": "delta"}).motor
    assert delta.kv_rpm_per_v == pytest.approx(math.sqrt(3.0) * wye.kv_rpm_per_v)
    assert delta.km_nm_per_sqrt_w == pytest.approx(wye.km_nm_per_sqrt_w, rel=0.02)  # lead resistance differs slightly


def test_larger_airgap_weakens_flux() -> None:
    small = model.analyze({**REF, "airgap_mm": 0.25}).internals["bg0_t"]
    large = model.analyze({**REF, "airgap_mm": 0.6}).internals["bg0_t"]
    assert large < small


def test_classical_eddy_loss_scales_with_thickness_squared() -> None:
    thin = model.analyze({**REF, "lamination_mm": 0.10}).internals["eddy_w_per_rad_s2"]
    std = model.analyze({**REF, "lamination_mm": 0.20}).internals["eddy_w_per_rad_s2"]
    assert thin / std == pytest.approx(0.25)


def test_invalid_geometry_flagged() -> None:
    a = model.analyze({**REF, "inner_ratio": 0.55, "yoke_frac": 0.2, "tooth_frac": 0.75})
    assert not a.valid and a.violation_total > 0


def test_dfm_rules() -> None:
    ok = manufacturing.dfm_rules(model.analyze(REF))
    assert not [r for r in ok if r.status == "FAIL"]
    tight = manufacturing.dfm_rules(model.analyze({**REF, "airgap_mm": 0.15, "magnet_mm": 0.6}))
    status = {r.rule: r.status for r in tight}
    assert status["airgap"] == "FAIL" and status["magnet_thickness"] == "FAIL"
    overfilled = manufacturing.dfm_rules(model.analyze({**REF, "turns": 12, "strands": 3}))
    assert {r.rule: r.status for r in overfilled}["slot_fill"] == "FAIL"
    assert manufacturing.manufacturability_score(ok) > manufacturing.manufacturability_score(tight)


def test_cost_breakdown_sums() -> None:
    cost = manufacturing.unit_cost_usd(model.analyze(REF))
    assert cost["total"] == pytest.approx(sum(v for k, v in cost.items() if k != "total"))
    pricier = manufacturing.unit_cost_usd(model.analyze({**REF, "magnet_grade": "N42UH"}))
    assert pricier["magnets"] > cost["magnets"]


def test_fast_hover_solver_matches_iterative() -> None:
    m = model.analyze(REF).motor
    spec = MISSIONS["5in_freestyle_6s"]
    pt = Powertrain(m, spec.propeller, spec.battery, spec.esc)
    slow = pt.solve_for_thrust(2.0, 0.6, 0.0, 50.0, 40.0)
    fast = pt.solve_for_thrust_direct(2.0, 0.6, 0.0, 50.0, 40.0)
    assert fast.throttle == pytest.approx(slow.throttle, rel=1e-6)
    assert fast.i_bus_total_a == pytest.approx(slow.i_bus_total_a, rel=1e-6)
    assert pt.solve_at_speed(units.rpm_to_rad_s(1e6)) is None  # beyond the voltage limit


def test_closed_form_burst_matches_integrator() -> None:
    m = model.analyze(REF).motor.with_updates(alpha_cu_per_k=0.0, beta_magnet_per_k=0.0)  # constant losses
    spec = MISSIONS["5in_freestyle_6s"]
    pt = Powertrain(m, spec.propeller, spec.battery, spec.esc)
    point = pt.solve_at_throttle(0.8, 0.9)
    end, _ = mission._thermal_after(m.thermal, point, np.zeros(2), 10.0)
    sim = thermal.simulate(pt, thermal.ThrottleProfile((0.0,), (0.8,)), 10.0, 0.01, 0.0, 0.9, track_soc=False)
    assert sim["winding_temp_c"][-1] == pytest.approx(end[0], rel=2e-3)
    assert sim["stator_temp_c"][-1] == pytest.approx(end[1], rel=2e-3)


def test_mission_evaluation() -> None:
    spec = MISSIONS["5in_freestyle_6s"]
    ev = mission.evaluate(REF, spec)
    a = ev.analysis
    assert ev.metrics["auw_g"] == pytest.approx(
        spec.dry_mass_g + 4 * (a.mass_g + spec.propeller.mass_g) + spec.battery.mass_g
    )
    assert ev.metrics["thrust_to_weight"] > 1.0
    invalid = mission.evaluate({**REF, "inner_ratio": 0.55, "yoke_frac": 0.2, "tooth_frac": 0.75}, spec)
    assert invalid.score < ev.score and not invalid.feasible


def test_calibration_recovers_known_factors() -> None:
    truth = model.Calibration(
        kv_factor=1.08, resistance_factor=0.92, iron_loss_factor=1.6, friction_factor=2.5, convection_factor=0.8
    )
    built = model.analyze(REF, truth).motor
    omega = 2500.0
    ratios = calibration.prototype_ratios(REF, built, built.thermal.g_stator_ambient(omega), omega)
    for key in ("kv_factor", "resistance_factor", "iron_loss_factor", "friction_factor", "convection_factor"):
        assert ratios[key] == pytest.approx(getattr(truth, key), rel=1e-9)
    cal = calibration.update_calibration(model.Calibration(), ratios)
    assert cal.samples == 1
    assert model.analyze(REF, cal).motor.kv_rpm_per_v == pytest.approx(built.kv_rpm_per_v)


def test_tolerance_samples_are_bounded() -> None:
    samples = manufacturing.perturbed_designs(REF, 50, np.random.default_rng(0))
    gaps = [s[0]["airgap_mm"] for s in samples]
    assert len(samples) == 50 and abs(np.mean(gaps) - REF["airgap_mm"]) < 0.02
    assert all(g > 0 for g in gaps)


def test_magnet_grade_catalog_is_consistent() -> None:
    # Higher temperature grades trade remanence for coercivity.
    brs = [g.br_t for g in materials.MAGNET_GRADES]
    hcjs = [g.hcj_ka_m for g in materials.MAGNET_GRADES]
    assert brs == sorted(brs, reverse=True) and hcjs == sorted(hcjs)
