"""Geometry checks for the locked MIL/GMT station tables."""

from __future__ import annotations

import math

import pytest

from prandtl_prop import geometry


def test_locked_geometry_pair_is_consistent() -> None:
    """MIL and GMT should share stations, chord, and airfoil schedule."""

    mil_rows = geometry.load_case_geometry("MIL")
    gmt_rows = geometry.load_case_geometry("GMT")
    geometry.validate_geometry_pair(mil_rows, gmt_rows)
    assert len(mil_rows) == len(gmt_rows) >= 30


def test_radial_stations_are_monotonic() -> None:
    """Nondimensional station locations should increase from root toward tip."""

    stations = geometry.radial_stations(geometry.load_case_geometry("MIL"))
    assert all(b > a for a, b in zip(stations, stations[1:]))
    assert stations[0] == pytest.approx(0.2)
    assert stations[-1] <= 1.0


def test_chord_positive_and_twist_finite() -> None:
    """Chord and twist values should remain physically usable."""

    for case in ("MIL", "GMT"):
        rows = geometry.load_case_geometry(case)
        assert min(geometry.chord_values_m(rows)) > 0.0
        assert all(math.isfinite(value) for value in geometry.twist_values_deg(rows))
