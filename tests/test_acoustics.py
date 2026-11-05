"""Aeroacoustics: FW-H against the closed-form Gutin far field, BPM against the reference implementation."""

from __future__ import annotations

import math

import numpy as np
import pytest

from prandtl_prop.acoustics import bpm, fwh
from prandtl_prop.acoustics.rotor import a_weighting_db, smooth_phi

# (f, U, L, c, r, theta_e, phi_e, alpha, h, psi, alpha_tip, tripped, flat_tip) and the reference SPLs
# (TBL-TE, tip, LBL-VS, bluntness) from the BYU Fortran (github.com/byuflowlab/bpm-turbine-acoustics,
# tip noise with AR = 30, i.e. no aspect-ratio correction), compiled with gfortran.
CASES = [
    (
        15860.201606034852,
        105.59874259669171,
        0.004215129632362967,
        0.03614379648846658,
        1.222934039488071,
        2.818798615683558,
        1.5933851809792723,
        1.7379841870615547,
        0.0006611407799837504,
        16.65024671513447,
        7.549537962109965,
        1.0,
        1.0,
    ),
    (
        10623.343923930695,
        67.83961336817967,
        0.002195925419946906,
        0.038654107228370496,
        3.530569492188225,
        2.6815392730113206,
        2.432428389803147,
        13.282804000649202,
        0.0006943198424684711,
        8.683284008647664,
        9.337945487306925,
        1.0,
        0.0,
    ),
    (
        7266.320842802015,
        22.83974032134603,
        0.0033160581317928104,
        0.0509253662964649,
        2.188161484234888,
        1.0551930497038036,
        2.0666099952499013,
        2.6785781726155786,
        0.0004566305459952878,
        5.087368926619714,
        3.518689196626167,
        1.0,
        0.0,
    ),
    (
        19496.968692271264,
        45.40193303829089,
        0.002585520579127728,
        0.021357108295809342,
        1.6601140354853146,
        2.2604470378689436,
        2.0843126408443196,
        1.9300981847575416,
        0.0004386146512852848,
        11.313820919261943,
        8.147318833162547,
        1.0,
        0.0,
    ),
    (
        1678.6016657485811,
        119.96057545111779,
        0.004920058108068472,
        0.04577662773624326,
        2.517783392050179,
        1.1927888381630178,
        0.49628359281808115,
        3.0486231613109487,
        0.00035542584004971797,
        9.712008434034534,
        4.100050376429284,
        1.0,
        1.0,
    ),
    (
        16842.01060998849,
        111.8330168654665,
        0.00807414800431664,
        0.04913896530498587,
        3.186444787356904,
        2.6777690943614645,
        2.0620014197071184,
        7.5053464610530645,
        0.00016937542765048489,
        12.326738406282857,
        2.947556446413845,
        0.0,
        0.0,
    ),
    (
        1036.976223199263,
        113.13566157461987,
        0.008150174079273332,
        0.02334033502588701,
        2.8653328542301435,
        0.6024296631009238,
        2.8054129087452866,
        6.0245433583277626,
        0.0003657108300964262,
        17.704975559505947,
        1.931293823928018,
        1.0,
        1.0,
    ),
    (
        154.2923275877936,
        64.06147200157281,
        0.008729823941539094,
        0.020058793680031196,
        2.255335483729378,
        2.8316705947821807,
        1.888206007920788,
        10.404342520543729,
        0.0005693726099191758,
        9.634522986133867,
        5.04889884210293,
        1.0,
        1.0,
    ),
]
REFERENCE = [
    (49.481882745676, 58.641817367202, 83.483390313905, 49.358592320088),
    (16.954268625084, 32.165345480391, -108.76869826496, 5.2441919757213),
    (-3.0427990200363, 0.52576053388863, -36.990391464547, 9.6528288382569),
    (18.356292412541, 25.341280396733, -0.71088874754648, 16.429746358532),
    (29.058321636914, 3.8820752177358, 22.915754014682, -33.568143337131),
    (46.681815652699, 30.205949274476, 31.312393397191, 30.424954537987),
    (14.191509631797, -66.15596816726, -49.558042833577, -12.613464104464),
    (-1.2754159030563, -108.65027320041, -133.60531564722, -4.0295558735808),
]


