#!/usr/bin/env python3
"""Catalogue an existing footage archive (READ-ONLY), find its best moments, and audit the backup.

    python scripts/scan_archive.py --root SSD=E:\\ --root HDD=F:\\FPV --out D:\\FPV\\Catalog

Never writes, moves or renames anything on the scanned drives; output goes only to --out.
Fast on spinning drives: telemetry is read via the MP4 index (a few hundred KB per file), not by decoding video.
Incremental: re-runs skip files whose path, size and modification time are unchanged.

Writes to --out:
  catalog.csv        one row per clip: drive, path, camera, profile, 10-bit, res/fps, length, in-camera stab, lens,
                     shot date (GPS time when present), location, shutter/ISO, highlight counts and best score
  best_moments.csv   the top highlight windows across the whole archive (reel backlog), with file + timestamp
  sessions.csv       clips grouped into shoot days per camera and place, ranked by how many good moments they hold
  backup_audit.txt   files present on only one drive (matched by size + sampled hash, so renamed copies still match)
Next: ingest.py <folder of a chosen session> --session <name> copies just that session onto the SSD library.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import camera, config, gpmf, highlights, media  # noqa: E402

VIDEO_EXT = {".mp4", ".mov"}
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", ".Trashes", ".Spotlight-V100", ".fseventsd"}


def quick_hash(path: Path, size: int, chunk: int = 1 << 20) -> str:
    """size + SHA-1 of the first and last MB: identifies copies across drives without reading whole files."""
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(chunk))
        if size > 2 * chunk:
            f.seek(size - chunk)
            h.update(f.read(chunk))
    return h.hexdigest()[:20]


def walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for n in filenames:
            if Path(n).suffix.lower() in VIDEO_EXT and not n.startswith("."):
                yield Path(dirpath) / n


def scan_clip(path: Path, cfg: dict) -> tuple[dict, list[dict]]:
    info = camera.identify(path, look=False)
    from statistics import median
    row = {
        "camera": f"{info.brand} {info.model}".strip(), "profile": info.profile, "bit_depth": info.bit_depth,
        "resolution": f"{info.width}x{info.height}", "fps": info.fps, "duration_s": round(info.duration_s, 1),
        "in_camera_stab": info.in_camera_stabilization, "lens": info.lens,
        "shot_utc": info.utc.isoformat() if info.utc else "", "time_source": info.time_source,
        "lat": round(info.lat, 5) if info.lat is not None else "", "lon": round(info.lon, 5) if info.lon is not None else "",
        "shutter": f"1/{round(1 / median(info.shutter_s))}" if info.shutter_s else "",
        "iso": round(median(info.iso)) if info.iso else "",
    }
    moments = []
    if info.brand == "GoPro":
        try:
            tr = gpmf.parse(media.extract_gpmf(path))
            g, a = tr["GYRO"].array(), tr["ACCL"].array()
            if len(g):
                h = cfg["highlights"]
                w = highlights.score_windows(gpmf.timeline(tr["GYRO"], info.duration_s), g,
                                             gpmf.timeline(tr["ACCL"], info.duration_s), a, window_s=h["window_s"])
                best = highlights.pick(w, top_k=h["top_k"], min_gap_s=h["min_gap_s"])
                moments = [asdict(b) for b in best]
                row["smooth_s"] = round(sum(e - s for s, e in highlights.smooth_sections(w)), 1)
        except Exception as e:
            row["notes"] = f"telemetry: {e}"
    for lab in ("flip/roll", "dive/hang", "aggressive"):
        row["n_" + lab.split("/")[0]] = sum(1 for m in moments if m["label"] == lab)
    row["best_score"] = max((m["score"] for m in moments), default="")
    return row, moments


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", required=True, metavar="LABEL=PATH", help="drive/folder to scan")
    ap.add_argument("--out", type=Path, help="catalog folder (default <library>/../Catalog)")
    ap.add_argument("--top", type=int, default=100, help="how many best moments to list")
    ap.add_argument("--no-hash", action="store_true", help="skip the backup audit (no extra reads)")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    roots = []
    for r in args.root:
        label, _, p = r.partition("=")
        roots.append((label, Path(p or label)))
    out = args.out or cfg["paths"]["library"].parent / "Catalog"
    out.mkdir(parents=True, exist_ok=True)

    cat_path = out / "catalog.csv"
    old = {}
    if cat_path.exists():
        with open(cat_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                old[(r["path"], r["size"], r["mtime"])] = r
    mom_path = out / "moments_cache.csv"
    old_m = defaultdict(list)
    if mom_path.exists():
        with open(mom_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                old_m[r["path"]].append(r)

    rows, moments, n_new = [], [], 0
    for label, rp in roots:
        files = list(walk(rp))
        print(f"{label}: {len(files)} video files under {rp}")
        for i, p in enumerate(files, 1):
            st = p.stat()
            key = (str(p), str(st.st_size), str(int(st.st_mtime)))
            if key in old:
                rows.append(old[key])
                moments += old_m.get(str(p), [])
                continue
            n_new += 1
            print(f"  [{i}/{len(files)}] {p.name}", end="\r", flush=True)
            base = {"drive": label, "path": str(p), "file": p.name, "size": st.st_size, "mtime": int(st.st_mtime)}
            try:
                row, ms = scan_clip(p, cfg)
            except Exception as e:
                row, ms = {"notes": f"unreadable: {e}"}, []
            if not args.no_hash:
                try:
                    row["qhash"] = quick_hash(p, st.st_size)
                except OSError as e:
                    row["notes"] = f"hash: {e}"
            rows.append({**base, **row})
            moments += [{"path": str(p), "file": p.name, "drive": label, **m} for m in ms]
        print()

    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(cat_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    mkeys = ["path", "file", "drive", "start_s", "end_s", "score", "rotation_deg", "peak_rate_dps", "freefall_frac", "label"]
    with open(mom_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=mkeys, extrasaction="ignore")
        w.writeheader()
        w.writerows(moments)

    # Best moments: one copy per physical clip (prefer the SSD/first root's copy when duplicates exist).
    by_hash = {}
    for r in rows:
        by_hash.setdefault(r.get("qhash") or r["path"], r["path"])
    keep = set(by_hash.values())
    ranked = sorted((m for m in moments if m["path"] in keep), key=lambda m: float(m["score"]), reverse=True)
    with open(out / "best_moments.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=mkeys, extrasaction="ignore")
        w.writeheader()
        w.writerows(ranked[: args.top])

    # Sessions: shoot day x camera x place (~1 km grid), ranked by good moments.
    sess = defaultdict(lambda: {"clips": 0, "minutes": 0.0, "moments": 0, "best": 0.0, "folders": set()})
    score_by_path = defaultdict(list)
    for m in moments:
        score_by_path[m["path"]].append(float(m["score"]))
    for r in rows:
        if r["path"] not in keep:
            continue
        day = (r.get("shot_utc") or "")[:10] or "unknown-date"
        place = f"{float(r['lat']):.2f},{float(r['lon']):.2f}" if r.get("lat") not in ("", None) else "no-gps"
        k = (day, r.get("camera", "?"), place)
        s = sess[k]
        s["clips"] += 1
        s["minutes"] += float(r.get("duration_s") or 0) / 60
        s["moments"] += len(score_by_path[r["path"]])
        s["best"] = max([s["best"]] + score_by_path[r["path"]])
        s["folders"].add(str(Path(r["path"]).parent))
    with open(out / "sessions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "camera", "place", "clips", "minutes", "good_moments", "best_score", "folders"])
        for k, s in sorted(sess.items(), key=lambda kv: (kv[1]["moments"], kv[1]["best"]), reverse=True):
            w.writerow([*k, s["clips"], round(s["minutes"], 1), s["moments"], round(s["best"], 2), " | ".join(sorted(s["folders"]))])

    # Backup audit.
    lines = []
    if not args.no_hash and len(roots) > 1:
        where = defaultdict(set)
        size_of = {}
        for r in rows:
            if r.get("qhash"):
                where[r["qhash"]].add(r["drive"])
                size_of[r["qhash"]] = (int(r["size"]), r["path"])
        labels = [l for l, _ in roots]
        for lab in labels:
            only = [h for h, d in where.items() if d == {lab}]
            gb = sum(size_of[h][0] for h in only) / 1e9
            lines.append(f"Only on {lab}: {len(only)} files, {gb:.1f} GB" + ("  <-- NOT BACKED UP" if only else ""))
            lines += [f"    {size_of[h][1]}" for h in sorted(only, key=lambda h: size_of[h][1])[:500]]
        (out / "backup_audit.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    total_gb = sum(int(r["size"]) for r in rows) / 1e9
    log_share = sum(1 for r in rows if "log" in str(r.get("profile", "")).lower()) / max(len(rows), 1)
    stab_on = sum(1 for r in rows if r.get("in_camera_stab") not in ("", "off", None))
    print(f"\n{len(rows)} clips ({total_gb:.0f} GB), {n_new} newly scanned -> {out}")
    print(f"  log profile: {log_share:.0%} of clips | in-camera stabilization on: {stab_on} clips | "
          f"highlight windows: {len(moments)}")
    for line in lines:
        if line.startswith("Only on"):
            print("  " + line)
    if ranked:
        print("  top moments:")
        for m in ranked[:5]:
            print(f"    {float(m['score']):.2f}  {m['label']:<10} {m['file']} @ {float(m['start_s']):.1f}s  ({m['drive']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
