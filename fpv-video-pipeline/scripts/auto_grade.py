#!/usr/bin/env python3
"""Scope-driven automatic primary grade: exposure, white balance, black/white point, contrast, saturation.

    # every clip on V1 of a timeline, written straight into Resolve (node template applied first):
    python scripts/auto_grade.py --from-resolve REEL_final --session 2026-11-02_quarry --look reel --template
    # match everything to one hero shot you graded/liked:
    python scripts/auto_grade.py --from-resolve YT_final --session ... --look youtube --match GX010042
    # offline check on one clip (no Resolve): scopes PNG + numbers
    python scripts/auto_grade.py --clip D:\\FPV\\Library\\...\\raw\\GX010042.MP4 --look reel

What it does per clip (details and the maths: docs/COLOR_ALGORITHM.md):
  1. samples 7 frames from the part of the clip actually used on the timeline
  2. simulates the node tree ([color.nodes]): scene order BAL (log) -> CONT (log) -> ... -> LUT (output transform);
     display order BAL (log) -> LUT -> CONT (display)
  3. white balance: finds the achromatic cluster in log space (widest brightness spread), cancels its offset
  4. exposure: common log offset that puts the median on target; then black/mid/white via slope/offset/power;
     then saturation to the look's level (capped)
  5. writes the BAL + CONT CDLs and the camera's LUT into the configured nodes via Resolve's SetCDL / SetLUT,
     plus <exports>/grades/<name>/<clip>.png (before/after: picture, waveform, RGB parade, vectorscope)
It's the technical base (a colourist's "balance" pass). Creative look, windows and secondaries stay yours.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import camera, colorsci as cs, config, media, scopes  # noqa: E402


def sample_frames(path: Path, start: float, dur: float, n: int = 7, width: int = 384) -> list[np.ndarray]:
    """Decode n frames as 16-bit RGB (full range, like Resolve's 'auto' data levels for video files)."""
    info = media.video_summary(media.ffprobe(path))
    h = int(round(info["height"] * width / max(info["width"], 1) / 2) * 2)
    frames = []
    for k in range(n):
        t = start + dur * (k + 0.5) / n
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                              "-vf", f"scale={width}:{h}", "-pix_fmt", "rgb48le", "-f", "rawvideo", "-"],
                             capture_output=True).stdout
        a = np.frombuffer(raw, "<u2").astype(np.float64) / 65535.0
        if a.size == width * h * 3:
            frames.append(a.reshape(h, width, 3))
    return frames


def pool(frames: list[np.ndarray], k: int = 60000, seed: int = 0) -> np.ndarray:
    px = np.concatenate([f.reshape(-1, 3) for f in frames])
    if len(px) > k:
        px = px[np.random.default_rng(seed).choice(len(px), k, replace=False)]
    return px


def lut_for(profile: str, cfg: dict, override: Path | None) -> Path | None:
    if override:
        return override
    p = cfg["color"]["luts"].get(profile) or cfg["color"]["conversion_lut"]
    return p if p and p.exists() else None


