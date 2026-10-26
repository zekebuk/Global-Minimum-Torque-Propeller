"""Command line: ``prandtl run`` and ``prandtl compare`` over design files.

prandtl run designs/gmt.yaml                      # all stages, cached
prandtl run designs/gmt.yaml --stages aero,beam   # only what is needed for these
prandtl run designs/gmt.yaml --set operating_point.rpm=4200
prandtl run designs/gmt.yaml --set-file designs/smoke_overrides.txt
prandtl compare designs/mil.yaml designs/gmt.yaml --stages aero,beam
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from prandtl_prop import config
from prandtl_prop.design import load_design, read_override_file
from prandtl_prop.pipeline import DEFAULT_STAGES, STAGES, StageOutcome, run_design
from prandtl_prop.runs import RunStore

HEADLINE = {
    "aero": ("thrust_N", "torque_Nm", "power_W", "figure_of_merit", "collective_offset_deg", "nonconverged_station_count"),
    "sections": ("section_count", "substituted_airfoil_count"),
    "acoustics": (
        "oaspl_ref_dB",
        "dba_ref_dB",
        "pwl_dB",
        "pwl_A_dB",
        "tonal_pwl_dB",
        "broadband_pwl_dB",
        "tip_pwl_dB",
        "alpha_tip_effective_deg",
        "delta_pwl_A_vs_MIL_dB",
        "delta_loading_pwl_vs_MIL_dB",
        "bvi_proxy_delta_vs_MIL_dB",
    ),
    "sitl": (
        "thr_mdl_fac",
        "ca_rotor_ct_N",
        "ca_rotor_km_m",
        "mpc_thr_hover",
        "motor_time_constant_ms",
        "hover_mean_battery_power_W",
        "survey_energy_Wh",
        "freestyle_saturated_fraction",
        "weighted_energy_Wh",
    ),
    "multirotor": (
        "hover_rpm",
        "hover_total_electrical_power_W",
        "hover_endurance_min",
        "max_thrust_rpm",
        "max_thrust_current_A",
        "motor_kv_rpm_per_V",
        "best_range_speed_m_s",
        "best_range_km",
        "motor_time_constant_ms",
    ),
    "dfm": ("all_pass", "min_trailing_edge_thickness_m", "max_section_thickness_m", "pull_angle_deg", "beam_excitation_margin_over_band"),
    "cad": ("blade_mass_kg", "blade_solid_valid", "loft_vs_section_quadrature_volume_ratio"),
    "beam": ("max_von_mises_Pa", "margin_of_safety", "tip_deflection_z_m", "tip_elastic_twist_deg", "root_centrifugal_force_N"),
    "fea": (
        "element_count",
        "max_von_mises_outside_clamp_zone_Pa",
        "margin_of_safety_outside_clamp_zone",
        "tip_deflection_z_m",
        "tip_deflection_z_ratio_fea_over_beam",
        "root_centrifugal_ratio_fea_over_beam",
    ),
    "modal": ("mode1_flap_hz", "mode1_margin", "min_frequency_margin", "min_frequency_margin_over_band"),
    "hub": (
        "variant",
        "blade_bolt",
        "hinge_pin",
        "motor_screw",
        "bolt_max_shear_N",
        "running_lag_angle_deg",
        "lag_frequency_per_rev",
        "max_same_side_fold_deg",
        "slip_margin",
        "plate_fatigue_margin",
        "neck_at_boss_edge_margin",
        "hub_mass_kg",
    ),
    "mold": ("core_valid", "cavity_valid", "impression_over_part_volume", "part_volume_fraction_in_cavity_half", "clearance_ok"),
    "cfd": (
        "cell_count",
        "thrust_N",
        "torque_Nm",
        "figure_of_merit",
        "thrust_ratio_cfd_over_bemt",
        "torque_ratio_cfd_over_bemt",
        "wall_vs_farfield_thrust_difference_fraction",
        "last_interval_thrust_change_fraction",
    ),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prandtl", description="Iterative propeller design pipeline.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("run", "Run stages for one or more designs."), ("compare", "Run designs and compare headline metrics.")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("designs", nargs="+", type=Path, help="Design YAML files.")
        command.add_argument(
            "--stages",
            default=",".join(DEFAULT_STAGES),
            help=f"Comma-separated subset of {','.join(STAGES)} (default: the fast stages {','.join(DEFAULT_STAGES)}).",
        )
        command.add_argument(
            "--set",
            dest="overrides",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="Override a design value, e.g. aero.tip_loss=false.",
        )
        command.add_argument(
            "--set-file",
            dest="override_files",
            action="append",
            default=[],
            type=Path,
            metavar="PATH",
            help="File of KEY=VALUE overrides, one per line.",
        )
        command.add_argument("--runs-dir", type=Path, default=config.RUNS_DIR, help="Root folder for run outputs.")
        command.add_argument("--force", action="store_true", help="Re-run even when a cached run exists.")
    molding = commands.add_parser(
        "optimize-molding", help="Optimise the thickness schedule for injection-molded CFRP and write a new design."
    )
    molding.add_argument("design", type=Path)
    molding.add_argument(
        "--material", default="peek-cf30", help="Material preset: aluminum, pa6-cf30, peek-cf30 (see manufacturing/materials.py)."
    )
    molding.add_argument(
        "--rpm-band", type=float, nargs=2, default=(0.9, 1.1), metavar=("LO", "HI"), help="Operating rpm band as fractions of design rpm."
    )
    molding.add_argument("--margin", type=float, default=0.10, help="Minimum frequency separation from kP lines.")
    molding.add_argument(
        "--model-cushion", type=float, default=0.01, help="Extra margin for beam-vs-solid-model error (CalculiX confirms the plain margin)."
    )
    molding.add_argument("--max-thickness-mm", type=float, default=6.5, help="Largest solid section thickness the molder accepts.")
    molding.add_argument("--te-mm", type=float, default=0.6, help="Minimum blunt trailing-edge thickness.")
    molding.add_argument(
        "--min-scale",
        type=float,
        default=1.0,
        help="Lowest thickness scale; below 1 thins the designed airfoils (aero model less reliable).",
    )
    molding.add_argument("--output", type=Path, default=None, help="Where to write the optimised design YAML.")
    molding.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    molding.add_argument("--set-file", dest="override_files", action="append", default=[], type=Path, metavar="PATH")
    molding.add_argument("--runs-dir", type=Path, default=config.RUNS_DIR)

    gmt = commands.add_parser(
        "design-gmt", help="Global-minimum-torque prop for a multirotor: design scan, motor match, write a new design."
    )
    gmt.add_argument("spec", type=Path, help="Spec design YAML (with gmt and multirotor sections).")
    gmt.add_argument("--output", type=Path, required=True, help="Design YAML to write (its stem becomes the design name).")
    gmt.add_argument("--report-dir", type=Path, default=None, help="Report folder (default: outputs/<design name>).")
    gmt.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    gmt.add_argument("--set-file", dest="override_files", action="append", default=[], type=Path, metavar="PATH")
    gmt.add_argument("--runs-dir", type=Path, default=config.RUNS_DIR)

    cost = commands.add_parser("cost", help="Production cost per prop against annual volume, per route (manufacturing/cost.py).")
    cost.add_argument("designs", nargs="+", type=Path)
    cost.add_argument("--output-dir", type=Path, default=config.PROJECT_ROOT / "outputs" / "production")
    cost.add_argument("--labor-usd-h", type=float, default=None, help="Labor rate (default 25; ~6 for an Asian contract molder).")
    cost.add_argument("--resin-usd-kg", type=float, default=None, help="PA6-CF30 compound price (default 10).")
    cost.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    cost.add_argument("--set-file", dest="override_files", action="append", default=[], type=Path, metavar="PATH")
    cost.add_argument("--runs-dir", type=Path, default=config.RUNS_DIR)

    noise_cfd = commands.add_parser(
        "noise-cfd", help="Tonal hover noise by FW-H on a CFD case's rotor wall pressure (acoustics/cfd_fwh.py)."
    )
    noise_cfd.add_argument("design", type=Path)
    noise_cfd.add_argument("--case", type=Path, required=True, help="OpenFOAM case folder (a mission or cfd stage case).")
    noise_cfd.add_argument("--rpm", type=float, required=True)
    noise_cfd.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    noise_cfd.add_argument("--set-file", dest="override_files", action="append", default=[], type=Path, metavar="PATH")
    noise_cfd.add_argument("--runs-dir", type=Path, default=config.RUNS_DIR)

    for name, help_text in (
        ("sitl-replay", "Replay PX4 ULog / truth-CSV missions (or the reference missions) with the design as built (sitl/)."),
        ("sitl-optimize", "Rank GMT twists by mission-weighted battery energy on the same missions; export the best for SITL."),
    ):
        sitl = commands.add_parser(name, help=help_text)
        sitl.add_argument("design", type=Path)
        sitl.add_argument("--log", dest="logs", action="append", default=[], type=Path, help="PX4 .ulg or truth .csv (repeatable).")
        sitl.add_argument("--weights", default=None, help="Comma-separated mission weights (default: equal, or sitl.mission_weights).")
        sitl.add_argument("--output-dir", type=Path, default=None, help="Default: outputs/sitl/<design name>.")
        sitl.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
        sitl.add_argument("--set-file", dest="override_files", action="append", default=[], type=Path, metavar="PATH")
        sitl.add_argument("--runs-dir", type=Path, default=config.RUNS_DIR)

    loop = commands.add_parser("loop", help="Iterate BEMT trim, CAD, CFD, and BEMT calibration until CFD meets target thrust.")
    loop.add_argument("design", type=Path, help="Design YAML file.")
    loop.add_argument("--iterations", type=int, default=4, help="Maximum CFD runs.")
    loop.add_argument("--thrust-tolerance", type=float, default=0.005, help="Converged when |T_cfd - T_target| / T_target is below this.")
    loop.add_argument(
        "--factor-degree", type=int, default=3, help="Polynomial degree of the spanwise correction factors (-1: per station)."
    )
    loop.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    loop.add_argument("--set-file", dest="override_files", action="append", default=[], type=Path, metavar="PATH")
    loop.add_argument("--runs-dir", type=Path, default=config.RUNS_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = RunStore(args.runs_dir)
    overrides = [line for path in args.override_files for line in read_override_file(path)] + args.overrides
    if args.command == "loop":
        return _loop(args, store, overrides)
    if args.command == "optimize-molding":
        return _optimize_molding(args, store, overrides)
    if args.command == "design-gmt":
        return _design_gmt(args, overrides)
    if args.command == "cost":
        return _cost(args, store, overrides)
    if args.command == "noise-cfd":
        return _noise_cfd(args, overrides)
    if args.command in ("sitl-replay", "sitl-optimize"):
        return _sitl(args, store, overrides)
    stages = [stage.strip() for stage in args.stages.split(",") if stage.strip()]
    results: dict[str, dict[str, StageOutcome]] = {}
    for path in args.designs:
        design = load_design(path, overrides)
        try:
            results[design.name] = run_design(design, stages, store=store, force=args.force)
        except (FileNotFoundError, ValueError, ImportError) as exc:
            print(f"[{design.name}] failed: {exc}", file=sys.stderr)
            return 1
        if args.command == "run":
            _print_design(design.name, results[design.name])
    if args.command == "compare":
        _print_comparison(results)
    return 0


def _loop(args: argparse.Namespace, store: RunStore, overrides: list[str]) -> int:
    from prandtl_prop.loop import run_cfd_loop

    try:
        result = run_cfd_loop(
            args.design,
            overrides=overrides,
            store=store,
            max_iterations=args.iterations,
            thrust_tolerance=args.thrust_tolerance,
            factor_degree=None if args.factor_degree < 0 else args.factor_degree,
        )
    except (FileNotFoundError, ValueError, ImportError, RuntimeError) as exc:
        print(f"loop failed: {exc}", file=sys.stderr)
        return 1
    columns = (
        "iteration",
        "collective_offset_deg",
        "bemt_thrust_N",
        "cfd_thrust_N",
        "cfd_torque_Nm",
        "cfd_thrust_error_fraction",
        "mean_lift_factor",
        "mean_drag_factor",
    )
    print("".join(f"{name:>26}" for name in columns))
    for row in result.history:
        print("".join(f"{_format(row[name]):>26}" for name in columns))
    print(f"converged: {result.converged}")
    print(f"history: {config.relative(result.history_csv)}")
    if result.corrections_csv:
        print(f"latest corrections: {config.relative(result.corrections_csv)}")
    return 0 if result.converged else 3


def _noise_cfd(args: argparse.Namespace, overrides: list[str]) -> int:
    from prandtl_prop.acoustics import cfd_fwh

    design = load_design(args.design, overrides)
    op = design.operating_point
    try:
        result = cfd_fwh.cfd_surface_noise(args.case, args.rpm, op.radius_m, op.blade_count, op.rho_kg_m3, design.acoustics)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"noise-cfd failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"{result['faces']} wall faces, pressure thrust {result['pressure_thrust_N']:.3f} N, observers at {result['observer_distance_m']:.2f} m"
    )
    print(f"{'elevation':>10}" + "".join(f"{'BPF' + str(m + 1) + ' load':>12}{'thick':>8}{'total':>8}" for m in range(3)))
    for i, el in enumerate(result["elevations_deg"]):
        cells = "".join(
            f"{cfd_fwh.spl(result['loading'][i, m]):12.2f}{cfd_fwh.spl(result['thickness'][i, m]):8.2f}{cfd_fwh.spl(result['total'][i, m]):8.2f}"
            for m in range(min(3, result["total"].shape[1]))
        )
        print(f"{el:10.0f}{cells}")
    return 0


def _sitl(args: argparse.Namespace, store: RunStore, overrides: list[str]) -> int:
    from prandtl_prop.sitl.workflow import mission_study

    design = load_design(args.design, overrides)
    weights = [float(w) for w in args.weights.split(",")] if args.weights else None
    output = args.output_dir or config.PROJECT_ROOT / "outputs" / "sitl" / design.name
    try:
        mission_study(design, store, args.logs, weights, output, optimize=args.command == "sitl-optimize")
    except (FileNotFoundError, ValueError, ImportError) as exc:
        print(f"{args.command} failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _usd(value: float) -> str:
    return f"${value:.2f}"


def _cost(args: argparse.Namespace, store: RunStore, overrides: list[str]) -> int:
    from dataclasses import replace

    from prandtl_prop.manufacturing import production
    from prandtl_prop.manufacturing.cost import CostAssumptions

    assumptions = CostAssumptions()
    if args.labor_usd_h is not None:
        assumptions = replace(assumptions, labor_usd_h=args.labor_usd_h)
    if args.resin_usd_kg is not None:
        assumptions = replace(assumptions, resin_usd_per_kg=args.resin_usd_kg)
    designs, runs = [], {}
    for path in args.designs:
        design = load_design(path, overrides)
        try:
            outcomes = run_design(design, ["cad", "dfm"], store=store)
        except (FileNotFoundError, ValueError, ImportError) as exc:
            print(f"[{design.name}] failed: {exc}", file=sys.stderr)
            return 1
        designs.append(design)
        runs[design.name] = {stage: outcome.run for stage, outcome in outcomes.items()}
    rows = production.cost_table(designs, runs, assumptions)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    production.write_csv(rows, args.output_dir / "production_costs.csv")
    production.plot(rows, args.output_dir / "production_costs.png")
    production.plot_breakdown(rows, args.output_dir / "production_breakdown.png")
    print(f"{'design':<24}{'variant':<26}{'props/yr':>10}  " + "".join(f"{r:>28}" for r in dict.fromkeys(r["route"] for r in rows)))
    for key in dict.fromkeys((r["design"], r["variant"], r["props_per_year"]) for r in rows):
        pick = [r for r in rows if (r["design"], r["variant"], r["props_per_year"]) == key]
        cells = "".join(f"{_usd(r['usd_per_prop']) if r['feasible'] else 'n/a':>28}" for r in pick)
        print(f"{key[0]:<24}{key[1][:25]:<26}{key[2]:>10.0f}  {cells}")
    print(f"report: {config.relative(args.output_dir)}")
    return 0


def _design_gmt(args: argparse.Namespace, overrides: list[str]) -> int:
    from prandtl_prop.gmt_design import design_gmt

    report_dir = args.report_dir or config.PROJECT_ROOT / "outputs" / args.output.stem
    try:
        summary = design_gmt(args.spec, output_design=args.output, report_dir=report_dir, overrides=overrides)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"design-gmt failed: {exc}", file=sys.stderr)
        return 1
    chosen, same = summary["selected"], summary["same_cell_comparison"]
    print(f"selected: hover {chosen['hover_rpm']:.0f} rpm, c_max/R {chosen['chord_max_over_R']:.2f} (objective: {chosen['objective']})")
    print(
        f"  hover {chosen['hover_total_electrical_power_W']:.1f} W total, {chosen['hover_torque_Nm'] * 1e3:.2f} mN*m per rotor, "
        f"endurance {chosen['hover_endurance_min']:.1f} min; full throttle {chosen['max_thrust_rpm']:.0f} rpm, "
        f"{chosen['max_thrust_current_A']:.1f} A, Kv {chosen['motor_kv_rpm_per_V']:.0f}"
    )
    for name in ("gmt", "gmt_unconstrained", "mil", "helix"):
        row = same[name]
        fits = "fits" if row["root_scale_needed"] is not None else "exceeds the root-thickness cap"
        print(f"  same cell, {name:>17}: torque {row['torque_Nm'] * 1e3:.3f} mN*m, FM {row['figure_of_merit']:.4f}, root stress {fits}")
    print(f"design: {config.relative(Path(summary['design_file']))}")
    print(f"report: {config.relative(report_dir)}")
    return 0


def _optimize_molding(args: argparse.Namespace, store: RunStore, overrides: list[str]) -> int:
    from prandtl_prop.manufacturing.workflow import optimize_for_molding

    try:
        outcome = optimize_for_molding(
            args.design,
            material=args.material,
            overrides=overrides,
            store=store,
            rpm_band=tuple(args.rpm_band),
            margin=args.margin,
            max_section_thickness_m=args.max_thickness_mm / 1000.0,
            trailing_edge_thickness_m=args.te_mm / 1000.0,
            min_scale=args.min_scale,
            output=args.output,
            model_cushion=args.model_cushion,
        )
    except (FileNotFoundError, ValueError, KeyError, ImportError) as exc:
        print(f"optimize-molding failed: {exc}", file=sys.stderr)
        return 1
    result = outcome.result
    print(f"baseline first flap f0 {result.f0_baseline_hz:.1f} Hz, Southwell K {result.southwell_k:.2f}")
    for attempt in result.attempts:
        print(f"  {attempt['window']}: {attempt['result']}")
    if result.best is None:
        print("no feasible design: relax the rpm band, the thickness limit, or choose a stiffer material")
        print(f"report: {config.relative(outcome.report_path)}")
        return 3
    best = result.best
    print(f"chosen window {result.window.lower_harmonic}P-{result.window.lower_harmonic + 1}P")
    print(f"  profile-torque penalty {best.profile_torque_penalty_Nm * 1e3:.2f} mN*m, blade mass {best.blade_mass_kg * 1e3:.0f} g")
    print(
        f"  first flap {best.flap1_hz.min():.0f}-{best.flap1_hz.max():.0f} Hz over the band, max stress {best.max_stress_Pa / 1e6:.1f} MPa"
    )
    print(f"  tip deflection {best.tip_deflection_m * 1e3:.1f} mm, max thickness {best.molding['max_section_thickness_m'] * 1e3:.2f} mm")
    print(f"design: {config.relative(outcome.design_path)}")
    print(f"report: {config.relative(outcome.report_path)}")
    return 0


def _print_design(name: str, outcomes: dict[str, StageOutcome]) -> None:
    print(f"{name}")
    for stage, outcome in outcomes.items():
        state = "cached" if outcome.cached else "ran"
        print(f"  {stage:<9} {state:<6} {config.relative(outcome.run.final_directory)}")
        for metric in HEADLINE.get(stage, ()):
            if metric in outcome.run.metrics:
                print(f"      {metric:<40} {_format(outcome.run.metrics[metric])}")
        for warning in outcome.run.manifest.get("warnings", []):
            print(f"      warning: {warning}")


def _print_comparison(results: dict[str, dict[str, StageOutcome]]) -> None:
    names = list(results)
    baseline = names[0]
    print(f"{'metric':<48}" + "".join(f"{name:>16}" for name in names) + (f"{'delta vs ' + baseline:>22}" if len(names) == 2 else ""))
    for stage in STAGES:
        if not all(stage in results[name] for name in names):
            continue
        for metric in HEADLINE.get(stage, ()):
            values = [results[name][stage].run.metrics.get(metric) for name in names]
            line = f"{stage + '.' + metric:<48}" + "".join(f"{_format(value):>16}" for value in values)
            if len(values) == 2 and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values) and values[0]:
                line += f"{(values[1] - values[0]) / abs(values[0]) * 100.0:>+21.3f}%"
            print(line)


def _format(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, float):
        return f"{value:.6g}" if math.isfinite(value) else str(value)
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
