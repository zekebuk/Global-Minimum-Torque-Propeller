"""Rotor noise from a BEMT solution: tonal (FW-H) plus broadband (BPM), spectra, directivity, sound power.

Assumptions (first principles, stated):

- **Hover**, medium at rest; the observers sit on a meridian (x-z plane) at one distance. Hover noise
  is axisymmetric in its time-averaged levels.
- **Tonal:** loading noise from compact forces at the quarter-chord line, the BEMT strip loads (force on
  the fluid = minus the blade force). Thickness noise from panels of the CAD sections. Both by
  Farassat 1A (``fwh``). Steady loading only: no blade-vortex interaction, no turbulence ingestion.
- **Broadband:** BPM self-noise (``bpm``) per blade station (span dr, chord c, speed W, angle of
  attack alpha - alpha0), with the segment's position and BPM frame swept over the blade azimuth
  (chordwise x_e downstream from the trailing edge, spanwise y_e radial, z_e along the rotor axis),
  a Doppler shift f_obs = f / (1 - M_r) per azimuth, energy averaged over the azimuth, and summed
  incoherently over stations and blades.
- **Tip vortex formation:** BPM scales the tip vortex size with an effective tip angle alpha'_tip
  set by the spanwise loading gradient at the tip. Here alpha'_tip is the zero-lift-referenced angle
  equivalent to the circulation shed within one tip chord, Gamma(R - c_tip) (Gamma = W c Cl / 2,
  Cl = 2 pi alpha at the tip speed and chord). The tip-vortex strength Gamma_v is the bound
  circulation peak outboard of 0.75 R (the vorticity that rolls into the tip vortex); blade-vortex
  interaction loading scales with it, so 20 log10(Gamma_v / Gamma_v,ref) is reported as a BVI proxy
  (forward flight, descent, rotor-rotor interaction; isolated steady hover has no BVI: its wake is
  steady in the blade frame).
- **Levels:** 1/3-octave bands 20 Hz - 20 kHz; tones are added to their bands. A-weighting per
  IEC 61672. Sound power integrates the mean-square pressure over the observer sphere (far field,
  axisymmetric).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from prandtl_prop.acoustics import bpm, fwh

P_REF = 20.0e-6
W_REF = 1.0e-12
BAND_CENTRES = 1000.0 * 2.0 ** (np.arange(-17, 14) / 3.0)  # 20 Hz ... 20 kHz


@dataclass(frozen=True)
class AcousticsSpec:
    observer_distance_m: float | None = None  # None: 5 rotor diameters
    elevations_deg: tuple[float, ...] = tuple(float(v) for v in range(-90, 91, 10))  # + above the rotor plane (upstream)
    reference_elevation_deg: float = -30.0  # the observer reported as "the" spectrum (below and to the side)
    harmonics: int = 20
    samples_per_rev: int = 720
    azimuths: int = 24
    tripped: bool = False  # molded blades at Re ~1e5: laminar boundary layer by default
    trailing_edge_thickness_m: float | None = None  # None: airfoils.trailing_edge_thickness_m, else 0.2 mm
    trailing_edge_angle_deg: float = 14.0  # BPM solid angle (NACA 0012 value)
    rounded_tip: bool = True
    speed_of_sound_m_s: float = 343.0
    thickness_noise: bool = True
    sources: tuple[str, ...] = field(default=("thickness", "loading", "tbl_te", "lbl_vs", "bluntness", "tip"))
    compare_twists: bool = True  # also evaluate GMT / bell family / MIL / constant pitch at matched thrust
    bending_weights: tuple[float, ...] = (0.0, 0.1, 0.2, 0.4, 0.8)


def a_weighting_db(f: np.ndarray) -> np.ndarray:
    f2 = np.asarray(f, dtype=float) ** 2
    ra = 12194.0**2 * f2**2 / ((f2 + 20.6**2) * np.sqrt((f2 + 107.7**2) * (f2 + 737.9**2)) * (f2 + 12194.0**2))
    return 20.0 * np.log10(ra) + 2.00


def _db(power: np.ndarray) -> np.ndarray:
    return 10.0 * np.log10(np.maximum(power, 1e-30))


def tip_vortex_metrics(result) -> dict[str, float]:
    """Circulation picture of the tip: Gamma(r), Betz roll-up strength and radius, and alpha'_tip."""

    st = result.stations
    R = result.operating_point.radius_m
    W = np.array([s.w_m_s for s in result.solutions])
    cl = np.array([s.cl for s in result.solutions])
    gamma = 0.5 * W * st.chord_m * cl
    peak = int(np.argmax(gamma))
    outboard = slice(peak, len(gamma))
    shed = -np.diff(np.concatenate([gamma[outboard], [0.0]]))  # circulation shed between stations, to zero at the tip
    radii = np.concatenate([0.5 * (st.r_m[outboard][1:] + st.r_m[outboard][:-1]), [R]])
    centroid = float((shed * radii).sum() / shed.sum()) if shed.sum() > 0 else R
    c_tip = float(st.chord_m[-1])
    gamma_one_chord = float(np.interp(R - c_tip, st.r_m, gamma))
    W_tip = float(np.interp(R - c_tip, st.r_m, W))
    alpha_tip = math.degrees(2.0 * gamma_one_chord / (W_tip * c_tip * 2.0 * math.pi))
    outer = st.r_m >= 0.75 * R
    return {
        "gamma_tip_vortex_m2_s": float(gamma[outer].max()),  # outboard circulation peak: what rolls into the tip vortex
        "gamma_peak_m2_s": float(gamma[peak]),
        "gamma_peak_r_R": float(st.r_m[peak] / R),
        "betz_rollup_centroid_r_R": centroid / R,
        "gamma_one_tip_chord_inboard_m2_s": gamma_one_chord,
        "alpha_tip_effective_deg": alpha_tip,
        "tip_chord_m": c_tip,
        "tip_speed_m_s": W_tip,
        "_gamma": gamma,
    }


