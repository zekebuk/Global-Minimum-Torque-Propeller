"""Center hub: two machined plates that clamp both blade root tabs, COTS fasteners, a four-hole motor mount.

Every part is either a 2.5D machined plate (profile, through holes, counterbores, tapped holes, all from
one side) or an off-the-shelf fastener, so the hub can be cut on a router, mill, or waterjet plus a drill
press. Frame: the blade frame (+z thrust and rotation axis, blade 1 along +x, blade 2 rotated 180 deg
about z). The motor sits below the hub (-z).

Stack, bottom to top:

1. **Bottom plate** (thicker): sits on the motor. Motor screws pass up from the motor through counterbores
   (heads below the plate's top face, inside the tab-free centre), and the blade bolts thread into it, so
   nothing protrudes below its bottom face into the motor.
2. **Blade tabs**, clamped between the plates over r = tab_inner_radius..tab_outer_radius. The plates end
   at tab_outer_radius, matching the clamp the FEA and modal stages model.
3. **Top plate** (thinner): clearance holes for the blade bolts, access holes over the motor screws so a
   hex key reaches them with the blades fitted.

Load path assumptions (first principles, stated so they can be checked):

- The centrifugal force on every mass element points radially away from the rotation axis, so the
  resultant F_c = m omega^2 (x_cg, y_cg) acts along a line through the axis. About the bolt-group
  centroid c it has a moment M_z = -(c x F), which the bolt pair reacts as a couple. A tab centred off
  that line (e.g. on the chord midpoint, 13.5 mm from the pitch axis at the GMT root) multiplies the bolt
  shear; ``ideal_tab_centre_s`` gives the tab position that zeroes it.
- Primary path: clamp friction at two interfaces per blade. Secondary path (after preload loss from
  polymer creep): the bolts in double shear and bearing. Both are checked against the full load.
- Thrust and torque loads are small next to the centrifugal load; the root flap moment is carried by
  the plates as beams, shared in proportion to t^3 (no spacer ties the plates together between the tabs).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class MetricScrew:
    """ISO 4762 socket head cap screw with its ISO 273 holes, ISO 7089 washer, and counterbore (metres)."""

    name: str
    d: float
    pitch: float
    head_d: float
    head_h: float
    clearance_fine: float
    clearance_medium: float
    tap_drill: float
    counterbore_d: float
    stress_area: float
    washer_od: float
    washer_h: float
    button_head_d: float = 0.0  # ISO 7380 button head
    button_head_h: float = 0.0


SCREWS = {
    "M3": MetricScrew("M3", 3.0e-3, 0.5e-3, 5.5e-3, 3.0e-3, 3.2e-3, 3.4e-3, 2.5e-3, 6.5e-3, 5.03e-6, 7.0e-3, 0.5e-3, 5.7e-3, 1.65e-3),
    "M4": MetricScrew("M4", 4.0e-3, 0.7e-3, 7.0e-3, 4.0e-3, 4.3e-3, 4.5e-3, 3.3e-3, 8.0e-3, 8.78e-6, 9.0e-3, 0.8e-3, 7.6e-3, 2.2e-3),
    "M5": MetricScrew("M5", 5.0e-3, 0.8e-3, 8.5e-3, 5.0e-3, 5.3e-3, 5.5e-3, 4.2e-3, 10.0e-3, 14.2e-6, 10.0e-3, 1.0e-3, 9.5e-3, 2.75e-3),
    "M6": MetricScrew("M6", 6.0e-3, 1.0e-3, 10.0e-3, 6.0e-3, 6.4e-3, 6.6e-3, 5.0e-3, 11.0e-3, 20.1e-6, 12.0e-3, 1.6e-3, 10.5e-3, 3.3e-3),
}
ISO_4762_LENGTHS_M = tuple(v * 1e-3 for v in (6, 8, 10, 12, 16, 20, 25, 30, 35, 40, 45, 50))
ISO_8734_LENGTHS_M = tuple(v * 1e-3 for v in (8, 10, 12, 14, 16, 18, 20, 24, 28, 30, 32, 36, 40))  # hardened dowels, d = 6
PROOF_STRESS_PA = {"8.8": 580.0e6, "12.9": 970.0e6}  # ISO 898-1, d <= 16 mm


@dataclass(frozen=True)
class HubDesign:
    # "bolted": two bolts per blade, fixed. "folding": one hinge pin per blade (prandtl_prop.cad.hub_variants).
    # "one_piece": both blades and a hub boss molded as one part, clamped by a machined cap.
    variant: str = "bolted"
    top_plate_thickness_m: float = 0.004
    bottom_plate_thickness_m: float = 0.008
    plate_side_margin_m: float = 0.004  # plate width = tab width + 2 x margin
    plate_corner_radius_m: float = 0.003
    plate_material: str = "6061-T6 aluminium"
    plate_yield_Pa: float = 276.0e6
    plate_fatigue_Pa: float = 96.0e6  # 6061-T6 endurance at 5e8 cycles (R = -1)
    plate_density_kg_m3: float = 2700.0
    blade_bolt_class: str = "8.8"
    blade_bolt_preload_N: float = 3000.0  # modest: the tabs are polymer (creep, crushing)
    friction_coefficient: float = 0.2  # aluminium on molded CFRP, dry
    nut_factor: float = 0.2  # torque = K F d
    min_thread_engagement_d: float = 1.25  # in the aluminium bottom plate
    # Motor interface: a placeholder until set from the motor drawing (motor_pattern_confirmed: true).
    motor_screw: str = "M3"
    motor_screw_count: int = 4
    motor_bolt_circle_m: float = 0.025
    motor_pattern_angle_deg: float = 45.0  # first hole, from +x (the blade axis): between the tabs
    motor_thread_depth_m: float = 0.004  # usable thread depth in the motor bell
    motor_pattern_confirmed: bool = False
    center_bore_m: float = 0.010  # clearance for the motor's shaft end / circlip
    access_holes: bool = True
    safety_factor: float = 2.0
    standard_lengths_m: tuple[float, ...] = field(default=ISO_4762_LENGTHS_M)
    edge_chamfer_m: float = 0.0008  # exposed plate/cap edges
    # Folding variant
    hinge_pin_diameter_m: float = 0.006  # ISO 8734 hardened dowel, m6: press fit in the bottom plate
    hinge_pin_shear_strength_Pa: float = 550.0e6  # ISO 8734 double-shear rating / (2 x area), d = 6
    hinge_bushing_od_m: float = 0.008  # PTFE-lined steel-backed bushing (DU type 0608) pressed into the tab
    hinge_bushing_length_m: float = 0.008
    hinge_bushing_static_pressure_Pa: float = 250.0e6
    plate_margin_m: float = 0.002  # plate edge beyond the tab end's circle
    fold_clearance_m: float = 0.001
    fold_angle_deg: float = 90.0  # stowed: both blades to the same side (one lags, one leads)
    spacer_squeeze_m: float = 2.0e-5  # core spacer thinner than the tab: no flap slop, light fold friction
    top_plate_options_m: tuple[float, ...] = (0.004, 0.005, 0.006)
    bottom_plate_options_m: tuple[float, ...] = (0.006, 0.008, 0.010)
    dowel_lengths_m: tuple[float, ...] = field(default=ISO_8734_LENGTHS_M)
    # One-piece variant
    boss_diameter_m: float = 0.036
    boss_thickness_m: float = 0.010
    cap_options_m: tuple[float, ...] = (0.003, 0.004, 0.005, 0.006)
    motor_screw_preload_N: float = 1000.0  # through the polymer boss: modest, re-check after the first runs


@dataclass
class HubResult:
    parts: dict  # machined parts by name
    fasteners: list  # simplified COTS solids for the assembly view
    metrics: dict
    warnings: list[str]
    bom: list[dict]


# ------------------------------------------------------------------------------------------ loads


def screw_for_hole(diameter_m: float) -> MetricScrew:
    """Largest metric screw whose ISO 273 fine clearance hole fits the given hole."""

    fitting = [s for s in SCREWS.values() if s.clearance_fine <= diameter_m + 1.0e-6]
    if not fitting:
        raise ValueError(f"No metric screw fits a {diameter_m * 1000:.2f} mm hole")
    return max(fitting, key=lambda s: s.d)


def bolt_group_shear(points_xy: np.ndarray, force_xy: Sequence[float], point_on_line_xy: Sequence[float]) -> np.ndarray:
    """Per-bolt shear vectors (elastic method) for a force acting along a line through ``point_on_line_xy``."""

    points = np.asarray(points_xy, dtype=float)
    force = np.asarray(force_xy, dtype=float)
    centroid = points.mean(axis=0)
    arm = np.asarray(point_on_line_xy, dtype=float) - centroid
    moment = arm[0] * force[1] - arm[1] * force[0]
    relative = points - centroid
    polar = float(np.sum(relative**2))
    direct = np.tile(force / len(points), (len(points), 1))
    if polar == 0.0:
        return direct
    return direct + moment * np.column_stack([-relative[:, 1], relative[:, 0]]) / polar


def centrifugal_force(mass_kg: float, cg_m: Sequence[float], rpm: float) -> np.ndarray:
    omega = rpm * math.pi / 30.0
    return mass_kg * omega**2 * np.array([cg_m[0], cg_m[1]])


def root_joint_loads(mass_kg: float, cg_m: Sequence[float], rpm: float, bolts_xy: np.ndarray) -> dict[str, float]:
    """Centrifugal load on the root bolt group: the resultant runs through the rotation axis."""

    force = centrifugal_force(mass_kg, cg_m, rpm)
    shear = bolt_group_shear(bolts_xy, force, (0.0, 0.0))
    centroid = np.asarray(bolts_xy).mean(axis=0)
    return {
        "centrifugal_force_N": float(np.linalg.norm(force)),
        "root_inplane_moment_Nm": float(-(centroid[0] * force[1] - centroid[1] * force[0])),
        "bolt_max_shear_N": float(np.max(np.linalg.norm(shear, axis=1))),
        "bolt_direct_shear_N": float(np.linalg.norm(force) / len(bolts_xy)),
    }


def ideal_tab_centre_s(cg_m: Sequence[float], bolt_radii_m: Sequence[float], tab_pitch_deg: float = 0.0) -> float:
    """Tab centre (root (s, t) frame, from the pitch axis) that puts the bolt centroid on the centrifugal line."""

    if abs(tab_pitch_deg) > 1.0e-9:
        raise ValueError("ideal_tab_centre_s assumes a flat (zero-pitch) tab")
    x_c = float(np.mean(bolt_radii_m))
    return -x_c * float(cg_m[1]) / float(cg_m[0])  # y = -s for a zero-pitch tab


def clamp_contact_fraction(outline_3d: np.ndarray, tolerance_m: float = 5.0e-5) -> tuple[float, float]:
    """Fraction of the tab width within ``tolerance_m`` of the top (bottom) crest: the flat contact the plates see."""

    y, z = outline_3d[:, 1], outline_3d[:, 2]
    width = y.max() - y.min()
    fractions = []
    for crest, side in ((z.max(), z >= 0.5 * (z.max() + z.min())), (z.min(), z < 0.5 * (z.max() + z.min()))):
        near = side & (np.abs(z - crest) <= tolerance_m)
        fractions.append(float((y[near].max() - y[near].min()) / width) if near.sum() > 1 else 0.0)
    return fractions[0], fractions[1]


# ---------------------------------------------------------------------------------- fastener sizing


def tapped_bolt_length(screw: MetricScrew, stack_above_m: float, plate_m: float, design: HubDesign) -> tuple[float, float] | None:
    """Shortest standard length that engages >= min_thread_engagement_d and stops 0.5 mm short of the plate's far face."""

    for length in design.standard_lengths_m:
        engagement = length - stack_above_m
        if design.min_thread_engagement_d * screw.d <= engagement <= plate_m - 0.5e-3:
            return length, engagement
    return None


