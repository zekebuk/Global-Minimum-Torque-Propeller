"""Rigid multirotor: rotor layout, the inflow each hub sees, and the body wrench the rotors make.

Frames: body FRD (x forward, y right, z down), PX4's convention. Every rotor thrusts along
z_r = -z_b (a flat multirotor); rotor i sits at p_i = (x_i, y_i, 0) and spins with s_i = +1
counter-clockwise seen from above (PX4 ``CA_ROTORi_KM`` > 0).

Per rotor, with v_rel the hub's velocity relative to the air (body frame),

    v_rel = v_air,b + omega_b x p_i,   V_axial = v_rel . z_r (climb > 0),
    V_edge = |v_rel - V_axial z_r|,    e_f = in-plane part / V_edge (the direction the hub moves through the air).

Loads on the body (derivation in docs/sitl.md):

    force   = T z_r - H e_f
    moment  = p_i x force - s_i M_roll e_f + s_i Q z_b

The advancing side lies at s_i (e_f x z_r), so the rigid-hub roll moment (advancing side up) is
-s_i M_roll e_f; the reaction to the aerodynamic torque yaws the body by +s_i Q about z_b.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

Z_ROTOR = np.array([0.0, 0.0, -1.0])
G = 9.80665


@dataclass(frozen=True)
class Vehicle:
    mass_kg: float
    inertia_kg_m2: np.ndarray  # diagonal (Ixx, Iyy, Izz)
    positions_m: np.ndarray  # (n, 2) x forward, y right
    spins: np.ndarray  # (n,) +1 CCW seen from above
    body_cda_m2: float
    rho_kg_m3: float

    @property
    def rotor_count(self) -> int:
        return len(self.spins)


def layout(name: str, arm_length_m: float) -> tuple[np.ndarray, np.ndarray]:
    """PX4 rotor order. quad_x: 0 front-right CCW, 1 rear-left CCW, 2 front-left CW, 3 rear-right CW."""

    if name == "quad_x":
        a = arm_length_m / math.sqrt(2.0)
        return np.array([[a, a], [-a, -a], [a, -a], [-a, a]]), np.array([1, 1, -1, -1])
    count = {"hex_x": 6, "octo_x": 8}.get(name)
    if count is None:
        raise ValueError(f"sitl.layout must be quad_x, hex_x or octo_x (or set rotor_positions_m), not {name!r}")
    # Evenly spaced from 30 deg (hex) / 22.5 deg (octo) clockwise from the nose, alternating spin.
    # Match your CA_ROTORi_* order with sitl.rotor_positions_m / spin_directions if it differs.
    angles = np.radians(180.0 / count + 360.0 / count * np.arange(count))
    positions = arm_length_m * np.column_stack([np.cos(angles), np.sin(angles)])
    return positions, np.where(np.arange(count) % 2 == 0, 1, -1)


def from_specs(multirotor, sitl, rho_kg_m3: float) -> Vehicle:
    if sitl.rotor_positions_m:
        positions, spins = np.array(sitl.rotor_positions_m, float), np.array(sitl.spin_directions, int)
        if len(spins) != len(positions):
            raise ValueError("sitl.spin_directions must give one entry per sitl.rotor_positions_m")
    else:
        positions, spins = layout(sitl.layout, multirotor.arm_length_m)
    if len(spins) != multirotor.rotor_count:
        raise ValueError(f"layout has {len(spins)} rotors, multirotor.rotor_count is {multirotor.rotor_count}")
    iyy = sitl.iyy_kg_m2 if sitl.iyy_kg_m2 is not None else multirotor.ixx_kg_m2
    return Vehicle(
        mass_kg=multirotor.mass_kg,
        inertia_kg_m2=np.array([multirotor.ixx_kg_m2, iyy, multirotor.izz_kg_m2]),
        positions_m=positions,
        spins=spins,
        body_cda_m2=multirotor.body_cda_m2,
        rho_kg_m3=rho_kg_m3,
    )


def inflow(vehicle: Vehicle, v_air_body: np.ndarray, body_rate: np.ndarray) -> dict[str, np.ndarray]:
    """Per-sample, per-rotor V_axial, V_edge (N, n) and e_f (N, n, 3) from body air velocity and rates (N, 3)."""

    p = np.column_stack([vehicle.positions_m, np.zeros(vehicle.rotor_count)])  # (n, 3)
    v_rel = v_air_body[:, None, :] + np.cross(body_rate[:, None, :], p[None, :, :])
    v_axial = v_rel @ Z_ROTOR
    plane = v_rel - v_axial[..., None] * Z_ROTOR
    v_edge = np.linalg.norm(plane, axis=-1)
    e_f = np.where(v_edge[..., None] > 1.0e-6, plane / np.maximum(v_edge, 1.0e-6)[..., None], np.array([1.0, 0.0, 0.0]))
    return {"v_axial": v_axial, "v_edge": v_edge, "e_f": e_f}


def rotor_wrench4(vehicle: Vehicle, loads: dict[str, np.ndarray], e_f: np.ndarray) -> np.ndarray:
    """(N, n, 4) per-rotor contributions: thrust along -z_b, then roll, pitch, yaw moment about the CG."""

    p = np.column_stack([vehicle.positions_m, np.zeros(vehicle.rotor_count)])
    s = vehicle.spins[None, :, None]
    force = loads["thrust_N"][..., None] * Z_ROTOR - loads["h_force_N"][..., None] * e_f
    moment = np.cross(p[None, :, :], force) - s * loads["roll_moment_Nm"][..., None] * e_f
    moment[..., 2] += vehicle.spins[None, :] * loads["torque_Nm"]
    return np.concatenate([-force[..., 2:3], moment], axis=-1)


def wrench4(vehicle: Vehicle, loads: dict[str, np.ndarray], e_f: np.ndarray) -> np.ndarray:
    """(N, 4): total thrust along -z_b, then roll, pitch, yaw moments."""

    return rotor_wrench4(vehicle, loads, e_f).sum(axis=1)


def rotation_from_quaternion(q: np.ndarray) -> np.ndarray:
    """(N, 3, 3) body-to-NED rotation from PX4 quaternions [w, x, y, z] (N, 4)."""

    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    w, x, y, z = q.T
    return np.stack(
        [
            np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], axis=-1),
            np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], axis=-1),
            np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], axis=-1),
        ],
        axis=1,
    )
