"""CadQuery reference geometry for every printed part (and keep-out envelopes for bought parts).

All parts are generated in their *assembly* position (global frame, see geometry.py) except the arm,
which is generated in arm-local coordinates and placed by `place_arm`.
Print orientations are documented in print_plan.py.
"""

from __future__ import annotations

import math

import cadquery as cq

from ..geometry import FrameGeometry, arm_axes, arm_height_at, arm_section_profile

COS30 = math.cos(math.radians(30))


# ----------------------------------------------------------------------------- helpers
def cyl(x: float, y: float, z0: float, h: float, d: float) -> cq.Workplane:
    return cq.Workplane("XY").workplane(offset=z0).center(x, y).circle(d / 2).extrude(h)


def box(x0, x1, y0, y1, z0, z1) -> cq.Workplane:
    return cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0, centered=False).translate((x0, y0, z0))


def hexagon(x: float, y: float, z0: float, h: float, af: float) -> cq.Workplane:
    return cq.Workplane("XY").workplane(offset=z0).center(x, y).polygon(6, af / COS30).extrude(h)


def place(wp: cq.Workplane, ang_deg: float, r: float, z: float = 0.0) -> cq.Workplane:
    a = math.radians(ang_deg)
    return wp.rotate((0, 0, 0), (0, 0, 1), ang_deg).translate((r * math.cos(a), r * math.sin(a), z))


def union_all(items):
    out = items[0]
    for it in items[1:]:
        out = out.union(it)
    return out


def motor_hole_points(g: FrameGeometry):
    """16x16 pattern rotated 45 deg ('+' on the arm axis) and 19x19 square ('x'): no overlap."""
    pts = []
    r16 = g.motor_patterns[0] / math.sqrt(2)
    pts += [(r16, 0.0), (-r16, 0.0), (0.0, r16), (0.0, -r16)]
    h19 = g.motor_patterns[1] / 2
    pts += [(h19, h19), (h19, -h19), (-h19, h19), (-h19, -h19)]
    return pts


def tenon_plan(g: FrameGeometry, grow: float = 0.0, x_out: float | None = None):
    """Arm root plan outline (arm-local XY). grow > 0 gives the matching socket pocket."""
    Lt, bt, b, c = g.tenon_L, g.tenon_b / 2 + grow, g.arm_b / 2 + grow, g.tenon_chamfer
    x_out = g.arm_L if x_out is None else x_out
    return [(-Lt - grow, -bt), (-c, -bt), (0.0, -b), (x_out, -b), (x_out, b), (0.0, b), (-c, bt), (-Lt - grow, bt)]


