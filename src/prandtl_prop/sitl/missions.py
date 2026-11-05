"""Reference missions: quasi-steady rigid-body traces that stand in for SITL logs until real ones exist.

Each mission is a list of segments, each setting a target horizontal speed, course rate, climb
rate, and yaw behaviour. The velocity follows the target through a critically damped third-order
filter (triple pole at ``wn``: how aggressively the pilot or autopilot moves), so acceleration and
jerk are continuous and the attitude, which follows the acceleration, has finite angular
acceleration. The yaw rate follows its command through a first-order lag ``yaw_tau_s``. Wind is a mean vector plus a turbulent part: first-order filtered noise
(a one-pole stand-in for a Dryden spectrum) with intensity ``gust_m_s`` and length scale 30 m.

From the trajectory (first principles, no controller):

    thrust vector  F = m (a - g) - D,   D = -1/2 rho CdA |v_air| v_air
                   then low-passed (critically damped, ``attitude_bandwidth_rad_s``): the attitude loop
                   cannot follow turbulence instantly, and an unfiltered gust would give unbounded
                   angular acceleration (the residual force error is reported)
    attitude       z_b = -F / |F|, x_b in the plane of z_b and the heading
    body rates     from the attitude derivative; moments = I d(omega)/dt + omega x I omega

The result is a ``Trace`` (wrench and inflow), the same thing a log becomes, so the replay and the
optimizer cannot tell them apart. They are not a substitute for flying the exported tables in SITL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from prandtl_prop.sitl.replay import Trace
from prandtl_prop.sitl.vehicle import G, Vehicle


@dataclass(frozen=True)
class Segment:
    duration_s: float
    speed_m_s: float = 0.0  # horizontal ground speed target
    climb_m_s: float = 0.0  # up positive
    course_rate_rad_s: float = 0.0
    yaw_rate_rad_s: float | None = None  # None: nose follows the course
    wn_rad_s: float = 1.5
    yaw_tau_s: float = 0.5


@dataclass(frozen=True)
class Mission:
    name: str
    segments: tuple[Segment, ...]
    wind_ned_m_s: tuple[float, float, float] = (0.0, 0.0, 0.0)
    gust_m_s: float = 0.0
    seed: int = 0


def _survey() -> Mission:
    legs = []
    for _ in range(4):
        legs += [Segment(25.0, 12.0), Segment(math.pi / 0.6, 6.0, course_rate_rad_s=0.6)]  # 180 deg turn at 6 m/s
    return Mission(
        "survey",
        (
            Segment(4.0),
            Segment(14.0, climb_m_s=3.0),
            Segment(4.0),
            *legs,
            Segment(30.0),  # loiter
            Segment(16.0, climb_m_s=-2.5),
            Segment(4.0, climb_m_s=-0.5),
        ),
        wind_ned_m_s=(0.0, 5.0, 0.0),
        gust_m_s=1.5,
        seed=1,
    )


def _freestyle() -> Mission:
    fast = 3.0
    lap = (
        Segment(3.0, climb_m_s=15.0, wn_rad_s=fast),  # punch-out
        Segment(2.0),  # gentler stops downward: the vertical deceleration must stay below 1 g
        Segment(3.0, climb_m_s=-10.0),  # dive through the wake
        Segment(2.0, wn_rad_s=fast),
        Segment(8.0, 25.0, wn_rad_s=fast),  # straight at speed
        Segment(5.0, 18.0, course_rate_rad_s=1.4, wn_rad_s=fast),  # hard banked turn (~2.6 g lateral)
        Segment(3.0, 5.0, wn_rad_s=fast),
        Segment(2.5, yaw_rate_rad_s=6.0, wn_rad_s=fast, yaw_tau_s=0.8),  # yaw spin, near the rotors' yaw authority
        Segment(3.0, wn_rad_s=fast),
    )
    return Mission("freestyle", (Segment(3.0), *lap, *lap, Segment(4.0)), wind_ned_m_s=(2.0, 0.0, 0.0), gust_m_s=2.0, seed=2)


MISSIONS = {
    "hover": lambda: Mission("hover", (Segment(120.0, yaw_rate_rad_s=0.1),), wind_ned_m_s=(3.0, 0.0, 0.0), gust_m_s=1.5, seed=0),
    "survey": _survey,
    "freestyle": _freestyle,
}


def mission(name: str) -> Mission:
    if name not in MISSIONS:
        raise ValueError(f"Unknown reference mission {name!r}; choose from {sorted(MISSIONS)}")
    return MISSIONS[name]()


def fly(m: Mission, vehicle: Vehicle, sample_rate_hz: float = 50.0, attitude_bandwidth_rad_s: float = 25.0) -> Trace:
    dt = 1.0 / sample_rate_hz
    sub = 10
    h = dt / sub
    rng = np.random.default_rng(m.seed)
    v = np.zeros(3)
    a = np.zeros(3)
    j = np.zeros(3)
    course = yaw = yaw_rate = 0.0
    gust = np.zeros(3)
    rows_v, rows_a, rows_yaw, rows_w = [], [], [], []
    for seg in m.segments:
        steps = int(round(seg.duration_s * sample_rate_hz))
        for _ in range(steps):
            for _ in range(sub):
                course += seg.course_rate_rad_s * h
                target = np.array([seg.speed_m_s * math.cos(course), seg.speed_m_s * math.sin(course), -seg.climb_m_s])
                wn = seg.wn_rad_s
                snap = wn**3 * (target - v) - 3.0 * wn**2 * a - 3.0 * wn * j
                j = j + snap * h
                a = a + j * h
                v = v + a * h
                if seg.yaw_rate_rad_s is None:
                    command = seg.course_rate_rad_s + math.atan2(math.sin(course - yaw), math.cos(course - yaw)) / 0.5
                else:
                    command = seg.yaw_rate_rad_s
                yaw_rate += (command - yaw_rate) * h / seg.yaw_tau_s
                yaw += yaw_rate * h
            # Turbulence: dg/dt = -(U / L) g + sigma sqrt(2 U / L) w (U: airspeed, floor 2 m/s).
            rate = max(float(np.linalg.norm(v - np.array(m.wind_ned_m_s))), 2.0) / 30.0
            gust = gust - rate * gust * dt + m.gust_m_s * math.sqrt(2.0 * rate * dt) * rng.standard_normal(3) * np.array([1.0, 1.0, 0.5])
            rows_v.append(v.copy())
            rows_a.append(a.copy())
            rows_yaw.append(yaw)
            rows_w.append(np.array(m.wind_ned_m_s) + gust)
    vel, acc, yaw_s, wind = np.array(rows_v), np.array(rows_a), np.array(rows_yaw), np.array(rows_w)
    t = np.arange(len(vel)) * dt
    v_air = vel - wind
    drag = -0.5 * vehicle.rho_kg_m3 * vehicle.body_cda_m2 * np.linalg.norm(v_air, axis=1, keepdims=True) * v_air
    demand = vehicle.mass_kg * (acc - np.array([0.0, 0.0, G])) - drag
    force = np.empty_like(demand)
    state, slope, wc = demand[0].copy(), np.zeros(3), attitude_bandwidth_rad_s
    for k in range(len(demand)):
        for _ in range(sub):
            slope = slope + (wc * wc * (demand[k] - state) - 2.0 * wc * slope) * h
            state = state + slope * h
        force[k] = state
    thrust = np.linalg.norm(force, axis=1)
    if np.any(force[:, 2] > -0.1 * vehicle.mass_kg * G):
        raise ValueError(
            f"mission {m.name!r} asks for less than 0.1 g of upward thrust (inverted flight is not modelled); soften its segments"
        )
    z_b = -force / thrust[:, None]
    heading = np.column_stack([np.cos(yaw_s), np.sin(yaw_s), np.zeros_like(yaw_s)])
    y_b = np.cross(z_b, heading)
    y_b /= np.linalg.norm(y_b, axis=1, keepdims=True)
    x_b = np.cross(y_b, z_b)
    rot = np.stack([x_b, y_b, z_b], axis=2)  # columns: body axes in NED (body -> NED)
    rdot = np.gradient(rot, t, axis=0)
    skew = np.einsum("kji,kjl->kil", rot, rdot)  # R^T dR/dt = [omega]x
    rate = np.column_stack([skew[:, 2, 1] - skew[:, 1, 2], skew[:, 0, 2] - skew[:, 2, 0], skew[:, 1, 0] - skew[:, 0, 1]]) / 2.0
    inertia = vehicle.inertia_kg_m2
    moment = inertia * np.gradient(rate, t, axis=0) + np.cross(rate, inertia * rate)
    v_air_body = np.einsum("kji,kj->ki", rot, v_air)
    return Trace(m.name, t, v_air_body, rate, np.column_stack([thrust, moment]), source="reference mission (quasi-steady rigid body)")
