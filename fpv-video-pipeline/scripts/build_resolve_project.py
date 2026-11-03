#!/usr/bin/env python3
"""Step 3b - Build the Resolve project for a session (Resolve Studio must be open).

    python scripts/build_resolve_project.py 2026-10-31_quarry --music ~/FPV/Music/track.mp3

What it does, in order:
  1. Creates (or opens) project FPV_<session>; 3840x2160 @ source fps, DaVinci YRGB (LUT-based color, section 4).
  2. Bins: 01_Footage (Gyroflow intermediates if present, else raw GoPro), 02_Music, 03_SFX.
  3. Clip markers from highlights.json (red flip/roll, fuchsia dive, yellow aggressive, green smooth).
  4. Timeline YT_selects   - 16:9 4K, every clip in shooting order (chapters kept together).
     Timeline REEL_selects - 9:16 1080x1920, a string-out of the highlight windows, best first,
                             with beat/downbeat/drop markers from <track>.beats.json.
  5. Base grade on every clip: the .drx in config (preferred) or the GP-Log conversion LUT on node 1.
  6. Gyroflow ([gyroflow] mode = "ofx"): the OpenFX plugin on every clip without a stabilized export,
     [gyroflow.youtube] settings on YT_selects, [gyroflow.reel] on REEL_selects.
Re-running is safe: existing timelines are left alone and a new suffixed one is made.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, gyroflow, resolve as rlib  # noqa: E402

# Resolve marker colors: Blue Cyan Green Yellow Red Pink Purple Fuchsia Rose Lavender Sky Mint Lemon Sand Cocoa Cream
MARKER_COLOR = {"flip/roll": "Red", "dive/hang": "Fuchsia", "aggressive": "Yellow"}
VIDEO_EXT = {".mp4", ".mov", ".mxf"}


def try_set(obj, key, value, log):
    ok = obj.SetSetting(key, str(value))
    if not ok:
        log.append(f"could not set {key}={value} (set it by hand in Project/Timeline Settings)")
    return ok


def footage_for(session_dir: Path, stabilized_root: Path) -> list[Path]:
    """Per clip: the stabilized export if there is one (Gyroflow or GoPro Player), else the original."""
    raw = sorted({p for p in (session_dir / "raw").iterdir() if p.suffix.lower() in VIDEO_EXT})
    stab_dir = stabilized_root / session_dir.name
    stab = {}
    if stab_dir.is_dir():
        for p in stab_dir.iterdir():
            if p.is_file() and p.suffix.lower() in VIDEO_EXT:
                stab[clip_key(p.name)] = p
    return [stab.get(clip_key(p.name), p) for p in raw]


def clip_key(name: str) -> str:
    """'GX010042_stabilized.mov' and 'GX010042.MP4' -> 'GX010042'; works for DJI_..._D names too."""
    stem = Path(name).stem
    return re.sub(r"_stabili[sz]ed.*$", "", stem, flags=re.IGNORECASE).upper()


def load_manifest(session_dir: Path) -> dict[str, dict]:
    m = session_dir / "manifest.csv"
    if not m.exists():
        return {}
    with open(m, newline="") as f:
        return {clip_key(r["file"]): r for r in csv.DictReader(f)}


def unique_timeline(media_pool, project, name):
    existing = {project.GetTimelineByIndex(i).GetName() for i in range(1, project.GetTimelineCount() + 1)}
    n, final = 2, name
    while final in existing:
        final, n = f"{name}_{n}", n + 1
    return media_pool.CreateEmptyTimeline(final)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session")
    ap.add_argument("--music", type=Path, help="licensed track for the reel (run beat_map.py on it first)")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()

    cfg = config.load(args.config)
    log: list[str] = []
    session_dir = cfg["paths"]["library"] / args.session
    clips = footage_for(session_dir, cfg["paths"]["stabilized"])
    hl_file = session_dir / "raw" / "highlights.json"
    hl = {clip_key(k): v for k, v in json.loads(hl_file.read_text()).items()} if hl_file.exists() else {}
    manifest = load_manifest(session_dir)
    if not hl:
        log.append("no highlights.json - run find_highlights.py first for markers and the reel string-out")

    resolve = rlib.connect()
    pm = resolve.GetProjectManager()
    name = f"{cfg['project']['name_prefix']}_{args.session}"
    project = pm.LoadProject(name) or pm.CreateProject(name)
    if not project:
        raise SystemExit(f"Could not create or open project {name}")

    yt, reel = cfg["youtube"], cfg["reel"]
    if project.GetTimelineCount() == 0:  # frame rate is locked once a timeline exists
        try_set(project, "timelineFrameRate", yt["fps"], log)
    try_set(project, "timelineResolutionWidth", yt["width"], log)
    try_set(project, "timelineResolutionHeight", yt["height"], log)
    try_set(project, "colorScienceMode", "davinciYRGB", log)
    # GoPro 4:3 / 8:7 sources fill a 16:9 or 9:16 frame and get cropped rather than letterboxed.
    try_set(project, "timelineInputResMismatchBehavior", "scaleToFill", log)

    mp = project.GetMediaPool()
    root = mp.GetRootFolder()
    bins = {}
    for b in ("01_Footage", "02_Music", "03_SFX"):
        existing = next((f for f in root.GetSubFolderList() if f.GetName() == b), None)
        bins[b] = existing or mp.AddSubFolder(root, b)

    mp.SetCurrentFolder(bins["01_Footage"])
    items = mp.ImportMedia([str(c) for c in clips]) or []
    print(f"Imported {len(items)} clips from {clips[0].parent if clips else '(none)'}")
    music_item = None
    if args.music:
        mp.SetCurrentFolder(bins["02_Music"])
        imported = mp.ImportMedia([str(args.music)]) or []
        music_item = imported[0] if imported else None

    # 3. Clip markers
    by_name = {}
    for it in items:
        key = clip_key(it.GetName())
        by_name[key] = it
        fps = float(it.GetClipProperty("FPS") or cfg["project"]["source_fps"])
        info = hl.get(key, {})
        for h in info.get("highlights", []):
            it.AddMarker(rlib.seconds_to_frames(h["start_s"], fps), MARKER_COLOR.get(h["label"], "Yellow"),
                         h["label"], f"score {h['score']} rot {h['rotation_deg']} peak {h['peak_rate_dps']}",
                         rlib.seconds_to_frames(h["end_s"] - h["start_s"], fps))
        for a, b in info.get("smooth", []):
            it.AddMarker(rlib.seconds_to_frames(a, fps), "Green", "smooth", "YouTube candidate",
                         rlib.seconds_to_frames(b - a, fps))

    # 4a. YouTube string-out: shooting order.
    yt_tl = unique_timeline(mp, project, "YT_selects")
    project.SetCurrentTimeline(yt_tl)
    def shoot_order(item):
        # Chronological across cameras (camera clocks permitting); GoPro chapters stay together and in order.
        row = manifest.get(clip_key(item.GetName()), {})
        rec, chap = row.get("recording", ""), row.get("chapter", "")
        first = min((r["creation_time"] for r in manifest.values() if rec and r.get("recording") == rec
                     and r.get("camera") == row.get("camera")), default=row.get("creation_time", ""))
        return (first, int(chap or 0), item.GetName())
    mp.AppendToTimeline(sorted(items, key=shoot_order))

    # 4b. Reel string-out: highlight windows ranked by score, padded 0.75 s each side for trimming.
    reel_tl = unique_timeline(mp, project, "REEL_selects")
    project.SetCurrentTimeline(reel_tl)
    try_set(reel_tl, "useCustomSettings", "1", log)
    try_set(reel_tl, "timelineResolutionWidth", reel["width"], log)
    try_set(reel_tl, "timelineResolutionHeight", reel["height"], log)
    try_set(reel_tl, "timelineFrameRate", reel["fps"], log)
    ranked = sorted(((h["score"], name, h) for name, r in hl.items() for h in r.get("highlights", [])),
                    key=lambda x: x[0], reverse=True)
    infos = []
    for _, name_, h in ranked:
        it = by_name.get(name_)
        if not it:
            continue
        fps = float(it.GetClipProperty("FPS") or cfg["project"]["source_fps"])
        infos.append({"mediaPoolItem": it,
                      "startFrame": max(0, rlib.seconds_to_frames(h["start_s"] - 0.75, fps)),
                      "endFrame": rlib.seconds_to_frames(h["end_s"] + 0.75, fps)})
    if infos:
        mp.AppendToTimeline(infos)

    if args.music:
        beats_file = args.music.with_suffix(".beats.json")
        if music_item is not None:
            ok = mp.AppendToTimeline([{"mediaPoolItem": music_item, "mediaType": 2, "trackIndex": 2,
                                       "recordFrame": reel_tl.GetStartFrame()}])
            if not ok:
                log.append("could not place music on A2 - drag it from 02_Music")
        if beats_file.exists():
            bm = json.loads(beats_file.read_text())
            tfps = float(reel["fps"])
            down = set(bm["downbeats_s"])
            for t in bm["beats_s"]:
                color, label = ("Purple", "bar") if t in down else ("Blue", "beat")
                reel_tl.AddMarker(rlib.seconds_to_frames(t, tfps), color, label, "", 1)
            for t in bm["drops_s"]:
                reel_tl.AddMarker(rlib.seconds_to_frames(t, tfps) + 0, "Red", "DROP", "hardest move here", 1)
        else:
            log.append(f"no {beats_file.name} - run beat_map.py on the track for beat markers")

    # 5. Base grade: DRX (one camera) or the conversion LUT matching each clip's camera profile (mixed cameras).
    drx, fallback, luts = cfg["color"]["base_grade_drx"], cfg["color"]["conversion_lut"], cfg["color"]["luts"]
    for tl in (yt_tl, reel_tl):
        tl_items = tl.GetItemListInTrack("video", 1) or []
        if drx and drx.exists():
            tl.ApplyGradeFromDRX(str(drx), 0, tl_items)
            continue
        for ti in tl_items:
            mpi = ti.GetMediaPoolItem()
            row = manifest.get(clip_key(mpi.GetName())) if mpi else None
            profile = (row or {}).get("profile", "")
            lut = luts.get(profile) or (fallback if not row else None)
            if lut and lut.exists():
                ti.SetLUT(1, str(lut))
                ti.SetClipColor({"GP-Log": "Orange", "GP-Log2": "Yellow", "D-Log M": "Teal"}.get(profile, "Blue"))
            elif profile and "log" in profile.lower():
                log.append(f"no LUT for profile {profile!r}: add it under [color.luts] in config.toml")
            elif not row:
                log.append("no manifest.csv / LUT: clips left ungraded (run ingest.py, put LUTs in luts/)")

    # 6. Gyroflow plugin on clips that still have gyro data (stabilized exports have none and are skipped).
    if gyroflow.mode(cfg) == "ofx":
        if gyroflow.plugin_installed():
            for tl, pre in ((yt_tl, "youtube"), (reel_tl, "reel")):
                res = gyroflow.stabilize_timeline(tl, gyroflow.preset(cfg, pre))
                print(f"Gyroflow on {tl.GetName()}: {len(res.get('added', []))} clips stabilized, "
                      f"{len(res.get('no gyro', []))} without gyro data left alone")
                log += [f"Gyroflow {k} on {', '.join(v)}" for k, v in res.items() if k in ("failed", "has comp")]
        else:
            log.append("[gyroflow] mode is ofx but the plugin is not installed (docs/PIPELINE.md 3b)")

    project.SetCurrentTimeline(reel_tl)
    pm.SaveProject()
    print(f"Project {name}: timelines {yt_tl.GetName()}, {reel_tl.GetName()}; {len(infos)} reel candidates")
    for line in dict.fromkeys(log):
        print(f"  note: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
