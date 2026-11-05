"""Small Euler-Bernoulli beam + St-Venant torsion finite-element solver (SI units).

Used for the arm-body load path: clamped at the body centre (symmetric collective mode),
body box segment, socket segment, joint spring, tapered arm, lumped motor/prop at the tip.
"""

from __future__ import annotations

import numpy as np
from scipy.linalg import eigh


def _beam_k(L: float, EI: float) -> np.ndarray:
    return EI / L**3 * np.array(
        [[12, 6 * L, -12, 6 * L], [6 * L, 4 * L * L, -6 * L, 2 * L * L],
         [-12, -6 * L, 12, -6 * L], [6 * L, 2 * L * L, -6 * L, 4 * L * L]]
    )


def _beam_m(L: float, mpl: float) -> np.ndarray:
    return mpl * L / 420.0 * np.array(
        [[156, 22 * L, 54, -13 * L], [22 * L, 4 * L * L, 13 * L, -3 * L * L],
         [54, 13 * L, 156, -22 * L], [-13 * L, -3 * L * L, -22 * L, 4 * L * L]]
    )


def assemble_bending(x: np.ndarray, EI: np.ndarray, mpl: np.ndarray, tip_mass: float, tip_rot: float):
    """x: node coordinates [m]; EI, mpl: per element. Node 0 clamped. Returns reduced K, M."""
    n = len(x)
    K = np.zeros((2 * n, 2 * n))
    M = np.zeros((2 * n, 2 * n))
    for e in range(n - 1):
        L = x[e + 1] - x[e]
        idx = slice(2 * e, 2 * e + 4)
        K[idx, idx] += _beam_k(L, EI[e])
        M[idx, idx] += _beam_m(L, mpl[e])
    M[2 * n - 2, 2 * n - 2] += tip_mass
    M[2 * n - 1, 2 * n - 1] += tip_rot
    return K[2:, 2:], M[2:, 2:]


def assemble_torsion(x: np.ndarray, GJ: np.ndarray, ipl: np.ndarray, tip_inertia: float):
    n = len(x)
    K = np.zeros((n, n))
    M = np.zeros((n, n))
    for e in range(n - 1):
        L = x[e + 1] - x[e]
        K[e:e + 2, e:e + 2] += GJ[e] / L * np.array([[1, -1], [-1, 1]])
        M[e:e + 2, e:e + 2] += ipl[e] * L / 6 * np.array([[2, 1], [1, 2]])
    M[-1, -1] += tip_inertia
    return K[1:, 1:], M[1:, 1:]


def natural_frequencies(K: np.ndarray, M: np.ndarray, n_modes: int = 3):
    w2, vecs = eigh(K, M, subset_by_index=[0, min(n_modes, K.shape[0]) - 1])
    w2 = np.clip(w2, 0, None)
    return np.sqrt(w2) / (2 * np.pi), vecs


def static_solve(K: np.ndarray, F: np.ndarray) -> np.ndarray:
    return np.linalg.solve(K, F)
