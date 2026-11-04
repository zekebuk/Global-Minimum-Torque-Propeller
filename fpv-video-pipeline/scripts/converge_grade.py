#!/usr/bin/env python3
"""Grade each shot in a closed loop on what Resolve actually outputs, then add the unifying layer.

    python scripts/converge_grade.py reel.json --project SENJA_reel_4x3 --aspect 4:3            # per-clip loop
    python scripts/converge_grade.py reel.json --project SENJA_reel_4x3 --aspect 4:3 --unify 0.35   # then the layer

Per iteration, for every shot not yet on target: solve the 5-node grade, load it on the timeline clip, export three
stills from the Color page (so Gyroflow, sizing and every node are in the measurement), read the scopes, and adjust
mid (median), black (p1) and saturation (mean chroma). Highlights over the clip limit lower "log_gain" first, then
exposure. Each shot's targets live in the cut list as "target"; a shot that cannot reach them settles on its best
iteration, and the report says so. A cut list shot marked "manual" is left alone.

--unify solves shared anchors from the measured reel (median p5 / median / p95 / chroma and the reel's own warm hue,
not a preset) and pulls every shot part of the way to them on node 5.
The title track is switched off while measuring. LUTs go in a new folder per pass, so Resolve cannot serve a stale cube.
Never touches the timeline's cuts. Resolve Studio must be open on the project with the timeline current.
"""
from __future__ import annotations

import argparse
import copy
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_reel_project as b  # noqa: E402
from fpvpipe import config, cutlist as cl, fastpreview as fp, resolve as rlib  # noqa: E402

TOL = {"med": 0.012, "p1": 0.007, "chroma": 0.006}
DEFAULT = {"med": 0.25, "p1": 0.012, "chroma": 0.07, "clip": 0.005}


def timecode(f: int, rate: int = 30) -> str:
    return f"{f // (rate * 3600):02d}:{(f // (rate * 60)) % 60:02d}:{(f // rate) % 60:02d}:{f % rate:02d}"


def pixels(paths) -> np.ndarray:
    return np.vstack([np.asarray(Image.open(p).convert("RGB").resize((360, 270)), float).reshape(-1, 3) / 255 for p in paths])


def scopes(a: np.ndarray) -> dict:
    y = a @ [.2126, .7152, .0722]
    cb, cr = (a[:, 2] - y) / 1.8556, (a[:, 0] - y) / 1.5748
    c = np.hypot(cb, cr)
    hue = np.degrees(np.arctan2(cr, cb))
    warm = (np.abs(hue - 130) < 50) & (c > 0.03)
    return {"p1": round(float(np.percentile(y, 1)), 4), "p5": round(float(np.percentile(y, 5)), 4),
            "med": round(float(np.median(y)), 4), "p95": round(float(np.percentile(y, 95)), 4),
            "clip": round(float((y > .97).mean()), 4), "crush": round(float((y < .012).mean()), 4),
            "chroma": round(float(c.mean()), 4),
            "warm_hue": round(float(np.median(hue[warm])), 1) if warm.sum() > 50 else None,
            "warm_chroma": round(float(np.median(c[warm])), 4) if warm.sum() > 50 else None}


class Rig:
    def __init__(self, a):
        self.c = cl.CutList.load(a.cutlist)
        self.path, self.aspect, self.cfg = a.cutlist, a.aspect, config.load(a.config)
        self.cache = a.cutlist.parent / ".preview_cache"
        self.crop = b.shot_crop(self.c.shots[0], a.aspect)
        self.out = a.cutlist.parent / "converge"
        self.out.mkdir(exist_ok=True)
        self.resolve = rlib.connect()
        self.pm = self.resolve.GetProjectManager()
        self.p = self.pm.GetCurrentProject()
        if self.p.GetName() != a.project:
            raise SystemExit(f"Resolve is on {self.p.GetName()!r}, not {a.project!r}")
        self.tl = self.p.GetCurrentTimeline()
        self.wait = a.wait
        self.resolve.OpenPage("color")

    def load(self, ns: list[int], tag: str):
        sub = []
        for n in ns:
            s = self.c.shots[n - 1]
            fp._cst5_cube(Path(s.src), s, self.cache / "cubes", self.crop)
            sub.append(s)
        c2 = copy.copy(self.c)
        c2.shots = sub
        rel = b.stage_luts(c2, self.cache, f"{self.p.GetName()}_{tag}", self.aspect, self.cfg)
        self.p.RefreshLUTList()
        items = b.clips_on(self.tl)
        log = []
        b.load_grades(c2, [items[n - 1] for n in ns], rel, b.DEFAULT_TEMPLATE, log, True, True)
        for m in log:
            print("  !", m, flush=True)

    def measure(self, ns: list[int], tag: str) -> dict[int, dict]:
        items, res = b.clips_on(self.tl), {}
        for n in ns:
            it, paths = items[n - 1], []
            for q, fr in enumerate((.25, .5, .8)):
                self.tl.SetCurrentTimecode(timecode(it.GetStart() + int(it.GetDuration() * fr), int(round(self.c.fps))))
                time.sleep(self.wait)
                path = self.out / f"{tag}_{n:02d}_{q}.png"
                if self.p.ExportCurrentFrameAsStill(str(path)):
                    paths.append(path)
            if not paths:
                raise SystemExit("Resolve exported no stills: look for an open dialog")
            res[n] = scopes(pixels(paths))
        return res


def error(m: dict, t: dict) -> float:
    return sum(abs(m[k] - t[k]) / TOL[k] for k in TOL) + max(0.0, m["clip"] - t["clip"]) / 0.005