# ----------------------------------------------------------------------------- arm (PPA-CF)
def make_arm(g: FrameGeometry) -> cq.Workplane:
    L, Lt, hr, ht = g.arm_L, g.tenon_L, g.arm_h_root, g.arm_h_tip
    r = g.pad_d / 2
    side = (cq.Workplane("XZ")
            .polyline([(-Lt, 0), (L + r + 1, 0), (L + r + 1, -ht), (L, -ht), (0, -hr), (-Lt, -hr)]).close()
            .extrude(g.tenon_b, both=True))
    plan = cq.Workplane("XY").workplane(offset=-hr - 1).polyline(tenon_plan(g)).close().extrude(hr + 2)
    pad = cq.Workplane("XY").workplane(offset=-ht).center(L, 0).circle(r).extrude(ht)
    arm = side.intersect(plan).union(pad)

    # wire channel: constant through the tenon, lofted (tapered) along the span, 45-deg self-supporting V
    x_end = g.wire_exit_x + 3.0
    p0 = arm_section_profile(g, hr)
    p1 = arm_section_profile(g, arm_height_at(g, x_end))
    prism = cq.Workplane("YZ").workplane(offset=-Lt - 1).polyline(p0).close().extrude(Lt + 1)
    loft = cq.Workplane("YZ").polyline(p0).close().workplane(offset=x_end).polyline(p1).close().loft(ruled=True)
    arm = arm.cut(prism).cut(loft)
    # wire exit slot through the top flange just inboard of the motor base
    slot_w = min(6.0, g.arm_b - 2 * g.arm_t_w - 1.0)
    exit_slot = (cq.Workplane("XY").workplane(offset=-g.arm_t_f - 1.0).center(g.wire_exit_x, 0)
                 .slot2D(10.0, slot_w, 0).extrude(g.arm_t_f + 2.0))
    arm = arm.cut(exit_slot)

    # motor holes (through + counterbore from below so M3x8 bolts clamp motor_clamp_t of material)
    cuts = [cyl(L, 0, -ht - 1, ht + 2, g.shaft_d)]
    for dx, dy in motor_hole_points(g):
        cuts.append(cyl(L + dx, dy, -ht - 1, ht + 2, g.motor_hole_d))
        cuts.append(cyl(L + dx, dy, -hr - 1, hr + 1 - g.motor_clamp_t, 6.2))
    # tenon clamp bolts
    for sy in (1, -1):
        cuts.append(cyl(g.tenon_bolt_x, sy * g.tenon_bolt_y, -hr - 1, hr + 2, g.m3_clear))
    for c in cuts:
        arm = arm.cut(c)
    return arm


def place_arm(arm: cq.Workplane, g: FrameGeometry, ang: float) -> cq.Workplane:
    return place(arm, ang, g.root_radius, g.z_arm_top)


# ----------------------------------------------------------------------------- body (PAHT-CF)
def _outline_solid(g: FrameGeometry, z0: float, h: float) -> cq.Workplane:
    hx, hy = g.body_hx, g.body_hy
    parts = [box(-hx, hx, -hy, hy, z0, z0 + h)]
    r0 = g.root_radius - g.tenon_L - g.socket_wall_t - g.socket_clearance
    for ang, _ in arm_axes(g):
        boss = box(r0 - g.root_radius, 0.0, -g.socket_half_w, g.socket_half_w, z0, z0 + h)
        parts.append(place(boss, ang, g.root_radius))
    return union_all(parts)


def _socket_pocket(g: FrameGeometry, z0: float, z1: float) -> cq.Workplane:
    """Arm-local pocket: tenon outline + clearance, open past the root face."""
    pts = tenon_plan(g, grow=g.socket_clearance, x_out=8.0)
    return cq.Workplane("XY").workplane(offset=z0).polyline(pts).close().extrude(z1 - z0)


RIB_H = 4.5        # battery-pad strap ribs (U-channels closed by the body floor)
TUNNEL_H = 2.5
PAD_T = 2.0


def strap_x(g: FrameGeometry):
    return (-0.33 * g.body_hx, 0.33 * g.body_hx)


def pad_bolt_pts(g: FrameGeometry):
    return ((0.70 * g.body_hx, 0.0), (-0.70 * g.body_hx, 0.0), (0.0, 0.70 * g.body_hy), (0.0, -0.70 * g.body_hy))


