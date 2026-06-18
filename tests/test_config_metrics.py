"""Sanity checks for locked design constants and integrated metrics."""

from __future__ import annotations

import math

import pytest

from prandtl_prop import config, metrics


def test_diameter_and_radius_conversion() -> None:
    """The 22 inch propeller diameter must convert correctly to SI units."""

    assert config.DESIGN_DIAMETER_M == pytest.approx(0.5588)
    assert config.DESIGN_RADIUS_M == pytest.approx(0.2794)
    assert config.DISK_AREA_M2 == pytest.approx(math.pi * 0.2794**2)


def test_rpm_to_omega_conversion() -> None:
    """The locked 4000 rpm operating point should have the expected omega."""

    expected = 4000.0 * 2.0 * math.pi / 60.0
    assert config.DESIGN_OMEGA_RAD_S == pytest.approx(expected)
    assert metrics.rpm_to_omega_rad_s(4000.0) == pytest.approx(expected)


def test_locked_torque_reduction() -> None:
    """The GMT torque reduction should remain the locked modeled value."""

    reduction = metrics.torque_reduction_pct(config.LOCKED_MIL_TORQUE_NM, config.LOCKED_GMT_TORQUE_NM)
    assert reduction == pytest.approx(1.49694105766, abs=1.0e-6)
    assert round(reduction, 2) == 1.50


def test_locked_summary_matches_constants() -> None:
    """Mirrored processed data must match the locked constants."""

    metrics.assert_locked_summary_matches_constants()