def motor_screw_fit(screw: MetricScrew, plate_m: float, design: HubDesign) -> tuple[float, float, float] | None:
    """(length, counterbore depth, thread engagement in the motor) or None.

    The head sits in a counterbore at least head height + 0.3 mm deep; the engagement must be at least
    max(0.8 d, 3 pitches) and leave 0.5 mm of the motor's thread depth unused, so the screw cannot reach
    the windings.
    """

    minimum = max(0.8 * screw.d, 3.0 * screw.pitch)
    maximum = design.motor_thread_depth_m - 0.5e-3
    for length in design.standard_lengths_m:
        for depth in np.arange(screw.head_h + 0.3e-3, plate_m - 2.0e-3 + 1e-9, 0.1e-3):
            engagement = length - (plate_m - depth)
            if minimum <= engagement <= maximum:
                return length, float(depth), float(engagement)
    return None


# -------------------------------------------------------------------------------------- geometry


def _cq():
    import cadquery as cq

    return cq


def _plate(outline_parts, z0: float, thickness: float, corner_radius: float):
    cq = _cq()
    solid = None
    for kind, args in outline_parts:
        if kind == "box":
            (x0, x1, y0, y1) = args
            piece = cq.Solid.makeBox(x1 - x0, y1 - y0, thickness, cq.Vector(x0, y0, z0))
        else:
            (cx, cy, radius) = args
            piece = cq.Solid.makeCylinder(radius, thickness, cq.Vector(cx, cy, z0), cq.Vector(0, 0, 1))
        solid = piece if solid is None else solid.fuse(piece)
    solid = solid.clean()
    try:
        filleted = cq.Workplane().add(solid).edges("|Z").fillet(corner_radius).val()
        if filleted.isValid():
            solid = filleted
    except Exception:  # noqa: BLE001 - concave corners can defeat the fillet; sharp edges are still machinable
        pass
    return solid


