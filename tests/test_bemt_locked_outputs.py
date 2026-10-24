"""BEMT station-data sanity checks for the locked final comparison."""

from __future__ import annotations

import pytest

from prandtl_prop import config, locked_stations, metrics


def test_locked_bemt_station_data_are_positive() -> None:
    """The locked final case should not contain undocumented negative thrust stations."""

    for case in ("MIL", "GMT"):
        rows = locked_stations.load_station_data(case)
        locked_stations.assert_station_data_sane(rows)
        assert locked_stations.total_station_load(rows, "dT_N") > 0.0
        assert locked_stations.total_station_load(rows, "dQ_Nm") > 0.0


def test_locked_cases_are_thrust_matched() -> None:
    """The integrated MIL and GMT thrust values should match the 35 N target."""

    cases = {case.case_label: case for case in metrics.locked_performance_cases()}
    assert cases["MIL"].thrust_n == pytest.approx(config.TARGET_THRUST_N, abs=2.0e-5)
    assert cases["GMT"].thrust_n == pytest.approx(config.TARGET_THRUST_N, abs=2.0e-5)


def test_gmt_has_lower_locked_torque() -> None:
    """The locked GMT modeled torque should be lower than MIL torque."""

    cases = {case.case_label: case for case in metrics.locked_performance_cases()}
    assert cases["GMT"].torque_nm < cases["MIL"].torque_nm
