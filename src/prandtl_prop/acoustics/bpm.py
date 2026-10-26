"""Brooks-Pope-Marcolini airfoil self-noise (NASA RP-1218, 1989), vectorised.

Each function returns a 1/3-octave band SPL (dB re 20 uPa) for a blade segment of span L and
chord c in a flow of speed U at angle of attack alpha (deg; BPM's alpha is referenced to zero lift
of the NACA 0012 it was fitted on, so callers pass alpha - alpha0), observed at distance r with
the BPM retarded directivity angles (theta_e, phi_e). The constants follow RP-1218 as transcribed in
the BYU implementation (github.com/byuflowlab/bpm-turbine-acoustics).

Sub-models: turbulent boundary layer trailing-edge noise (pressure side, suction side, angle of
attack; separation/stall above the switch angle), laminar boundary layer vortex shedding,
trailing-edge bluntness vortex shedding, and tip vortex formation noise.
"""

from __future__ import annotations

import numpy as np

CONVECTION = 0.8  # convection Mach factor in the high-frequency directivity


def directivity_high(theta_e, phi_e, M):
    Mc = CONVECTION * M
    return 2.0 * np.sin(theta_e / 2.0) ** 2 * np.sin(phi_e) ** 2 / ((1.0 + M * np.cos(theta_e)) * (1.0 + (M - Mc) * np.cos(theta_e)) ** 2)


def directivity_low(theta_e, phi_e, M):
    return np.sin(theta_e) ** 2 * np.sin(phi_e) ** 2 / (1.0 + M * np.cos(theta_e)) ** 4


def _amin(a):
    return np.where(
        a < 0.204,
        np.sqrt(np.maximum(67.552 - 886.788 * a**2, 0.0)) - 8.219,
        np.where(a <= 0.244, -32.665 * a + 3.981, -142.795 * a**3 + 103.656 * a**2 - 57.757 * a + 6.006),
    )


def _amax(a):
    return np.where(
        a < 0.13,
        np.sqrt(np.maximum(67.552 - 886.788 * a**2, 0.0)) - 8.219,
        np.where(a <= 0.321, -15.901 * a + 1.098, -4.669 * a**3 + 3.491 * a**2 - 16.699 * a + 1.149),
    )


def _bmin(b):
    return np.where(
        b < 0.13,
        np.sqrt(np.maximum(16.888 - 886.788 * b**2, 0.0)) - 4.109,
        np.where(b <= 0.145, -83.607 * b + 8.138, -817.810 * b**3 + 355.210 * b**2 - 135.024 * b + 10.619),
    )


def _bmax(b):
    return np.where(
        b < 0.10,
        np.sqrt(np.maximum(16.888 - 886.788 * b**2, 0.0)) - 4.109,
        np.where(b <= 0.187, -31.330 * b + 1.854, -80.541 * b**3 + 44.174 * b**2 - 39.381 * b + 2.344),
    )


def spectral_a(ratio, Re):
    a = np.abs(np.log10(ratio))
    a0 = np.where(Re < 9.52e4, 0.57, np.where(Re <= 8.57e5, -9.57e-13 * (Re - 8.57e5) ** 2 + 1.13, 1.13))
    ar0 = (-20.0 - _amin(a0)) / (_amax(a0) - _amin(a0))
    return _amin(a) + ar0 * (_amax(a) - _amin(a))


def spectral_b(ratio, Re):
    b = np.abs(np.log10(ratio))
    b0 = np.where(Re < 9.52e4, 0.30, np.where(Re <= 8.57e5, -4.48e-13 * (Re - 8.57e5) ** 2 + 0.56, 0.56))
    br0 = (-20.0 - _bmin(b0)) / (_bmax(b0) - _bmin(b0))
    return _bmin(b) + br0 * (_bmax(b) - _bmin(b))


