"""Five-node, scope-solved grade: the Senja/Mefjord recipe as exact transforms, each node solved from the waveform and
vectorscope instead of by eye.

    1 BAL       In camera log, before the transform. Exposure (waveform median) + white balance (vectorscope:
                neutral cluster to centre, faded out for coloured surfaces like red rock) as a CDL offset, plus a
                shadow-only lift: +N stops below the shot's 25th percentile, easing to none by its median. All three
                are offsets in log, i.e. exact gains on scene light (the lift just varies with brightness).
    2 CST       Camera log -> Rec.709. GP-Log: the exact base-400 decode to scene light and a filmic display
                transform (DRT below). GoPro's GP-Log2 LUT on re-encoded GP-Log was tried and turned sandstone pink
                and skies cyan on the vectorscope. GP-Log2 footage: GoPro's GP-Log2 "Rolloff" LUT, as in the Senja
                grade (CST below).
    3 CONTRAST  One curve on luma, RGB scaled by the luma ratio (Resolve's "luma only" contrast): hue and relative
                saturation untouched, so node 4 alone owns colour. Solved on the waveform: p0.5 -> black, p99.5 ->
                white, a gamma that keeps the median where node 1 put it, and an S-curve around it.
    4 SAT       Saturation around BT.709 luma, plus hue-vs-sat for blues/cyans (sky, water), solved on the
                vectorscope: mean chroma to an absolute target (Senja's), capped so bright and very saturated areas stay under limits
                that survive phone compression.
    5 FINISH    Final vectorscope trim: whatever cast nodes 3-4 left on the neutrals is removed with a gain (highlights)
                and lift (shadows) pair, and a soft clip keeps whites under the ceiling.

Every node can be baked to its own .cube (Resolve: one LUT per node, applied exactly) and the whole chain to one
.cube for ffmpeg previews. All measurement happens in Rec.709 display space, i.e. what the scopes show.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np

from . import colorsci as cs

KR, KB = 0.2126, 0.0722
KG = 1 - KR - KB

# Waveform / vectorscope targets, from the Senja export (dense true blacks, whites just under clip) with the median
# left to the scene (a desert is brighter than a fjord) and saturation as a ratio over the camera LUT.
TARGETS = {
    "black": 0.03,        # p1 luma: dense but open (desert shadows are sky-lit; Senja's 0.0 crushed them)
    "floor_p20": 0.08,    # shadow floor: luma p20 may not go below this (keeps texture in big shadow areas)
    "shadow_lift": 0.18,  # node 1: stops added to the shadows (full below lift_pct, fading out above it)
    "lift_pct": 25.0,     # node 1: percentile of the shot's log values that sets the lift threshold
    "white": 0.94,        # p99.5 luma (Senja p99 = 0.949)
    "mid": 0.38,          # median luma after node 1, metered without the brightest 10% (sky, sun)
    "meter": "mid",       # node 1 metering: "mid" (median -> mid) or "white" (expose for the highlights: luma p95 ->
                          #   hi_level (sunlit rock high), mids fall where the light puts them - keeps real brightness ratios)
    "hi_pct": 99.0, "hi_level": 0.86,   # node 1 "white" metering: this luma percentile -> this level after node 2
    "max_stretch": 1.3,   # node 3: largest white-point gain allowed (bigger means node 1 exposure is wrong)       # node 1 "white" metering target (p95, below the transform's shoulder so it is reachable)
    "s_curve": 0.18,      # node 3 S strength (0 = straight line between black and white points)
    "sat_mean": 0.105,    # node 4 vectorscope mean chroma (Senja export 0.082 was too dead on desert)
    "vibrance": 0.6,      # node 4: extra gain for muted colours, fading to none at chroma 0.15 (natural saturation)
    "shadow_sat": 0.8,    # node 4: chroma x this below luma 0.12 (deep shadows stay clean, not brown-red)
    "hi_warm": 1.0,       # node 4: extra chroma for warm hues (red..yellow) above luma 0.6 (sunlit sandstone)
    "white_desat": 0.35,
    "sky_lift": 0.0,      # node 4: sky qualifier (blue hues, luma > 0.25) brightened by this many stops  # node 2: how fast bright colours fade to white in the display transform
    "sat_p95_max": 0.26,  # vectorscope: 95th percentile chroma ceiling
    "hi_sat_max": 0.16,   # chroma ceiling for the brightest 5% (skies near the sun band after compression)
    "blue_gain": 1.30,    # node 4 hue-vs-sat for true blues (sky), not cyan
    "sky_hue": 6.0,       # node 4 hue-vs-hue: bright cyans turned this many degrees toward blue
    "rock_hue": 4.0,      # node 4 hue-vs-hue: reds turned toward orange (sandstone reads salmon otherwise)
    "yellow_hue": 0.0,    # node 4 hue-vs-hue: yellows turned toward orange (per-shot; backlit rock reads khaki)
    "wb_r": 0.0, "wb_b": 0.0, "wb_g": 0.0,   # node 1 manual white-balance / tint trim in log (per-shot)
    "sat_min": 0.6, "sat_max": 1.8,   # node 4 saturation range
    "ceiling": 0.96,      # node 5 soft clip
}


def targets_for(over: dict | None) -> dict:
    """TARGETS, then the shot's own overrides (rock_chroma / rock_hue_target are off unless a shot sets them)."""
    return {**TARGETS, "rock_chroma": 0.0, "rock_hue_target": 0.0, **(over or {})}


