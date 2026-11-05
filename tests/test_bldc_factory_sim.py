"""Factory simulation: validation against theory (capacity, Little's law, conservation, detection
probabilities) and Gymnasium environment checks."""

from __future__ import annotations

import numpy as np
import pytest

gym = pytest.importorskip("gymnasium")

from bldc_motor.design import manufacturing_package as mp  # noqa: E402
from bldc_motor.production import assembly_line as al  # noqa: E402
from bldc_motor.production import sim as S  # noqa: E402
from bldc_motor.production import sim_physics as sp  # noqa: E402
from bldc_motor.production.sim_env import FactoryEnv, HeuristicPolicy, decode, encode, evaluate, random_policy  # noqa: E402

DESIGN = mp.Path(__file__).resolve().parents[1] / "data" / "motor_designs" / "rl_5in_freestyle_6s_v1" / "design.json"


@pytest.fixture(scope="module")
def sens():
    design, _ = mp.load_design(DESIGN)
    return sp.sensitivity(design)


def steady_rate(cfg: S.SimConfig, controls: S.Controls, warmup_days: int = 3) -> float:
    sim = S.LineSim(cfg)
    sim.controls = controls
    sim.run_until(warmup_days * 1440.0)
    start = sim.k["completed"]
    sim.run_until(cfg.days * 1440.0)
    return (sim.k["completed"] - start) / (cfg.days - warmup_days)


@pytest.mark.parametrize("cnc, labour", [(False, sum(s.labour_min for s in al.STATIONS)), (True, None)])
def test_deterministic_capacity_matches_line_plan(cnc, labour):
    cfg = S.SimConfig(
        days=20, deterministic=True, operators=1, initial_kits=100000, cnc_winder_installed=cnc, demand_orders_per_day=5
    )
    if labour is None:  # CNC: S2+S3 replaced by operator load/unload (machine time overlaps other work)
        labour = sum(s.labour_min for s in al.STATIONS if s.key not in ("S2", "S3")) + cfg.t_cnc_load + cfg.t_cnc_unload
    rate = steady_rate(cfg, S.Controls(zones=[4]))
    analytic = cfg.shift_min / labour
    assert rate == pytest.approx(analytic, rel=0.04)


def test_littles_law_holds():
    cfg = S.SimConfig(days=60, operators=2, initial_kits=100000, demand_orders_per_day=5, seed=3)
    sim = S.LineSim(cfg)
    sim.run(S.Controls(zones=[4, 4]))
    k = sim.k
    T = sim.t
    L = sim.wip_area / T
    lam = k["stators_departed"] / T
    W = k["sojourn_sum"] / k["stators_departed"]
    assert L == pytest.approx(lam * W, rel=0.05)


def test_material_and_unit_conservation():
    cfg = S.SimConfig(days=20, operators=2, seed=5)
    sim = S.LineSim(cfg)
    sim.controls = S.Controls(zones=[4, 4])
    for day in range(cfg.days):
        sim.place_order(50 if day % 2 == 0 else 0)
        sim.run_until((day + 1) * 1440.0)
        assert all(v >= 0 for v in sim.stock.values()) and sim.rotors_in_system >= 0 and sim._in_process >= 0
        assert len(sim.oven["batch"]) <= cfg.oven_capacity
        assert sim.count_stators_physically() == sim.wip()  # ledger agrees with an independent walk
    k = sim.k
    assert (
        k["stators_entered"] == k["completed"] + k["scrap_stator"] + k["scrap_motor"] + sim.count_stators_physically()
    )
    assert k["shipped"] <= k["completed"]


def test_kv_scatter_matches_linearised_sigma(sens):
    rng = np.random.default_rng(0)
    kvs = np.array([sp.true_kv(sens, (24, 24, 24), sp.sample_tolerances(rng)) for _ in range(20000)])
    assert np.std(np.log(kvs)) == pytest.approx(sp.kv_sigma(sens), rel=0.03)


@pytest.mark.parametrize("turns", [(23, 24, 24), (25, 24, 24), (24, 24, 24), (22, 24, 24)])
def test_r_test_matches_exact_probability(sens, turns):
    spec = sp.TestSpec()
    rng = np.random.default_rng(1)
    zero = dict.fromkeys((*sp.TOL_KEYS, "br", "r"), 0.0)
    pairs = sp.true_r_pairs(sens, turns, zero)
    mc = np.mean([not sp.r_test(sens, pairs, spec, rng) for _ in range(30000)])
    assert mc == pytest.approx(sp.r_test_fail_probability(sens, pairs, spec), abs=0.01)


def test_single_turn_error_invisible_to_kv(sens):
    zero = dict.fromkeys((*sp.TOL_KEYS, "br", "r"), 0.0)
    assert sp.true_kv(sens, (23, 24, 24), zero) == pytest.approx(sens.kv0)  # strongest line pair unaffected


def test_balancing_model(sens):
    rng = np.random.default_rng(2)
    runs = [sp.balance(sens, sp.TestSpec(), rng) for _ in range(5000)]
    assert 1.5 < np.mean([r[0] for r in runs]) < 2.5 and all(r[1] <= 11.0 for r in runs if r[2])


def test_cnc_winding_escapes_fewer_defects():
    def escapes(cnc):
        out = []
        for seed in range(6):
            cfg = S.SimConfig(
                days=20, operators=2, initial_kits=100000, cnc_winder_installed=cnc, demand_orders_per_day=5, seed=seed
            )
            sim = S.LineSim(cfg)
            sim.run(S.Controls(zones=[4, 4]))
            out.append((sim.k["turn_errors_made"], sim.k["shipped_defective"], sim.k["shipped"]))
        return np.array(out).sum(axis=0)

    hand, cnc = escapes(False), escapes(True)
    assert hand[0] > 5 * max(cnc[0], 1) / 2  # many more turn errors made by hand
    assert hand[1] / hand[2] > cnc[1] / cnc[2]


def test_action_codec_round_trip():
    c = S.Controls(zones=[1, 4], oven_rule=3, order_qty=2, inspect=0, gauss_check=1, loaded_test=2, use_cnc=0,
                   preventive_maintenance=1, rotor_cap=3, stator_cap=0)  # fmt: skip
    assert decode(encode(c, 2), 2) == c


def test_env_api_and_reward_telescopes():
    from gymnasium.utils.env_checker import check_env

    check_env(FactoryEnv(S.SimConfig(days=2)), skip_render_check=True)
    env = FactoryEnv(S.SimConfig(days=5), reward_scale=1.0)
    obs, info = env.reset(seed=7)
    policy = HeuristicPolicy(env, "flex")
    total, done = 0.0, False
    start = info["profit"]
    while not done:
        obs, reward, terminated, truncated, info = env.step(policy(obs))
        total += reward
        done = terminated or truncated
    assert env.steps == 5 * 7
    assert total == pytest.approx(info["profit"] - start)  # profit at horizon includes salvage


def test_heuristic_beats_random():
    env = FactoryEnv(S.SimConfig(days=20))
    h = evaluate(env, HeuristicPolicy(env, "flex"), episodes=3)
    r = evaluate(env, random_policy(env), episodes=3)
    assert h["profit_with_salvage"] > r["profit_with_salvage"] + 5000
    assert h["fill_rate"] > r["fill_rate"]


def test_common_random_numbers_demand_is_policy_independent():
    def demand(controls):
        sim = S.LineSim(S.SimConfig(days=10, seed=11))
        sim.run(controls)
        return sim.k["demand"]

    assert demand(S.Controls(zones=[4, 4])) == demand(S.Controls(zones=[2, 3], oven_rule=0, use_cnc=0))
