"""Gymnasium environment: run the RL2407 factory (discrete-event simulation in ``sim``).

* **Step** = one productive hour; the step that ends a shift also simulates the night (oven cures,
  winder cycles, deliveries). An episode is ``days`` working days (7 steps per day).
* **Action** = Box(-1, 1, n_ops + 9), decoded into discrete operations decisions (Box keeps the env
  compatible with the NumPy PPO here and with SB3 SAC/PPO):
  operator zones (stator / oven+base / rotor / final / flex) x n_ops, oven batching rule, kit order
  quantity (0/25/50/100), goods-in inspection fraction (0/10/100 %), gauss pole check, loaded-test
  sampling (1/50, 1/10, all), CNC winder use, preventive maintenance request, rotor kanban size,
  stator CONWIP cap.
* **Observation** = normalised buffers, stock, kits in transit, machine states (winder age vs its
  wear scale), order backlog and lateness, recent yields, time of day and episode progress.
* **Reward** = change in profit / 100 (revenue - material - order fees - labour - holding - late
  penalties - warranty for escaped defects). At the horizon, unused kits and WIP are added back as
  salvage, so the agent cannot profit by starving the line of material near the end.
"""

from __future__ import annotations

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from bldc_motor.production import sim as S

STEP_MIN = 60.0


def decode(action: np.ndarray, n_ops: int) -> S.Controls:
    a = np.clip(np.asarray(action, dtype=float), -1.0, 1.0)

    def pick(x: float, n: int) -> int:
        return min(int((x + 1.0) / 2.0 * n), n - 1)

    rest = a[n_ops:]
    return S.Controls(
        zones=[pick(x, len(S.ZONES)) for x in a[:n_ops]],
        oven_rule=pick(rest[0], len(S.OVEN_RULES)),
        order_qty=pick(rest[1], len(S.ORDER_QTY)),
        inspect=pick(rest[2], len(S.INSPECT_FRACTIONS)),
        gauss_check=pick(rest[3], 2),
        loaded_test=pick(rest[4], len(S.LOADED_TEST_RATES)),
        use_cnc=pick(rest[5], 2),
        preventive_maintenance=pick(rest[6], 2),
        rotor_cap=pick(rest[7], len(S.ROTOR_CAPS)),
        stator_cap=pick(rest[8], len(S.STATOR_CAPS)),
    )


def encode(c: S.Controls, n_ops: int) -> np.ndarray:
    """Inverse of ``decode`` (bin centres): lets heuristic policies act through the same interface."""

    def centre(i: int, n: int) -> float:
        return (i + 0.5) / n * 2.0 - 1.0

    zones = [centre(z, len(S.ZONES)) for z in (c.zones + [4] * n_ops)[:n_ops]]
    rest = [
        centre(c.oven_rule, len(S.OVEN_RULES)), centre(c.order_qty, len(S.ORDER_QTY)), centre(c.inspect, len(S.INSPECT_FRACTIONS)),
        centre(c.gauss_check, 2), centre(c.loaded_test, len(S.LOADED_TEST_RATES)), centre(c.use_cnc, 2),
        centre(c.preventive_maintenance, 2), centre(c.rotor_cap, len(S.ROTOR_CAPS)), centre(c.stator_cap, len(S.STATOR_CAPS)),
    ]  # fmt: skip
    return np.array(zones + rest, dtype=np.float32)