def _cylinder(radius, z0, z1, x, y):
    cq = _cq()
    return cq.Solid.makeCylinder(radius, z1 - z0, cq.Vector(x, y, z0), cq.Vector(0, 0, 1))


def _segment_distance_to_box(px: float, py: float, box: tuple[float, float, float, float]) -> float:
    x0, x1, y0, y1 = box
    dx = max(x0 - px, 0.0, px - x1)
    dy = max(y0 - py, 0.0, py - y1)
    return math.hypot(dx, dy)


def build_hub(
    root_geometry,
    bolt_centre_m: Sequence[tuple[float, float, float]],
    tab_outline_3d: np.ndarray,
    *,
    blade_mass_kg: float,
    blade_cg_m: Sequence[float],
    rpm: float,
    thrust_N: float,
    torque_Nm: float,
    blade_count: int,
    root_flap_moment_Nm: float,
    tab_allowable_Pa: float,
    design: HubDesign = HubDesign(),  # noqa: B008
) -> HubResult:
    """Plates, fasteners, checks, and bill of materials for a two-blade hub (metres, blade frame)."""

    if blade_count != 2:
        raise ValueError("The hub geometry is for two blades")
    g, h = root_geometry, design
    warnings: list[str] = []
    bolt = screw_for_hole(g.bolt_diameter_m)
    motor = SCREWS[h.motor_screw]
    z_top, z_bottom = float(tab_outline_3d[:, 2].max()), float(tab_outline_3d[:, 2].min())
    tab_thickness = z_top - z_bottom
    y_c = float(bolt_centre_m[0][1])
    half_w = 0.5 * g.tab_width_m + h.plate_side_margin_m
    r_o = g.tab_outer_radius_m
    bolts_1 = np.array([[c[0], c[1]] for c in bolt_centre_m])
    bolts_all = np.vstack([bolts_1, -bolts_1])
    angles = [math.radians(h.motor_pattern_angle_deg + k * 360.0 / h.motor_screw_count) for k in range(h.motor_screw_count)]
    motor_xy = [(0.5 * h.motor_bolt_circle_m * math.cos(a), 0.5 * h.motor_bolt_circle_m * math.sin(a)) for a in angles]

    # --- fasteners
    blade_fit = tapped_bolt_length(bolt, bolt.washer_h + h.top_plate_thickness_m + tab_thickness, h.bottom_plate_thickness_m, h)
    if blade_fit is None:
        raise ValueError(
            f"No standard {bolt.name} length gives {h.min_thread_engagement_d:.2f} d engagement inside a "
            f"{h.bottom_plate_thickness_m * 1000:.1f} mm bottom plate; make it thicker"
        )
    blade_length, blade_engagement = blade_fit
    motor_fit = motor_screw_fit(motor, h.bottom_plate_thickness_m, h)
    if motor_fit is None:
        raise ValueError(f"No standard {motor.name} length fits the bottom plate and the motor thread depth")
    motor_length, cbore_depth, motor_engagement = motor_fit

    # --- plates: one rectangle over both tabs (they sit +/- y_c off the x axis, 180 deg apart); a disc
    # only if the motor pattern does not fit inside it with 3 mm of edge
    half_h = half_w + abs(y_c)
    disc_r = 0.5 * h.motor_bolt_circle_m + 0.5 * motor.counterbore_d + 3.0e-3
    outline = [("box", (-r_o, r_o, -half_h, half_h))]
    if max(abs(y) for _, y in motor_xy) + 0.5 * motor.counterbore_d + 3.0e-3 > half_h:
        outline.append(("disc", (0.0, 0.0, disc_r)))
    bottom = _plate(outline, z_bottom - h.bottom_plate_thickness_m, h.bottom_plate_thickness_m, h.plate_corner_radius_m)
    top = _plate(outline, z_top, h.top_plate_thickness_m, h.plate_corner_radius_m)
    zb0, zt1 = z_bottom - h.bottom_plate_thickness_m, z_top + h.top_plate_thickness_m
    for x, y in bolts_all:
        bottom = bottom.cut(_cylinder(0.5 * bolt.tap_drill, zb0 - 1e-3, z_bottom + 1e-3, x, y))  # tapped through
        top = top.cut(_cylinder(0.5 * bolt.clearance_fine, z_top - 1e-3, zt1 + 1e-3, x, y))
    for x, y in motor_xy:
        bottom = bottom.cut(_cylinder(0.5 * motor.clearance_medium, zb0 - 1e-3, z_bottom + 1e-3, x, y))
        bottom = bottom.cut(_cylinder(0.5 * motor.counterbore_d, z_bottom - cbore_depth, z_bottom + 1e-3, x, y))
        if h.access_holes:
            top = top.cut(_cylinder(0.5 * motor.counterbore_d, z_top - 1e-3, zt1 + 1e-3, x, y))
    for plate_name in ("bottom", "top"):
        plate = bottom if plate_name == "bottom" else top
        z0, z1 = (zb0, z_bottom) if plate_name == "bottom" else (z_top, zt1)
        plate = plate.cut(_cylinder(0.5 * h.center_bore_m, z0 - 1e-3, z1 + 1e-3, 0.0, 0.0)).clean()
        bottom, top = (plate, top) if plate_name == "bottom" else (bottom, plate)

    # --- simplified COTS solids (assembly view and mass)
    fasteners = []
    for x, y in bolts_all:
        head_z = zt1 + bolt.washer_h
        fasteners.append(
            _cylinder(0.5 * bolt.d, head_z - blade_length, head_z, x, y).fuse(
                _cylinder(0.5 * bolt.head_d, head_z, head_z + bolt.head_h, x, y)
            )
        )
        fasteners.append(
            _cylinder(0.5 * bolt.washer_od, zt1, zt1 + bolt.washer_h, x, y).cut(
                _cylinder(0.5 * bolt.clearance_fine, zt1 - 1e-3, zt1 + 1e-3 + bolt.washer_h, x, y)
            )
        )
    for x, y in motor_xy:
        head_top = z_bottom - cbore_depth + motor.head_h
        fasteners.append(
            _cylinder(0.5 * motor.d, head_top - motor.head_h - motor_length, head_top - motor.head_h, x, y).fuse(
                _cylinder(0.5 * motor.head_d, head_top - motor.head_h, head_top, x, y)
            )
        )

    # --- checks
    joint = root_joint_loads(blade_mass_kg, blade_cg_m, rpm, bolts_1)
    f_bolt = joint["bolt_max_shear_N"]
    proof = PROOF_STRESS_PA[h.blade_bolt_class]
    bolt_shear_allow = 0.6 * proof / h.safety_factor
    tau_bolt = f_bolt / (2.0 * bolt.stress_area)  # double shear; threads may sit in the shear planes
    tab_bearing = f_bolt / (g.bolt_diameter_m * g.tab_thickness_m)
    plate_bearing = 0.5 * f_bolt / (bolt.d * h.top_plate_thickness_m)  # thinner plate governs
    end_distance = r_o - max(bolts_1[:, 0])
    plate_shear_out = 0.5 * f_bolt / (2.0 * (end_distance - 0.5 * bolt.d) * h.top_plate_thickness_m)
    slip_capacity = 2.0 * h.friction_coefficient * h.blade_bolt_preload_N  # per bolt, two interfaces
    torque_spec = h.nut_factor * h.blade_bolt_preload_N * bolt.d
    preload_ratio = h.blade_bolt_preload_N / (proof * bolt.stress_area)
    strip_area = 0.75 * math.pi * bolt.d * blade_engagement
    strip_stress = h.blade_bolt_preload_N / strip_area
    contact_top, contact_bottom = clamp_contact_fraction(tab_outline_3d)
    clamp_area = min(contact_top, contact_bottom) * g.tab_width_m * (g.tab_outer_radius_m - g.tab_inner_radius_m)
    clamp_pressure = len(bolts_1) * h.blade_bolt_preload_N / max(clamp_area, 1e-12)
    width_centre = 2.0 * half_h - h.center_bore_m
    t_b, t_t = h.bottom_plate_thickness_m, h.top_plate_thickness_m
    share_bottom = t_b**3 / (t_b**3 + t_t**3)
    bending = max(
        share_bottom * root_flap_moment_Nm / (width_centre * t_b**2 / 6.0),
        (1 - share_bottom) * root_flap_moment_Nm / (width_centre * t_t**2 / 6.0),
    )
    tension = joint["centrifugal_force_N"] / (width_centre * (t_b + t_t))
    plate_allow = h.plate_fatigue_Pa / h.safety_factor
    motor_shear = torque_Nm / (h.motor_screw_count * 0.5 * h.motor_bolt_circle_m)
    motor_tension = thrust_N / h.motor_screw_count
    access = min(_segment_distance_to_box(x, y, box) for x, y in motor_xy for box in _tab_boxes(g, y_c)) - 0.5 * motor.counterbore_d
    holes = (
        [(x, y, 0.5 * bolt.tap_drill) for x, y in bolts_all]
        + [(x, y, 0.5 * motor.counterbore_d) for x, y in motor_xy]
        + [(0.0, 0.0, 0.5 * h.center_bore_m)]
    )
    ligament = min(math.hypot(a[0] - b[0], a[1] - b[1]) - a[2] - b[2] for i, a in enumerate(holes) for b in holes[i + 1 :])
    side_ligament = half_w - 0.5 * bolt.clearance_fine - abs(bolts_1[0, 1] - y_c)
    plate_volume = sum(_volume(p) for p in (top, bottom))
    fastener_mass = sum(_volume(f) for f in fasteners) * 7900.0
    ideal_s = ideal_tab_centre_s(blade_cg_m, g.bolt_radii_m, g.tab_pitch_deg)
    margins = {
        "bolt_shear_margin": bolt_shear_allow / tau_bolt - 1.0,
        "tab_bearing_margin": tab_allowable_Pa / tab_bearing - 1.0,
        "plate_bearing_margin": (h.plate_yield_Pa / h.safety_factor) / plate_bearing - 1.0,
        "plate_shear_out_margin": (0.58 * h.plate_yield_Pa / h.safety_factor) / plate_shear_out - 1.0,
        "plate_fatigue_margin": plate_allow / (bending + tension) - 1.0,
        "thread_strip_margin": (0.58 * h.plate_yield_Pa / h.safety_factor) / strip_stress - 1.0,
        "slip_margin": slip_capacity / f_bolt - 1.0,
        "tab_clamp_pressure_margin": tab_allowable_Pa / clamp_pressure - 1.0,
    }
    metrics: dict = {
        **joint,
        **margins,
        "ideal_tab_centre_s_m": ideal_s,
        "tab_centre_offset_from_ideal_m": float(-y_c - ideal_s),
        "blade_bolt": f"ISO 4762 {bolt.name}x{blade_length * 1000:.0f} class {h.blade_bolt_class}",
        "blade_bolt_thread_engagement_m": blade_engagement,
        "blade_bolt_torque_Nm": torque_spec,
        "blade_bolt_preload_fraction_of_proof": preload_ratio,
        "bolt_shear_stress_Pa": tau_bolt,
        "tab_bearing_stress_Pa": tab_bearing,
        "plate_bearing_stress_Pa": plate_bearing,
        "plate_shear_out_stress_Pa": plate_shear_out,
        "plate_centre_bending_stress_Pa": bending,
        "plate_centre_tension_stress_Pa": tension,
        "thread_strip_stress_Pa": strip_stress,
        "tab_clamp_pressure_Pa": clamp_pressure,
        "clamp_contact_fraction_top": contact_top,
        "clamp_contact_fraction_bottom": contact_bottom,
        "motor_screw": f"ISO 4762 {motor.name}x{motor_length * 1000:.0f} class 12.9",
        "motor_screw_engagement_m": motor_engagement,
        "motor_counterbore_depth_m": cbore_depth,
        "motor_screw_shear_N": motor_shear,
        "motor_screw_tension_N": motor_tension,
        "motor_access_clearance_to_tabs_m": access,
        "min_hole_ligament_m": ligament,
        "bolt_side_ligament_m": side_ligament,
        "plate_size_mm": [round(2 * r_o * 1000, 1), round(2 * half_h * 1000, 1)],
        "hub_mass_kg": plate_volume * h.plate_density_kg_m3 + fastener_mass,
        "stack_height_mm": round((zt1 - zb0) * 1000, 2),
    }
    for name, value in margins.items():
        if value < 0.0:
            warnings.append(f"Hub check failed: {name} = {value:.2f}")
    if abs(metrics["tab_centre_offset_from_ideal_m"]) > 2.0e-3:
        warnings.append(
            f"Root tab centre is {metrics['tab_centre_offset_from_ideal_m'] * 1000:.1f} mm off the centrifugal load line: "
            f"in-plane moment {joint['root_inplane_moment_Nm']:.1f} N*m on the bolt pair; set root.tab_centre_s_m: {ideal_s:.4f}"
        )
    if min(contact_top, contact_bottom) < 0.6:
        warnings.append(f"Only {min(contact_top, contact_bottom):.0%} of the tab width touches the plates; raise root.corner_exponent")
    if access < 0.0:
        warnings.append("Motor screws cannot be reached past the blade tabs; rotate motor_pattern_angle_deg")
    if min(ligament, side_ligament) < 1.5e-3:
        warnings.append(f"Hole ligament {min(ligament, side_ligament) * 1000:.1f} mm < 1.5 mm")
    if not h.motor_pattern_confirmed:
        warnings.append(
            f"Motor pattern is a placeholder ({h.motor_screw_count}x{motor.name} on {h.motor_bolt_circle_m * 1000:.0f} mm): "
            "set hub.motor_* from the motor drawing and hub.motor_pattern_confirmed: true"
        )
    bom = _bom(g, h, bolt, motor, blade_length, motor_length, len(bolts_all), cbore_depth)
    return HubResult({"hub_top_plate": top, "hub_bottom_plate": bottom}, fasteners, metrics, warnings, bom)


