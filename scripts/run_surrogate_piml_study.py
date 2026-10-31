"""Surrogate model and physics-informed ML study for the 22 in hover propeller.

Steps:
  1. Check the vectorized BEMT truth model against the locked MIL/GMT results.
  2. Learning curves: black-box vs physics-structured (rotor-coefficient) MLP surrogates.
  3. Surrogate-driven twist optimization at the thesis operating point, verified by BEMT,
     including a torque vs root-bending-moment Pareto front with the MIL/GMT designs marked.
  4. PINN trained only on the BEMT residual (no solver data), checked against the solver.

Usage:
  python scripts/run_surrogate_piml_study.py            # full study (~6 min on 4 CPU cores)
  python scripts/run_surrogate_piml_study.py --quick    # reduced sizes for a smoke run
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "src"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from scipy.optimize import minimize  # noqa: E402

from prandtl_prop import bemt_solver, config, pinn, surrogate  # noqa: E402
from scripts.helpers import paths  # noqa: E402
from scripts.helpers.thesis_plotting import apply_thesis_style, save_figure  # noqa: E402

TRAIN_SIZES = (25, 50, 100, 200, 400, 800, 1600)
ALPHA_BOUNDS_DEG = (0.0, 9.0)


def write_csv(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: f"{v:.8g}" if isinstance(v, float) else v for k, v in row.items()})
    return path


def solver_optimum(rotor, polar, base_twist, max_root_moment=None, starts=4, seed=0) -> np.ndarray | None:
    """Same problem as `surrogate.optimize_min_torque`, solved directly on BEMT (finite differences)."""

    cache: dict[bytes, dict[str, np.ndarray]] = {}

    def run(z: np.ndarray) -> dict[str, np.ndarray]:
        key = np.asarray(z, dtype=float).tobytes()
        if key not in cache:
            design = np.concatenate([[config.DESIGN_RPM], z, [1.0]])
            cache[key] = surrogate.evaluate_designs(design, rotor, polar, base_twist)
        return cache[key]

    constraints = [
        {"type": "eq", "fun": lambda z: run(z)["thrust_N"][0] - config.TARGET_THRUST_N},
        {"type": "ineq", "fun": lambda z: ALPHA_BOUNDS_DEG[1] - run(z)["max_alpha_deg"][0]},
        {"type": "ineq", "fun": lambda z: run(z)["min_alpha_deg"][0] - ALPHA_BOUNDS_DEG[0]},
    ]
    if max_root_moment is not None:
        constraints.append({"type": "ineq", "fun": lambda z: max_root_moment - run(z)["root_moment_Nm"][0]})
    bounds = list(
        zip(surrogate.DESIGN_LOWER[surrogate.TWIST_SLICE], surrogate.DESIGN_UPPER[surrogate.TWIST_SLICE], strict=False)
    )
    rng = np.random.default_rng(seed)
    best = None
    for _ in range(starts):
        result = minimize(
            lambda z: run(z)["torque_Nm"][0],
            rng.uniform(surrogate.DESIGN_LOWER[surrogate.TWIST_SLICE], surrogate.DESIGN_UPPER[surrogate.TWIST_SLICE]),
            bounds=bounds,
            constraints=constraints,
            method="SLSQP",
            options={"maxiter": 200, "ftol": 1e-12, "eps": 1e-6},
        )
        if result.success and (best is None or result.fun < best.fun):
            best = result
    if best is None:
        return None
    return np.concatenate([[config.DESIGN_RPM], best.x, [1.0]])


def _cap_label(cap: float) -> str:
    return "none" if np.isnan(cap) else f"{cap:.3f}"


def trimmed_reference(rotor, polar, case: str) -> dict[str, float]:
    twist = bemt_solver.locked_twist_deg(case)
    offset = bemt_solver.trim_collective_to_thrust(rotor, polar, twist, rotor.chord_m, config.DESIGN_RPM)
    result = bemt_solver.evaluate_rotor(rotor, polar, twist + offset, rotor.chord_m, np.array([config.DESIGN_RPM]))
    return {
        "case": case,
        "trim_offset_deg": offset,
        **{k: float(result[k][0]) for k in ("thrust_N", "torque_Nm", "root_moment_Nm", "figure_of_merit")},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true", help="small sizes for a fast smoke run")
    args = parser.parse_args()
    torch.set_num_threads(max(1, torch.get_num_threads()))
    apply_thesis_style()

    train_sizes = (25, 100) if args.quick else TRAIN_SIZES
    test_size = 200 if args.quick else 1000
    epochs = 500 if args.quick else 3000
    pinn_steps = 500 if args.quick else 8000
    members = 3

    polar = bemt_solver.fit_polar_from_locked_data()
    rotor = bemt_solver.load_rotor_stations(polar)
    base_twist = bemt_solver.locked_twist_deg("MIL")
    tables, figures = paths.OUTPUT_TABLES_DIR, paths.OUTPUT_FIGURES_DIR
    outputs: list[Path] = []

    # 1. Truth model check against the locked thesis results ---------------------------------
    validation = []
    for case, locked_t, locked_q in (
        ("MIL", config.LOCKED_MIL_THRUST_N, config.LOCKED_MIL_TORQUE_NM),
        ("GMT", config.LOCKED_GMT_THRUST_N, config.LOCKED_GMT_TORQUE_NM),
    ):
        ref = trimmed_reference(rotor, polar, case)
        validation.append(
            {
                **ref,
                "locked_thrust_N": locked_t,
                "locked_torque_Nm": locked_q,
                "torque_diff_pct": (ref["torque_Nm"] / locked_q - 1.0) * 100.0,
            }
        )
    refs = {row["case"]: row for row in validation}
    solver_reduction = (1.0 - refs["GMT"]["torque_Nm"] / refs["MIL"]["torque_Nm"]) * 100.0
    outputs.append(write_csv(tables / "piml_truth_model_validation.csv", validation))

    # 2. Data and learning curves -------------------------------------------------------------
    train_x = surrogate.sample_designs(max(train_sizes), seed=1)
    test_x = surrogate.sample_designs(test_size, seed=2)
    start = time.perf_counter()
    train_y = surrogate.target_matrix(surrogate.evaluate_designs(train_x, rotor, polar, base_twist))
    solver_seconds_per_design = (time.perf_counter() - start) / len(train_x)
    test_y = surrogate.target_matrix(surrogate.evaluate_designs(test_x, rotor, polar, base_twist))

    curve, models = [], {}
    for physics in (False, True):
        for size in train_sizes:
            model = surrogate.SurrogateEnsemble(physics=physics, members=members, epochs=epochs).fit(
                train_x[:size], train_y[:size]
            )
            predicted, _ = model.predict(test_x)
            models[(physics, size)] = model
            curve.append(
                {
                    "variant": "physics" if physics else "black_box",
                    "train_size": size,
                    "thrust_mape_pct": surrogate.mean_abs_pct_error(predicted[:, 0], test_y[:, 0]),
                    "torque_mape_pct": surrogate.mean_abs_pct_error(predicted[:, 1], test_y[:, 1]),
                    "root_moment_mape_pct": surrogate.mean_abs_pct_error(predicted[:, 2], test_y[:, 2]),
                    "max_alpha_mae_deg": float(np.mean(np.abs(predicted[:, 3] - test_y[:, 3]))),
                }
            )
            print(f"  surrogate {curve[-1]['variant']:9s} n={size:5d}  torque MAPE {curve[-1]['torque_mape_pct']:.3f}%")
    outputs.append(write_csv(tables / "piml_surrogate_learning_curve.csv", curve))

    best = models[(True, max(train_sizes))]
    predicted, spread = best.predict(test_x)
    start = time.perf_counter()
    best.predict(test_x)
    surrogate_seconds_per_design = (time.perf_counter() - start) / len(test_x)

    fig, ax = plt.subplots()
    for variant, style in (("black_box", "--o"), ("physics", "-s")):
        rows = [row for row in curve if row["variant"] == variant]
        ax.loglog(
            [r["train_size"] for r in rows],
            [r["torque_mape_pct"] for r in rows],
            style,
            color="black",
            label=f"{variant} torque",
        )
        ax.loglog(
            [r["train_size"] for r in rows],
            [r["thrust_mape_pct"] for r in rows],
            style,
            color="0.55",
            label=f"{variant} thrust",
        )
    ax.axhline(solver_reduction, color="0.3", linestyle=":", linewidth=1.2)
    ax.text(train_sizes[0], solver_reduction * 1.08, "GMT vs MIL torque difference", fontsize=9)
    ax.set_xlabel("BEMT training samples")
    ax.set_ylabel("Test mean abs. error [%]")
    ax.legend(fontsize=8)
    ax.grid(True, which="both", alpha=0.3)
    outputs += save_figure(fig, figures, "fig_piml_surrogate_learning_curve")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(8.5, 4.0))
    for ax, j, label in ((axes[0], 0, "Thrust [N]"), (axes[1], 1, "Torque [N m]")):
        ax.plot(test_y[:, j], predicted[:, j], ".", color="black", markersize=3)
        lims = [test_y[:, j].min(), test_y[:, j].max()]
        ax.plot(lims, lims, color="0.5", linewidth=1)
        ax.set_xlabel(f"BEMT {label}")
        ax.set_ylabel(f"Surrogate {label}")
        ax.grid(True, alpha=0.3)
    fig.tight_layout()
    outputs += save_figure(fig, figures, "fig_piml_surrogate_parity")
    plt.close(fig)

    # 3. Surrogate optimization, verified on BEMT ---------------------------------------------
    m_mil = refs["MIL"]["root_moment_Nm"]
    caps = [None] + list(np.linspace(0.99, 0.90, 4 if args.quick else 7) * m_mil)
    pareto = []
    for cap in caps:
        z_sur = surrogate.optimize_min_torque(best, alpha_bounds_deg=ALPHA_BOUNDS_DEG, max_root_moment_nm=cap)
        z_true = solver_optimum(rotor, polar, base_twist, max_root_moment=cap)
        if z_sur is None or z_true is None:
            print(f"  optimum cap={cap:.3f}  infeasible in this design space")
            continue
        sur_pred, sur_std = best.predict(z_sur)
        sur_check = surrogate.evaluate_designs(z_sur, rotor, polar, base_twist)
        true_opt = surrogate.evaluate_designs(z_true, rotor, polar, base_twist)
        pareto.append(
            {
                "root_moment_cap_Nm": float("nan") if cap is None else cap,
                **{
                    name: float(value)
                    for name, value in zip(
                        surrogate.DESIGN_NAMES[surrogate.TWIST_SLICE], z_sur[surrogate.TWIST_SLICE], strict=False
                    )
                },
                "surrogate_torque_Nm": sur_pred[0, 1],
                "surrogate_torque_std_Nm": sur_std[0, 1],
                "bemt_check_thrust_N": sur_check["thrust_N"][0],
                "bemt_check_torque_Nm": sur_check["torque_Nm"][0],
                "bemt_check_root_moment_Nm": sur_check["root_moment_Nm"][0],
                "bemt_optimum_torque_Nm": true_opt["torque_Nm"][0],
                "bemt_optimum_root_moment_Nm": true_opt["root_moment_Nm"][0],
                "optimality_gap_pct": (sur_check["torque_Nm"][0] / true_opt["torque_Nm"][0] - 1.0) * 100.0,
            }
        )
        print(
            f"  optimum cap={pareto[-1]['root_moment_cap_Nm']:.3f}  Q_sur={sur_pred[0, 1]:.5f}  Q_bemt={sur_check['torque_Nm'][0]:.5f}"
        )
    outputs.append(write_csv(tables / "piml_surrogate_pareto.csv", pareto))

    fig, ax = plt.subplots()
    ax.plot(
        [p["bemt_optimum_root_moment_Nm"] for p in pareto],
        [p["bemt_optimum_torque_Nm"] for p in pareto],
        "-",
        color="0.6",
        label="BEMT direct optimum",
    )
    ax.plot(
        [p["bemt_check_root_moment_Nm"] for p in pareto],
        [p["bemt_check_torque_Nm"] for p in pareto],
        "o",
        color="black",
        label="Surrogate optimum (BEMT-checked)",
    )
    for case, marker in (("MIL", "^"), ("GMT", "D")):
        ax.plot(
            refs[case]["root_moment_Nm"],
            refs[case]["torque_Nm"],
            marker,
            color="0.35",
            markersize=8,
            label=f"Locked {case} (trimmed)",
        )
    ax.set_xlabel("Blade root bending moment [N m]")
    ax.set_ylabel("Torque at 35 N, 4000 rpm [N m]")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    outputs += save_figure(fig, figures, "fig_piml_torque_moment_pareto")
    plt.close(fig)

    # 4. PINN: residual-only training ----------------------------------------------------------
    start = time.perf_counter()
    model = pinn.InflowPINN(rotor, polar, base_twist, steps=pinn_steps).train()
    pinn_train_seconds = time.perf_counter() - start
    pinn_result = model.evaluate(test_x)
    truth = surrogate.evaluate_designs(test_x, rotor, polar, base_twist)
    vi_err = np.abs(pinn_result["vi_m_s"] - truth["vi_m_s"]) / truth["vi_m_s"] * 100.0
    pinn_summary = {
        "train_steps": pinn_steps,
        "train_seconds": pinn_train_seconds,
        "solver_samples_used": 0,
        "final_loss": model.history[-1],
        "test_residual_rms": model.residual_rms(test_x),
        "vi_mean_abs_pct_error": float(vi_err.mean()),
        "vi_p99_abs_pct_error": float(np.percentile(vi_err, 99)),
        "thrust_mape_pct": surrogate.mean_abs_pct_error(pinn_result["thrust_N"], truth["thrust_N"]),
        "torque_mape_pct": surrogate.mean_abs_pct_error(pinn_result["torque_Nm"], truth["torque_Nm"]),
    }
    outputs.append(write_csv(tables / "piml_pinn_summary.csv", [pinn_summary]))
    print(f"  PINN torque MAPE {pinn_summary['torque_mape_pct']:.3f}% with zero solver samples")

    fig, ax = plt.subplots()
    examples = np.array([[config.DESIGN_RPM, 0, 0, 0, 0, 1.0], surrogate.DESIGN_LOWER, surrogate.DESIGN_UPPER])
    exact = surrogate.evaluate_designs(examples, rotor, polar, base_twist)["vi_m_s"]
    guess = model.predict_vi(examples)
    for k, (label, shade) in enumerate(
        (("MIL baseline", "black"), ("box lower corner", "0.4"), ("box upper corner", "0.65"))
    ):
        ax.plot(rotor.mu, exact[k], "-", color=shade, label=f"BEMT: {label}")
        ax.plot(rotor.mu, guess[k], "o", color=shade, markersize=3.5, markerfacecolor="none", label=f"PINN: {label}")
    ax.set_xlabel("r/R")
    ax.set_ylabel("Induced velocity [m/s]")
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)
    outputs += save_figure(fig, figures, "fig_piml_pinn_inflow")
    plt.close(fig)

    # Report ---------------------------------------------------------------------------------
    unconstrained = pareto[0]
    lines = [
        "# Surrogate and Physics-Informed ML Study",
        "",
        "Generated by `scripts/run_surrogate_piml_study.py`. Truth model: vectorized hover BEMT",
        "(`src/prandtl_prop/bemt_solver.py`) with an analytic polar fitted to the locked XFOIL station data",
        f"(Cl RMS fit error {polar.cl_rms_error:.4f}, Cd RMS fit error {polar.cd_rms_error:.5f}).",
        "",
        "## 1. Truth-model check (both cases trimmed to 35 N at 4000 rpm)",
        "",
        "| Case | Trim offset [deg] | Torque [N m] | Locked torque [N m] | Diff [%] | Root moment [N m] |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        *[
            f"| {r['case']} | {r['trim_offset_deg']:.3f} | {r['torque_Nm']:.5f} | {r['locked_torque_Nm']:.5f} | {r['torque_diff_pct']:.2f} | {r['root_moment_Nm']:.4f} |"
            for r in validation
        ],
        "",
        f"GMT torque reduction: {solver_reduction:.2f}% (truth model) vs {config.LOCKED_TORQUE_REDUCTION_PCT:.2f}% (locked thesis).",
        "",
        f"## 2. Surrogate learning curve (test set of {test_size} Latin-hypercube designs)",
        "",
        "| Variant | Train samples | Thrust MAPE [%] | Torque MAPE [%] | Root moment MAPE [%] | Max alpha MAE [deg] |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        *[
            f"| {r['variant']} | {r['train_size']} | {r['thrust_mape_pct']:.3f} | {r['torque_mape_pct']:.3f} | {r['root_moment_mape_pct']:.3f} | {r['max_alpha_mae_deg']:.3f} |"
            for r in curve
        ],
        "",
        f"BEMT cost: {solver_seconds_per_design * 1e6:.0f} us/design (batched). Surrogate cost: {surrogate_seconds_per_design * 1e6:.1f} us/design.",
        "This BEMT is already cheap, so the point here is the method, checked against an exact truth model;",
        "the speedup matters when the truth model is CFD or a coupled aero-structural solve.",
        "",
        "## 3. Surrogate optimization at 4000 rpm, fixed chord, T = 35 N, alpha in [{:.1f}, {:.1f}] deg".format(
            *ALPHA_BOUNDS_DEG
        ),
        "",
        "| Root moment cap [N m] | Surrogate Q [N m] | BEMT check T [N] | BEMT check Q [N m] | BEMT direct optimum Q [N m] | Gap [%] |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
        *[
            f"| {_cap_label(p['root_moment_cap_Nm'])} | {p['surrogate_torque_Nm']:.5f} | {p['bemt_check_thrust_N']:.3f} | {p['bemt_check_torque_Nm']:.5f} | {p['bemt_optimum_torque_Nm']:.5f} | {p['optimality_gap_pct']:.3f} |"
            for p in pareto
        ],
        "",
        f"Unconstrained optimum vs trimmed MIL torque: {(1 - unconstrained['bemt_check_torque_Nm'] / refs['MIL']['torque_Nm']) * 100:.2f}% lower.",
        "The design space is the GMT blend plus three smooth twist modes around the MIL blade at fixed chord, so these results",
        "describe that subspace only.",
        "",
        "## 4. PINN (trained on the BEMT residual only, zero solver samples)",
        "",
        *[f"- {k}: {v:.6g}" if isinstance(v, float) else f"- {k}: {v}" for k, v in pinn_summary.items()],
        "",
        "## Assumptions and limits",
        "",
        f"- Polar model is a fit to XFOIL values recorded at the locked operating points (alpha {polar.alpha_fit_min_deg:.1f}-{polar.alpha_fit_max_deg:.1f} deg,",
        f"  Re {polar.re_fit_min:.0f}-{polar.re_fit_max:.0f}). Outside that range the soft stall (Cl_max = {polar.cl_max:.2f}) is an assumption, not XFOIL data.",
        "- Hover only, no swirl, Prandtl tip loss with 0.05 floor, same as the locked solver.",
        "- All results are model-to-model; none of this is experimental or CFD validation.",
        "",
    ]
    report = paths.OUTPUT_REPORTS_DIR / "surrogate_piml_report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    outputs.append(report)

    print("Surrogate/PIML study complete")
    for output in outputs:
        print(f"  {paths.relative(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