def boundary_layer(c, Re, alpha, tripped: bool):
    """(delta_p, delta*_p, delta*_s): pressure-side thickness, pressure- and suction-side displacement thicknesses."""

    lr = np.log10(Re)
    if tripped:
        d0 = c * 10.0 ** (1.892 - 0.9045 * lr + 0.0596 * lr**2)
        d0s = np.where(Re <= 0.3e6, c * 0.0601 * Re ** (-0.114), c * 10.0 ** (3.411 - 1.5397 * lr + 0.1059 * lr**2))
        ds = np.where(
            alpha <= 5.0,
            d0s * 10.0 ** (0.0679 * alpha),
            np.where(alpha <= 12.5, d0s * 0.381 * 10.0 ** (0.1516 * alpha), d0s * 14.296 * 10.0 ** (0.0258 * alpha)),
        )
    else:
        d0 = c * 10.0 ** (1.6569 - 0.9045 * lr + 0.0596 * lr**2)
        d0s = c * 10.0 ** (3.0187 - 1.5397 * lr + 0.1059 * lr**2)
        ds = np.where(
            alpha <= 7.5,
            d0s * 10.0 ** (0.0679 * alpha),
            np.where(alpha <= 12.5, d0s * 0.0162 * 10.0 ** (0.3066 * alpha), d0s * 52.42 * 10.0 ** (0.0258 * alpha)),
        )
    dp = d0 * 10.0 ** (-0.04175 * alpha + 0.00106 * alpha**2)
    dps = d0s * 10.0 ** (-0.0432 * alpha + 0.00113 * alpha**2)
    return dp, dps, ds


def _db(x):
    return 10.0 * np.log10(np.maximum(x, 1e-300))


def tbl_te(f, U, L, c, r, theta_e, phi_e, alpha, nu, c0, tripped: bool):
    """Turbulent boundary layer trailing-edge noise, including separation/stall above the switch angle."""

    alpha = np.maximum(alpha, 0.0)  # BPM correlations are for alpha >= 0 (zero-lift referenced)
    M = U / c0
    Re = U * c / nu
    _, dps, dss = boundary_layer(c, Re, alpha, tripped)
    Dh = directivity_high(theta_e, phi_e, M)
    Dl = directivity_low(theta_e, phi_e, M)
    Stp, Sts = f * dps / U, f * dss / U
    St1 = 0.02 * M ** (-0.6)
    St2 = np.where(alpha < 1.33, St1, np.where(alpha <= 12.5, St1 * 10.0 ** (0.0054 * (alpha - 1.33) ** 2), St1 * 4.72))
    gamma = 27.094 * M + 3.31
    gamma0 = 23.43 * M + 4.651
    beta = 72.65 * M + 10.74
    beta0 = -34.19 * M - 13.82
    lr = np.log10(Re)
    K1 = np.where(Re < 2.47e5, -4.31 * lr + 156.3, np.where(Re <= 8.0e5, -9.0 * lr + 181.6, 128.5))
    inside = (alpha >= gamma0 - gamma) & (alpha <= gamma0 + gamma)
    K2 = np.where(
        alpha < gamma0 - gamma,
        K1 - 1000.0,
        np.where(inside, K1 + np.sqrt(np.maximum(beta**2 - (beta / gamma) ** 2 * (alpha - gamma0) ** 2, 0.0)) + beta0, K1 - 12.0),
    )
    Re_dp = U * dps / nu
    dK1 = np.where(Re_dp <= 5000.0, alpha * (1.43 * np.log10(Re_dp) - 5.29), 0.0)
    stall = (alpha > 12.5) | (alpha > gamma0)
    spl_p = _db(dps * M**5 * L * Dh / r**2) + spectral_a(Stp / St1, Re) + (K1 - 3.0) + dK1
    spl_s = _db(dss * M**5 * L * Dh / r**2) + spectral_a(Sts / St1, Re) + (K1 - 3.0)
    spl_a = _db(dss * M**5 * L * Dh / r**2) + spectral_b(Sts / St2, Re) + K2
    spl_stall = _db(dss * M**5 * L * Dl / r**2) + spectral_a(Sts / St2, 3.0 * Re) + K2
    attached = _db(10.0 ** (spl_p / 10.0) + 10.0 ** (spl_s / 10.0) + 10.0 ** (spl_a / 10.0))
    return np.where(stall, spl_stall, attached)


def _g1(e):
    le = np.log10(e)
    return np.where(
        e <= 0.5974,
        39.8 * le - 11.12,
        np.where(
            e <= 0.8545,
            98.409 * le + 2.0,
            np.where(
                e <= 1.17,
                np.sqrt(np.maximum(2.484 - 506.25 * le**2, 0.0)) - 5.076,
                np.where(e <= 1.674, -98.409 * le + 2.0, -39.8 * le - 11.12),
            ),
        ),
    )


def _g2(d):
    ld = np.log10(d)
    return np.where(
        d <= 0.3237,
        77.852 * ld + 15.328,
        np.where(
            d <= 0.5689,
            65.188 * ld + 9.125,
            np.where(d <= 1.7579, -114.052 * ld**2, np.where(d <= 3.0889, -65.188 * ld + 9.125, -77.852 * ld + 15.328)),
        ),
    )