def _ycc(rgb):
    y = rgb @ np.array([KR, KG, KB])
    return y, (rgb[..., 2] - y) / 1.8556, (rgb[..., 0] - y) / 1.5748


def _rgb(y, cb, cr):
    r = y + 1.5748 * cr
    b = y + 1.8556 * cb
    g = (y - KR * r - KB * b) / KG
    return np.stack([r, g, b], -1)


# ------------------------------------------------------------------ node 2: camera -> GP-Log2 -> Rec.709

def gplog_to_gplog2_curve(gplog_lut: Path, gplog2_lut: Path, n: int = 1024) -> tuple[np.ndarray, np.ndarray]:
    """GP-Log code value -> GP-Log2 code value that GoPro's LUTs render to the same display grey.
    GoPro's GP-Log (EV0) and GP-Log2 (EV+1.8) LUTs agree through the mids; matching them on the neutral axis gives
    the re-encode. Outside the range where both are informative the curve continues with slope 1 (log stays log)."""
    v = np.linspace(0, 1, 4001)
    grey = np.stack([v, v, v], 1)
    a = cs.apply_lut(cs.load_cube(gplog_lut), grey) @ np.array([KR, KG, KB])
    b = cs.apply_lut(cs.load_cube(gplog2_lut), grey) @ np.array([KR, KG, KB])
    a, b = np.maximum.accumulate(a), np.maximum.accumulate(b)     # monotone for interpolation
    x = np.linspace(0, 1, n)
    d = np.interp(x, v, a)
    y = np.interp(d, b + np.arange(len(b)) * 1e-9, v)
    ok = (d > 0.03) & (d < 0.80)                                    # both LUTs informative, below either shoulder
    i0, i1 = np.argmax(ok), len(ok) - 1 - np.argmax(ok[::-1])
    y[:i0] = y[i0] - (x[i0] - x[:i0])
    y[i1 + 1:] = y[i1] + (x[i1 + 1:] - x[i1])
    return x, np.clip(y, 0, 1)


@dataclass
class CST:
    lut: cs.CubeLUT
    curve: tuple[np.ndarray, np.ndarray] | None = None   # None: footage is already GP-Log2

    def apply(self, v):
        if self.curve is not None:
            v = np.interp(np.clip(v, 0, 1), *self.curve)
        return np.clip(cs.apply_lut(self.lut, np.clip(v, 0, 1)), 0, 1)


