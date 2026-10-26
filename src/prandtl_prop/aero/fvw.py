"""Free-vortex-wake lifting-line rotor solver (mid-fidelity, after NREL OLAF and CHARM-type methods).

Why. BEMT assumes each annulus sees a uniform induced velocity from momentum theory. That is good
in axial hover but is the weakest link in edgewise flight, where the stall constraint now binds
(docs/efficiency.md): the real inflow is skewed, strongest at the rear of the disk, and the tip
vortices of earlier blades pass close to later ones (blade-vortex interaction, BVI). A free wake
computes that inflow from the vorticity itself.

Method (time-marching, rotor hub fixed, blades rotating at Omega about +z, thrust along +z):

- **Blades.** Each blade is a lifting line at the quarter chord, split into ``panels`` spanwise
  panels with cosine spacing. Each panel carries a vortex ring from the lifting line to the
  trailing edge (strength Gamma, the bound circulation); its trailing-edge segment is the first
  shed vortex. The circulation comes from the section polar through Kutta-Joukowski,
      Gamma = 1/2 |W| c Cl(alpha),   alpha = beta - phi,
  with W the local velocity (rotation + free stream + induced from every ring except the panel's
  own bound segment), iterated with under-relaxation at every step. Profile drag acts along W.
- **Wake.** At every step each blade's current ring strengths are shed into a new row of wake
  rings attached at the trailing edge; older rows convect with the local velocity (free stream +
  induced velocity of all blade and wake rings), first-order explicit. Rows older than
  ``revolutions`` are dropped (the far wake's influence on the disk is small after ~3 turns in hover).
  Ring formulation: adjacent ring segments cancel automatically, leaving trailed vorticity
  dGamma/dr and shed vorticity dGamma/dt.
- **Regularisation.** Biot-Savart with the Vatistas n = 2 core,
      v = Gamma/(4 pi) (r1 x r2)/|r1 x r2|^2 (r0 . (r1/|r1| - r2/|r2|)) h^2 / sqrt(rc^4 + h^4),
  h the distance to the segment line, and Squire core growth rc^2 = rc0^2 + 4 alpha_L delta nu t
  (alpha_L = 1.25643, delta the turbulent-viscosity factor, Bhagwat & Leishman 2002).

Outputs: thrust, torque, and power averaged over the last revolution; spanwise circulation,
induced velocity, angle of attack, and Cl at every azimuth step (the unsteady blade loading for
the FW-H solver and the stall check); the wake geometry (tip-vortex miss distances for BVI).

Validated against BEMT in hover (tests/test_aero_methods.py) and run edgewise where BEMT's uniform inflow is
the approximation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from prandtl_prop.aero.polars import AnalyticPolar

ALPHA_L = 1.25643


@dataclass(frozen=True)
class FvwSettings:
    panels: int = 12
    steps_per_rev: int = 36
    revolutions: float = 4.0  # wake age kept
    run_revolutions: float = 5.0
    core_radius_over_chord: float = 0.1  # initial Vatistas core radius / tip chord
    turbulent_viscosity_factor: float = 50.0  # delta in the Squire core growth
    gamma_relaxation: float = 0.3
    gamma_iterations: int = 30
    chunk: int = 128


@dataclass
class FvwResult:
    thrust_N: float
    torque_Nm: float
    power_W: float
    h_force_N: float
    r_m: np.ndarray
    psi_deg: np.ndarray  # azimuth of blade 0 at each stored step (last revolution)
    gamma: np.ndarray  # (steps, blades, panels)
    alpha_deg: np.ndarray
    cl: np.ndarray
    dT_dr: np.ndarray  # (steps, blades, panels) thrust per span, N/m
    dQ_dr: np.ndarray  # torque per span, N m / m
    v_axial_induced: np.ndarray  # (steps, blades, panels) induced velocity along -z (downwash positive)
    wake_tip: np.ndarray  # (blades, rows, 3) tip-vortex node positions at the end
    history: dict[str, list[float]] = field(default_factory=dict)


try:  # optional: compiled, multi-threaded kernel (pip install numba); the NumPy path below is the reference
    import numba

    @numba.njit(parallel=True, fastmath=True, cache=True)
    def _biot_savart_numba(points, a, b, gamma, core):  # pragma: no cover - exercised when numba is installed
        out = np.zeros_like(points)
        n_seg = a.shape[0]
        for m in numba.prange(points.shape[0]):
            px, py, pz = points[m, 0], points[m, 1], points[m, 2]
            vx = 0.0
            vy = 0.0
            vz = 0.0
            for i in range(n_seg):
                r1x, r1y, r1z = px - a[i, 0], py - a[i, 1], pz - a[i, 2]
                r2x, r2y, r2z = px - b[i, 0], py - b[i, 1], pz - b[i, 2]
                cx = r1y * r2z - r1z * r2y
                cy = r1z * r2x - r1x * r2z
                cz = r1x * r2y - r1y * r2x
                c2 = cx * cx + cy * cy + cz * cz + 1e-30
                n1 = math.sqrt(r1x * r1x + r1y * r1y + r1z * r1z) + 1e-30
                n2 = math.sqrt(r2x * r2x + r2y * r2y + r2z * r2z) + 1e-30
                r0x, r0y, r0z = b[i, 0] - a[i, 0], b[i, 1] - a[i, 1], b[i, 2] - a[i, 2]
                dot = r0x * (r1x / n1 - r2x / n2) + r0y * (r1y / n1 - r2y / n2) + r0z * (r1z / n1 - r2z / n2)
                h2 = c2 / (r0x * r0x + r0y * r0y + r0z * r0z + 1e-30)
                kernel = h2 / math.sqrt(core[i] ** 4 + h2 * h2)
                coeff = gamma[i] / (4.0 * math.pi) * dot / c2 * kernel
                vx += coeff * cx
                vy += coeff * cy
                vz += coeff * cz
            out[m, 0] = vx
            out[m, 1] = vy
            out[m, 2] = vz
        return out

except ImportError:  # pragma: no cover
    _biot_savart_numba = None


def biot_savart(points: np.ndarray, a: np.ndarray, b: np.ndarray, gamma: np.ndarray, core: np.ndarray, chunk: int = 128) -> np.ndarray:
    """Velocity at ``points`` (M,3) induced by straight segments a->b (S,3) of strength gamma (S,), Vatistas n=2 cores (S,)."""

    if _biot_savart_numba is not None and len(points) * len(a) > 20000:
        return _biot_savart_numba(
            np.ascontiguousarray(points, dtype=np.float64),
            np.ascontiguousarray(a, dtype=np.float64),
            np.ascontiguousarray(b, dtype=np.float64),
            np.ascontiguousarray(gamma, dtype=np.float64),
            np.ascontiguousarray(core, dtype=np.float64),
        )
    return biot_savart_numpy(points, a, b, gamma, core, chunk)


def biot_savart_numpy(
    points: np.ndarray, a: np.ndarray, b: np.ndarray, gamma: np.ndarray, core: np.ndarray, chunk: int = 128
) -> np.ndarray:
    """Reference NumPy implementation of ``biot_savart``."""

    out = np.zeros_like(points)
    r0 = b - a
    r0_sq = np.einsum("ij,ij->i", r0, r0) + 1e-30
    rc4 = core**4
    for start in range(0, len(points), chunk):
        p = points[start : start + chunk, None, :]
        r1 = p - a[None]
        r2 = p - b[None]
        cross = np.cross(r1, r2)
        cross_sq = np.einsum("mij,mij->mi", cross, cross) + 1e-30
        n1 = np.sqrt(np.einsum("mij,mij->mi", r1, r1)) + 1e-30
        n2 = np.sqrt(np.einsum("mij,mij->mi", r2, r2)) + 1e-30
        dot = np.einsum("ij,mij->mi", r0, r1 / n1[..., None] - r2 / n2[..., None])
        h_sq = cross_sq / r0_sq[None]
        kernel = h_sq / np.sqrt(rc4[None] + h_sq**2)
        coeff = gamma[None] / (4.0 * math.pi) * dot / cross_sq * kernel
        out[start : start + chunk] = np.einsum("mi,mij->mj", coeff, cross)
    return out


def ring_segments(nodes_le: np.ndarray, nodes_te: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Four segments per ring for rings between node rows (..., n+1, 3); returns a, b (R*4, 3) and the ring index per segment."""

    l0, l1 = nodes_le[..., :-1, :], nodes_le[..., 1:, :]
    t0, t1 = nodes_te[..., :-1, :], nodes_te[..., 1:, :]
    a = np.stack([l0, l1, t1, t0], axis=-2).reshape(-1, 3)
    b = np.stack([l1, t1, t0, l0], axis=-2).reshape(-1, 3)
    rings = np.repeat(np.arange(a.shape[0] // 4), 4)
    return a, b, rings


def solve(
    stations,
    radius_m: float,
    blade_count: int,
    rpm: float,
    polar,
    *,
    v_inf_m_s: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rho_kg_m3: float = 1.225,
    nu_m2_s: float = 1.48e-5,
    settings: FvwSettings | None = None,
    collective_offset_deg: float = 0.0,
) -> FvwResult:
    """Run the free wake. ``v_inf_m_s``: free stream relative to the hub in the hub frame (edgewise along -x, climb along -z)."""

    s = settings or FvwSettings()
    omega = rpm * math.pi / 30.0
    dpsi = 2.0 * math.pi / s.steps_per_rev
    dt = dpsi / omega
    root = float(stations.r_m[0] - 0.5 * stations.dr_m[0])
    # cosine-spaced panel edges between the root and the tip
    edges = root + (radius_m - root) * 0.5 * (1.0 - np.cos(np.linspace(0.0, math.pi, s.panels + 1)))
    mids = 0.5 * (edges[1:] + edges[:-1])
    widths = np.diff(edges)
    chord = np.interp(mids, stations.r_m, stations.chord_m)
    twist = np.interp(mids, stations.r_m, stations.twist_deg) + collective_offset_deg
    airfoil = [stations.airfoil[int(np.argmin(np.abs(stations.r_m - r)))] for r in mids]
    chord_edges = np.interp(edges, stations.r_m, stations.chord_m)
    twist_edges = np.radians(np.interp(edges, stations.r_m, stations.twist_deg) + collective_offset_deg)
    v_inf = np.asarray(v_inf_m_s, float)
    core0 = s.core_radius_over_chord * float(chord[-1])
    keep_rows = int(round(s.revolutions * s.steps_per_rev))
    total_steps = int(round(s.run_revolutions * s.steps_per_rev))

    def blade_frame(psi: float):
        """Lifting-line and trailing-edge node positions of every blade at azimuth psi (blade 0 at psi)."""

        ll, te = [], []
        for k in range(blade_count):
            ang = psi + 2.0 * math.pi * k / blade_count
            e_r = np.array([math.cos(ang), math.sin(ang), 0.0])
            e_t = np.array([-math.sin(ang), math.cos(ang), 0.0])  # direction of blade motion (CCW about +z)
            z = np.array([0.0, 0.0, 1.0])
            # chord line: leading edge ahead (+e_t), nose up by beta (thrust +z): TE at -0.75 c (cos b e_t - sin b z)... trailing below
            chord_dir = -np.cos(twist_edges)[:, None] * e_t[None] - np.sin(twist_edges)[:, None] * z[None]
            ll.append(edges[:, None] * e_r[None])
            te.append(edges[:, None] * e_r[None] + 0.75 * chord_edges[:, None] * chord_dir)
        return np.array(ll), np.array(te)

    def polar_cl_cd(alpha_deg: np.ndarray, w: np.ndarray):
        if isinstance(polar, AnalyticPolar):
            cl = np.clip(polar.cl_alpha_per_rad * np.radians(alpha_deg - polar.alpha0_deg), polar.cl_min, polar.cl_max)
            return cl, polar.cd0 + polar.k * cl**2
        cl = np.empty_like(alpha_deg)
        cd = np.empty_like(alpha_deg)
        reynolds = w * chord[None] / nu_m2_s
        for idx in np.ndindex(alpha_deg.shape):
            point = polar.evaluate(airfoil[idx[-1]], float(alpha_deg[idx]), float(reynolds[idx]))
            cl[idx], cd[idx] = point.cl, point.cd
        return cl, cd

    gamma = np.zeros((blade_count, s.panels))
    wake = np.zeros((blade_count, 0, s.panels + 1, 3))  # rows of nodes, row 0 newest (at the trailing edge)
    wake_gamma = np.zeros((blade_count, 0, s.panels))
    wake_age = np.zeros(0)
    stored = {k: [] for k in ("psi", "gamma", "alpha", "cl", "dT", "dQ", "vz", "H")}
    history = {"thrust_N": [], "torque_Nm": []}

    for step in range(total_steps):
        psi = step * dpsi
        ll, te = blade_frame(psi)
        ctrl = 0.5 * (ll[:, 1:] + ll[:, :-1])  # control points on the lifting line, (B, P, 3)
        ang = psi + 2.0 * math.pi * np.arange(blade_count) / blade_count
        e_t = np.stack([-np.sin(ang), np.cos(ang), np.zeros_like(ang)], axis=-1)  # (B,3)
        blade_vel = omega * mids[None, :, None] * e_t[:, None, :]  # velocity of the blade sections
        # wake-induced velocity at control points (fixed during the Gamma iteration)
        if wake.shape[1] > 1:
            a_w, b_w, ring_w = ring_segments(wake[:, :-1], wake[:, 1:])
            g_w = wake_gamma[:, : wake.shape[1] - 1].reshape(-1)[ring_w]
            core_w = np.sqrt(
                core0**2
                + 4.0
                * ALPHA_L
                * s.turbulent_viscosity_factor
                * nu_m2_s
                * np.repeat(wake_age[: wake.shape[1] - 1], s.panels)[None].repeat(blade_count, 0).reshape(-1)[ring_w]
            )
            v_wake = biot_savart(ctrl.reshape(-1, 3), a_w, b_w, g_w, core_w, s.chunk).reshape(ctrl.shape)
        else:
            v_wake = np.zeros_like(ctrl)
        # attach the newest wake row (current strengths) from the trailing edge to the last row
        a_b, b_b, ring_b = ring_segments(ll, te)
        for _ in range(s.gamma_iterations):
            g_b = gamma.reshape(-1)[ring_b]
            v_bound = biot_savart(ctrl.reshape(-1, 3), a_b, b_b, g_b, np.full(len(a_b), 1e-4 * radius_m), s.chunk).reshape(ctrl.shape)
            v_near = np.zeros_like(ctrl)
            if wake.shape[1] >= 1:  # first wake row: ring from the TE to row 0 with the current strength
                a_n, b_n, ring_n = ring_segments(te, wake[:, 0])
                v_near = biot_savart(ctrl.reshape(-1, 3), a_n, b_n, gamma.reshape(-1)[ring_n], np.full(len(a_n), core0), s.chunk).reshape(
                    ctrl.shape
                )
            v_air = v_inf[None, None, :] + v_wake + v_bound + v_near - blade_vel  # air velocity relative to the section
            u_t = -np.einsum("bpj,bj->bp", v_air, e_t)  # tangential (onto the leading edge)
            u_p = -v_air[..., 2]  # through the disk, downward positive
            w = np.sqrt(u_t**2 + u_p**2)
            phi = np.arctan2(u_p, u_t)
            alpha = twist[None] - np.degrees(phi)
            cl, cd = polar_cl_cd(alpha, w)
            new = 0.5 * w * chord[None] * cl
            if np.max(np.abs(new - gamma)) < 1e-5 * max(1e-9, np.max(np.abs(new))):
                gamma = new
                break
            gamma = gamma + s.gamma_relaxation * (new - gamma)
        # loads per span (Kutta-Joukowski lift normal to W, drag along W)
        q = 0.5 * rho_kg_m3 * w**2 * chord[None]
        lift, drag = q * cl, q * cd
        dT = lift * np.cos(phi) - drag * np.sin(phi)
        dF = lift * np.sin(phi) + drag * np.cos(phi)  # tangential force opposing rotation
        dQ = dF * mids[None]
        thrust = float((dT * widths[None]).sum())
        torque = float((dQ * widths[None]).sum())
        history["thrust_N"].append(thrust)
        history["torque_Nm"].append(torque)
        if step >= total_steps - s.steps_per_rev:
            stored["psi"].append(math.degrees(psi) % 360.0)
            stored["gamma"].append(gamma.copy())
            stored["alpha"].append(alpha.copy())
            stored["cl"].append(cl.copy())
            stored["dT"].append(dT.copy())
            stored["dQ"].append(dQ.copy())
            stored["vz"].append(-(v_wake + v_bound + v_near)[..., 2].copy())
            # H-force: in-plane force opposing motion projected on -x (edgewise direction)
            fx = (-(dF[..., None] * e_t[:, None, :]))[..., 0]
            stored["H"].append(float((fx * widths[None]).sum()))
        # convect the wake: velocity at every node = free stream + induced by blades and wake
        new_row = te.copy()[:, None]
        nodes = np.concatenate([new_row, wake], axis=1) if wake.shape[1] else new_row
        strengths = np.concatenate([gamma[:, None], wake_gamma], axis=1) if wake.shape[1] else gamma[:, None]
        ages = np.concatenate([[0.0], wake_age])
        if nodes.shape[1] > 1:
            a_w, b_w, ring_w = ring_segments(nodes[:, :-1], nodes[:, 1:])
            g_w = strengths[:, : nodes.shape[1] - 1].reshape(-1)[ring_w]
            age_r = np.broadcast_to(ages[None, : nodes.shape[1] - 1, None], (blade_count, nodes.shape[1] - 1, s.panels)).reshape(-1)[ring_w]
            core_w = np.sqrt(core0**2 + 4.0 * ALPHA_L * s.turbulent_viscosity_factor * nu_m2_s * age_r)
            free = nodes[:, 1:].reshape(-1, 3)
            v = biot_savart(
                free,
                np.concatenate([a_w, a_b]),
                np.concatenate([b_w, b_b]),
                np.concatenate([g_w, gamma.reshape(-1)[ring_b]]),
                np.concatenate([core_w, np.full(len(a_b), core0)]),
                s.chunk,
            )
            nodes[:, 1:] = (free + (v + v_inf[None]) * dt).reshape(nodes[:, 1:].shape)
        # the newest row leaves the trailing edge with the local velocity too (first step moves it off the blade)
        v0 = biot_savart(nodes[:, 0].reshape(-1, 3), a_b, b_b, gamma.reshape(-1)[ring_b], np.full(len(a_b), core0), s.chunk)
        nodes[:, 0] = (nodes[:, 0].reshape(-1, 3) + (v0 + v_inf[None]) * dt * 0.5).reshape(nodes[:, 0].shape)
        wake = nodes[:, :keep_rows]
        wake_gamma = strengths[:, :keep_rows]
        wake_age = (ages + dt)[:keep_rows]

    last = slice(len(history["thrust_N"]) - s.steps_per_rev, None)
    thrust = float(np.mean(history["thrust_N"][last]))
    torque = float(np.mean(history["torque_Nm"][last]))
    return FvwResult(
        thrust_N=thrust,
        torque_Nm=torque,
        power_W=torque * omega,
        h_force_N=float(np.mean(stored["H"])),
        r_m=mids,
        psi_deg=np.array(stored["psi"]),
        gamma=np.array(stored["gamma"]),
        alpha_deg=np.array(stored["alpha"]),
        cl=np.array(stored["cl"]),
        dT_dr=np.array(stored["dT"]),
        dQ_dr=np.array(stored["dQ"]),
        v_axial_induced=np.array(stored["vz"]),
        wake_tip=wake[:, :, -1, :].copy(),
        history=history,
    )
