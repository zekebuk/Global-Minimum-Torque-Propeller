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
from prandtl_prop.pipeline import STAGES, StageOutcome, run_design
from prandtl_prop.runs import RunStore

HEADLINE = {
    "aero": ("thrust_N", "torque_Nm", "power_W", "figure_of_merit", "collective_offset_deg", "nonconverged_station_count"),
    "sections": ("section_count", "substituted_airfoil_count"),
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
        command.add_argument("--stages", default=",".join(STAGES), help=f"Comma-separated subset of {','.join(STAGES)}.")
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


def _print_design(name: str, outcomes: dict[str, StageOutcome]) -> None:
    print(f"{name}")
    for stage, outcome in outcomes.items():
        state = "cached" if outcome.cached else "ran"
        print(f"  {stage:<9} {state:<6} {config.relative(outcome.run.final_directory)}")
        for metric in HEADLINE[stage]:
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
        for metric in HEADLINE[stage]:
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
