#!/usr/bin/env python3
"""Measure reference reels (ones you saved yourself) and turn them into editing targets.

    python scripts/analyze_references.py ~/FPV/References            # folder of reels
    python scripts/analyze_references.py ref1.mp4 ref2.mp4 --compare ~/FPV/Exports/FPV_x/REEL_final.mp4

Per reel: length, number of shots, cuts per 10 s, median / shortest / longest shot, time to first cut (the hook),
whether pace speeds up toward the end, share of cuts on a beat (vs. what random cutting would give), how many
beats per shot, and the grade's black level, contrast and saturation.
Then: a combined "target" over all references, written to references_profile.json, plus a side-by-side with
your own reel if --compare is given. Screen recordings work; crop out any UI first or the look stats skew.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import beats, cuts, media  # noqa: E402

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}
KEYS = ["duration_s", "shots", "cuts_per_10s", "shot_len_median_s", "shot_len_p10_s", "shot_len_p90_s",
        "first_cut_s", "pace_first_third_s", "pace_last_third_s", "tempo_bpm", "cuts_on_beat_frac",
        "chance_level_frac", "cut_every_beats_median", "black_p2", "contrast_range", "saturation_mean"]


def analyse(path: Path, threshold: float) -> dict:
    info = media.video_summary(media.ffprobe(path))
    dur = info["duration_s"]
    c = cuts.detect_cuts(path, threshold)
    row = {"file": path.name, "duration_s": round(dur, 1), "fps": info["fps"], **cuts.shot_stats(c, dur)}
    try:
        y, sr = media.decode_audio(path)
        if len(y) > sr * 3:
            bm = beats.analyse(y, sr)
            row["tempo_bpm"] = bm.tempo_bpm
            row.update(cuts.beat_alignment(c, bm.beats_s, info["fps"] or 30))
    except Exception:
        row["audio"] = "none"
    mids = [(a + b) / 2 for a, b in zip([0.0] + c, c + [dur])][:12]
    row.update(cuts.look_stats(path, mids))
    row["cuts_s"] = c
    return row


def summarize(rows: list[dict]) -> dict:
    out = {}
    for k in KEYS:
        vals = [r[k] for r in rows if isinstance(r.get(k), (int, float))]
        if vals:
            out[k] = round(median(vals), 2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--compare", type=Path, help="your own reel to compare against the references")
    ap.add_argument("--threshold", type=float, default=0.35, help="scene-change threshold (lower = more cuts)")
    args = ap.parse_args()

    files = []
    for p in args.inputs:
        files += sorted(x for x in p.iterdir() if x.suffix.lower() in VIDEO_EXT) if p.is_dir() else [p]
    rows = []
    for f in files:
        try:
            rows.append(analyse(f, args.threshold))
        except Exception as e:
            print(f"! {f.name}: {e}", file=sys.stderr)
            continue
        r = rows[-1]
        onbeat = (f"{r['cuts_on_beat_frac']:.0%} of cuts on beat (chance {r['chance_level_frac']:.0%})"
                  if "cuts_on_beat_frac" in r else "no beat data")
        print(f"{r['file']}: {r['duration_s']}s, {r.get('shots')} shots, median shot {r.get('shot_len_median_s')}s, "
              f"first cut {r.get('first_cut_s')}s, {onbeat}, saturation {r.get('saturation_mean')}")
    if not rows:
        return 1
    target = summarize(rows)
    out_dir = files[0].parent
    (out_dir / "references_profile.json").write_text(json.dumps({"target": target, "reels": rows}, indent=2))
    print("\nTarget (median of references):")
    for k, v in target.items():
        print(f"  {k:<24} {v}")
    if args.compare:
        mine = analyse(args.compare, args.threshold)
        print(f"\nYour reel vs. target ({args.compare.name}):")
        for k, v in target.items():
            if isinstance(mine.get(k), (int, float)):
                print(f"  {k:<24} {mine[k]:>8}  vs {v}")
    print(f"\nWrote {out_dir / 'references_profile.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
