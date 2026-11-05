"""Amortised-design benchmark: where an RL design agent can beat per-mission optimisation.

For one fixed mission, black-box optimisers (differential evolution) are hard to beat. A
policy trained across a *distribution* of missions, however, has paid its search cost up front.
On a new mission it needs only a few rollouts. This benchmark measures that claim instead
of assuming it:

1. Train PPO on missions sampled around the presets (``mission_sampler_from_presets``).
2. On held-out missions (fresh jitter seeds), give every method the same small budget of
   design evaluations: the trained policy (stochastic rollouts), differential evolution and
   random search.
3. Report the best feasible score per method and mission.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from bldc_motor.design import baselines, ppo
from bldc_motor.design.env import DesignArchive, MotorDesignEnv, mission_sampler_from_presets


@dataclass
class BenchmarkResult:
    budget: int
    rows: list[dict] = field(default_factory=list)
    training: ppo.PPOResult | None = None

    def summary(self) -> dict:
        out = {}
        for method in ("rl_policy", "differential_evolution", "random_search"):
            scores = np.array([r[method] for r in self.rows], float)
            feasible = np.isfinite(scores)
            out[method] = {
                "missions_with_feasible_design": int(feasible.sum()),
                "mean_best_feasible_score": float(np.mean(scores[feasible])) if feasible.any() else math.nan,
                "wins": int(sum(1 for r in self.rows if r["winner"] == method)),
            }
        return out


def _best_feasible(archive: DesignArchive) -> float:
    best = archive.best
    return best[0] if best and best[1] else math.nan


def amortized_benchmark(
    train_steps: int = 150_000,
    test_missions: int = 6,
    rollouts_per_mission: int = 8,
    max_steps: int = 24,
    seed: int = 0,
    jitter: float = 0.15,
    log=print,
) -> BenchmarkResult:
    sampler = mission_sampler_from_presets(jitter=jitter)
    train_env = MotorDesignEnv(mission_sampler=sampler, max_steps=max_steps)
    log(f"training a mission-conditioned design policy for {train_steps} steps")
    training = ppo.train_ppo(train_env, ppo.PPOConfig(total_steps=train_steps, seed=seed))
    budget = rollouts_per_mission * (max_steps + 1)
    result = BenchmarkResult(budget=budget, training=training)
    rng = np.random.default_rng(seed + 12345)  # held-out mission draws
    for index in range(test_missions):
        mission = sampler(rng)
        rl_archive = DesignArchive()
        env = MotorDesignEnv(mission=mission, max_steps=max_steps, archive=rl_archive)
        for k in range(rollouts_per_mission):
            ppo.rollout(env, training.policy, deterministic=False, seed=seed + 1000 * index + k)
        de = baselines.differential_evolution_search(mission, budget, seed + index, popsize=5)
        rs = baselines.random_search(mission, budget, seed + index)
        row = {
            "mission": mission.name,
            "dry_mass_g": mission.dry_mass_g,
            "min_thrust_to_weight": mission.min_thrust_to_weight,
            "rl_policy": _best_feasible(rl_archive),
            "differential_evolution": _best_feasible(de),
            "random_search": _best_feasible(rs),
            "evaluations": {
                "rl_policy": rl_archive.evaluations,
                "differential_evolution": de.evaluations,
                "random_search": rs.evaluations,
            },
        }
        scores = {m: row[m] for m in ("rl_policy", "differential_evolution", "random_search")}
        finite = {m: s for m, s in scores.items() if math.isfinite(s)}
        row["winner"] = max(finite, key=finite.get) if finite else "none"
        result.rows.append(row)
        log(
            f"  {mission.name:18s} dry {mission.dry_mass_g:5.0f} g T/W>={mission.min_thrust_to_weight:4.1f} | "
            + "  ".join(f"{m}={s:+.3f}" for m, s in scores.items())
            + f"  -> {row['winner']}"
        )
    return result