def _tab_boxes(g, y_c: float) -> list[tuple[float, float, float, float]]:
    half = 0.5 * g.tab_width_m
    return [
        (g.tab_inner_radius_m, g.tab_outer_radius_m, y_c - half, y_c + half),
        (-g.tab_outer_radius_m, -g.tab_inner_radius_m, -y_c - half, -y_c + half),
    ]


def _volume(shape) -> float:
    from prandtl_prop.cad.step import report

    return report(shape).volume_m3


def _bom(
    g, h: HubDesign, bolt: MetricScrew, motor: MetricScrew, blade_length: float, motor_length: float, bolt_count: int, cbore_depth: float
) -> list[dict]:
    mm = lambda v: f"{v * 1000:g}"  # noqa: E731
    return [
        {
            "item": 1,
            "qty": 1,
            "part": "hub_top_plate",
            "description": f"Top clamp plate, {mm(h.top_plate_thickness_m)} mm plate; {bolt_count}x {mm(bolt.clearance_fine)} thru (blade bolts), "
            f"{h.motor_screw_count}x {mm(motor.counterbore_d)} thru (motor screw access), {mm(h.center_bore_m)} centre bore",
            "source": "machined",
            "material": h.plate_material,
        },
        {
            "item": 2,
            "qty": 1,
            "part": "hub_bottom_plate",
            "description": f"Bottom clamp plate, {mm(h.bottom_plate_thickness_m)} mm plate; {bolt_count}x {bolt.name} tapped thru "
            f"({mm(bolt.tap_drill)} drill), {h.motor_screw_count}x {mm(motor.clearance_medium)} thru with {mm(motor.counterbore_d)} x "
            f"{cbore_depth * 1000:.1f} deep counterbore from the blade side, {mm(h.center_bore_m)} centre bore",
            "source": "machined",
            "material": h.plate_material,
        },
        {
            "item": 3,
            "qty": bolt_count,
            "part": f"{bolt.name}x{mm(blade_length)} socket head cap screw",
            "description": f"Blade clamp bolt, class {h.blade_bolt_class}, zinc or black oxide; torque {h.nut_factor * h.blade_bolt_preload_N * bolt.d:.1f} N*m",
            "source": "COTS ISO 4762",
            "material": "steel",
        },
        {
            "item": 4,
            "qty": bolt_count,
            "part": f"{bolt.name} plain washer",
            "description": f"{mm(bolt.washer_od)} OD x {mm(bolt.washer_h)} thick, under the blade bolt heads",
            "source": "COTS ISO 7089",
            "material": "steel, 200 HV",
        },
        {
            "item": 5,
            "qty": h.motor_screw_count,
            "part": f"{motor.name}x{mm(motor_length)} socket head cap screw",
            "description": "Hub to motor, class 12.9 (check against the motor maker's screw-length limit)",
            "source": "COTS ISO 4762",
            "material": "alloy steel",
        },
        {
            "item": 6,
            "qty": 1,
            "part": "Medium-strength threadlocker",
            "description": "All threads (e.g. Loctite 243); clean and prime the aluminium threads",
            "source": "COTS",
            "material": "-",
        },
        {
            "item": 7,
            "qty": bolt_count,
            "part": f"{bolt.name}x{mm(1.5 * bolt.d)} helical insert (optional)",
            "description": "If the bottom plate threads are re-used often; re-drill/tap for the insert",
            "source": "COTS",
            "material": "stainless",
        },
    ]


