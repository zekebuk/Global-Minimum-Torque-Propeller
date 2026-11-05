"""Mission requirements and the design objective (what "optimal" means).

A design is evaluated *in the aircraft*: the analytical motor is installed on the mission's
propeller, battery and ESC, the all-up weight includes ``n_motors`` copies of the motor's own
mass, and performance is computed with the ``Powertrain`` solver:

* hover at ``soc_hover``, at the hover steady-state temperature (the motor is warm in flight);
* punch-out from that hover state at ``soc_punch``, held for ``burst_s`` seconds, with the
  two-node thermal network integrated in closed form (matrix exponential).

Objective (higher is better):

    J = w_eff * (hover g/W) / ref_g_per_w          efficiency -> endurance
      + w_mfg * manufacturability                   exp(-sum DFM penalties), 0..1
      - w_cost * unit_cost / ref_cost_usd
      - constraint penalties                        T/W, temperatures, demag & ESC current, size

Infeasible geometry or a design that cannot hover gets a graded negative score, so that
optimizers (including the RL agent) still see which direction improves.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
from scipy.linalg import expm

from bldc_motor import units
from bldc_motor.design import manufacturing, model
from bldc_motor.library import Library, load_library
from bldc_motor.powertrain import ESC, Battery, Powertrain, PowertrainPoint
from bldc_motor.propeller import PropellerModel


@dataclass(frozen=True)
class MissionSpec:
    name: str
    propeller: PropellerModel
    battery: Battery
    esc: ESC
    dry_mass_g: float
    n_motors: int = 4
    min_thrust_to_weight: float = 6.0
    soc_hover: float = 0.6
    soc_punch: float = 0.9
    burst_s: float = 10.0
    ambient_c: float = 30.0
    esc_max_current_a: float | None = None
    max_stator_od_mm: float | None = None
    max_motor_mass_g: float | None = None
    ref_g_per_w: float = 4.5
    ref_cost_usd: float = 8.0
    w_eff: float = 1.0
    w_mfg: float = 0.6
    w_cost: float = 0.25

    def context_vector(self) -> np.ndarray:
        """Normalised mission features for policies that generalise across missions."""

        return np.array(
            [
                self.propeller.diameter_m / 0.2,
                self.propeller.ct() / 0.2,
                self.propeller.cp() / 0.15,
                self.battery.cells_series / 6.0,
                self.dry_mass_g / 500.0,
                self.min_thrust_to_weight / 10.0,
            ]
        )


def preset_missions(library: Library | None = None) -> dict[str, MissionSpec]:
    lib = library or load_library()
    return {
        "5in_freestyle_6s": MissionSpec(
            name="5in_freestyle_6s",
            propeller=lib.propeller("generic_5.1x4.6_tri"),
            battery=lib.battery("lipo_6s_1300"),
            esc=lib.esc("generic_esc"),
            dry_mass_g=250.0,
            min_thrust_to_weight=8.0,
            esc_max_current_a=55.0,
            max_stator_od_mm=30.0,
            ref_g_per_w=4.5,
        ),
        "3in_cinewhoop_4s": MissionSpec(
            name="3in_cinewhoop_4s",
            propeller=lib.propeller("generic_3x3_tri"),
            battery=lib.battery("lipo_4s_650"),
            esc=lib.esc("generic_esc_small"),
            dry_mass_g=140.0,
            min_thrust_to_weight=4.0,
            esc_max_current_a=25.0,
            max_stator_od_mm=18.0,
            ref_g_per_w=3.5,
            ref_cost_usd=6.0,
        ),
        "7in_longrange_6s": MissionSpec(
            name="7in_longrange_6s",
            propeller=lib.propeller("generic_7x3.5_bi"),
            battery=lib.battery("lipo_6s_2200"),
            esc=lib.esc("generic_esc"),
            dry_mass_g=320.0,
            min_thrust_to_weight=4.0,
            soc_hover=0.5,
            esc_max_current_a=45.0,
            max_stator_od_mm=32.0,
            ref_g_per_w=7.0,
            w_eff=1.5,
        ),
    }


@dataclass
class Evaluation:
    design: dict
    score: float
    feasible: bool
    metrics: dict = field(default_factory=dict)
    penalties: dict = field(default_factory=dict)
    analysis: model.DesignAnalysis | None = None
    rules: list = field(default_factory=list)
    cost: dict = field(default_factory=dict)
    hover: PowertrainPoint | None = None
    punch: PowertrainPoint | None = None

    @property
    def failed_rules(self) -> list[str]:
        return [r.rule for r in self.rules if r.status == "FAIL"]


def _thermal_after(thermal, point: PowertrainPoint, x0: np.ndarray, t: float) -> tuple[np.ndarray, np.ndarray]:
    """Closed-form two-node response (temperatures above ambient) to constant losses."""

    g_ws = thermal.g_winding_stator_w_per_k
    g_sa = thermal.g_stator_ambient(point.motor.omega_rad_s)
    conductance = np.array([[g_ws, -g_ws], [-g_ws, g_ws + g_sa]])
    capacitance = np.array([thermal.c_winding_j_per_k, thermal.c_stator_j_per_k])
    power = np.array([point.motor.p_copper_w, point.motor.p_no_load_w])
    x_ss = np.linalg.solve(conductance, power)
    if not math.isfinite(t):
        return x_ss, x_ss
    a = -conductance / capacitance[:, None]
    return x_ss + expm(a * t) @ (x0 - x_ss), x_ss


def evaluate(
    design: dict,
    mission: MissionSpec,
    calibration: model.Calibration | None = None,
    mass_override_g: float | None = None,
) -> Evaluation:
    """Score ``design`` on ``mission``. ``mass_override_g`` replaces the model's motor mass (e.g. CAD mass)."""

    analysis = model.analyze(design, calibration, name="candidate")
    if mass_override_g is not None and analysis.motor is not None:
        analysis.motor = analysis.motor.with_updates(mass_g=mass_override_g)
        analysis.internals["mass_total_g"] = mass_override_g
    rules = manufacturing.dfm_rules(analysis)
    mfg = manufacturing.manufacturability_score(rules)
    cost = manufacturing.unit_cost_usd(analysis)
    ev = Evaluation(design=design, score=0.0, feasible=False, analysis=analysis, rules=rules, cost=cost)
    ev.metrics.update({"manufacturability": mfg, "unit_cost_usd": cost["total"], "mass_g": analysis.mass_g})
    motor = analysis.motor
    if not analysis.valid or motor is None:
        ev.penalties["geometry"] = analysis.violation_total
        ev.score = -3.0 - min(analysis.violation_total, 10.0) * 0.3 + 0.5 * mfg
        return ev

    prop = mission.propeller
    auw_g = (
        mission.dry_mass_g + mission.n_motors * (motor.mass_g + (prop.mass_g or 0.0)) + (mission.battery.mass_g or 0.0)
    )
    hover_thrust = units.grams_force_to_n(auw_g) / mission.n_motors
    pt = Powertrain(motor, prop, mission.battery, mission.esc, mission.n_motors)
    thermal = motor.thermal
    amb = mission.ambient_c

    # Hover at its own steady-state temperature (two fixed-point passes).
    t_w, t_s = amb + 20.0, amb + 15.0
    hover = None
    for _ in range(3):
        hover = pt.solve_for_thrust_direct(hover_thrust, mission.soc_hover, 0.0, t_w, t_s)
        if hover is None:
            break
        rise, _ = _thermal_after(thermal, hover, np.zeros(2), math.inf)
        t_w, t_s = amb + rise[0], amb + rise[1]
    if hover is None:
        # Graded: how much thrust is missing at full throttle?
        full = pt.solve_at_throttle(1.0, mission.soc_hover, 0.0, amb + 20.0, amb + 15.0)
        shortfall = max(0.0, 1.0 - full.thrust_n / hover_thrust)
        ev.penalties["cannot_hover"] = 1.0 + shortfall
        ev.score = -2.0 - 2.0 * shortfall + 0.5 * mfg
        ev.metrics.update({"auw_g": auw_g, "max_thrust_fraction_of_hover": full.thrust_n / hover_thrust})
        return ev
    hover_temps = np.array([t_w - amb, t_s - amb])

    # Punch-out burst from hover temperature; losses re-evaluated at mid-burst temperature.
    temps_mid = hover_temps.copy()
    for _ in range(2):
        punch = pt.solve_at_throttle(1.0, mission.soc_punch, 0.0, amb + temps_mid[0], amb + temps_mid[1])
        end, _ = _thermal_after(thermal, punch, hover_temps, mission.burst_s)
        temps_mid = 0.5 * (hover_temps + end)
    burst_w, burst_s = amb + end[0], amb + end[1]
    thrust_to_weight = punch.thrust_n / hover_thrust

    m = ev.metrics
    m.update(
        {
            "auw_g": auw_g,
            "kv_rpm_per_v": motor.kv_rpm_per_v,
            "r_ll_mohm": motor.r_ll_ohm * 1e3,
            "km_nm_per_sqrt_w": motor.km_nm_per_sqrt_w,
            "hover_throttle": hover.throttle,
            "hover_g_per_w": hover.grams_per_watt,
            "hover_motor_eff": hover.motor.efficiency,
            "hover_i_total_a": hover.i_bus_total_a,
            "hover_winding_c": t_w,
            "thrust_to_weight": thrust_to_weight,
            "punch_i_motor_a": punch.motor.current_a,
            "punch_rpm": punch.rpm,
            "burst_winding_c": burst_w,
            "burst_magnet_c": burst_s,
            "demag_current_a": motor.max_current_a,
            "stator_od_mm": design["stator_od_mm"],
        }
    )
    p = ev.penalties
    p["thrust_to_weight"] = 3.0 * max(
        0.0, (mission.min_thrust_to_weight - thrust_to_weight) / mission.min_thrust_to_weight
    )
    p["winding_temp"] = max(0.0, (burst_w - motor.max_winding_temp_c) / 20.0)
    p["magnet_temp"] = max(0.0, (burst_s - motor.max_magnet_temp_c) / 15.0)
    p["demag_current"] = 2.0 * max(0.0, punch.motor.current_a / max(motor.max_current_a, 1e-6) - 1.0)
    if mission.esc_max_current_a:
        p["esc_current"] = 2.0 * max(0.0, punch.motor.current_a / mission.esc_max_current_a - 1.0)
    if mission.max_stator_od_mm:
        p["stator_size"] = max(0.0, (design["stator_od_mm"] - mission.max_stator_od_mm) / 2.0)
    if mission.max_motor_mass_g:
        p["motor_mass"] = max(0.0, (motor.mass_g - mission.max_motor_mass_g) / 5.0)
    p["hover_throttle_high"] = max(0.0, hover.throttle - 0.5)

    efficiency_term = mission.w_eff * hover.grams_per_watt / mission.ref_g_per_w
    ev.score = (
        efficiency_term + mission.w_mfg * mfg - mission.w_cost * cost["total"] / mission.ref_cost_usd - sum(p.values())
    )
    ev.feasible = all(v == 0.0 for v in p.values()) and not ev.failed_rules
    ev.hover, ev.punch = hover, punch
    return ev


def with_overrides(mission: MissionSpec, **changes) -> MissionSpec:
    return replace(mission, **changes)
