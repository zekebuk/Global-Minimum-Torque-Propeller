"""Folding hub (one hinge pin per blade) and one-piece fixed prop (molded blades + boss, machined cap).

Both use only 2.5D machined aluminium parts and COTS hardware. Frame and load conventions are those of
``prandtl_prop.cad.hub``.

**Folding.** Each blade's root tab ends in a semicircle around a single hinge pin parallel to the rotor
axis (in-plane folding, the usual multirotor folding prop):

- **Pin and bushing.** The pin is a hardened ISO 8734 dowel. It is pressed into the bottom plate, a
  slip fit in the top plate, and flush with both faces, so nothing threads near the motor. The blade
  turns on a PTFE-lined steel-backed bushing pressed into the tab, so the polymer bore never wears.
- **Plates and core.** Top and bottom plates are spaced by an aluminium core a few hundredths of a
  millimetre thinner than the tab. The light squeeze removes flap slop, so the blade is clamped in
  flap at the plate edge, and gives the fold friction that holds the blades stowed.
- **Fasteners.** Four button-head motor screws run through the whole stack into the motor.
- **Shapes.** The plate outline is the convex hull of circles (arcs and tangent lines) round the
  pins and the motor pattern. The core is that outline minus the area each tab sweeps over its fold
  range.
- **Running lag angle.** In flight the free hinge sets the blade's in-plane angle. The centrifugal
  force (through the rotor axis and the blade CG) must balance the aerodynamic drag moment about the
  pin. ``lag_equilibrium`` solves for that angle and ``ideal_pin_y`` places the pin so that it is
  zero. ``lag_frequency_per_rev`` gives the rigid lag mode, which sits below 1P for a small hinge
  offset.

**One-piece.** Both blades and a cylindrical hub boss are molded as one part (see
``prandtl_prop.cad.step.one_piece_solid``). The boss is clamped between the motor face and a machined
cap by the motor screws. The centrifugal loads of the two blades balance inside the part, so the
motor screws carry only torque and thrust.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy.optimize import brentq

from prandtl_prop.cad.hub import SCREWS, HubDesign, HubResult, MetricScrew, _cq, _cylinder, _volume, centrifugal_force

# ------------------------------------------------------------------------------- 2D geometry helpers


def _convex_hull(points: np.ndarray) -> np.ndarray:
    """Indices of the convex hull, counter-clockwise (Andrew's monotone chain)."""

    order = np.lexsort((points[:, 1], points[:, 0]))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[int] = []
    for i in order:
        while len(lower) >= 2 and cross(points[lower[-2]], points[lower[-1]], points[i]) <= 0:
            lower.pop()
        lower.append(i)
    upper: list[int] = []
    for i in order[::-1]:
        while len(upper) >= 2 and cross(points[upper[-2]], points[upper[-1]], points[i]) <= 0:
            upper.pop()
        upper.append(i)
    return np.array(lower[:-1] + upper[:-1])


def hull_of_circles(circles: Sequence[tuple[float, float, float]]) -> list[tuple[str, tuple]]:
    """Exact convex hull of circles (cx, cy, r) as CCW segments: ("arc", (circle, a0, a1)) and ("line", (p0, p1))."""

    samples, owner = [], []
    for index, (cx, cy, r) in enumerate(circles):
        for a in np.linspace(0.0, 2.0 * math.pi, 720, endpoint=False):
            samples.append((cx + r * math.cos(a), cy + r * math.sin(a)))
            owner.append(index)
    hull = _convex_hull(np.array(samples))
    sequence: list[int] = []
    for i in hull:
        if not sequence or owner[i] != sequence[-1]:
            sequence.append(owner[i])
    if len(sequence) > 1 and sequence[0] == sequence[-1]:
        sequence.pop()
    if len(sequence) == 1:
        return [("arc", (circles[sequence[0]], 0.0, 2.0 * math.pi))]
    tangents = []
    for k, i in enumerate(sequence):
        j = sequence[(k + 1) % len(sequence)]
        (x1, y1, r1), (x2, y2, r2) = circles[i], circles[j]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        phi = math.atan2(dy, dx) - math.acos(max(-1.0, min(1.0, (r1 - r2) / length)))  # outward normal, right of travel
        tangents.append((i, j, phi))
    segments: list[tuple[str, tuple]] = []
    for k, (i, j, phi) in enumerate(tangents):
        (x1, y1, r1), (x2, y2, r2) = circles[i], circles[j]
        segments.append(("line", ((x1 + r1 * math.cos(phi), y1 + r1 * math.sin(phi)), (x2 + r2 * math.cos(phi), y2 + r2 * math.sin(phi)))))
        next_phi = tangents[(k + 1) % len(tangents)][2]
        a0, a1 = phi, next_phi
        while a1 <= a0:
            a1 += 2.0 * math.pi
        segments.append(("arc", (circles[j], a0, a1)))
    return segments


def hull_polygon(circles: Sequence[tuple[float, float, float]], step_deg: float = 2.0) -> np.ndarray:
    points = []
    for kind, data in hull_of_circles(circles):
        if kind == "line":
            points.append(data[0])
        else:
            (cx, cy, r), a0, a1 = data
            for a in np.arange(a0, a1, math.radians(step_deg)):
                points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return np.array(points)


def hull_solid(circles: Sequence[tuple[float, float, float]], z0: float, thickness: float):
    """Extruded convex hull of circles: arcs and tangent lines, so CAM sees true arcs."""

    cq = _cq()
    edges = []
    for kind, data in hull_of_circles(circles):
        if kind == "line":
            (p0, p1) = data
            edges.append(cq.Edge.makeLine(cq.Vector(*p0, z0), cq.Vector(*p1, z0)))
        else:
            (cx, cy, r), a0, a1 = data
            if a1 - a0 >= 2.0 * math.pi - 1e-9:
                edges.append(cq.Edge.makeCircle(r, cq.Vector(cx, cy, z0)))
                continue
            am = 0.5 * (a0 + a1)
            pts = [cq.Vector(cx + r * math.cos(a), cy + r * math.sin(a), z0) for a in (a0, am, a1)]
            edges.append(cq.Edge.makeThreePointArc(*pts))
    face = cq.Face.makeFromWires(cq.Wire.assembleEdges(edges))
    return cq.Solid.extrudeLinear(face, cq.Vector(0, 0, thickness))


def _sector_solid(cx: float, cy: float, radius: float, a0_deg: float, a1_deg: float, z0: float, z1: float):
    cq = _cq()
    angles = np.radians(np.linspace(a0_deg, a1_deg, max(8, int(abs(a1_deg - a0_deg) / 5) + 2)))
    points = [cq.Vector(cx, cy, z0)] + [cq.Vector(cx + radius * math.cos(a), cy + radius * math.sin(a), z0) for a in angles]
    face = cq.Face.makeFromWires(cq.Wire.makePolygon(points, close=True))
    return cq.Solid.extrudeLinear(face, cq.Vector(0, 0, z1 - z0))


def _chamfer_outer(solid, face_selector: str, size: float):
    cq = _cq()
    try:
        out = cq.Workplane().add(solid).faces(face_selector).edges().chamfer(size).val()
        return out if out.isValid() else solid
    except Exception:  # noqa: BLE001 - keep a sharp (deburred) edge if the chamfer fails
        return solid


def _segments_intersect(a: np.ndarray, b: np.ndarray) -> bool:
    """Any crossing between the edges of closed polygons a and b (vectorised)."""

    a0, a1 = a, np.roll(a, -1, axis=0)
    b0, b1 = b, np.roll(b, -1, axis=0)

    def orient(p, q, r):
        return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])

    p, q = a0[:, None, :], a1[:, None, :]
    r, s = b0[None, :, :], b1[None, :, :]
    d1, d2 = orient(p, q, r), orient(p, q, s)
    d3, d4 = orient(r, s, p), orient(r, s, q)
    return bool(np.any((d1 * d2 < 0) & (d3 * d4 < 0)))


