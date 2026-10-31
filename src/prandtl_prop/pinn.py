"""Physics-informed neural network (PINN) for the BEMT inflow field over the design space.

The network maps (mu, design vector) -> induced velocity vi [m/s]. It is trained with **no solver
data**: the loss is the squared, normalized BEMT closure residual

    R = [dT/dr|momentum(vi) - dT/dr|blade element(vi)] / [B c (Omega r)^2 rho / 2]

evaluated with the same differentiable physics (`bemt_solver.station_physics`) that the bisection
solver drives to zero. Once trained, one forward pass gives the converged inflow, thrust, and torque
for any design in the box, and the whole map is differentiable with respect to the design variables.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import torch

from prandtl_prop import bemt_solver, surrogate

VI_SCALE_M_S = 8.0


class InflowNet(torch.nn.Module):
    def __init__(self, width: int = 64, depth: int = 4) -> None:
        super().__init__()
        layers: list[torch.nn.Module] = []
        size = 2 + len(surrogate.DESIGN_NAMES)
        for _ in range(depth):
            layers += [torch.nn.Linear(size, width), torch.nn.Tanh()]
            size = width
        layers.append(torch.nn.Linear(size, 1))
        self.net = torch.nn.Sequential(*layers)

    def forward(self, mu: torch.Tensor, cl0: torch.Tensor, unit_design: torch.Tensor) -> torch.Tensor:
        """mu, cl0: (N, S), unit_design: (N, 5) in [-1, 1] -> vi (N, S), positive by construction.

        The station zero-lift Cl is an input so the smooth network can follow the step in inflow
        where the airfoil schedule switches sections (MH115 -> A3 at r/R = 0.74).
        """

        n, s = mu.shape
        features = [(2.0 * mu - 1.2)[..., None] / 0.8, ((cl0 - 0.16) / 0.05)[..., None]]
        x = torch.cat([*features, unit_design[:, None, :].expand(n, s, -1)], dim=-1)
        return VI_SCALE_M_S * torch.nn.functional.softplus(self.net(x)[..., 0])


@dataclass
class InflowPINN:
    rotor: bemt_solver.RotorStations
    polar: bemt_solver.PolarModel
    base_twist_deg: np.ndarray
    steps: int = 6000
    batch_designs: int = 128
    learning_rate: float = 2.0e-3
    seed: int = 0
    history: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        torch.manual_seed(self.seed)
        self.net = InflowNet().double()
        as_tensor = lambda values: torch.tensor(np.asarray(values), dtype=torch.float64)  # noqa: E731
        self._mu = as_tensor(self.rotor.mu)
        self._r = as_tensor(self.rotor.r_m)
        self._chord = as_tensor(self.rotor.chord_m)
        self._cl0 = as_tensor(self.rotor.cl0)
        self._base_twist = as_tensor(self.base_twist_deg)
        self._modes = as_tensor(surrogate.twist_modes(self.rotor.mu))

    def _residual(self, design: torch.Tensor) -> torch.Tensor:
        omega = (design[:, 0] * 2.0 * np.pi / 60.0)[:, None]
        twist = self._base_twist + design[:, surrogate.TWIST_SLICE] @ self._modes
        chord = design[:, -1:] * self._chord
        vi = self._vi(design)
        physics = bemt_solver.station_physics(
            torch,
            vi,
            omega,
            twist,
            chord,
            self._r,
            self._mu,
            self._cl0,
            self.polar,
            self.rotor.blade_count,
            self.rotor.rho,
        )
        return bemt_solver.normalized_residual(torch, physics, omega, chord, self._r, self.rotor.blade_count)

    def _vi(self, design: torch.Tensor) -> torch.Tensor:
        n = design.shape[0]
        return self.net(self._mu.expand(n, -1), self._cl0.expand(n, -1), self._unit(design))

    @staticmethod
    def _unit(design: torch.Tensor) -> torch.Tensor:
        lower = torch.as_tensor(surrogate.DESIGN_LOWER, dtype=design.dtype)
        upper = torch.as_tensor(surrogate.DESIGN_UPPER, dtype=design.dtype)
        return 2.0 * (design - lower) / (upper - lower) - 1.0

    def train(self, log_every: int = 500) -> InflowPINN:
        optimizer = torch.optim.Adam(self.net.parameters(), lr=self.learning_rate)
        schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, self.steps, eta_min=1.0e-5)
        generator = np.random.default_rng(self.seed)
        lower, upper = surrogate.DESIGN_LOWER, surrogate.DESIGN_UPPER
        for step in range(self.steps):
            design = torch.tensor(
                generator.uniform(lower, upper, size=(self.batch_designs, lower.size)), dtype=torch.float64
            )
            optimizer.zero_grad()
            loss = torch.mean(self._residual(design) ** 2)
            loss.backward()
            optimizer.step()
            schedule.step()
            if step % log_every == 0 or step == self.steps - 1:
                self.history.append(float(loss.detach()))
        return self

    def predict_vi(self, design: np.ndarray) -> np.ndarray:
        design_t = torch.tensor(np.atleast_2d(design), dtype=torch.float64)
        with torch.no_grad():
            return self._vi(design_t).numpy()

    def residual_rms(self, design: np.ndarray) -> float:
        with torch.no_grad():
            return float(
                torch.sqrt(torch.mean(self._residual(torch.tensor(np.atleast_2d(design), dtype=torch.float64)) ** 2))
            )

    def evaluate(self, design: np.ndarray) -> dict[str, np.ndarray]:
        """Integrated performance using PINN inflow (no iteration)."""

        twist, chord, rpm = surrogate.design_geometry(design, self.rotor, self.base_twist_deg)
        return bemt_solver.evaluate_rotor(self.rotor, self.polar, twist, chord, rpm, vi=self.predict_vi(design))
