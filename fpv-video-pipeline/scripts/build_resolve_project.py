#!/usr/bin/env python3
"""Step 3b - Build the Resolve project for a session (Resolve Studio must be open).

    python scripts/build_resolve_project.py 2026-10-31_quarry --music ~/FPV/Music/track.mp3

What it does, in order:
  1. Creates (or opens) project FPV_<session>; 3840x2160 @ source fps, DaVinci YRGB (LUT-based color, section 4).
  2. Bins: 01_Footage (Gyroflow intermediates if present, else raw GoPro), 02_Music, 03_SFX.
  3. Clip markers from highlights.json (red flip/roll, orange dive, yellow aggressive, green smooth).
  4. Timeline YT_selects   - 16:9 4K, every clip in shooting order (chapters kept together).
     Timeline REEL_selects - 9:16 1080x1920, a string-out of the highlight windows, best first,
                             with beat/downbeat/drop markers from <track>.beats.json.
  5. Base grade on every clip: the .drx in config (preferred) or the GP-Log conversion LUT on node 1.
Re-running is safe: existing timelines are left alone and a new suffixed one is made.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, resolve as rlib  # noqa: E402

MARKER_COLOR = {"flip/roll": "Red", "dive/hang": "Orange", "aggressive": "Yellow"}
VIDEO_EXT = {".mp4", ".mov", ".mxf"}


def try_set(obj, key, value, log):
    ok = obj.SetSetting(key, str(value))
    if not ok:
        log.append(f"could not set {key}={value} (set it by hand in Project/Timeline Settings)")
    return ok


def footage_for(session_dir: Path, stabilized_root: Path) -> list[Path]:
    stab = stabilized_root / session_dir.name
    src = stab if stab.is_dir() and any(stab.iterdir()) else session_dir / "raw"
    return sorted({p for p in src.iterdir() if p.suffix.lower() in VIDEO_EXT})


def original_name(item_name: str) -> str:
    """Map 'GX010042_stabilized.mov' back to 'GX010042.MP4' to look up telemetry highlights."""
    return item_name.split("_")[0].split(".")[0].upper() + ".MP4"


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
    hl = json.loads(hl_file.read_text()) if hl_file.exists() else {}
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
        key = original_name(it.GetName())
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
    mp.AppendToTimeline(sorted(items, key=lambda i: (i.GetName()[4:8], i.GetName()[2:4])))

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

    # 5. Base grade
    drx, lut = cfg["color"]["base_grade_drx"], cfg["color"]["conversion_lut"]
    for tl in (yt_tl, reel_tl):
        tl_items = tl.GetItemListInTrack("video", 1) or []
        if drx and drx.exists():
            tl.ApplyGradeFromDRX(str(drx), 0, tl_items)
        elif lut and lut.exists():
            for ti in tl_items:
                ti.SetLUT(1, str(lut))
        else:
            log.append("no base grade applied: put your GP-Log LUT in luts/ or set base_grade_drx in config.toml")
            break

    project.SetCurrentTimeline(reel_tl)
    pm.SaveProject()
    print(f"Project {name}: timelines {yt_tl.GetName()}, {reel_tl.GetName()}; {len(infos)} reel candidates")
    for line in dict.fromkeys(log):
        print(f"  note: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
