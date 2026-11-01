#!/usr/bin/env python3
"""Which camera shot each clip, in what profile, and which ND filter was on / should be on.

    python scripts/camera_report.py ~/FPV/Library/2026-10-31_quarry/raw
    python scripts/camera_report.py clip.MP4 --nd ND16            # you know what was on: sharper recommendation
    python scripts/camera_report.py DJI_0001.MP4 --lat 47.6 --lon -122.3 --utc-offset -7

Per clip it prints: camera model, color profile (GP-Log / D-Log M / ...), bit depth, lens, in-camera stabilization,
median shutter (as 1/x and shutter angle), ISO, white balance, then
  ND used (est.)  - from exposure vs. clear-sky sun brightness at that place and time (needs GPS or --lat/--lon)
  ND for 180 deg  - what to mount next time to hit 1/(2*fps) at base ISO in the same light
Writes camera_report.csv next to the clips. The math and its error bars are in fpvpipe/exposure.py.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import timedelta
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import camera, config, exposure  # noqa: E402

VIDEO_EXT = {".mp4", ".mov"}


def parse_nd(s: str | None) -> float | None:
    """'ND16' -> 4 stops, '4' -> 4 stops, 'none' -> 0."""
    if s is None:
        return None
    s = s.strip().upper()
    if s in ("NONE", "0", "ND0"):
        return 0.0
    m = re.fullmatch(r"ND(\d+)", s)
    if m:
        import math
        return math.log2(int(m.group(1)))
    return float(s)


def analyse(path: Path, args, cam_table: dict) -> dict:
    info = camera.identify(path)
    if info.time_source == "file creation_time" and info.utc and args.utc_offset is not None:
        # creation_time held local wall-clock time labelled as UTC: shift it to real UTC.
        info.utc = info.utc - timedelta(hours=args.utc_offset)
        info.time_source += f" (shifted {args.utc_offset:+g} h)"
    if args.lat is not None and args.lon is not None:
        info.lat, info.lon = args.lat, args.lon

    spec = {**camera.CAMERAS.get(info.model, {}), **cam_table.get(info.model, {})}
    f_number = info.settings.get("fnum") or spec.get("f_number")
    base_iso = spec.get("base_iso", 100)
    row = {
        "file": path.name, "camera": f"{info.brand} {info.model}".strip(), "firmware": info.firmware,
        "resolution": f"{info.width}x{info.height}", "fps": info.fps, "bit_depth": info.bit_depth,
        "profile": info.profile, "profile_source": info.profile_source, "lens": info.lens,
        "in_camera_stab": info.in_camera_stabilization,
    }
    notes = list(info.notes)
    nd_known = args.nd if args.nd is not None else args.nd_log.get(path.name.upper())
    if nd_known is not None:
        row["nd_logged"] = exposure.nd_name(nd_known)
    ev_scene = None
    if info.lat is not None and info.utc is not None:
        elev = exposure.sun_elevation_deg(info.lat, info.lon, info.utc)
        row["sun_elev_deg"] = round(elev, 1)
        ev_scene = exposure.clear_sky_ev(elev)
        if ev_scene is None:
            notes.append("sun below ~2 deg: clear-sky model not valid")
    if info.shutter_s and info.iso:
        t, iso = median(info.shutter_s), median(info.iso)
        angle = 360.0 * t * info.fps if info.fps else 0
        row.update(shutter=f"1/{round(1 / t)}", shutter_angle=round(angle), iso=round(iso),
                   wb_k=round(median(info.wb_k)) if info.wb_k else "")
        if f_number:
            ev_cam = exposure.ev100(f_number, t, iso)
            row["ev_camera"] = round(ev_cam, 1)
            nd_used = None
            if ev_scene is not None:
                nd_used = max(0.0, ev_scene - ev_cam)
                row["nd_used_est"] = f"{exposure.nd_name(nd_used)} ({nd_used:.1f} stops, clear-sky bound)"
                if nd_known is not None:
                    # Calibration: + means the model over-estimates (clouds/haze), - under (snow, bright sky in frame).
                    row["nd_est_error_stops"] = round(nd_used - nd_known, 2)
            fps = info.fps or 30
            if nd_known is not None:
                rec = exposure.recommend(t, iso, fps, nd_known, base_iso, args.angle)
                row["nd_recommended"] = (f"{rec['recommended_nd']} for {rec['target_shutter']} "
                                         f"({rec['extra_stops']:+.1f} stops vs. "
                                         f"{'no filter' if nd_known == 0 else 'the ' + exposure.nd_name(nd_known)})")
            else:
                bare = exposure.recommend(t, iso, fps, 0.0, base_iso, args.angle)
                row["nd_recommended"] = f"{bare['recommended_nd']} for {bare['target_shutter']} if no filter was on"
                if nd_used is not None and round(nd_used) >= 1:
                    est = exposure.recommend(t, iso, fps, nd_used, base_iso, args.angle)
                    row["nd_recommended"] += (f"; {est['recommended_nd']} if the estimated "
                                              f"{exposure.nd_name(nd_used)} was on (pass --nd to pick)")
            if t > 1.0 / max(info.fps, 1) * 0.99:
                notes.append("shutter at its slowest: scene was too dark for this ND, recommendation is a floor")
            if iso <= base_iso * 1.05 and angle < args.angle * 0.9:
                notes.append(f"shutter {angle:.0f} deg is faster than {args.angle:.0f} deg at base ISO: add ND")
        else:
            if nd_known is not None and ev_scene is not None:
                # Unknown aperture (e.g. Mission 1 Pro): EV_scene - ND = log2(N^2/t) - log2(S/100)  ->  solve for N.
                n_est = (t * 2 ** (ev_scene - nd_known) * iso / 100) ** 0.5
                row["implied_f_number"] = round(n_est, 2)
                notes.append(f"implied aperture ~f/{n_est:.1f} (clear sky assumed; average several clear-sky clips)")
            notes.append(f"aperture of {info.model} unknown: set [cameras.\"{info.model}\"] f_number in config.toml")
    else:
        notes.append("no per-frame shutter/ISO in this file")
    img = info.image
    if img:
        row.update(luma_median=img["luma_median"], clipped=img["clipped_frac"], looks_log=img["looks_log"])
        if img["clipped_frac"] > 0.02:
            notes.append(f"{img['clipped_frac']:.0%} of pixels clipped: the ND estimate reads low")
    row["notes"] = "; ".join(dict.fromkeys(notes))
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help="clips and/or folders")
    ap.add_argument("--nd", type=parse_nd, help="the filter you had on (ND16, 4, none)")
    ap.add_argument("--angle", type=float, default=180.0, help="target shutter angle (default 180)")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--utc-offset", type=float, help="camera clock offset from UTC in hours (e.g. -7 for PDT)")
    ap.add_argument("--log", type=Path, help="flight_log.csv: per-clip ND you actually had on (calibration)")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    args.nd_log = {}
    if args.log:
        with open(args.log, newline="") as f:
            for r in csv.DictReader(f):
                if r.get("file") and r.get("nd", "").strip():
                    args.nd_log[r["file"].strip().upper()] = parse_nd(r["nd"])
    cam_table = config.load(args.config).get("cameras", {})

    clips = []
    for p in args.inputs:
        clips += sorted(x for x in p.iterdir() if x.suffix.lower() in VIDEO_EXT) if p.is_dir() else [p]
    rows = []
    for c in clips:
        try:
            rows.append(analyse(c, args, cam_table))
        except Exception as e:
            print(f"! {c.name}: {e}", file=sys.stderr)
            continue
        r = rows[-1]
        print(f"\n{r['file']}  [{r['camera']}]  {r['resolution']} {r['fps']}p {r['bit_depth']}-bit  profile: {r['profile']}")
        print(f"  lens {r['lens'] or '?'} | in-camera stab {r['in_camera_stab'] or '?'} | "
              f"shutter {r.get('shutter', '?')} ({r.get('shutter_angle', '?')} deg) | ISO {r.get('iso', '?')} | WB {r.get('wb_k', '?')}K")
        if "nd_used_est" in r:
            print(f"  ND used (est.): {r['nd_used_est']}  [sun {r['sun_elev_deg']} deg]")
        if "nd_recommended" in r:
            print(f"  ND next time  : {r['nd_recommended']}")
        if r["notes"]:
            print(f"  notes: {r['notes']}")
    if rows:
        out = (args.inputs[0] if args.inputs[0].is_dir() else args.inputs[0].parent) / "camera_report.csv"
        keys = list(dict.fromkeys(k for r in rows for k in r))
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        print(f"\nWrote {out}")
    errs = [r["nd_est_error_stops"] for r in rows if "nd_est_error_stops" in r]
    if errs:
        bias = sum(errs) / len(errs)
        print(f"ND estimate vs. your log: mean error {bias:+.2f} stops over {len(errs)} clips "
              f"(spread {max(errs) - min(errs):.1f}); send me this to calibrate the model")
    apert = sorted(r["implied_f_number"] for r in rows if "implied_f_number" in r)
    if apert:
        print(f"implied aperture from {len(apert)} clips: median f/{apert[len(apert) // 2]:.2f}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
