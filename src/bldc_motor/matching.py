"""Motor / propeller / battery matching sweep for a multirotor.

For each combination it evaluates:

* hover: throttle, current, efficiency and g/W at thrust = weight / n_motors (at ``soc_hover``),
* punch-out: full-throttle thrust, thrust-to-weight, peak current (at ``soc_punch``),
* thermal: winding temperature after a full-throttle burst and the hover steady state,
* limit flags against motor current, ESC current and temperature ratings.

Mass bookkeeping: with ``dry_mass_g`` (frame + electronics, without motors/props/battery), the
all-up weight is dry + n*(motor + prop) + battery, so heavier motors are penalised fairly.
With ``auw_g`` the all-up weight is fixed for every combination.
"""

from __future__ import annotations

import csv
import itertools
import math
from dataclasses import dataclass, field
from pathlib import Path

from bldc_motor import thermal, units
from bldc_motor.motor import MotorParams
from bldc_motor.powertrain import ESC, Battery, Powertrain, hover_endurance_min
from bldc_motor.propeller import PropellerModel


@dataclass(frozen=True)
class MatchSettings:
    n_motors: int = 4
    auw_g: float | None = None
    dry_mass_g: float | None = None
    soc_hover: float = 0.6
    soc_punch: float = 0.9
    burst_s: float = 10.0
    ambient_c: float = 25.0
    rho: float = units.SEA_LEVEL_AIR_DENSITY_KG_M3

    def all_up_mass_g(self, motor: MotorParams, prop: PropellerModel, battery: Battery) -> float:
        if self.auw_g is not None:
            return self.auw_g
        if self.dry_mass_g is None:
            raise ValueError("Provide auw_g or dry_mass_g")
        missing = [
            n
            for n, m in ((motor.name, motor.mass_g), (prop.name, prop.mass_g), (battery.name, battery.mass_g))
            if m is None
        ]
        if missing:
            raise ValueError(f"dry_mass_g bookkeeping needs masses for: {', '.join(missing)}")
        return self.dry_mass_g + self.n_motors * (motor.mass_g + prop.mass_g) + battery.mass_g


@dataclass
class MatchResult:
    motor: str
    propeller: str
    battery: str
    esc: str
    row: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)


def evaluate_combination(
    motor: MotorParams, prop: PropellerModel, battery: Battery, esc: ESC, settings: MatchSettings
) -> MatchResult:
    powertrain = Powertrain(motor, prop, battery, esc, settings.n_motors, settings.rho)
    auw_g = settings.all_up_mass_g(motor, prop, battery)
    hover_thrust_n = units.grams_force_to_n(auw_g) / settings.n_motors
    result = MatchResult(motor.name, prop.name, battery.name, esc.name)
    row = result.row
    row.update({"motor": motor.name, "propeller": prop.name, "battery": battery.name, "esc": esc.name, "auw_g": auw_g})

    punch = powertrain.solve_at_throttle(1.0, settings.soc_punch)
    row.update(
        {
            "max_thrust_gf": units.n_to_grams_force(punch.thrust_n),
            "thrust_to_weight": punch.thrust_n / hover_thrust_n,
            "max_rpm": punch.rpm,
            "max_i_motor_a": punch.motor.current_a,
            "max_i_total_a": punch.i_bus_total_a,
            "punch_v_bus_v": punch.v_bus,
            "punch_cell_v": punch.v_bus / battery.cells_series,
        }
    )
    hover = powertrain.solve_for_thrust(hover_thrust_n, settings.soc_hover)
    if hover is None:
        result.flags.append("cannot_hover")
        row.update(
            {
                k: math.nan
                for k in (
                    "hover_throttle",
                    "hover_i_total_a",
                    "hover_motor_eff",
                    "hover_drive_eff",
                    "hover_g_per_w",
                    "hover_time_min",
                    "hover_rpm",
                )
            }
        )
    else:
        row.update(
            {
                "hover_throttle": hover.throttle,
                "hover_rpm": hover.rpm,
                "hover_i_total_a": hover.i_bus_total_a,
                "hover_motor_eff": hover.motor.efficiency,
                "hover_drive_eff": hover.drive_efficiency,
                "hover_g_per_w": hover.grams_per_watt,
                "hover_time_min": hover_endurance_min(battery, hover.i_bus_total_a),
            }
        )
        if hover.throttle > 0.6:
            result.flags.append("hover_throttle_high")

    if motor.thermal is not None:
        sim = thermal.simulate(
            powertrain,
            thermal.ThrottleProfile((0.0,), (1.0,)),
            settings.burst_s,
            0.05,
            settings.ambient_c,
            settings.soc_punch,
        )
        row["burst_winding_temp_c"] = float(sim["winding_temp_c"][-1])
        row["burst_magnet_temp_c"] = float(sim["stator_temp_c"][-1])
        if row["burst_winding_temp_c"] > motor.max_winding_temp_c:
            result.flags.append("burst_winding_overtemp")
        if row["burst_magnet_temp_c"] > motor.max_magnet_temp_c:
            result.flags.append("burst_magnet_overtemp")
        if hover is not None:
            ss = thermal.steady_state(powertrain, hover.throttle, settings.ambient_c, settings.soc_hover)
            row["hover_steady_winding_temp_c"] = ss.winding_temp_c
            if not ss.within_limits:
                result.flags.append("hover_overtemp")

    if motor.max_current_a is not None and punch.motor.current_a > motor.max_current_a:
        result.flags.append("motor_overcurrent")
    if esc.max_current_a is not None and punch.motor.current_a > esc.max_current_a:
        result.flags.append("esc_overcurrent")
    if punch.v_bus / battery.cells_series < battery.min_cell_v_under_load:
        result.flags.append("battery_sag")
    row["flags"] = ";".join(result.flags)
    return result


