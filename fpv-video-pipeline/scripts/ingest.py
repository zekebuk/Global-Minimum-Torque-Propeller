#!/usr/bin/env python3
"""Step 1 - Ingest: copy a GoPro / Mission 1 / DJI Avata 2 card into the library, verify, and write a manifest.

    python scripts/ingest.py /Volumes/GOPRO --session "2026-10-31_quarry"

Creates  <library>/<session>/raw/        original MP4s (never modified again)
         <library>/<session>/manifest.csv  one row per clip: camera, profile, recording, chapter, res, fps, 10-bit, gyro
Flags clips that will not grade well (8-bit) or cannot be gyro-stabilized (no gyro data).
DJI .SRT sidecars are copied with their clips. Run camera_report.py afterwards for shutter/ISO/ND.
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
from fpvpipe import camera, config, media  # noqa: E402

VIDEO = {"MP4", "MOV"}


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

    wanted = VIDEO | {"SRT"} | ({"LRV"} if ing["keep_lrv"] else set()) | ({"THM"} if ing["keep_thm"] else set())
    files = sorted(p for p in args.card.rglob("*")
                   if p.is_file() and not p.name.startswith(".") and p.suffix[1:].upper() in wanted)
    if not files:
        print(f"No video files found under {args.card}")
        return 1

    rows = []
    for src in files:
        name = media.parse_gopro_name(src.name)
        ext = src.suffix[1:].upper()
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
        if ext not in VIDEO:
            continue
        clip = target if target.exists() else src
        info = media.video_summary(media.ffprobe(clip))
        cam = camera.identify(clip)
        warn = []
        if not info["ten_bit"]:
            warn.append("8-bit: limited grading latitude")
        # DJI: Gyroflow needs EIS off and the Wide FOV (DJI Avata 2 FAQ).
        has_gyro = info["has_gpmf"] or (cam.brand == "DJI" and cam.in_camera_stabilization in ("", "off"))
        if not has_gyro:
            warn.append("Gyroflow cannot stabilize (no gyro data, or DJI EIS was on)")
        if cam.in_camera_stabilization not in ("", "off"):
            warn.append(f"in-camera stabilization was on ({cam.in_camera_stabilization}): less Gyroflow headroom")
        if cam.brand == "DJI" and cam.profile.startswith("Normal"):
            warn.append("8-bit Normal: switch to D-Log M for grading")
        rows.append({
            "file": src.name, "camera": f"{cam.brand} {cam.model}", "profile": cam.profile, "lens": cam.lens,
            "recording": name.recording if name else "", "chapter": name.chapter if name else "",
            **{k: info[k] for k in ("codec", "width", "height", "fps", "pix_fmt", "duration_s", "creation_time")},
            "has_gyro": has_gyro,
            "warnings": "; ".join(warn),
        })

    if rows and not args.dry_run:
        with open(dest / "manifest.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\n{len(rows)} clips -> {dest / 'manifest.csv'}")
    recordings = {r["recording"] for r in rows if r["recording"] != ""}
    chaptered = [r for r in recordings if sum(1 for x in rows if x["recording"] == r) > 1]
    if chaptered:
        print(f"Chaptered recordings (split at 4 GB, keep together in the edit): {sorted(chaptered)}")
    for r in rows:
        if r["warnings"]:
            print(f"  ! {r['file']}: {r['warnings']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
