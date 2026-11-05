"""``bldc-motor design ...`` subcommands (heavy imports are deferred to the handlers)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path


def _load_design(path: str) -> dict:
    from bldc_motor.design import export

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return export.design_from_json(data.get("design", data))


def cmd_missions(args) -> int:
    from bldc_motor.design import mission

    for name, m in mission.preset_missions().items():
        print(
            f"{name:20s} prop={m.propeller.name} battery={m.battery.name} dry={m.dry_mass_g:g} g "
            f"T/W>={m.min_thrust_to_weight:g} burst={m.burst_s:g}s ESC<={m.esc_max_current_a} A OD<={m.max_stator_od_mm} mm"
        )
    return 0


def cmd_run(args) -> int:
    from bldc_motor.design.pipeline import PipelineConfig, run_pipeline

    config = PipelineConfig(
        mission=args.mission,
        rl_steps=args.rl_steps,
        exploit_rollouts=args.rollouts,
        run_baselines=not args.no_baselines,
        yield_samples=args.yield_samples,
        min_yield=args.min_yield,
        seed=args.seed,
        out_dir=args.out,
        calibration_path=args.calibration,
        design_name=args.name,
    )
    result = run_pipeline(config)
    print("\nMethod comparison (same evaluation budget):")
    for method, s in result.method_summary.items():
        print(
            f"  {method:24s} evals={s['evaluations']:>7}  best raw={s['best_raw_score']:+.3f}  "
            f"refined={s['best_refined_score']:+.3f}  yield={s['best_feasible_yield']:.0%}  first feasible @ {s['first_feasible_at']}"
        )
    if result.winner is None:
        print("No candidate found.")
        return 1
    w = result.winner
    m = w.evaluation.metrics
    print(f"\nWinner (found by {w.method}): score {w.evaluation.score:+.3f}, feasible={w.evaluation.feasible}")
    if w.yield_report:
        print(
            f"  yield {w.yield_report.yield_fraction:.0%}, Kv {w.yield_report.kv_mean:.0f} ± {w.yield_report.kv_cv:.1%}"
        )
    if "kv_rpm_per_v" in m:
        print(
            f"  Kv {m['kv_rpm_per_v']:.0f}  R {m['r_ll_mohm']:.1f} mOhm  mass {m['mass_g']:.1f} g  hover {m['hover_g_per_w']:.2f} g/W  "
            f"T/W {m['thrust_to_weight']:.1f}  burst {m['burst_winding_c']:.0f} C  mfg {m['manufacturability']:.2f}  cost ${m['unit_cost_usd']:.2f}"
        )
    for key, path in result.outputs.items():
        print(f"  {key}: {path}")
    return 0


def cmd_evaluate(args) -> int:
    from bldc_motor.design import mission, model, robustness

    design = _load_design(args.design)
    spec = mission.preset_missions()[args.mission]
    calibration = model.Calibration.load(args.calibration)
    ev = mission.evaluate(design, spec, calibration)
    print(
        json.dumps(
            {"score": ev.score, "feasible": ev.feasible, "metrics": ev.metrics, "penalties": ev.penalties},
            indent=2,
            default=float,
        )
    )
    for rule in ev.rules:
        if rule.status != "OK":
            print(f"  DFM {rule.status}: {rule.rule} = {rule.value:.3g} ({rule.limit})")
    if args.yield_samples:
        report = robustness.tolerance_yield(design, spec, args.yield_samples, calibration=calibration)
        print(json.dumps(asdict(report), indent=2, default=float))
    return 0


def cmd_calibrate(args) -> int:
    from bldc_motor import units
    from bldc_motor.design import calibration as cal_mod
    from bldc_motor.design import model
    from bldc_motor.library import load_library

    design = _load_design(args.design)
    measured = load_library(args.measured, include_bundled=False).motor(args.motor)
    omega = units.rpm_to_rad_s(args.g_sa_rpm) if args.g_sa_rpm else None
    ratios = cal_mod.prototype_ratios(design, measured, args.g_sa_w_per_k, omega)
    current = model.Calibration.load(args.calibration)
    updated = cal_mod.update_calibration(current, ratios)
    updated.save(args.calibration)
    print("prototype / model ratios: " + ", ".join(f"{k}={v:.3f}" for k, v in ratios.items()))
    print(f"calibration ({updated.samples} samples) written to {args.calibration}:")
    for key in ("kv_factor", "resistance_factor", "iron_loss_factor", "friction_factor", "convection_factor"):
        print(f"  {key} = {getattr(updated, key):.3f}")
    print("re-run `bldc-motor design run --calibration ...` to optimise with the corrected model")
    return 0


def cmd_benchmark(args) -> int:
    import json as _json

    from bldc_motor.design.benchmark import amortized_benchmark

    result = amortized_benchmark(args.train_steps, args.missions, args.rollouts, seed=args.seed)
    print(f"\nPer-mission budget: {result.budget} design evaluations")
    print(_json.dumps(result.summary(), indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            _json.dumps(
                {"budget": result.budget, "rows": result.rows, "summary": result.summary()}, indent=2, default=float
            )
            + "\n"
        )
        print(f"wrote {args.out}")
    return 0


def cmd_package(args) -> int:
    from bldc_motor.design.manufacturing_package import build_package

    result = build_package(args.design, args.out, args.name, args.mission)
    audit = result["audit"]
    print(f"mechanical checks: {'all OK' if result['checks_ok'] else 'FAILURES - see mechanical_checks.md'}")
    print(
        f"mass: CAD {audit['cad_total_g']:.1f} g (+{audit['leads_g']:.1f} g leads) vs analytical model {audit['model_total_g']:.1f} g"
    )
    for label, r in result["rescored"].items():
        print(
            f"  {label:22s} AUW {r['auw_g']:.0f} g  hover {r['hover_g_per_w']:.2f} g/W  T/W {r['thrust_to_weight']:.2f}  feasible={r['feasible']}"
        )
    print(f"wrote {len(result['files'])} files to {args.out}")
    return 0 if result["checks_ok"] else 1


def cmd_production(args) -> int:
    from bldc_motor.production import assembly_line, report

    files = report.write_all(args.out)
    p = assembly_line.plan(args.volume)
    print(
        f"capital {p.capital_total:,.0f} USD (equipment {p.equipment_total:,.0f} + contingency {p.contingency:,.0f}); within budget: {p.within_budget}"
    )
    print(
        f"labour {p.labour_min_per_motor:.1f} min/motor, bottleneck {p.bottleneck}; capacity/yr (1-3 operators) {p.capacity_per_year_single_shift}"
    )
    for label, u in p.unit_cost.items():
        print(f"  unit cost {label}: {u['total_usd']:.2f} USD")
    for key, path in files.items():
        print(f"  {key}: {path}")
    return 0 if p.within_budget else 1


def cmd_winder(args) -> int:
    from bldc_motor.production.winder_package import build_winder_package, write_readme

    result = build_winder_package(args.design, args.out, args.name)
    readme = write_readme(result, args.out, args.name)
    t = result["timing"]
    print(
        f"turns per tooth {result['verification']['turns_per_tooth']} -> {'MATCH' if result['turns_ok'] else 'MISMATCH'}"
    )
    print(
        f"collisions: {result['verification']['collision_count']}; clearance checks: {'all OK' if all(c[3] for c in result['checks']) else 'FAILURES'}"
    )
    print(
        f"cycle: machine {t['machine_min']:.2f} min + operator {t['operator_min']:.1f} min = {t['station_min']:.2f} min/stator; wire {result['wire_m']:.2f} m"
    )
    print(f"hardware BOM ~${result['bom_total']:,.0f}; wrote {readme} and {len(result['files'])} files")
    ok = result["turns_ok"] and result["verification"]["collision_count"] == 0 and all(c[3] for c in result["checks"])
    return 0 if ok else 1


def add_design_commands(sub) -> None:
    design = sub.add_parser("design", help="RL-driven motor design pipeline (geometry -> hardware package)")
    dsub = design.add_subparsers(dest="design_command", required=True)

    p = dsub.add_parser("missions", help="list preset missions")
    p.set_defaults(func=cmd_missions)

    p = dsub.add_parser("run", help="train the design agent, compare optimisers, export the hardware package")
    p.add_argument("--mission", default="5in_freestyle_6s")
    p.add_argument("--rl-steps", type=int, default=150_000)
    p.add_argument("--rollouts", type=int, default=64, help="deterministic policy rollouts after training")
    p.add_argument("--no-baselines", action="store_true")
    p.add_argument("--yield-samples", type=int, default=200)
    p.add_argument("--min-yield", type=float, default=0.9)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--calibration", help="calibration JSON from `design calibrate`")
    p.add_argument("--name", help="name of the designed motor")
    p.add_argument("--out", default="outputs/motor/design")
    p.set_defaults(func=cmd_run)

    p = dsub.add_parser("benchmark", help="amortised benchmark: mission-conditioned policy vs per-mission optimisers")
    p.add_argument("--train-steps", type=int, default=150_000)
    p.add_argument("--missions", type=int, default=6, help="held-out missions")
    p.add_argument("--rollouts", type=int, default=8, help="policy rollouts per held-out mission (sets the budget)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default="outputs/motor/design/benchmark.json")
    p.set_defaults(func=cmd_benchmark)

    p = dsub.add_parser("package", help="manufacturing data package: STEP, DXF, drawing set (SVG/PDF), mass audit")
    p.add_argument("--design", default="data/motor_designs/rl_5in_freestyle_6s_v1/design.json")
    p.add_argument("--name", default="RL2407")
    p.add_argument("--mission", help="mission for re-scoring at CAD mass (default: from design.json)")
    p.add_argument("--out", default="docs/manufacturing/rl2407")
    p.set_defaults(func=cmd_package)

    p = dsub.add_parser("production", help="assembly line plan, suppliers, equipment budget, layout")
    p.add_argument("--volume", type=float, default=5000.0)
    p.add_argument("--out", default="docs/manufacturing")
    p.set_defaults(func=cmd_production)

    p = dsub.add_parser("winder", help="from-scratch CNC needle winder: toolpath, G-code, verification, CAD, BOM")
    p.add_argument("--design", default="data/motor_designs/rl_5in_freestyle_6s_v1/design.json")
    p.add_argument("--name", default="RL2407")
    p.add_argument("--out", default="docs/manufacturing/cnc_winder")
    p.set_defaults(func=cmd_winder)

    p = dsub.add_parser("evaluate", help="evaluate a design JSON against a mission")
    p.add_argument("design")
    p.add_argument("--mission", default="5in_freestyle_6s")
    p.add_argument("--calibration")
    p.add_argument("--yield-samples", type=int, default=0)
    p.set_defaults(func=cmd_evaluate)

    p = dsub.add_parser("calibrate", help="update model calibration from a measured prototype")
    p.add_argument("--design", required=True, help="design.json of the prototype that was built")
    p.add_argument("--measured", required=True, help="library JSON from `bldc-motor fit`")
    p.add_argument("--motor", required=True, help="motor name inside the measured library")
    p.add_argument("--g-sa-w-per-k", type=float, help="measured stator-ambient conductance (fit-thermal)")
    p.add_argument("--g-sa-rpm", type=float, help="rpm at which that conductance was measured")
    p.add_argument("--calibration", default="outputs/motor/design/calibration.json")
    p.set_defaults(func=cmd_calibrate)
