"""End-to-end hardware design pipeline.

    mission spec
      -> 1. train the RL design agent (PPO on MotorDesignEnv)
      -> 2. exploit: deterministic policy rollouts from many starts
      -> 3. baselines on the same evaluation budget (differential evolution, random search)
      -> 4. candidates = top designs of every method -> DFM-grid local refinement
      -> 5. production-tolerance Monte Carlo -> yield; select the best robust design
      -> 6. export: lamination DXF, cross-section SVG, winding table, BOM, spec sheet,
            library entry, predicted bench (acceptance) curve, convergence plots
      -> 7. (after building) calibrate the model with prototype measurements, re-run

The winner is whichever method's design is best after refinement and robustness screening;
the report states which method found it.
"""

from __future__ import annotations

import csv
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from bldc_motor import synthetic
from bldc_motor.design import baselines, export, geometry, ppo, robustness
from bldc_motor.design import mission as mission_mod
from bldc_motor.design.env import DesignArchive, MotorDesignEnv
from bldc_motor.design.model import Calibration
from bldc_motor.library import save_components
from bldc_motor.powertrain import Powertrain


@dataclass
class PipelineConfig:
    mission: str = "5in_freestyle_6s"
    rl_steps: int = 150_000
    exploit_rollouts: int = 64
    run_baselines: bool = True
    top_k_per_method: int = 4
    yield_samples: int = 200
    min_yield: float = 0.9
    step_scale: float = 0.08
    seed: int = 0
    out_dir: str = "outputs/motor/design"
    calibration_path: str | None = None
    design_name: str | None = None


@dataclass
class Candidate:
    method: str
    design: dict
    raw_score: float
    refined_score: float
    refine_changes: list = field(default_factory=list)
    yield_report: robustness.YieldReport | None = None
    evaluation: mission_mod.Evaluation | None = None


@dataclass
class PipelineResult:
    config: PipelineConfig
    mission: mission_mod.MissionSpec
    winner: Candidate | None
    candidates: list
    method_summary: dict
    outputs: dict
    timings: dict


