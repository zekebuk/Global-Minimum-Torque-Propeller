"""Rotor load tables for a flight simulator: thrust, torque, H-force, and hub roll moment in any inflow.

First principles. With the blade fixed, the forward-flight BEMT (``multirotor.rotor_forward``)
depends on the rotor speed Omega and on the inflow the hub sees: an axial part V_axial along the
thrust axis (climb positive) and an edgewise part V_edge in the disk plane. Dimensional analysis
with a Reynolds-independent polar collapses that to two ratios,

    lambda_c = V_axial / (Omega R),   mu = V_edge / (Omega R),

and four coefficients that depend on them only:

    T = rho A (Omega R)^2 C_T,   Q = rho A (Omega R)^2 R C_Q,   H = rho A (Omega R)^2 C_H,
    M_roll = rho A (Omega R)^2 R C_M,   A = pi R^2.

So a table over (lambda_c, mu) at one rpm is exact at every rpm. A Reynolds-dependent (table)
polar adds rpm as a third axis. Interpolation is multilinear; inflow beyond the grid is clamped to
its edge, which for Omega -> 0 still sends every load to zero with (Omega R)^2.

The table also carries the peak section Cl over the thrust-carrying disk (azimuth points with at
least 25% of the tip dynamic pressure), which depends on the same two ratios; with a polar that has
no stall drag rise it is the stall check. Root sections inside that boundary carry little thrust and
are excluded: GMT twists load the root hard, and their root stalls first in edgewise flow (uniform
inflow drops with translational lift and raises every section's angle of attack).

Validity. Momentum theory fails in the vortex-ring state (descending into the rotor's own wake).
Cells inside the usual VRS boundary are flagged, not corrected: -2 < V_axial / v_h < -0.3 with
V_edge / v_h < 1, where v_h = Omega R sqrt(C_T / 2) is the ideal hover induced velocity at that
thrust (boundaries after Wolkovitch 1972 and Johnson, NASA TP-2005-213477). Reverse flow on the
retreating side carries no load, and the induced velocity is uniform over the disk.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from prandtl_prop.aero.bemt import BladeStations, OperatingPoint
from prandtl_prop.aero.polars import AnalyticPolar

VRS_DESCENT_RANGE = (-2.0, -0.3)  # V_axial / v_h
VRS_EDGEWISE_LIMIT = 1.0  # V_edge / v_h


@dataclass(frozen=True)
class RotorTable:
    name: str
    radius_m: float
    rho_kg_m3: float
    blade_count: int
    rpm: np.ndarray  # (n_rpm,): one entry when the polar is Reynolds-independent
    lambda_c: np.ndarray  # (n_lambda,)
    mu: np.ndarray  # (n_mu,)
    ct: np.ndarray  # (n_rpm, n_lambda, n_mu)
    cq: np.ndarray
    ch: np.ndarray
    cm: np.ndarray
    vrs: np.ndarray  # bool, same shape
    peak_cl: np.ndarray | None = None  # peak section Cl where q >= 25% of the tip's (stall check)
    cq0: np.ndarray | None = None  # the section-drag (profile) part of C_Q

    @property
    def disk_area_m2(self) -> float:
        return math.pi * self.radius_m**2

    def coefficients(self, rpm, lambda_c, mu) -> dict[str, np.ndarray]:
        rpm, lambda_c, mu = np.broadcast_arrays(np.asarray(rpm, float), np.asarray(lambda_c, float), np.asarray(mu, float))
        axes = [(self.lambda_c, lambda_c), (self.mu, mu)]
        if len(self.rpm) > 1:
            axes.insert(0, (self.rpm, rpm))
        corners = [_bracket(grid, x) for grid, x in axes]
        out = {}
        for key in ("ct", "cq", "ch", "cm", "peak_cl", "cq0"):
            if getattr(self, key) is None:
                continue
            table = getattr(self, key).astype(float)
            if len(self.rpm) == 1:
                table = table[0]
            value = np.zeros(lambda_c.shape)
            for bits in range(1 << len(axes)):
                index, weight = [], np.ones(lambda_c.shape)
                for axis, (low, frac) in enumerate(corners):
                    upper = (bits >> axis) & 1
                    index.append(low + upper)
                    weight = weight * (frac if upper else 1.0 - frac)
                value = value + weight * table[tuple(index)]
            out[key] = value
        out["vrs"] = vrs_flag(out["ct"], lambda_c, mu)  # from the interpolated thrust, not the coarse cell flags
        return out

    def evaluate(self, rpm, v_axial_m_s, v_edge_m_s) -> dict[str, np.ndarray]:
        """Dimensional loads: thrust (N), torque (N m), H-force (N, opposing edgewise motion), hub roll moment (N m)."""

        omega = np.maximum(np.asarray(rpm, float), 1.0e-9) * math.pi / 30.0
        tip = omega * self.radius_m
        c = self.coefficients(rpm, np.asarray(v_axial_m_s) / tip, np.asarray(v_edge_m_s) / tip)
        scale = self.rho_kg_m3 * self.disk_area_m2 * tip**2
        return {
            "thrust_N": scale * c["ct"],
            "torque_Nm": scale * self.radius_m * c["cq"],
            "h_force_N": scale * c["ch"],
            "roll_moment_Nm": scale * self.radius_m * c["cm"],
            "vrs": c["vrs"],
            "peak_cl": c.get("peak_cl", np.full(np.shape(c["ct"]), np.nan)),
            "profile_torque_Nm": scale * self.radius_m * c["cq0"] if "cq0" in c else np.full(np.shape(c["ct"]), np.nan),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "prandtl-rotor-table/1",
            "name": self.name,
            "radius_m": self.radius_m,
            "rho_kg_m3": self.rho_kg_m3,
            "blade_count": self.blade_count,
            "axes": {"rpm": self.rpm.tolist(), "lambda_c": self.lambda_c.tolist(), "mu": self.mu.tolist()},
            "shape": list(self.ct.shape),
            "layout": "C order [rpm][lambda_c][mu]",
            "definitions": {
                "lambda_c": "V_axial / (Omega R), V_axial = hub velocity relative to air along the thrust axis (climb > 0)",
                "mu": "V_edge / (Omega R), V_edge = |hub velocity relative to air in the disk plane|",
                "thrust_N": "rho pi R^2 (Omega R)^2 C_T, along the thrust axis",
                "torque_Nm": "rho pi R^2 (Omega R)^2 R C_Q, aerodynamic torque opposing rotation",
                "h_force_N": "rho pi R^2 (Omega R)^2 C_H, in the disk plane opposing the edgewise motion",
                "roll_moment_Nm": "rho pi R^2 (Omega R)^2 R C_M, advancing side up; body vector = -s M e_f (see docs/sitl.md)",
                "vrs": "1 inside the vortex-ring-state boundary (momentum theory invalid there)",
                "peak_cl": "largest section Cl over the azimuth points carrying >= 25% of the tip dynamic pressure",
                "cq0": "section-drag (profile) part of C_Q: B q c Cd cos(phi) r, azimuth-averaged",
            },
            "ct": self.ct.tolist(),
            "cq": self.cq.tolist(),
            "ch": self.ch.tolist(),
            "cm": self.cm.tolist(),
            "vrs": self.vrs.astype(int).tolist(),
            **({"peak_cl": self.peak_cl.tolist()} if self.peak_cl is not None else {}),
            **({"cq0": self.cq0.tolist()} if self.cq0 is not None else {}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RotorTable:
        axes = data["axes"]
        return cls(
            name=data["name"],
            radius_m=float(data["radius_m"]),
            rho_kg_m3=float(data["rho_kg_m3"]),
            blade_count=int(data["blade_count"]),
            rpm=np.array(axes["rpm"], float),
            lambda_c=np.array(axes["lambda_c"], float),
            mu=np.array(axes["mu"], float),
            ct=np.array(data["ct"], float),
            cq=np.array(data["cq"], float),
            ch=np.array(data["ch"], float),
            cm=np.array(data["cm"], float),
            vrs=np.array(data["vrs"], bool),
            peak_cl=np.array(data["peak_cl"], float) if "peak_cl" in data else None,
            cq0=np.array(data["cq0"], float) if "cq0" in data else None,
        )

    def save(self, path: Path) -> Path:
        path.write_text(json.dumps(self.to_dict()), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> RotorTable:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _bracket(grid: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Lower index and fraction along one axis, clamped to the grid."""

    if len(grid) == 1:
        return np.zeros(x.shape, dtype=int), np.zeros(x.shape)
    x = np.clip(x, grid[0], grid[-1])
    low = np.clip(np.searchsorted(grid, x, side="right") - 1, 0, len(grid) - 2)
    return low, (x - grid[low]) / (grid[low + 1] - grid[low])


