"""Thickness-schedule optimisation of an injection-molded CFRP blade.

Planform, twist, and camber lines stay as designed; only the thickness distribution changes, so the
thesis aerodynamic design is preserved. The objective is the extra profile torque from thickening:

    dQ = sum_i B q_i c_i Cd_i (FF(t'_i/c) / FF(t_i/c) - 1) cos(phi_i) r_i dr_i,   FF = 1 + 2 t/c + 60 (t/c)^4

evaluated with BEMT's own station inflow. That is a first-order estimate: thickness barely changes
lift at fixed camber, and the change in induced torque is neglected.

Frequency placement: the first flap mode follows f(n)^2 = f0^2 + K n^2 (Southwell). Keeping it at
least ``margin`` clear of the kP and (k+1)P lines over the whole rpm band [n_lo, n_hi] requires

    f(n_hi) >= (1 + margin) k n_hi   and   f(n_lo) <= (1 - margin) (k + 1) n_lo,

which has a solution only if the band is narrow enough (``campbell_windows``). The optimiser tries
each window and keeps the cheapest feasible design.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import numpy as np
from scipy.optimize import minimize

from prandtl_prop.cad.sections import BladeSection
from prandtl_prop.manufacturing.injection import MoldingRules, check_molding, hoerner_form_factor, molded_sections, thickness_schedule
from prandtl_prop.structures.beam import solve_beam
from prandtl_prop.structures.loads import map_station_loads
from prandtl_prop.structures.modal_beam import BeamMode, concat_arrays, outline_arrays, rotating_modes, section_arrays

CONTROL_R_R = (0.2, 0.45, 0.7, 1.0)


@dataclass(frozen=True)
class Window:
    lower_harmonic: int  # first flap sits between lower_harmonic P and (lower_harmonic + 1) P
    f0_min_hz: float
    f0_max_hz: float

    @property
    def feasible(self) -> bool:
        return self.f0_min_hz <= self.f0_max_hz


def campbell_windows(southwell_k: float, n_lo: float, n_hi: float, margin: float, harmonics: int = 4) -> list[Window]:
    """Range of non-rotating first-flap frequency f0 that keeps the mode between kP and (k+1)P over the band."""

    windows = []
    for k in range(1, harmonics):
        lower = ((1 + margin) * k * n_hi) ** 2 - southwell_k * n_hi**2
        upper = ((1 - margin) * (k + 1) * n_lo) ** 2 - southwell_k * n_lo**2
        f0_min = math.sqrt(max(lower, 0.0))
        f0_max = math.sqrt(upper) if upper > 0 else -1.0
        windows.append(Window(k, f0_min, f0_max))
    return windows


@dataclass
class Evaluation:
    control: np.ndarray  # thickness scales at the control radii (the design variables)
    scales: np.ndarray  # resulting scale at every station
    sections: list[BladeSection]
    profile_torque_penalty_Nm: float
    flap1_hz: np.ndarray  # over the band
    other_modes_margin: float
    max_stress_Pa: float
    tip_deflection_m: float
    blade_mass_kg: float
    molding: dict[str, object] = field(default_factory=dict)


@dataclass
class OptimisationResult:
    best: Evaluation | None
    window: Window | None
    attempts: list[dict[str, object]]
    baseline: Evaluation
    southwell_k: float
    f0_baseline_hz: float


class ThicknessProblem:
    def __init__(
        self,
        base_sections: Sequence[BladeSection],
        station_rows: Sequence[dict[str, str]],
        *,
        blade_count: int,
        rpm: float,
        rho_air: float,
        material: dict[str, object],
        rules: MoldingRules,
        trailing_edge_thickness_m: float,
        rpm_band: tuple[float, float],
        margin: float,
        stress_safety_factor: float,
        max_tip_deflection_m: float,
        control_r_R: Sequence[float] = CONTROL_R_R,
        radius_m: float,
        model_cushion: float = 0.01,
        root_geometry=None,
    ) -> None:
        self.base = list(base_sections)
        self.rows = list(station_rows)
        self.blade_count, self.rpm, self.rho_air = blade_count, rpm, rho_air
        self.material, self.rules = material, rules
        self.te = trailing_edge_thickness_m
        self.rpm_values = np.linspace(rpm_band[0], rpm_band[1], 7) * rpm
        # The beam model and CalculiX differ by up to ~1% on flap modes, so frequency constraints carry a
        # cushion: the optimiser targets margin + cushion and the solid model confirms at least margin.
        self.margin, self.sf, self.max_tip = margin + model_cushion, stress_safety_factor, max_tip_deflection_m
        self.required_margin = margin
        self.control = list(control_r_R)
        self.r_R = np.array([s.r_m for s in self.base]) / radius_m
        self.base_tc = np.array([s.airfoil.max_thickness for s in self.base])
        self.drag_weight = np.array(
            [
                blade_count
                * 0.5
                * rho_air
                * float(row["Vrel_m_s"]) ** 2
                * float(row["chord_m"])
                * float(row["Cd"])
                * math.cos(math.radians(float(row["phi_deg"])))
                * float(row["r_m"])
                * float(row["dr_m"])
                for row in self.rows
            ]
        )
        self.root_geometry = root_geometry
        self._cache: dict[tuple[float, ...], Evaluation] = {}

    def beam_arrays(self, sections: Sequence[BladeSection]) -> dict[str, np.ndarray]:
        """Section properties for the modal beam, clamped where the hub grips the blade.

        With a root tab, the beam starts at the tab's outer clamp edge and runs through the transition
        outlines (which blend into the thickened root airfoil) before the aero stations. Clamping at
        the first aero station instead overestimated flap 1 by ~14% against CalculiX (159 vs 139 Hz).
        """

        data = section_arrays(sections)
        if self.root_geometry is None:
            return data
        from prandtl_prop.cad.root import root_outlines

        return concat_arrays(outline_arrays(root_outlines(sections[0], self.root_geometry)[1:]), data)

    def schedule(self, control_scales: np.ndarray) -> np.ndarray:
        return thickness_schedule(self.r_R, list(zip(self.control, control_scales, strict=True)))

    def evaluate(self, control_scales: Sequence[float]) -> Evaluation:
        key = tuple(round(float(v), 10) for v in control_scales)
        if key in self._cache:
            return self._cache[key]
        scales = self.schedule(np.asarray(control_scales, dtype=float))
        sections = molded_sections(self.base, scales, self.te)
        new_tc = np.array([s.airfoil.max_thickness for s in sections])
        penalty = float(np.sum(self.drag_weight * (hoerner_form_factor(new_tc) / hoerner_form_factor(self.base_tc) - 1.0)))
        data = self.beam_arrays(sections)
        E, rho = float(self.material["E_Pa"]), float(self.material["density_kg_m3"])
        modes_by_rpm = [
            rotating_modes(data, E_Pa=E, density_kg_m3=rho, omega_rad_s=rpm * math.pi / 30.0, count=3) for rpm in self.rpm_values
        ]
        flap1 = np.array([_first_flap(modes).frequency_hz for modes in modes_by_rpm])
        other = _other_modes_margin(modes_by_rpm, self.rpm_values)
        loads = map_station_loads(
            self.rows,
            sections,
            blade_count=self.blade_count,
            omega_rad_s=self.rpm * math.pi / 30.0,
            rho_air_kg_m3=self.rho_air,
            material_density_kg_m3=rho,
        )
        beam = solve_beam(
            sections,
            loads,
            E_Pa=E,
            nu=float(self.material["nu"]),
            density_kg_m3=rho,
            allowable_stress_Pa=float(self.material["allowable_stress_Pa"]),
        )
        evaluation = Evaluation(
            np.asarray(control_scales, dtype=float).copy(),
            scales,
            sections,
            penalty,
            flap1,
            other,
            float(beam.metrics["max_von_mises_Pa"]),
            float(math.hypot(beam.metrics["tip_deflection_z_m"], beam.metrics["tip_deflection_y_m"])),
            float(beam.metrics["blade_mass_kg"]),
        )
        self._cache[key] = evaluation
        return evaluation

    def constraints(self, control_scales: Sequence[float], window: Window) -> np.ndarray:
        """All entries must be >= 0. Each is normalised to order 1."""

        e = self.evaluate(control_scales)
        n = self.rpm_values / 60.0
        k = window.lower_harmonic
        sections = e.sections
        t_max = np.array([s.airfoil.max_thickness * s.chord_m for s in sections])
        gradient = np.abs(np.diff(t_max) / np.diff([s.r_m for s in sections]))
        allowable = float(self.material["allowable_stress_Pa"]) / self.sf
        return np.concatenate(
            [
                e.flap1_hz / (k * n) - (1.0 + self.margin),
                (1.0 - self.margin) - e.flap1_hz / ((k + 1) * n),
                [e.other_modes_margin - self.margin],
                [1.0 - e.max_stress_Pa / allowable],
                [1.0 - e.tip_deflection_m / self.max_tip],
                1.0 - t_max / self.rules.max_section_thickness_m,
                1.0 - gradient / self.rules.max_thickness_gradient,
            ]
        )


def _first_flap(modes: Sequence[BeamMode]) -> BeamMode:
    return next((mode for mode in modes if mode.kind == "flap"), modes[0])


def _other_modes_margin(modes_by_rpm: Sequence[Sequence[BeamMode]], rpm_values: Sequence[float], harmonics: int = 4) -> float:
    worst = math.inf
    for modes, rpm in zip(modes_by_rpm, rpm_values, strict=True):
        first = _first_flap(modes)
        for mode in modes:
            if mode is first:
                continue
            for k in range(1, harmonics + 1):
                worst = min(worst, abs(mode.frequency_hz - k * rpm / 60.0) / (k * rpm / 60.0))
    return worst


def optimise(problem: ThicknessProblem, *, bounds: tuple[float, float] = (1.0, 2.0), uniform_steps: int = 41) -> OptimisationResult:
    """Uniform-scale scan, a 2-D scan of linear root-to-tip profiles, then COBYLA polish (derivative-free).

    The lower bound defaults to 1.0: manufacturing may add thickness but not remove it, because thinning
    the designed airfoils changes Cl_max and stall margin beyond what the form-factor drag model captures.

    The constraints are not smooth (mode identities and min-over-harmonics margins switch), which
    defeats gradient-based SLSQP. Flap frequency rises monotonically with overall thickness, so the
    uniform scan either finds a feasible start or shows that no uniform scaling works.
    """

    count = len(problem.control)
    baseline = problem.evaluate(np.ones(count))
    material = problem.material
    still = rotating_modes(
        problem.beam_arrays(baseline.sections),
        E_Pa=float(material["E_Pa"]),
        density_kg_m3=float(material["density_kg_m3"]),
        omega_rad_s=0.0,
        count=3,
    )
    f0 = _first_flap(still).frequency_hz
    flap_mid = float(np.interp(problem.rpm, problem.rpm_values, baseline.flap1_hz))
    southwell = (flap_mid**2 - f0**2) / (problem.rpm / 60.0) ** 2
    windows = campbell_windows(southwell, problem.rpm_values[0] / 60.0, problem.rpm_values[-1] / 60.0, problem.margin)
    uniform = np.linspace(bounds[0], bounds[1], uniform_steps)
    attempts: list[dict[str, object]] = []
    best: tuple[Evaluation, Window] | None = None
    for window in windows:
        record: dict[str, object] = {
            "window": f"{window.lower_harmonic}P-{window.lower_harmonic + 1}P",
            "f0_range_hz": [window.f0_min_hz, window.f0_max_hz],
        }
        if not window.feasible:
            record["result"] = "infeasible for this rpm band: no f0 satisfies both frequency margins"
            attempts.append(record)
            continue
        # Candidates: uniform scales, plus linear root-to-tip profiles. A thick root and thin tip raise flap
        # frequency for less drag than uniform thickening, and can be feasible where uniform scaling is not.
        fraction = (np.asarray(problem.control) - problem.control[0]) / (problem.control[-1] - problem.control[0])
        candidates = [np.full(count, scale) for scale in uniform]
        grid = np.linspace(bounds[0], bounds[1], 21)
        candidates += [root + (tip - root) * fraction for root in grid for tip in grid]
        feasible = [c for c in candidates if np.min(problem.constraints(c, window)) >= 0.0]
        if not feasible:
            least = max(candidates, key=lambda c: np.min(problem.constraints(c, window)))
            record["result"] = (
                f"no feasible uniform or linear thickness profile in {bounds} (least violating: {np.round(least, 2).tolist()}); "
                "stress, deflection, or molding limits bind"
            )
            attempts.append(record)
            continue
        start = min(feasible, key=lambda c: problem.evaluate(c).profile_torque_penalty_Nm)
        feasible_uniform = [c[0] for c in feasible if np.ptp(c) == 0.0]
        start_scale = (
            min(feasible_uniform, key=lambda v: problem.evaluate([v] * count).profile_torque_penalty_Nm)
            if feasible_uniform
            else float("nan")
        )
        cons = [{"type": "ineq", "fun": lambda x, w=window: problem.constraints(x, w)}]
        cons += [{"type": "ineq", "fun": lambda x, i=i: x[i] - bounds[0]} for i in range(count)]
        cons += [{"type": "ineq", "fun": lambda x, i=i: bounds[1] - x[i]} for i in range(count)]
        # COBYLA is path dependent, so polish from the cheapest few feasible starts and keep the best
        # result that validates exactly as returned.
        ranked = sorted(feasible, key=lambda c: problem.evaluate(c).profile_torque_penalty_Nm)
        x, better = start, False
        for x0 in ranked[:5]:
            solution = minimize(
                lambda v: problem.evaluate(v).profile_torque_penalty_Nm * 1.0e3,
                x0=x0,
                method="COBYLA",
                constraints=cons,
                options={"rhobeg": 0.08, "maxiter": 600, "tol": 1.0e-6},
            )
            clipped = np.clip(solution.x, *bounds)
            if np.min(problem.constraints(clipped, window)) >= 0.0 and (
                problem.evaluate(clipped).profile_torque_penalty_Nm < problem.evaluate(x).profile_torque_penalty_Nm
            ):
                x, better = clipped, True
        found = problem.evaluate(x)
        record.update(
            {
                "result": "feasible",
                "best_uniform_scale": float(start_scale),
                "best_uniform_penalty_Nm": problem.evaluate([start_scale] * count).profile_torque_penalty_Nm if feasible_uniform else None,
                "cobyla_improved": bool(better),
                "profile_torque_penalty_Nm": found.profile_torque_penalty_Nm,
                "control_scales": [float(v) for v in x],
            }
        )
        attempts.append(record)
        if best is None or found.profile_torque_penalty_Nm < best[0].profile_torque_penalty_Nm:
            best = (found, window)
    if best is not None:
        best[0].molding = check_molding(best[0].sections, problem.rules)
    baseline.molding = check_molding(baseline.sections, problem.rules)
    return OptimisationResult(best[0] if best else None, best[1] if best else None, attempts, baseline, float(southwell), float(f0))


def with_material(design_dict: dict[str, object], material: dict[str, object]) -> dict[str, object]:
    structure = dict(design_dict.get("structure", {}))
    structure["material"] = {key: material[key] for key in ("name", "E_Pa", "nu", "density_kg_m3", "allowable_stress_Pa")}
    return {**design_dict, "structure": structure}


__all__ = ["CONTROL_R_R", "ThicknessProblem", "Window", "campbell_windows", "optimise", "replace", "with_material"]