def make_bottom_body(g: FrameGeometry) -> cq.Workplane:
    hx, hy, H, tb, w = g.body_hx, g.body_hy, g.H_int, g.t_bp, g.wall_t
    floor = _outline_solid(g, 0.0, tb)
    shell = box(-hx, hx, -hy, hy, tb, tb + H).cut(box(-hx + w, hx - w, -hy + w, hy - w, tb, tb + H + 1))
    r0 = g.root_radius - g.tenon_L - g.socket_wall_t - g.socket_clearance
    blocks = [place(box(r0 - g.root_radius, 0.0, -g.socket_half_w, g.socket_half_w, tb, tb + H), ang, g.root_radius)
              for ang, _ in arm_axes(g)]
    body = union_all([floor, shell] + blocks)

    # inner bosses: FC stack (gummy mounts sit on these) and top-plate/canopy bolts
    p = g.stack_pattern / 2
    bosses = [cyl(sx * p, sy * p, tb, 5.0, g.boss_d) for sx in (1, -1) for sy in (1, -1)]
    bosses += [cyl(x, y, tb, H, g.boss_d) for x, y in g.top_bolt_pts]
    cam_y = g.camera_w / 2 + 2.8 + 5.0
    bosses += [cyl(hx - 4.0, sy * cam_y, tb, H, 8.0) for sy in (1, -1)]
    # battery-pad insert bosses (inserts pressed in from the underside)
    bosses += [cyl(x, y, tb, 5.0, g.boss_d) for x, y in pad_bolt_pts(g)]
    body = union_all([body] + bosses)

    cuts = [cyl(x, y, -1.0, 7.0, g.m3_insert) for x, y in pad_bolt_pts(g)]
    for ang, _ in arm_axes(g):
        cuts.append(place(_socket_pocket(g, tb + g.pedestal_h, tb + H + 1), ang, g.root_radius))
        # back-wall window for the motor pigtail / MR30 connector
        chw = g.arm_b - 2 * g.arm_t_w + 4
        win = box(-g.tenon_L - g.socket_wall_t - 2, -g.tenon_L + 0.5, -chw / 2, chw / 2,
                  tb + g.pedestal_h + 1.5, tb + H + 1)
        cuts.append(place(win, ang, g.root_radius))
        for sy in (1, -1):
            hole = cyl(g.tenon_bolt_x, sy * g.tenon_bolt_y, -1, tb + H + 2, g.m3_clear)
            nut = hexagon(g.tenon_bolt_x, sy * g.tenon_bolt_y, -1, 1 + g.m3_nut_depth, g.m3_nut_af)
            cuts += [place(hole, ang, g.root_radius), place(nut, ang, g.root_radius)]
    for sx in (1, -1):
        for sy in (1, -1):
            cuts.append(cyl(sx * p, sy * p, tb + 0.8, 6.0, g.m3_insert))
    for x, y in g.top_bolt_pts:
        cuts.append(cyl(x, y, tb + H - 7.0, 8.0, g.m3_insert))
    # camera cage inserts (horizontal, into the front pillars)
    for sy in (1, -1):
        cuts.append(cq.Workplane("YZ").workplane(offset=hx - 8.0).center(sy * cam_y, tb + H / 2)
                    .circle(g.m3_insert / 2).extrude(9.0))
    # cable windows: XT60 lead out the rear, camera/VTX cable at the front
    cuts.append(box(-hx - 1, -hx + w + 1, -8, 8, tb + 3, tb + 13))
    cuts.append(box(hx - w - 1, hx + 1, -6, 6, tb + H - 8, tb + H + 1))
    for c in cuts:
        body = body.cut(c)
    return body


def make_top_plate(g: FrameGeometry) -> cq.Workplane:
    z0 = g.z_arm_top
    plate = _outline_solid(g, z0, g.t_tp)
    cuts = []
    for ang, _ in arm_axes(g):
        for sy in (1, -1):
            cuts.append(place(cyl(g.tenon_bolt_x, sy * g.tenon_bolt_y, z0 - 1, g.t_tp + 2, g.m3_clear), ang, g.root_radius))
    for x, y in list(g.top_bolt_pts) + list(g.grommet_pts):
        cuts.append(cyl(x, y, z0 - 1, g.t_tp + 2, g.m3_clear))
    # FC <-> Jetson cable pass-through behind the stack
    cuts.append(cq.Workplane("XY").workplane(offset=z0 - 1).center(-g.stack_board / 2 - 9, 0)
                .slot2D(26.0, 10.0, 90).extrude(g.t_tp + 2))
    for c in cuts:
        plate = plate.cut(c)
    return plate