def grade_one(src: Path, profile: str, start: float, dur: float, lut_path: Path, look: str, match: dict | None,
              out_dir: Path, name: str, order: str = "scene") -> tuple[cs.Grade, Path]:
    lut = cs.load_cube(lut_path)
    frames = sample_frames(src, start, dur)
    if not frames:
        raise RuntimeError("could not decode frames")
    g = cs.solve(pool(frames), lut, look, match, order=order)
    mid = frames[len(frames) // 2]
    flat = mid.reshape(-1, 3)
    before = cs.render(flat, lut, cs.CDL(), cs.CDL(), order).reshape(mid.shape)
    after = cs.render(flat, lut, g.bal, g.cont, order).reshape(mid.shape)
    nb = (g.before["neutral_cb"], g.before["neutral_cr"])
    na = (g.after["neutral_cb"], g.after["neutral_cr"])
    img = np.concatenate([scopes.panel(before, "before", nb), np.full((6, scopes.panel(before).shape[1], 3), 40, np.uint8),
                          scopes.panel(after, "after", na)], 0)
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"{name}.png"
    scopes.save_png(img, png)
    return g, png


def summary(name: str, g: cs.Grade) -> str:
    b, a = g.before, g.after
    return (f"{name}: mid {b['mid_p50']:.2f}->{a['mid_p50']:.2f}  black {b['black_p0_5']:.3f}->{a['black_p0_5']:.3f}  "
            f"white {b['white_p99_5']:.2f}->{a['white_p99_5']:.2f}  cast {b['neutral_cast']:.3f}->{a['neutral_cast']:.3f} "
            f"(conf {a.get('neutral_confidence', 0):.2f})  sat x{g.cont.sat:.2f}"
            + ("".join(f"\n    ! {n}" for n in g.notes)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-resolve", metavar="TIMELINE")
    src.add_argument("--clip", type=Path, nargs="+")
    ap.add_argument("--look", choices=sorted(cs.LOOKS), default="youtube")
    ap.add_argument("--match", metavar="CLIP", help="grade this clip first, then match every other clip to it")
    ap.add_argument("--lut", type=Path, help="override the per-profile conversion LUT")
    ap.add_argument("--session", help="library session (manifest gives each clip's camera profile)")
    ap.add_argument("--template", action="store_true", help="apply [color].base_grade_drx (node template) first")
    ap.add_argument("--dry-run", action="store_true", help="compute + scopes only, don't write to Resolve")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    nodes = {"order": "scene", "bal": 1, "cont": 2, "lut": 6, **cfg["color"].get("nodes", {})}
    manifest = {}
    if args.session:
        mpath = cfg["paths"]["library"] / args.session / "manifest.csv"
        if mpath.exists():
            with open(mpath, newline="") as f:
                manifest = {Path(r["file"]).stem.upper(): r for r in csv.DictReader(f)}

    def profile_of(path: Path) -> str:
        key = path.stem.upper().split("_STABILI")[0]
        if key in manifest:
            return manifest[key]["profile"]
        return camera.identify(path).profile

    jobs = []  # (name, file, start, dur, profile, timeline item or None)
    handle = None
    if args.clip:
        for c in args.clip:
            d = media.video_summary(media.ffprobe(c))["duration_s"]
            jobs.append((c.stem, c, 0.0, d, profile_of(c), None))
        tag = args.clip[0].stem
    else:
        from fpvpipe import resolve as rlib
        project = rlib.connect().GetProjectManager().GetCurrentProject()
        tl = next((project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)
                   if project.GetTimelineByIndex(i).GetName() == args.from_resolve), None)
        if not tl:
            raise SystemExit(f"timeline {args.from_resolve!r} not found")
        project.SetCurrentTimeline(tl)
        fps = float(tl.GetSetting("timelineFrameRate") or 30)
        items = tl.GetItemListInTrack("video", 1) or []
        if args.template and not args.dry_run:
            drx = cfg["color"]["base_grade_drx"]
            if not (drx and drx.exists()):
                raise SystemExit("--template needs [color].base_grade_drx (see docs/COLOR_ALGORITHM.md: node template)")
            tl.ApplyGradeFromDRX(str(drx), 0, items)
        for it in items:
            mpi = it.GetMediaPoolItem()
            if not mpi:
                continue
            path = Path(mpi.GetClipProperty("File Path"))
            jobs.append((f"{it.GetName()}@{it.GetStart()}", path, it.GetLeftOffset() / fps, it.GetDuration() / fps,
                         profile_of(path), it))
        handle = tl
        tag = args.from_resolve

    out_dir = cfg["paths"]["exports"] / "grades" / tag
    if args.match:
        jobs.sort(key=lambda j: 0 if args.match.upper() in j[0].upper() or args.match.upper() in j[1].stem.upper() else 1)
        if not (args.match.upper() in jobs[0][0].upper() or args.match.upper() in jobs[0][1].stem.upper()):
            raise SystemExit(f"--match {args.match}: no such clip")
    results, hero = {}, None
    for name, path, start, dur, profile, item in jobs:
        lut = lut_for(profile, cfg, args.lut)
        if not lut:
            print(f"! {name}: no LUT for profile {profile!r} (add it under [color.luts] or pass --lut)")
            continue
        try:
            g, png = grade_one(path, profile, start, dur, lut, args.look, hero, out_dir, name.replace("@", "_"),
                               nodes["order"])
        except Exception as e:
            print(f"! {name}: {e}")
            continue
        if args.match and hero is None:
            hero = g.after
        print(summary(name, g) + f"\n    scopes: {png}")
        results[name] = {"file": str(path), "profile": profile, "lut": str(lut), "order": nodes["order"],
                         "bal": {"node": nodes["bal"], **g.bal.to_dict()}, "cont": {"node": nodes["cont"], **g.cont.to_dict()},
                         "before": g.before, "after": g.after, "notes": g.notes}
        if item is not None and not args.dry_run:
            graph = item.GetNodeGraph() if hasattr(item, "GetNodeGraph") else None
            n_nodes = graph.GetNumNodes() if graph and hasattr(graph, "GetNumNodes") else None
            nodes_cfg = nodes
            need = max(nodes_cfg["bal"], nodes_cfg["cont"], nodes_cfg["lut"])
            if n_nodes is not None and n_nodes < need:
                print(f"    ! only {n_nodes} node(s), template needs {need}: apply it (--template); skipped")
                continue
            ok = [item.SetLUT(nodes_cfg["lut"], str(lut)), item.SetCDL(g.bal.resolve_map(nodes_cfg["bal"])),
                  item.SetCDL(g.cont.resolve_map(nodes_cfg["cont"]))]
            print(f"    written to Resolve ({nodes_cfg['order']} order): LUT node {nodes_cfg['lut']}, "
                  f"BAL node {nodes_cfg['bal']}, CONT node {nodes_cfg['cont']}" if all(ok)
                  else f"    ! Resolve rejected part of the grade (LUT/BAL/CONT = {ok})")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "grades.json").write_text(json.dumps(results, indent=2))
    print(f"\n{len(results)} clip(s) graded -> {out_dir / 'grades.json'}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
