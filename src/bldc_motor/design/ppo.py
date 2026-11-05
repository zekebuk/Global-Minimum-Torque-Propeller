"""Proximal Policy Optimization in NumPy (no deep-learning framework required).

Standard clipped-surrogate PPO (Schulman et al., 2017) with GAE(lambda), a diagonal-Gaussian
policy whose mean comes from a tanh MLP (state-independent log-std), a separate value MLP,
running observation normalisation, Adam and global-norm gradient clipping. Gradients are
derived by hand; ``tests/test_bldc_design_rl.py`` checks them against finite differences.

Works with any Gymnasium env that has a Box action space. For larger studies, the same
environment also runs with Stable-Baselines3 (``train_sb3``), if that is installed.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

LOG_2PI = math.log(2.0 * math.pi)


# ---- network ---------------------------------------------------------------------------
class MLP:
    def __init__(self, sizes: list[int], rng: np.random.Generator, out_gain: float = 1.0) -> None:
        self.params: list[np.ndarray] = []
        for index, (fan_in, fan_out) in enumerate(zip(sizes[:-1], sizes[1:], strict=True)):
            gain = out_gain if index == len(sizes) - 2 else math.sqrt(2.0)
            self.params.append(rng.normal(0.0, gain / math.sqrt(fan_in), size=(fan_in, fan_out)))
            self.params.append(np.zeros(fan_out))

    @property
    def n_layers(self) -> int:
        return len(self.params) // 2

    def forward(self, x: np.ndarray) -> tuple[np.ndarray, list[np.ndarray]]:
        activations = [x]
        h = x
        for layer in range(self.n_layers):
            w, b = self.params[2 * layer], self.params[2 * layer + 1]
            z = h @ w + b
            h = np.tanh(z) if layer < self.n_layers - 1 else z
            activations.append(h)
        return h, activations

    def backward(self, activations: list[np.ndarray], grad_out: np.ndarray) -> list[np.ndarray]:
        grads: list[np.ndarray] = [np.zeros_like(p) for p in self.params]
        g = grad_out
        for layer in reversed(range(self.n_layers)):
            h_in = activations[layer]
            w = self.params[2 * layer]
            grads[2 * layer] = h_in.T @ g
            grads[2 * layer + 1] = g.sum(axis=0)
            if layer > 0:
                g = (g @ w.T) * (1.0 - activations[layer] ** 2)
        return grads


class Adam:
    def __init__(self, params: list[np.ndarray], lr: float, betas=(0.9, 0.999), eps: float = 1e-8) -> None:
        self.params, self.lr, self.betas, self.eps = params, lr, betas, eps
        self.m = [np.zeros_like(p) for p in params]
        self.v = [np.zeros_like(p) for p in params]
        self.t = 0

    def step(self, grads: list[np.ndarray]) -> None:
        self.t += 1
        b1, b2 = self.betas
        for p, g, m, v in zip(self.params, grads, self.m, self.v, strict=True):
            m *= b1
            m += (1 - b1) * g
            v *= b2
            v += (1 - b2) * g * g
            m_hat = m / (1 - b1**self.t)
            v_hat = v / (1 - b2**self.t)
            p -= self.lr * m_hat / (np.sqrt(v_hat) + self.eps)


class RunningNorm:
    def __init__(self, dim: int) -> None:
        self.mean = np.zeros(dim)
        self.var = np.ones(dim)
        self.count = 1e-4

    def update(self, batch: np.ndarray) -> None:
        batch_mean, batch_var, n = batch.mean(axis=0), batch.var(axis=0), batch.shape[0]
        delta = batch_mean - self.mean
        total = self.count + n
        self.mean = self.mean + delta * n / total
        self.var = (self.var * self.count + batch_var * n + delta**2 * self.count * n / total) / total
        self.count = total

    def __call__(self, x: np.ndarray) -> np.ndarray:
        return np.clip((x - self.mean) / np.sqrt(self.var + 1e-8), -10.0, 10.0)


# ---- policy ------------------------------------------------------------------------------
class GaussianPolicy:
    def __init__(self, obs_dim: int, act_dim: int, hidden=(64, 64), log_std_init: float = -0.5, seed: int = 0) -> None:
        rng = np.random.default_rng(seed)
        self.pi = MLP([obs_dim, *hidden, act_dim], rng, out_gain=0.01)
        self.vf = MLP([obs_dim, *hidden, 1], rng, out_gain=1.0)
        self.log_std = np.full(act_dim, log_std_init)
        self.norm = RunningNorm(obs_dim)

    def distribution(self, obs_n: np.ndarray):
        mean, cache = self.pi.forward(obs_n)
        return mean, cache

    def act(self, obs: np.ndarray, rng: np.random.Generator, deterministic: bool = False):
        obs_n = self.norm(obs[None, :])
        mean, _ = self.pi.forward(obs_n)
        mean = mean[0]
        std = np.exp(self.log_std)
        action = mean if deterministic else mean + std * rng.standard_normal(mean.shape)
        logp = gaussian_logp(action[None, :], mean[None, :], self.log_std)[0]
        value = self.vf.forward(obs_n)[0][0, 0]
        return action, logp, value

    def value(self, obs: np.ndarray) -> float:
        return float(self.vf.forward(self.norm(obs[None, :]))[0][0, 0])

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        arrays = {f"pi_{i}": p for i, p in enumerate(self.pi.params)}
        arrays |= {f"vf_{i}": p for i, p in enumerate(self.vf.params)}
        arrays |= {
            "log_std": self.log_std,
            "norm_mean": self.norm.mean,
            "norm_var": self.norm.var,
            "norm_count": np.array([self.norm.count]),
        }
        np.savez(target, **arrays)
        return target

    @classmethod
    def load(cls, path: str | Path) -> GaussianPolicy:
        data = np.load(path)
        pi = [data[f"pi_{i}"] for i in range(sum(k.startswith("pi_") for k in data.files))]
        vf = [data[f"vf_{i}"] for i in range(sum(k.startswith("vf_") for k in data.files))]
        hidden = tuple(w.shape[1] for w in pi[0:-2:2])
        policy = cls(pi[0].shape[0], pi[-1].shape[0], hidden)
        policy.pi.params, policy.vf.params = pi, vf
        policy.log_std = data["log_std"]
        policy.norm.mean, policy.norm.var, policy.norm.count = (
            data["norm_mean"],
            data["norm_var"],
            float(data["norm_count"][0]),
        )
        return policy


def gaussian_logp(actions: np.ndarray, mean: np.ndarray, log_std: np.ndarray) -> np.ndarray:
    z = (actions - mean) / np.exp(log_std)
    return -0.5 * np.sum(z**2, axis=1) - np.sum(log_std) - 0.5 * actions.shape[1] * LOG_2PI


def policy_loss_and_grads(
    policy: GaussianPolicy,
    obs_n: np.ndarray,
    actions: np.ndarray,
    advantages: np.ndarray,
    logp_old: np.ndarray,
    clip: float,
    ent_coef: float,
):
    """Clipped surrogate loss (to minimise) and its gradients w.r.t. policy MLP and log_std."""

    mean, cache = policy.pi.forward(obs_n)
    std = np.exp(policy.log_std)
    logp = gaussian_logp(actions, mean, policy.log_std)
    ratio = np.exp(logp - logp_old)
    surr1 = ratio * advantages
    surr2 = np.clip(ratio, 1.0 - clip, 1.0 + clip) * advantages
    batch = actions.shape[0]
    entropy = float(np.sum(policy.log_std) + 0.5 * actions.shape[1] * (1.0 + LOG_2PI))
    loss = -float(np.mean(np.minimum(surr1, surr2))) - ent_coef * entropy
    unclipped = surr1 <= surr2  # min() picks the unclipped branch -> gradient flows
    dloss_dlogp = np.where(unclipped, -ratio * advantages, 0.0) / batch
    diff = actions - mean
    grad_mean = dloss_dlogp[:, None] * diff / std**2
    grad_log_std = np.sum(dloss_dlogp[:, None] * (diff**2 / std**2 - 1.0), axis=0) - ent_coef
    grads = policy.pi.backward(cache, grad_mean)
    approx_kl = float(np.mean(logp_old - logp))
    clip_frac = float(np.mean(np.abs(ratio - 1.0) > clip))
    return loss, grads, grad_log_std, {"approx_kl": approx_kl, "clip_frac": clip_frac, "entropy": entropy}


def value_loss_and_grads(policy: GaussianPolicy, obs_n: np.ndarray, returns: np.ndarray):
    values, cache = policy.vf.forward(obs_n)
    err = values[:, 0] - returns
    loss = 0.5 * float(np.mean(err**2))
    grads = policy.vf.backward(cache, (err / err.size)[:, None])
    return loss, grads


def _clip_global(grads: list[np.ndarray], max_norm: float) -> list[np.ndarray]:
    norm = math.sqrt(sum(float(np.sum(g * g)) for g in grads))
    if norm > max_norm:
        grads = [g * (max_norm / norm) for g in grads]
    return grads


# ---- training ----------------------------------------------------------------------------
@dataclass
class PPOConfig:
    total_steps: int = 60_000
    rollout_steps: int = 2048
    epochs: int = 10
    minibatch: int = 128
    gamma: float = 0.995
    gae_lambda: float = 0.95
    clip: float = 0.2
    lr: float = 3e-4
    vf_lr: float = 1e-3
    ent_coef: float = 0.0
    max_grad_norm: float = 0.5
    target_kl: float = 0.03
    hidden: tuple[int, ...] = (64, 64)
    log_std_init: float = -0.5
    log_std_bounds: tuple[float, float] = (-3.0, 0.5)
    seed: int = 0


@dataclass
class PPOResult:
    policy: GaussianPolicy
    config: PPOConfig
    log: list[dict] = field(default_factory=list)
    episode_returns: list[float] = field(default_factory=list)
    episode_final_scores: list[float] = field(default_factory=list)
    wall_time_s: float = 0.0

    def config_dict(self) -> dict:
        return asdict(self.config)


def train_ppo(env, config: PPOConfig | None = None, callback: Callable[[dict], None] | None = None) -> PPOResult:
    cfg = config or PPOConfig()
    rng = np.random.default_rng(cfg.seed)
    obs_dim = env.observation_space.shape[0]
    act_dim = env.action_space.shape[0]
    policy = GaussianPolicy(obs_dim, act_dim, cfg.hidden, cfg.log_std_init, cfg.seed)
    pi_opt = Adam(policy.pi.params + [policy.log_std], cfg.lr)
    vf_opt = Adam(policy.vf.params, cfg.vf_lr)
    result = PPOResult(policy, cfg)
    start = time.time()

    obs, info = env.reset(seed=cfg.seed)
    episode_return = 0.0
    steps_done = 0
    while steps_done < cfg.total_steps:
        n = min(cfg.rollout_steps, cfg.total_steps - steps_done)
        buf_obs = np.zeros((n, obs_dim))
        buf_act = np.zeros((n, act_dim))
        buf_logp = np.zeros(n)
        buf_val = np.zeros(n)
        buf_rew = np.zeros(n)
        buf_done = np.zeros(n)
        policy.norm.update(np.asarray(obs)[None, :])
        for t in range(n):
            action, logp, value = policy.act(np.asarray(obs, float), rng)
            next_obs, reward, terminated, truncated, info = env.step(action)
            buf_obs[t], buf_act[t], buf_logp[t], buf_val[t], buf_rew[t] = obs, action, logp, value, reward
            done = terminated or truncated  # progress is in the state, so the horizon is terminal
            buf_done[t] = float(done)
            episode_return += reward
            obs = next_obs
            if done:
                result.episode_returns.append(episode_return)
                result.episode_final_scores.append(info["score"])
                episode_return = 0.0
                obs, info = env.reset()
        steps_done += n
        last_value = 0.0 if buf_done[-1] else policy.value(np.asarray(obs, float))

        # GAE
        adv = np.zeros(n)
        gae = 0.0
        for t in reversed(range(n)):
            next_value = last_value if t == n - 1 else buf_val[t + 1]
            nonterminal = 1.0 - buf_done[t]
            delta = buf_rew[t] + cfg.gamma * next_value * nonterminal - buf_val[t]
            gae = delta + cfg.gamma * cfg.gae_lambda * nonterminal * gae
            adv[t] = gae
        returns = adv + buf_val

        policy.norm.update(buf_obs)
        obs_n = policy.norm(buf_obs)
        stats = {}
        for _epoch in range(cfg.epochs):
            order = rng.permutation(n)
            kl_epoch = []
            for begin in range(0, n, cfg.minibatch):
                idx = order[begin : begin + cfg.minibatch]
                if idx.size < 2:
                    continue
                a = adv[idx]
                a = (a - a.mean()) / (a.std() + 1e-8)
                loss_pi, g_pi, g_std, stats = policy_loss_and_grads(
                    policy, obs_n[idx], buf_act[idx], a, buf_logp[idx], cfg.clip, cfg.ent_coef
                )
                grads = _clip_global(g_pi + [g_std], cfg.max_grad_norm)
                pi_opt.step(grads)
                np.clip(policy.log_std, *cfg.log_std_bounds, out=policy.log_std)
                loss_v, g_v = value_loss_and_grads(policy, obs_n[idx], returns[idx])
                vf_opt.step(_clip_global(g_v, cfg.max_grad_norm))
                kl_epoch.append(stats["approx_kl"])
            if kl_epoch and np.mean(kl_epoch) > 1.5 * cfg.target_kl:
                break
        recent = result.episode_final_scores[-50:]
        entry = {
            "steps": steps_done,
            "episodes": len(result.episode_returns),
            "mean_final_score_last50": float(np.mean(recent)) if recent else math.nan,
            "mean_return_last50": float(np.mean(result.episode_returns[-50:])) if result.episode_returns else math.nan,
            "policy_loss": loss_pi,
            "value_loss": loss_v,
            "approx_kl": stats.get("approx_kl", math.nan),
            "clip_frac": stats.get("clip_frac", math.nan),
            "mean_std": float(np.exp(policy.log_std).mean()),
            "wall_s": time.time() - start,
        }
        result.log.append(entry)
        if callback:
            callback(entry)
    result.wall_time_s = time.time() - start
    return result


def rollout(
    env, policy: GaussianPolicy, deterministic: bool = True, seed: int | None = None, options: dict | None = None
):
    """Run one episode with a trained policy; returns (final info, list of per-step infos)."""

    rng = np.random.default_rng(seed)
    obs, info = env.reset(seed=seed, options=options)
    trace = [info]
    done = False
    while not done:
        action, _, _ = policy.act(np.asarray(obs, float), rng, deterministic)
        obs, _, terminated, truncated, info = env.step(action)
        trace.append(info)
        done = terminated or truncated
    return info, trace


def train_sb3(env, algorithm: str = "SAC", total_steps: int = 100_000, seed: int = 0, **kwargs):
    """Optional: train with Stable-Baselines3 (``pip install stable-baselines3``) on the same env."""

    try:
        import stable_baselines3 as sb3
    except ImportError as error:  # pragma: no cover - optional dependency
        raise ImportError(
            "Stable-Baselines3 is not installed; use train_ppo or `pip install stable-baselines3`"
        ) from error
    model = getattr(sb3, algorithm)("MlpPolicy", env, seed=seed, verbose=0, **kwargs)  # pragma: no cover
    model.learn(total_timesteps=total_steps)  # pragma: no cover
    return model  # pragma: no cover
