"""The five-node grade, in the owner's Resolve layout, every node an exact transform solved from the scopes.

    1 CST IN     GP-Log -> DaVinci Intermediate / DaVinci Wide Gamut                      (cst.cst_in)
    2 CST OUT    DaVinci Intermediate / DWG -> Rec.709 gamma 2.4                            (cst.cst_out)
    3 PRIMARY    Lift / Gamma / Gain / Offset wheels:  y = (lift (1 - x) + gain x + offset) ^ power
                 gain  = exposure (+ highlight white balance), lift = black point (+ shadow white balance),
                 power = 1/gamma puts the median on target. Solved on the waveform (p0.5 / median / p99.5) and
                 the vectorscope (neutral cluster to centre), per channel.
    4 TONE+SAT   Custom curve: luma-only S around the median (hue and ratios untouched).
                 HSL: global saturation to a vectorscope target; Sat vs Lum (clean deep shadows, capped
                 highlights); Hue vs Sat / Hue vs Hue / Hue vs Lum for the sky (blue gain, toward true blue, lift).
    5 LOOK       Creative: vibrance (muted colours first), warm/cool split tone, warm highlight chroma, soft
                 shoulder under the ceiling.

Nodes 3-5 grade display-referred Rec.709, after the output transform, exactly as listed. Everything that went
wrong in the earlier auto-grades is a rule here, not advice (docs/COLOR_LESSONS.md):
  - exposure is metered without the brightest 10% (sky, sun) and never by anything node 3 changes afterwards;
  - node 3 cannot redo exposure: the white stretch is capped and the gamma bounded (squash-then-stretch = gray);
  - white balance fades out when the "neutral" cluster is too coloured to be a light cast (red rock);
  - a shadow floor keeps texture in big shadow areas; deep shadows lose chroma instead of going brown;
  - every curve is monotonic by construction (a lift that reversed tones once turned a sky to blotches).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np

from . import colorsci as cs, cst

KR, KB = 0.2126, 0.0722
KG = 1 - KR - KB
GOPRO_LUT = "E:/ASSETS/GPLOG_Creative_EV0 - a9g8a0000004G5nAAE.cube"      # GoPro's GP-Log -> Rec.709 download

TARGETS = {
    "camera_gamut": "rec709",   # node 1: see cst.py
    "transform": "cst",         # nodes 1-2: "cst" (exact DWG maths) or "gopro_lut" (GoPro's own GP-Log -> Rec.709 LUT)
    "order": "display",         # "scene": grade in log on nodes 1-3, CST last (solve_scene); "display": transform first
    "cst_lut": None,            # scene order: Resolve's CST exported as .cube (else RESOLVE_CST_LUT, else GoPro's LUT)
    "white_gain": True,         # scene order: False = no white-point gain; the CST's own shoulder handles highlights
    "creative_lut": None,       # node 5: path of a creative .cube applied on Rec.709 (e.g. VDR CINE pack)
    "creative_mix": 1.0,        # node 5: how much of the creative LUT (0 = none)
    # node 3 (waveform targets, 0-1 display luma)
    "black": 0.03,              # p0.5 -> this
    "mid": 0.42,                # median (without the brightest 10%) -> this
    "white": 0.88,              # gain also brings p99.5 at least up to this (the Gain wheel sets the white point)
    "over": 1.10,               # exposure gain may push luma p95 up to this (nodes pass >1 through; node 5 rolls off)
    "power_range": [0.7, 1.43], # allowed 1/gamma: a trim around the exposure, never a second exposure
    "floor_pct": 10.0, "floor_level": 0.06,   # luma at floor_pct may not fall below floor_level (lift up to +0.08)
    "wb": 1.0,                  # white-balance strength (0 = as shot)
    "wb_r": 0.0, "wb_g": 0.0, "wb_b": 0.0,    # manual trims added to gain (highlights), display units
    # node 4
    "s_curve": 0.18,            # S strength around the median (0 = none)
    "sat_mean": 0.105,          # vectorscope mean chroma target
    "sat_min": 0.6, "sat_max": 1.8, "sat_p95_max": 0.26,
    "shadow_sat": 1.15,          # chroma x this below luma 0.12
    "hi_sat_max": 0.22,         # chroma ceiling in the brightest 5% (compression banding)
    "blue_gain": 1.45,           # Hue vs Sat: blues
    "green_gain": 1.0,          # Hue vs Sat: greens (shrubs, trees) - flat midday light wants them to separate
    "warm_split": 0.0,          # Hue vs Sat by luma on warm hues: +x = sunlit rock more saturated, shaded less
    "sky_hue": 16.0,             # Hue vs Hue: bright cyans this many degrees toward blue
    "sky_lift": 0.0,            # Hue vs Lum: sky brightness in stops
    # node 5
    "vibrance": 0.5,            # extra gain for muted colours, none by chroma 0.15
    "split": 0.015,             # split tone: shadows cool / highlights warm (fraction per channel)
    "hi_warm": 1.15,            # chroma gain for warm hues above luma 0.6 (sunlit sandstone stays orange)
    "ceiling": 0.96,            # soft shoulder: nothing above this
    # shot matching (all optional): targets measured on a hero shot of the same session, in display space
    "rock_rgb": None,           # node 4: [R/G, 1, B/G] of the rock's median colour -> hue-qualified RGB gain
    "sky": None,                # node 4: {"Y": luma, "c": chroma, "hue": degrees} of the sky -> Hue vs Lum/Sat/Hue
    "shoulder": 0.16,           # width of the shoulder below the ceiling (over-range from node 3 lands here)
}


def _ycc(rgb):
    y = rgb @ np.array([KR, KG, KB])
    return y, (rgb[..., 2] - y) / 1.8556, (rgb[..., 0] - y) / 1.5748


def _rgb(y, cb, cr):
    r, b = y + 1.5748 * cr, y + 1.8556 * cb
    return np.stack([r, (y - KR * r - KB * b) / KG, b], -1)


def rock_sky(rgb) -> dict:
    """Vectorscope signature used for shot matching: the rock (warm hues, mid luma) and the sky (blue hues)."""
    y, cb, cr = _ycc(rgb)
    c, h = np.hypot(cb, cr), np.degrees(np.arctan2(cr, cb))
    rock = (h > 90) & (h < 180) & (c > 0.03) & (y > 0.2) & (y < 0.8)
    sky = (h < 0) & (h > -90) & (c > 0.02) & (y > 0.3)
    out = {"rock_n": int(rock.sum()), "sky_n": int(sky.sum())}
    if rock.sum() > 50:
        med = np.median(rgb[rock], axis=0)
        out["rock_rgb"] = med / max(med[1], 1e-4)
    if sky.sum() > 50:
        out["sky"] = {"Y": float(np.median(y[sky])), "c": float(np.median(c[sky])), "hue": float(np.median(h[sky]))}
    return out


def scopes(rgb) -> dict:
    y, cb, cr = _ycc(rgb)
    c = np.hypot(cb, cr)
    q = np.percentile(y, [0.5, 50, 99.5])
    return {"black": float(q[0]), "mid": float(q[1]), "white": float(q[2]), "sat_mean": float(c.mean()),
            "sat_p95": float(np.percentile(c, 95)),
            "hi_sat": float(np.percentile(c[y >= np.percentile(y, 95)], 95))}   # >=: a clipped top 5% is one value


# ---------------------------------------------------------------- nodes

@dataclass
class Primary:                      # node 3
    lift: np.ndarray = field(default_factory=lambda: np.zeros(3))
    gain: np.ndarray = field(default_factory=lambda: np.ones(3))
    power: float = 1.0              # = 1 / gamma
    offset: float = 0.0

    def apply(self, x):     # float through: values above 1 are kept for node 5's shoulder (as Resolve does)
        y = self.lift * (1 - x) + self.gain * x + self.offset
        return np.power(np.maximum(y, 0), self.power)

    def resolve(self) -> dict:      # the wheel values to type into Resolve's primaries (Lift / Gamma / Gain)
        return {"lift": self.lift.round(4).tolist(), "gain": self.gain.round(4).tolist(),
                "gamma": round(1 / self.power, 4), "offset": round(self.offset, 4)}


@dataclass
class ToneSat:                      # node 4
    pivot: float = 0.42
    s: float = 0.0
    sat: float = 1.0
    shadow_sat: float = 1.0
    hi_limit: float = 1.0
    blue: float = 1.0
    sky_hue: float = 0.0
    sky_lift: float = 0.0
    rock_gain: np.ndarray = field(default_factory=lambda: np.ones(3))   # qualified on warm hues only
    green: float = 1.0
    warm_split: float = 0.0

    def curve(self, u):             # S around the pivot: a power above and below it, continuous at the pivot;
        p, k = np.clip(self.pivot, 0.05, 0.95), 1 + self.s                  # over-range (u > 1) passes through
        uu = np.clip(u, 0, 1)
        c = np.where(uu < p, p * (uu / p) ** k, 1 - (1 - p) * ((1 - uu) / (1 - p)) ** k)
        return np.where(u > 1, u, c)

    def apply(self, rgb):
        y0 = rgb @ np.array([KR, KG, KB])
        if self.s:
            rgb = np.maximum(rgb * (self.curve(np.maximum(y0, 1e-4)) / np.maximum(y0, 1e-4))[:, None], 0)
        y, cb, cr = _ycc(rgb)
        hue, c = np.degrees(np.arctan2(cr, cb)), np.hypot(cb, cr)
        if np.any(self.rock_gain != 1):     # qualifier: warm hues (rock) get their own RGB gain; sky untouched
            w = np.clip(1 - np.abs(hue - 130) / 50, 0, 1) * np.clip(c / 0.03, 0, 1)
            rgb = np.maximum(rgb * (1 + w[:, None] * (self.rock_gain - 1)), 0)
            y, cb, cr = _ycc(rgb)
            hue, c = np.degrees(np.arctan2(cr, cb)), np.hypot(cb, cr)
        if self.sky_hue:            # Hue vs Hue: bright cyan (-45) toward blue (-10)
            w = np.clip(1 - np.abs(hue + 45) / 30, 0, 1) * np.clip((y - 0.45) / 0.15, 0, 1)
            rot = np.radians(w * self.sky_hue)
            cb, cr = cb * np.cos(rot) - cr * np.sin(rot), cb * np.sin(rot) + cr * np.cos(rot)
            hue = np.degrees(np.arctan2(cr, cb))
        w_blue = np.clip(1 - np.abs((hue + 12 + 180) % 360 - 180) / 35, 0, 1) ** 1.5 * np.clip(c / 0.03, 0, 1)
        g = self.sat * (1 + w_blue * (self.blue - 1))                              # Hue vs Sat
        g = g * (1 - (1 - self.shadow_sat) * np.clip((0.12 - y) / 0.06, 0, 1))      # Sat vs Lum: shadows
        g = g * (1 - (1 - self.hi_limit) * np.clip((y - 0.75) / 0.2, 0, 1))         # Sat vs Lum: highlights
        out = _rgb(y, cb * g, cr * g)
        if self.sky_lift:           # Hue vs Lum: sky
            w = np.clip(1 - np.abs(hue + 30) / 50, 0, 1) * np.clip((y - 0.25) / 0.1, 0, 1) * np.clip(c / 0.02, 0, 1)
            out = out * (2 ** (self.sky_lift / 2.4)) ** w[:, None]
        return np.maximum(out, 0)


@dataclass
class Look:                         # node 5
    vibrance: float = 0.0
    split: float = 0.0
    hi_warm: float = 1.0
    ceiling: float = 1.0
    shoulder: float = 0.0
    creative_lut: object = None     # cs.CubeLUT, applied first (expects Rec.709 display values)
    creative_mix: float = 1.0

    def apply(self, rgb):
        if self.creative_lut is not None:
            looked = cs.apply_lut(self.creative_lut, np.clip(rgb, 0, 1))
            rgb = np.where(rgb[:, :1] > 1, rgb, rgb + self.creative_mix * (looked - np.clip(rgb, 0, 1)))
        y, cb, cr = _ycc(rgb)
        c, hue = np.hypot(cb, cr), np.degrees(np.arctan2(cr, cb))
        g = 1 + self.vibrance * np.clip(1 - c / 0.15, 0, 1)
        g = g * (1 + np.clip(1 - np.abs(hue - 140) / 45, 0, 1) * np.clip((y - 0.6) / 0.1, 0, 1) * (self.hi_warm - 1))
        out = _rgb(y, cb * g, cr * g)
        if self.split:              # shadows cool, highlights warm, measured from the median
            lo, hi = np.clip((0.3 - y) / 0.3, 0, 1)[:, None], np.clip((y - 0.6) / 0.3, 0, 1)[:, None]
            out = out * (1 + self.split * (hi * np.array([1, 0, -1]) - lo * np.array([1, -0.2, -1])))
        if self.shoulder > 0:       # soft shoulder: exponential knee into the ceiling, monotonic, takes over-range
            knee = self.ceiling - self.shoulder
            over = np.maximum(out - knee, 0)
            out = np.where(out > knee, knee + (self.ceiling - knee) * (1 - np.exp(-over / (self.ceiling - knee))), out)
        return np.clip(out, 0, 1)


@dataclass
class Grade5:
    gamut: str
    prim: Primary
    tone: ToneSat
    look: Look
    notes: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    transform: str = "cst"
    conv_lut: object = None

    def node_fns(self):
        if self.transform == "scene":       # grade in log, CST last
            return {1: self.prim.apply, 2: self.tone.apply, 3: self.look.apply,
                    4: lambda v: np.clip(cs.apply_lut(self.conv_lut, np.clip(v, 0, 1)), 0, 1)}
        if self.transform == "gopro_lut":   # nodes 1-2 as one step: the manufacturer's conversion LUT
            return {1: lambda v: np.clip(v, 0, 1), 2: lambda v: np.clip(cs.apply_lut(self.conv_lut, np.clip(v, 0, 1)), 0, 1),
                    3: self.prim.apply, 4: self.tone.apply, 5: self.look.apply}
        return {1: lambda v: cst.cst_in(np.clip(v, 0, 1), self.gamut), 2: cst.cst_out,
                3: self.prim.apply, 4: self.tone.apply, 5: self.look.apply}

    def apply(self, log_rgb, upto: int = 5):
        x = log_rgb
        for k, f in self.node_fns().items():
            if k > upto:
                break
            x = f(x)
        return np.clip(x, 0, 1) if upto >= len(self.node_fns()) else x   # only the last node clips

    def to_dict(self):
        return {"1_cst_in": ({"from": f"GP-Log / {self.gamut}", "to": "DaVinci Intermediate / DWG"} if self.transform == "cst"
                             else {"lut": "GoPro GP-Log -> Rec.709 (manufacturer)"}),
                "2_cst_out": {"to": "Rec.709 gamma 2.4", "tone_mapping": "none"},
                "3_primary": self.prim.resolve(),
                "4_tone_sat": {k: (np.round(v, 4).tolist() if isinstance(v, np.ndarray) else round(float(v), 4))
                               for k, v in asdict(self.tone).items() if k != "key" and v is not None},
                "5_look": {k: (round(float(v), 4) if isinstance(v, (int, float)) else str(v is not None))
                           for k, v in asdict(self.look).items() if k != "key"},
                "notes": self.notes, "stats": self.stats}


# ---------------------------------------------------------------- solver

def targets_for(over: dict | None) -> dict:
    return {**TARGETS, **(over or {})}


def solve(log_rgb: np.ndarray, over: dict | None = None) -> Grade5:
    """log_rgb: (N,3) GP-Log pixels sampled from the shot's used range (0-1, full range)."""
    t = targets_for(over)
    if t.get("order") == "scene":
        return solve_scene(log_rgb, over)
    rng = np.random.default_rng(0)
    px = log_rgb[rng.choice(len(log_rgb), min(len(log_rgb), 12000), replace=False)]
    notes = []
    conv = None
    if t["transform"] == "gopro_lut":
        conv = cs.load_cube(Path(GOPRO_LUT))
        base = np.clip(cs.apply_lut(conv, px), 0, 1)                         # nodes 1-2: GoPro's conversion
    else:
        base = cst.cst_out(cst.cst_in(px, t["camera_gamut"]))              # nodes 1-2 (exact CST)
    y = _ycc(base)[0]

    # node 3: white balance from the neutral cluster (found in log, measured in display), guarded for red rock
    nz = cs.find_neutrals(px)
    cast = float(np.hypot(*nz["d"]))
    wb = t["wb"] * float(np.clip((cs.WB_NONE - cast) / (cs.WB_NONE - cs.WB_FULL), 0, 1))
    if nz["confidence"] < 0.15 or nz["spread"] < 0.05:
        wb *= 0.5
        notes.append(f"3 PRIMARY: weak neutrals (confidence {nz['confidence']:.2f}), white balance half-applied")
    if cast > cs.WB_FULL:
        notes.append(f"3 PRIMARY: 'neutral' cluster too coloured ({cast:.3f}) to be a light cast; WB limited to {wb:.0%}")
    m = nz["mask"]
    gain, lift = np.ones(3), np.zeros(3)
    if m.sum() > 100 and wb > 0:
        yn = y[m]
        hi, lo = m.copy(), m.copy()
        hi[m] = yn >= np.median(yn)
        lo[m] = yn < np.median(yn)
        ch, cl = base[hi].mean(0), base[lo].mean(0)
        gain = 1 + wb * (ch.mean() / np.maximum(ch, 1e-4) - 1)               # per-channel gain: highlights neutral
        lift = -wb * (cl - cl.mean())                                         # per-channel lift: shadows neutral
    gain = gain * np.array([1 + t["wb_r"], 1 + t["wb_g"], 1 + t["wb_b"]])

    # node 3: exposure = gain (all channels) metered on the median without the brightest 10%, limited so the bulk
    # of the picture (p95) goes no further than `over` into node 5's shoulder; lift sets the black point; a bounded
    # gamma trims the median the rest of the way. The white point is wherever the exposure puts it.
    meter = y < np.percentile(y, 90)
    b_in, mid_in, p95, w_in = np.percentile(y, 0.5), np.median(y[meter]), np.percentile(y, 95), np.percentile(y, 99.5)

    def lgg(white_t):
        """Lift, gain and power from three waveform points: p0.5 -> black, median -> mid, p99.5 -> white.
        For a given power p the black and white constraints are linear in (lift, gain); p is then bisected so the
        median lands on mid (the median falls as p rises, so the search is monotonic)."""
        def fit(p):
            Bt, Wt = t["black"] ** (1 / p), white_t ** (1 / p)
            k = (Wt - Bt) / max(w_in - b_in, 1e-3)
            l = Bt - k * b_in
            return l, l + k, (l + k * mid_in) ** p
        lo_p, hi_p = t["power_range"]
        for _ in range(30):
            p = (lo_p + hi_p) / 2
            if fit(p)[2] > t["mid"]:
                lo_p = p
            else:
                hi_p = p
        p = (lo_p + hi_p) / 2
        l, g, _ = fit(p)
        return l, g, p

    white_t = max(t["white"], float(w_in))               # the white point only ever moves up (never squash)
    l_e, g_e, pw = lgg(white_t)

    def p95_after(l, g, p):
        return (l + (g - l) * p95) ** p
    if (l_e + (g_e - l_e) * mid_in) ** pw < t["mid"] - 0.01:
        # mids still under target within the gamma range: raise the white point further (into the shoulder, up to
        # `over` at p95 - the sky was clipped at capture anyway) until the median lands or the budget is spent
        lo_w, hi_w = white_t, 3.0
        for _ in range(25):
            w = (lo_w + hi_w) / 2
            l, g, p = lgg(w)
            if (l + (g - l) * mid_in) ** p < t["mid"] and p95_after(l, g, p) <= t["over"]:
                lo_w = w
            else:
                hi_w = w
        white_t = lo_w
        l_e, g_e, pw = lgg(white_t)
        if p95_after(l_e, g_e, pw) >= t["over"] - 0.01:
            notes.append(f"3 PRIMARY: exposure limited by the shoulder budget (p95 at {t['over']:.2f})")
    else:
        for _ in range(20):                               # bright scene: keep p95 out of the shoulder
            if p95_after(l_e, g_e, pw) <= t["over"]:
                break
            white_t -= 0.01
            l_e, g_e, pw = lgg(white_t)
    prim = Primary(lift=l_e + lift * g_e, gain=gain * g_e, power=pw)
    if abs(np.median(_ycc(prim.apply(base))[0][meter]) - t["mid"]) > 0.03:
        notes.append("3 PRIMARY: median off target within the gamma range (very flat or very contrasty scene)")
    for _ in range(10):                                   # shadow floor: big shadow areas keep texture
        yy = _ycc(prim.apply(base))[0]
        if np.percentile(yy, t["floor_pct"]) >= t["floor_level"] or prim.lift.mean() > l_e + 0.08:
            break
        prim.lift = prim.lift + 0.01
    if prim.lift.mean() > l_e + 0.005:
        notes.append(f"3 PRIMARY: shadow floor lifted blacks to {prim.lift.mean():.3f}")
    a3 = prim.apply(base)
    y3 = _ycc(a3)[0]

    # node 4: S around the median; saturation to the vectorscope target under its ceilings
    tone = ToneSat(pivot=float(np.median(y3[meter])), s=t["s_curve"], shadow_sat=t["shadow_sat"],
                   blue=t["blue_gain"], sky_hue=t["sky_hue"], sky_lift=t["sky_lift"])
    lo_, hi_ = t["sat_min"], t["sat_max"]
    for _ in range(30):
        s_ = (lo_ + hi_) / 2
        mm = scopes(replace(tone, sat=s_).apply(a3))
        if mm["sat_mean"] < t["sat_mean"] and mm["sat_p95"] < t["sat_p95_max"]:
            lo_ = s_
        else:
            hi_ = s_
    tone.sat = lo_
    mm = scopes(tone.apply(a3))
    if mm["sat_mean"] < t["sat_mean"] * 0.95:
        notes.append(f"4 TONE+SAT: saturation held at x{tone.sat:.2f} by the vectorscope ceiling")
    if mm["hi_sat"] > t["hi_sat_max"]:
        l2, h2 = 0.0, 1.0
        for _ in range(25):
            hl = (l2 + h2) / 2
            if scopes(replace(tone, hi_limit=hl).apply(a3))["hi_sat"] > t["hi_sat_max"]:
                h2 = hl
            else:
                l2 = hl
        tone.hi_limit = l2
        notes.append(f"4 TONE+SAT: highlight chroma limited (x{l2:.2f} at the top)")
    if t["rock_rgb"] is not None:      # shot matching: the rock's median colour to the hero's, on warm hues only
        target = np.array(t["rock_rgb"], float)
        for _ in range(5):
            rs = rock_sky(tone.apply(a3))
            if "rock_rgb" not in rs:
                notes.append("4 TONE+SAT: no rock found to match"); break
            tone.rock_gain = np.clip(tone.rock_gain * target / rs["rock_rgb"], 0.6, 1.6)
            tone.rock_gain = tone.rock_gain / tone.rock_gain[1]
        rs = rock_sky(tone.apply(a3))
        notes.append(f"4 TONE+SAT: rock matched R/G {rs['rock_rgb'][0]:.3f} B/G {rs['rock_rgb'][2]:.3f} (gain {np.round(tone.rock_gain, 3)})")
    if t["sky"] is not None:           # shot matching: the sky to the hero shot's (Hue vs Hue / Sat / Lum)
        rs = rock_sky(tone.apply(a3))
        if "sky" in rs and rs["sky_n"] > 200:
            tgt = t["sky"]
            tone.sky_hue = float(np.clip(tone.sky_hue + (tgt["hue"] - rs["sky"]["hue"]), -30, 30))
            for key, lo_v, hi_v in (("blue", 0.5, 3.0), ("sky_lift", -1.0, 1.5)):
                for _ in range(20):
                    mid_v = (lo_v + hi_v) / 2
                    m = rock_sky(replace(tone, **{key: mid_v}).apply(a3)).get("sky", rs["sky"])
                    under = (m["c"] < tgt["c"]) if key == "blue" else (m["Y"] < tgt["Y"])
                    lo_v, hi_v = (mid_v, hi_v) if under else (lo_v, mid_v)
                setattr(tone, key, (lo_v + hi_v) / 2)
            m = rock_sky(tone.apply(a3))["sky"]
            notes.append(f"4 TONE+SAT: sky matched (Y {m['Y']:.2f} c {m['c']:.3f} hue {m['hue']:.0f})")
        else:
            notes.append("4 TONE+SAT: too little sky to match")
    a4 = tone.apply(a3)

    look = Look(vibrance=t["vibrance"], split=t["split"], hi_warm=t["hi_warm"], ceiling=t["ceiling"], shoulder=t["shoulder"],
                creative_lut=cs.load_cube(Path(t["creative_lut"])) if t["creative_lut"] else None, creative_mix=t["creative_mix"])
    a5 = look.apply(a4)
    g = Grade5(t["camera_gamut"], prim, tone, look, notes, transform=t["transform"], conv_lut=conv)
    g.stats = {"after_cst": {k: round(v, 4) for k, v in scopes(base).items()},
               "final": {k: round(v, 4) for k, v in scopes(a5).items()},
               "neutral_confidence": round(float(nz["confidence"]), 3), "neutral_cast_log": round(cast, 4)}
    return g


