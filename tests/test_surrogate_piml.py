"""Checks for the vectorized BEMT truth model, the surrogate, and the PINN residual."""

from __future__ import annotations

import numpy as np
import pytest

from prandtl_prop import bemt_solver, config

torch = pytest.importorskip("torch")

from prandtl_prop import pinn, surrogate  # noqa: E402


@pytest.fixture(scope="module")
def model() -> tuple[bemt_solver.PolarModel, bemt_solver.RotorStations, np.ndarray]:
    polar = bemt_solver.fit_polar_from_locked_data()
    return polar, bemt_solver.load_rotor_stations(polar), bemt_solver.locked_twist_deg("MIL")


def test_truth_model_reproduces_locked_torque(model) -> None:
    """Trimmed to 35 N, the analytic-polar BEMT should match the locked torque within 1%."""

    polar, rotor, _ = model
    for case, locked in (("MIL", config.LOCKED_MIL_TORQUE_NM), ("GMT", config.LOCKED_GMT_TORQUE_NM)):
        twist = bemt_solver.locked_twist_deg(case)
        offset = bemt_solver.trim_collective_to_thrust(rotor, polar, twist, rotor.chord_m, config.DESIGN_RPM)
        result = bemt_solver.evaluate_rotor(rotor, polar, twist + offset, rotor.chord_m, np.array([config.DESIGN_RPM]))
        assert result["thrust_N"][0] == pytest.approx(config.TARGET_THRUST_N, rel=1e-6)
        assert result["torque_Nm"][0] == pytest.approx(locked, rel=0.01)


def test_solver_inflow_zeroes_torch_residual(model) -> None:
    """The bisection solution must zero the same residual the PINN is trained on."""

    polar, rotor, base = model
    design = surrogate.sample_designs(8, seed=3)
    twist, chord, rpm = surrogate.design_geometry(design, rotor, base)
    vi = bemt_solver.solve_inflow(rotor, polar, twist, chord, rpm)
    omega = torch.tensor(rpm * 2.0 * np.pi / 60.0)[:, None]
    as_t = lambda values: torch.tensor(np.asarray(values), dtype=torch.float64)  # noqa: E731
    physics = bemt_solver.station_physics(
        torch, as_t(vi), omega, as_t(twist), as_t(chord), as_t(rotor.r_m), as_t(rotor.mu), as_t(rotor.cl0), polar
    )
    residual = bemt_solver.normalized_residual(torch, physics, omega, as_t(chord), as_t(rotor.r_m), rotor.blade_count)
    assert float(residual.abs().max()) < 1e-8


def test_power_balance_matches_momentum_theory(model) -> None:
    """Figure of merit must sit below 1 (ideal actuator disk) for every sampled design."""

    polar, rotor, base = model
    result = surrogate.evaluate_designs(surrogate.sample_designs(64, seed=4), rotor, polar, base)
    assert np.all(result["figure_of_merit"] < 1.0)
    assert np.all(result["figure_of_merit"] > 0.4)


def test_physics_surrogate_learns_thrust_and_torque(model) -> None:
    polar, rotor, base = model
    train = surrogate.sample_designs(150, seed=5)
    test = surrogate.sample_designs(100, seed=6)
    train_y = surrogate.target_matrix(surrogate.evaluate_designs(train, rotor, polar, base))
    test_y = surrogate.target_matrix(surrogate.evaluate_designs(test, rotor, polar, base))
    fitted = surrogate.SurrogateEnsemble(physics=True, members=1, epochs=800).fit(train, train_y)
    predicted, _ = fitted.predict(test)
    assert surrogate.mean_abs_pct_error(predicted[:, 0], test_y[:, 0]) < 2.0
    assert surrogate.mean_abs_pct_error(predicted[:, 1], test_y[:, 1]) < 2.0


def test_pinn_reduces_residual_without_solver_data(model) -> None:
    polar, rotor, base = model
    designs = surrogate.sample_designs(16, seed=7)
    untrained = pinn.InflowPINN(rotor, polar, base, steps=1)
    start = untrained.residual_rms(designs)
    trained = pinn.InflowPINN(rotor, polar, base, steps=300).train()
    assert trained.residual_rms(designs) < 0.5 * start
