"""Derived frame dimensions shared by the CadQuery reference CAD and the Onshape export.

Global frame: X forward, Y left, Z up, origin at the body centre on the underside of the bottom plate.
Arm local frame: X from the root face (x=0) outward to the motor centre (x=L), top face at z=0.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .arm_model import body_interior_height, dovetail, socket_half_width, tenon_width, tie_bolt_y


@dataclass
class FrameGeometry:
    # layout
    arm_angle_deg: float
    motor_radius: float
    root_radius: float
    arm_L: float
    # arm
    arm_b: float
    arm_h_root: float
    arm_h_tip: float
    arm_t_w: float
    arm_t_f: float
    arm_n_walls: int
    arm_n_layers: int
    tenon_L: float
    tenon_b: float
    tenon_chamfer: float
    pad_d: float
    motor_clamp_t: float
    motor_patterns: tuple
    motor_hole_d: float
    shaft_d: float
    motor_base_d: float
    wire_exit_x: float
    tenon_bolt_x: float          # retention insert position (arm-local)
    tenon_bolt_y: float
    # tapered sliding dovetail
    dv_depth: float
    dv_flare: float
    dv_beta_deg: float
    dv_taper: float
    dv_home_gap: float
    dv_side_clear: float
    dv_top_relief: float
    dv_insert_depth: float
    tie_bolt_x: float
    tie_bolt_y: float
    washer_od: float
    # body
    body_hx: float
    body_hy: float
    H_int: float
    t_bp: float
    t_tp: float
    wall_t: float
    socket_half_w: float
    socket_clearance: float
    socket_wall_t: float
    pedestal_h: float
    z_arm_top: float
    # electronics / misc
    stack_pattern: float
    stack_board: float
    stack_h: float
    jetson_env: tuple
    jetson_mount_rect: tuple
    jetson_hole_d: float
    jetson_slot_travel: float
    fan_d: float
    grommet_h: float
    grommet_pts: tuple
    top_bolt_pts: tuple
    canopy_wall_t: float
    canopy_rect: tuple
    battery_env: tuple
    strap_w: float
    camera_w: float
    prop_d: float
    motor_h: float
    m3_clear: float
    m3_insert: float
    m3_nut_af: float
    m3_nut_depth: float
    boss_d: float

    def to_dict(self) -> dict:
        return asdict(self)


def derive(cfg: dict, analysis: dict) -> FrameGeometry:
    lay = analysis["layout"]
    d = analysis["arm"]["result"]["design"]
    a, b, e, p = cfg["arm"], cfg["body"], cfg["electronics"], cfg["propulsion"]
    hx, hy = lay["body_half_dims"]
    H_int = body_interior_height(d["h_root"], cfg)
    dv = dovetail(cfg)
    bt = tenon_width(d, cfg)
    L = lay["arm_free_length"]
    tenon_L = a["tenon_length"]
    # Jetson tray soft mounts on a diamond (on the X/Y axes, clear of the diagonal arm sockets)
    gx, gy = 0.30 * 2 * hx, 0.30 * 2 * hy
    grommets = ((gx, 0.0), (-gx, 0.0), (0.0, gy), (0.0, -gy))
    top_bolts = ((hx - 4.5, 0.0), (-(hx - 4.5), 0.0), (0.0, hy - 4.5), (0.0, -(hy - 4.5)))
    return FrameGeometry(
        arm_angle_deg=float(lay["arm_angle_deg"]), motor_radius=float(lay["motor_radius"]),
        root_radius=float(lay["root_radius"]), arm_L=float(L),
        arm_b=d["b"], arm_h_root=d["h_root"], arm_h_tip=d["h_tip"], arm_t_w=d["t_w"], arm_t_f=d["t_f"],
        arm_n_walls=d["n_walls"], arm_n_layers=d["n_layers"],
        tenon_L=tenon_L, tenon_b=bt, tenon_chamfer=4.0, pad_d=a["pad_diameter"], motor_clamp_t=a["motor_clamp_t"],
        motor_patterns=tuple(p["motor_patterns"]), motor_hole_d=p["motor_bolt_clearance_d"],
        shaft_d=p["motor_shaft_clearance_d"], motor_base_d=p["motor_base_diameter"],
        wire_exit_x=L - a["pad_diameter"] / 2 - 5.0,
        tenon_bolt_x=-tenon_L / 2, tenon_bolt_y=d["b"] / 2 + a["tenon_ear_width"] / 2,
        dv_depth=dv["depth"], dv_flare=dv["flare"], dv_beta_deg=dv["flank_angle_deg"], dv_taper=dv["taper_per_side"],
        dv_home_gap=dv["home_gap"], dv_side_clear=dv["side_clearance"], dv_top_relief=dv["top_relief"],
        dv_insert_depth=dv["insert_depth"], tie_bolt_x=-tenon_L / 4, tie_bolt_y=tie_bolt_y(d["b"], cfg),
        washer_od=dv["washer_od"],
        body_hx=hx, body_hy=hy, H_int=H_int, t_bp=b["bottom_plate_t"], t_tp=b["top_plate_t"], wall_t=b["wall_t"],
        socket_half_w=socket_half_width(d["b"], cfg), socket_clearance=b["socket_clearance"],
        socket_wall_t=b["socket_wall_t"], pedestal_h=H_int - d["h_root"],
        z_arm_top=b["bottom_plate_t"] + H_int,
        stack_pattern=e["fc_stack"]["pattern"], stack_board=e["fc_stack"]["board_size"], stack_h=e["fc_stack"]["height"],
        jetson_env=tuple(e["jetson"]["envelope"]), jetson_mount_rect=tuple(e["jetson"]["mount_rect"]),
        jetson_hole_d=e["jetson"]["mount_hole_d"], jetson_slot_travel=e["jetson"]["tray_slot_travel"],
        fan_d=e["jetson"]["fan_diameter"], grommet_h=b["grommet_height"], grommet_pts=grommets,
        top_bolt_pts=top_bolts, canopy_wall_t=b["canopy_wall_t"], canopy_rect=tuple(lay["canopy_rect"]),
        battery_env=tuple(e["battery"]["envelope"]), strap_w=e["battery"]["strap_width"],
        camera_w=e["camera_width"], prop_d=p["prop_diameter"], motor_h=p["motor_height"],
        m3_clear=b["m3_clearance_d"], m3_insert=b["m3_insert_hole_d"], m3_nut_af=b["m3_nut_af"],
        m3_nut_depth=b["m3_nut_pocket_depth"], boss_d=b["insert_boss_d"],
    )


def arm_section_profile(g: FrameGeometry, h: float) -> list[tuple[float, float]]:
    """Channel pentagon (y, z) for outer height h - same definition as sections.ArmSection.channel()."""
    w = g.arm_b - 2 * g.arm_t_w
    z_top = -g.arm_t_f
    z_apex = -h + g.arm_t_f
    z_v = z_apex + w / 2
    return [(0.0, z_apex), (w / 2, z_v), (w / 2, z_top), (-w / 2, z_top), (-w / 2, z_v)]


def arm_height_at(g: FrameGeometry, x: float) -> float:
    s = min(max(x / g.arm_L, 0.0), 1.0)
    return g.arm_h_root + (g.arm_h_tip - g.arm_h_root) * s


def arm_axes(g: FrameGeometry):
    """(angle_deg, unit vector) for the 4 arms: FR, FL, RL, RR order consistent with layout motors."""
    th = g.arm_angle_deg
    angles = [th, -th, 180 + th, 180 - th]
    return [(ang, (math.cos(math.radians(ang)), math.sin(math.radians(ang)))) for ang in angles]


def tenon_section(g: FrameGeometry, x: float, side_grow: float = 0.0, top: float = 0.0) -> list[tuple[float, float]]:
    """Dovetail tenon / socket cross-section (y, z) at arm-local x (CCW).

    Upper sides are straight at half width hw(x) = tenon_b/2 + taper*x (narrower inward, x < 0);
    the bottom `dv_depth` flares out at the flank angle to hw + flare. `side_grow` opens clearance on
    the straight sides only (flanks and floor stay line-on-line = the seat); `top` raises the top edge.
    """
    hw = g.tenon_b / 2 + g.dv_taper * x
    hf = hw + g.dv_flare
    z0 = -g.arm_h_root
    tanb = math.tan(math.radians(g.dv_beta_deg))
    z_knee = z0 + (g.dv_flare - side_grow) / tanb
    ys = hw + side_grow
    return [(-hf, z0), (hf, z0), (ys, z_knee), (ys, top), (-ys, top), (-ys, z_knee)]


def socket_r0(g: FrameGeometry) -> float:
    """Radius of the socket block's inner (back) face."""
    return g.root_radius - g.tenon_L - g.dv_home_gap - g.socket_wall_t
