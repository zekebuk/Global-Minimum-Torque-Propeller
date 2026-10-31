"""Neural-network surrogate of the hover BEMT model over a low-dimensional propeller design space.

Design vector (6 variables), perturbing the locked MIL blade:

    rpm            rotor speed
    gmt_blend      fraction of the locked (GMT - MIL) twist difference added (0 = MIL, 1 = GMT)
    twist_0_deg    collective twist offset
    twist_1_deg    linear twist mode   (x = (mu - 0.6) / 0.4 in [-1, 1])
    twist_2_deg    quadratic twist mode (Legendre P2(x))
    chord_scale    uniform chord multiplier

Two surrogate variants are trained on the same BEMT samples:

    "black_box"  predicts dimensional thrust, torque, and root moment directly.
    "physics"    predicts rotor coefficients CT = T / (rho n^2 D^4), CQ = Q / (rho n^2 D^5),
                 CM = M / (rho n^2 D^5); dimensional analysis supplies the n^2 scaling exactly,
                 so the network only learns the weak residual dependence on rpm (Reynolds, tip loss).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
from scipy.stats import qmc

from prandtl_prop import bemt_solver, config

DESIGN_NAMES = ("rpm", "gmt_blend", "twist_0_deg", "twist_1_deg", "twist_2_deg", "chord_scale")
DESIGN_LOWER = np.array([3200.0, -0.5, -2.5, -2.5, -1.5, 0.85])
DESIGN_UPPER = np.array([4800.0, 1.5, 2.5, 2.5, 1.5, 1.15])
TWIST_SLICE = slice(1, 5)
TARGET_NAMES = ("thrust_N", "torque_Nm", "root_moment_Nm", "max_alpha_deg", "min_alpha_deg")
# Coefficient exponents of the rotor diameter for each target in the physics variant (None: not scaled).
DIAMETER_EXPONENT = (4, 5, 5, None, None)


def to_unit(design: np.ndarray) -> np.ndarray:
    return 2.0 * (design - DESIGN_LOWER) / (DESIGN_UPPER - DESIGN_LOWER) - 1.0


def from_unit(unit: np.ndarray) -> np.ndarray:
    return DESIGN_LOWER + 0.5 * (unit + 1.0) * (DESIGN_UPPER - DESIGN_LOWER)


def sample_designs(count: int, seed: int) -> np.ndarray:
    """Latin hypercube sample of the design box, shape (count, 6)."""

    unit = qmc.LatinHypercube(d=len(DESIGN_NAMES), seed=seed).random(count)
    return qmc.scale(unit, DESIGN_LOWER, DESIGN_UPPER)


def twist_modes(mu: np.ndarray) -> np.ndarray:
    """Twist shape functions on the locked station grid, shape (4, S): GMT - MIL, then 1, x, P2(x)."""

    gmt_delta = bemt_solver.locked_twist_deg("GMT") - bemt_solver.locked_twist_deg("MIL")
    if gmt_delta.shape != mu.shape:
        raise ValueError("twist modes are defined on the locked station grid only")
    x = (mu - 0.6) / 0.4
    return np.vstack([gmt_delta, np.ones_like(x), x, 1.5 * x**2 - 0.5])


def design_geometry(
    design: np.ndarray, rotor: bemt_solver.RotorStations, base_twist_deg: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Map design vectors (N, 6) to station twist (N, S), chord (N, S), and rpm (N,)."""

    design = np.atleast_2d(design)
    twist = base_twist_deg + design[:, TWIST_SLICE] @ twist_modes(rotor.mu)
    chord = design[:, -1:] * rotor.chord_m
    return twist, chord, design[:, 0]


def evaluate_designs(
    design: np.ndarray,
    rotor: bemt_solver.RotorStations,
    polar: bemt_solver.PolarModel,
    base_twist_deg: np.ndarray,
) -> dict[str, np.ndarray]:
    """Run the BEMT truth model on a batch of design vectors."""

    twist, chord, rpm = design_geometry(design, rotor, base_twist_deg)
    return bemt_solver.evaluate_rotor(rotor, polar, twist, chord, rpm)


def target_matrix(results: dict[str, np.ndarray]) -> np.ndarray:
    return np.column_stack([results[name] for name in TARGET_NAMES])


def _coefficient_scale(rpm: np.ndarray | torch.Tensor) -> list:
    """rho n^2 D^k for each target (1 for unscaled targets)."""

    n_sq = (rpm / 60.0) ** 2
    diameter = 2.0 * config.DESIGN_RADIUS_M
    return [1.0 + 0.0 * n_sq if k is None else config.AIR_DENSITY_KG_M3 * n_sq * diameter**k for k in DIAMETER_EXPONENT]


class _MLP(torch.nn.Module):
    def __init__(self, n_in: int, n_out: int, width: int = 64, depth: int = 3) -> None:
        super().__init__()
        layers: list[torch.nn.Module] = []
        size = n_in
        for _ in range(depth):
            layers += [torch.nn.Linear(size, width), torch.nn.SiLU()]
            size = width
        layers.append(torch.nn.Linear(size, n_out))
        self.net = torch.nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