def _inside(polygon: np.ndarray, points: np.ndarray) -> np.ndarray:
    x, y = points[:, 0][:, None], points[:, 1][:, None]
    x0, y0 = polygon[:, 0][None, :], polygon[:, 1][None, :]
    x1, y1 = np.roll(polygon[:, 0], -1)[None, :], np.roll(polygon[:, 1], -1)[None, :]
    crosses = ((y0 > y) != (y1 > y)) & (x < (x1 - x0) * (y - y0) / np.where(y1 == y0, 1e-300, y1 - y0) + x0)
    return np.count_nonzero(crosses, axis=1) % 2 == 1


def _rotate(points: np.ndarray, centre: Sequence[float], angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    d = points - np.asarray(centre)
    return np.column_stack([c * d[:, 0] - s * d[:, 1], s * d[:, 0] + c * d[:, 1]]) + np.asarray(centre)


def planform(outlines: Sequence[np.ndarray]) -> np.ndarray:
    """Plan-view footprint (x, y) of a blade from its constant-x outlines: LE edge out, TE edge back."""

    upper = [(float(o[0, 0]), float(o[:, 1].max())) for o in outlines]
    lower = [(float(o[0, 0]), float(o[:, 1].min())) for o in outlines]
    return np.array(upper + lower[::-1])


# --------------------------------------------------------------------------------- folding analysis


def _cf_moment(mass: float, cg_xy: np.ndarray, pin: np.ndarray, omega: float, zeta: float) -> float:
    c = pin + _rotate((cg_xy - pin)[None, :], (0.0, 0.0), zeta)[0]
    d = c - pin
    return mass * omega**2 * (d[0] * c[1] - d[1] * c[0])


def lag_equilibrium(mass: float, cg_m: Sequence[float], pin_xy: Sequence[float], rpm: float, aero_moment_Nm: float) -> float:
    """In-plane blade angle about the hinge (rad, + = lead) where centrifugal and aero moments balance."""

    omega = rpm * math.pi / 30.0
    cg, pin = np.array(cg_m[:2], dtype=float), np.array(pin_xy, dtype=float)
    return float(brentq(lambda z: _cf_moment(mass, cg, pin, omega, z) + aero_moment_Nm, -0.6, 0.6))


def ideal_pin_y(mass: float, cg_m: Sequence[float], pin_x: float, rpm: float, aero_moment_Nm: float) -> float:
    """Pin y that makes the running lag angle zero: m w^2 (y_p x_cg - x_p y_cg) = -M_aero."""

    omega = rpm * math.pi / 30.0
    return (pin_x * cg_m[1] - aero_moment_Nm / (mass * omega**2)) / cg_m[0]


def lag_frequency_per_rev(
    mass: float, cg_m: Sequence[float], pin_xy: Sequence[float], inertia_cg_zz: float, rpm: float, zeta: float = 0.0
) -> float:
    """Rigid lag mode about the hinge, per rev: sqrt(-dM_cf/dzeta / I_pin) / Omega (centrifugal stiffness only)."""

    omega = rpm * math.pi / 30.0
    cg, pin = np.array(cg_m[:2], dtype=float), np.array(pin_xy, dtype=float)
    h = 1.0e-4
    stiffness = -(_cf_moment(mass, cg, pin, omega, zeta + h) - _cf_moment(mass, cg, pin, omega, zeta - h)) / (2.0 * h)
    inertia = inertia_cg_zz + mass * float(np.sum((cg - pin) ** 2))
    return math.sqrt(max(stiffness, 0.0) / inertia) / omega


def max_fold_angle(
    blade_planform: np.ndarray,
    pin_xy: Sequence[float],
    hub_outline: np.ndarray | None = None,
    blade_points: np.ndarray | None = None,
    *,
    plate_radius_m: float = 0.0,
    slot_radius_m: float = 0.0,
    slot_half_height_m: float = 0.0,
    z_centre_m: float = 0.0,
    limit_deg: float = 180.0,
) -> float:
    """Largest same-side fold (blade 1 lags, blade 2 leads, both by the angle) before anything touches.

    Blade against blade: plan-view footprints. Blade against hub: a blade point (3D) beyond its own
    plate circle that lands inside the hub outline collides if it is above or below the tab slot
    (|z - z_centre| > slot half height: a plate) or beyond the core cut-out around its pin (the core).
    """

    pin1 = np.asarray(pin_xy, dtype=float)
    blade2, pin2 = -blade_planform, -pin1
    if blade_points is not None:
        d0 = np.hypot(blade_points[:, 0] - pin1[0], blade_points[:, 1] - pin1[1])
        keep = d0 > plate_radius_m
        points = blade_points[keep]
        proud = np.abs(points[:, 2] - z_centre_m) > slot_half_height_m
        far = d0[keep] > slot_radius_m
        risky = proud | far
    last = 0.0
    for deg in np.arange(1.0, limit_deg + 0.5, 1.0):
        a = math.radians(deg)
        b1 = _rotate(blade_planform, pin1, -a)
        b2 = _rotate(blade2, pin2, +a)
        hit = _segments_intersect(b1, b2) or bool(np.any(_inside(b1, b2))) or bool(np.any(_inside(b2, b1)))
        if not hit and hub_outline is not None and blade_points is not None and risky.any():
            p1 = _rotate(points[risky][:, :2], pin1, -a)  # blade 2 is the 180 deg image: same test by symmetry
            hit = bool(np.any(_inside(hub_outline, p1)))
        if hit:
            return last
        last = float(deg)
    return last


def folding_stack(design: HubDesign, tab_thickness_m: float, motor: MetricScrew) -> dict | None:
    """Stock plate thicknesses, dowel and motor-screw lengths for the folding stack (None if nothing fits).

    Dowel: flush with the top plate's top face, pressed >= 1 d into the bottom plate, not past its far
    face. Motor screw (button head on the top plate): engagement in the motor of max(0.8 d, 3 pitches)
    to the motor thread depth - 0.5 mm.
    """

    d = design.hinge_pin_diameter_m
    eng_min, eng_max = max(0.8 * motor.d, 3.0 * motor.pitch), design.motor_thread_depth_m - 0.5e-3
    best = None
    for t_top in design.top_plate_options_m:
        for t_bot in design.bottom_plate_options_m:
            dowel = next((L for L in design.dowel_lengths_m if d <= L - t_top - tab_thickness_m <= t_bot + 1e-9), None)
            grip = t_top + tab_thickness_m + t_bot
            screw = next((L for L in design.standard_lengths_m if eng_min <= L - grip <= eng_max), None)
            if dowel is None or screw is None:
                continue
            candidate = {
                "top_plate_m": t_top,
                "bottom_plate_m": t_bot,
                "dowel_length_m": dowel,
                "dowel_press_depth_m": dowel - t_top - tab_thickness_m,
                "motor_screw_length_m": screw,
                "motor_screw_engagement_m": screw - grip,
            }
            if best is None or (t_top + t_bot, dowel) < (best["top_plate_m"] + best["bottom_plate_m"], best["dowel_length_m"]):
                best = candidate
    return best


# -------------------------------------------------------------------------------------- builders


def _motor_holes(design: HubDesign) -> list[tuple[float, float]]:
    angles = [math.radians(design.motor_pattern_angle_deg + k * 360.0 / design.motor_screw_count) for k in range(design.motor_screw_count)]
    return [(0.5 * design.motor_bolt_circle_m * math.cos(a), 0.5 * design.motor_bolt_circle_m * math.sin(a)) for a in angles]


def build_folding_hub(
    root_geometry,
    pin_centre_m: tuple[float, float, float],
    tab_outline_3d: np.ndarray,
    blade_outlines: Sequence[np.ndarray],
    *,
    blade_mass_kg: float,
    blade_cg_m: Sequence[float],
    blade_inertia_cg_zz: float,
    rpm: float,
    thrust_N: float,
    torque_Nm: float,
    aero_moment_about_pin_Nm: float,
    root_flap_moment_Nm: float,
    tab_allowable_Pa: float,
    design: HubDesign,
) -> HubResult:
    g, h = root_geometry, design
    if g.tab_end != "round" or len(g.bolt_radii_m) != 1:
        raise ValueError("The folding hub needs root.tab_end: round and a single hinge radius in root.bolt_radii_m")
    warnings: list[str] = []
    motor = SCREWS[h.motor_screw]
    z_top, z_bot = float(tab_outline_3d[:, 2].max()), float(tab_outline_3d[:, 2].min())
    tab_t = z_top - z_bot
    stack = folding_stack(h, tab_t, motor)
    if stack is None:
        raise ValueError("No stock plate / dowel / motor-screw combination fits the folding stack; widen the options")
    t_top, t_bot = stack["top_plate_m"], stack["bottom_plate_m"]
    pin1 = np.array(pin_centre_m[:2], dtype=float)
    pins = [pin1, -pin1]
    r_end = 0.5 * g.tab_width_m
    r_plate = r_end + h.plate_margin_m
    motor_xy = _motor_holes(h)
    r_centre = max(0.5 * h.motor_bolt_circle_m + 0.5 * motor.clearance_medium + 3.0e-3, 0.5 * h.center_bore_m + 4.0e-3)
    circles = [(float(p[0]), float(p[1]), r_plate) for p in pins] + [(0.0, 0.0, r_centre)]
    inset = 1.0e-3  # the core sits 1 mm inside the plates: a shadow line, and no exposed seam

    # --- machined parts
    zb0, zt1 = z_bot - t_bot, z_top + t_top
    bottom = _chamfer_outer(hull_solid(circles, zb0, t_bot), "<Z", h.edge_chamfer_m)
    top = _chamfer_outer(hull_solid(circles, z_top, t_top), ">Z", h.edge_chamfer_m)
    # Core: only the centre disc (the squeezed tabs support the plates near the pins), minus the fold sweeps.
    core = _cylinder(r_centre - inset, z_bot, z_top - h.spacer_squeeze_m, 0.0, 0.0)
    margin_deg = 5.0
    fold = h.fold_angle_deg
    sweeps = [(-90.0 - fold - margin_deg, 90.0 + margin_deg), (90.0 - margin_deg, 270.0 + fold + margin_deg)]
    for pin, (a0, a1) in zip(pins, sweeps, strict=True):
        core = core.cut(_cylinder(r_end + h.fold_clearance_m, z_bot - 1e-3, z_top + 1e-3, float(pin[0]), float(pin[1])))
        core = core.cut(
            _sector_solid(float(pin[0]), float(pin[1]), r_plate + h.fold_clearance_m + inset, a0, a1, z_bot - 1e-3, z_top + 1e-3)
        )
    for x, y in motor_xy:
        hole = _cylinder(0.5 * motor.clearance_medium, zb0 - 1e-3, zt1 + 1e-3, x, y)
        top, core, bottom = top.cut(hole), core.cut(hole), bottom.cut(hole)
    bore = _cylinder(0.5 * h.center_bore_m, zb0 - 1e-3, zt1 + 1e-3, 0.0, 0.0)
    top, core, bottom = top.cut(bore), core.cut(bore), bottom.cut(bore)
    for pin in pins:
        hole = _cylinder(0.5 * h.hinge_pin_diameter_m, zb0 - 1e-3, zt1 + 1e-3, float(pin[0]), float(pin[1]))
        top, bottom = top.cut(hole), bottom.cut(hole)
    core_solids = core.Solids()
    if len(core_solids) != 1:
        warnings.append(f"Core spacer splits into {len(core_solids)} pieces: increase the hinge radius or reduce the fold range")
    top, core, bottom = top.clean(), core.clean(), bottom.clean()

    # --- COTS solids
    fasteners = []
    for pin in pins:
        x, y = float(pin[0]), float(pin[1])
        dowel_bottom = z_top + t_top - stack["dowel_length_m"]
        fasteners.append(_cylinder(0.5 * h.hinge_pin_diameter_m, dowel_bottom, zt1, x, y))
        z_mid = 0.5 * (z_top + z_bot)
        fasteners.append(
            _cylinder(0.5 * h.hinge_bushing_od_m, z_mid - 0.5 * h.hinge_bushing_length_m, z_mid + 0.5 * h.hinge_bushing_length_m, x, y).cut(
                _cylinder(0.5 * h.hinge_pin_diameter_m, z_mid - h.hinge_bushing_length_m, z_mid + h.hinge_bushing_length_m, x, y)
            )
        )
    for x, y in motor_xy:
        head = _cylinder(0.5 * motor.button_head_d, zt1, zt1 + motor.button_head_h, x, y)
        fasteners.append(head.fuse(_cylinder(0.5 * motor.d, zt1 - stack["motor_screw_length_m"], zt1, x, y)))

    # --- checks
    f_c = float(np.linalg.norm(centrifugal_force(blade_mass_kg, blade_cg_m, rpm)))
    d, od, L = h.hinge_pin_diameter_m, h.hinge_bushing_od_m, h.hinge_bushing_length_m
    sf = h.safety_factor
    pin_shear = f_c / (2.0 * math.pi * (0.5 * d) ** 2)
    bushing_pressure = f_c / (d * L)
    tab_bearing = f_c / (od * tab_t)
    tab_shear_out = f_c / (2.0 * (r_end - 0.5 * od) * tab_t)
    plate_bearing = 0.5 * f_c / (d * min(t_top, stack["dowel_press_depth_m"]))
    plate_shear_out = 0.5 * f_c / (2.0 * (r_plate - 0.5 * d) * t_top)
    polygon = hull_polygon(circles)
    near_axis = polygon[np.abs(polygon[:, 0]) < 2e-3]
    width_centre = (2.0 * float(np.max(np.abs(near_axis[:, 1]))) if len(near_axis) else 2.0 * r_centre) - h.center_bore_m
    share_bottom = t_bot**3 / (t_bot**3 + t_top**3)
    bending = max(
        share_bottom * root_flap_moment_Nm / (width_centre * t_bot**2 / 6.0),
        (1.0 - share_bottom) * root_flap_moment_Nm / (width_centre * t_top**2 / 6.0),
    )
    tension = f_c / (width_centre * (t_top + t_bot))
    overlap = r_end + r_plate
    flap_contact = 6.0 * root_flap_moment_Nm / (g.tab_width_m * overlap**2)
    zeta = lag_equilibrium(blade_mass_kg, blade_cg_m, pin1, rpm, aero_moment_about_pin_Nm)
    nu = lag_frequency_per_rev(blade_mass_kg, blade_cg_m, pin1, blade_inertia_cg_zz, rpm, zeta)
    pin_y_ideal = ideal_pin_y(blade_mass_kg, blade_cg_m, float(pin1[0]), rpm, aero_moment_about_pin_Nm)
    plan = planform(blade_outlines)
    points = np.vstack([o[::3] for o in blade_outlines if float(o[0, 0]) < float(pin1[0]) + 0.08])
    fold_max = max_fold_angle(
        plan,
        pin1,
        polygon,
        points,
        plate_radius_m=r_plate,
        slot_radius_m=r_plate + h.fold_clearance_m + inset,
        slot_half_height_m=0.5 * tab_t - 0.05e-3,
        z_centre_m=0.5 * (z_top + z_bot),
        limit_deg=max(h.fold_angle_deg + 30.0, 120.0),
    )
    ligament = min(
        min(
            (
                math.hypot(x - p[0], y - p[1])
                - ((r_plate + h.fold_clearance_m + inset) if _in_sweep(x, y, p, sweep) else (r_end + h.fold_clearance_m))
            )
            for p, sweep in zip(pins, sweeps, strict=True)
        )
        - 0.5 * motor.clearance_medium
        for x, y in motor_xy
    )
    margins = {
        "hinge_pin_shear_margin": (h.hinge_pin_shear_strength_Pa / sf) / pin_shear - 1.0,
        "bushing_pressure_margin": (h.hinge_bushing_static_pressure_Pa / sf) / bushing_pressure - 1.0,
        "tab_bearing_margin": tab_allowable_Pa / tab_bearing - 1.0,
        "tab_shear_out_margin": 0.5 * tab_allowable_Pa / tab_shear_out - 1.0,
        "plate_bearing_margin": (h.plate_yield_Pa / sf) / plate_bearing - 1.0,
        "plate_shear_out_margin": (0.58 * h.plate_yield_Pa / sf) / plate_shear_out - 1.0,
        "plate_fatigue_margin": (h.plate_fatigue_Pa / sf) / (bending + tension) - 1.0,
        "tab_flap_contact_margin": tab_allowable_Pa / flap_contact - 1.0,
        "fold_angle_margin_deg": fold_max - h.fold_angle_deg,
        "core_ligament_margin_m": ligament - 1.5e-3,
    }
    metrics = {
        "variant": "folding",
        "centrifugal_force_N": f_c,
        **margins,
        **{k: v for k, v in stack.items()},
        "hinge_pin": f"ISO 8734 {d * 1000:g}x{stack['dowel_length_m'] * 1000:g} m6 hardened dowel",
        "hinge_bushing": f"PTFE-lined bushing {d * 1000:g} ID x {od * 1000:g} OD x {L * 1000:g} (DU-type {d * 1000:02.0f}{L * 1000:02.0f})",
        "motor_screw": f"ISO 7380 {motor.name}x{stack['motor_screw_length_m'] * 1000:g} button head, class 10.9",
        "hinge_pin_shear_stress_Pa": pin_shear,
        "bushing_pressure_Pa": bushing_pressure,
        "tab_bearing_stress_Pa": tab_bearing,
        "tab_shear_out_stress_Pa": tab_shear_out,
        "tab_end_edge_distance_over_bushing_od": r_end / od,
        "plate_centre_stress_Pa": bending + tension,
        "tab_flap_contact_pressure_Pa": flap_contact,
        "running_lag_angle_deg": math.degrees(zeta),
        "lag_frequency_per_rev": nu,
        "ideal_pin_y_m": pin_y_ideal,
        "pin_y_offset_from_ideal_m": float(pin1[1] - pin_y_ideal),
        "max_same_side_fold_deg": fold_max,
        "motor_screw_tension_N": thrust_N / h.motor_screw_count,
        "motor_screw_shear_N": torque_Nm / (h.motor_screw_count * 0.5 * h.motor_bolt_circle_m),
        "hub_span_mm": round(2.0 * (float(np.max(np.abs(polygon[:, 0])))) * 1000, 1),
        "hub_width_mm": round(2.0 * float(np.max(np.abs(polygon[:, 1]))) * 1000, 1),
        "stack_height_mm": round((zt1 - zb0) * 1000, 2),
        "hub_mass_kg": sum(_volume(p) for p in (top, core, bottom)) * h.plate_density_kg_m3 + sum(_volume(f) for f in fasteners) * 7900.0,
    }
    for name, value in margins.items():
        if value < 0.0:
            warnings.append(f"Folding hub check failed: {name} = {value:.3g}")
    if abs(metrics["running_lag_angle_deg"]) > 2.0:
        warnings.append(f"Running lag angle {metrics['running_lag_angle_deg']:.1f} deg; set root.tab_centre_s_m = {-pin_y_ideal:.4f}")
    if abs(nu - 1.0) < 0.1:
        warnings.append(f"Rigid lag mode at {nu:.2f}/rev is within 10% of 1P")
    if r_end / od < 1.5:
        warnings.append(f"Tab end edge distance {r_end / od:.2f} x bushing OD < 1.5")
    _placeholder(h, motor, warnings)
    bom = _folding_bom(h, motor, stack)
    return HubResult({"hub_top_plate": top, "hub_core_spacer": core, "hub_bottom_plate": bottom}, fasteners, metrics, warnings, bom)


def _in_sweep(x: float, y: float, pin: np.ndarray, sweep: tuple[float, float]) -> bool:
    angle = math.degrees(math.atan2(y - pin[1], x - pin[0]))
    a0, a1 = sweep
    for k in (-360.0, 0.0, 360.0):
        if a0 <= angle + k <= a1:
            return True
    return False


def build_one_piece_hub(
    boss_centre_z_m: float,
    *,
    half_mass_kg: float,
    half_cg_m: Sequence[float],
    rpm: float,
    thrust_N: float,
    torque_Nm: float,
    flap_moment_at_axis_Nm: float,
    flap_moment_at_boss_edge_Nm: float,
    neck_section,
    prop_allowable_Pa: float,
    prop_safety_factor: float,
    design: HubDesign,
) -> HubResult:
    """Machined cap + motor screws for the one-piece prop, and the checks on its molded boss."""

    h = design
    warnings: list[str] = []
    motor = SCREWS[h.motor_screw]
    radius, t_boss = 0.5 * h.boss_diameter_m, h.boss_thickness_m
    eng_min, eng_max = max(0.8 * motor.d, 3.0 * motor.pitch), h.motor_thread_depth_m - 0.5e-3
    choice = None
    for t_cap in h.cap_options_m:
        screw = next((L for L in h.standard_lengths_m if eng_min <= L - (t_cap + t_boss) <= eng_max), None)
        if screw is not None:
            choice = (t_cap, screw)
            break
    if choice is None:
        raise ValueError("No stock cap thickness gives a standard motor-screw length; adjust hub.boss_thickness_m or cap_options_m")
    t_cap, screw_length = choice
    z_top = boss_centre_z_m + 0.5 * t_boss
    cap = _chamfer_outer(_cylinder(radius, z_top, z_top + t_cap, 0.0, 0.0), ">Z", 1.5 * h.edge_chamfer_m)
    motor_xy = _motor_holes(h)
    for x, y in motor_xy:
        cap = cap.cut(_cylinder(0.5 * motor.clearance_medium, z_top - 1e-3, z_top + t_cap + 1e-3, x, y))
    cap = cap.cut(_cylinder(0.5 * h.center_bore_m, z_top - 1e-3, z_top + t_cap + 1e-3, 0.0, 0.0)).clean()
    fasteners = []
    zt1 = z_top + t_cap
    for x, y in motor_xy:
        fasteners.append(
            _cylinder(0.5 * motor.button_head_d, zt1, zt1 + motor.button_head_h, x, y).fuse(
                _cylinder(0.5 * motor.d, zt1 - screw_length, zt1, x, y)
            )
        )

    f_c = float(np.linalg.norm(centrifugal_force(half_mass_kg, half_cg_m, rpm)))
    allow = prop_allowable_Pa / prop_safety_factor
    hole = motor.clearance_medium
    width_axis = h.boss_diameter_m - h.center_bore_m
    x_holes = abs(motor_xy[0][0])
    width_holes = 2.0 * math.sqrt(max(radius**2 - x_holes**2, 0.0)) - 2.0 * hole
    boss_axis = f_c / (width_axis * t_boss) + flap_moment_at_axis_Nm / (width_axis * t_boss**2 / 6.0)
    boss_holes = f_c / (width_holes * t_boss) + flap_moment_at_axis_Nm / (width_holes * t_boss**2 / 6.0)
    neck = f_c / neck_section.area_m2 + flap_moment_at_boss_edge_Nm * (0.5 * neck_section.height_m) / neck_section.I_flap_m4
    cap_area = math.pi * radius**2 - math.pi * (0.5 * h.center_bore_m) ** 2 - len(motor_xy) * math.pi * (0.5 * hole) ** 2
    clamp_pressure = len(motor_xy) * h.motor_screw_preload_N / cap_area
    friction_torque = 2.0 * h.friction_coefficient * len(motor_xy) * h.motor_screw_preload_N * 0.5 * h.motor_bolt_circle_m
    margins = {
        "boss_section_at_axis_margin": allow / boss_axis - 1.0,
        "boss_section_at_motor_holes_margin": allow / boss_holes - 1.0,
        "neck_at_boss_edge_margin": allow / neck - 1.0,
        "cap_clamp_pressure_margin": prop_allowable_Pa / clamp_pressure - 1.0,
        "torque_slip_margin": friction_torque / (h.safety_factor * torque_Nm) - 1.0,
    }
    metrics = {
        "variant": "one_piece",
        "centrifugal_force_across_axis_N": f_c,
        **margins,
        "cap_thickness_m": t_cap,
        "motor_screw": f"ISO 7380 {motor.name}x{screw_length * 1000:g} button head, class 10.9",
        "motor_screw_engagement_m": screw_length - (t_cap + t_boss),
        "boss_stress_at_axis_Pa": boss_axis,
        "boss_stress_at_motor_holes_Pa": boss_holes,
        "neck_stress_at_boss_edge_Pa": neck,
        "cap_clamp_pressure_Pa": clamp_pressure,
        "motor_screw_tension_N": thrust_N / h.motor_screw_count,
        "hub_mass_kg": _volume(cap) * h.plate_density_kg_m3 + sum(_volume(f) for f in fasteners) * 7900.0,
    }
    for name, value in margins.items():
        if value < 0.0:
            warnings.append(f"One-piece hub check failed: {name} = {value:.3g}")
    _placeholder(h, motor, warnings)
    bom = _one_piece_bom(h, motor, t_cap, screw_length)
    return HubResult({"hub_cap": cap}, fasteners, metrics, warnings, bom)


def _placeholder(h: HubDesign, motor: MetricScrew, warnings: list[str]) -> None:
    if not h.motor_pattern_confirmed:
        warnings.append(
            f"Motor pattern is a placeholder ({h.motor_screw_count}x{motor.name} on {h.motor_bolt_circle_m * 1000:.0f} mm): "
            "set hub.motor_* from the motor drawing and hub.motor_pattern_confirmed: true"
        )


def _folding_bom(h: HubDesign, motor: MetricScrew, stack: dict) -> list[dict]:
    mm = lambda v: f"{v * 1000:g}"  # noqa: E731
    d, od, L = h.hinge_pin_diameter_m, h.hinge_bushing_od_m, h.hinge_bushing_length_m
    return [
        {
            "item": 1,
            "qty": 1,
            "part": "hub_top_plate",
            "description": f"Top plate, {mm(stack['top_plate_m'])} mm; 2x {mm(d)} H7 hinge holes, 4x {mm(motor.clearance_medium)} thru, {mm(h.center_bore_m)} bore; {mm(h.edge_chamfer_m)} mm chamfer on the top outer edge",
            "source": "machined",
            "material": h.plate_material,
        },
        {
            "item": 2,
            "qty": 1,
            "part": "hub_core_spacer",
            "description": f"Core spacer, tab thickness - {h.spacer_squeeze_m * 1000:.2f} mm (face to fit the molded tabs); 4x {mm(motor.clearance_medium)} thru, {mm(h.center_bore_m)} bore",
            "source": "machined",
            "material": h.plate_material,
        },
        {
            "item": 3,
            "qty": 1,
            "part": "hub_bottom_plate",
            "description": f"Bottom plate, {mm(stack['bottom_plate_m'])} mm; 2x {mm(d)} N7 hinge holes (press), 4x {mm(motor.clearance_medium)} thru, {mm(h.center_bore_m)} bore; {mm(h.edge_chamfer_m)} mm chamfer on the bottom outer edge",
            "source": "machined",
            "material": h.plate_material,
        },
        {
            "item": 4,
            "qty": 2,
            "part": f"{mm(d)}x{mm(stack['dowel_length_m'])} dowel pin, m6",
            "description": f"Hinge pin, hardened; pressed {stack['dowel_press_depth_m'] * 1000:.1f} mm into the bottom plate, flush top",
            "source": "COTS ISO 8734",
            "material": "hardened steel",
        },
        {
            "item": 5,
            "qty": 2,
            "part": f"{mm(d)}x{mm(od)}x{mm(L)} PTFE-lined bushing",
            "description": "Pressed into each blade's hinge bore (DU-type, steel-backed)",
            "source": "COTS",
            "material": "steel/bronze/PTFE",
        },
        {
            "item": 6,
            "qty": h.motor_screw_count,
            "part": f"{motor.name}x{mm(stack['motor_screw_length_m'])} button head screw",
            "description": "Through the whole stack into the motor, class 10.9 (check the motor maker's screw-length limit)",
            "source": "COTS ISO 7380",
            "material": "alloy steel, black oxide",
        },
        {
            "item": 7,
            "qty": 1,
            "part": "Medium-strength threadlocker",
            "description": "Motor screws (e.g. Loctite 243)",
            "source": "COTS",
            "material": "-",
        },
    ]


def _one_piece_bom(h: HubDesign, motor: MetricScrew, t_cap: float, screw_length: float) -> list[dict]:
    mm = lambda v: f"{v * 1000:g}"  # noqa: E731
    return [
        {
            "item": 1,
            "qty": 1,
            "part": "molded one-piece prop",
            "description": f"Both blades and a {mm(h.boss_diameter_m)} x {mm(h.boss_thickness_m)} mm hub boss, center-gated; drill/ream the {mm(h.center_bore_m)} bore after degating",
            "source": "molded (mold stage)",
            "material": "per structure.material",
        },
        {
            "item": 2,
            "qty": 1,
            "part": "hub_cap",
            "description": f"Clamp cap, {mm(h.boss_diameter_m)} dia x {mm(t_cap)} mm; 4x {mm(motor.clearance_medium)} thru, {mm(h.center_bore_m)} bore; chamfered top edge",
            "source": "machined",
            "material": h.plate_material,
        },
        {
            "item": 3,
            "qty": h.motor_screw_count,
            "part": f"{motor.name}x{mm(screw_length)} button head screw",
            "description": f"Through cap and boss into the motor, class 10.9; preload about {h.motor_screw_preload_N:.0f} N (re-check after the first runs: polymer boss)",
            "source": "COTS ISO 7380",
            "material": "alloy steel, black oxide",
        },
        {
            "item": 4,
            "qty": 1,
            "part": "Medium-strength threadlocker",
            "description": "Motor screws (e.g. Loctite 243)",
            "source": "COTS",
            "material": "-",
        },
    ]


def fold_blades(blade, pin_xy: Sequence[float], angle_deg: float) -> list:
    """Blade 1 lagged and blade 2 led by ``angle_deg`` about their pins (the stowed same-side fold)."""

    cq = _cq()
    x, y = float(pin_xy[0]), float(pin_xy[1])
    blade1 = blade.rotate(cq.Vector(x, y, 0), cq.Vector(x, y, 1), -angle_deg)
    blade2 = blade.rotate(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), 180.0).rotate(cq.Vector(-x, -y, 0), cq.Vector(-x, -y, 1), angle_deg)
    return [blade1, blade2]


__all__ = [
    "build_folding_hub",
    "build_one_piece_hub",
    "fold_blades",
    "folding_stack",
    "hull_of_circles",
    "hull_solid",
    "ideal_pin_y",
    "lag_equilibrium",
    "lag_frequency_per_rev",
    "max_fold_angle",
    "planform",
]
