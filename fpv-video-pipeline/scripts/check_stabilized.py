#!/usr/bin/env python3
"""Step 3b-check - Verify GoPro Player / Gyroflow exports and name them so the Resolve builder picks them up.

    python scripts/check_stabilized.py 2026-10-31_quarry

For every video in <stabilized>/<session>/ (not _queue) it finds the original it came from (the export name starts
with the original's name, e.g. GX010042*.mp4), then checks that the export is safe to grade and edit:
  * still 10-bit (an 8-bit export throws away most of the log grading latitude)
  * same frame rate (a 59.94 -> 29.97 export loses your slow-motion headroom)
  * same duration within 0.5 s (otherwise the highlight markers land on the wrong frames)
  * codec suitable for editing (CineForm / ProRes / DNx are smooth; HEVC/H.264 work but play back slower)
Good files are renamed <ORIGINAL>_stabilized.<ext>. Problems are listed and the file is left alone.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, media  # noqa: E402

VIDEO_EXT = {".mp4", ".mov", ".mxf"}
EDIT_FRIENDLY = {"cfhd", "prores", "dnxhd"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session")
    ap.add_argument("--config", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = config.load(args.config)
    raw = cfg["paths"]["library"] / args.session / "raw"
    out_dir = cfg["paths"]["stabilized"] / args.session
    originals = {p.stem.upper(): p for p in raw.iterdir() if p.suffix.lower() in VIDEO_EXT}
    # Longest name first so GX010042 never matches an export of GX0100421 style names by accident.
    keys = sorted(originals, key=len, reverse=True)

    ok = bad = 0
    for exp in sorted(p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT):
        if exp.stem.upper().endswith("_STABILIZED") and exp.stem.upper()[:-11] in originals:
            src_key = exp.stem.upper()[:-11]
        else:
            src_key = next((k for k in keys if exp.stem.upper().startswith(k)), None)
        if not src_key:
            print(f"? {exp.name}: no matching original in {raw}")
            bad += 1
            continue
        a = media.video_summary(media.ffprobe(originals[src_key]))
        b = media.video_summary(media.ffprobe(exp))
        problems = []
        if a["ten_bit"] and not b["ten_bit"]:
            problems.append(f"exported 8-bit ({b['pix_fmt']}): pick CineForm/ProRes or 10-bit HEVC in the export preset")
        if abs(a["fps"] - b["fps"]) > 0.05:
            problems.append(f"frame rate {b['fps']} != source {a['fps']}")
        if abs(a["duration_s"] - b["duration_s"]) > 0.5:
            problems.append(f"duration {b['duration_s']:.1f}s != source {a['duration_s']:.1f}s (trimmed? markers will be off)")
        hint = "" if b["codec"] in EDIT_FRIENDLY else f" [{b['codec']}: fine, but CineForm edits smoother]"
        if problems:
            bad += 1
            print(f"✗ {exp.name}: " + "; ".join(problems))
            continue
        ok += 1
        target = exp.with_name(f"{originals[src_key].stem}_stabilized{exp.suffix.lower()}")
        if target != exp and not args.dry_run:
            if target.exists():
                print(f"✗ {exp.name}: {target.name} already exists, left as is")
                bad += 1
                ok -= 1
                continue
            exp.rename(target)
        print(f"✓ {exp.name} -> {target.name}  {b['width']}x{b['height']} {b['fps']}p {b['pix_fmt']}{hint}")
    print(f"\n{ok} ready, {bad} need attention. Next: build_resolve_project.py {args.session}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