# ----------------------------------------------------------------------------- Jetson tray (PAHT-CF)
def tray_z(g: FrameGeometry) -> float:
    return g.z_arm_top + g.t_tp + g.grommet_h


TRAY_T = 3.0


def make_jetson_tray(g: FrameGeometry) -> cq.Workplane:
    z0 = tray_z(g)
    mx, my = g.jetson_mount_rect[0] / 2, g.jetson_mount_rect[1] / 2
    hx = min(mx + 7.0, g.body_hx - 1.5)
    hy = min(my + 7.0, g.body_hy - 1.5)
    tray = box(-hx, hx, -hy, hy, z0, z0 + TRAY_T)
    gx = max(abs(p[0]) for p in g.grommet_pts)
    gy = max(abs(p[1]) for p in g.grommet_pts)
    cx, cy = min(mx - 8.0, gx - 5.0), min(my - 8.0, gy - 5.0)
    cuts = [box(-cx, cx, -cy, cy, z0 - 1, z0 + TRAY_T + 1)]
    for sx in (1, -1):
        for sy in (1, -1):
            cuts.append(cq.Workplane("XY").workplane(offset=z0 - 1).center(sx * mx, sy * my)
                        .slot2D(2 * g.jetson_slot_travel + g.jetson_hole_d, g.jetson_hole_d, 0).extrude(TRAY_T + 2))
    for x, y in g.grommet_pts:
        cuts.append(cyl(x, y, z0 - 1, TRAY_T + 2, g.m3_clear))
    for c in cuts:
        tray = tray.cut(c)
    return tray


# ----------------------------------------------------------------------------- canopy (PETG-HF)
def canopy_height(g: FrameGeometry) -> float:
    return g.grommet_h + TRAY_T + g.jetson_env[2] + 3.0


def make_canopy(g: FrameGeometry) -> cq.Workplane:
    t = g.canopy_wall_t
    hx, hy = g.body_hx + t, g.body_hy + t
    z0 = g.z_arm_top + g.t_tp
    Hc = canopy_height(g)
    outer = box(-hx, hx, -hy, hy, z0, z0 + Hc).edges("|Z").fillet(8.0).faces(">Z").edges().fillet(6.0)
    can = outer.faces("<Z").shell(-t)
    # bolt tabs on the top-plate bolt circle
    tabs = []
    for x, y in g.top_bolt_pts:
        ax = 1 if abs(x) > abs(y) else 0
        if ax:
            tabs.append(box(min(x, math.copysign(hx - t + 0.5, x)), max(x, math.copysign(hx - t + 0.5, x)), -4.5, 4.5, z0, z0 + 3))
        else:
            tabs.append(box(-4.5, 4.5, min(y, math.copysign(hy - t + 0.5, y)), max(y, math.copysign(hy - t + 0.5, y)), z0, z0 + 3))
        tabs.append(cyl(x, y, z0, 3.0, 9.0))
    can = union_all([can] + tabs)
    cuts = [cyl(x, y, z0 - 1, 5.0, g.m3_clear) for x, y in g.top_bolt_pts]
    # fan inlet grille (hex hole pattern inside the fan circle)
    pitch = 5.5
    for i in range(-6, 7):
        for j in range(-6, 7):
            x = i * pitch + (pitch / 2 if j % 2 else 0.0)
            y = j * pitch * COS30
            if math.hypot(x, y) < g.fan_d / 2 - 2.5:
                cuts.append(cyl(x, y, z0 + Hc - 4, 6.0, 4.0))
    # side exhaust louvres
    for sy in (1, -1):
        for k in range(5):
            x = -30.0 + k * 15.0
            cuts.append(cq.Workplane("XZ").workplane(offset=-sy * (hy - t / 2))
                        .center(x, z0 + Hc * 0.55).slot2D(12.0, 4.0, 0).extrude((t + 2) / 2, both=True))
    # rear antenna holes and GPS mast holes
    for sy in (1, -1):
        cuts.append(cq.Workplane("YZ").workplane(offset=-hx - 1).center(sy * 20.0, z0 + Hc * 0.45).circle(3.25).extrude(t + 2))
        cuts.append(cyl(-g.body_hx + 14.0, sy * 8.0, z0 + Hc - 5, 8.0, g.m3_clear))
    for c in cuts:
        can = can.cut(c)
    return can