class FactoryEnv(gym.Env):
    metadata = {"render_modes": ["ansi"]}

    def __init__(self, config: S.SimConfig | None = None, reward_scale: float = 0.01, randomize_seed: bool = True):
        super().__init__()
        self.base_config = config or S.SimConfig()
        self.reward_scale = reward_scale
        self.randomize_seed = randomize_seed
        n = self.base_config.operators
        self.n_ops = n
        self.action_space = spaces.Box(-1.0, 1.0, shape=(n + 9,), dtype=np.float32)
        self.observation_space = spaces.Box(-5.0, 5.0, shape=(len(self._obs_template()),), dtype=np.float32)
        self.sim: S.LineSim | None = None
        self.steps = 0
        self.max_steps = self.base_config.days * int(self.base_config.shift_min // STEP_MIN)

    def _obs_template(self) -> list[str]:
        return [
            "day_frac", "progress", "stock_stator", "stock_rotor", "stock_base", "received", "in_transit",
            "to_term", "to_test1", "to_oven", "oven_batch", "cured", "base_ready", "to_balance", "rotor_ready", "to_pack",
            "finished", "wip", "rotors", "oven_idle", "oven_busy", "oven_done", "winder_idle", "winder_busy", "winder_down",
            "winder_age", "backlog", "late", "next_due", "s5_fpy", "eol_yield",
        ] + [f"zone_{i}" for i in range(self.base_config.operators)]  # fmt: skip

    def _t_end(self, i: int) -> float:
        per_day = int(self.base_config.shift_min // STEP_MIN)
        return (i // per_day) * 1440.0 + STEP_MIN * (i % per_day + 1)

    def _observation(self) -> np.ndarray:
        sim = self.sim
        k = sim.k
        t = sim.t
        backlog = sum(q for _, q in sim.orders)
        late = sum(q for due, q in sim.orders if due < t)
        next_due = (sim.orders[0][0] - t) / 1440.0 / 7.0 if sim.orders else 1.0
        oven, winder = sim.oven["state"], sim.winder["state"]
        values = [
            (t % 1440.0) / sim.cfg.shift_min, self.steps / self.max_steps,
            sim.stock["stator"] / 100, sim.stock["rotor"] / 100, sim.stock["base"] / 100, sim.received / 100, sim.in_transit / 100,
            len(sim.to_term) / 20, len(sim.to_test1) / 20, len(sim.to_oven) / 20, len(sim.oven["batch"]) / 12, len(sim.cured) / 20,
            len(sim.base_ready) / 20, len(sim.to_balance) / 20, len(sim.rotor_ready) / 20, len(sim.to_pack) / 20, len(sim.finished) / 50,
            sim.wip() / 50, sim.rotors_in_system / 16,
            float(oven == "idle"), float(oven in ("loading", "curing")), float(oven in ("done", "unloading")),
            float(winder == "idle"), float(winder in ("loading", "running", "done", "unloading")), float(winder in ("broken", "maintenance")),
            sim.winder["age_min"] / (sim.cfg.winder_weibull_scale_h * 60.0),
            backlog / 100, late / 100, next_due,
            1.0 - k["s5_fail"] / max(k["s5_tested"], 1), 1.0 - k["eol_fail"] / max(k["eol_tested"], 1),
        ] + [z / 4.0 for z in sim.controls.zones]  # fmt: skip
        return np.clip(np.array(values, dtype=np.float32), -5.0, 5.0)

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        cfg = self.base_config
        if self.randomize_seed:
            cfg = S.SimConfig(**{**cfg.__dict__, "seed": int(self.np_random.integers(0, 2**31 - 1))})
        self.sim = S.LineSim(cfg)
        self.steps = 0
        self._profit = self.sim.profit()
        return self._observation(), self._info()

    def step(self, action):
        sim = self.sim
        controls = decode(action, self.n_ops)
        sim.controls = controls
        sim.place_order(S.ORDER_QTY[controls.order_qty])
        sim.run_until(self._t_end(self.steps))
        self.steps += 1
        truncated = self.steps >= self.max_steps
        profit = sim.profit() + (sim.salvage_value() if truncated else 0.0)
        reward = (profit - self._profit) * self.reward_scale
        self._profit = profit
        return self._observation(), float(reward), False, truncated, self._info()

    def _info(self) -> dict:
        end = self.steps == self.max_steps
        profit = self.sim.profit() + (self.sim.salvage_value() if end else 0.0)
        return {"profit": profit, "score": profit / 1000.0, "kpis": self.sim.kpis() if end else None}

    def render(self):
        k = self.sim.kpis()
        return (
            f"day {self.sim.t / 1440:.1f}: shipped {k['shipped']}, WIP {k['avg_wip']:.1f}, profit ${k['profit']:,.0f}"
        )


# ---------------------------------------------------------------------------------------------
# Baseline policies (act through the same action interface)
# ---------------------------------------------------------------------------------------------


class HeuristicPolicy:
    """Textbook operations rules: flexible staffing, pull caps, (s, Q) reordering, age-based PM.

    * staff: one operator owns the stator branch (winding is the bottleneck), the rest float (flex)
    * oven: start at half load (and any load near shift end)
    * reorder Q = 100 kits when inventory position < demand x (lead time + 1 day) + safety stock
    * maintenance: request PM when the winder has used 60 % of its wear-out scale
    """

    def __init__(self, env: FactoryEnv, staffing: str = "zoned"):
        self.env = env
        self.staffing = staffing

    def __call__(self, obs: np.ndarray) -> np.ndarray:
        sim = self.env.sim
        cfg = sim.cfg
        n = self.env.n_ops
        zones = [4] * n if self.staffing == "flex" or n == 1 else [0] + [4] * (n - 1)
        demand_per_day = cfg.demand_orders_per_day * sum(cfg.order_size) / 2.0
        reorder_point = demand_per_day * (cfg.lead_time_days + 1.0) + 2.0 * demand_per_day
        position = min(sim.stock.values()) + sim.received + sim.in_transit
        order = 3 if position < reorder_point else 0
        pm = 1 if sim.winder["age_min"] > 0.6 * cfg.winder_weibull_scale_h * 60.0 else 0
        controls = S.Controls(zones=zones, oven_rule=2, order_qty=order, inspect=1, gauss_check=1, loaded_test=0,
                              use_cnc=1, preventive_maintenance=pm, rotor_cap=1, stator_cap=2)  # fmt: skip
        return encode(controls, n)


def evaluate(env: FactoryEnv, policy, episodes: int = 5, seed: int = 1000) -> dict:
    """Run complete episodes; returns mean KPIs (profit includes horizon salvage)."""

    results = []
    for e in range(episodes):
        obs, _ = env.reset(seed=seed + e)
        done = False
        total = 0.0
        while not done:
            obs, reward, terminated, truncated, info = env.step(policy(obs))
            total += reward
            done = terminated or truncated
        k = env.sim.kpis()
        k["episode_return"] = total
        results.append(k)
    keys = [k for k, v in results[0].items() if isinstance(v, (int, float))]
    mean = {k: float(np.mean([r[k] for r in results])) for k in keys}
    mean["profit_std"] = float(np.std([r["profit_with_salvage"] for r in results]))
    return mean


def random_policy(env: FactoryEnv, seed: int = 0):
    rng = np.random.default_rng(seed)
    return lambda obs: rng.uniform(-1.0, 1.0, env.action_space.shape).astype(np.float32)


def ppo_policy(policy):
    rng = np.random.default_rng(0)
    return lambda obs: np.clip(policy.act(np.asarray(obs, float), rng, deterministic=True)[0], -1.0, 1.0).astype(
        np.float32
    )


class ResidualFactoryEnv(gym.Wrapper):
    """Residual RL: the agent's action is a correction added to a base heuristic's action.

    ``a_env = clip(a_heuristic + scale * a_agent)``. A near-zero initial policy (the PPO output layer is
    initialised small) therefore starts at heuristic performance and only has to learn improvements,
    which makes credit assignment over the 140-step horizon much easier (Johannink et al., 2019).
    """

    def __init__(self, env: FactoryEnv, scale: float = 1.0, staffing: str = "flex"):
        super().__init__(env)
        self.scale = scale
        self.base_policy = HeuristicPolicy(env, staffing)
        self._obs = None

    def reset(self, **kwargs):
        self._obs, info = self.env.reset(**kwargs)
        return self._obs, info

    def step(self, action):
        base = self.base_policy(self._obs)
        combined = np.clip(base + self.scale * np.asarray(action, dtype=np.float32), -1.0, 1.0)
        self._obs, reward, terminated, truncated, info = self.env.step(combined)
        return self._obs, reward, terminated, truncated, info


def residual_policy(env: FactoryEnv, policy, scale: float = 1.0, staffing: str = "flex"):
    base = HeuristicPolicy(env, staffing)
    rng = np.random.default_rng(0)

    def act(obs):
        delta = policy.act(np.asarray(obs, float), rng, deterministic=True)[0]
        return np.clip(base(obs) + scale * delta, -1.0, 1.0).astype(np.float32)

    return act
