#!/usr/bin/env python3
"""Step 1 - Ingest: copy a GoPro card into the library, verify, and write a manifest.

    python scripts/ingest.py /Volumes/GOPRO --session "2026-10-31_quarry"

Creates  <library>/<session>/raw/        original MP4s (never modified again)
         <library>/<session>/manifest.csv  one row per clip: recording, chapter, res, fps, 10-bit, gyro
Flags clips that will not grade well (8-bit) or cannot be gyro-stabilized (no GPMF).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, media  # noqa: E402


def sha1(path: Path, chunk=8 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("card", type=Path, help="card root or any folder of GoPro files (searched recursively)")
    ap.add_argument("--session", default=date.today().isoformat(), help="session folder name")
    ap.add_argument("--config", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = config.load(args.config)
    ing = cfg["ingest"]
    dest = cfg["paths"]["library"] / args.session
    raw = dest / "raw"
    if not args.dry_run:
        raw.mkdir(parents=True, exist_ok=True)

    wanted = {"MP4"} | ({"LRV"} if ing["keep_lrv"] else set()) | ({"THM"} if ing["keep_thm"] else set())
    files = sorted(p for p in args.card.rglob("*") if p.is_file() and (n := media.parse_gopro_name(p.name)) and n.ext in wanted)
    if not files:
        print(f"No GoPro files found under {args.card}")
        return 1

    rows = []
    for src in files:
        name = media.parse_gopro_name(src.name)
        target = raw / src.name
        if args.dry_run:
            print(f"would copy {src} -> {target}")
        elif target.exists() and target.stat().st_size == src.stat().st_size:
            print(f"skip (already ingested) {src.name}")
        else:
            print(f"copy {src.name}  ({src.stat().st_size / 1e9:.2f} GB)")
            shutil.copy2(src, target)
            if ing["verify_checksums"] and sha1(src) != sha1(target):
                print(f"  CHECKSUM MISMATCH on {src.name} - do not format the card", file=sys.stderr)
                return 2
        if name.ext != "MP4":
            continue
        info = media.video_summary(media.ffprobe(target if target.exists() else src))
        warn = []
        if not info["ten_bit"]:
            warn.append("8-bit: limited grading latitude")
        if not info["has_gpmf"]:
            warn.append("no gyro: Gyroflow cannot stabilize")
        rows.append({
            "file": src.name, "recording": name.recording, "chapter": name.chapter,
            **{k: info[k] for k in ("codec", "width", "height", "fps", "pix_fmt", "duration_s", "creation_time", "has_gpmf")},
            "warnings": "; ".join(warn),
        })

    if rows and not args.dry_run:
        with open(dest / "manifest.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\n{len(rows)} clips -> {dest / 'manifest.csv'}")
    recordings = {r["recording"] for r in rows}
    chaptered = [r for r in recordings if sum(1 for x in rows if x["recording"] == r) > 1]
    if chaptered:
        print(f"Chaptered recordings (split at 4 GB, keep together in the edit): {sorted(chaptered)}")
    for r in rows:
        if r["warnings"]:
            print(f"  ! {r['file']}: {r['warnings']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