# ----------------------------------------------------------------------------- camera cage (PETG-HF)
def camera_center(g: FrameGeometry):
    return (g.body_hx + 3.0 + 12.0, 0.0, g.t_bp + g.H_int / 2 + 2.0)


def make_camera_mount(g: FrameGeometry) -> cq.Workplane:
    hx, H, tb = g.body_hx, g.H_int, g.t_bp
    cam_y = g.camera_w / 2 + 2.8 + 5.0
    gap = g.camera_w / 2 + 0.3
    back = box(hx, hx + 3.0, -cam_y - 5, cam_y + 5, tb, tb + H)
    sides = [box(hx + 3.0, hx + 27.0, gap, gap + 2.5, tb, tb + H), box(hx + 3.0, hx + 27.0, -gap - 2.5, -gap, tb, tb + H)]
    cage = union_all([back] + sides)
    cx, _, cz = camera_center(g)
    cuts = []
    for sy in (1, -1):
        cuts.append(cq.Workplane("YZ").workplane(offset=hx - 1).center(sy * cam_y, tb + H / 2).circle(g.m3_clear / 2).extrude(5))
    # M2 pivot and a 0-45 deg tilt-lock arc (row of overlapping holes) in each side plate
    pivot = cq.Workplane("XZ").workplane(offset=-(gap + 3.0)).center(cx, cz).circle(1.1).extrude(gap * 2 + 6.0)
    cuts.append(pivot)
    for k in range(0, 46, 3):
        a = math.radians(180 + k)
        px, pz = cx + 8.0 * math.cos(a), cz + 8.0 * math.sin(a)
        cuts.append(cq.Workplane("XZ").workplane(offset=-(gap + 3.0)).center(px, pz).circle(1.1).extrude(gap * 2 + 6.0))
    for c in cuts:
        cage = cage.cut(c)
    return cage


# ----------------------------------------------------------------------------- battery pad, GPS mast, legs (PETG-HF)
def battery_top_z() -> float:
    return -RIB_H - PAD_T


def make_battery_pad(g: FrameGeometry) -> cq.Workplane:
    """Plate + two strap U-channels (closed by the body floor) + 4 standoff bosses bolted into floor inserts."""
    L, W = g.battery_env[0], g.battery_env[1]
    zb = battery_top_z()
    hy = max(W / 2, g.body_hy * 0.75)
    pad = box(-L / 2, L / 2, -W / 2, W / 2, zb, zb + PAD_T)
    ribs = [box(sx - g.strap_w / 2 - 3, sx + g.strap_w / 2 + 3, -hy, hy, -RIB_H, 0.0) for sx in strap_x(g)]
    stand = [cyl(x, y, -RIB_H, RIB_H, 8.0) for x, y in pad_bolt_pts(g)]
    pad = union_all([pad] + ribs + stand)
    for sx in strap_x(g):
        pad = pad.cut(box(sx - g.strap_w / 2 - 0.5, sx + g.strap_w / 2 + 0.5, -hy - 1, hy + 1, -TUNNEL_H, 0.1))
    for x, y in pad_bolt_pts(g):
        pad = pad.cut(cyl(x, y, zb - 1, PAD_T + RIB_H + 2, g.m3_clear))
        pad = pad.cut(cyl(x, y, zb - 1, 1 + 2.0, 6.0))   # counterbore for the M3 head
    for k in range(-6, 7):   # grip grooves on the battery face
        x = k * 10.0
        if all(abs(x - sx) > g.strap_w / 2 + 4 for sx in strap_x(g)):
            pad = pad.cut(box(x - 1.0, x + 1.0, -W / 2 - 1, W / 2 + 1, zb - 0.1, zb + 0.8))
    return pad