@dataclass
class DRT:
    """Node 2 alternative for GP-Log: the exact GP-Log decode (base 400) to scene light, then a filmic display
    transform (Naka-Rushton tone scale on max-RGB with path to white, Rec.709 gamma 2.4) - no camera-maker look."""
    look: object = None
    ceiling: float = 1.0

    white_desat: float = TARGETS["white_desat"]

    def __post_init__(self):
        from . import cinelook
        self._cl = cinelook
        self.look = self.look or cinelook.Look(contrast=1.0, sat=1.0, sky_sat=1.0, sky_dens=0.0, warm_hi=0.0,
                                               cool_lo=0.0, tone_c=1.35, toe=0.03, grey_out=0.42,
                                               white_desat=self.white_desat)

    def apply(self, v):
        return self._cl.render(np.clip(v, 0, 1), self.look, self._cl.ShotNodes(ev=1.6))


# ------------------------------------------------------------------ nodes 3-5

@dataclass
class Contrast:
    black: float = 0.0      # input luma that becomes target black
    white: float = 1.0      # input luma that becomes target white
    pivot: float = 0.4      # S-curve pivot = output median
    gamma: float = 1.0      # after the stretch, puts the median back on the pivot
    s: float = 0.0
    out_black: float = TARGETS["black"]
    out_white: float = TARGETS["white"]

    def curve(self, x):
        t = np.clip((x - self.black) / max(self.white - self.black, 1e-4), -0.2, 1.3)
        t = np.sign(t) * np.abs(t) ** self.gamma
        lin = self.out_black + t * (self.out_white - self.out_black)
        p = np.clip(self.pivot, 0.05, 0.95)
        u = np.clip(lin, 0, 1)
        # smooth S around the pivot: a power curve below and above it, continuous with matching value at p
        k = 1 + self.s
        lo = p * (u / p) ** k
        hi = 1 - (1 - p) * ((1 - u) / (1 - p)) ** k
        s_part = np.where(u < p, lo, hi)
        return np.where((lin >= 0) & (lin <= 1), s_part, lin)

    def apply(self, rgb):
        y = rgb @ np.array([KR, KG, KB])
        ratio = self.curve(y) / np.maximum(y, 1e-4)
        return np.clip(rgb * ratio[:, None], 0, 1)


