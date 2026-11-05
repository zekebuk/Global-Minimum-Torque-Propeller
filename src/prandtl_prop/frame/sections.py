"""Exact polygon section properties and the printed arm cross-section.

Section coordinates: y lateral, z vertical (flight up). Arm top face at z = 0, solid below.

Arm section = rectangle b x h minus a wire channel whose flight-bottom is a 45 deg "V".
The arm is printed flight-top-face-down, so in print orientation the V is a pointed roof and
the channel prints with no support and no bridging.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def _signed_area(p: np.ndarray) -> float:
    y, z = p[:, 0], p[:, 1]
    return 0.5 * float(np.sum(y * np.roll(z, -1) - np.roll(y, -1) * z))


def poly_props(pts) -> dict:
    """Area, centroid and second moments (about the origin) of a simple polygon."""
    p = np.asarray(pts, dtype=float)
    y, z = p[:, 0], p[:, 1]
    y1 = np.concatenate((y[1:], y[:1]))
    z1 = np.concatenate((z[1:], z[:1]))
    c = y * z1 - y1 * z
    A = 0.5 * c.sum()
    s = 1.0 if A >= 0 else -1.0
    A = abs(A)
    if A == 0:
        return {"A": 0.0, "Sy": 0.0, "Sz": 0.0, "Iyy0": 0.0, "Izz0": 0.0}
    Sy = s * ((z + z1) * c).sum() / 6.0   # first moment about y axis (int z dA)
    Sz = s * ((y + y1) * c).sum() / 6.0   # int y dA
    Iyy0 = s * ((z * z + z * z1 + z1 * z1) * c).sum() / 12.0  # int z^2 dA
    Izz0 = s * ((y * y + y * y1 + y1 * y1) * c).sum() / 12.0  # int y^2 dA
    return {"A": A, "Sy": Sy, "Sz": Sz, "Iyy0": Iyy0, "Izz0": Izz0}


def clip_above(pts, z_cut: float) -> np.ndarray:
    """Sutherland-Hodgman clip of a polygon to the half plane z >= z_cut."""
    p = np.asarray(pts, dtype=float)
    out = []
    n = len(p)
    for i in range(n):
        a, b = p[i], p[(i + 1) % n]
        ina, inb = a[1] >= z_cut, b[1] >= z_cut
        if ina:
            out.append(a)
        if ina != inb:
            t = (z_cut - a[1]) / (b[1] - a[1])
            out.append(a + t * (b - a))
    return np.array(out) if out else np.zeros((0, 2))


@dataclass(frozen=True)
class ArmSection:
    b: float        # outer width
    h: float        # outer height
    t_w: float      # web (side wall) thickness
    t_f: float      # flange (top/bottom skin) thickness

    @property
    def channel_w(self) -> float:
        return self.b - 2 * self.t_w

    @property
    def channel_h(self) -> float:
        return self.h - 2 * self.t_f

    @property
    def channel_valid(self) -> bool:
        return self.channel_w > 0 and self.channel_h >= self.channel_w / 2 + 1.0

    def outer(self) -> np.ndarray:
        b, h = self.b, self.h
        return np.array([[-b / 2, -h], [b / 2, -h], [b / 2, 0.0], [-b / 2, 0.0]])

    def channel(self) -> np.ndarray:
        """Pentagon, CCW: apex at the bottom (flight), flat top under the top flange."""
        w = self.channel_w
        z_top = -self.t_f
        z_apex = -self.h + self.t_f
        z_v = z_apex + w / 2
        return np.array([[0.0, z_apex], [w / 2, z_v], [w / 2, z_top], [-w / 2, z_top], [-w / 2, z_v]])

    def props(self) -> dict:
        o = poly_props(self.outer())
        if self.channel_valid:
            c = poly_props(self.channel())
        else:
            c = {"A": 0.0, "Sy": 0.0, "Sz": 0.0, "Iyy0": 0.0, "Izz0": 0.0}
        A = o["A"] - c["A"]
        zc = (o["Sy"] - c["Sy"]) / A
        Iyy = (o["Iyy0"] - c["Iyy0"]) - A * zc**2   # vertical bending (about lateral axis)
        Izz = o["Izz0"] - c["Izz0"]                  # lateral bending (section symmetric in y)
        # First moment of area above the neutral axis (for transverse / interlaminar shear)
        Q = 0.0
        for poly, sgn in ((self.outer(), 1.0), (self.channel() if self.channel_valid else None, -1.0)):
            if poly is None:
                continue
            cp = clip_above(poly, zc)
            if len(cp) >= 3:
                pp = poly_props(cp)
                Q += sgn * (pp["Sy"] - pp["A"] * zc)
        # Bredt-Batho closed-cell torsion constant (midline approximation)
        b_m = self.b - self.t_w
        h_m = self.h - self.t_f
        if self.channel_valid:
            t_bot_eff = self.t_f + self.channel_w / 8.0   # the V fill thickens the bottom flange
            A_m = b_m * h_m
            line = b_m / self.t_f + b_m / t_bot_eff + 2 * h_m / self.t_w
            J = 4 * A_m**2 / line
        else:
            a, bb = max(self.b, self.h), min(self.b, self.h)
            J = a * bb**3 * (1 / 3 - 0.21 * bb / a * (1 - bb**4 / (12 * a**4)))
        c_top = abs(zc)
        c_bot = abs(-self.h - zc)
        return {
            "A": A, "zc": zc, "Iyy": Iyy, "Izz": Izz, "J": J, "Q": Q,
            "c_max": max(c_top, c_bot), "c_lat": self.b / 2,
            "A_channel": c["A"], "Ip": Iyy + Izz,
        }


def rect_props(b: float, h: float) -> dict:
    return {"A": b * h, "Iyy": b * h**3 / 12, "Izz": h * b**3 / 12}


def box_props(width: float, height: float, t_top: float, t_bot: float, t_wall: float) -> dict:
    """Closed thin-walled box (body tub + top plate) about its own centroid."""
    parts = [
        (width, t_bot, t_bot / 2),
        (width, t_top, height - t_top / 2),
        (t_wall, height - t_top - t_bot, height / 2),
        (t_wall, height - t_top - t_bot, height / 2),
    ]
    A = sum(w * t for w, t, _ in parts)
    zc = sum(w * t * z for w, t, z in parts) / A
    Iyy = sum(w * t**3 / 12 + w * t * (z - zc) ** 2 for w, t, z in parts)
    A_m = (width - t_wall) * (height - (t_top + t_bot) / 2)
    line = 2 * (width - t_wall) / ((t_top + t_bot) / 2) + 2 * (height - (t_top + t_bot) / 2) / t_wall
    J = 4 * A_m**2 / line
    return {"A": A, "zc": zc, "Iyy": Iyy, "J": J}


def bolt_bearing_area(d: float, t: float) -> float:
    return d * t


def circle_area(d: float) -> float:
    return math.pi * d * d / 4