def blade_panels(sections, chordwise_stride: int = 4) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Quad panels between consecutive section loops plus fan end caps; outward normals (checked).

    ``chordwise_stride`` thins the loop points: thickness noise needs panels small against the
    shortest wavelength resolved (the 20th BPF harmonic is several chords long), not airfoil detail.
    """

    loops = [s.points_3d()[::chordwise_stride] for s in sections]
    cents, norms, areas = [], [], []
    for a, b in zip(loops[:-1], loops[1:], strict=True):
        a1, b1 = np.roll(a, -1, axis=0), np.roll(b, -1, axis=0)
        n = 0.5 * np.cross(b1 - a, b - a1)  # quad (a, a1, b1, b): diagonals
        cents.append(0.25 * (a + a1 + b + b1))
        norms.append(n)
    for loop, sign in ((loops[0], -1.0), (loops[-1], 1.0)):
        centre = loop.mean(axis=0)
        nxt = np.roll(loop, -1, axis=0)
        n = 0.5 * np.cross(loop - centre, nxt - centre) * sign
        cents.append((loop + nxt + centre) / 3.0)
        norms.append(n)
    cents, norms = np.vstack(cents), np.vstack(norms)
    if (cents * norms).sum() < 0.0:  # divergence theorem: sum x . n dA = 3 V > 0 for outward normals
        norms = -norms
    # make the caps consistent with the side panels if the loop orientation flips them
    areas = np.linalg.norm(norms, axis=1)
    return cents, norms / np.maximum(areas, 1e-30)[:, None], areas


def smooth_phi(phi: np.ndarray) -> np.ndarray:
    """BPM's sin^2(phi_e) directivity has a hard null in the chord-span plane; round it off within 5 deg
    of 0 and 180 deg (quadratic, as the reference implementation does) so in-plane observers stay finite."""

    deg = np.degrees(np.abs(phi))
    sign = np.where(phi >= 0.0, 1.0, -1.0)
    deg = np.where(deg < 5.0, 0.1 * deg**2 + 2.5, np.where(deg > 175.0, -0.1 * (deg - 180.0) ** 2 + 177.5, deg))
    return sign * np.radians(deg)


def observers(spec: AcousticsSpec, radius_m: float) -> tuple[np.ndarray, float]:
    d = spec.observer_distance_m or 10.0 * radius_m
    el = np.radians(spec.elevations_deg)
    return np.column_stack([d * np.cos(el), np.zeros_like(el), d * np.sin(el)]), d


def tonal(result, sections, spec: AcousticsSpec, obs: np.ndarray) -> dict[str, np.ndarray]:
    op = result.operating_point
    st = result.stations
    B = op.blade_count
    fz = np.array([s.thrust_per_span_N_m for s in result.solutions]) * st.dr_m / B
    ft = np.array([s.tangential_per_span_N_m for s in result.solutions]) * st.dr_m / B
    positions = np.column_stack([st.r_m, np.zeros_like(st.r_m), np.zeros_like(st.r_m)])
    forces = np.column_stack([np.zeros_like(fz), ft, -fz])  # on the fluid: thrust reaction down, drag reaction along the motion
    out = {}
    load = fwh.pressure_history(
        fwh.RotorSources(compact_positions_m=positions, compact_forces_N=forces),
        obs,
        omega_rad_s=op.omega_rad_s,
        blade_count=B,
        c0=spec.speed_of_sound_m_s,
        rho0=op.rho_kg_m3,
        samples_per_rev=spec.samples_per_rev,
    )
    out["loading"] = fwh.harmonics(load["loading"], B, spec.harmonics)
    out["loading_signal"] = load["loading"]
    out["t"] = load["t"]
    if spec.thickness_noise and sections is not None:
        c, n, a = blade_panels(sections)
        thick = fwh.pressure_history(
            fwh.RotorSources(panel_centroids_m=c, panel_normals=n, panel_areas_m2=a),
            obs,
            omega_rad_s=op.omega_rad_s,
            blade_count=B,
            c0=spec.speed_of_sound_m_s,
            rho0=op.rho_kg_m3,
            samples_per_rev=spec.samples_per_rev,
        )
        out["thickness"] = fwh.harmonics(thick["thickness"], B, spec.harmonics)
        out["thickness_signal"] = thick["thickness"]
        total = thick["thickness"] + load["loading"]
    else:
        out["thickness"] = np.zeros_like(out["loading"])
        out["thickness_signal"] = np.zeros_like(load["loading"])
        total = load["loading"]
    out["total"] = fwh.harmonics(total, B, spec.harmonics)
    out["frequencies_hz"] = B * op.rpm / 60.0 * np.arange(1, out["total"].shape[1] + 1)
    return out


def broadband(result, spec: AcousticsSpec, obs: np.ndarray, *, alpha0_deg, te_thickness_m: float) -> dict[str, np.ndarray]:
    """Band mean-square pressure (observers x bands) per BPM source, azimuth-averaged with Doppler."""

    op = result.operating_point
    st = result.stations
    B = op.blade_count
    c0 = spec.speed_of_sound_m_s
    nu = op.mu_air_kg_m_s / op.rho_kg_m3
    W = np.array([s.w_m_s for s in result.solutions])
    alpha = np.array([s.alpha_deg for s in result.solutions]) - alpha0_deg
    tip = tip_vortex_metrics(result)
    psi = (np.arange(spec.azimuths) + 0.5) * 2.0 * math.pi / spec.azimuths
    # Arrays: (observer, azimuth, station, band)
    obs5 = obs[:, None, None, None, :]
    pos = np.stack(
        [st.r_m[None, :] * np.cos(psi)[:, None], st.r_m[None, :] * np.sin(psi)[:, None], np.zeros((len(psi), len(st.r_m)))], axis=-1
    )[None, :, :, None, :]
    t_hat = np.stack([-np.sin(psi), np.cos(psi), np.zeros_like(psi)], axis=-1)[None, :, None, None, :]  # blade motion
    r_hat_span = np.stack([np.cos(psi), np.sin(psi), np.zeros_like(psi)], axis=-1)[None, :, None, None, :]
    d = obs5 - pos
    dist = np.linalg.norm(d, axis=-1)
    dx = (d * -t_hat).sum(-1)  # downstream chordwise
    dy = (d * r_hat_span).sum(-1)  # spanwise
    dz = d[..., 2]  # normal
    theta_e = np.arctan2(np.sqrt(dy**2 + dz**2), dx)
    phi_e = smooth_phi(np.arctan2(dz, dy))
    blade_speed = op.omega_rad_s * st.r_m[None, None, :, None]
    Mr = blade_speed * (d * t_hat).sum(-1) / dist / c0
    f_src = BAND_CENTRES[None, None, None, :] * (1.0 - Mr)
    U = W[None, None, :, None]
    c = st.chord_m[None, None, :, None]
    L = st.dr_m[None, None, :, None]
    a = alpha[None, None, :, None]
    out: dict[str, np.ndarray] = {}

    def average(spl: np.ndarray) -> np.ndarray:
        return B * (10.0 ** (spl / 10.0)).mean(axis=1).sum(axis=1) * P_REF**2

    if "tbl_te" in spec.sources:
        out["tbl_te"] = average(bpm.tbl_te(f_src, U, L, c, dist, theta_e, phi_e, a, nu, c0, spec.tripped))
    if "lbl_vs" in spec.sources and not spec.tripped:
        out["lbl_vs"] = average(bpm.lbl_vs(f_src, U, L, c, dist, theta_e, phi_e, a, nu, c0, spec.tripped))
    if "bluntness" in spec.sources and te_thickness_m > 0.0:
        out["bluntness"] = average(
            bpm.te_bluntness(f_src, U, L, c, te_thickness_m, dist, theta_e, phi_e, a, nu, c0, spec.trailing_edge_angle_deg, spec.tripped)
        )
    if "tip" in spec.sources:
        k = len(st.r_m) - 1  # tip segment geometry
        sel = (slice(None), slice(None), slice(k, k + 1), slice(None))
        spl = bpm.tip_vortex(
            f_src[sel],
            tip["tip_speed_m_s"],
            tip["tip_chord_m"],
            dist[sel],
            theta_e[sel],
            phi_e[sel],
            tip["alpha_tip_effective_deg"],
            c0,
            spec.rounded_tip,
        )
        out["tip"] = average(spl)
    return out


def _band_of(f: np.ndarray) -> np.ndarray:
    return np.clip(np.round(3.0 * np.log2(np.asarray(f) / 1000.0)).astype(int) + 17, 0, len(BAND_CENTRES) - 1)


def rotor_noise(result, sections, spec: AcousticsSpec, *, alpha0_deg, te_thickness_m: float) -> dict[str, Any]:
    op = result.operating_point
    obs, distance = observers(spec, op.radius_m)
    tones = tonal(result, sections, spec, obs)
    broad = broadband(result, spec, obs, alpha0_deg=alpha0_deg, te_thickness_m=te_thickness_m)
    aw_band = a_weighting_db(BAND_CENTRES)
    aw_tone = a_weighting_db(tones["frequencies_hz"])
    bands = len(BAND_CENTRES)

    def tone_bands(amplitudes: np.ndarray) -> np.ndarray:
        power = np.zeros((amplitudes.shape[0], bands))
        idx = _band_of(tones["frequencies_hz"])
        for j, b in enumerate(idx):
            power[:, b] += amplitudes[:, j] ** 2
        return power

    source_bands = {"thickness": tone_bands(tones["thickness"]), "loading": tone_bands(tones["loading"])}
    source_bands.update(broad)
    tonal_total = tone_bands(tones["total"])  # coherent thickness + loading
    broadband_total = sum(broad.values()) if broad else np.zeros((len(obs), bands))
    total = tonal_total + broadband_total
    el = np.radians(spec.elevations_deg)
    weights = np.cos(el) * np.gradient(el) * 2.0 * math.pi * distance**2  # sphere strip areas (axisymmetric)
    rho_c = op.rho_kg_m3 * spec.speed_of_sound_m_s

    def oaspl(power):
        return _db(power.sum(axis=-1) / P_REF**2)

    def dba(power):
        return _db((power * 10.0 ** (aw_band / 10.0)).sum(axis=-1) / P_REF**2)

    def pwl(power, weighted=False):
        p2 = (power * 10.0 ** (aw_band / 10.0)).sum(axis=-1) if weighted else power.sum(axis=-1)
        return float(_db((p2 * weights).sum() / rho_c / W_REF))

    ref = int(np.argmin(np.abs(np.array(spec.elevations_deg) - spec.reference_elevation_deg)))
    summary: dict[str, Any] = {
        "observer_distance_m": distance,
        "reference_elevation_deg": spec.elevations_deg[ref],
        "bpf_hz": op.blade_count * op.rpm / 60.0,
        "oaspl_ref_dB": float(oaspl(total)[ref]),
        "dba_ref_dB": float(dba(total)[ref]),
        "tonal_oaspl_ref_dB": float(oaspl(tonal_total)[ref]),
        "broadband_oaspl_ref_dB": float(oaspl(broadband_total)[ref]),
        "pwl_dB": pwl(total),
        "pwl_A_dB": pwl(total, weighted=True),
        "tonal_pwl_dB": pwl(tonal_total),
        "broadband_pwl_dB": pwl(broadband_total),
        "bpf1_ref_dB": float(_db(tones["total"][ref, 0] ** 2 / P_REF**2)),
    }
    for name, power in source_bands.items():
        summary[f"{name}_pwl_dB"] = pwl(power)
        summary[f"{name}_pwl_A_dB"] = pwl(power, weighted=True)
        summary[f"{name}_ref_dBA"] = float(dba(power)[ref])
    tip = tip_vortex_metrics(result)
    summary.update({k: v for k, v in tip.items() if not k.startswith("_")})
    return {
        "summary": summary,
        "bands_hz": BAND_CENTRES,
        "source_bands": source_bands,
        "tonal_bands": tonal_total,
        "broadband_bands": broadband_total,
        "total_bands": total,
        "tones": tones,
        "aw_tone": aw_tone,
        "observers": obs,
        "elevations_deg": np.array(spec.elevations_deg),
        "oaspl": oaspl(total),
        "dba": dba(total),
        "tonal_oaspl": oaspl(tonal_total),
        "broadband_oaspl": oaspl(broadband_total),
        "gamma": tip["_gamma"],
        "r_R": result.stations.r_m / op.radius_m,
    }