@dataclass
class Sat:
    sat: float = 1.0
    blue: float = 1.0
    hi_limit: float = 1.0    # chroma multiplier fade for the brightest areas (1 = none)
    sky_hue: float = 0.0     # degrees: bright cyans toward blue
    rock_hue: float = 0.0    # degrees: reds toward orange
    yellow_hue: float = 0.0  # degrees: yellows toward orange
    vibrance: float = 0.0    # extra gain for muted colours
    shadow_sat: float = 1.0  # chroma multiplier in deep shadows
    hi_warm: float = 1.0     # chroma multiplier for warm highlights
    sky_lift: float = 0.0    # stops of brightness for the sky qualifier
    rock_center: float = 115.0   # hue the rock rotation is centred on (solver sets it to the shot's rock hue)

    def apply(self, rgb):
        y, cb, cr = _ycc(rgb)
        hue = np.degrees(np.arctan2(cr, cb))                     # angle in (cb, cr): blue ~ -10, cyan ~ -60,
        if self.sky_hue or self.rock_hue or self.yellow_hue:     #   red ~ 110, orange ~ 140, yellow ~ 170
            w_sky = np.clip(1 - np.abs(hue + 45) / 30, 0, 1) * np.clip((y - 0.45) / 0.15, 0, 1)
            w_rock = np.clip(1 - np.abs(hue - self.rock_center) / 30, 0, 1)
            w_yel = np.clip(1 - np.abs(hue - 175) / 30, 0, 1)
            rot = np.radians(w_sky * self.sky_hue + w_rock * self.rock_hue - w_yel * self.yellow_hue)
            c, s_ = np.cos(rot), np.sin(rot)
            cb, cr = cb * c - cr * s_, cb * s_ + cr * c
            hue = np.degrees(np.arctan2(cr, cb))
        dist = np.abs((hue + 12 + 180) % 360 - 180)
        w_blue = np.clip(1 - dist / 35, 0, 1) ** 1.5 * np.clip(np.hypot(cb, cr) / 0.03, 0, 1)
        g = self.sat * (1 + w_blue * (self.blue - 1))
        g = g * (1 + self.vibrance * np.clip(1 - np.hypot(cb, cr) / 0.15, 0, 1))      # vibrance
        g = g * (1 - (1 - self.shadow_sat) * np.clip((0.12 - y) / 0.06, 0, 1))         # shadow guard
        w_warm = np.clip(1 - np.abs(hue - 140) / 45, 0, 1) * np.clip((y - 0.6) / 0.1, 0, 1)
        g = g * (1 + w_warm * (self.hi_warm - 1))                                      # warm highlights
        hi = np.clip((y - 0.75) / 0.2, 0, 1)
        g = g * (1 - hi * (1 - self.hi_limit))
        out = _rgb(y, cb * g, cr * g)
        if self.sky_lift:                                      # sky qualifier: blue/cyan hues, mid-bright, soft
            w = np.clip(1 - np.abs(hue + 30) / 50, 0, 1) * np.clip((y - 0.25) / 0.1, 0, 1)
            w = w * np.clip(np.hypot(cb, cr) / 0.02, 0, 1)
            out = out * (2 ** (self.sky_lift / 2.4)) ** w[:, None]   # stops of scene light in a gamma-2.4 display
        return np.clip(out, 0, 1)


@dataclass
class Finish:
    gain: np.ndarray = field(default_factory=lambda: np.ones(3))
    lift: np.ndarray = field(default_factory=lambda: np.zeros(3))
    ceiling: float = TARGETS["ceiling"]

    def apply(self, rgb):
        out = self.lift + rgb * (self.gain - self.lift)
        c, knee = self.ceiling, self.ceiling - 0.08
        over = np.maximum(out - knee, 0)
        out = np.where(out > knee, knee + (c - knee) * (1 - np.exp(-over / (c - knee))), out)
        return np.clip(out, 0, 1)


def _lin(v):     # GP-Log (base 400) <-> scene light, only used to shape the shadow lift
    return (400.0 ** np.clip(v, 0, 1) - 1) / 399


def _log(x):
    return np.log(np.maximum(x, 0) * 399 + 1) / np.log(400)


@dataclass
class Bal:
    cdl: cs.CDL
    lift_stops: float = 0.0
    lo: float = 0.0          # log value (mean of R,G,B) of the shadows to lift (shot p25)
    hi: float = 1.0          # kept for reference: the shot median

    def apply(self, v):
        """Offset in log, then the shadow lift as a gain on scene light x' = x (1 + G (t / (x + t))^2): full gain
        2^stops for x << t, a quarter of it at t, nearly none by 3t. With u = x/t the slope is
        1 + G (1 - u) / (1 + u)^3 >= 1 - G/27, so tones can never reverse for lifts up to log2(28) = 4.8 stops.
        The gain is computed on the mean so hue and saturation are kept."""
        v = self.cdl.apply(v)
        if self.lift_stops:
            x = _lin(v)
            n = x.mean(axis=1, keepdims=True)
            t = float(_lin(np.array(self.lo)))
            gain = 1 + (2.0 ** self.lift_stops - 1) * (t / (n + t)) ** 2   # monotonic up to ~4.7 stops
            v = _log(x * gain)
        return v


