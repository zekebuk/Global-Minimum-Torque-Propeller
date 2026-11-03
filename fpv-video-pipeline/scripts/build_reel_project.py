#!/usr/bin/env python3
"""Build the reel as a real Resolve project from a cut list, ready to edit by hand (Resolve Studio must be open).

    python scripts/build_reel_project.py E:/EXPORTS/Review/2026-03_moab_cine/reel_dune2.json --name MOAB_reel

What it does:
  1. Project <name> (created or opened): 1080x1920 @ the cut list's fps, DaVinci YRGB, mismatched sources
     scaled to fit (so the 8:7 GoPro frame letterboxes exactly like the fast preview).
  2. Bins 01_Footage / 02_Music / 03_Graphics; the shots' sources, the song and the title PNG imported.
  3. Timeline <name>_vN: every shot with its in/out and speed, the song at the cut list's offset on A1,
     the title PNG on V2 over the opener with fades, picture + music fade-out at the end.
  4. Grades: each shot's solved chain (fast preview cache) copied into Resolve's LUT folder as one cube per
     node plus the whole chain, and loaded:
       - with a node template .drx (--template, 5+ empty serial nodes): one cube per node, so every node is
         the thing it says it is (1 PRIMARY, 2 TONE+SAT, 3 LOOK, 4 CST, 5 UNIFY) and editable in place;
       - without one: the whole chain on node 1 (exact, but one black box). Resolve's API cannot add nodes,
         so make the template once: on any clip add 4 serial nodes (Alt+S), Grab Still, export it as .drx.
Run `review.py fast --look cst5` first so the cubes exist for the current cut list.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, cutlist as cl, grade5, resolve as rlib  # noqa: E402

LUT_ROOT = Path(r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\LUT")
NODE_FILES = ["node_n1", "node_n2", "node_n3", "node_n4", "node_unify"]
NODE_NAMES = ["n1_PRIMARY_log", "n2_TONE_SAT_log", "n3_LOOK_log", "n4_CST_GPLog_to_Rec709", "n5_UNIFY_reel"]


def cube_key(s: cl.Shot, crop: str | None) -> str:
    """The fast preview's cache key for this shot's grade (must match fastpreview._cst5_cube)."""
    src = Path(s.src)
    t = {k: v for k, v in s.grade.items() if k not in ("clarity", "sky_grad", "unify")}
    raw = (f"{src.resolve()}|{src.stat().st_mtime_ns}|{s.in_s:.3f}|{s.dur:.3f}|{sorted(t.items())}|"
           f"{sorted(grade5.TARGETS.items())}|{sorted(grade5.UNIFY.items())}|{crop}|cst5.16")
    return "cst5_" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def stage_luts(c: cl.CutList, cache: Path, name: str, crop: str | None) -> list[str | None]:
    """Copy each shot's cubes into Resolve's LUT folder; returns the relative folder per shot (None if missing)."""
    out = []
    for i, s in enumerate(c.shots, 1):
        key = cube_key(s, crop)
        src_dir, chain = cache / "cubes" / key, cache / "cubes" / f"{key}.cube"
        if not (src_dir.exists() and chain.exists()):
            out.append(None)
            continue
        label = s.note.split(":")[0].replace(" ", "_").replace("/", "")[:24]
        rel = f"FPV/{name}/{i:02d}_{label}"
        d = LUT_ROOT / rel
        d.mkdir(parents=True, exist_ok=True)
        for f, nm in zip(NODE_FILES, NODE_NAMES):
            shutil.copy(src_dir / f"{f}.cube", d / f"{nm}.cube")
        shutil.copy(chain, d / "whole_chain.cube")
        out.append(rel)
    return out


def find_or_import(mp, folder, path: str):
    mp.SetCurrentFolder(folder)
    for clip in folder.GetClipList() or []:
        if clip.GetClipProperty("File Path") == path:
            return clip
    items = mp.ImportMedia([path]) or []
    return items[0] if items else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cutlist", type=Path)
    ap.add_argument("--name", default="REEL")
    ap.add_argument("--template", type=Path, help=".drx with 5+ empty serial nodes (one cube per node)")
    ap.add_argument("--aspect", default="8:7", choices=["8:7", "16:9"], help="picture aspect the grades were solved on")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    c = cl.CutList.load(args.cutlist)
    cache = args.cutlist.parent / ".preview_cache"
    crop = None if args.aspect == "8:7" else "crop=iw:iw*9/16"
    rel = stage_luts(c, cache, args.name, crop)
    missing = [n for n, r in enumerate(rel, 1) if r is None]
    if missing:
        print(f"no solved grade in the cache for shot(s) {missing}: run review.py fast --look cst5 first", file=sys.stderr)

    resolve = rlib.connect()
    pm = resolve.GetProjectManager()
    pm.GotoRootFolder()
    p = pm.CreateProject(args.name) or pm.LoadProject(args.name)
    if not p:
        raise SystemExit(f"could not create or open project {args.name}")
    log = []
    for k, v in {"timelineResolutionWidth": "1080", "timelineResolutionHeight": "1920", "timelineFrameRate": str(c.fps),
                 "colorScienceMode": "davinciYRGB", "timelineInputResMismatchBehavior": "scaleToFit"}.items():
        if not p.SetSetting(k, v):
            log.append(f"could not set {k}={v}")
    p.RefreshLUTList()
    mp = p.GetMediaPool()
    root = mp.GetRootFolder()
    bins = {}
    for b in ("01_Footage", "02_Music", "03_Graphics"):
        bins[b] = next((f for f in root.GetSubFolderList() or [] if f.GetName() == b), None) or mp.AddSubFolder(root, b)
    clips = {src: find_or_import(mp, bins["01_Footage"], src) for src in dict.fromkeys(s.src for s in c.shots)}
    music = find_or_import(mp, bins["02_Music"], c.music) if c.music else None
    title_png = cache / "title_0.png"
    title = find_or_import(mp, bins["03_Graphics"], str(title_png)) if c.titles and title_png.exists() else None
    mp.SetCurrentFolder(root)
    bad = [s for s, m in clips.items() if m is None]
    if bad:
        raise SystemExit(f"could not import: {bad}")

    existing = {p.GetTimelineByIndex(i).GetName() for i in range(1, p.GetTimelineCount() + 1)}
    n = 1
    while f"{args.name}_v{n}" in existing:
        n += 1
    tl = mp.CreateEmptyTimeline(f"{args.name}_v{n}")
    p.SetCurrentTimeline(tl)
    infos = []
    for s in c.shots:
        m = clips[s.src]
        sf = float(m.GetClipProperty("FPS") or c.fps)
        infos.append({"mediaPoolItem": m, "startFrame": int(round(s.in_s * sf)), "endFrame": int(round(s.out_s * sf)) - 1,
                      "mediaType": 1, "trackIndex": 1})
    items = mp.AppendToTimeline(infos) or []
    if len(items) != len(infos):
        raise SystemExit(f"Resolve placed {len(items)} of {len(infos)} clips")
    for s, it in zip(c.shots, items):
        if s.speed != 1.0 and not it.SetSpeed({"Percentage": s.speed * 100.0, "RippleTimeline": True}):
            log.append(f"speed refused on {s.note}")
    start = tl.GetStartFrame()
    if music:
        mf = float(music.GetClipProperty("FPS") or c.fps)
        if not mp.AppendToTimeline([{"mediaPoolItem": music, "mediaType": 2, "trackIndex": 1,
                                     "startFrame": int(round(c.music_in_s * mf)),
                                     "endFrame": int(round((c.music_in_s + c.duration()) * mf)) - 1, "recordFrame": start}]):
            log.append("music refused")
    if title:
        t = c.titles[0]
        tl.AddTrack("video")
        if mp.AppendToTimeline([{"mediaPoolItem": title, "mediaType": 1, "trackIndex": 2, "startFrame": 0,
                                 "endFrame": int(round(t["dur"] * c.fps)) - 1, "recordFrame": start + int(round(t["start"] * c.fps))}]):
            v2 = tl.GetItemListInTrack("video", 2) or []
            if v2:
                v2[0].SetFades({"FadeIn": int(round(0.7 * c.fps)), "FadeOut": int(round(0.7 * c.fps))})
        else:
            log.append("title refused")
    if c.fade_out_s:
        fo = int(round(c.fade_out_s * c.fps))
        for track in ("video", "audio"):
            tr = tl.GetItemListInTrack(track, 1) or []
            if tr:
                tr[-1].SetFades({"FadeOut": fo})

    # grades
    v1 = tl.GetItemListInTrack("video", 1) or []
    expanded = 0
    for s, it, r in zip(c.shots, v1, rel):
        if r is None:
            continue
        g = it.GetNodeGraph()
        if args.template and args.template.exists() and g.ApplyGradeFromDRX(str(args.template), 0):
            g = it.GetNodeGraph()
            if g.GetNumNodes() >= 5:
                for k, nm in enumerate(NODE_NAMES, 1):
                    g.SetLUT(k, f"{r}/{nm}.cube")
                for k in range(6, g.GetNumNodes() + 1):
                    g.SetNodeEnabled(k, False)
                expanded += 1
                continue
            g.ResetAllGrades()
            g = it.GetNodeGraph()
        if not g.SetLUT(1, f"{r}/whole_chain.cube"):
            log.append(f"LUT refused on {s.note}")
    pm.SaveProject()
    print(f"built {p.GetName()} / {tl.GetName()}: {len(v1)} shots, {c.duration():.1f}s, "
          f"{expanded} shot(s) as 5-node trees, {len(v1) - expanded} as a whole-chain LUT on node 1")
    for m in log:
        print("  !", m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
