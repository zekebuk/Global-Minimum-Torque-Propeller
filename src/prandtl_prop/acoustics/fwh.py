"""Tonal rotor noise: Ffowcs Williams-Hawkings, Farassat Formulation 1A, for a rigidly rotating rotor.

Sources rotate about +z at angular speed Omega in a medium at rest (hover; the free stream is not
modelled). With r the source-to-observer distance, r_hat its unit vector, M = v / c, M_r = M . r_hat:

- **Thickness (monopole), over the blade surface panels:**
      4 pi p_T = int [ rho0 (U_n_dot + U_ndot) / (r (1 - M_r)^2) ]
               + int [ rho0 U_n (r M_r_dot + c (M_r - M^2)) / (r^2 (1 - M_r)^3) ]
  with U_n = v . n, U_n_dot = v_dot . n, U_ndot = v . n_dot.
- **Loading (dipole):** with L the force per unit area the surface exerts on the fluid (p n for a
  pressure p on a surface with outward normal n), or, for a compact source, the point force on
  the fluid,
      4 pi p_L = int [ L_r_dot / (c r (1 - M_r)^2) ] + int [ (L_r - L_M) / (r^2 (1 - M_r)^2) ]
               + int [ L_r (r M_r_dot + c (M_r - M^2)) / (c r^2 (1 - M_r)^3) ]
  with L_r = L . r_hat, L_M = L . M.

Rigid rotation makes every source-time derivative analytic: v = Omega x X, v_dot = Omega x v,
n_dot = Omega x n, and a load fixed in the blade frame has L_dot = Omega x L. The integrals are
evaluated **source-time dominant**: each source is sampled uniformly over one revolution, its
reception time t = tau + r(tau)/c is computed, and its signal is interpolated (periodically) onto
a uniform observer-time grid before summing. In hover blade k is blade 0 delayed by k/B of a
revolution, so one blade is computed and the others are exact time shifts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RotorSources:
    """Sources of one blade, in the blade frame at tau = 0 (rotor axis +z, rotation +z).

    compact: positions (n, 3) with forces on the fluid (n, 3); panels: centroids (m, 3), outward
    unit normals (m, 3), areas (m,), and optional gauge pressures (m,) for panel loading.
    """

    compact_positions_m: np.ndarray | None = None
    compact_forces_N: np.ndarray | None = None
    panel_centroids_m: np.ndarray | None = None
    panel_normals: np.ndarray | None = None
    panel_areas_m2: np.ndarray | None = None
    panel_pressures_Pa: np.ndarray | None = None


def _rotate_z(vectors: np.ndarray, angles: np.ndarray) -> np.ndarray:
    """vectors (n, 3) rotated by each angle (k,): (k, n, 3)."""

    c, s = np.cos(angles)[:, None], np.sin(angles)[:, None]
    x, y, z = vectors[:, 0][None, :], vectors[:, 1][None, :], vectors[:, 2][None, :]
    return np.stack([c * x - s * y, s * x + c * y, np.broadcast_to(z, (len(angles), len(vectors)))], axis=-1)


def _cross_z(omega: float, vectors: np.ndarray) -> np.ndarray:
    """Omega z_hat x vectors."""

    out = np.empty_like(vectors)
    out[..., 0] = -omega * vectors[..., 1]
    out[..., 1] = omega * vectors[..., 0]
    out[..., 2] = 0.0
    return out


def _received(observer: np.ndarray, X: np.ndarray, tau: np.ndarray, c0: float):
    """Geometry at each source time: distance, unit vector, reception time."""

    d = observer[None, None, :] - X
    r = np.linalg.norm(d, axis=-1)
    return r, d / r[..., None], tau[:, None] + r / c0


def _thickness(X, n, area, omega, c0, rho0, r, rhat):
    v = _cross_z(omega, X)
    a = _cross_z(omega, v)
    ndot = _cross_z(omega, n)
    M = v / c0
    Mr = (M * rhat).sum(-1)
    Mdot_r = (a / c0 * rhat).sum(-1)
    M2 = (M * M).sum(-1)
    Un = (v * n).sum(-1)
    dUn = (a * n).sum(-1) + (v * ndot).sum(-1)
    one = 1.0 - Mr
    near = rho0 * dUn / (r * one**2)
    far = rho0 * Un * (r * Mdot_r + c0 * (Mr - M2)) / (r**2 * one**3)
    return (near + far) * area / (4.0 * math.pi)


def _loading(X, L, omega, c0, r, rhat):
    v = _cross_z(omega, X)
    a = _cross_z(omega, v)
    Ldot = _cross_z(omega, L)
    M = v / c0
    Mr = (M * rhat).sum(-1)
    Mdot_r = (a / c0 * rhat).sum(-1)
    M2 = (M * M).sum(-1)
    Lr = (L * rhat).sum(-1)
    LM = (L * M).sum(-1)
    dLr = (Ldot * rhat).sum(-1)
    one = 1.0 - Mr
    term1 = dLr / (c0 * r * one**2)
    term2 = (Lr - LM) / (r**2 * one**2)
    term3 = Lr * (r * Mdot_r + c0 * (Mr - M2)) / (c0 * r**2 * one**3)
    return (term1 + term2 + term3) / (4.0 * math.pi)


def _to_observer_grid(t_rec: np.ndarray, p: np.ndarray, period: float, n_out: int) -> np.ndarray:
    """Sum per-source periodic signals p (k, n) received at t_rec (k, n) onto a uniform grid of one period."""

    # For subsonic sources t_rec(tau) is monotonic, so each column is one sorted period. Close each
    # column periodically, offset the columns apart, and interpolate all of them with one searchsorted.
    grid = np.arange(n_out) * period / n_out
    k, n = p.shape
    tt = np.vstack([t_rec, t_rec[:1] + period])
    pp = np.vstack([p, p[:1]])
    offset = (np.arange(n) * 4.0 * period)[None, :]
    keys = (tt + offset).T.ravel()
    t0 = t_rec[0][None, :]
    queries = t0 + np.mod(grid[:, None] - t0, period)
    q = (queries + offset).T.ravel()
    idx = np.clip(np.searchsorted(keys, q), 1, len(keys) - 1)
    x0, x1 = keys[idx - 1], keys[idx]
    y = pp.T.ravel()
    y0, y1 = y[idx - 1], y[idx]
    values = y0 + (y1 - y0) * (q - x0) / np.where(x1 > x0, x1 - x0, 1.0)
    return values.reshape(n, n_out).sum(axis=0)


def pressure_history(
    sources: RotorSources,
    observers_m: np.ndarray,
    *,
    omega_rad_s: float,
    blade_count: int,
    c0: float = 343.0,
    rho0: float = 1.225,
    samples_per_rev: int = 720,
    chunk: int = 2000,
) -> dict[str, np.ndarray]:
    """Acoustic pressure over one revolution at each observer: {"t", "thickness", "loading"} (observers x samples)."""

    period = 2.0 * math.pi / omega_rad_s
    samples_per_rev = blade_count * math.ceil(samples_per_rev / blade_count)  # blade k = blade 0 shifted by k/B rev
    tau = np.arange(samples_per_rev) * period / samples_per_rev
    angles = omega_rad_s * tau
    observers = np.atleast_2d(observers_m)
    thick = np.zeros((len(observers), samples_per_rev))
    load = np.zeros((len(observers), samples_per_rev))

    def contributions(kind: str):
        if kind == "compact" and sources.compact_positions_m is not None:
            return sources.compact_positions_m, sources.compact_forces_N, None, None
        if kind == "panels" and sources.panel_centroids_m is not None:
            loads = None
            if sources.panel_pressures_Pa is not None:
                loads = sources.panel_pressures_Pa[:, None] * sources.panel_normals * sources.panel_areas_m2[:, None]
            return sources.panel_centroids_m, loads, sources.panel_normals, sources.panel_areas_m2
        return None

    for kind in ("compact", "panels"):
        data = contributions(kind)
        if data is None:
            continue
        positions, forces, normals, areas = data
        for start in range(0, len(positions), chunk):
            sl = slice(start, start + chunk)
            X = _rotate_z(positions[sl], angles)
            F = _rotate_z(forces[sl], angles) if forces is not None else None
            N = _rotate_z(normals[sl], angles) if normals is not None else None
            for i, obs in enumerate(observers):
                r, rhat, t_rec = _received(obs, X, tau, c0)
                if F is not None:
                    load[i] += _to_observer_grid(t_rec, _loading(X, F, omega_rad_s, c0, r, rhat), period, samples_per_rev)
                if N is not None and kind == "panels":
                    p = _thickness(X, N, areas[sl][None, :], omega_rad_s, c0, rho0, r, rhat)
                    thick[i] += _to_observer_grid(t_rec, p, period, samples_per_rev)
    # In hover the B blades are copies of blade 0 delayed by k/B of a revolution (exact).
    shift = samples_per_rev // blade_count
    thick = sum(np.roll(thick, -k * shift, axis=1) for k in range(blade_count))
    load = sum(np.roll(load, -k * shift, axis=1) for k in range(blade_count))
    t = np.arange(samples_per_rev) * period / samples_per_rev
    return {"t": t, "thickness": thick, "loading": load}


def harmonics(p: np.ndarray, blade_count: int, count: int) -> np.ndarray:
    """RMS amplitude (Pa) of the first ``count`` blade-passing harmonics of one-revolution signals (observers x samples)."""

    spectrum = np.fft.rfft(p, axis=-1) / p.shape[-1]
    idx = blade_count * np.arange(1, count + 1)
    idx = idx[idx < spectrum.shape[-1]]
    return np.sqrt(2.0) * np.abs(spectrum[..., idx])


def gutin_rms(
    m: int, blade_count: int, omega: float, thrust_N: float, torque_Nm: float, r_e: float, distance: float, theta: float, c0: float = 343.0
) -> float:
    """Far-field Gutin harmonic (RMS Pa) for steady thrust and torque concentrated at radius r_e.

    theta is measured from the thrust (+z, upstream) axis. Used only to verify the FW-H solver.
    """

    from scipy.special import jv

    k = m * blade_count
    return abs(
        k
        * omega
        / (2.0 * math.sqrt(2.0) * math.pi * c0 * distance)
        * (-thrust_N * math.cos(theta) + torque_Nm * c0 / (omega * r_e**2))
        * jv(k, k * omega * r_e * math.sin(theta) / c0)
    )
