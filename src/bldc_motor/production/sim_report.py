"""Compare factory policies and write a report (KPI table, episode trace plot)."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from bldc_motor.production import sim as S
from bldc_motor.production.sim_env import (
    FactoryEnv,
    HeuristicPolicy,
    evaluate,
    ppo_policy,
    random_policy,
    residual_policy,
)

KPI_COLUMNS = (
    ("profit_with_salvage", "Profit incl. salvage ($)", "{:,.0f}"),
    ("profit_std", "± std ($)", "{:,.0f}"),
    ("shipped", "Shipped", "{:.0f}"),
    ("fill_rate", "Fill rate", "{:.2f}"),
    ("late_units_days", "Late unit-days", "{:.0f}"),
    ("throughput_per_day", "Output/day", "{:.1f}"),
    ("avg_wip", "Avg WIP", "{:.1f}"),
    ("avg_flow_time_h", "Flow time (h)", "{:.1f}"),
    ("s5_first_pass_yield", "S5 FPY", "{:.3f}"),
    ("eol_yield", "EOL yield", "{:.3f}"),
    ("escape_rate", "Escape rate", "{:.4f}"),
    ("operator_utilisation", "Operator util.", "{:.2f}"),
    ("winder_failures", "Winder failures", "{:.1f}"),
)


def trace_episode(env: FactoryEnv, policy, seed: int = 1000) -> dict[str, list[float]]:
    obs, _ = env.reset(seed=seed)
    rows: dict[str, list[float]] = {
        k: [] for k in ("day", "wip", "shipped", "profit", "backlog", "stock", "winder_down", "oven_busy")
    }
    done = False
    while not done:
        obs, _, terminated, truncated, info = env.step(policy(obs))
        sim = env.sim
        rows["day"].append(sim.t / 1440.0)
        rows["wip"].append(sim.wip())
        rows["shipped"].append(sim.k["shipped"])
        rows["profit"].append(info["profit"])
        rows["backlog"].append(sum(q for _, q in sim.orders))
        rows["stock"].append(min(sim.stock.values()))
        rows["winder_down"].append(float(sim.winder["state"] in ("broken", "maintenance")))
        rows["oven_busy"].append(float(sim.oven["state"] in ("loading", "curing")))
        done = terminated or truncated
    return rows


def plot_trace(traces: dict[str, dict], path: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(11, 6.5), sharex=True)
    panels = (
        ("shipped", "Cumulative motors shipped"),
        ("profit", "Profit ($)"),
        ("wip", "WIP (stator-bearing units)"),
        ("backlog", "Order backlog (motors)"),
    )
    for ax, (key, title) in zip(axes.flat, panels, strict=True):
        for name, tr in traces.items():
            ax.plot(tr["day"], tr[key], label=name, lw=1.4)
        ax.set_title(title, fontsize=10)
        ax.grid(alpha=0.3)
    for ax in axes[1]:
        ax.set_xlabel("day")
    axes[0, 0].legend(fontsize=8)
    fig.suptitle("RL2407 factory simulation: one 20-day episode (same random seed)")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def compare(policies: dict, env: FactoryEnv, episodes: int = 10) -> dict[str, dict]:
    return {name: evaluate(env, pol, episodes=episodes) for name, pol in policies.items()}


def default_policies(env: FactoryEnv, ppo_npz: str | None = None, residual_npz: str | None = None) -> dict:
    from bldc_motor.design.ppo import GaussianPolicy

    policies = {
        "heuristic (flex staffing)": HeuristicPolicy(env, "flex"),
        "heuristic (zoned staffing)": HeuristicPolicy(env, "zoned"),
        "random": random_policy(env),
    }
    if ppo_npz and Path(ppo_npz).exists():
        policies["PPO from scratch"] = ppo_policy(GaussianPolicy.load(ppo_npz))
    if residual_npz and Path(residual_npz).exists():
        policies["residual PPO (on flex heuristic)"] = residual_policy(env, GaussianPolicy.load(residual_npz))
    return policies


def write_report(results: dict[str, dict], out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "policy_comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        w = csv.writer(handle)
        w.writerow(["policy", *[c[0] for c in KPI_COLUMNS]])
        for name, k in results.items():
            w.writerow([name, *[f"{k.get(c[0], np.nan):.6g}" for c in KPI_COLUMNS]])
    lines = ["| Policy | " + " | ".join(c[1] for c in KPI_COLUMNS) + " |", "|---" * (len(KPI_COLUMNS) + 1) + "|"]
    for name, k in results.items():
        lines.append(f"| {name} | " + " | ".join(c[2].format(k.get(c[0], float("nan"))) for c in KPI_COLUMNS) + " |")
    table = "\n".join(lines)
    (out / "policy_comparison.md").write_text(table + "\n", encoding="utf-8")
    return out / "policy_comparison.md"


def run(
    out_dir: str | Path, episodes: int = 10, days: int = 20, ppo_npz: str | None = None, residual_npz: str | None = None
) -> dict:
    out = Path(out_dir)
    env = FactoryEnv(S.SimConfig(days=days))
    policies = default_policies(env, ppo_npz, residual_npz)
    results = compare(policies, env, episodes)
    traces = {name: trace_episode(env, pol) for name, pol in policies.items() if name != "random"}
    out.mkdir(parents=True, exist_ok=True)
    png = plot_trace(traces, out / "episode_trace.png")
    report = write_report(results, out)
    return {"results": results, "report": report, "trace": png}