@dataclass
class SurrogateEnsemble:
    """Ensemble of small MLPs; the member spread is a cheap uncertainty estimate."""

    physics: bool
    members: int = 3
    epochs: int = 3000
    learning_rate: float = 3.0e-3
    seed: int = 0

    def _encode(self, design: np.ndarray, targets: np.ndarray) -> np.ndarray:
        if not self.physics:
            return targets
        scale = _coefficient_scale(design[:, 0])
        return np.column_stack([targets[:, j] / scale[j] for j in range(targets.shape[1])])

    def fit(self, design: np.ndarray, targets: np.ndarray) -> SurrogateEnsemble:
        encoded = self._encode(design, targets)
        self.y_mean = encoded.mean(axis=0)
        self.y_std = encoded.std(axis=0) + 1.0e-12
        x = torch.tensor(to_unit(design), dtype=torch.float64)
        y = torch.tensor((encoded - self.y_mean) / self.y_std, dtype=torch.float64)
        self.models = []
        for member in range(self.members):
            torch.manual_seed(self.seed * 100 + member)
            model = _MLP(x.shape[1], y.shape[1]).double()
            optimizer = torch.optim.Adam(model.parameters(), lr=self.learning_rate)
            schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, self.epochs)
            for _ in range(self.epochs):
                optimizer.zero_grad()
                loss = torch.mean((model(x) - y) ** 2)
                loss.backward()
                optimizer.step()
                schedule.step()
            model.eval()
            self.models.append(model)
        return self

    def predict_torch(self, design: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """Differentiable mean and ensemble std of the dimensional targets."""

        lower = torch.as_tensor(DESIGN_LOWER, dtype=design.dtype)
        upper = torch.as_tensor(DESIGN_UPPER, dtype=design.dtype)
        unit = 2.0 * (design - lower) / (upper - lower) - 1.0
        y_mean = torch.as_tensor(self.y_mean, dtype=design.dtype)
        y_std = torch.as_tensor(self.y_std, dtype=design.dtype)
        stacked = torch.stack([model(unit) * y_std + y_mean for model in self.models])
        if self.physics:
            scale = _coefficient_scale(design[:, 0])
            stacked = torch.stack([stacked[..., j] * scale[j] for j in range(stacked.shape[-1])], dim=-1)
        return stacked.mean(dim=0), stacked.std(dim=0, correction=0)

    def predict(self, design: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        with torch.no_grad():
            mean, std = self.predict_torch(torch.tensor(np.atleast_2d(design), dtype=torch.float64))
        return mean.numpy(), std.numpy()


def optimize_min_torque(
    surrogate: SurrogateEnsemble,
    rpm: float = config.DESIGN_RPM,
    chord_scale: float = 1.0,
    target_thrust_n: float = config.TARGET_THRUST_N,
    alpha_bounds_deg: tuple[float, float] = (0.0, 9.0),
    max_root_moment_nm: float | None = None,
    starts: int = 12,
    seed: int = 0,
) -> np.ndarray | None:
    """Minimize surrogate torque over the twist modes at fixed rpm and chord (SLSQP, autograd gradients).

    Constraints: thrust = target, alpha inside the polar-fit range, optional root bending moment cap.
    Returns the full design vector, or None when no start converges to a feasible point.
    """

    from scipy.optimize import minimize

    def full(twist_modes_deg: np.ndarray) -> tuple[torch.Tensor, torch.Tensor]:
        values = torch.tensor(twist_modes_deg, dtype=torch.float64, requires_grad=True)
        design = torch.cat(
            [torch.tensor([rpm], dtype=torch.float64), values, torch.tensor([chord_scale], dtype=torch.float64)]
        )
        return values, surrogate.predict_torch(design[None, :])[0][0]

    def scalar(index: int, sign: float = 1.0, offset: float = 0.0):
        def value(z: np.ndarray) -> float:
            _, out = full(z)
            return float(sign * (out[index].detach() - offset))

        def grad(z: np.ndarray) -> np.ndarray:
            values, out = full(z)
            (g,) = torch.autograd.grad(sign * out[index], values)
            return g.numpy()

        return value, grad

    torque_f, torque_g = scalar(1)
    thrust_f, thrust_g = scalar(0, 1.0, target_thrust_n)
    alpha_hi_f, alpha_hi_g = scalar(3, -1.0, alpha_bounds_deg[1])
    alpha_lo_f, alpha_lo_g = scalar(4, 1.0, alpha_bounds_deg[0])
    constraints = [
        {"type": "eq", "fun": thrust_f, "jac": thrust_g},
        {"type": "ineq", "fun": alpha_hi_f, "jac": alpha_hi_g},
        {"type": "ineq", "fun": alpha_lo_f, "jac": alpha_lo_g},
    ]
    if max_root_moment_nm is not None:
        moment_f, moment_g = scalar(2, -1.0, max_root_moment_nm)
        constraints.append({"type": "ineq", "fun": moment_f, "jac": moment_g})

    bounds = list(zip(DESIGN_LOWER[TWIST_SLICE], DESIGN_UPPER[TWIST_SLICE], strict=False))
    rng = np.random.default_rng(seed)
    best: tuple[float, np.ndarray] | None = None
    for _ in range(starts):
        start = rng.uniform(DESIGN_LOWER[TWIST_SLICE], DESIGN_UPPER[TWIST_SLICE])
        result = minimize(
            torque_f,
            start,
            jac=torque_g,
            bounds=bounds,
            constraints=constraints,
            method="SLSQP",
            options={"maxiter": 200, "ftol": 1e-12},
        )
        feasible = (
            result.success
            and abs(thrust_f(result.x)) < 1e-3
            and all(c["fun"](result.x) >= -1e-4 for c in constraints[1:])
        )
        if feasible and (best is None or result.fun < best[0]):
            best = (float(result.fun), result.x)
    if best is None:
        return None
    return np.concatenate([[rpm], best[1], [chord_scale]])


def mean_abs_pct_error(predicted: np.ndarray, actual: np.ndarray) -> float:
    return float(np.mean(np.abs(predicted - actual) / np.abs(actual)) * 100.0)
