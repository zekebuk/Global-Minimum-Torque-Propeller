"""Scope-driven primary grading: simulate the node tree on sampled frames, solve CDLs that hit measurable targets.

Node model (matches docs/PIPELINE.md §4 and the DRX template):
    node 1  BAL    CDL offset only, in LOG, before the LUT   -> exposure + white balance
    node 2  LUT    camera log -> Rec.709 conversion (.cube)
    node 3  CONT   CDL slope/offset/power + saturation, in display space -> black/mid/white + saturation

Why node 1 is offset-only: a log curve maps linear light x to a*log2(x)+b, so adding k to every channel multiplies
light by 2^(k/a) (an exposure change) and adding different k per channel is a per-channel gain (a white-balance
change). Both are exact in log; neither is exact after the LUT.

Measurements are made after node 3 in Rec.709 display space (BT.709 luma/chroma), i.e. what the waveform and
vectorscope show. Everything is numpy; nothing here talks to Resolve.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

KR, KB = 0.2126, 0.0722
KG = 1 - KR - KB


# ------------------------------------------------------------------ LUT

@dataclass
class CubeLUT:
    table: np.ndarray            # (N, N, N, 3) indexed [b, g, r] as stored in .cube (red changes fastest)
    domain_min: np.ndarray
    domain_max: np.ndarray

    @property
    def size(self) -> int:
        return self.table.shape[0]


def load_cube(path: Path) -> CubeLUT:
    n, dmin, dmax, rows = None, np.zeros(3), np.ones(3), []
    for line in Path(path).read_text(errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        up = s.upper()
        if up.startswith("LUT_3D_SIZE"):
            n = int(s.split()[1])
        elif up.startswith("DOMAIN_MIN"):
            dmin = np.array([float(v) for v in s.split()[1:4]])
        elif up.startswith("DOMAIN_MAX"):
            dmax = np.array([float(v) for v in s.split()[1:4]])
        elif up.startswith(("TITLE", "LUT_1D", "LUT_3D_INPUT_RANGE")):
            if up.startswith("LUT_1D"):
                raise ValueError("1D LUTs are not supported; export a 3D .cube")
        elif s[0] in "-.0123456789":
            rows.append([float(v) for v in s.split()[:3]])
    if n is None or len(rows) != n ** 3:
        raise ValueError(f"{path}: expected {n}^3 rows, found {len(rows)}")
    return CubeLUT(np.asarray(rows, np.float64).reshape(n, n, n, 3), dmin, dmax)


def apply_lut(lut: CubeLUT, rgb: np.ndarray) -> np.ndarray:
    """Trilinear interpolation, same as Resolve's default 3D LUT interpolation."""
    n = lut.size
    x = (np.clip(rgb, lut.domain_min, lut.domain_max) - lut.domain_min) / (lut.domain_max - lut.domain_min) * (n - 1)
    i0 = np.clip(np.floor(x).astype(int), 0, n - 2)
    f = x - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    t = lut.table
    c = lambda db, dg, dr: t[b0 + db, g0 + dg, r0 + dr]
    c00 = c(0, 0, 0) * (1 - fr) + c(0, 0, 1) * fr
    c01 = c(0, 1, 0) * (1 - fr) + c(0, 1, 1) * fr
    c10 = c(1, 0, 0) * (1 - fr) + c(1, 0, 1) * fr
    c11 = c(1, 1, 0) * (1 - fr) + c(1, 1, 1) * fr
    c0 = c00 * (1 - fg) + c01 * fg
    c1 = c10 * (1 - fg) + c11 * fg
    return c0 * (1 - fb) + c1 * fb


def write_cube(path: Path, fn, n: int = 33, title: str = "generated"):
    g = np.linspace(0, 1, n)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    rgb = np.stack([r, gg, b], -1).reshape(-1, 3)
    out = np.clip(fn(rgb), 0, 1)
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {n}"] + [f"{a:.6f} {b_:.6f} {c:.6f}" for a, b_, c in out]
    Path(path).write_text("\n".join(lines) + "\n")


