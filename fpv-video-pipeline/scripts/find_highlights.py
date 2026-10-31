#!/usr/bin/env python3
"""Step 2 - Shortlist: rank every clip's most "epic" moments from the GoPro's own gyro/accelerometer.

    python scripts/find_highlights.py ~/FPV/Library/2026-10-31_quarry/raw

Writes highlights.json next to the clips:
  { "GX010042.MP4": { "highlights": [...reel candidates...], "smooth": [[start, end], ...] } }
build_resolve_project.py turns these into colored clip markers in Resolve:
  red = flip/roll, fuchsia = dive/hang time, yellow = aggressive, green = smooth stretch (YouTube).
Run it on the ORIGINAL GoPro files: re-encoded/stabilized intermediates no longer carry telemetry.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, gpmf, highlights, media  # noqa: E402


def analyse_clip(path: Path, cfg: dict) -> dict:
    info = media.video_summary(media.ffprobe(path))
    tracks = gpmf.parse(media.extract_gpmf(path))
    gyro, acc = tracks["GYRO"].array(), tracks["ACCL"].array()
    t_g = gpmf.timeline(tracks["GYRO"], info["duration_s"])
    t_a = gpmf.timeline(tracks["ACCL"], info["duration_s"])
    h = cfg["highlights"]
    windows = highlights.score_windows(t_g, gyro, t_a, acc, window_s=h["window_s"])
    best = highlights.pick(windows, top_k=h["top_k"], min_gap_s=h["min_gap_s"])
    return {
        "duration_s": info["duration_s"],
        "fps": info["fps"],
        "highlights": [asdict(b) for b in best],
        "smooth": highlights.smooth_sections(windows),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)

    results = {}
    for clip in sorted({p for p in args.folder.iterdir() if p.suffix.lower() == ".mp4"}):
        try:
            results[clip.name] = analyse_clip(clip, cfg)
        except Exception as e:  # keep going: one bad clip should not stop the batch
            print(f"  ! {clip.name}: {e}", file=sys.stderr)
            continue
        for hl in results[clip.name]["highlights"]:
            print(f"{clip.name}  {hl['start_s']:7.1f}s  {hl['label']:<10}  score {hl['score']:.2f}  "
                  f"rot {hl['rotation_deg']:.0f}deg  peak {hl['peak_rate_dps']:.0f}deg/s")

    out = args.folder / "highlights.json"
    out.write_text(json.dumps(results, indent=2))
    ranked = sorted(((hl["score"], name, hl["start_s"]) for name, r in results.items() for hl in r["highlights"]), reverse=True)
    print(f"\nWrote {out}\nTop 10 reel candidates across the session:")
    for score, name, start in ranked[:10]:
        print(f"  {score:.2f}  {name} @ {start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