def vrs_flag(ct: np.ndarray, lambda_c: np.ndarray, mu: np.ndarray) -> np.ndarray:
    vh = np.sqrt(np.maximum(ct, 0.0) / 2.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        descent, edge = lambda_c / vh, mu / vh
    return (ct > 0.0) & (descent > VRS_DESCENT_RANGE[0]) & (descent < VRS_DESCENT_RANGE[1]) & (edge < VRS_EDGEWISE_LIMIT)


def build_table(
    name: str,
    stations: BladeStations,
    op: OperatingPoint,
    polar,
    spec,
    *,
    tip_loss: bool = True,
    corrections=None,
    azimuth_points: int = 36,
) -> RotorTable:
    """Tabulate ``multirotor.rotor_forward`` over (rpm,) lambda_c, mu (``spec``: a SitlSpec)."""

    from prandtl_prop.multirotor import rotor_forward

    rpms = [op.rpm] if isinstance(polar, AnalyticPolar) else [op.rpm * f for f in spec.rpm_fractions]
    lam, mu = np.array(spec.lambda_grid, float), np.array(spec.mu_grid, float)
    shape = (len(rpms), len(lam), len(mu))
    ct, cq, ch, cm, peak, cq0 = (np.zeros(shape) for _ in range(6))
    for k, rpm in enumerate(rpms):
        point = replace(op, rpm=rpm, v_inf_m_s=0.0)
        tip = point.omega_rad_s * point.radius_m
        scale = point.rho_kg_m3 * point.disk_area_m2 * tip**2
        for i, lc in enumerate(lam):
            for j, m in enumerate(mu):
                out = rotor_forward(
                    stations,
                    point,
                    polar,
                    v_edge_m_s=float(m) * tip,
                    v_axial_m_s=float(lc) * tip,
                    tip_loss=tip_loss,
                    azimuth_points=azimuth_points,
                    corrections=corrections,
                )
                ct[k, i, j] = out["thrust_N"] / scale
                cq[k, i, j] = out["torque_Nm"] / (scale * point.radius_m)
                ch[k, i, j] = out["h_force_N"] / scale
                cm[k, i, j] = out["hub_roll_moment_Nm"] / (scale * point.radius_m)
                peak[k, i, j] = out["peak_cl_outer"] if math.isfinite(out["peak_cl_outer"]) else 0.0
                cq0[k, i, j] = out["profile_torque_Nm"] / (scale * point.radius_m)
    vrs = vrs_flag(ct, lam[None, :, None], mu[None, None, :])
    return RotorTable(name, op.radius_m, op.rho_kg_m3, op.blade_count, np.array(rpms, float), lam, mu, ct, cq, ch, cm, vrs, peak, cq0)


def write_cpp_tables(table: RotorTable, path: Path) -> Path:
    """Generated C++ header: the table as constexpr arrays and a ``prandtl::RotorTable`` view (see prandtl_rotor.hpp)."""

    ident = "".join(ch if ch.isalnum() else "_" for ch in table.name)

    def array(label: str, values: np.ndarray) -> str:
        flat = ", ".join(f"{float(v):.9g}" for v in np.asarray(values, float).ravel())
        return f"inline constexpr double {label}[] = {{{flat}}};"

    lines = [
        f"// Generated by prandtl_prop.sitl from design '{table.name}'. Do not edit; re-export instead.",
        "#pragma once",
        '#include "prandtl_rotor.hpp"',
        "",
        f"namespace prandtl::tables::{ident} {{",
        array("rpm", table.rpm),
        array("lambda_c", table.lambda_c),
        array("mu", table.mu),
        array("ct", table.ct),
        array("cq", table.cq),
        array("ch", table.ch),
        array("cm", table.cm),
        array("vrs", table.vrs.astype(float)),
        "inline constexpr prandtl::RotorTable table{",
        f"    {len(table.rpm)}, {len(table.lambda_c)}, {len(table.mu)}, rpm, lambda_c, mu, ct, cq, ch, cm, vrs,",
        f"    {table.radius_m:.9g}, {table.rho_kg_m3:.9g}}};",
        f"}}  // namespace prandtl::tables::{ident}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