# ------------------------------------------------------------------ CDL + measurements

@dataclass
class CDL:
    slope: np.ndarray = field(default_factory=lambda: np.ones(3))
    offset: np.ndarray = field(default_factory=lambda: np.zeros(3))
    power: np.ndarray = field(default_factory=lambda: np.ones(3))
    sat: float = 1.0

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        out = np.clip(rgb * self.slope + self.offset, 0, None) ** self.power
        if self.sat != 1.0:
            y = luma(out)[..., None]
            out = y + self.sat * (out - y)
        return out

    def resolve_map(self, node: int) -> dict:
        fmt = lambda v: " ".join(f"{x:.5f}" for x in v)
        return {"NodeIndex": str(node), "Slope": fmt(self.slope), "Offset": fmt(self.offset),
                "Power": fmt(self.power), "Saturation": f"{self.sat:.5f}"}

    def to_dict(self) -> dict:
        return {"slope": self.slope.round(5).tolist(), "offset": self.offset.round(5).tolist(),
                "power": self.power.round(5).tolist(), "sat": round(float(self.sat), 5)}


def luma(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0] * KR + rgb[..., 1] * KG + rgb[..., 2] * KB


def chroma(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    y = luma(rgb)
    return (rgb[..., 2] - y) / (2 * (1 - KB)), (rgb[..., 0] - y) / (2 * (1 - KR))  # Cb, Cr (BT.709)


def neutral_mask(rgb: np.ndarray, frac: float = 0.15) -> np.ndarray:
    """Candidate neutrals: mid-tone pixels in the lowest `frac` of chroma. Re-selected every iteration, so a
    cast that pushes true neutrals away from the centre still converges (they stay the least saturated)."""
    y = luma(rgb)
    cb, cr = chroma(rgb)
    c = np.hypot(cb, cr)
    mid = (y > 0.15) & (y < 0.85)
    if mid.sum() < 50:
        mid = np.ones_like(y, bool)
    thr = np.quantile(c[mid], frac)
    return mid & (c <= thr)


def measure(rgb: np.ndarray, neutrals: np.ndarray | None = None) -> dict:
    y = luma(rgb)
    cb, cr = chroma(rgb)
    m = neutral_mask(rgb) if neutrals is None or neutrals.sum() < 20 else neutrals
    p = np.percentile(y, [0.5, 2, 50, 98, 99.5])
    c = np.hypot(cb, cr)
    return {
        "black_p0_5": float(p[0]), "black_p2": float(p[1]), "mid_p50": float(p[2]), "white_p98": float(p[3]),
        "white_p99_5": float(p[4]),
        "clipped_frac": float(np.mean(rgb.max(-1) >= 0.995)), "crushed_frac": float(np.mean(y <= 0.005)),
        "neutral_cb": float(cb[m].mean()), "neutral_cr": float(cr[m].mean()),
        "neutral_cast": float(np.hypot(cb[m].mean(), cr[m].mean())),
        "sat_mean": float(c.mean()), "sat_p95": float(np.percentile(c, 95)),
        # Saturation in bright areas (skies): heavy chroma there is what H.264/H.265 turns into blocks and banding.
        "hi_sat_p95": float(np.percentile(c[y > 0.7], 95)) if (y > 0.7).sum() > 50 else 0.0,
    }


def find_neutrals(log_rgb: np.ndarray, bin_w: float = 0.01, radius: float = 0.025, peaks: int = 5) -> dict:
    """Locate the achromatic cluster in LOG space.

    In a log encoding a white-balance error is a per-channel gain, i.e. a constant shift of d = (R-G, B-G) at every
    brightness. All neutral surfaces therefore share one d and span many stops (shadowed rock .. bright cloud);
    a coloured surface (sky, grass) is one d at one brightness band. So: find the d-clusters, prefer the one with the
    widest brightness spread, with a vote for the brightest unclipped pixels (clouds / speculars = the illuminant).
    Least-saturated-pixel heuristics fail exactly when it matters: under a warm cast the blue sky looks grey."""
    usable = (log_rgb.max(-1) < 0.96) & (log_rgb.min(-1) > 0.04)
    if usable.sum() < 200:
        return {"d": np.zeros(2), "mask": np.zeros(len(log_rgb), bool), "confidence": 0.0, "spread": 0.0}
    v = log_rgb[usable]
    d = np.stack([v[:, 0] - v[:, 1], v[:, 2] - v[:, 1]], 1)
    edges = np.arange(-0.3, 0.3 + bin_w, bin_w)
    h, _, _ = np.histogram2d(d[:, 0], d[:, 1], bins=[edges, edges])
    k = np.ones((3, 3)) / 9  # light smoothing so one cluster gives one peak
    hp = np.pad(h, 1)
    hs = sum(hp[i:i + h.shape[0], j:j + h.shape[1]] * k[i, j] for i in range(3) for j in range(3))
    cand = []
    hc = hs.copy()
    centers = (edges[:-1] + edges[1:]) / 2
    for _ in range(peaks):
        i, j = np.unravel_index(np.argmax(hc), hc.shape)
        if hc[i, j] <= 0:
            break
        c = np.array([centers[i], centers[j]])
        hc[max(0, i - 3):i + 4, max(0, j - 3):j + 4] = 0  # suppress the neighbourhood
        mem = np.hypot(*(d - c).T) <= radius
        if mem.sum() < 30:
            continue
        g = v[mem, 1]
        spread = float(np.percentile(g, 95) - np.percentile(g, 5))       # in log units ~ stops * slope
        bright = float(np.mean(v[mem].max(-1) >= np.percentile(v.max(-1), 97)))
        share = mem.mean()
        score = spread * (0.5 + bright) * np.sqrt(share)
        cand.append((score, c, mem, spread))
    if not cand:
        return {"d": np.zeros(2), "mask": np.zeros(len(log_rgb), bool), "confidence": 0.0, "spread": 0.0}
    cand.sort(key=lambda x: x[0], reverse=True)
    best = cand[0]
    centre = np.median(d[best[2]], axis=0)
    conf = 1.0 if len(cand) == 1 else float(1 - cand[1][0] / max(best[0], 1e-9))
    mask = np.zeros(len(log_rgb), bool)
    mask[np.flatnonzero(usable)[best[2]]] = True
    return {"d": centre, "mask": mask, "confidence": round(conf, 3), "spread": round(best[3], 3)}


# ------------------------------------------------------------------ targets

LOOKS = {
    # Reels: deeper blacks, brighter, more saturated - survives phone screens and heavy compression.
    "reel": {"black": 0.025, "mid": 0.44, "white": 0.95, "sat_mult": 1.18, "sat_max": 0.16, "hi_sat_max": 0.11},
    # YouTube: natural, open shadows, gentler saturation for long viewing on big screens.
    "youtube": {"black": 0.04, "mid": 0.42, "white": 0.93, "sat_mult": 1.05, "sat_max": 0.13, "hi_sat_max": 0.09},
}
# Node orders. "scene" (default, professional practice): balance, contrast and saturation happen in camera log, the
# log->709 LUT is the output transform near the end, so its highlight roll-off shapes the contrast. "display": the
# original layout (LUT on node 2, contrast after it in display space).
ORDERS = ("scene", "display")


# ------------------------------------------------------------------ solver

def nelder_mead(f, x0, step, iters=200, tol=1e-7):
    n = len(x0)
    pts = [np.asarray(x0, float)] + [np.asarray(x0, float) + np.eye(n)[i] * step[i] for i in range(n)]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = np.argsort(vals)
        pts, vals = [pts[i] for i in order], [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) < tol:
            break
        c = np.mean(pts[:-1], axis=0)
        xr = c + (c - pts[-1]); fr = f(xr)
        if fr < vals[0]:
            xe = c + 2 * (c - pts[-1]); fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = c + 0.5 * (pts[-1] - c); fc = f(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                pts = [pts[0]] + [pts[0] + 0.5 * (p - pts[0]) for p in pts[1:]]
                vals = [vals[0]] + [f(p) for p in pts[1:]]
    i = int(np.argmin(vals))
    return pts[i], vals[i]


@dataclass
class Grade:
    bal: CDL           # BAL node (log)
    cont: CDL          # CONT node (log in "scene" order, display in "display" order)
    before: dict
    after: dict
    notes: list[str]
    order: str = "scene"


def render(log_rgb: np.ndarray, lut: CubeLUT, bal: CDL, cont: CDL, order: str = "scene") -> np.ndarray:
    v = np.clip(bal.apply(log_rgb), 0, 1)
    if order == "scene":
        return np.clip(apply_lut(lut, np.clip(cont.apply(v), 0, 1)), 0, 1)
    return np.clip(cont.apply(np.clip(apply_lut(lut, v), 0, 1)), 0, 1)


def solve(log_rgb: np.ndarray, lut: CubeLUT, look: str | dict = "youtube", match: dict | None = None,
          max_offset: float = 0.15, order: str = "scene") -> Grade:
    """log_rgb: (N,3) pixels sampled from the clip, as Resolve sees them (0-1, full range, camera log)."""
    if order not in ORDERS:
        raise ValueError(f"order must be one of {ORDERS}")
    t = dict(LOOKS[look]) if isinstance(look, str) else dict(look)
    notes = []
    nz = find_neutrals(log_rgb)
    nmask = nz["mask"]
    before = measure(render(log_rgb, lut, CDL(), CDL(), order), nmask)
    if match:  # shot matching: aim at another clip's measured result instead of the look's absolutes
        t.update(black=match["black_p0_5"], mid=match["mid_p50"], white=match["white_p99_5"])
        t["sat_target"] = match["sat_mean"]

    # Small fixed subsample for the iterative stages (quantile targets are stable at a few thousand pixels).
    rng = np.random.default_rng(0)
    idx = rng.choice(len(log_rgb), min(len(log_rgb), 6000), replace=False)
    sub, smask = log_rgb[idx], nmask[idx]

    # Stage 1a: white balance = cancel the neutral cluster's log-chromaticity offset (R and B relative to G).
    wb = -nz["d"]
    if nz["confidence"] < 0.15 or nz["spread"] < 0.05:
        wb = wb * 0.5
        notes.append(f"white balance only half-applied: no clear neutral surfaces (confidence {nz['confidence']:.2f}); "
                     "for sunsets/forests use --match with a hero clip or set WB by eye")
    wb = np.clip(wb, -max_offset, max_offset)

    # Stage 1b: exposure = common log offset that puts the median luma on target (an exact exposure change in log).
    def bal_of(e):
        return CDL(offset=np.array([e + wb[0], e, e + wb[1]]))
    x1, _ = nelder_mead(lambda x: (np.median(luma(render(sub, lut, bal_of(x[0]), CDL(), order))) - t["mid"]) ** 2,
                        [0.0], [0.03], iters=80)
    e = float(np.clip(x1[0], -max_offset, max_offset))
    bal = bal_of(e)

    # Stage 2: black / mid / white with one slope, offset, power (same for R,G,B so balance is untouched).
    # Bounds are enforced INSIDE the objective, so a low-contrast scene gets the best feasible curve with the mid
    # still on target instead of a clamped unconstrained optimum. Priority: mid > white > black.
    # In "scene" order the curve acts on log values and is measured through the LUT, so the LUT's own highlight
    # roll-off does the shoulder (what colorists mean by grading "under the transform").
    if order == "scene":
        lo, hi = np.array([0.7, -0.15, 0.8]), np.array([1.5, 0.15, 1.25])
        pivot = float(np.median(luma(np.clip(bal.apply(sub), 0, 1))))
        def cont_of(x):
            s_, o_, p_ = np.clip(x, lo, hi)
            return CDL(slope=np.full(3, s_), offset=np.full(3, o_ + pivot * (1 - s_)), power=np.full(3, p_))
    else:
        lo, hi = np.array([0.7, -0.1, 0.7]), np.array([1.6, 0.1, 1.4])
        def cont_of(x):
            s_, o_, p_ = np.clip(x, lo, hi)
            return CDL(slope=np.full(3, s_), offset=np.full(3, o_), power=np.full(3, p_))
    def f2(x):
        q = np.percentile(luma(render(sub, lut, bal, cont_of(x), order)), [0.5, 50, 99.5, 100])
        pen = float(np.sum(np.maximum(0, lo - x) + np.maximum(0, x - hi))) * 10
        over = max(0.0, q[3] - 0.999)
        return (q[1] - t["mid"]) ** 2 * 120 + (q[2] - t["white"]) ** 2 * 6 + (q[0] - t["black"]) ** 2 * 3 + over * 2 + pen
    best = None
    for x0 in ([1.0, 0.0, 1.0], [1.25, -0.03, 1.0], [0.85, 0.03, 1.0]):   # a few starts: the objective has kinks
        x, v = nelder_mead(f2, x0, [0.05, 0.01, 0.05], iters=200)
        if best is None or v < best[1]:
            best = (x, v)
    cont = cont_of(best[0])
    reached = measure(render(sub, lut, bal, cont, order), smask)
    if abs(reached["black_p0_5"] - t["black"]) > 0.03 or abs(reached["white_p99_5"] - t["white"]) > 0.04:
        notes.append(f"scene range too small for the {look if isinstance(look, str) else 'target'} contrast within safe "
                     f"limits (blacks {reached['black_p0_5']:.2f}, whites {reached['white_p99_5']:.2f}): haze/flat light; "
                     "add contrast by eye or use a dehaze/contrast node")

    # Stage 3: saturation toward the look's level, with two caps: overall (no blooming colours) and in the
    # highlights (skies): bright saturated areas are what the platform encoder breaks into blocks/banding.
    pre = reached
    target_sat = t.get("sat_target", pre["sat_mean"] * t["sat_mult"])
    def f3(x):
        c = CDL(slope=cont.slope, offset=cont.offset, power=cont.power, sat=float(x[0]))
        m = measure(render(sub, lut, bal, c, order), smask)
        pen = max(0.0, m["sat_p95"] - t.get("sat_max", 0.16) * 1.6) * 20 + max(0.0, m["hi_sat_p95"] - t["hi_sat_max"]) * 20
        return (m["sat_mean"] - target_sat) ** 2 * 100 + pen
    x3, _ = nelder_mead(f3, [target_sat / max(pre["sat_mean"], 1e-4)], [0.05], iters=60)
    cont.sat = float(np.clip(x3[0], 0.6, 1.5))

    after = measure(render(log_rgb, lut, bal, cont, order), nmask)
    after["neutral_confidence"] = nz["confidence"]
    if after["sat_mean"] < target_sat * 0.9:
        notes.append("saturation held back to protect bright areas/skies from compression banding (raise it in "
                     "Hue vs Sat on mid-tones only if you need more)")
    if after["clipped_frac"] > 0.02:
        notes.append(f"{after['clipped_frac']:.1%} still clipped: the source highlights are blown (exposure at capture)")
    if abs(e) >= max_offset * 0.99:
        notes.append("exposure correction hit its limit: footage is very far off, check it by eye")
    if after["neutral_cast"] > 0.01:
        notes.append("residual cast on neutrals: scene may have no true neutrals (sunset, forest) - judge by eye")
    return Grade(bal, cont, before, after, notes, order)
