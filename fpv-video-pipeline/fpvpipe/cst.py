"""Colour space transforms as exact, published maths (what Resolve's CST node does, without Resolve).

    GP-Log          GoPro HERO11+ log: v = log(399 x + 1) / log(400), scene white = 1.0 (base 400; GP-Log2 is 600).
    DaVinci Wide Gamut / DaVinci Intermediate   Blackmagic's published primaries and curve
                    ("DaVinci Wide Gamut Intermediate", 2021): the timeline space of a colour-managed project.
    Rec.709         ITU-R BT.709 primaries, display gamma 2.4 (BT.1886), D65.

Camera gamut: GoPro does not publish HERO13 primaries. The IMX117-derived "Protune native" matrix that circulates
turns sandstone pink on this footage, so the default treats GP-Log as Rec.709 primaries ("rec709"), which rendered
natural colour in every test; "protune" is kept as an option.
"""
from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------- GP-Log
GPLOG_BASE = 400.0


def gplog_to_linear(v):
    return (np.power(GPLOG_BASE, np.clip(v, 0, 1)) - 1) / (GPLOG_BASE - 1)


def linear_to_gplog(x):
    return np.log(np.maximum(x, 0) * (GPLOG_BASE - 1) + 1) / np.log(GPLOG_BASE)


# ---------------------------------------------------------------- DaVinci Intermediate (published constants)
DI_A, DI_B, DI_C, DI_M = 0.0075, 7.0, 0.07329248, 10.44426855
DI_LIN_CUT, DI_LOG_CUT = 0.00262409, 0.02740668


def linear_to_di(x):
    x = np.asarray(x, float)
    return np.where(x > DI_LIN_CUT, (np.log2(np.maximum(x, DI_LIN_CUT) + DI_A) + DI_B) * DI_C, x * DI_M)


def di_to_linear(t):
    t = np.asarray(t, float)
    return np.where(t > DI_LOG_CUT, np.power(2.0, t / DI_C - DI_B) - DI_A, t / DI_M)


# ---------------------------------------------------------------- gamuts (RGB -> XYZ, D65)
DWG_TO_XYZ = np.array([[0.70062239, 0.14877482, 0.10105872],
                       [0.27411851, 0.87363190, -0.14775041],
                       [-0.09896291, -0.13789533, 1.32591599]])
XYZ_TO_DWG = np.array([[1.51667204, -0.28147805, -0.14696363],
                       [-0.46491710, 1.25142378, 0.17488800],
                       [0.06484905, 0.10913934, 0.76141462]])
REC709_TO_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                          [0.2126729, 0.7151522, 0.0721750],
                          [0.0193339, 0.1191920, 0.9503041]])
XYZ_TO_REC709 = np.array([[3.2404542, -1.5371385, -0.4985314],
                          [-0.9692660, 1.8760108, 0.0415560],
                          [0.0556434, -0.2040259, 1.0572252]])
PROTUNE_TO_XYZ = np.array([[0.501918, 0.294524, 0.154014],      # IMX117 fit (protune-transforms); see module doc
                           [0.138173, 0.913553, -0.051725],
                           [0.078774, -0.320766, 1.331049]])
CAMERA_TO_XYZ = {"rec709": REC709_TO_XYZ, "protune": PROTUNE_TO_XYZ}

REC709_TO_DWG = XYZ_TO_DWG @ REC709_TO_XYZ
DWG_TO_REC709 = XYZ_TO_REC709 @ DWG_TO_XYZ


def cst_in(gplog_rgb, camera_gamut: str = "rec709"):
    """Node 1: GP-Log (camera gamut) -> DaVinci Intermediate in DaVinci Wide Gamut."""
    lin = gplog_to_linear(gplog_rgb) @ (XYZ_TO_DWG @ CAMERA_TO_XYZ[camera_gamut]).T
    return linear_to_di(np.maximum(lin, 0))


def cst_out(di_rgb):
    """Node 2: DaVinci Intermediate / DWG -> Rec.709 gamma 2.4, tone mapping 'none' (clip at display white)."""
    lin = np.maximum(di_to_linear(di_rgb) @ DWG_TO_REC709.T, 0)
    return np.power(np.clip(lin, 0, 1), 1 / 2.4)


def eotf_709(disp):
    return np.power(np.clip(disp, 0, 1), 2.4)
