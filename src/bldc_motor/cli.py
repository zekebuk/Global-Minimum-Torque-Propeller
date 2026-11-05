"""Command-line interface: ``bldc-motor <command>`` (or ``python -m bldc_motor``)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from bldc_motor import identification, matching, synthetic, teststand, thermal, units
from bldc_motor.library import load_library, save_components
from bldc_motor.powertrain import Powertrain

DEFAULT_OUT = Path("outputs/motor")


def _library(args):
    return load_library(*(args.library or []))


def _powertrain(args, lib, n_motors=None):
    return Powertrain(
        lib.motor(args.motor),
        lib.propeller(args.prop),
        lib.battery(args.battery),
        lib.esc(args.esc),
        n_motors=n_motors if n_motors is not None else args.n_motors,
    )


def _print_rows(rows: list[dict], keys: list[str]) -> None:
    widths = {k: max(len(k), *(len(_cell(r.get(k))) for r in rows)) for k in keys}
    print("  ".join(k.rjust(widths[k]) for k in keys))
    for row in rows:
        print("  ".join(_cell(row.get(k)).rjust(widths[k]) for k in keys))


def _cell(value) -> str:
    if isinstance(value, (float, np.floating)):
        return f"{value:.4g}"
    return "" if value is None else str(value)


POINT_KEYS = [
    "throttle",
    "rpm",
    "thrust_gf",
    "i_bus_per_motor_a",
    "i_bus_total_a",
    "v_bus_v",
    "p_bus_per_motor_w",
    "motor_efficiency",
    "drive_efficiency",
    "grams_per_watt",
]


# ---- commands --------------------------------------------------------------------------


def cmd_list(args) -> int:
    lib = _library(args)
    print("Motors (Kv, R_ll, I0, Km):")
    for name, m in sorted(lib.motors.items()):
        print(
            f"  {name:28s} {m.kv_rpm_per_v:6.0f} Kv  {m.r_ll_ohm * 1000:6.1f} mOhm  {m.i0_a:4.2f} A  Km={m.km_nm_per_sqrt_w:.4f} N·m/√W  [{m.source}]"
        )
    print("Propellers (D, CT0, CP0, FM0):")
    for name, p in sorted(lib.propellers.items()):
        print(
            f"  {name:28s} {p.diameter_m / units.INCH_TO_M:4.1f} in  CT={p.ct():.3f}  CP={p.cp():.3f}  FM={p.figure_of_merit():.2f}  [{p.source}]"
        )
    print("Batteries:")
    for name, b in sorted(lib.batteries.items()):
        print(
            f"  {name:28s} {b.cells_series}S{b.cells_parallel}P {b.capacity_mah:.0f} mAh  R={b.resistance_ohm * 1000:.1f} mOhm  [{b.source}]"
        )
    print("ESCs:")
    for name, e in sorted(lib.escs.items()):
        print(f"  {name:28s} R_series={e.r_series_ohm * 1000:.1f} mOhm  [{e.source}]")
    return 0


def cmd_point(args) -> int:
    lib = _library(args)
    pt = _powertrain(args, lib)
    rows = []
    for throttle in args.throttle:
        point = pt.solve_at_throttle(throttle, args.soc, args.v_inf, args.temp, args.temp)
        rows.append(point.as_row())
    _print_rows(rows, POINT_KEYS)
    if args.out:
        teststand.write_columns_csv({k: np.array([r[k] for r in rows]) for k in rows[0]}, args.out)
        print(f"wrote {args.out}")
    return 0


def cmd_hover(args) -> int:
    lib = _library(args)
    pt = _powertrain(args, lib)
    per_motor_n = units.grams_force_to_n(args.auw_g) / pt.n_motors
    point = pt.solve_for_thrust(per_motor_n, args.soc, args.v_inf)
    if point is None:
        print(f"Cannot produce {args.auw_g / pt.n_motors:.0f} gf per motor at full throttle")
        return 1
    _print_rows([point.as_row()], POINT_KEYS)
    from bldc_motor.powertrain import hover_endurance_min

    print(
        f"estimated hover time: {hover_endurance_min(pt.battery, point.i_bus_total_a):.1f} min ({pt.battery.usable_fraction:.0%} usable capacity, constant current)"
    )
    return 0


def cmd_match(args) -> int:
    lib = _library(args)
    settings = matching.MatchSettings(
        n_motors=args.n_motors,
        auw_g=args.auw_g,
        dry_mass_g=args.dry_mass_g,
        soc_hover=args.soc_hover,
        soc_punch=args.soc_punch,
        burst_s=args.burst_s,
    )
    motors = [lib.motor(n) for n in (args.motors or sorted(lib.motors))]
    props = [lib.propeller(n) for n in (args.props or sorted(lib.propellers))]
    batteries = [lib.battery(n) for n in (args.batteries or sorted(lib.batteries))]
    escs = [lib.esc(n) for n in (args.escs or ["generic_esc"])]
    results = matching.sweep(motors, props, batteries, escs, settings)
    table = matching.results_markdown(results, settings)
    print(table)
    out = Path(args.out)
    csv_path = matching.write_results_csv(results, out / "match_results.csv")
    (out / "match_report.md").write_text(_match_report(table, settings), encoding="utf-8")
    print(f"\nwrote {csv_path} and {out / 'match_report.md'}")
    return 0


def _match_report(table: str, settings: matching.MatchSettings) -> str:
    mass = (
        f"fixed AUW {settings.auw_g:g} g"
        if settings.auw_g
        else f"dry mass {settings.dry_mass_g:g} g + motors, props, battery"
    )
    return (
        "# Motor / propeller / battery matching\n\n"
        f"- {settings.n_motors} motors, {mass}\n"
        f"- hover evaluated at SoC {settings.soc_hover:.0%}, punch-out at SoC {settings.soc_punch:.0%}\n"
        f"- thermal burst: {settings.burst_s:g} s at full throttle from {settings.ambient_c:g} °C\n"
        "- quasi-steady DC-equivalent model; see docs/motor_development.md for assumptions\n"
        "- components marked `illustrative` in the library are placeholders, not measurements\n\n"
        f"{table}\n"
    )


def cmd_thermal(args) -> int:
    lib = _library(args)
    pt = _powertrain(args, lib)
    profile = thermal.ThrottleProfile.parse(args.profile, step=not args.ramp)
    duration = args.duration or profile.end_time_s + 10.0
    sim = thermal.simulate(pt, profile, duration, args.dt, args.ambient, args.soc)
    peak_w = float(sim["winding_temp_c"].max())
    peak_s = float(sim["stator_temp_c"].max())
    print(
        f"peak winding {peak_w:.1f} °C (limit {pt.motor.max_winding_temp_c:g}), peak stator/magnet {peak_s:.1f} °C (limit {pt.motor.max_magnet_temp_c:g})"
    )
    print(f"final SoC {sim['soc'][-1]:.3f}, min bus voltage {sim['v_bus_v'].min():.2f} V")
    out = Path(args.out)
    teststand.write_columns_csv(sim, out / "thermal_transient.csv")
    if not args.no_plot:
        from bldc_motor import plotting

        plotting.thermal_trace(
            sim,
            out / "thermal_transient.png",
            f"{pt.motor.name} + {pt.propeller.name}",
            (pt.motor.max_winding_temp_c, pt.motor.max_magnet_temp_c),
        )
    print(f"wrote {out}/thermal_transient.*")
    return 0


def _load(args, path):
    return teststand.load_stand_csv(path, rpm_is_electrical=args.erpm, pole_pairs=args.pole_pairs)


def cmd_import(args) -> int:
    log = _load(args, args.data)
    for warning in log.warnings:
        print(f"warning: {warning}")
    print("columns: " + ", ".join(f"{k} <- '{v}'" for k, v in log.source_columns.items()))
    diameter = args.diameter_in * units.INCH_TO_M if args.diameter_in else None
    teststand.add_derived_metrics(log, diameter)
    target = log
    if args.steady:
        target = teststand.add_derived_metrics(
            teststand.steady_state_points(log, settle_s=args.settle_s, min_hold_s=args.min_hold_s), diameter
        )
        keys = [
            k
            for k in (
                "throttle",
                "voltage_v",
                "current_a",
                "rpm",
                "thrust_n",
                "torque_nm",
                "drive_efficiency",
                "grams_per_watt",
                "ct",
                "cp",
            )
            if k in target.data
        ]
        _print_rows([{k: target[k][i] for k in keys} for i in range(len(target))], keys)
    out = args.out or Path(args.data).with_suffix(".processed.csv")
    target.to_csv(out)
    print(f"wrote {out}")
    return 0


def cmd_fit(args) -> int:
    lib = _library(args)
    loaded = teststand.steady_state_points(_load(args, args.data), settle_s=args.settle_s, min_hold_s=args.min_hold_s)
    no_load = (
        teststand.steady_state_points(_load(args, args.no_load), settle_s=args.settle_s, min_hold_s=args.min_hold_s)
        if args.no_load
        else None
    )
    r_ll = args.r_ll_mohm / 1000.0 if args.r_ll_mohm else None
    if r_ll is not None and args.r_temp_c is not None:
        r_ll = identification.resistance_to_reference(r_ll, args.r_temp_c)
    template = lib.motor(args.template) if args.template else None
    esc = lib.esc(args.esc)
    result = identification.fit_motor_from_stand(
        loaded, args.name, r_ll, args.fit_resistance, no_load, esc, trust_duty=args.trust_duty, template=template
    )
    print(
        json.dumps(
            {k: (float(v) if isinstance(v, (float, np.floating)) else v) for k, v in result.diagnostics.items()},
            indent=2,
        )
    )
    out = Path(args.out)
    save_components(out / "fitted_library.json", motors=[result.motor])
    print(f"wrote motor '{result.motor.name}' to {out / 'fitted_library.json'} (load with --library)")
    if args.diameter_in:
        from bldc_motor.propeller import PropellerModel

        prop, diag = PropellerModel.fit_static(
            args.prop_name,
            args.diameter_in * units.INCH_TO_M,
            loaded["rpm"],
            loaded["thrust_n"],
            loaded["torque_nm"],
            mass_g=args.prop_mass_g,
            source="fitted from thrust-stand data",
        )
        print(
            f"propeller fit: CT={prop.ct():.4f} CP={prop.cp():.4f} thrust R²={diag['thrust_r2']:.4f} torque R²={diag['torque_r2']:.4f}"
        )
        save_components(out / "fitted_library.json", propellers=[prop])
        if args.supply_v:
            from bldc_motor import plotting

            pt = Powertrain(result.motor, prop, synthetic.bench_supply(args.supply_v, args.supply_r), esc, n_motors=1)
            plotting.model_vs_stand(loaded, pt, out / "model_vs_stand.png")
            print(f"wrote {out / 'model_vs_stand.png'}")
    return 0


def cmd_fit_thermal(args) -> int:
    log = _load(args, args.data)
    column = args.column
    mask = np.isfinite(log[column])
    if args.t_start is not None:
        mask &= log["time_s"] >= args.t_start
    if args.t_end is not None:
        mask &= log["time_s"] <= args.t_end
    result = identification.fit_first_order_thermal(log["time_s"][mask], log[column][mask], args.power_w)
    print(json.dumps(result, indent=2))
    if result["record_length_over_tau"] < 3.0:
        print("warning: record shorter than 3 time constants; steady-state temperature is an extrapolation")
    return 0


def cmd_synth(args) -> int:
    lib = _library(args)
    motor = lib.motor(args.motor)
    prop = lib.propeller(args.prop)
    supply = synthetic.bench_supply(args.supply_v, args.supply_r)
    esc = lib.esc(args.esc)
    out = Path(args.out)
    pt = Powertrain(motor, prop, supply, esc, n_motors=1)
    steps = tuple(args.steps)
    cols = synthetic.generate_stand_log(pt, steps, args.hold_s, args.sample_hz, seed=args.seed)
    loaded = synthetic.write_stand_csv(
        cols, out / "synthetic_loaded.csv", description=f"{motor.name} + {prop.name} @ {args.supply_v:g} V supply"
    )
    nl_pt = Powertrain(motor, synthetic.unloaded(prop), supply, esc, n_motors=1)
    nl_cols = synthetic.generate_stand_log(nl_pt, (1.0,), args.hold_s, args.sample_hz, seed=args.seed + 1)
    no_load = synthetic.write_stand_csv(
        nl_cols, out / "synthetic_no_load.csv", description=f"{motor.name} no-load @ {args.supply_v:g} V supply"
    )
    print(f"wrote {loaded} and {no_load}")
    return 0


def cmd_effmap(args) -> int:
    from bldc_motor import plotting

    lib = _library(args)
    out = Path(args.out)
    if args.prop and args.battery:
        pt = _powertrain(args, lib)
        path = plotting.overlay_prop_line(pt, out / f"effmap_{args.motor}_{args.prop}.png", args.soc)
    else:
        voltage = args.voltage or lib.battery(args.battery).ocv(args.soc)
        path = plotting.efficiency_map(
            lib.motor(args.motor), voltage, out / f"effmap_{args.motor}.png", temp_c=args.temp
        )
    print(f"wrote {path}")
    return 0


def cmd_rewind(args) -> int:
    lib = _library(args)
    base = lib.motor(args.motor)
    new = base.rewound(args.kv, args.name)
    for m in (base, new):
        print(
            f"{m.name:36s} Kv={m.kv_rpm_per_v:7.1f}  R={m.r_ll_ohm * 1000:6.2f} mOhm  I0={m.i0_a:5.3f} A  Km={m.km_nm_per_sqrt_w:.4f}"
        )
    if args.save:
        save_components(args.save, motors=[new])
        print(f"saved to {args.save}")
    return 0


# ---- parser ----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bldc-motor", description="BLDC motor development toolkit for FPV multirotors"
    )
    parser.add_argument(
        "--library", action="append", help="extra component library JSON (repeatable; overrides bundled entries)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def powertrain_args(p, battery_required=True):
        p.add_argument("--motor", required=True)
        p.add_argument("--prop", required=battery_required)
        p.add_argument("--battery", required=battery_required)
        p.add_argument("--esc", default="generic_esc")
        p.add_argument("--n-motors", type=int, default=4)
        p.add_argument("--soc", type=float, default=1.0, help="battery state of charge 0..1")

    def stand_args(p):
        p.add_argument("--erpm", action="store_true", help="logged speed is electrical rpm")
        p.add_argument("--pole-pairs", type=int)
        p.add_argument("--settle-s", type=float, default=1.0)
        p.add_argument("--min-hold-s", type=float, default=0.5)

    sub.add_parser("list", help="list library components").set_defaults(func=cmd_list)

    p = sub.add_parser("point", help="operating point(s) at given throttle(s)")
    powertrain_args(p)
    p.add_argument("--throttle", type=float, nargs="+", default=[0.25, 0.5, 0.75, 1.0])
    p.add_argument("--v-inf", type=float, default=0.0, help="axial airspeed [m/s]")
    p.add_argument("--temp", type=float, help="motor temperature [°C] for R and Ke (default: reference)")
    p.add_argument("--out", type=Path)
    p.set_defaults(func=cmd_point)

    p = sub.add_parser("hover", help="hover throttle for an all-up weight")
    powertrain_args(p)
    p.add_argument("--auw-g", type=float, required=True)
    p.add_argument("--v-inf", type=float, default=0.0)
    p.set_defaults(func=cmd_hover, soc=0.6)

    p = sub.add_parser("match", help="sweep motor/prop/battery combinations")
    p.add_argument("--motors", nargs="+")
    p.add_argument("--props", nargs="+")
    p.add_argument("--batteries", nargs="+")
    p.add_argument("--escs", nargs="+")
    p.add_argument("--n-motors", type=int, default=4)
    mass = p.add_mutually_exclusive_group(required=True)
    mass.add_argument("--auw-g", type=float)
    mass.add_argument("--dry-mass-g", type=float, help="frame+electronics without motors, props, battery")
    p.add_argument("--soc-hover", type=float, default=0.6)
    p.add_argument("--soc-punch", type=float, default=0.9)
    p.add_argument("--burst-s", type=float, default=10.0)
    p.add_argument("--out", default=str(DEFAULT_OUT / "match"))
    p.set_defaults(func=cmd_match)

    p = sub.add_parser("thermal", help="electro-thermal transient over a throttle profile")
    powertrain_args(p)
    p.add_argument("--profile", default="0:0.3,10:1.0,20:0.3", help="'t:throttle,...' breakpoints")
    p.add_argument("--ramp", action="store_true", help="interpolate between breakpoints instead of steps")
    p.add_argument("--duration", type=float)
    p.add_argument("--dt", type=float, default=0.05)
    p.add_argument("--ambient", type=float, default=25.0)
    p.add_argument("--out", default=str(DEFAULT_OUT / "thermal"))
    p.add_argument("--no-plot", action="store_true")
    p.set_defaults(func=cmd_thermal)

    p = sub.add_parser("import", help="normalize a thrust-stand CSV, add derived metrics")
    p.add_argument("data")
    p.add_argument("--diameter-in", type=float)
    p.add_argument("--steady", action="store_true", help="also extract steady-state plateau averages")
    p.add_argument("--out", type=Path)
    stand_args(p)
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("fit", help="identify motor (and prop) parameters from stand data")
    p.add_argument("data", help="loaded stepped-throttle stand CSV")
    p.add_argument("--no-load", help="no-prop run at 100%% throttle (optional)")
    p.add_argument("--r-ll-mohm", type=float, help="measured line-to-line resistance [mOhm]")
    p.add_argument("--r-temp-c", type=float, help="temperature at which R was measured")
    p.add_argument("--fit-resistance", action="store_true")
    p.add_argument("--trust-duty", action="store_true", help="assume duty = throttle for all points")
    p.add_argument("--esc", default="generic_esc")
    p.add_argument("--name", default="fitted_motor")
    p.add_argument("--template", help="library motor whose mass/thermal/limits are kept")
    p.add_argument("--diameter-in", type=float, help="also fit propeller CT/CP")
    p.add_argument("--prop-name", default="fitted_prop")
    p.add_argument("--prop-mass-g", type=float, help="weighed prop mass (needed for --dry-mass-g matching)")
    p.add_argument("--supply-v", type=float, help="bench supply voltage, enables model-vs-stand plot")
    p.add_argument("--supply-r", type=float, default=0.005)
    p.add_argument("--out", default=str(DEFAULT_OUT / "fit"))
    stand_args(p)
    p.set_defaults(func=cmd_fit)

    p = sub.add_parser("fit-thermal", help="first-order thermal fit of a constant-load heating log")
    p.add_argument("data")
    p.add_argument("--column", default="temp_c", choices=["temp_c", "magnet_temp_c"])
    p.add_argument("--power-w", type=float, help="constant loss power during the run")
    p.add_argument("--t-start", type=float)
    p.add_argument("--t-end", type=float)
    stand_args(p)
    p.set_defaults(func=cmd_fit_thermal)

    p = sub.add_parser("synth", help="generate labelled synthetic stand logs from the model")
    p.add_argument("--motor", required=True)
    p.add_argument("--prop", required=True)
    p.add_argument("--esc", default="generic_esc")
    p.add_argument("--supply-v", type=float, default=24.0)
    p.add_argument("--supply-r", type=float, default=0.005)
    p.add_argument("--steps", type=float, nargs="+", default=[0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    p.add_argument("--hold-s", type=float, default=3.0)
    p.add_argument("--sample-hz", type=float, default=20.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default=str(DEFAULT_OUT / "synthetic"))
    p.set_defaults(func=cmd_synth)

    p = sub.add_parser("effmap", help="motor efficiency map (optionally with prop operating line)")
    powertrain_args(p, battery_required=False)
    p.add_argument("--voltage", type=float)
    p.add_argument("--temp", type=float)
    p.add_argument("--out", default=str(DEFAULT_OUT / "figures"))
    p.set_defaults(func=cmd_effmap)

    p = sub.add_parser("rewind", help="what-if: same stator rewound to a different Kv")
    p.add_argument("--motor", required=True)
    p.add_argument("--kv", type=float, required=True)
    p.add_argument("--name")
    p.add_argument("--save", type=Path, help="append the rewound motor to a library JSON")
    p.set_defaults(func=cmd_rewind)
    from bldc_motor.design.cli import add_design_commands

    add_design_commands(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "command", None) == "effmap" and not (args.voltage or args.battery):
        print("effmap needs --voltage or --battery", file=sys.stderr)
        return 2
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
