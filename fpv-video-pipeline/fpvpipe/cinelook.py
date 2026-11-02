"""Cinematic, natural grade built from the camera's log encoding up, as a scene-referred node tree.

Unlike colorsci (which grades around the camera maker's display LUT), nothing here comes from a creative LUT: the
log curve is decoded to scene-linear light, every creative decision happens on scene light, and one output transform
at the end maps it to the display. That is the node layout colorists use in Resolve (colour-managed / CST sandwich):

    1 IN    CST   GP-Log -> scene linear, GoPro native gamut -> Rec.709 primaries
    2 EXP   gain  exposure in stops (per shot; shot matching)
    3 WB    gain  white balance per channel (per shot; off by default - daylight on red rock is meant to look warm)
    4 CON   curve contrast in log2 around 18% grey (pivoted, so exposure stays put)
    5 SAT   mix   saturation around BT.709 luminance (scene linear, so it is even across the tonal range)
    5b SKY  hue/sat extra saturation and a touch of density for blues only (sky, water): hue-vs-sat
    6 LOOK  split subtle warm highlights / cool shadows, measured in stops from grey
    7 OUT   DRT   Naka-Rushton tone scale on a max-RGB norm with path-to-white, Rec.709 / gamma 2.4 encode

Per-shot nodes are 2 and 3; nodes 1 and 4-7 are one shared look so every shot matches. Grain and vignette are
spatial, so they are ffmpeg filters after the LUT (fastpreview) / Resolve's film grain + a window in finishing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

KR, KG, KB = 0.2126, 0.7152, 0.0722

# GP-Log (HERO11+): Protune-style log with base 400, v = log(399x + 1)/log(400), linear 1.0 = SDR peak white.
GPLOG_BASE = 400.0
# GoPro native -> XYZ (D65), from the Protune transforms project (IMX117 data; GoPro uses one matrix family).
NATIVE_TO_XYZ = np.array([[0.501918, 0.294524, 0.154014],
                          [0.138173, 0.913553, -0.051725],
                          [0.078774, -0.320766, 1.331049]])
XYZ_TO_709 = np.array([[3.2404542, -1.5371385, -0.4985314],
                       [-0.9692660, 1.8760108, 0.0415560],
                       [0.0556434, -0.2040259, 1.0572252]])
NATIVE_TO_709 = XYZ_TO_709 @ NATIVE_TO_XYZ


def gplog_decode(v: np.ndarray) -> np.ndarray:
    return (np.power(GPLOG_BASE, v) - 1) / (GPLOG_BASE - 1)


@dataclass
class Look:
    """The shared part of the tree (nodes 1, 4-7). Defaults are tuned toward the Senja/Mefjord grade (dense blacks,
    bright whites, rich but real colour, deep skies) with a filmic roll-off instead of a hard clip."""
    # "709" treats the log as Rec.709 primaries. The published GoPro native matrix ("native") turns HERO13
    # sandstone pink, so it stays an option only; the missing gamut expansion is made up for in node 5.
    gamut: str = "709"
    contrast: float = 1.25           # node 4: log2 slope around grey
    sat: float = 1.60                # node 5: the decoded log is undersaturated in 709 primaries
    sky_sat: float = 1.4             # node 5b: extra chroma for blue hues
    sky_dens: float = 0.45            # node 5b: darken blues by this many stops (deep skies, polariser feel)
    warm_hi: float = 0.0           # node 6: highlights toward warm (fraction per channel)
    cool_lo: float = 0.01            # node 6: shadows toward cool/teal
    grey_out: float = 0.37           # node 7: display code value of 18% grey (Rec.709 gamma 2.4)
    tone_c: float = 1.80             # node 7: tone-scale contrast (Naka-Rushton exponent)
    toe: float = 0.020               # node 7: flare / toe, keeps blacks dense but not crushed
    white_desat: float = 0.40        # node 7: path to white for bright saturated colours (sun, sky near the sun)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ShotNodes:
    ev: float = 0.0                                          # node 2
    wb: np.ndarray = field(default_factory=lambda: np.ones(3))   # node 3 (R, G, B gains, G = 1)

    def to_dict(self) -> dict:
        return {"ev": round(float(self.ev), 4), "wb": [round(float(x), 4) for x in self.wb]}


def _lum(rgb):
    return rgb @ np.array([KR, KG, KB])


def tone_scale(look: Look):
    """Naka-Rushton y = x^c / (x^c + m), solved so 18% grey lands on look.grey_out after the toe and encode."""
    c = look.tone_c
    g_disp = look.grey_out ** 2.4
    # invert the toe y' = y^2 / (y + toe) for the grey point
    t = look.toe
    y = (g_disp + np.sqrt(g_disp ** 2 + 4 * g_disp * t)) / 2
    m = 0.18 ** c * (1 - y) / y
    def f(x):
        xc = np.power(np.maximum(x, 0), c)
        yy = xc / (xc + m)
        return yy * yy / (yy + t) * (1 + t)          # toe, renormalised so peak stays 1
    return f


def render(log_rgb: np.ndarray, look: Look, shot: ShotNodes, upto: int = 7) -> np.ndarray:
    """(N,3) camera log 0-1 -> Rec.709 display code values 0-1. upto < 7 returns scene linear after that node."""
    lin = gplog_decode(np.clip(log_rgb, 0, 1))                                         # 1 IN
    if look.gamut == "native":
        lin = lin @ NATIVE_TO_709.T
    lin = np.maximum(lin, 0)
    if upto == 1:
        return lin
    lin = lin * 2.0 ** shot.ev                                                          # 2 EXP
    lin = lin * shot.wb                                                                 # 3 WB
    if upto == 3:
        return lin
    L = np.maximum(_lum(lin), 1e-6)[:, None]                                            # 4 CON (on luminance,
    Lc = 0.18 * np.power(L / 0.18, look.contrast)                                       #   ratios kept: no hue shift)
    lin = lin * (Lc / L)
    L = Lc
    lin = np.maximum(L + (lin - L) * look.sat, 0)                                       # 5 SAT
    if look.sky_sat != 1 or look.sky_dens:                                             # 5b SKY
        blue = np.clip((lin[:, 2] - lin[:, :2].max(axis=1)) / np.maximum(lin[:, 2], 1e-6) * 4, 0, 1)[:, None]
        Lb = _lum(lin)[:, None]
        lin = np.maximum(Lb + (lin - Lb) * (1 + blue * (look.sky_sat - 1)), 0) * 2.0 ** (-look.sky_dens * blue)
    stops = np.log2(np.maximum(L, 1e-6) / 0.18)                                         # 6 LOOK
    hi = np.clip(stops / 3, 0, 1)
    lo = np.clip(-stops / 3, 0, 1)
    tint = 1 + hi * look.warm_hi * np.array([1, 0, -1]) + lo * look.cool_lo * np.array([-1, 0.2, 1])
    lin = lin * tint
    if upto == 6:
        return lin
    n = np.maximum(lin.max(axis=1, keepdims=True), 1e-6)                                # 7 OUT (max-RGB norm)
    y = tone_scale(look)(n)
    ratio = lin / n
    w = look.white_desat * np.power(np.clip(y, 0, 1), 3)                                # path to white
    disp = y * (ratio * (1 - w) + w)
    return np.clip(disp, 0, 1) ** (1 / 2.4)


def solve_exposure(log_rgb: np.ndarray, look: Look, target_mid: float = 0.44, max_ev: float = 3.0,
                   strength: float = 1.0) -> float:
    """Stops that put the median display luma on target_mid (bisection: the chain is monotonic in exposure)."""
    sub = log_rgb[np.random.default_rng(0).choice(len(log_rgb), min(len(log_rgb), 8000), replace=False)]
    lo, hi = -max_ev, max_ev
    for _ in range(30):
        mid = (lo + hi) / 2
        if np.median(_lum(render(sub, look, ShotNodes(ev=mid)))) < target_mid:
            lo = mid
        else:
            hi = mid
    return float((lo + hi) / 2 * strength)


def write_cube(path, look: Look, shot: ShotNodes, n: int = 65, title: str = "cinelook"):
    """Bake nodes 1-7 for one shot into a 3D LUT (65 points: the log input needs the density)."""
    g = np.linspace(0, 1, n)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")              # .cube order: red fastest
    rgb = np.stack([r.ravel(), gg.ravel(), b.ravel()], 1)
    out = render(rgb, look, shot)
    with open(path, "w") as f:
        f.write(f'TITLE "{title}"\nLUT_3D_SIZE {n}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
        f.write("\n".join(f"{a:.6f} {c:.6f} {d:.6f}" for a, c, d in out))
        f.write("\n")
    return path
