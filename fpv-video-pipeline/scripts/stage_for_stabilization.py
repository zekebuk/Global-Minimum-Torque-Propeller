#!/usr/bin/env python3
"""Step 3b-prep - Put exactly the clips worth stabilizing in one folder for GoPro Player's batch export.

    python scripts/stage_for_stabilization.py 2026-10-31_quarry            # clips that have highlight markers
    python scripts/stage_for_stabilization.py 2026-10-31_quarry --all      # every GoPro clip

Creates <stabilized>/<session>/_queue/ with hard links to the originals (no extra disk space, same drive) or copies
(different drive), plus QUEUE.txt listing what's in it and why. In GoPro Player you then select all files in
_queue, apply your HyperSmooth Pro preset once, and Batch Export into <stabilized>/<session>/.
Only GoPro HERO8+ files go in: HyperSmooth Pro does not process DJI footage.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config  # noqa: E402


def link_or_copy(src: Path, dst: Path) -> str:
    if dst.exists():
        return "exists"
    try:
        os.link(src, dst)
        return "linked"
    except OSError:
        shutil.copy2(src, dst)
        return "copied"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session")
    ap.add_argument("--all", action="store_true", help="queue every GoPro clip, not only ones with highlights")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)

    raw = cfg["paths"]["library"] / args.session / "raw"
    manifest = raw.parent / "manifest.csv"
    if not manifest.exists():
        raise SystemExit(f"{manifest} missing - run ingest.py first")
    with open(manifest, newline="") as f:
        rows = list(csv.DictReader(f))
    hl_file = raw / "highlights.json"
    hl = json.loads(hl_file.read_text()) if hl_file.exists() else {}
    if not hl and not args.all:
        raise SystemExit("no highlights.json - run find_highlights.py, or pass --all")

    queue = cfg["paths"]["stabilized"] / args.session / "_queue"
    queue.mkdir(parents=True, exist_ok=True)
    lines, total = [], 0.0
    for r in rows:
        if not r["camera"].startswith("GoPro"):
            continue
        h = hl.get(r["file"], {})
        reasons = [f"{x['label']}@{x['start_s']:.0f}s" for x in h.get("highlights", [])]
        if h.get("smooth"):
            reasons.append(f"{len(h['smooth'])} smooth stretch(es)")
        if not reasons and not args.all:
            continue
        # A chaptered recording must be stabilized as a whole: queue every chapter of it.
        chapters = [x for x in rows if x["recording"] and x["recording"] == r["recording"] and x["camera"] == r["camera"]]
        for c in chapters or [r]:
            status = link_or_copy(raw / c["file"], queue / c["file"])
            if status != "exists":
                total += float(c["duration_s"] or 0)
                lines.append(f"{c['file']}\t{status}\t{', '.join(reasons) or 'all'}")
    (queue / "QUEUE.txt").write_text("file\thow\twhy\n" + "\n".join(lines) + "\n")
    print(f"{len(lines)} clips ({total / 60:.1f} min of footage) staged in {queue}")
    print("Next: GoPro Player > open that folder > select all > apply your HyperSmooth Pro preset > Batch Export to "
          f"{queue.parent}  (then run check_stabilized.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