def lbl_vs(f, U, L, c, r, theta_e, phi_e, alpha, nu, c0, tripped: bool):
    """Laminar boundary layer vortex shedding (meaningful for an untripped boundary layer)."""

    alpha = np.maximum(alpha, 0.0)
    M = U / c0
    Re = U * c / nu
    dp, _, _ = boundary_layer(c, Re, alpha, tripped)
    St = f * dp / U
    Dh = directivity_high(theta_e, phi_e, M)
    St1 = np.where(Re <= 1.3e5, 0.18, np.where(Re <= 4.0e5, 0.001756 * Re**0.3931, 0.28))
    St_peak = St1 * 10.0 ** (-0.04 * alpha)
    Re0 = np.where(alpha <= 3.0, 10.0 ** (0.215 * alpha + 4.978), 10.0 ** (0.12 * alpha + 5.263))
    return _db(dp * M**5 * L * Dh / r**2) + _g1(St / St_peak) + _g2(Re / Re0) + (171.04 - 3.03 * alpha)


def _g5_core(hdav, eta):
    mu = np.where(
        hdav < 0.25, 0.1221, np.where(hdav < 0.62, -0.2175 * hdav + 0.1755, np.where(hdav < 1.15, -0.0308 * hdav + 0.0596, 0.0242))
    )
    m = np.where(
        hdav <= 0.02,
        0.0,
        np.where(
            hdav <= 0.5,
            68.724 * hdav - 1.35,
            np.where(
                hdav <= 0.62,
                308.475 * hdav - 121.23,
                np.where(hdav <= 1.15, 224.811 * hdav - 69.35, np.where(hdav <= 1.2, 1583.28 * hdav - 1631.59, 268.344)),
            ),
        ),
    )
    eta0 = -np.sqrt(m**2 * mu**4 / (6.25 + m**2 * mu**2))
    k = 2.5 * np.sqrt(np.maximum(1.0 - (eta0 / mu) ** 2, 0.0)) - 2.5 - m * eta0
    return np.where(
        eta < eta0,
        m * eta + k,
        np.where(
            eta < 0.0,
            2.5 * np.sqrt(np.maximum(1.0 - (eta / mu) ** 2, 0.0)) - 2.5,
            np.where(eta < 0.03616, np.sqrt(np.maximum(1.5625 - 1194.99 * eta**2, 0.0)) - 1.25, -155.543 * eta + 4.375),
        ),
    )


def te_bluntness(f, U, L, c, h, r, theta_e, phi_e, alpha, nu, c0, psi_deg, tripped: bool):
    """Trailing-edge bluntness vortex shedding: TE thickness h, TE solid angle psi (deg)."""

    alpha = np.maximum(alpha, 0.0)
    M = U / c0
    Re = U * c / nu
    _, dps, dss = boundary_layer(c, Re, alpha, tripped)
    Dh = directivity_high(theta_e, phi_e, M)
    hdav = h / ((dps + dss) / 2.0)
    St = f * h / U
    St_peak = np.where(
        hdav >= 0.2, (0.212 - 0.0045 * psi_deg) / (1.0 + 0.235 / hdav - 0.0132 / hdav**2), 0.1 * hdav + 0.095 - 0.00243 * psi_deg
    )
    G4 = np.where(hdav <= 5.0, 17.5 * np.log10(hdav) + 157.5 - 1.114 * psi_deg, 169.7 - 1.114 * psi_deg)
    eta = np.log10(St / St_peak)
    hdav_prime = 6.724 * hdav**2 - 4.019 * hdav + 1.107
    G5 = _g5_core(hdav_prime, eta) + 0.0714 * psi_deg * (_g5_core(hdav, eta) - _g5_core(hdav_prime, eta))
    return _db(h * M**5.5 * L * Dh / r**2) + G4 + G5


def tip_vortex(f, U, c, r, theta_e, phi_e, alpha_tip, c0, rounded: bool = True):
    """Tip vortex formation noise; alpha_tip is the effective tip angle of attack (deg)."""

    alpha_tip = np.maximum(alpha_tip, 1e-6)
    M = U / c0
    Mmax = M * (1.0 + 0.036 * alpha_tip)
    Dh = directivity_high(theta_e, phi_e, M)
    if rounded:
        ell = 0.008 * c * alpha_tip
    else:
        ell = np.where(alpha_tip <= 2.0, c * (0.0230 + 0.0169 * alpha_tip), c * (0.0378 + 0.0095 * alpha_tip))
    St = f * ell / (U * (1.0 + 0.036 * alpha_tip))
    return _db(M**2 * Mmax**3 * ell**2 * Dh / r**2) - 30.5 * (np.log10(St) + 0.3) ** 2 + 126.0
