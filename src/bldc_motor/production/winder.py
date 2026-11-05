"""From-scratch CNC needle winder for outrunner stators: kinematics, toolpath, G-code, verification.

Machine concept (3 axes, open-loop steppers, FluidNC controller):

* **A**: the stator rotates on a mandrel (horizontal axis), through a 5:1 belt drive;
* **Z**: the needle carriage travels parallel to the stator axis (axial passes through the slot);
* **X**: radial needle position (sets the layer radius on the tooth end faces).

The needle is a radially oriented 18RW hypodermic tube with the wire fed through its bore. One
turn around tooth *k* (starting at the base side, z < 0, as DWG-003 requires) is:

    X in to layer radius r_j      (beyond the stack end, in free air)
    A sweep slot1 -> slot2        (wire wraps over the bottom end face at r_j)
    X out to the slot opening     (needle tip in the 2.5 mm opening, no copper there)
    Z up through the slot
    X in to r_j, A sweep slot2 -> slot1 (top end-face wrap), X out, Z down

Swapping slot1 and slot2 reverses the coil direction. The wire inside the slot runs straight
between the two end-face wraps, so the needle never has to enter the copper region of a slot.
Inter-coil links run on the base side at the yoke (r < slot bottom), clear of the magnet
overhang. Coordinates: X = radius [mm], Z = axial from mid-stack [mm], A = stator rotation [deg];
the needle angle relative to the stator is phi = -A.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from bldc_motor.design.mechanical import MechanicalDesign
from bldc_motor.design.model import DesignAnalysis


@dataclass(frozen=True)
class WinderConfig:
    needle_od_mm: float = 1.27  # 18RW hypodermic tube, McMaster 8988K48 (304) / 89875K68 (316)
    needle_id_mm: float = 0.84
    end_clearance_mm: float = 0.8  # needle tip -> end turns, axially
    opening_clearance_mm: float = 0.3  # needle -> shoe tips, each side
    link_inset_mm: float = 0.5  # link wire radius inside the slot bottom (on the yoke face)
    layer_pitch_factor: float = 1.03  # radial pitch per turn = factor * insulated wire OD
    # axis limits (output side); stepper speeds kept <= ~750 rpm for usable NEMA17 torque at 24 V
    v_x: float = 50.0
    a_x: float = 2000.0
    v_z: float = 100.0
    a_z: float = 3000.0
    v_a: float = 900.0  # deg/s
    a_a: float = 20000.0  # deg/s^2
    dwell_s: float = 0.03  # settle at each corner
    tension_n: float = 1.8
    # operator time per stator (load, three lead anchors + cuts, unload)
    operator_load_min: float = 0.5
    operator_per_phase_min: float = 0.3
    operator_unload_min: float = 0.3


DEFAULT_CONFIG = WinderConfig()


@dataclass
class Move:
    x: float
    z: float
    a: float
    kind: str  # 'rapid', 'feed', 'pause'
    comment: str = ""


@dataclass
class Toolpath:
    moves: list[Move]
    geometry: dict
    config: WinderConfig
    meta: dict = field(default_factory=dict)


def geometry_for(m: MechanicalDesign, a: DesignAnalysis, cfg: WinderConfig) -> dict:
    d = m.dims
    i = a.internals
    coat = d["coat"]
    r_s = d["stator_od"] / 2
    r1 = d["slot_bottom_d"] / 2
    r2 = d["shoe_inner_d"] / 2
    d_ins = i["wire_insulated_mm"]
    half_stack = d["stack"] / 2 + coat
    end_turn_top = half_stack + d["end_turn_overhang"] - coat  # overhang already includes coat
    z_clear = end_turn_top + cfg.end_clearance_mm + cfg.needle_od_mm / 2
    r_root = r1 + coat + d_ins / 2 + 0.05
    r_max = r2 - coat - d_ins / 2 - 0.05
    pitch = d_ins * cfg.layer_pitch_factor
    per_layer = int(math.floor((r_max - r_root) / pitch)) + 1
    return {
        "slots": d["slots"],
        "pitch_deg": 360.0 / d["slots"],
        "r_s": r_s,
        "r1": r1,
        "r2": r2,
        "r_root": r_root,
        "r_max": r_max,
        "layer_pitch": pitch,
        "turns_per_layer": per_layer,
        "half_stack": half_stack,
        "end_turn_top": end_turn_top,
        "z_clear": z_clear,
        "z_link": -(z_clear + 0.3),
        "r_open": r_s,  # needle tip centre in the slot opening during axial passes
        "r_link": r1 - coat - cfg.link_inset_mm,
        "r_yoke_inner": d["stator_id"] / 2,
        "slot_opening": d["slot_opening"],
        "tooth_width": d["tooth_width"],
        "wire_ins": d_ins,
        "wire_bare": i["wire_bare_mm"],
        "magnet_ri": d["magnet_ri"],
        "needle_od": cfg.needle_od_mm,
    }


def layer_radius(g: dict, j: int) -> float:
    """Radius of turn j: layer 1 root -> tip, layer 2 tip -> root, ..."""

    n = g["turns_per_layer"]
    layer, pos = divmod(j, n)
    if layer % 2 == 0:
        return g["r_root"] + pos * g["layer_pitch"]
    return g["r_max"] - pos * g["layer_pitch"]


def mandrel_dims(g: dict, cfg: WinderConfig) -> dict:
    """Bore-gripping expanding mandrel. Nothing may touch the stator end faces.

    On the base side the needle lays links down to r_link at z_link, so the mandrel body behind
    the stack must stay inside r_link - needle_od/2 - 0.3 for an axial length beyond z_link. At the
    front, the needle only goes down to r_root.
    """

    relief_back = 2 * (g["r_link"] - cfg.needle_od_mm / 2 - 0.3)
    relief_front = 2 * (g["r_root"] - cfg.needle_od_mm / 2 - 0.5)
    back_len = abs(g["z_link"]) + cfg.needle_od_mm / 2 + 0.5 - g["half_stack"]
    return {
        "sleeve_d": 2 * g["r_yoke_inner"],  # nominal = stator bore, expands +0.05
        "sleeve_len": 2 * g["half_stack"],
        "relief_back_d": min(relief_back, 2 * g["r_yoke_inner"] - 0.6),
        "relief_back_len": back_len,
        "nose_d": min(relief_front, 2 * g["r_yoke_inner"] + 1.0),
        "nose_len": 3.0,
        "flange_d": 24.0,
        "flange_len": 4.0,
        "spigot_d": 8.0,  # into the spindle (h6), driven by a flat
        "spigot_len": 20.0,
        "draw_screw": "M5 cone screw (expands 3-slot sleeve)",
    }


def clearance_report(
    m: MechanicalDesign, a: DesignAnalysis, cfg: WinderConfig | None = None
) -> list[tuple[str, float, str, bool]]:
    cfg = cfg or DEFAULT_CONFIG
    g = geometry_for(m, a, cfg)
    turns = a.design["turns"]
    layers = math.ceil(turns / g["turns_per_layer"])
    radial_room = g["r_max"] - g["r_root"]
    checks = [
        ("needle in slot opening: side clearance [mm]", (g["slot_opening"] - cfg.needle_od_mm) / 2, f">= {cfg.opening_clearance_mm}", (g["slot_opening"] - cfg.needle_od_mm) / 2 >= cfg.opening_clearance_mm),
        ("needle bore / insulated wire", cfg.needle_id_mm / g["wire_ins"], ">= 1.25 (wire feeds freely)", cfg.needle_id_mm / g["wire_ins"] >= 1.25),
        ("needle tip -> end turns during A sweep [mm]", g["z_clear"] - cfg.needle_od_mm / 2 - g["end_turn_top"], f">= {cfg.end_clearance_mm - 1e-9:.2f}", g["z_clear"] - cfg.needle_od_mm / 2 - g["end_turn_top"] >= cfg.end_clearance_mm - 1e-9),
        ("turns fit radially (layers used)", float(layers), "<= 2 for neat layering", layers <= 2),
        ("radial room for layer 1 [mm]", radial_room, ">= 1 wire", radial_room >= g["wire_ins"]),
        ("link wire on yoke face: inside slot bottom, outside bore [mm]", g["r_link"] - g["r_yoke_inner"] - g["wire_ins"] / 2, ">= 0.2", g["r_link"] - g["r_yoke_inner"] - g["wire_ins"] / 2 >= 0.2),
        ("link wire clear of magnet overhang [mm]", g["magnet_ri"] - g["r_link"], ">= 3 (links never at OD)", g["magnet_ri"] - g["r_link"] >= 3.0),
        ("mandrel back relief inside needle link path [mm]", (2 * (g["r_link"] - cfg.needle_od_mm / 2) - mandrel_dims(g, cfg)["relief_back_d"]) / 2, ">= 0.3", (2 * (g["r_link"] - cfg.needle_od_mm / 2) - mandrel_dims(g, cfg)["relief_back_d"]) / 2 >= 0.3 - 1e-9),
        ("mandrel relief keeps a wall around the M5 draw screw [mm]", (mandrel_dims(g, cfg)["relief_back_d"] - 5.0) / 2, ">= 1.5", (mandrel_dims(g, cfg)["relief_back_d"] - 5.0) / 2 >= 1.5),
    ]  # fmt: skip
    return checks


# ---------------------------------------------------------------------------------------------
# Toolpath generation
# ---------------------------------------------------------------------------------------------


def _phi_to_a(phi: float) -> float:
    return -phi


def generate(m: MechanicalDesign, a: DesignAnalysis, cfg: WinderConfig | None = None) -> Toolpath:
    cfg = cfg or DEFAULT_CONFIG
    g = geometry_for(m, a, cfg)
    layout = a.layout
    turns = a.design["turns"]
    half_pitch = g["pitch_deg"] / 2
    moves: list[Move] = []
    zc, zl, r_open, r_link = g["z_clear"], g["z_link"], g["r_open"], g["r_link"]
    safe_x = r_open + 3.0

    def add(x, z, a_deg, kind="feed", comment=""):
        moves.append(Move(x, z, a_deg, kind, comment))

    # home / park
    add(safe_x, zl, 0.0, "rapid", "park")
    for phase in "ABC":
        coils = layout.coils_of(phase)  # [(tooth 1-based, direction)]
        for index, (tooth, direction) in enumerate(coils):
            theta = (tooth - 1) * g["pitch_deg"]
            # forward (+1): bottom wrap from phi1 = theta - half_pitch to phi2 = theta + half_pitch
            phi1, phi2 = (
                (theta - half_pitch, theta + half_pitch) if direction > 0 else (theta + half_pitch, theta - half_pitch)
            )
            if index == 0:
                add(safe_x, zl, _phi_to_a(phi1), "rapid", f"phase {phase}: go to T{tooth} start slot")
                add(r_open, -zc, _phi_to_a(phi1), "feed")
                moves.append(
                    Move(
                        r_open,
                        -zc,
                        _phi_to_a(phi1),
                        "pause",
                        f"ANCHOR phase {phase} START lead on post S{phase}, press cycle start",
                    )
                )
            else:
                # link on the base side, on the yoke face, then out to the new start slot
                add(r_link, zl, moves[-1].a, "feed", f"link to T{tooth}")
                add(r_link, zl, _phi_to_a(phi1), "feed")
                add(r_open, -zc, _phi_to_a(phi1), "feed")
            for j in range(turns):
                r = layer_radius(g, j)
                tag = f"T{tooth} turn {j + 1}/{turns} {'CW' if direction > 0 else 'CCW'}"
                add(r, -zc, _phi_to_a(phi1), "feed", tag)  # X in (below stack)
                add(r, -zc, _phi_to_a(phi2), "feed")  # bottom wrap
                add(r_open, -zc, _phi_to_a(phi2), "feed")  # X out to opening
                add(r_open, zc, _phi_to_a(phi2), "feed")  # Z up through slot 2
                add(r, zc, _phi_to_a(phi2), "feed")  # X in (above stack)
                add(r, zc, _phi_to_a(phi1), "feed")  # top wrap
                add(r_open, zc, _phi_to_a(phi1), "feed")  # X out
                add(r_open, -zc, _phi_to_a(phi1), "feed")  # Z down through slot 1
        add(safe_x, zl, moves[-1].a, "rapid", f"phase {phase} done")
        moves.append(
            Move(
                safe_x,
                zl,
                moves[-1].a,
                "pause",
                f"CUT + ANCHOR phase {phase} FINISH (star point lead), then cycle start",
            )
        )
    add(safe_x, zl, 0.0, "rapid", "park, unload stator")
    meta = {"turns_per_coil": turns, "coils": len(layout.coils), "connection": a.design["connection"]}
    return Toolpath(moves, g, cfg, meta)


# ---------------------------------------------------------------------------------------------
# G-code (FluidNC / grblHAL dialect: X, Z linear mm, A rotary deg)
# ---------------------------------------------------------------------------------------------


def to_gcode(tp: Toolpath, name: str) -> str:
    cfg = tp.config
    feed = 60.0 * min(cfg.v_x, cfg.v_z)  # mm/min; firmware per-axis limits do the real shaping
    lines = [
        f"; {name} stator needle-winding program - generated by bldc_motor.production.winder",
        "; Axes: X = needle radius [mm], Z = axial from mid-stack [mm], A = stator rotation [deg]",
        f"; needle 18RW (OD {cfg.needle_od_mm} / ID {cfg.needle_id_mm} mm), wire tension ~{cfg.tension_n} N",
        "; Work zero: Z0 = stack mid-plane, X0 = stator axis (set with the zero gauge), A0 = tooth T1 under the needle",
        "G21 G90 G94",
        "G17",
    ]
    last = None
    for mv in tp.moves:
        if mv.kind == "pause":
            lines.append(f"(MSG,{mv.comment})")
            lines.append("M0")
            continue
        if mv.comment:
            lines.append(f"; {mv.comment}")
        words = []
        for axis, value in (("X", mv.x), ("Z", mv.z), ("A", mv.a)):
            if last is None or abs(getattr(last, axis.lower()) - value) > 1e-9:
                words.append(f"{axis}{value:.4f}")
        if words:
            code = "G0" if mv.kind == "rapid" else "G1"
            lines.append(f"{code} {' '.join(words)}" + ("" if code == "G0" else f" F{feed:.0f}"))
        last = mv
    lines += ["M5", "M30", ""]
    return "\n".join(lines)


_WORD = re.compile(r"([XZAF])(-?\d+(?:\.\d+)?)")
_MOTION = re.compile(r"^G0*[01](?!\d)")  # G0/G1/G00/G01 only (not G17, G10, ...)


def parse_gcode(text: str) -> list[tuple[float, float, float, str]]:
    """Independent decoder (used by the verifier): returns (x, z, a, kind) for each motion line."""

    x = z = a = 0.0
    out = []
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line or line.startswith("("):
            continue
        if re.match(r"^M0*0(?!\d)", line):
            out.append((x, z, a, "pause"))
            continue
        if _MOTION.match(line):
            for axis, value in _WORD.findall(line):
                if axis == "X":
                    x = float(value)
                elif axis == "Z":
                    z = float(value)
                elif axis == "A":
                    a = float(value)
            out.append((x, z, a, "move"))
    return out


# ---------------------------------------------------------------------------------------------
# Verification: turns per tooth (winding number) and collisions against the stator geometry
# ---------------------------------------------------------------------------------------------


def verify(points: list[tuple[float, float, float, str]], g: dict, samples: int = 24) -> dict:
    """Count signed turns around each tooth and check every segment against the stator envelope.

    Winding number of the needle path in the (phi, z) plane around each tooth centre (theta_k, 0),
    counted only while the needle is at a winding radius (inside the end-face/slot region). The
    collision model: the solid region is |z| <= end_turn_top for r < r_s (teeth, coils, yoke). Inside
    it the needle tip may only be in a slot opening at r >= r_open - 1e-6, centred within the
    allowed lateral band.
    """

    slots, pitch = g["slots"], g["pitch_deg"]
    winding = [0.0] * slots
    collisions = []
    band = g["slot_opening"] / 2 - g["needle_od"] / 2  # max lateral offset of the needle centre
    for (x0, z0, a0, _k0), (x1, z1, a1, k1) in zip(points, points[1:], strict=False):
        if k1 == "pause":
            continue
        for s in range(1, samples + 1):
            t = s / samples
            x, z, a = x0 + (x1 - x0) * t, z0 + (z1 - z0) * t, a0 + (a1 - a0) * t
            phi = -a
            if abs(z) <= g["end_turn_top"] and x < g["r_s"] + 1e-6:
                # inside the stator envelope: only allowed at the opening radius, centred in a slot
                rel = (phi % pitch) - pitch / 2  # 0 at slot centre
                lateral = abs(math.radians(rel)) * g["r_s"]
                if x < g["r_open"] - 1e-6 or lateral > band + 1e-6:
                    collisions.append((round(x, 3), round(z, 3), round(a, 3)))
        # winding number contribution (only segments at winding radius, i.e. not links/rapids)
        if max(x0, x1) <= g["r_s"] + 1e-6 and min(x0, x1) >= g["r_root"] - 1e-6:
            for k in range(slots):
                theta = k * pitch
                p0 = (-a0 - theta, z0)
                p1 = (-a1 - theta, z1)
                ang0, ang1 = math.atan2(p0[1], _wrap(p0[0])), math.atan2(p1[1], _wrap(p1[0]))
                d = ang1 - ang0
                while d > math.pi:
                    d -= 2 * math.pi
                while d < -math.pi:
                    d += 2 * math.pi
                if abs(_wrap(p0[0])) < pitch and abs(_wrap(p1[0])) < pitch:
                    winding[k] += d
    turns = [round(w / (2 * math.pi), 3) for w in winding]
    return {"turns_per_tooth": turns, "collisions": collisions[:20], "collision_count": len(collisions)}


def _wrap(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


# ---------------------------------------------------------------------------------------------
# Cycle time and wire length
# ---------------------------------------------------------------------------------------------


def _axis_time(distance: float, v: float, acc: float) -> float:
    d = abs(distance)
    if d < 1e-12:
        return 0.0
    if d >= v * v / acc:
        return d / v + v / acc
    return 2.0 * math.sqrt(d / acc)


def cycle_time(tp: Toolpath) -> dict:
    cfg = tp.config
    machine = 0.0
    for m0, m1 in zip(tp.moves, tp.moves[1:], strict=False):
        if m1.kind == "pause":
            continue
        t = max(
            _axis_time(m1.x - m0.x, cfg.v_x, cfg.a_x),
            _axis_time(m1.z - m0.z, cfg.v_z, cfg.a_z),
            _axis_time(m1.a - m0.a, cfg.v_a, cfg.a_a),
        )
        if t > 0:
            machine += t + cfg.dwell_s
    pauses = sum(1 for mv in tp.moves if mv.kind == "pause")
    operator = cfg.operator_load_min + 3 * cfg.operator_per_phase_min + cfg.operator_unload_min
    return {
        "machine_s": machine,
        "machine_min": machine / 60.0,
        "pauses": pauses,
        "operator_min": operator,
        "station_min": machine / 60.0 + operator,
    }


def wire_length_m(tp: Toolpath, a: DesignAnalysis) -> float:
    """Copper actually laid per stator: model turn length x turns x coils + link allowance."""

    i = a.internals
    coils = tp.meta["coils"]
    per_coil = tp.meta["turns_per_coil"] * i["turn_length_mm"]
    links = (coils - 3) * (2 * math.pi * tp.geometry["r_link"] / tp.geometry["slots"] * 3 + 4.0)
    return (coils * per_coil + links) / 1000.0


def axis_loads(tp: Toolpath, stator_mass_g: float) -> dict:
    """First-order drive sizing: A-axis torque at the output, Z/X forces, and motor speeds."""

    cfg = tp.config
    g = tp.geometry
    r = g["r_s"] / 1000.0
    j_stator = 0.5 * stator_mass_g / 1000.0 * (r**2 + (g["r_yoke_inner"] / 1000.0) ** 2)
    j_mandrel = 1.5e-5  # mandrel + 100T pulley (estimate)
    alpha = math.radians(cfg.a_a)
    torque_a = (j_stator + j_mandrel) * alpha + cfg.tension_n * r
    reduction = 5.0
    motor_rpm_a = cfg.v_a / 360.0 * 60.0 * reduction
    lead = 8.0
    return {
        "a_output_torque_nm": torque_a,
        "a_motor_torque_nm": torque_a / reduction + 5.7e-6 * alpha * reduction,
        "a_motor_rpm": motor_rpm_a,
        "a_resolution_deg": 1.8 / 16 / reduction,
        "a_resolution_um_at_od": math.radians(1.8 / 16 / reduction) * g["r_s"] * 1000.0,
        "z_motor_rpm": cfg.v_z / lead * 60.0,
        "x_motor_rpm": cfg.v_x / lead * 60.0,
        "z_force_n": 0.25 * cfg.a_z / 1000.0 + cfg.tension_n,
        "lead_resolution_um": lead / (200 * 16) * 1000.0,
    }


# ---------------------------------------------------------------------------------------------
# Hardware bill of materials
# ---------------------------------------------------------------------------------------------

WINDER_BOM = (
    # (item, qty, unit_usd, source/status)
    (
        "Elecrow 6x CNC Controller for FluidNC (ESP32)",
        1,
        49.00,
        "https://www.elecrow.com/6x-cnc-controller-for-fluidnc.html (search result, 2026-11-05)",
    ),
    ("TMC2209 stepper driver modules", 3, 9.00, "estimate"),
    ("NEMA17 stepper 0.45 N·m (A, Z, X)", 3, 15.00, "estimate"),
    (
        "MGN12H linear rail 200 mm + carriage (Z)",
        1,
        30.95,
        "https://www.fruugo.us/mgn12h-miniature-linear-rail-slide-guide-strength-linear-rail-guide-for-cnc-machines-200mm/p-402239549 (search result)",
    ),
    ("MGN12H linear rail 150-200 mm + carriage (X)", 1, 30.95, "same as above"),
    ("T8 lead screw 8 mm lead, 150 mm + anti-backlash nut + coupler", 2, 15.00, "estimate"),
    ("A axis: GT2 20T/100T pulleys + 200 mm belt (5:1), 8 mm shaft, 2 x KP08 pillow blocks", 1, 35.00, "estimate"),
    ("Stator mandrel (turned, DWG-W01) + clamp nut + T1 index pin", 1, 40.00, "estimate - turn in-house or JLCCNC"),
    (
        "Needle: 18RW 304 hypodermic tube 0.050 x 0.033 in (McMaster 8988K48), tip polished",
        1,
        20.00,
        "https://www.mcmaster.com/hypodermic-tubing (price not shown - estimate)",
    ),
    ("Needle holder + wire guide eyelet (ceramic)", 1, 15.00, "estimate"),
    (
        "Wire tensioner, magnetic, 0.5-1.2 mm range (or DIY dancer + felt brake ~$30)",
        1,
        150.00,
        "https://cncwindingmachine.en.made-in-china.com/product/cmuRLrQOOgkS/ (no price - estimate)",
    ),
    ("Spool holder for 10 lb spool + guide pulleys", 1, 25.00, "estimate"),
    ("Frame: 2020 extrusion 2 m, 10 mm Al base plate, brackets, T-nuts", 1, 90.00, "estimate"),
    ("24 V 6 A power supply, E-stop, foot pedal, 3 limit switches, wiring, enclosure box", 1, 75.00, "estimate"),
    ("Printed parts (P1S, PETG/CF), fasteners", 1, 30.00, "estimate"),
)


def bom_total() -> float:
    return sum(qty * unit for _, qty, unit, _ in WINDER_BOM)


def fluidnc_config(name: str = "rl2407_winder") -> str:
    """FluidNC config.yaml skeleton for the 3-axis winder (X, Z linear; A rotary in degrees)."""

    steps_lin = 200 * 16 / 8.0  # 1.8 deg, 1/16 microstep, 8 mm lead
    steps_a = 200 * 16 * 5 / 360.0  # 5:1 belt, steps per degree
    return f"""# FluidNC config for {name} - generated by bldc_motor.production.winder