@pytest.mark.parametrize("case,ref", list(zip(CASES, REFERENCE, strict=True)))
def test_bpm_matches_the_reference_implementation(case, ref) -> None:
    f, U, L, c, r, th, ph, a, h, psi, at, trip, flat = case
    nu, c0 = 1.78e-5 / 1.225, 343.0
    assert bpm.tbl_te(f, U, L, c, r, th, ph, a, nu, c0, bool(trip)) == pytest.approx(ref[0], abs=1e-6)
    assert bpm.tip_vortex(f, U, c, r, th, ph, at, c0, rounded=not bool(flat)) == pytest.approx(ref[1], abs=1e-4)
    assert bpm.lbl_vs(f, U, L, c, r, th, ph, a, nu, c0, bool(trip)) == pytest.approx(ref[2], abs=1e-6)
    assert bpm.te_bluntness(f, U, L, c, h, r, th, ph, a, nu, c0, psi, bool(trip)) == pytest.approx(ref[3], abs=1e-6)


@pytest.mark.parametrize("theta_deg", [30.0, 90.0, 120.0, 150.0])
def test_fwh_compact_loading_matches_gutin(theta_deg) -> None:
    B, R = 3, 0.127
    re, omega, T, Q = 0.8 * R, 7500 * math.pi / 30, 3.5, 0.045
    source = fwh.RotorSources(compact_positions_m=np.array([[re, 0.0, 0.0]]), compact_forces_N=np.array([[0.0, Q / (B * re), -T / B]]))
    t, S = math.radians(theta_deg), 50.0
    obs = np.array([[S * math.sin(t), 0.0, S * math.cos(t)]])
    amp = fwh.harmonics(fwh.pressure_history(source, obs, omega_rad_s=omega, blade_count=B, samples_per_rev=1440)["loading"], B, 2)[0]
    for m in (1, 2):
        assert amp[m - 1] == pytest.approx(fwh.gutin_rms(m, B, omega, T, Q, re, S, t), rel=2e-3)


def test_a_weighting_anchor_values() -> None:
    assert a_weighting_db(np.array([1000.0]))[0] == pytest.approx(0.0, abs=0.05)
    assert a_weighting_db(np.array([100.0]))[0] == pytest.approx(-19.1, abs=0.1)
    assert a_weighting_db(np.array([10000.0]))[0] == pytest.approx(-2.5, abs=0.1)


def test_phi_smoothing_removes_the_in_plane_null_only() -> None:
    assert math.degrees(smooth_phi(np.array([0.0]))[0]) == pytest.approx(2.5)
    assert math.degrees(smooth_phi(np.array([math.pi]))[0]) == pytest.approx(177.5)
    assert smooth_phi(np.array([1.0]))[0] == pytest.approx(1.0)


def test_fwh_unsteady_history_reduces_to_steady_and_matches_on_axis_dipole() -> None:
    """A constant load history equals the steady source. A B/rev axial load seen from the axis
    radiates the exact compact-dipole tone: B F cos(g)/(4 pi r) sqrt((B Omega/c0)^2 + 1/r^2) (r Mdot_r = c0 M^2, M_r = 0 there)."""

    B, re, omega, c0, S, F = 3, 0.1, 7000 * math.pi / 30, 343.0, 5.0, 0.2
    pos = np.array([[re, 0.0, 0.0]])
    steady_force = np.array([[0.0, 0.01, -0.9]])
    obs = np.array([[3.0, 1.0, -2.0], [0.0, 0.0, -S]])
    kw = dict(omega_rad_s=omega, blade_count=B, c0=c0, samples_per_rev=720)
    p0 = fwh.pressure_history(fwh.RotorSources(compact_positions_m=pos, compact_forces_N=steady_force), obs, **kw)["loading"]
    history = np.repeat(steady_force[None], 36, axis=0)
    p1 = fwh.pressure_history(fwh.RotorSources(compact_positions_m=pos, compact_force_history_N=history), obs, **kw)["loading"]
    assert np.allclose(p1, p0, atol=1e-9 * np.abs(p0).max())

    psi = np.arange(36) * 2 * math.pi / 36
    history = np.zeros((36, 1, 3))
    history[:, 0, 2] = -F * np.cos(B * psi)
    p = fwh.pressure_history(fwh.RotorSources(compact_positions_m=pos, compact_force_history_N=history), obs[1:], **kw)["loading"]
    r = math.hypot(S, re)
    exact = B * F * (S / r) / (4 * math.pi * r) * math.sqrt((B * omega / c0) ** 2 + 1 / r**2) / math.sqrt(2)
    assert fwh.harmonics(p, B, 1)[0, 0] == pytest.approx(exact, rel=1e-3)
