"""Discrete-event simulation of the RL2407 manufacturing and assembly line.

Flow (station numbers as in ``assembly_line.STATIONS``)::

    supplier kit lots --(lead time)--> received --S1 goods-in (inspect fraction f)--> stock
    stator kit --S2+S3 wind (hand, or CNC load/run/unload)--> S4 terminate --> S5 R/hipot test
        --> S6 oven batch (<= 12, 3 h cure) --> S7 base subassy (+ base kit) --> 30 min cure --> base ready
    rotor kit --S8 rotor subassy (+ optional gauss pole check)--> 30 min cure --> S9 balance (n runs)
    base ready + rotor ready + final kit --S10 final assy + EOL test--> S11 pack --> finished goods --> ship

Resources: a pool of operators on a shift calendar (7 productive h/day), each assigned to a zone by
the controller. Machines: CNC winder (Weibull wear-out failures, jams), batch oven, balancer and a test
bench shared by S5 and S10. Cure delays need no resource. Every unit carries as-built physics
(``sim_physics``): tolerance-driven Kv/R, turn-count errors, unbalance, and latent defects. Tests apply
the real acceptance specs with instrument noise, so defects can escape to customers.

Units are minutes. Times not taken from the line model are labelled assumptions in ``SimConfig``.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np

from bldc_motor.design import manufacturing_package as mp
from bldc_motor.production import assembly_line as al
from bldc_motor.production import sim_physics as sp

ZONES = ("stator", "oven_base", "rotor", "final", "flex")
OVEN_RULES = ("hold", "start_any", "start_half", "start_full")
ORDER_QTY = (0, 25, 50, 100)
INSPECT_FRACTIONS = (0.0, 0.1, 1.0)
LOADED_TEST_RATES = (1 / 50, 1 / 10, 1.0)
ROTOR_CAPS = (2, 4, 8, 16)  # kanban: rotors in the system (S8 start .. consumed at S10)
STATOR_CAPS = (12, 24, 36, 48)  # CONWIP: stator-bearing units in the system


def _station_min(key: str) -> float:
    return next(s.labour_min for s in al.STATIONS if s.key == key)


@dataclass
class SimConfig:
    days: int = 20
    shift_min: float = 420.0  # productive minutes per day (7 h)
    paid_hours_per_day: float = 8.0
    operators: int = 2
    labour_rate: float = al.LABOUR_RATE_USD_H
    cnc_winder_installed: bool = True
    cv: float = 0.2  # coefficient of variation of manual task times (lognormal)
    learning: bool = True  # first units of each task slower: x (1 + 0.5 exp(-n/40))
    deterministic: bool = False  # no variability, defects, breakdowns (validation mode)
    # station labour from the line model; CNC timing from the winder model
    t_s1_per_kit: float = 0.2
    t_s1_inspect_per_kit: float = _station_min("S1")
    t_s2_s3_hand: float = _station_min("S2") + _station_min("S3")
    t_cnc_load: float = 0.5 + 0.5 + 3 * 0.3  # prep + load + three phase anchors
    t_cnc_machine: float = 1.32
    t_cnc_unload: float = 0.3
    t_s4: float = _station_min("S4")
    t_s5: float = _station_min("S5")
    t_s6_per_stator: float = _station_min("S6")
    t_s7: float = _station_min("S7")
    t_s8: float = _station_min("S8")
    t_gauss_check: float = 0.5
    t_s9_setup: float = 1.0
    t_s9_per_run: float = 1.5
    t_s10: float = _station_min("S10")
    t_loaded_test: float = 3.0
    t_s11: float = _station_min("S11")
    t_rework_solder: float = 5.0
    oven_capacity: int = 12
    oven_cure_min: float = 180.0
    bond_cure_min: float = 30.0
    # CNC winder reliability (assumptions: needle/guide wear-out, jams)
    winder_weibull_scale_h: float = 20.0  # machine run-hours (~900 stators); assumption
    winder_weibull_shape: float = 2.0
    winder_repair_min: float = 90.0
    winder_pm_min: float = 30.0
    winder_jam_prob: float = 0.005
    winder_jam_fix_min: float = 3.0
    # defect rates (assumptions until the pilot measures them)
    p_turn_error_hand: float = 0.004  # per coil, +-1 turn
    p_turn_error_cnc: float = 0.0002
    p_insulation_hand: float = 0.005
    p_insulation_cnc: float = 0.003
    p_solder: float = 0.01
    p_pole_order: float = 0.005
    p_pole_order_checked: float = 0.0005
    p_magnet_chip: float = 0.003
    p_chip_seen_s8: float = 0.6
    p_chip_seen_eol: float = 0.3
    p_bearing_damage: float = 0.003
    p_bearing_seen_eol: float = 0.7
    p_bearing_seen_loaded: float = 0.25
    p_incoming: dict = field(default_factory=lambda: {"stator": 0.01, "rotor": 0.005, "base": 0.005})
    p_incoming_detect: float = 0.9
    # supply, demand, money (assumptions)
    kit_cost: float = sum(v[1] for v in al.BOM.values())
    order_fee: float = 80.0
    lead_time_days: float = 5.0
    lead_time_cv: float = 0.3
    initial_kits: int = 60
    price: float = 40.0
    warranty_cost: float = 60.0
    backlog_penalty_per_day: float = 1.0
    holding_rate_per_day: float = 0.001
    demand_orders_per_day: float = 1.0
    order_size: tuple = (10, 40)
    due_days: float = 7.0
    spec: sp.TestSpec | None = None
    seed: int = 0


@dataclass
class Controls:
    zones: list[int] = field(default_factory=lambda: [4, 4])
    oven_rule: int = 2
    order_qty: int = 0
    inspect: int = 1
    gauss_check: int = 1
    loaded_test: int = 0
    use_cnc: int = 1
    preventive_maintenance: int = 0
    rotor_cap: int = 1
    stator_cap: int = 2


@dataclass
class Stator:
    sid: int
    turns: tuple
    tol: dict
    solder_bad: bool
    insulation_bad: bool
    incoming_bad: bool
    t_start: float
    rework_min: float = 0.0


@dataclass
class Rotor:
    pole_error: bool
    chip: bool
    incoming_bad: bool


@dataclass
class Motor:
    stator: Stator
    bearing_bad: bool
    kv: float
    defective: bool


class LineSim:
    TASK_ZONES = {
        "S1": {0, 1, 4}, "S3_hand": {0, 4}, "S3_load": {0, 4}, "S3_unload": {0, 4}, "S4": {0, 4}, "S5": {0, 4},
        "S6_load": {1, 4}, "S6_unload": {1, 4}, "S7": {1, 4}, "S8": {2, 4}, "S9": {2, 3, 4}, "S10": {3, 4}, "S11": {3, 4},
    }  # fmt: skip
    PRIORITY = (
        "S11",
        "S10",
        "S9",
        "S6_unload",
        "S3_unload",
        "S7",
        "S8",
        "S6_load",
        "S5",
        "S4",
        "S3_load",
        "S3_hand",
        "S1",
    )

    def __init__(self, config: SimConfig | None = None, design_path: str | None = None):
        self.cfg = cfg = config or SimConfig()
        design, _ = mp.load_design(
            design_path
            or (
                mp.Path(__file__).resolve().parents[3]
                / "data"
                / "motor_designs"
                / "rl_5in_freestyle_6s_v1"
                / "design.json"
            )
        )
        self.sens = sp.sensitivity(design)
        self.spec = cfg.spec or sp.TestSpec(kv_tol=max(0.03, 3.0 * sp.kv_sigma(self.sens)))
        # Separate streams (common random numbers): demand and supply do not depend on shop-floor decisions,
        # so policies compared on the same seed face identical orders and delivery delays.
        self.rng = np.random.default_rng([cfg.seed, 0])
        self.rng_demand = np.random.default_rng([cfg.seed, 1])
        self.rng_supply = np.random.default_rng([cfg.seed, 2])
        self.t = 0.0
        self.events: list = []
        self._seq = 0
        self.controls = Controls(zones=[4] * cfg.operators)
        n = self.sens.turns_per_phase
        self.nominal_turns = (n, n, n)
        # buffers
        self.received = 0
        self.stock = {
            "stator": cfg.initial_kits,
            "base": cfg.initial_kits,
            "rotor": cfg.initial_kits,
            "final": cfg.initial_kits,
        }
        self.stock_inspected_frac = {k: 1.0 for k in self.stock}
        self.to_term: list[Stator] = []
        self.to_test1: list[Stator] = []
        self.to_oven: list[Stator] = []
        self.cured: list[Stator] = []
        self.base_ready: list[tuple] = []
        self.to_balance: list[Rotor] = []
        self.rotor_ready: list[Rotor] = []
        self.to_pack: list[Motor] = []
        self.finished: list[Motor] = []
        self.orders: list[list] = []  # [due_time, qty_remaining]
        # machines
        self.winder = {"state": "idle", "age_min": 0.0, "fail_at_min": self._draw_failure(), "job": None}
        self.oven = {"state": "idle", "batch": []}
        self.balancer_busy = False
        self.bench_busy = False
        # operators
        self.ops = [{"busy_until": 0.0, "busy": False, "busy_min": 0.0} for _ in range(cfg.operators)]
        self.done_count: dict[str, int] = {}
        self._sid = 0
        self.rotors_in_system = 0
        self.in_transit = 0
        self.wip_area = 0.0
        self._last_wip_t = 0.0
        self.k = {  # KPIs and money
            "revenue": 0.0, "material": 0.0, "order_fees": 0.0, "labour": 0.0, "holding": 0.0, "backlog": 0.0, "warranty": 0.0,
            "started": 0, "shipped": 0, "shipped_defective": 0, "scrap_stator": 0, "scrap_rotor": 0, "scrap_motor": 0,
            "rework": 0, "s5_fail": 0, "eol_fail": 0, "eol_tested": 0, "s5_tested": 0, "flow_time_sum": 0.0, "completed": 0,
            "winder_failures": 0, "winder_jams": 0, "demand": 0, "late_units_days": 0.0, "kits_ordered": 0,
            "incoming_rejected": 0, "balance_runs": 0, "turn_errors_made": 0, "turn_errors_caught": 0,
            "stators_entered": 0, "stators_departed": 0, "sojourn_sum": 0.0, "winder_pm": 0,
        }  # fmt: skip
        self.day = 0
        self._schedule(0.0, "shift_start", None)
        self._schedule(0.0, "demand", None)

    # ---- event machinery -------------------------------------------------------------------
    def _schedule(self, t: float, kind: str, payload) -> None:
        self._seq += 1
        heapq.heappush(self.events, (t, self._seq, kind, payload))

    def _draw_failure(self) -> float:
        c = self.cfg
        if c.deterministic:
            return math.inf
        return c.winder_weibull_scale_h * 60.0 * (-math.log(1.0 - self.rng.random())) ** (1.0 / c.winder_weibull_shape)

    def _dur(self, nominal: float, task: str) -> float:
        c = self.cfg
        n = self.done_count.get(task, 0)
        learn = 1.0 + 0.5 * math.exp(-n / 40.0) if c.learning and not c.deterministic else 1.0
        if c.deterministic or c.cv <= 0:
            return nominal * learn
        s2 = math.log(1.0 + c.cv**2)
        return nominal * learn * float(self.rng.lognormal(-0.5 * s2, math.sqrt(s2)))

    def _p(self, p: float) -> bool:
        return (not self.cfg.deterministic) and self.rng.random() < p

    def shift_bounds(self, day: int) -> tuple[float, float]:
        start = day * 1440.0
        return start, start + self.cfg.shift_min

    def in_shift(self, t: float) -> bool:
        s, e = self.shift_bounds(int(t // 1440.0))
        return s <= t < e

    def wip(self) -> int:
        """Stator-bearing units in the system (entered at winding, leave as a packed motor or scrap)."""

        return self.k["stators_entered"] - self.k["stators_departed"]

    _in_process = 0

    def _track_wip(self) -> None:
        self.wip_area += self.wip() * (self.t - self._last_wip_t)
        self._last_wip_t = self.t

    # ---- run ----------------------------------------------------------------------------
    def run_until(self, t_end: float) -> None:
        while self.events and self.events[0][0] <= t_end:
            t, _, kind, payload = heapq.heappop(self.events)
            self.t = t
            self._track_wip()
            getattr(self, f"_on_{kind}")(payload)
            self._dispatch()
        self.t = t_end
        self._track_wip()

    def run(self, controls: Controls | None = None) -> dict:
        if controls:
            self.controls = controls
        self.run_until(self.cfg.days * 1440.0)
        return self.kpis()

    # ---- event handlers --------------------------------------------------------------------
    def _on_shift_start(self, _payload) -> None:
        self.day = int(self.t // 1440.0)
        self.k["labour"] += self.cfg.operators * self.cfg.paid_hours_per_day * self.cfg.labour_rate
        inventory_value = self.cfg.kit_cost * (min(self.stock.values()) + self.received)
        self.k["holding"] += inventory_value * self.cfg.holding_rate_per_day
        late = sum(q for due, q in self.orders if due < self.t)
        self.k["backlog"] += late * self.cfg.backlog_penalty_per_day
        self.k["late_units_days"] += late
        self._schedule((self.day + 1) * 1440.0, "shift_start", None)

    def _on_demand(self, _payload) -> None:
        c = self.cfg
        n_orders = c.demand_orders_per_day if c.deterministic else self.rng_demand.poisson(c.demand_orders_per_day)
        for _ in range(int(n_orders)):
            qty = (
                (sum(c.order_size) // 2)
                if c.deterministic
                else int(self.rng_demand.integers(c.order_size[0], c.order_size[1] + 1))
            )
            self.orders.append([self.t + c.due_days * 1440.0, qty])
            self.k["demand"] += qty
        self.orders.sort()
        self._schedule(self.t + 1440.0, "demand", None)

    def _on_kits(self, qty) -> None:
        self.received += qty
        self.in_transit -= qty

    def _on_op_done(self, payload) -> None:
        i, task, item = payload
        op = self.ops[i]
        op["busy"] = False
        self._in_process -= 1
        self.done_count[task] = self.done_count.get(task, 0) + 1
        getattr(self, f"_finish_{task}")(item)

    def _on_winder_done(self, stator) -> None:
        self.winder["state"] = "done"
        self.winder["job"] = stator

    def _on_winder_repaired(self, _payload) -> None:
        self.winder.update(state="idle", age_min=0.0, fail_at_min=self._draw_failure())

    def _on_oven_done(self, _payload) -> None:
        self.oven["state"] = "done"

    def _on_cured(self, payload) -> None:
        kind, item = payload
        if kind == "base":
            self.base_ready.append(item)
        else:
            self.to_balance.append(item)
        self._in_process -= 1

    # ---- dispatching -------------------------------------------------------------------------
    def _feasible(self, task: str) -> bool:
        c, ctl = self.cfg, self.controls
        cnc = c.cnc_winder_installed and ctl.use_cnc
        if task == "S1":
            return self.received > 0
        stator_room = self.wip() < STATOR_CAPS[ctl.stator_cap]
        if task == "S3_hand":
            return not cnc and self.stock["stator"] > 0 and stator_room
        if task == "S3_load":
            return cnc and self.winder["state"] == "idle" and self.stock["stator"] > 0 and stator_room
        if task == "S3_unload":
            return self.winder["state"] == "done"
        if task == "S4":
            return bool(self.to_term)
        if task == "S5":
            return bool(self.to_test1) and not self.bench_busy
        if task == "S6_unload":
            return self.oven["state"] == "done"
        if task == "S6_load":
            if self.oven["state"] != "idle" or not self.to_oven:
                return False
            need = {0: math.inf, 1: 1, 2: c.oven_capacity // 2, 3: c.oven_capacity}[ctl.oven_rule]
            # never leave stators waiting past the end of the shift when they could cure overnight
            _, end = self.shift_bounds(int(self.t // 1440.0))
            closing = end - self.t < 30.0 and ctl.oven_rule != 0
            return len(self.to_oven) >= need or closing
        if task == "S7":
            return bool(self.cured) and self.stock["base"] > 0
        if task == "S8":
            return self.stock["rotor"] > 0 and self.rotors_in_system < ROTOR_CAPS[ctl.rotor_cap]
        if task == "S9":
            return bool(self.to_balance) and not self.balancer_busy
        if task == "S10":
            return bool(self.base_ready) and bool(self.rotor_ready) and self.stock["final"] > 0 and not self.bench_busy
        if task == "S11":
            return bool(self.to_pack)
        return False

    NOMINAL = {
        "S1": lambda self: min(10, self.received) * (self.cfg.t_s1_per_kit + INSPECT_FRACTIONS[self.controls.inspect] * self.cfg.t_s1_inspect_per_kit),
        "S3_hand": lambda self: self.cfg.t_s2_s3_hand,
        "S3_load": lambda self: self.cfg.t_cnc_load,
        "S3_unload": lambda self: self.cfg.t_cnc_unload,
        "S4": lambda self: self.cfg.t_s4,
        "S5": lambda self: self.cfg.t_s5 + self.cfg.t_rework_solder,
        "S6_load": lambda self: self.cfg.t_s6_per_stator * min(len(self.to_oven), self.cfg.oven_capacity),
        "S6_unload": lambda self: 0.2 * len(self.oven["batch"]),
        "S7": lambda self: self.cfg.t_s7,
        "S8": lambda self: self.cfg.t_s8 + self.cfg.t_gauss_check,
        "S9": lambda self: self.cfg.t_s9_setup + 3 * self.cfg.t_s9_per_run,
        "S10": lambda self: self.cfg.t_s10 + self.cfg.t_loaded_test,
        "S11": lambda self: self.cfg.t_s11,
    }  # fmt: skip

    def _dispatch(self) -> None:
        if not self.in_shift(self.t):
            return
        _, end = self.shift_bounds(int(self.t // 1440.0))
        w = self.winder
        if w["state"] == "idle" and not self.cfg.deterministic:
            if w["age_min"] >= w["fail_at_min"]:
                w["state"] = "broken"
                self.k["winder_failures"] += 1
                self._schedule(self.t + self.cfg.winder_repair_min, "winder_repaired", None)
            elif self.controls.preventive_maintenance:
                w["state"] = "maintenance"
                self.k["winder_pm"] += 1
                self._schedule(self.t + self.cfg.winder_pm_min, "winder_repaired", None)
        self.controls.preventive_maintenance = 0
        for i, op in enumerate(self.ops):
            if op["busy"]:
                continue
            zone = self.controls.zones[i] if i < len(self.controls.zones) else 4
            for task in self.PRIORITY:
                if zone not in self.TASK_ZONES[task] or not self._feasible(task):
                    continue
                if self.t + 1.3 * self.NOMINAL[task](self) > end + 5.0:  # cannot finish this shift
                    continue
                duration, item = getattr(self, f"_start_{task}")()
                op["busy"] = True
                op["busy_min"] += duration
                self._in_process += 1
                self._schedule(self.t + duration, "op_done", (i, task, item))
                break

    # ---- task start / finish ---------------------------------------------------------------
    def _incoming_defect(self, kind: str) -> bool:
        """Is a (reserved) kit component latently defective? Inspection at S1 removes most bad parts."""

        q = self.cfg.p_incoming[kind] if kind in self.cfg.p_incoming else 0.0
        q_eff = q * (1.0 - self.stock_inspected_frac[kind] * self.cfg.p_incoming_detect)
        return self._p(q_eff)

    def _new_stator(self, cnc: bool) -> Stator:
        c = self.cfg
        p_turn = c.p_turn_error_cnc if cnc else c.p_turn_error_hand
        turns = list(self.nominal_turns)
        for phase in range(3):
            for _coil in range(4):
                if self._p(p_turn):
                    turns[phase] += -1 if self.rng.random() < 0.6 else 1
        if tuple(turns) != self.nominal_turns:
            self.k["turn_errors_made"] += 1
        tol = {k: 0.0 for k in (*sp.TOL_KEYS, "br", "r")} if c.deterministic else sp.sample_tolerances(self.rng)
        self._sid += 1
        self.k["started"] += 1
        self.stock["stator"] -= 1
        self.k["stators_entered"] += 1
        return Stator(
            self._sid,
            tuple(turns),
            tol,
            False,
            self._p(c.p_insulation_cnc if cnc else c.p_insulation_hand),
            self._incoming_defect("stator"),
            self.t,
        )

    def _start_S1(self):
        n = min(10, self.received)
        self.received -= n
        f = INSPECT_FRACTIONS[self.controls.inspect]
        return self._dur(n * (self.cfg.t_s1_per_kit + f * self.cfg.t_s1_inspect_per_kit), "S1"), n

    def _finish_S1(self, n):
        f = INSPECT_FRACTIONS[self.controls.inspect]
        for kind in self.stock:
            q = self.cfg.p_incoming.get(kind, 0.0)
            rejected = 0 if self.cfg.deterministic else int(self.rng.binomial(n, q * f * self.cfg.p_incoming_detect))
            total = self.stock[kind] + n
            self.stock_inspected_frac[kind] = (self.stock_inspected_frac[kind] * self.stock[kind] + f * n) / max(
                total, 1
            )
            self.stock[kind] += n - rejected
            self.k["incoming_rejected"] += rejected
        # rejected parts are returned for credit (supplier replaces them in the next lot)

    def _start_S3_hand(self):
        return self._dur(self.cfg.t_s2_s3_hand, "S3_hand"), self._new_stator(cnc=False)

    def _finish_S3_hand(self, stator):
        self.to_term.append(stator)

    def _start_S3_load(self):
        self.winder["state"] = "loading"
        return self._dur(self.cfg.t_cnc_load, "S3_load"), self._new_stator(cnc=True)

    def _finish_S3_load(self, stator):
        c = self.cfg
        run = c.t_cnc_machine
        if self._p(c.winder_jam_prob):
            run += c.winder_jam_fix_min
            self.k["winder_jams"] += 1
        self.winder["state"] = "running"
        self.winder["job"] = stator
        self.winder["age_min"] += run
        self._schedule(self.t + run, "winder_done", stator)

    def _start_S3_unload(self):
        self.winder["state"] = "unloading"
        return self._dur(self.cfg.t_cnc_unload, "S3_unload"), self.winder["job"]

    def _finish_S3_unload(self, stator):
        self.winder.update(state="idle", job=None)
        self.to_term.append(stator)

    def _start_S4(self):
        return self._dur(self.cfg.t_s4, "S4"), self.to_term.pop(0)

    def _finish_S4(self, stator):
        stator.solder_bad = self._p(self.cfg.p_solder)
        self.to_test1.append(stator)

    def _start_S5(self):
        self.bench_busy = True
        stator = self.to_test1.pop(0)
        extra, stator.rework_min = stator.rework_min, 0.0
        return self._dur(self.cfg.t_s5 + extra, "S5"), stator

    def _finish_S5(self, stator):
        self.bench_busy = False
        self.k["s5_tested"] += 1
        solder = 0.1 * self.sens.r0 if stator.solder_bad else 0.0  # a cold joint adds ~10 % of R_ll
        r_pairs = sp.true_r_pairs(self.sens, stator.turns, stator.tol, solder)
        r_ok = sp.r_test(self.sens, r_pairs, self.spec, self.rng) if not self.cfg.deterministic else True
        hipot_ok = not (stator.insulation_bad or stator.incoming_bad) or not self._p(self.spec.hipot_detect)
        if r_ok and hipot_ok:
            self.to_oven.append(stator)
            return
        self.k["s5_fail"] += 1
        if stator.turns != self.nominal_turns:
            self.k["turn_errors_caught"] += 1
        if stator.solder_bad and stator.turns == self.nominal_turns and hipot_ok:
            stator.solder_bad = False  # re-solder (operator time added to the retest) and retest
            stator.rework_min = self.cfg.t_rework_solder
            self.k["rework"] += 1
            self.to_test1.insert(0, stator)
            return
        self.k["scrap_stator"] += 1
        self._stator_leaves(stator)

    def _start_S6_load(self):
        batch = self.to_oven[: self.cfg.oven_capacity]
        del self.to_oven[: len(batch)]
        self.oven.update(state="loading", batch=batch)
        return self._dur(self.cfg.t_s6_per_stator * len(batch), "S6_load"), batch

    def _finish_S6_load(self, batch):
        self.oven["state"] = "curing"
        self._schedule(self.t + self.cfg.oven_cure_min, "oven_done", None)

    def _start_S6_unload(self):
        self.oven["state"] = "unloading"
        return self._dur(0.2 * len(self.oven["batch"]), "S6_unload"), self.oven["batch"]

    def _finish_S6_unload(self, batch):
        self.cured.extend(batch)
        self.oven.update(state="idle", batch=[])

    def _start_S7(self):
        self.stock["base"] -= 1  # reserve the base kit
        return self._dur(self.cfg.t_s7, "S7"), self.cured.pop(0)

    def _finish_S7(self, stator):
        bad_base = self._incoming_defect("base")
        bearing = self._p(self.cfg.p_bearing_damage) or bad_base
        self._in_process += 1
        self._schedule(self.t + self.cfg.bond_cure_min, "cured", ("base", (stator, bearing)))

    def _start_S8(self):
        check = self.controls.gauss_check
        self.stock["rotor"] -= 1  # reserve the rotor kit
        self.rotors_in_system += 1
        return self._dur(self.cfg.t_s8 + (self.cfg.t_gauss_check if check else 0.0), "S8"), check

    def _finish_S8(self, check):
        c = self.cfg
        incoming = self._incoming_defect("rotor")
        pole = self._p(c.p_pole_order_checked if check else c.p_pole_order)
        chip = self._p(c.p_magnet_chip)
        if chip and self._p(c.p_chip_seen_s8):
            self.k["scrap_rotor"] += 1
            self.rotors_in_system -= 1
            return
        rotor = Rotor(pole_error=pole, chip=chip, incoming_bad=incoming)
        self._in_process += 1
        self._schedule(self.t + c.bond_cure_min, "cured", ("rotor", rotor))

    def _start_S9(self):
        self.balancer_busy = True
        rotor = self.to_balance.pop(0)
        if self.cfg.deterministic:
            runs, ok = 2, True
        else:
            runs, _, ok = sp.balance(self.sens, self.spec, self.rng)
        self.k["balance_runs"] += runs
        return self._dur(self.cfg.t_s9_setup + runs * self.cfg.t_s9_per_run, "S9"), (rotor, ok)

    def _finish_S9(self, item):
        rotor, ok = item
        self.balancer_busy = False
        if ok:
            self.rotor_ready.append(rotor)
        else:
            self.k["scrap_rotor"] += 1
            self.rotors_in_system -= 1

    def _start_S10(self):
        self.bench_busy = True
        base = self.base_ready.pop(0)
        rotor = self.rotor_ready.pop(0)
        self.rotors_in_system -= 1
        self.stock["final"] -= 1
        loaded = (not self.cfg.deterministic) and self.rng.random() < LOADED_TEST_RATES[self.controls.loaded_test]
        t = self.cfg.t_s10 + (self.cfg.t_loaded_test if loaded else 0.0)
        return self._dur(t, "S10"), (base, rotor, loaded)

    def _finish_S10(self, item):
        (stator, bearing_bad), rotor, loaded = item
        self.bench_busy = False
        c = self.cfg
        self.k["eol_tested"] += 1
        tol = stator.tol
        kv = sp.true_kv(self.sens, stator.turns, tol)
        solder = 0.1 * self.sens.r0 if stator.solder_bad else 0.0
        r_pairs = sp.true_r_pairs(self.sens, stator.turns, tol, solder)
        if c.deterministic:
            passed = True
        else:
            passed = (
                sp.r_test(self.sens, r_pairs, self.spec, self.rng)
                and sp.kv_test(self.sens, kv, self.spec, self.rng)
                and not rotor.pole_error  # a motor with a pole-order error does not commutate
                and not ((stator.insulation_bad or stator.incoming_bad) and self._p(self.spec.hipot_detect))
                and not (rotor.chip and self._p(c.p_chip_seen_eol))
                and not (bearing_bad and self._p(c.p_bearing_seen_eol + (c.p_bearing_seen_loaded if loaded else 0.0)))
                and not (rotor.incoming_bad and self._p(0.5))
            )
        if not passed:
            self.k["eol_fail"] += 1
            self.k["scrap_motor"] += 1
            self._stator_leaves(stator)
            return
        defective = (
            stator.turns != self.nominal_turns
            or stator.insulation_bad
            or stator.incoming_bad
            or stator.solder_bad
            or rotor.chip
            or rotor.incoming_bad
            or bearing_bad
            or abs(kv / self.sens.kv0 - 1.0) > self.spec.kv_tol
        )
        motor = Motor(stator=stator, bearing_bad=bearing_bad, kv=kv, defective=defective)
        self.to_pack.append(motor)

    def _start_S11(self):
        return self._dur(self.cfg.t_s11, "S11"), self.to_pack.pop(0)

    def _finish_S11(self, motor):
        self.k["completed"] += 1
        self.k["flow_time_sum"] += self.t - motor.stator.t_start
        self._stator_leaves(motor.stator)
        self.finished.append(motor)
        self._ship()

    def _ship(self) -> None:
        while self.finished and self.orders:
            order = self.orders[0]
            motor = self.finished.pop(0)
            order[1] -= 1
            self.k["shipped"] += 1
            self.k["revenue"] += self.cfg.price
            if motor.defective:
                self.k["shipped_defective"] += 1
                self.k["warranty"] += self.cfg.warranty_cost
            if order[1] <= 0:
                self.orders.pop(0)

    def count_stators_physically(self) -> int:
        """Independent count of stator-bearing units by walking buffers, machines and pending events."""

        n = len(self.to_term) + len(self.to_test1) + len(self.to_oven) + len(self.oven["batch"]) + len(self.cured)
        n += len(self.base_ready) + len(self.to_pack)
        if self.winder["job"] is not None and self.winder["state"] in ("done", "unloading"):
            n += 1
        for _, _, kind, payload in self.events:
            if kind == "winder_done":
                n += 1
            elif kind == "cured" and payload[0] == "base":
                n += 1
            elif kind == "op_done":
                task = payload[1]
                if task in ("S3_hand", "S3_load", "S4", "S5", "S7", "S10", "S11"):
                    n += 1
                elif task == "S3_unload":
                    pass  # counted via the winder job above
                elif task == "S6_unload":
                    pass  # still in the oven batch until unloaded
        return n

    def _stator_leaves(self, stator: Stator) -> None:
        self.k["stators_departed"] += 1
        self.k["sojourn_sum"] += self.t - stator.t_start

    # ---- control actions that are not dispatch rules -------------------------------------------------
    def place_order(self, qty: int) -> None:
        if qty <= 0:
            return
        c = self.cfg
        lead = (
            c.lead_time_days
            if c.deterministic
            else c.lead_time_days
            * float(
                self.rng_supply.lognormal(
                    -0.5 * math.log(1 + c.lead_time_cv**2), math.sqrt(math.log(1 + c.lead_time_cv**2))
                )
            )
        )
        self._schedule(self.t + lead * 1440.0, "kits", qty)
        self.in_transit += qty
        self.k["material"] += qty * c.kit_cost
        self.k["order_fees"] += c.order_fee
        self.k["kits_ordered"] += qty

    # ---- results -------------------------------------------------------------------------
    def profit(self) -> float:
        k = self.k
        return (
            k["revenue"] - k["material"] - k["order_fees"] - k["labour"] - k["holding"] - k["backlog"] - k["warranty"]
        )

    def salvage_value(self) -> float:
        """Value of unused kits (at cost, incl. in transit) and WIP (half the kit cost) at the horizon."""

        kits = min(self.stock.values()) + self.received + self.in_transit
        return self.cfg.kit_cost * (kits + 0.5 * self.wip() + len(self.finished))

    def kpis(self) -> dict:
        k = dict(self.k)
        days = max(self.t / 1440.0, 1e-9)
        k["profit"] = self.profit()
        k["profit_with_salvage"] = self.profit() + self.salvage_value()
        k["throughput_per_day"] = k["completed"] / days
        k["avg_wip"] = self.wip_area / max(self.t, 1e-9)
        k["avg_flow_time_h"] = k["flow_time_sum"] / max(k["completed"], 1) / 60.0
        k["escape_rate"] = k["shipped_defective"] / max(k["shipped"], 1)
        k["s5_first_pass_yield"] = 1.0 - k["s5_fail"] / max(k["s5_tested"], 1)
        k["eol_yield"] = 1.0 - k["eol_fail"] / max(k["eol_tested"], 1)
        k["operator_utilisation"] = sum(op["busy_min"] for op in self.ops) / (
            len(self.ops) * self.cfg.shift_min * max(math.ceil(days), 1)
        )
        k["fill_rate"] = k["shipped"] / max(k["demand"], 1)
        k["kv_tolerance"] = self.spec.kv_tol
        return k