def converge(r: Rig, shots: list[int], iters: int) -> list[int]:
    todo, best, brackets = {n for n in shots if not r.c.shots[n - 1].manual}, {}, {}
    for it in range(1, iters + 1):
        if not todo:
            break
        ns = sorted(todo)
        r.load(ns, f"p{int(time.time()) % 100000}")
        time.sleep(3)
        sc = r.measure(ns, f"it{it}")
        for n in ns:
            s, m = r.c.shots[n - 1], sc[n]
            t = {**DEFAULT, **s.target}
            g, e = s.grade, error(m, t)
            if n not in best or e < best[n][0]:
                best[n] = (e, copy.deepcopy(g), m, it)
            ok = all(abs(m[k] - t[k]) <= TOL[k] for k in TOL) and m["clip"] <= t["clip"]
            print(f"it{it} shot {n:2d} {'OK' if ok else '  '} mid {g['mid']:.3f} black {g['black']:.3f} sat {g['sat_max']:.2f} "
                  f"gain {g.get('log_gain', 1):.2f} | med {m['med']:.3f} p1 {m['p1']:.3f} chroma {m['chroma']:.3f} "
                  f"clip {m['clip']:.4f} crush {m['crush']:.3f}", flush=True)
            if ok:
                todo.discard(n)
                continue
            if m["clip"] > t["clip"]:
                g["log_gain"] = round(max(0.75, g.get("log_gain", 1.0) - min(0.05, 2 * (m["clip"] - t["clip"]))), 3)
            g["mid"] = round(float(np.clip(g["mid"] - .8 * (m["med"] - t["med"]), .10, .42)), 4)
            # p1 can jump by 0.05 across a black change of 0.004 (the conversion cube's toe), so a proportional step
            # just flips between two states: bracket the target and bisect once both sides have been seen
            lo, hi = brackets.setdefault(n, [None, None])
            if m["p1"] < t["p1"]:
                lo = g["black"]
            else:
                hi = g["black"]
            brackets[n] = [lo, hi]
            if lo is not None and hi is not None and hi > lo:
                g["black"] = round((lo + hi) / 2, 5)
            else:
                g["black"] = round(float(np.clip(g["black"] - .7 * (m["p1"] - t["p1"]), 0, .08)), 5)
            sat = float(np.clip(g["sat_max"] * (t["chroma"] / max(m["chroma"], .01)) ** .8, .6, 1.3))   # past 1.3 shadows go red
            g["sat_min"] = g["sat_max"] = round(sat, 3)
    for n, (e, g, m, it) in best.items():
        r.c.shots[n - 1].grade = g
        print(f"settled shot {n}: iteration {it}, error {e:.2f}" + ("" if e < 3 else "  (off target: check by eye)"), flush=True)
    return sorted(best)


def unify(r: Rig, strength: float):
    ns = list(range(1, len(r.c.shots) + 1))
    before = r.measure(ns, "before_unify")

    def med(k):
        return float(np.median([v[k] for v in before.values() if v[k] is not None]))
    u = {"strength": strength, "p5": round(med("p5"), 4), "mid": round(med("med"), 4), "p95": round(med("p95"), 4),
         "chroma": round(med("chroma"), 4), "rock_hue": round(med("warm_hue"), 1), "rock_chroma": round(med("warm_chroma"), 4),
         "max_hue_rot": 4.0}
    print("unify anchors", u, flush=True)
    for s in r.c.shots:
        s.grade["unify"] = dict(u)
    r.load(ns, f"u{int(time.time()) % 100000}")
    time.sleep(3)
    after = r.measure(ns, "after_unify")
    for k in ("med", "p95", "chroma"):
        print(f"spread of {k}: {np.std([v[k] for v in before.values()]):.4f} -> {np.std([v[k] for v in after.values()]):.4f}", flush=True)
    return before, after


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cutlist", type=Path)
    ap.add_argument("--project", required=True)
    ap.add_argument("--aspect", default="4:3", choices=["8:7", "16:9", "9:16", "4:3", "uhd"])
    ap.add_argument("--shots", type=int, nargs="*", help="shot numbers (default: all; empty with --unify: none)")
    ap.add_argument("--iters", type=int, default=7)
    ap.add_argument("--unify", type=float, metavar="STRENGTH", help="then add the unifying layer at this strength (0-1)")
    ap.add_argument("--keep-unify", action="store_true", help="fix single shots under the reel's existing unifying node")
    ap.add_argument("--no-loop", action="store_true", help="just load the listed shots' grades from the cut list, no iterations")
    ap.add_argument("--wait", type=float, default=6.0, help="seconds for Resolve to finish a frame before each still")
    ap.add_argument("--config", type=Path)
    a = ap.parse_args()
    r = Rig(a)
    r.tl.SetTrackEnable("video", 2, False)
    try:
        shots = a.shots if a.shots is not None else list(range(1, len(r.c.shots) + 1))
        if shots:
            if not a.keep_unify:
                for s in (r.c.shots[n - 1] for n in shots):
                    s.grade["unify"] = False
            if not a.no_loop:
                converge(r, shots, a.iters)
            r.load(shots, f"settled{int(time.time()) % 100000}")
        if a.unify is not None:
            unify(r, a.unify)
    finally:
        r.tl.SetTrackEnable("video", 2, True)
        b.fade_out(r.c, r.tl)
        r.c.save(r.path)
        r.pm.SaveProject()
    print("saved", r.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