def run_pipeline(config: PipelineConfig, log=print) -> PipelineResult:
    out = Path(config.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    calibration = Calibration.load(config.calibration_path)
    mission = mission_mod.preset_missions()[config.mission]
    timings: dict[str, float] = {}
    archives: dict[str, DesignArchive] = {}

    # 1-2. RL design agent ------------------------------------------------------------------
    t0 = time.time()
    rl_archive = DesignArchive(capacity=50)
    env = MotorDesignEnv(mission=mission, step_scale=config.step_scale, calibration=calibration, archive=rl_archive)
    log(f"[1/6] training PPO design agent for {config.rl_steps} steps on mission '{mission.name}'")
    result = ppo.train_ppo(
        env,
        ppo.PPOConfig(total_steps=config.rl_steps, seed=config.seed),
        callback=lambda e: (
            log(f"      steps {e['steps']:>7}  mean final score (last 50 episodes) {e['mean_final_score_last50']:+.3f}")
            if e["steps"] % (8 * 2048) < 2048
            else None
        ),
    )
    training_evals = rl_archive.evaluations
    result.policy.save(out / "design_policy.npz")
    _write_log(result.log, out / "training_log.csv")
    log(f"[2/6] exploiting the trained policy with {config.exploit_rollouts} deterministic rollouts")
    exploit_scores = []
    for i in range(config.exploit_rollouts):
        options = {"design": geometry.reference_design()} if i == 0 else None
        final, _ = ppo.rollout(env, result.policy, deterministic=True, seed=config.seed + 10_000 + i, options=options)
        exploit_scores.append(final["score"])
    archives["rl_ppo"] = rl_archive
    timings["rl_s"] = time.time() - t0
    budget = rl_archive.evaluations

    # 3. Baselines on the same evaluation budget ---------------------------------------------
    if config.run_baselines:
        t0 = time.time()
        log(f"[3/6] baselines with the same budget ({budget} evaluations): differential evolution, random search")
        archives["differential_evolution"] = baselines.differential_evolution_search(
            mission, budget, config.seed, calibration=calibration
        )
        archives["random_search"] = baselines.random_search(mission, budget, config.seed, calibration=calibration)
        timings["baselines_s"] = time.time() - t0

    # 4-5. Refine + robustness ---------------------------------------------------------------
    t0 = time.time()
    log("[4/6] DFM-grid refinement of the top candidates of every method")
    candidates: list[Candidate] = []
    for method, archive in archives.items():
        for score, _feasible, design in archive.entries[: config.top_k_per_method]:
            refined, changes = baselines.local_refine(design, mission, calibration)
            ev = mission_mod.evaluate(refined, mission, calibration)
            candidates.append(Candidate(method, refined, score, ev.score, changes, evaluation=ev))
    log(f"[5/6] tolerance Monte Carlo ({config.yield_samples} as-built samples per candidate)")
    for cand in candidates:
        if cand.evaluation.feasible:
            cand.yield_report = robustness.tolerance_yield(
                cand.design, mission, config.yield_samples, config.seed, calibration
            )
    timings["refine_robust_s"] = time.time() - t0
    winner = _select(candidates, config.min_yield)

    method_summary = {}
    for method, archive in archives.items():
        best = max((c for c in candidates if c.method == method), key=lambda c: c.refined_score, default=None)
        method_summary[method] = {
            "evaluations": archive.evaluations,
            "best_raw_score": archive.entries[0][0] if archive.entries else math.nan,
            "best_raw_feasible": bool(archive.entries[0][1]) if archive.entries else False,
            "best_refined_score": best.refined_score if best else math.nan,
            "best_feasible_yield": max(
                (c.yield_report.yield_fraction for c in candidates if c.method == method and c.yield_report),
                default=0.0,
            ),
            "first_feasible_at": next((i + 1 for i, v in enumerate(archive.history) if not math.isnan(v)), None),
        }
    method_summary["rl_ppo"]["training_evaluations"] = training_evals
    method_summary["rl_ppo"]["exploit_mean_final_score"] = float(np.mean(exploit_scores))
    method_summary["rl_ppo"]["final_training_mean_score"] = result.log[-1]["mean_final_score_last50"]

    # 6. Export ------------------------------------------------------------------------------
    outputs = {}
    if winner is not None:
        log(f"[6/6] exporting hardware package for the winner (found by {winner.method})")
        outputs = export_package(winner, mission, calibration, out, config, method_summary)
    _plot_convergence(archives, result, out / "convergence.png")
    outputs["convergence"] = str(out / "convergence.png")
    return PipelineResult(config, mission, winner, candidates, method_summary, outputs, timings)


def _select(candidates: list[Candidate], min_yield: float) -> Candidate | None:
    robust = [c for c in candidates if c.yield_report and c.yield_report.yield_fraction >= min_yield]
    if robust:
        return max(robust, key=lambda c: c.yield_report.score_mean)
    with_yield = [c for c in candidates if c.yield_report]
    if with_yield:
        return max(with_yield, key=lambda c: (c.yield_report.yield_fraction, c.yield_report.score_mean))
    return max(candidates, key=lambda c: c.refined_score, default=None)


def export_package(
    winner: Candidate, mission, calibration: Calibration, out: Path, config: PipelineConfig, method_summary: dict
) -> dict:
    ev = winner.evaluation
    analysis = ev.analysis
    name = config.design_name or f"rl_design_{mission.name}"
    motor = analysis.motor.with_updates(name=name)
    files = {
        "lamination_dxf": export.write_dxf(analysis, out / "lamination.dxf"),
        "cross_section_svg": export.write_svg(analysis, out / "cross_section.svg"),
        "winding_csv": export.write_winding_table(analysis, out / "winding.csv"),
        "bom_csv": export.write_bom(analysis, ev.cost, out / "bom.csv"),
        "library_json": save_components(out / "library.json", motors=[motor]),
    }
    # Predicted bench acceptance curve on a bench supply at the pack's full-charge voltage.
    supply = synthetic.bench_supply(mission.battery.ocv(1.0) * 0.95, 0.01)
    columns = synthetic.generate_stand_log(
        Powertrain(motor, mission.propeller, supply, mission.esc, n_motors=1), noise={}, seed=config.seed
    )
    files["predicted_stand_curve"] = synthetic.write_stand_csv(
        columns,
        out / "predicted_stand_curve.csv",
        description=f"PREDICTED acceptance curve for {name} on {mission.propeller.name}",
    )
    payload = {
        "name": name,
        "mission": mission.name,
        "found_by": winner.method,
        "design": export.design_to_json(winner.design),
        "score": ev.score,
        "feasible": ev.feasible,
        "metrics": ev.metrics,
        "penalties": ev.penalties,
        "cost_usd": ev.cost,
        "dfm": [
            {"rule": r.rule, "value": r.value, "limit": r.limit, "penalty": r.penalty, "status": r.status}
            for r in ev.rules
        ],
        "yield": asdict(winner.yield_report) if winner.yield_report else None,
        "internals": analysis.internals,
        "calibration": asdict(calibration),
        "method_summary": method_summary,
    }
    files["design_json"] = export.write_design_json(payload, out / "design.json")
    files["spec_sheet"] = _write_spec_sheet(payload, analysis, winner, out / "spec_sheet.md")
    return {k: str(v) for k, v in files.items()}


def _write_spec_sheet(payload: dict, analysis, winner: Candidate, path: Path) -> Path:
    m = payload["metrics"]
    d = winner.design
    i = analysis.internals
    y = winner.yield_report
    slots, poles = d["slot_pole"]
    kv = m["kv_rpm_per_v"]
    lines = [
        f"# {payload['name']}: motor design spec sheet",
        "",
        f"Mission `{payload['mission']}`; design found by **{payload['found_by']}**, refined on the manufacturable grid "
        f"and screened for tolerance yield. Model: first-order analytical (`bldc_motor.design.model`), "
        f"calibration: {payload['calibration']['source']}. **Predicted values: verify on the bench before production.**",
        "",
        "## Design",
        "",
        "| Parameter | Value |",
        "| --- | --- |",
    ]
    for p in geometry.PARAMETERS:
        value = d[p.name]
        text = (
            f"{value[0]}N{value[1]}P"
            if p.name == "slot_pole"
            else (f"{value:.3g} {p.unit}".strip() if isinstance(value, float) else f"{value} {p.unit}".strip())
        )
        lines.append(f"| {p.description or p.name} | {text} |")
    lines += [
        "",
        f"Winding ({slots}N{poles}P, k_w = {i['winding_factor']:.3f}): `{analysis.layout.notation()}` (upper case = forward); see `winding.csv`.",
        "",
        "## Predicted electrical / magnetic",
        "",
        "| Quantity | Value |",
        "| --- | --- |",
        f"| Kv | {kv:.0f} rpm/V |",
        f"| R line-to-line (25 C) | {m['r_ll_mohm']:.1f} mOhm |",
        f"| Km | {m['km_nm_per_sqrt_w'] * 1000:.2f} mN·m/√W |",
        f"| Airgap / tooth / yoke / bell flux density | {i['bg_t']:.2f} / {i['b_tooth_t']:.2f} / {i['b_yoke_t']:.2f} / {i['b_bell_t']:.2f} T |",
        f"| Slot fill (bare copper) | {i['fill_bare']:.2f} |",
        f"| Demagnetisation current limit (at {analysis.motor.max_magnet_temp_c:.0f} C) | {i['demag_current_a']:.0f} A |",
        f"| Mass | {m['mass_g']:.1f} g |",
        "",
        "## Predicted in-aircraft performance",
        "",
        "| Quantity | Value |",
        "| --- | --- |",
        f"| All-up weight | {m['auw_g']:.0f} g |",
        f"| Hover throttle / efficiency | {m['hover_throttle']:.2f} / {m['hover_g_per_w']:.2f} g/W (motor η {m['hover_motor_eff']:.3f}) |",
        f"| Thrust-to-weight | {m['thrust_to_weight']:.1f} |",
        f"| Punch-out motor current | {m['punch_i_motor_a']:.1f} A |",
        f"| Winding / magnet temperature after burst | {m['burst_winding_c']:.0f} / {m['burst_magnet_c']:.0f} C |",
        "",
        "## Manufacturability",
        "",
        f"Score {m['manufacturability']:.2f} (1 = no concerns); unit cost {m['unit_cost_usd']:.2f} USD (illustrative cost model).",
        "",
        "| Rule | Value | Limit | Status |",
        "| --- | --- | --- | --- |",
    ]
    for r in payload["dfm"]:
        lines.append(f"| {r['rule']} | {r['value']:.3g} | {r['limit']} | {r['status']} |")
    if y:
        lines += [
            "",
            f"Tolerance Monte Carlo ({y.samples} as-built samples): **yield {y.yield_fraction:.0%}**, "
            f"Kv {y.kv_mean:.0f} rpm/V ± {y.kv_cv:.1%} (1σ), burst winding P90 {y.burst_winding_p90_c:.0f} C.",
        ]
        if y.failure_modes:
            lines.append("Failure modes: " + ", ".join(f"{k} ({v})" for k, v in y.failure_modes.items()) + ".")
    kv_tol = max(3.0 * (y.kv_cv if y else 0.02), 0.03)
    lines += [
        "",
        "## Bench acceptance test (prototype)",
        "",
        f"1. R line-to-line, 4-wire, corrected to 25 C: {m['r_ll_mohm']:.1f} mOhm ± 6 %.",
        f"2. No-load Kv at 100 % throttle: {kv:.0f} rpm/V ± {kv_tol:.0%} (3σ of tolerance Monte Carlo, min ±3 %).",
        "3. Loaded sweep on the mission prop; compare to `predicted_stand_curve.csv` (model prediction, no noise).",
        "4. Fit with `bldc-motor fit ... --r-ll-mohm <measured>`; feed the fitted motor to "
        "`bldc-motor design calibrate` to correct the model, then re-run the pipeline.",
        "",
        "## Method comparison (same evaluation budget)",
        "",
        "| Method | Evaluations | Best raw score | Best refined score | Best yield | First feasible at eval |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for method, s in payload["method_summary"].items():
        lines.append(
            f"| {method} | {s['evaluations']} | {s['best_raw_score']:+.3f} | {s['best_refined_score']:+.3f} | "
            f"{s['best_feasible_yield']:.0%} | {s['first_feasible_at']} |"
        )
    lines += [
        "",
        "Files: `lamination.dxf`, `cross_section.svg`, `winding.csv`, `bom.csv`, `library.json`, `design.json`, "
        "`predicted_stand_curve.csv`, `design_policy.npz`, `training_log.csv`, `convergence.png`.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_log(log: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(log[0]))
        writer.writeheader()
        writer.writerows(log)


def _plot_convergence(archives: dict[str, DesignArchive], result: ppo.PPOResult, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 4.2))
    for method, archive in archives.items():
        hist = np.array(archive.history, float)
        ax1.plot(np.arange(1, hist.size + 1), hist, label=method)
    ax1.set_xscale("log")
    ax1.set_xlabel("Design evaluations")
    ax1.set_ylabel("Best feasible score so far")
    ax1.set_title("Optimiser convergence (same objective)")
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8)
    steps = [e["steps"] for e in result.log]
    ax2.plot(steps, [e["mean_final_score_last50"] for e in result.log], "C0-")
    ax2.set_xlabel("Environment steps")
    ax2.set_ylabel("Mean final-design score (last 50 episodes)")
    ax2.set_title("PPO training: design quality reached per episode")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