def make_gps_mast(g: FrameGeometry) -> cq.Workplane:
    z0 = g.z_arm_top + g.t_tp + canopy_height(g)
    x0 = -g.body_hx + 14.0
    base = box(x0 - 7, x0 + 7, -13, 13, z0, z0 + 3)
    post = box(x0 - 4, x0 + 4, -4, 4, z0 + 3, z0 + 43).cut(box(x0 - 2.4, x0 + 2.4, -2.4, 2.4, z0 + 3, z0 + 44))
    top = box(x0 - 14, x0 + 14, -14, 14, z0 + 43, z0 + 45)
    mast = union_all([base, post, top])
    for sy in (1, -1):
        mast = mast.cut(cyl(x0, sy * 8.0, z0 - 1, 5, g.m3_clear))
    return mast


def leg_length(g: FrameGeometry) -> float:
    battery_bottom = battery_top_z() - g.battery_env[2]
    tip_bottom = g.z_arm_top - g.arm_h_tip
    return tip_bottom - 3.0 - (battery_bottom - 6.0)


def make_landing_leg(g: FrameGeometry) -> cq.Workplane:
    """Arm-local (same frame as make_arm): flange under the pad + tube, clamped by the two on-axis motor bolts."""
    L, ht = g.arm_L, g.arm_h_tip
    Ll = leg_length(g)
    flange = cyl(L, 0, -ht - 3.0, 3.0, g.pad_d - 4.0)
    tube = cyl(L, 0, -ht - 3.0 - Ll, Ll, 16.0)
    foot = cq.Workplane("XY").workplane(offset=-ht - 3.0 - Ll).center(L, 0).sphere(8.0)
    leg = union_all([flange, tube]).union(foot).cut(cyl(L, 0, -ht - 3.0 - Ll + 2.0, Ll + 2.5, 12.0))
    r16 = g.motor_patterns[0] / math.sqrt(2)
    for dx in (r16, -r16):
        leg = leg.cut(cyl(L + dx, 0, -ht - 4.0, 5.0, g.motor_hole_d))
    for dx, dy in motor_hole_points(g)[2:]:
        leg = leg.cut(cyl(L + dx, dy, -ht - 4.0, 5.0, 7.0))  # clearance for the other bolt heads
    return leg


# ----------------------------------------------------------------------------- bought-part envelopes
def envelopes(g: FrameGeometry) -> dict[str, cq.Workplane]:
    env = {}
    jz = tray_z(g) + TRAY_T
    j = g.jetson_env
    env["jetson"] = box(-j[0] / 2, j[0] / 2, -j[1] / 2, j[1] / 2, jz, jz + j[2])
    s = g.stack_board / 2
    env["fc_stack"] = box(-s, s, -s, s, g.t_bp + 5.0, g.t_bp + 5.0 + g.stack_h)
    bL, bW, bH = g.battery_env
    zb = battery_top_z()
    env["battery"] = box(-bL / 2, bL / 2, -bW / 2, bW / 2, zb - bH, zb)
    cx, cy, cz = camera_center(g)
    env["camera"] = box(cx - 10, cx + 10, cy - g.camera_w / 2, cy + g.camera_w / 2, cz - 10, cz + 10)
    motors, props = [], []
    for ang, (ux, uy) in arm_axes(g):
        R = g.motor_radius
        motors.append(cyl(R * ux, R * uy, g.z_arm_top, g.motor_h, g.motor_base_d))
        props.append(cyl(R * ux, R * uy, g.z_arm_top + g.motor_h, 3.0, g.prop_d))
    env["motors"] = union_all(motors)
    env["props"] = union_all(props)
    return env