def sweep(motors, propellers, batteries, escs, settings: MatchSettings) -> list[MatchResult]:
    results = []
    for motor, prop, battery, esc in itertools.product(motors, propellers, batteries, escs):
        try:
            results.append(evaluate_combination(motor, prop, battery, esc, settings))
        except (RuntimeError, ValueError) as error:
            failed = MatchResult(motor.name, prop.name, battery.name, esc.name, flags=["solver_error"])
            failed.row = {
                "motor": motor.name,
                "propeller": prop.name,
                "battery": battery.name,
                "esc": esc.name,
                "flags": f"solver_error: {error}",
            }
            results.append(failed)
    results.sort(key=lambda r: (bool(r.flags), -(r.row.get("hover_g_per_w") or 0.0)))
    return results


def write_results_csv(results: list[MatchResult], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    keys: list[str] = []
    for result in results:
        keys.extend(k for k in result.row if k not in keys)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for result in results:
            writer.writerow({k: (f"{v:.6g}" if isinstance(v, float) else v) for k, v in result.row.items()})
    return target


def results_markdown(results: list[MatchResult], settings: MatchSettings) -> str:
    columns = [
        ("motor", "Motor", "{}"),
        ("propeller", "Prop", "{}"),
        ("battery", "Battery", "{}"),
        ("auw_g", "AUW [g]", "{:.0f}"),
        ("hover_throttle", "Hover thr", "{:.2f}"),
        ("hover_g_per_w", "Hover g/W", "{:.2f}"),
        ("hover_time_min", "Hover min", "{:.1f}"),
        ("thrust_to_weight", "T/W", "{:.1f}"),
        ("max_i_total_a", "Peak A", "{:.0f}"),
        ("burst_winding_temp_c", f"Wdg after {settings.burst_s:g}s WOT [C]", "{:.0f}"),
        ("flags", "Flags", "{}"),
    ]
    lines = ["| " + " | ".join(c[1] for c in columns) + " |", "|" + "---|" * len(columns)]
    for result in results:
        cells = []
        for key, _, fmt in columns:
            value = result.row.get(key, "")
            if isinstance(value, float) and not math.isfinite(value):
                cells.append("-")
            else:
                cells.append(fmt.format(value) if value != "" else "")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