# Verify pin numbers against the Elecrow 6x board documentation before powering up.
name: {name}
board: Elecrow 6x CNC Controller for FluidNC
stepping:
  engine: RMT
  idle_ms: 255
  pulse_us: 4
axes:
  x:  # needle radius
    steps_per_mm: {steps_lin:.3f}
    max_rate_mm_per_min: 3000
    acceleration_mm_per_sec2: 2000
    max_travel_mm: 40
    homing: {{cycle: 2, positive_direction: true, mpos_mm: 0, feed_mm_per_min: 300, seek_mm_per_min: 1500}}
    motor0: {{limit_pos_pin: gpio.xx, stepstick: {{step_pin: gpio.xx, direction_pin: gpio.xx}}}}
  z:  # axial
    steps_per_mm: {steps_lin:.3f}
    max_rate_mm_per_min: 6000
    acceleration_mm_per_sec2: 3000
    max_travel_mm: 60
    homing: {{cycle: 1, positive_direction: false, mpos_mm: 0, feed_mm_per_min: 300, seek_mm_per_min: 2000}}
    motor0: {{limit_neg_pin: gpio.xx, stepstick: {{step_pin: gpio.xx, direction_pin: gpio.xx}}}}
  a:  # stator rotation, degrees
    steps_per_mm: {steps_a:.4f}
    max_rate_mm_per_min: 54000
    acceleration_mm_per_sec2: 20000
    max_travel_mm: 100000
    homing: {{cycle: 3, positive_direction: true, mpos_mm: 0, feed_mm_per_min: 600, seek_mm_per_min: 3600}}
    motor0: {{limit_pos_pin: gpio.xx, stepstick: {{step_pin: gpio.xx, direction_pin: gpio.xx}}}}
control:
  safety_door_pin: NO_PIN
  reset_pin: NO_PIN
  feed_hold_pin: gpio.xx  # E-stop chain -> feed hold
  cycle_start_pin: gpio.xx  # foot pedal
"""
