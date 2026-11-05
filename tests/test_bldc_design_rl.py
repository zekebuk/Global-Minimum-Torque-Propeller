"""Gymnasium environment, NumPy PPO and end-to-end pipeline checks."""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET

import numpy as np
import pytest

gym = pytest.importorskip("gymnasium")

from bldc_motor.design import benchmark, export, geometry, model, ppo  # noqa: E402
from bldc_motor.design.env import MotorDesignEnv, shaped_potential  # noqa: E402
from bldc_motor.design.pipeline import PipelineConfig, run_pipeline  # noqa: E402
from bldc_motor.library import load_library  # noqa: E402


def test_env_passes_gymnasium_checker() -> None:
    from gymnasium.utils.env_checker import check_env

    check_env(MotorDesignEnv(max_steps=4), skip_render_check=True)


def test_reward_telescopes_to_final_minus_initial() -> None:
    env = MotorDesignEnv(max_steps=10)
    _, info = env.reset(seed=3)
    initial = info["score"]
    total = 0.0
    rng = np.random.default_rng(0)
    done = False
    while not done:
        _, reward, terminated, truncated, info = env.step(rng.uniform(-1, 1, env.action_space.shape))
        total += reward
        done = terminated or truncated
    assert total == pytest.approx(shaped_potential(info["score"]) - shaped_potential(initial))
    assert env.archive.evaluations == 11


def test_shaping_is_monotone() -> None:
    xs = np.linspace(-50, 5, 500)
    ys = [shaped_potential(x) for x in xs]
    assert all(b > a for a, b in zip(ys, ys[1:], strict=False))


def test_absolute_mode_is_one_step() -> None:
    env = MotorDesignEnv(action_mode="absolute")
    env.reset(seed=0)
    _, _, _, truncated, info = env.step(np.zeros(geometry.DIMENSION))
    assert truncated and info["design"]["slot_pole"] in geometry.PARAMETERS[10].choices


def test_policy_gradients_match_finite_differences() -> None:
    rng = np.random.default_rng(0)
    pol = ppo.GaussianPolicy(5, 3, (8, 8), seed=1)
    for p in pol.pi.params:
        p += rng.normal(0, 0.3, p.shape)
    obs, act, adv = rng.normal(size=(16, 5)), rng.normal(size=(16, 3)), rng.normal(size=16)
    mean, _ = pol.pi.forward(obs)
    logp_old = ppo.gaussian_logp(act, mean, pol.log_std) + rng.normal(0, 0.1, 16)
    _, grads, g_std, _ = ppo.policy_loss_and_grads(pol, obs, act, adv, logp_old, 0.2, 0.01)
    returns = rng.normal(size=16)
    _, v_grads = ppo.value_loss_and_grads(pol, obs, returns)

    def check(params, analytic, loss_fn):
        for p, g in zip(params, analytic, strict=True):
            for _ in range(4):
                idx = tuple(rng.integers(s) for s in p.shape)
                old = p[idx]
                p[idx] = old + 1e-6
                plus = loss_fn()
                p[idx] = old - 1e-6
                minus = loss_fn()
                p[idx] = old
                assert (plus - minus) / 2e-6 == pytest.approx(g[idx], abs=1e-6)

    check(
        pol.pi.params + [pol.log_std],
        grads + [g_std],
        lambda: ppo.policy_loss_and_grads(pol, obs, act, adv, logp_old, 0.2, 0.01)[0],
    )
    check(pol.vf.params, v_grads, lambda: ppo.value_loss_and_grads(pol, obs, returns)[0])


class _TargetEnv(gym.Env):
    """Toy continuous control: reward = -|a - target(obs)|^2. PPO must learn the mapping."""

    def __init__(self):
        self.observation_space = gym.spaces.Box(-1, 1, (2,), np.float32)
        self.action_space = gym.spaces.Box(-1, 1, (2,), np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.obs = self.np_random.uniform(-1, 1, 2).astype(np.float32)
        return self.obs, {"score": 0.0}

    def step(self, action):
        target = np.array([self.obs[0], -self.obs[1]]) * 0.8
        reward = -float(np.sum((np.clip(action, -1, 1) - target) ** 2))
        return self.obs, reward, True, False, {"score": reward}


def test_ppo_learns_toy_problem() -> None:
    result = ppo.train_ppo(
        _TargetEnv(), ppo.PPOConfig(total_steps=20_000, rollout_steps=1000, minibatch=100, seed=0, lr=1e-3)
    )
    first, last = result.episode_returns[:500], result.episode_returns[-500:]
    assert np.mean(last) > np.mean(first) + 0.3
    assert np.mean(last) > -0.15


def test_policy_save_load(tmp_path) -> None:
    pol = ppo.GaussianPolicy(7, 3, (16, 16), seed=2)
    pol.norm.update(np.random.default_rng(0).normal(size=(50, 7)))
    path = pol.save(tmp_path / "p.npz")
    loaded = ppo.GaussianPolicy.load(path)
    obs = np.ones(7)
    a1 = pol.act(obs, np.random.default_rng(0), deterministic=True)[0]
    a2 = loaded.act(obs, np.random.default_rng(0), deterministic=True)[0]
    np.testing.assert_allclose(a1, a2)


def test_pipeline_end_to_end(tmp_path) -> None:
    config = PipelineConfig(
        rl_steps=2048, exploit_rollouts=2, yield_samples=10, top_k_per_method=2, out_dir=str(tmp_path), min_yield=0.0
    )
    result = run_pipeline(config, log=lambda *_: None)
    assert result.winner is not None
    assert set(result.method_summary) == {"rl_ppo", "differential_evolution", "random_search"}
    budgets = {s["evaluations"] for s in result.method_summary.values()}
    assert max(budgets) - min(budgets) <= 255  # same evaluation budget (DE rounds to whole generations)
    for key in (
        "lamination_dxf",
        "cross_section_svg",
        "winding_csv",
        "bom_csv",
        "library_json",
        "design_json",
        "spec_sheet",
        "convergence",
    ):
        assert (tmp_path / result.outputs[key].split("/")[-1]).exists(), key

    # DXF: one LINE per outline vertex + one bore CIRCLE
    dxf = (tmp_path / "lamination.dxf").read_text().splitlines()
    analysis = model.analyze(result.winner.design)
    assert dxf.count("LINE") == len(export.stator_outline_mm(analysis))
    assert dxf.count("CIRCLE") == 1 and dxf[-1] == "EOF"
    ET.parse(tmp_path / "cross_section.svg")  # well-formed

    payload = json.loads((tmp_path / "design.json").read_text())
    design = export.design_from_json(payload["design"])
    assert design == result.winner.design
    motor = load_library(tmp_path / "library.json", include_bundled=False).motor(payload["name"])
    assert motor.kv_rpm_per_v == pytest.approx(payload["metrics"]["kv_rpm_per_v"])
    assert "SYNTHETIC" in (tmp_path / "predicted_stand_curve.csv").read_text().splitlines()[0]


def test_benchmark_runs() -> None:
    result = benchmark.amortized_benchmark(
        train_steps=2048, test_missions=1, rollouts_per_mission=1, max_steps=6, log=lambda *_: None
    )
    assert result.budget == 7 and len(result.rows) == 1
    assert set(result.summary()) == {"rl_policy", "differential_evolution", "random_search"}
    assert all(math.isnan(v) or isinstance(v, float) for v in (result.rows[0]["rl_policy"],))