def export_hub(result: HubResult, folder: Path, blades: Sequence = ()) -> dict[str, Path]:
    """STEP (mm) and STL (m) per machined part, DXF profiles (mm), fasteners, assembly STEP, BOM (CSV, Markdown)."""

    import csv

    from prandtl_prop.cad.step import export_step, export_stl

    cq = _cq()
    folder.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, part in result.parts.items():
        paths[f"{name}_step"] = export_step(part, folder / f"{name}.step")
        paths[f"{name}_stl"] = export_stl(part, folder / f"{name}_m.stl", 5e-5)
        profile = max(
            cq.Workplane().add(part).faces("%PLANE").vals(), key=lambda face: face.Area() if abs(face.normalAt().z) > 0.99 else 0.0
        )
        profile = profile.translate(cq.Vector(0, 0, -profile.Center().z)).scale(1000.0)
        paths[f"{name}_dxf"] = folder / f"{name}_profile_mm.dxf"
        cq.exporters.exportDXF(cq.Workplane("XY").add(profile), str(paths[f"{name}_dxf"]))
    if result.fasteners:
        paths["fasteners_stl"] = export_stl(cq.Compound.makeCompound(result.fasteners), folder / "hub_fasteners_m.stl", 5e-5)
    if blades:
        assembly = cq.Compound.makeCompound([*result.parts.values(), *result.fasteners, *blades])
        paths["assembly_step"] = export_step(assembly, folder / "hub_assembly.step")
        paths["blades_stl"] = export_stl(cq.Compound.makeCompound(list(blades)), folder / "hub_blades_m.stl", 2e-4)
    with (folder / "hub_bom.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(result.bom[0]))
        writer.writeheader()
        writer.writerows(result.bom)
    lines = ["| Item | Qty | Part | Description | Source | Material |", "| --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {r['item']} | {r['qty']} | {r['part']} | {r['description']} | {r['source']} | {r['material']} |" for r in result.bom]
    (folder / "hub_bom.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    paths["bom_csv"], paths["bom_md"] = folder / "hub_bom.csv", folder / "hub_bom.md"
    return paths


__all__ = [
    "SCREWS",
    "HubDesign",
    "HubResult",
    "MetricScrew",
    "bolt_group_shear",
    "build_hub",
    "centrifugal_force",
    "clamp_contact_fraction",
    "export_hub",
    "ideal_tab_centre_s",
    "motor_screw_fit",
    "root_joint_loads",
    "screw_for_hole",
    "tapped_bolt_length",
]