@dataclass
class FiveNode:
    bal: Bal
    cst: CST
    contrast: Contrast
    sat: Sat
    finish: Finish
    notes: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def apply(self, log_rgb, upto: int = 5):
        x = np.clip(self.bal.apply(log_rgb), 0, 1)
        for k, node in enumerate((self.cst, self.contrast, self.sat, self.finish), 2):
            if k > upto:
                break
            x = node.apply(x)
        return x

    def node_fns(self):
        """Each node as its own transform (input domain 0-1), for one LUT per Resolve node."""
        return {1: lambda v: np.clip(self.bal.apply(v), 0, 1), 2: self.cst.apply, 3: self.contrast.apply,
                4: self.sat.apply, 5: self.finish.apply}

    def to_dict(self):
        return {"1_bal_offset": self.bal.cdl.offset.round(4).tolist(),
                "1_shadow_lift": {"stops": self.bal.lift_stops, "lo": round(self.bal.lo, 4), "hi": round(self.bal.hi, 4)},
                "3_contrast": {k: round(float(v), 4) for k, v in asdict(self.contrast).items()},
                "4_sat": {k: round(float(v), 4) for k, v in asdict(self.sat).items()},
                "5_finish": {"gain": self.finish.gain.round(4).tolist(), "lift": self.finish.lift.round(4).tolist(),
                             "ceiling": self.finish.ceiling},
                "notes": self.notes, "stats": self.stats}


# ------------------------------------------------------------------ scopes

def scopes(rgb) -> dict:
    y, cb, cr = _ycc(rgb)
    c = np.hypot(cb, cr)
    q = np.percentile(y, [0.5, 50, 99.5])
    top = y > np.percentile(y, 95)
    return {"black": float(q[0]), "mid": float(q[1]), "white": float(q[2]), "sat_mean": float(c.mean()),
            "sat_p95": float(np.percentile(c, 95)), "hi_sat": float(np.percentile(c[top], 95))}


def neutral_cast(rgb, mask) -> np.ndarray:
    """Mean (cb, cr) of the pixels the neutral finder picked, split into shadows / highlights."""
    y, cb, cr = _ycc(rgb[mask])
    lo, hi = y < np.median(y), y >= np.median(y)
    return np.array([[cb[lo].mean(), cr[lo].mean()], [cb[hi].mean(), cr[hi].mean()]]) if mask.sum() > 50 else np.zeros((2, 2))


# ------------------------------------------------------------------ solver

