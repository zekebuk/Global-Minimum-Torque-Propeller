"""Mission envelope: windmill-safe rpm trim and the effective-polar diagnostic."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from prandtl_prop import mission
from prandtl_prop.aero.bemt import trim_rpm_to_thrust
from prandtl_prop.design import load_design, read_override_file


@pytest.fixture(scope="module")
def context():
    design = load_design("designs/gmt_pa6_cf30_folding.yaml", read_override_file("designs/smoke_overrides.txt"))
    aero_run = SimpleNamespace(metrics={"collective_offset_deg": -1.3})
    return design, mission.bemt_context(design, aero_run)


def _tq(context, polar, rpm, v):
    design, (stations, _, corrections, collective, op) = context
    return mission._bemt_tq(stations, op, polar, rpm, v, collective, design.aero.tip_loss, corrections)


def test_rpm_trim_hits_thrust_in_climb(context) -> None:
    design, (stations, polar, corrections, collective, op) = context
    result = trim_rpm_to_thrust(
        stations,
        replace(op, v_inf_m_s=15.0),
        polar,
        35.0,
        collective_offset_deg=collective,
        tip_loss=design.aero.tip_loss,
        corrections=corrections,
    )
    assert result.summary["thrust_N"] == pytest.approx(35.0, rel=1e-4)
    assert result.summary["rpm"] > op.rpm  # climbing at fixed pitch needs more rpm than hover


def test_effective_polar_recovers_a_known_polar(context) -> None:
    _, (_, polar, *_rest) = context
    truth = replace(polar, cl_alpha_per_rad=4.2, alpha0_deg=-5.0, cd0=0.016, k=0.02)
    solves = []
    for v, rpm in ((0.0, 4000.0), (5.0, 4400.0), (10.0, 5000.0), (15.0, 5600.0), (20.0, 6300.0)):
        thrust, torque = _tq(context, truth, rpm, v)
        solves.append({"v_inf_m_s": v, "rpm": rpm, "cfd_thrust_N": thrust, "cfd_torque_Nm": torque})
    fit = mission.effective_polar(polar, solves, bemt_at_polar=lambda pol, n, v: _tq(context, pol, n, v))
    assert fit["baseline_max_thrust_error"] > 0.1  # the starting polar is genuinely wrong
    assert fit["fit_max_thrust_error"] < 2e-3
    assert fit["fit_max_torque_error"] < 2e-3
    assert fit["fit_alpha0_deg"] == pytest.approx(-5.0, abs=0.3)
    assert fit["fit_cl_alpha_per_rad"] == pytest.approx(4.2, rel=0.08)
