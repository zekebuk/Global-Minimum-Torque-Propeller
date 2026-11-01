#!/usr/bin/env python3
"""Does a clip need a time-varying grade? Find light changes (AE lag, sun/shade), ignore content changes.

    python scripts/analyze_dynamics.py --clip D:\\FPV\\...\\GX010042.MP4
    python scripts/analyze_dynamics.py --from-resolve YT_final --markers      # every clip on V1, markers on timeline

Per clip: samples 4 frames/s across the used range, tracks ground exposure (sky excluded) and the illuminant
(neutral cluster), splits the clip into lighting sections with hysteresis, and gives each section a BAL-node
offset to ADD to the static auto grade (exposure partial, WB full). Most clips shot with locked exposure + WB need
nothing; the point is to find the ones that do.
Resolve's API cannot write grade keyframes, so --markers puts a Lavender timeline marker at every section start
with the numbers in the note: on the Color page, add a keyframe on the BAL node there and type the offsets.
Writes <exports>/grades/<name>/dynamics.json and one <clip>_dynamics.png (exposure + WB over time).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, dynamics as dy, media, scopes  # noqa: E402

FPS = 4.0


def sample_stream(path: Path, start: float, dur: float, width: int = 160) -> list[np.ndarray]:
    info = media.video_summary(media.ffprobe(path))
    h = int(round(info["height"] * width / max(info["width"], 1) / 2) * 2)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(path),
                          "-vf", f"fps={FPS},scale={width}:{h}", "-pix_fmt", "rgb48le", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    a = np.frombuffer(raw, "<u2").astype(np.float64) / 65535.0
    n = a.size // (width * h * 3)
    return [a[i * width * h * 3:(i + 1) * width * h * 3].reshape(h, width, 3) for i in range(n)]


def plot(t, exp, wb, secs, path: Path, w: int = 800, h: int = 120):
    """Two strips: ground exposure in stops (green) and WB R-G / B-G (red / blue); section starts in white."""
    def strip(series, colors, span):
        img = np.full((h, w, 3), 18, np.uint8)
        img[h // 2, :] = 60
        xs = np.linspace(t[0], t[-1], w) if len(t) > 1 else np.zeros(w)
        for y_, c in zip(series, colors):
            v = np.interp(xs, t, y_)
            rows = np.clip((h / 2 - v / span * (h / 2 - 4)).astype(int), 0, h - 1)
            for x in range(w):
                img[max(0, rows[x] - 1):rows[x] + 2, x] = c
        for s in secs[1:]:
            x = int((s.start_s - t[0]) / max(t[-1] - t[0], 1e-6) * (w - 1))
            img[:, max(0, x):x + 1] = 220
        return img
    e = (exp - np.median(exp)) / dy.LOG_STOP
    a = strip([e], [(80, 220, 80)], 2.0)                                     # +-2 stops full scale
    b = strip([wb[:, 0] - np.median(wb[:, 0]), wb[:, 1] - np.median(wb[:, 1])], [(230, 80, 80), (90, 120, 255)], 0.08)
    scopes.save_png(np.concatenate([a, np.full((4, w, 3), 40, np.uint8), b]), path)


def analyse(path: Path, start: float, dur: float, out_dir: Path, name: str) -> dict:
    frames = sample_stream(path, start, dur)
    if len(frames) < 8:
        return {"sections": [], "needs_keyframes": False, "note": "too short to analyse"}
    t = np.arange(len(frames)) / FPS
    exp, wb, conf = dy.signals(frames)
    secs = dy.segment(t, exp, wb, conf, FPS)
    need = dy.needs_keyframes(secs)
    out_dir.mkdir(parents=True, exist_ok=True)
    plot(t, exp, wb, secs, out_dir / f"{name}_dynamics.png")
    return {"needs_keyframes": need, "range_stops": round(float(np.ptp(exp) / dy.LOG_STOP), 2),
            "sections": [{"start_s": round(s.start_s, 2), "end_s": round(s.end_s, 2),
                          "exposure_stops": round(s.exposure_log / dy.LOG_STOP, 2),
                          "wb_shift": [round(float(v), 4) for v in s.wb],
                          "bal_offset_add": [round(float(v), 4) for v in s.correction]} for s in secs]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-resolve", metavar="TIMELINE")
    src.add_argument("--clip", type=Path, nargs="+")
    ap.add_argument("--markers", action="store_true", help="add timeline markers at section starts (Resolve)")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)

    jobs, tl, fps = [], None, 30.0
    if args.clip:
        for c in args.clip:
            jobs.append((c.stem, c, 0.0, media.video_summary(media.ffprobe(c))["duration_s"], None))
        tag = args.clip[0].stem
    else:
        from fpvpipe import resolve as rlib
        project = rlib.connect().GetProjectManager().GetCurrentProject()
        tl = next((project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)
                   if project.GetTimelineByIndex(i).GetName() == args.from_resolve), None)
        if not tl:
            raise SystemExit(f"timeline {args.from_resolve!r} not found")
        fps = float(tl.GetSetting("timelineFrameRate") or 30)
        for it in tl.GetItemListInTrack("video", 1) or []:
            mpi = it.GetMediaPoolItem()
            if mpi:
                jobs.append((f"{it.GetName()}@{it.GetStart()}", Path(mpi.GetClipProperty("File Path")),
                             it.GetLeftOffset() / fps, it.GetDuration() / fps, it))
        tag = args.from_resolve

    out_dir = cfg["paths"]["exports"] / "grades" / tag
    report, n_need = {}, 0
    for name, path, start, dur, item in jobs:
        try:
            r = analyse(path, start, dur, out_dir, name.replace("@", "_"))
        except Exception as e:
            print(f"! {name}: {e}")
            continue
        report[name] = r
        n_need += r["needs_keyframes"]
        flag = "KEYFRAMES" if r["needs_keyframes"] else "static ok"
        print(f"{name}: {flag}  (ground exposure range {r.get('range_stops', 0)} stops, {len(r['sections'])} section(s))")
        if r["needs_keyframes"]:
            for s in r["sections"]:
                print(f"    {s['start_s']:6.1f}s  exposure {s['exposure_stops']:+.2f} st  WB shift {s['wb_shift']}  "
                      f"-> add BAL offset {s['bal_offset_add']}")
            if args.markers and item is not None and tl is not None:
                t0 = item.GetStart() - tl.GetStartFrame()
                for s in r["sections"][1:]:
                    f = t0 + int(round(s["start_s"] * fps))
                    tl.AddMarker(f, "Lavender", "GRADE KEY",
                                 f"BAL offset add R/G/B {s['bal_offset_add']} (exp {s['exposure_stops']:+.2f} st); "
                                 f"ease ~0.5 s", 1)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dynamics.json").write_text(json.dumps(report, indent=2))
    print(f"\n{n_need}/{len(report)} clip(s) need keyframes -> {out_dir / 'dynamics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