def solve(log_rgb: np.ndarray, cst: CST, t: dict | None = None, max_ev: float = 0.3) -> FiveNode:
    """log_rgb: (N,3) pixels from the shot's used range, camera log 0-1 (full range)."""
    t = targets_for(t)
    rng = np.random.default_rng(0)
    px = log_rgb[rng.choice(len(log_rgb), min(len(log_rgb), 12000), replace=False)]
    notes = []

    # node 1: white balance from the neutral cluster (in log: an exact per-channel gain), guarded for red rock
    nz = cs.find_neutrals(px)
    wb = -nz["d"]
    if nz["confidence"] < 0.15 or nz["spread"] < 0.05:
        wb = wb * 0.5
        notes.append(f"1 BAL: weak neutrals (confidence {nz['confidence']:.2f}), white balance half-applied")
    cast = float(np.hypot(*nz["d"]))
    if cast > cs.WB_FULL:
        wb = wb * float(np.clip((cs.WB_NONE - cast) / (cs.WB_NONE - cs.WB_FULL), 0, 1))
        notes.append(f"1 BAL: 'neutral' cluster too coloured ({cast:.3f}) to be a light cast; white balance limited")
    wb = np.clip(wb, -0.1, 0.1) + np.array([t["wb_r"], t["wb_b"]])

    m_log = px.mean(axis=1)
    lo_l, hi_l = np.percentile(m_log, [t["lift_pct"], 50])

    def bal_of(e):
        return Bal(cs.CDL(offset=np.array([e + wb[0], e + t["wb_g"], e + wb[1]])), t["shadow_lift"], lo_l + e, hi_l + e)
    lo, hi = -max_ev, max_ev                       # node 1 exposure: bisection on the waveform (monotonic in e)
    for _ in range(30):
        m = (lo + hi) / 2
        # meter WITHOUT the shadow lift: the lift opens shadows, it must not move exposure (it used to raise the
        # median, so node 1 darkened the whole shot and node 3 had to stretch it back - a gray, flat result)
        yy = _ycc(cst.apply(np.clip(replace(bal_of(m), lift_stops=0.0).apply(px), 0, 1)))[0]
        if t["meter"] == "white":
            under = np.percentile(yy, t["hi_pct"]) < t["hi_level"]
        else:
            under = np.median(yy[yy < np.percentile(yy, 90)]) < t["mid"]
        if under:
            lo = m
        else:
            hi = m
    e = (lo + hi) / 2
    if abs(e) > max_ev * 0.98:
        notes.append(f"1 BAL: exposure hit its limit ({e:+.3f} log), check by eye")
    bal = bal_of(e)
    a2 = cst.apply(np.clip(bal.apply(px), 0, 1))

    # node 3: waveform: black/white points to targets, S around the median
    y2 = _ycc(a2)[0]
    b_in, w_in, mid = np.percentile(y2, [1, 99.5, 50])
    if bal.lift_stops:   # black point from the un-lifted image, or the stretch would pull the lifted shadows back down
        b_in = float(np.percentile(_ycc(cst.apply(np.clip(Bal(bal.cdl).apply(px), 0, 1)))[0], 1))
    con = Contrast(black=float(b_in), white=float(max(w_in, b_in + 0.2)), s=t["s_curve"], pivot=float(mid),
                   out_black=t["black"], out_white=t["white"])
    tm = (mid - con.black) / (con.white - con.black)                      # median's position in the stretch
    tp = (mid - con.out_black) / (con.out_white - con.out_black)          # where it has to land
    # node 3 may shape contrast but not redo exposure: limit the white stretch and the median gamma
    span_in, span_out = con.white - con.black, con.out_white - con.out_black
    if span_out / span_in > t["max_stretch"]:
        con.out_white = float(con.out_black + span_in * t["max_stretch"])
        notes.append(f"3 CONTRAST: white stretch limited to x{t['max_stretch']:.2f} (scene has soft highlights)")
        tp = (mid - con.out_black) / (con.out_white - con.out_black)
    con.gamma = float(np.clip(np.log(np.clip(tp, 1e-3, 0.999)) / np.log(np.clip(tm, 1e-3, 0.999)), 0.75, 1.35))
    if t["meter"] == "white":    # exposed for the highlights: keep the light's own mid-tones, no median pin, and
        con.gamma = 1.0          # no upward white stretch (that would undo the metering): only the black point moves
        con.out_white = float(np.clip(con.white, con.out_black + 0.3, t["white"]))
        con.pivot = float(con.out_black + tm * (con.out_white - con.out_black))
    # shadow floor: if a big shadow area is pushed under floor_p10, back the S-curve off, then lift the black target
    for _ in range(12):
        if np.percentile(_ycc(con.apply(a2))[0], 20) >= t["floor_p20"] or con.s <= 0 and con.out_black >= max(0.06, t["black"] + 0.03):
            break
        if con.s > 0:
            con.s = max(0.0, con.s - 0.05)
        else:
            con.out_black = min(max(0.06, t["black"] + 0.03), con.out_black + 0.01)
    if con.s < t["s_curve"] or con.out_black > t["black"]:
        notes.append(f"3 CONTRAST: shadow floor held (S {con.s:.2f}, black {con.out_black:.3f})")
    a3 = con.apply(a2)

    # node 4: vectorscope: chroma to sat_gain x the LUT's, capped (bisection keeps it monotonic and exact)
    base = scopes(a2)
    target = t["sat_mean"]

    def warm(rgb):    # rock: warm hues with some colour (what the eye reads as sandstone)
        _, cb_, cr_ = _ycc(rgb)
        h, c = np.degrees(np.arctan2(cr_, cb_)), np.hypot(cb_, cr_)
        m = (h > 90) & (h < 180) & (c > 0.03)
        return (float(np.median(c[m])), float(np.median(h[m]))) if m.sum() > 200 else (None, None)
    s_lo, s_hi = t["sat_min"], t["sat_max"]
    for _ in range(30):
        s_ = (s_lo + s_hi) / 2
        out4 = Sat(s_, t["blue_gain"], 1.0, t["sky_hue"], t["rock_hue"], t["yellow_hue"], t["vibrance"], t["shadow_sat"], t["hi_warm"], t["sky_lift"]).apply(a3)
        m = scopes(out4)
        rc = warm(out4)[0] if t["rock_chroma"] else None
        below = (rc < t["rock_chroma"]) if rc is not None else (m["sat_mean"] < target)
        if below and m["sat_p95"] < t["sat_p95_max"]:
            s_lo = s_
        else:
            s_hi = s_
    sat = Sat(s_lo, t["blue_gain"], 1.0, t["sky_hue"], t["rock_hue"], t["yellow_hue"], t["vibrance"], t["shadow_sat"], t["hi_warm"], t["sky_lift"])
    if t["rock_hue_target"]:          # rotate the rock's own hue onto the reference hue (full weight at its centre)
        _, h0 = warm(replace(sat, rock_hue=0.0).apply(a3))
        if h0 is not None:
            sat.rock_center, sat.rock_hue = h0, float(np.clip(t["rock_hue_target"] - h0, -20, 20))
    m4 = scopes(sat.apply(a3))
    if m4["sat_mean"] < target * 0.95:
        notes.append(f"4 SAT: held at x{s_lo:.2f} by the vectorscope ceiling (p95 chroma {m4['sat_p95']:.3f})")
    if m4["hi_sat"] > t["hi_sat_max"]:
        lo_, hi_ = 0.0, 1.0
        for _ in range(25):
            h = (lo_ + hi_) / 2
            if scopes(replace(sat, hi_limit=h).apply(a3))["hi_sat"] > t["hi_sat_max"]:
                hi_ = h
            else:
                lo_ = h
        sat.hi_limit = lo_
        notes.append(f"4 SAT: bright-area chroma limited (x{lo_:.2f} at the top)")
    a4 = sat.apply(a3)

    # node 5: vectorscope: neutrals back to centre (gain for highlights, lift for shadows), soft clip
    fin = Finish(ceiling=t["ceiling"])
    y4 = _ycc(a4)[0]
    mask = nz["mask"] & (y4 > 0.1) & (y4 < 0.9)
    if mask.sum() > 100 and cast <= cs.WB_FULL:
        c = neutral_cast(a4, mask)                                   # [[cb,cr]_shadows, [cb,cr]_highlights]
        def to_rgb(cb, cr):    # the RGB offset that a (cb, cr) cast is, at constant luma
            return _rgb(0.0, cb, cr)
        fin.gain = 1 - to_rgb(*c[1]) / max(float(np.median(y4[mask])), 0.2)
        fin.lift = -to_rgb(*c[0]) * 0.5
        fin.gain, fin.lift = np.clip(fin.gain, 0.9, 1.1), np.clip(fin.lift, -0.02, 0.02)
    a5 = fin.apply(a4)

    g = FiveNode(bal, cst, con, sat, fin, notes)
    g.stats = {"after_lut": {k: round(v, 4) for k, v in base.items()},
               "final": {k: round(v, 4) for k, v in scopes(a5).items()},
               "neutral_confidence": round(float(nz["confidence"]), 3), "neutral_cast_log": round(cast, 4)}
    return g


def write_node_cubes(g: FiveNode, folder: Path, stem: str) -> dict[int, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for k, fn in g.node_fns().items():
        p = folder / f"{stem}_n{k}.cube"
        cs.write_cube(p, fn, n=65 if k == 2 else 33, title=f"{stem} node {k}")
        out[k] = p
    return out
