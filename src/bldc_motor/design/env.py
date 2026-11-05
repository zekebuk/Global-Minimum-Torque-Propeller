"""Gymnasium environment: an agent iteratively refines a motor design for a mission.

Formulation (sequential design refinement, like an engineer iterating on a drawing):

* state   - the design (unit-cube encoding, 17 dims), normalised performance feedback for the
            current design (score, g/W, T/W, burst temperatures, slot fill, DFM score, cost,
            feasibility), the mission context and the remaining step budget;
* action  - Box(-1, 1, 17): a bounded edit ``u <- clip(u + step_scale * a, 0, 1)``. Discrete
            choices (slot/pole, turns, wire gauge, ...) change when their bin boundary is crossed;
* reward  - potential-based: ``r_t = phi(J(u_{t+1})) - phi(J(u_t))``. The episode return telescopes to
            ``phi(J_final) - phi(J_initial)``, so maximising return is exactly maximising the final
            design's objective (Ng et al., 1999), with dense per-step credit. ``phi`` is the identity
            for J >= 0 and ``-log(1 - J)`` below: monotone (same optimum), but it compresses the
            large constraint penalties of badly infeasible designs that would otherwise dominate
            the reward variance.

``action_mode="absolute"`` gives the one-shot (contextual-bandit) variant: ``u = (a+1)/2`` with a
single step. With ``mission_sampler`` the mission changes per episode, so a policy learns a design
*rule* across requirements instead of a single point design. Optimising one fixed mission
like that is something black-box optimisers do well; compare against ``baselines``.

Every evaluated design is offered to ``self.archive`` so the best designs found during
training are never lost.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from bldc_motor.design import geometry, model
from bldc_motor.design import mission as mission_mod

METRIC_FEATURES = (
    ("score", 0.0, 1.0),
    ("hover_g_per_w", 0.0, 5.0),
    ("thrust_to_weight", 0.0, 10.0),
    ("burst_winding_c", 100.0, 50.0),
    ("burst_magnet_c", 80.0, 40.0),
    ("fill_bare", 0.4, 0.2),
    ("manufacturability", 0.5, 0.5),
    ("unit_cost_usd", 5.0, 3.0),
    ("feasible", 0.5, 0.5),
)


@dataclass
class DesignArchive:
    """Keeps the top-k distinct designs seen by an environment or optimiser.

    Ranking: feasible designs first, then by score. ``history`` records the best *feasible*
    score after each evaluation (NaN until the first feasible design), which is the fair
    yardstick for comparing optimisers per evaluation budget.
    """

    capacity: int = 20
    entries: list = field(default_factory=list)  # (score, feasible, design)
    evaluations: int = 0
    history: list = field(default_factory=list)
    _best_feasible: float = -math.inf

    def offer(self, ev: mission_mod.Evaluation) -> None:
        self.evaluations += 1
        if ev.feasible:
            self._best_feasible = max(self._best_feasible, ev.score)
        key = _design_key(ev.design)
        if all(_design_key(d) != key for _, _, d in self.entries):
            self.entries.append((ev.score, ev.feasible, dict(ev.design)))
            self.entries.sort(key=lambda e: (e[1], e[0]), reverse=True)
            del self.entries[self.capacity :]
        self.history.append(self._best_feasible if math.isfinite(self._best_feasible) else math.nan)

    @property
    def best(self):
        return self.entries[0] if self.entries else None


def shaped_potential(score: float) -> float:
    """Monotone squashing of the objective for reward shaping (identity for score >= 0)."""

    return score if score >= 0.0 else -math.log1p(-score)


def _design_key(design: dict) -> tuple:
    snapped = geometry.snap(design)
    return tuple(
        snapped[p] if not isinstance(snapped[p], float) else round(snapped[p], 4) for p in geometry.PARAMETER_NAMES
    )


class MotorDesignEnv(gym.Env):
    metadata = {"render_modes": ["ansi"]}

    def __init__(
        self,
        mission: mission_mod.MissionSpec | None = None,
        mission_sampler: Callable[[np.random.Generator], mission_mod.MissionSpec] | None = None,
        max_steps: int = 24,
        step_scale: float = 0.08,
        action_mode: str = "delta",
        start: str = "random_valid",
        calibration: model.Calibration | None = None,
        snap_designs: bool = True,
        archive: DesignArchive | None = None,
        render_mode: str | None = None,
    ) -> None:
        super().__init__()
        if mission is None and mission_sampler is None:
            mission = mission_mod.preset_missions()["5in_freestyle_6s"]
        if action_mode not in ("delta", "absolute"):
            raise ValueError("action_mode must be 'delta' or 'absolute'")
        self.fixed_mission = mission
        self.mission_sampler = mission_sampler
        self.max_steps = 1 if action_mode == "absolute" else max_steps
        self.step_scale = step_scale
        self.action_mode = action_mode
        self.start = start
        self.calibration = calibration
        self.snap_designs = snap_designs
        self.archive = archive if archive is not None else DesignArchive()
        self.render_mode = render_mode
        dim = geometry.DIMENSION
        obs_dim = dim + len(METRIC_FEATURES) + 6 + 1
        self.action_space = spaces.Box(-1.0, 1.0, shape=(dim,), dtype=np.float32)
        self.observation_space = spaces.Box(-10.0, 10.0, shape=(obs_dim,), dtype=np.float32)
        self.mission = mission
        self.u = np.full(dim, 0.5)
        self.current: mission_mod.Evaluation | None = None
        self.steps = 0

    # ---- helpers -----------------------------------------------------------------------
    def _evaluate(self, u: np.ndarray) -> mission_mod.Evaluation:
        design = geometry.decode(u)
        if self.snap_designs:
            design = geometry.snap(design)
        ev = mission_mod.evaluate(design, self.mission, self.calibration)
        self.archive.offer(ev)
        return ev

    def _observation(self) -> np.ndarray:
        ev = self.current
        feats = []
        for key, center, scale in METRIC_FEATURES:
            if key == "score":
                value = ev.score
            elif key == "feasible":
                value = float(ev.feasible)
            elif key == "fill_bare":
                value = ev.analysis.internals.get("fill_bare", 1.0) if ev.analysis else 1.0
            else:
                value = ev.metrics.get(key, math.nan)
            value = float(value)
            feats.append(0.0 if not math.isfinite(value) else float(np.clip((value - center) / scale, -5.0, 5.0)))
        progress = [self.steps / max(self.max_steps, 1)]
        return np.concatenate([self.u, feats, self.mission.context_vector(), progress]).astype(np.float32)

    def _initial_u(self) -> np.ndarray:
        if self.start == "reference" or self.action_mode == "absolute":
            return geometry.encode(geometry.reference_design())
        if self.start == "random":
            return self.np_random.random(geometry.DIMENSION)
        for _ in range(200):  # random_valid: rejection-sample a geometrically valid design
            u = self.np_random.random(geometry.DIMENSION)
            design = geometry.snap(geometry.decode(u))
            if model.analyze(design, self.calibration).valid:
                return u
        return geometry.encode(geometry.reference_design())

    # ---- gym API -----------------------------------------------------------------------
    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        if self.mission_sampler is not None:
            self.mission = self.mission_sampler(self.np_random)
        else:
            self.mission = self.fixed_mission
        options = options or {}
        if "design" in options:
            self.u = geometry.encode(options["design"])
        else:
            self.u = self._initial_u()
        self.steps = 0
        self.current = self._evaluate(self.u)
        return self._observation(), self._info()

    def step(self, action):
        action = np.clip(np.asarray(action, dtype=float), -1.0, 1.0)
        previous = shaped_potential(self.current.score)
        if self.action_mode == "absolute":
            self.u = 0.5 * (action + 1.0)
        else:
            self.u = np.clip(self.u + self.step_scale * action, 0.0, 1.0)
        self.current = self._evaluate(self.u)
        self.steps += 1
        if self.action_mode == "absolute":
            reward = shaped_potential(self.current.score)
        else:
            reward = shaped_potential(self.current.score) - previous
        terminated = False
        truncated = self.steps >= self.max_steps
        return self._observation(), float(reward), terminated, truncated, self._info()

    def _info(self) -> dict:
        ev = self.current
        return {
            "score": ev.score,
            "feasible": ev.feasible,
            "design": ev.design,
            "metrics": ev.metrics,
            "penalties": ev.penalties,
        }

    def render(self):
        ev = self.current
        m = ev.metrics
        return (
            f"step {self.steps}/{self.max_steps} score={ev.score:+.3f} feasible={ev.feasible} "
            f"g/W={m.get('hover_g_per_w', float('nan')):.2f} T/W={m.get('thrust_to_weight', float('nan')):.1f} "
            f"Tw={m.get('burst_winding_c', float('nan')):.0f}C mfg={m.get('manufacturability', 0):.2f}"
        )


def mission_sampler_from_presets(names: list[str] | None = None, jitter: float = 0.1):
    """Sample a preset mission and jitter dry mass / T/W requirement by +/- ``jitter``."""

    presets = mission_mod.preset_missions()
    keys = names or sorted(presets)

    def sampler(rng: np.random.Generator) -> mission_mod.MissionSpec:
        base = presets[keys[int(rng.integers(len(keys)))]]
        scale = 1.0 + rng.uniform(-jitter, jitter, size=2)
        return mission_mod.with_overrides(
            base, dry_mass_g=base.dry_mass_g * scale[0], min_thrust_to_weight=base.min_thrust_to_weight * scale[1]
        )

    return sampler