def write_node_cubes(g: Grade5, folder: Path, stem: str) -> dict[int, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for k, fn in g.node_fns().items():
        p = folder / f"{stem}_n{k}.cube"
        cs.write_cube(p, fn, n=65 if k in (1, 2) else 33, title=f"{stem} node {k}")
        out[k] = p
    if isinstance(g.prim, LogPrimary):          # native primaries: node 1 as a CDL, its shadow lift moved into node 2
        (folder / f"{stem}_n1_cdl.json").write_text(json.dumps(g.prim.cdl()))
        cs.write_cube(folder / f"{stem}_n2_after_cdl.cube", lambda v: g.tone.apply(g.prim.shadow(v)), n=65,
                      title=f"{stem} node 2 (after a CDL node 1)")
    return out


# ================================================================ scene-referred order: grade in log, CST last
#
#   1 PRIMARY   Lift / Gamma / Gain / Offset on the GP-Log codes: offset = exposure (a log offset is an exact gain
#               on scene light), per-channel offset = white balance, lift/gain = black and white points
#   2 TONE+SAT  S-curve on log luma; saturation in log chroma; sky qualifiers keyed on the *displayed* colour
#   3 LOOK      vibrance in log (keyed on the displayed colour)
#   4 CST       GoPro / GP-Log -> Rec.709 / Rec.709: Resolve's own CST exported as a .cube when it exists
#               (RESOLVE_CST_LUT or TARGETS["cst_lut"]), else GoPro's conversion LUT. Nothing is graded after it.
# Every node is solved by measuring THROUGH the CST (the scopes show Rec.709): grading under the transform.

RESOLVE_CST_LUT = "E:/ASSETS/Resolve_CST_GoPro_GPLog_to_Rec709.cube"   # export this from a CST node in Resolve
STOP = np.log(2) / np.log(400)                                          # one stop in GP-Log code units


@dataclass
class LogPrimary(Primary):
    """Primary on log codes plus a shadow-only lift: x' = x (1 + G (t/(x+t))^2) on scene light, full gain 2^stops
    below the threshold t (a shot percentile), fading to none by ~3t. Monotonic for lifts up to ~4.8 stops; the gain
    is computed on the mean so hue and saturation are kept."""
    lift_stops: float = 0.0
    lift_at: float = 0.0        # log code (mean of RGB) of the threshold

    def apply(self, x):
        return self.shadow(super().apply(x))

    def shadow(self, v):
        """The shadow-only lift alone (identity when lift_stops is 0). No CDL can express it, so in Resolve it rides
        at the front of node 2's cube while node 1 carries cdl()."""
        if self.lift_stops:
            lin = (400.0 ** np.clip(v, 0, 1) - 1) / 399
            n = lin.mean(axis=1, keepdims=True)
            t = (400.0 ** self.lift_at - 1) / 399
            v = np.log(np.maximum(lin * (1 + (2.0 ** self.lift_stops - 1) * (t / (n + t)) ** 2), 0) * 399 + 1) / np.log(400)
        return v

    def resolve(self) -> dict:
        return {**super().resolve(), "lift_stops": round(float(self.lift_stops), 4), "lift_at": round(float(self.lift_at), 4)}

    def cdl(self) -> dict:
        """Primary.apply as an ASC CDL, exactly: lift (1-x) + gain x + offset = (gain - lift) x + (lift + offset),
        then ^power. Resolve takes it natively (TimelineItem.SetCDL), in float, instead of a sampled LUT."""
        return {"slope": (self.gain - self.lift).round(6).tolist(),
                "offset": (self.lift + self.offset).round(6).tolist(),
                "power": [round(float(self.power), 6)] * 3, "saturation": 1.0}


def _bisect(too_low, lo, hi, n=22):
    for _ in range(n):
        m = (lo + hi) / 2
        lo, hi = (m, hi) if too_low(m) else (lo, m)
    return (lo + hi) / 2


@dataclass
class LogToneSat(ToneSat):
    """ToneSat on log codes; the hue / luma qualifiers are keyed on the displayed result (through the CST)."""
    key: object = None   # callable: log rgb -> display rgb (a field, so dataclasses.replace keeps it)

    def apply(self, rgb):
        disp = self.key(rgb)
        yd, cbd, crd = _ycc(disp)
        hue, c = np.degrees(np.arctan2(crd, cbd)), np.hypot(cbd, crd)
        y0 = rgb.mean(axis=1)
        if self.s:                                     # S-curve on log luma, channel ratios kept
            rgb = np.maximum(rgb * (self.curve(np.clip(y0, 1e-4, 1)) / np.maximum(y0, 1e-4))[:, None], 0)
        m = rgb.mean(axis=1, keepdims=True)
        d = rgb - m                                    # log chroma
        if self.sky_hue:                               # rotate the log-chroma vector about the grey axis
            w = np.clip(1 - np.abs(hue + 45) / 30, 0, 1) * np.clip((yd - 0.45) / 0.15, 0, 1)
            rot = np.radians(w * self.sky_hue)
            k = np.ones(3) / np.sqrt(3)
            cosr, sinr = np.cos(rot)[:, None], np.sin(rot)[:, None]
            d = d * cosr + np.cross(k, d) * sinr + k * (d @ k)[:, None] * (1 - cosr)
        w_blue = np.clip(1 - np.abs((hue + 12 + 180) % 360 - 180) / 35, 0, 1) ** 1.5 * np.clip(c / 0.03, 0, 1)
        g = self.sat * (1 + w_blue * (self.blue - 1))
        w_green = np.clip(1 - np.abs((hue + 135 + 180) % 360 - 180) / 40, 0, 1) * np.clip(c / 0.03, 0, 1)  # green ~ -135
        g = g * (1 + w_green * (self.green - 1))
        if self.warm_split:                            # warm hues: sunlit faces up, shaded faces down
            w_warm = np.clip(1 - np.abs(hue - 130) / 50, 0, 1)
            g = g * (1 + self.warm_split * w_warm * np.clip((yd - 0.45) / 0.25, -1, 1))
        g = g * (1 - (1 - self.shadow_sat) * np.clip((0.12 - yd) / 0.06, 0, 1))
        g = g * (1 - (1 - self.hi_limit) * np.clip((yd - 0.75) / 0.2, 0, 1))
        out = m + d * g[:, None]
        if self.sky_lift:
            w = np.clip(1 - np.abs(hue + 30) / 50, 0, 1) * np.clip((yd - 0.25) / 0.1, 0, 1) * np.clip(c / 0.02, 0, 1)
            out = out + (w * self.sky_lift * STOP)[:, None]
        return np.clip(out, 0, 1)


@dataclass
class LogLook(Look):
    key: object = None

    def apply(self, rgb):
        if self.vibrance:
            _, cb, cr = _ycc(self.key(rgb))
            g = 1 + self.vibrance * np.clip(1 - np.hypot(cb, cr) / 0.15, 0, 1)
            m = rgb.mean(axis=1, keepdims=True)
            rgb = m + (rgb - m) * g[:, None]
        return np.clip(rgb, 0, 1)


def solve_scene(log_rgb: np.ndarray, over: dict | None = None) -> Grade5:
    t = targets_for(over)
    lut_path = Path(t.get("cst_lut") or RESOLVE_CST_LUT)
    if not lut_path.exists():
        lut_path = Path(GOPRO_LUT)
    lut = cs.load_cube(lut_path)
    cst_fn = lambda v: np.clip(cs.apply_lut(lut, np.clip(v, 0, 1)), 0, 1)
    rng = np.random.default_rng(0)
    px = log_rgb[rng.choice(len(log_rgb), min(len(log_rgb), 12000), replace=False)]
    notes = [f"4 CST: {lut_path.name}"]

    # node 1: white balance = per-channel log offset (exact gain on scene light), guarded for red rock
    nz = cs.find_neutrals(px)
    cast = float(np.hypot(*nz["d"]))
    wb = t["wb"] * float(np.clip((cs.WB_NONE - cast) / (cs.WB_NONE - cs.WB_FULL), 0, 1))
    if nz["confidence"] < 0.15 or nz["spread"] < 0.05:
        wb *= 0.5
    if cast > cs.WB_FULL:
        notes.append(f"1 PRIMARY: 'neutral' cluster too coloured ({cast:.3f}) to be a light cast; WB limited to {wb:.0%}")
    wb_off = -wb * np.array([nz["d"][0], 0.0, nz["d"][1]]) + np.array([t["wb_r"], t["wb_g"], t["wb_b"]]) * STOP

    def disp_y(p):
        return _ycc(cst_fn(p.apply(px)))[0]
    y0 = disp_y(Primary())
    meter = y0 < np.percentile(y0, 90)

    # "log_gain" < 1 pulls the top of the log range down: highlight control the exposure offset cannot give
    prim = LogPrimary(lift=np.zeros(3), gain=np.full(3, float(t.get("log_gain", 1.0))), power=1.0, offset=0.0)
    # exposure: one offset for all channels so the metered median lands on mid
    prim.offset = _bisect(lambda e: np.median(disp_y(replace(prim, offset=e))[meter]) < t["mid"], -0.4, 0.4)
    for _ in range(3):
        l = _bisect(lambda v: np.percentile(disp_y(replace(prim, lift=np.full(3, v))), 0.5) < t["black"], -0.4, 0.4)
        prim.lift = np.full(3, l)                                        # black point
        if t.get("white_gain", True) and np.percentile(disp_y(prim), 99.5) < t["white"]:   # white point, only upward
            g_ = _bisect(lambda v: np.percentile(disp_y(replace(prim, gain=np.full(3, v))), 99.5) < t["white"], 1.0, 2.0)
            prim.gain = np.full(3, g_)
        lo_p, hi_p = t.get("scene_power_range", (0.55, 1.6))
        prim.power = _bisect(lambda p: np.median(disp_y(replace(prim, power=p))[meter]) > t["mid"], lo_p, hi_p)
    prim.lift, prim.gain = prim.lift + wb_off, prim.gain + wb_off         # same add to lift and gain = pure offset
    if t.get("shadow_lift"):      # shadow-only lift, threshold at the shot's lift_pct percentile of the log codes
        m_log = prim.apply(px).mean(axis=1)
        prim.lift_stops, prim.lift_at = float(t["shadow_lift"]), float(np.percentile(m_log, t.get("lift_pct", 15.0)))
        notes.append(f"1 PRIMARY: shadow lift +{t['shadow_lift']:.1f} stops below p{t.get('lift_pct', 15.0):.0f}")
    # the solved lift/gain are RGB-equal by construction, so the trim survives untouched (nothing re-solves after it)
    if abs(np.median(disp_y(prim)[meter]) - t["mid"]) > 0.03:
        notes.append("1 PRIMARY: median off target within the gamma range")

    a1 = prim.apply(px)
    tone = LogToneSat(pivot=float(np.median(a1.mean(1))), s=t["s_curve"], shadow_sat=t["shadow_sat"],
                      blue=t["blue_gain"], sky_hue=t["sky_hue"], sky_lift=t["sky_lift"],
                      green=t["green_gain"], warm_split=t["warm_split"])
    tone.key = cst_fn

    def m4(tn):
        return scopes(cst_fn(tn.apply(a1)))
    tone.sat = _bisect(lambda s_: (lambda m: m["sat_mean"] < t["sat_mean"] and m["sat_p95"] < t["sat_p95_max"])(
        m4(replace(tone, sat=s_))), t["sat_min"], t["sat_max"])
    mm = m4(tone)
    if mm["sat_mean"] < t["sat_mean"] * 0.95:
        notes.append(f"2 TONE+SAT: saturation held at x{tone.sat:.2f} by the vectorscope ceiling")
    if mm["hi_sat"] > t["hi_sat_max"]:
        tone.hi_limit = _bisect(lambda h: m4(replace(tone, hi_limit=h))["hi_sat"] <= t["hi_sat_max"], 0.0, 1.0)
        notes.append(f"2 TONE+SAT: highlight chroma limited (x{tone.hi_limit:.2f} at the top)")
    if t.get("sky") is not None:       # shot matching: every shot's sky to one target {Y, c, hue} (display)
        tgt = t["sky"]
        rs = rock_sky(cst_fn(tone.apply(a1)))
        if "sky" in rs and rs["sky_n"] > 150:
            tone.sky_hue = float(np.clip(tone.sky_hue + (tgt["hue"] - rs["sky"]["hue"]), -30, 30))
            # bounded: a sky that cannot reach the target within these stays short of it (past them it bands/cooks)
            tone.blue = _bisect(lambda v: rock_sky(cst_fn(replace(tone, blue=v).apply(a1))).get("sky", rs["sky"])["c"] < tgt["c"], 0.7, 1.6)
            tone.sky_lift = _bisect(lambda v: rock_sky(cst_fn(replace(tone, sky_lift=v).apply(a1))).get("sky", rs["sky"])["Y"] < tgt["Y"], -0.5, 0.5)
            m = rock_sky(cst_fn(tone.apply(a1)))["sky"]
            notes.append(f"2 TONE+SAT: sky matched (Y {m['Y']:.2f} c {m['c']:.3f} hue {m['hue']:.0f})")
        else:
            notes.append("2 TONE+SAT: too little sky to match")
    look = LogLook(vibrance=t["vibrance"])
    look.key = cst_fn
    g = Grade5(t["camera_gamut"], prim, tone, look, notes, transform="scene", conv_lut=lut)
    g.stats = {"after_cst_ungraded": {k: round(v, 4) for k, v in scopes(cst_fn(px)).items()},
               "final": {k: round(v, 4) for k, v in scopes(cst_fn(look.apply(tone.apply(a1)))).items()},
               "neutral_confidence": round(float(nz["confidence"]), 3), "neutral_cast_log": round(cast, 4)}
    return g


# ================================================================ unifying grade (a timeline-level node)
#
# Applied AFTER each shot's own grade, in display space: measures the graded shot and pulls it part of the way to
# anchors shared by the whole reel, so the per-shot grades can differ in character while the reel reads as one.
#   tone   lift / gain / power: p5, median and p95 toward the anchors
#   rock   warm hues (sandstone): hue rotated toward the anchor hue, chroma scaled toward the anchor chroma
#   chroma overall chroma scaled toward the anchor
# strength 0 = off, 1 = land exactly on the anchors. Every step is monotonic.

UNIFY = {"strength": 0.6, "p5": 0.06, "mid": 0.44, "p95": 0.84, "rock_hue": 128.0, "rock_chroma": 0.18,
         "chroma": 0.14, "max_hue_rot": 12.0}


@dataclass
class Unify:
    lift: float = 0.0
    gain: float = 1.0
    power: float = 1.0
    rock_rot: float = 0.0        # degrees, warm hues only
    rock_scale: float = 1.0      # chroma, warm hues only
    chroma_scale: float = 1.0

    def apply(self, rgb):
        y0 = rgb @ np.array([KR, KG, KB])
        yt = np.power(np.clip(self.lift + self.gain * np.clip(y0, 0, 1), 0, 1), self.power)
        rgb = np.clip(rgb * (yt / np.maximum(y0, 1e-4))[:, None], 0, 1)
        y, cb, cr = _ycc(rgb)
        hue, c = np.degrees(np.arctan2(cr, cb)), np.hypot(cb, cr)
        w = np.clip(1 - np.abs(hue - 130) / 50, 0, 1) * np.clip(c / 0.03, 0, 1)      # warm (rock) qualifier
        rot = np.radians(w * self.rock_rot)
        cb, cr = cb * np.cos(rot) - cr * np.sin(rot), cb * np.sin(rot) + cr * np.cos(rot)
        g = self.chroma_scale * (1 + w * (self.rock_scale - 1))
        return np.clip(_rgb(y, cb * g, cr * g), 0, 1)

    def to_dict(self):
        return {k: round(float(v), 4) for k, v in asdict(self).items()}


def solve_unify(graded: np.ndarray, u: dict | None = None) -> Unify:
    """graded: (N,3) display pixels of the shot after its own grade (the delivered framing)."""
    t = {**UNIFY, **(u or {})}
    s = t["strength"]
    y, cb, cr = _ycc(graded)
    p5, med, p95 = np.percentile(y, [5, 50, 95])
    B, M, W = p5 + s * (t["p5"] - p5), med + s * (t["mid"] - med), p95 + s * (t["p95"] - p95)

    def fit(pw):     # for a given power, lift/gain that put p5 and p95 on B and W; returns the median that results
        Bt, Wt = max(B, 1e-3) ** (1 / pw), max(W, 1e-3) ** (1 / pw)
        g = (Wt - Bt) / max(p95 - p5, 1e-3)
        l = Bt - g * p5
        return l, g, (max(l + g * med, 1e-3)) ** pw
    lo_p, hi_p = 0.75, 1.33                               # bisect the power so the median lands too (joint fit:
    for _ in range(24):                                   # setting lift/gain and then a gamma undid the blacks)
        pw = (lo_p + hi_p) / 2
        lo_p, hi_p = (pw, hi_p) if fit(pw)[2] > M else (lo_p, pw)
    power = (lo_p + hi_p) / 2
    lift, gain, _ = fit(power)
    gain = float(np.clip(gain, 0.7, 1.4))
    lift = float(np.clip(lift, -0.15, 0.15))
    un = Unify(lift, gain, float(power))
    out = un.apply(graded)
    yy, cb2, cr2 = _ycc(out)
    hue, c = np.degrees(np.arctan2(cr2, cb2)), np.hypot(cb2, cr2)
    rock = (hue > 90) & (hue < 180) & (c > 0.03) & (yy > 0.2) & (yy < 0.8)
    if rock.sum() > 200:
        h_r, c_r = float(np.median(hue[rock])), float(np.median(c[rock]))
        un.rock_rot = float(np.clip(s * (t["rock_hue"] - h_r), -t["max_hue_rot"], t["max_hue_rot"]))
        un.rock_scale = float(np.clip((t["rock_chroma"] / max(c_r, 1e-3)) ** s, 0.7, 1.4))
    un.chroma_scale = float(np.clip((t["chroma"] / max(c.mean(), 1e-3)) ** s, 0.8, 1.25))
    return un
