"""Plan-view layout optimisation: where to put the motors.

First principles: arm stiffness scales ~ 1/L^3 and arm mass ~ L, so the best arm is the shortest one
that satisfies the geometric constraints. With a symmetric (true/stretched) X, motor i sits at
(+/-R cos(theta), +/-R sin(theta)). For each arm angle theta the minimum motor radius R follows from

  1. front-pair prop tips:  2 R sin(theta) - D >= c_pp
  2. side-pair prop tips:   2 R cos(theta) - D >= c_pp
  3. prop disc vs canopy:   dist(motor, canopy rectangle) - D/2 >= c_canopy  (canopy is taller than the prop plane)
  4. tuning symmetry:       |tan(theta) - 1| <= max_aspect_deviation

The arm root face sits where the socket boss (arm tenon + socket walls) leaves the body rectangle, so
free arm length L(theta) = R_min(theta) - r_root(theta). L is minimised by a dense 1-D scan in theta
(constraints 1-3 are solved exactly for R), which is a global optimum, not a local one.
"""

from __future__ import annotations

import math

import numpy as np


def canopy_rect(cfg: dict) -> tuple[float, float, float, float]:
    j = cfg["electronics"]["jetson"]["envelope"]
    m = cfg["body"]["jetson_margin"] + cfg["body"]["canopy_wall_t"]
    hx, hy = j[0] / 2 + m, j[1] / 2 + m
    return (-hx, hx + cfg["layout"]["camera_keepout_front"], -hy, hy)


def body_half_dims(cfg: dict) -> tuple[float, float]:
    """Body tub / top plate rectangle (carries the Jetson tray and the FC stack)."""
    j = cfg["electronics"]["jetson"]["envelope"]
    m = cfg["body"]["jetson_margin"]
    return j[0] / 2 + m, j[1] / 2 + m


def dist_point_rect(px: float, py: float, rect) -> float:
    x0, x1, y0, y1 = rect
    dx = max(x0 - px, 0.0, px - x1)
    dy = max(y0 - py, 0.0, py - y1)
    return math.hypot(dx, dy)


def _exit_radius(theta: float, offset: float, hx: float, hy: float) -> float:
    ux, uy = math.cos(theta), math.sin(theta)
    nx, ny = -math.sin(theta), math.cos(theta)
    ox, oy = offset * nx, offset * ny
    rx = (hx - ox) / ux if ux > 1e-9 else math.inf
    ry = (hy - oy) / uy if uy > 1e-9 else math.inf
    return min(rx, ry)


def root_radius(theta: float, socket_half_width: float, hx: float, hy: float, margin: float = 1.0) -> float:
    """Radius along the arm axis beyond which the whole socket-boss width is outside the body rectangle.

    The exit radius is piecewise linear in the lateral offset, so its maximum over [-w, w] is at an end
    point or at a rectangle corner whose lateral offset lies inside [-w, w].
    """
    cands = [_exit_radius(theta, +socket_half_width, hx, hy), _exit_radius(theta, -socket_half_width, hx, hy)]
    ux, uy = math.cos(theta), math.sin(theta)
    for cx, cy in ((hx, hy), (hx, -hy), (-hx, hy), (-hx, -hy)):
        along = cx * ux + cy * uy
        offset = -cx * uy + cy * ux
        if along > 0 and abs(offset) <= socket_half_width:
            cands.append(along)
    return max(cands) + margin


def _r_min_canopy(theta: float, D: float, c: float, rect) -> float:
    need = D / 2 + c
    lo, hi = 0.0, 2000.0
    ok = lambda R: all(
        dist_point_rect(sx * R * math.cos(theta), R * math.sin(theta), rect) >= need for sx in (+1, -1)
    )
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return hi


def solve_layout(cfg: dict, socket_half_width: float, step_deg: float = 0.05) -> dict:
    lay = cfg["layout"]
    D = cfg["propulsion"]["prop_diameter"]
    c_pp = lay["prop_tip_clearance_min"]
    c_can = lay["prop_to_canopy_clearance_min"]
    rect = canopy_rect(cfg)
    hx, hy = body_half_dims(cfg)
    lo, hi = lay["arm_angle_bounds_deg"]
    best = None
    scan = []
    for th_deg in np.arange(lo, hi + 1e-9, step_deg):
        th = math.radians(th_deg)
        if abs(math.tan(th) - 1.0) > lay["max_aspect_deviation"]:
            continue
        R = max((D + c_pp) / (2 * math.sin(th)), (D + c_pp) / (2 * math.cos(th)), _r_min_canopy(th, D, c_can, rect))
        r0 = root_radius(th, socket_half_width, hx, hy)
        L = R - r0
        J = L + 0.01 * (th_deg - 45.0) ** 2   # tiny tie-break toward a square X
        scan.append((th_deg, R, r0, L))
        if best is None or J < best[0]:
            best = (J, th_deg, R, r0, L)
    _, th_deg, R, r0, L = best
    th = math.radians(th_deg)
    motors = [(sx * R * math.cos(th), sy * R * math.sin(th)) for sx, sy in ((1, 1), (1, -1), (-1, -1), (-1, 1))]
    front_gap = 2 * R * math.sin(th) - D
    side_gap = 2 * R * math.cos(th) - D
    canopy_gap = min(dist_point_rect(x, y, rect) for x, y in motors) - D / 2
    binding = []
    tol = 0.05
    if abs(front_gap - c_pp) < tol:
        binding.append("front prop-prop clearance")
    if abs(side_gap - c_pp) < tol:
        binding.append("side prop-prop clearance")
    if abs(canopy_gap - c_can) < tol:
        binding.append("prop-canopy clearance")
    return {
        "arm_angle_deg": th_deg,
        "motor_radius": R,
        "wheelbase_diag": 2 * R,
        "motor_positions": motors,
        "root_radius": r0,
        "arm_free_length": L,
        "front_prop_gap": front_gap,
        "side_prop_gap": side_gap,
        "canopy_prop_gap": canopy_gap,
        "binding_constraints": binding,
        "canopy_rect": rect,
        "body_half_dims": (hx, hy),
        "scan": scan,
    }
