#!/usr/bin/env python3
"""Phone review loop: cut list <-> Resolve timeline, fast preview renders, numbered shot sheet.

Runs on the desktop (Resolve Studio open). Typical round, driven from the phone through Claude:

    python scripts/review.py firstcut --session 2026-11-02_quarry --music D:/FPV/Music/x.mp3 --out D:/FPV/Review/reel.json
                                       # automatic v1: top highlights on the beat grid, best move on the drop
    python scripts/review.py export  --timeline REEL_selects --out D:/FPV/Review/reel.json   # or: timeline -> cut list
    #  (Claude edits reel.json from your notes using fpvpipe/cutlist.py operations)
    python scripts/review.py build   --cutlist D:/FPV/Review/reel.json                        # -> new timeline REEL_vN
    python scripts/review.py fast    --cutlist D:/FPV/Review/reel.json --session <s>          # no Resolve needed: graded
                                       # preview via ffmpeg, only changed shots re-render (seconds per note)
    python scripts/review.py preview --cutlist D:/FPV/Review/reel.json                        # Resolve render of timeline vN

preview renders the current version small enough to watch on a phone (reel 540x960, YouTube 960x540, ~4 Mbps)
and makes a contact sheet: one numbered thumbnail per shot with its start time, so notes can say "shot 4".
Everything lands in the cut list's folder as <name>_v<N>.mp4, <name>_v<N>_shots.jpg, <name>_v<N>.txt.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, cutlist as cl, resolve as rlib  # noqa: E402


def project_and_timeline(name: str | None = None):
    project = rlib.connect().GetProjectManager().GetCurrentProject()
    if not project:
        raise SystemExit("no project open in Resolve")
    if name is None:
        return project, None
    tl = next((project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)
               if project.GetTimelineByIndex(i).GetName() == name), None)
    if not tl:
        raise SystemExit(f"timeline {name!r} not found")
    return project, tl


def cmd_export(args, cfg):
    project, tl = project_and_timeline(args.timeline)
    fps = float(tl.GetSetting("timelineFrameRate") or 30)
    c = cl.CutList(name=args.timeline.split("_")[0], fps=fps)
    for it in tl.GetItemListInTrack("video", 1) or []:
        mpi = it.GetMediaPoolItem()
        if not mpi:
            continue
        src_fps = float(mpi.GetClipProperty("FPS") or fps)
        # Left offset / duration are in timeline frames; source seconds = frames / timeline fps (no retime support)
        in_s = it.GetLeftOffset() / fps
        c.shots.append(cl.Shot(mpi.GetClipProperty("File Path"), round(in_s, 4), round(in_s + it.GetDuration() / fps, 4)))
        if abs(src_fps - fps) > 0.01 and not c.shots[-1].note:
            c.shots[-1].note = f"source {src_fps}fps"
    audio = tl.GetItemListInTrack("audio", int(cfg["reel"].get("music_track", 2))) or []
    if audio and audio[0].GetMediaPoolItem():
        c.music = audio[0].GetMediaPoolItem().GetClipProperty("File Path")
        c.music_in_s = audio[0].GetLeftOffset() / fps
        bj = Path(c.music).with_suffix(".beats.json")
        if bj.exists():
            import json
            c.beats_s = [b - c.music_in_s for b in json.loads(bj.read_text())["beats_s"] if b >= c.music_in_s]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    c.save(args.out)
    print(c.table() + f"\n-> {args.out}")


def cmd_firstcut(args, cfg):
    import json
    raw = cfg["paths"]["library"] / args.session / "raw"
    hl = json.loads((raw / "highlights.json").read_text())
    stab = cfg["paths"]["stabilized"] / args.session
    def best_src(name):
        stem = Path(name).stem.upper()
        if stab.is_dir():
            for p in stab.iterdir():
                if p.stem.upper().startswith(stem) and "_STABILI" in p.stem.upper():
                    return str(p)
        return str(raw / name)
    moments = sorted(({"src": best_src(f), "peak_s": (h["start_s"] + h["end_s"]) / 2, "score": h["score"],
                       "label": h["label"]} for f, r in hl.items() for h in r.get("highlights", [])),
                     key=lambda m: m["score"], reverse=True)
    if not moments:
        # Calm flights / no gyro (DJI, stabilized exports): evenly spaced moments, taken round-robin across the clips
        # (longest first) so the v1 shows every clip. Labelled "filler" on the shot sheet so you know to swap them.
        from itertools import zip_longest
        per_clip = []
        for f, r in sorted(hl.items(), key=lambda kv: -kv[1].get("duration_s", 0)):
            d = float(r.get("duration_s", 0))
            per_clip.append([{"src": best_src(f), "peak_s": t, "score": 0, "label": "filler"} for t in
                             [d * (k + 0.5) / max(1, int(d // 6)) for k in range(max(1, int(d // 6)))]])
        moments = [m for group in zip_longest(*per_clip) for m in group if m]
        if not moments:
            raise SystemExit("no clips with duration in highlights.json")
        print("! no highlight moments: v1 uses evenly spaced filler shots")
    bm = json.loads(args.music.with_suffix(".beats.json").read_text())
    drop = args.drop if args.drop is not None else (bm["drops_s"][0] if bm.get("drops_s") else None)
    c = cl.first_cut(moments, bm["beats_s"], bm["downbeats_s"], drop, float(cfg["reel"]["fps"]),
                     args.seconds, args.name)
    c.music = str(args.music)   # first_cut set music_in_s (the reel starts whole bars before the drop)
    meta = args.music.with_suffix(".json")       # guide tracks start partway into the real song
    args.out.parent.mkdir(parents=True, exist_ok=True)
    c.save(args.out)
    print(c.table() + f"\n-> {args.out}" + ("" if moments else "\n! no highlights found"))
    if meta.exists():
        print("guide track: reel_handoff.py will convert the start to song time for Edits")


def cmd_build(args, cfg):
    c = cl.CutList.load(args.cutlist)
    project, _ = project_and_timeline()
    mp = project.GetMediaPool()
    existing = {project.GetTimelineByIndex(i).GetName() for i in range(1, project.GetTimelineCount() + 1)}
    while f"{c.name}_v{c.version}" in existing:
        c.version += 1
    name = f"{c.name}_v{c.version}"
    by_path = {}
    def find_or_import(path):
        if path in by_path:
            return by_path[path]
        def walk(folder):
            for clip in folder.GetClipList() or []:
                if clip.GetClipProperty("File Path") == path:
                    return clip
            for sub in folder.GetSubFolderList() or []:
                hit = walk(sub)
                if hit:
                    return hit
        hit = walk(mp.GetRootFolder()) or (mp.ImportMedia([path]) or [None])[0]
        by_path[path] = hit
        return hit
    tl = mp.CreateEmptyTimeline(name)
    if not tl:
        raise SystemExit(f"could not create timeline {name}")
    project.SetCurrentTimeline(tl)
    infos = []
    for n, s in enumerate(c.shots, 1):
        item = find_or_import(s.src)
        if not item:
            raise SystemExit(f"shot {n}: can't find or import {s.src}")
        src_fps = float(item.GetClipProperty("FPS") or c.fps)
        infos.append({"mediaPoolItem": item, "startFrame": int(round(s.in_s * src_fps)),
                      "endFrame": int(round(s.out_s * src_fps)) - 1, "mediaType": 1, "trackIndex": 1})
    if not mp.AppendToTimeline(infos):
        raise SystemExit("Resolve refused the clip list (check sources are online)")
    if c.music:
        m = find_or_import(c.music)
        mf = float(m.GetClipProperty("FPS") or c.fps) if m else c.fps
        if m:
            mp.AppendToTimeline([{"mediaPoolItem": m, "mediaType": 2, "trackIndex": int(cfg["reel"].get("music_track", 2)),
                                  "startFrame": int(round(c.music_in_s * mf)),
                                  "endFrame": int(round((c.music_in_s + c.duration()) * mf)),
                                  "recordFrame": tl.GetStartFrame()}])
    for b in c.beats_s[:400]:
        if b <= c.duration():
            tl.AddMarker(int(round(b * c.fps)), "Blue", "beat", "", 1)
    c.save(args.cutlist)
    print(f"built {name}: {len(c.shots)} shots, {c.duration():.2f}s")
    print("next: auto_grade.py --from-resolve", name, "(grades are per timeline item, so regrade after rebuilds)")


def cmd_preview(args, cfg):
    c = cl.CutList.load(args.cutlist)
    name = f"{c.name}_v{c.version}"
    project, tl = project_and_timeline(name)
    out_dir = Path(args.cutlist).parent
    vertical = int(tl.GetSetting("timelineResolutionHeight") or 0) > int(tl.GetSetting("timelineResolutionWidth") or 1)
    w, h = (540, 960) if vertical else (960, 540)
    project.SetCurrentTimeline(tl)
    project.SetCurrentRenderFormatAndCodec("mp4", "H264")
    project.SetRenderSettings({"SelectAllFrames": True, "TargetDir": str(out_dir), "CustomName": name,
                               "FormatWidth": w, "FormatHeight": h, "VideoQuality": 4000,
                               "ExportVideo": True, "ExportAudio": True})
    job = project.AddRenderJob()
    project.StartRendering([job])
    t0 = time.time()
    while project.IsRenderingInProgress():
        time.sleep(1)
    video = out_dir / f"{name}.mp4"
    print(f"preview {video} ({time.time() - t0:.0f}s, {video.stat().st_size / 1e6:.1f} MB)" if video.exists()
          else "render failed: check the Deliver page")
    sheet = out_dir / f"{name}_shots.jpg"
    if video.exists():
        contact_sheet(video, c, sheet)
    (out_dir / f"{name}.txt").write_text(c.table() + "\n")
    print(c.table())


def cmd_fast(args, cfg):
    import csv
    from fpvpipe import fastpreview
    c = cl.CutList.load(args.cutlist)
    manifest = {}
    if args.session:
        m = cfg["paths"]["library"] / args.session / "manifest.csv"
        if m.exists():
            with open(m, newline="") as f:
                manifest = {Path(r["file"]).stem.upper(): r["profile"] for r in csv.DictReader(f)}
    def profile_of(p: Path):
        if args.profile:     # stabilized exports carry no camera metadata: say what the session was shot in
            return args.profile
        key = p.stem.upper().split("_STABILI")[0]
        if key in manifest:
            return manifest[key]
        from fpvpipe import camera
        return camera.identify(p, look=False).profile
    vertical = not args.horizontal
    out = Path(args.cutlist).parent / f"{c.name}_v{c.version}_fast.mp4"
    t0 = time.time()
    r = fastpreview.build(c, cfg, out, look=args.look, vertical=vertical, profile_of=profile_of,
                          letterbox=args.letterbox)
    print(f"preview {out} in {time.time() - t0:.1f}s ({r.rendered} shot(s) rendered, {r.reused} reused, "
          f"{out.stat().st_size / 1e6:.1f} MB)")
    for n in r.notes:
        print(f"  ! {n}")
    contact_sheet(out, c, out.with_name(out.stem + "_shots.jpg"))
    (out.with_suffix(".txt")).write_text(c.table() + "\n")
    print(c.table())


def contact_sheet(video: Path, c: cl.CutList, out: Path, cols: int = 4):
    """One thumbnail per shot (mid-shot), numbered, tiled. Numbers via drawtext when a system font exists."""
    font = next((f for f in ("C:/Windows/Fonts/arialbd.ttf", "/System/Library/Fonts/Helvetica.ttc",
                             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf") if Path(f).exists()), None)
    tmp = out.parent / "_thumbs"
    tmp.mkdir(exist_ok=True)
    for n, (s, t) in enumerate(zip(c.shots, c.starts()), 1):
        vf = "scale=240:-2"
        if font:
            ff = font.replace(":", "\\:")                # inside quotes ffmpeg wants a single backslash
            vf += (f",drawtext=fontfile='{ff}':text='{n}  {t:.1f}s':x=6:y=6:fontsize=22:fontcolor=white:"
                   "box=1:boxcolor=black@0.6")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t + s.dur / 2:.3f}", "-i", str(video), "-frames:v", "1",
                        "-vf", vf, str(tmp / f"{n:03d}.jpg")], check=False)
    rows = (len(c.shots) + cols - 1) // cols
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp / "%03d.jpg"), "-vf", f"tile={cols}x{rows}:padding=4",
                    "-frames:v", "1", str(out)], check=False)
    for f in tmp.glob("*.jpg"):
        f.unlink()
    tmp.rmdir()
    print(f"shot sheet {out}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("firstcut"); f.add_argument("--session", required=True); f.add_argument("--music", type=Path, required=True)
    f.add_argument("--out", type=Path, required=True); f.add_argument("--seconds", type=float, default=24.0)
    f.add_argument("--drop", type=float, help="music time of the drop (default: first detected drop)")
    f.add_argument("--name", default="REEL")
    e = sub.add_parser("export"); e.add_argument("--timeline", required=True); e.add_argument("--out", type=Path, required=True)
    b = sub.add_parser("build"); b.add_argument("--cutlist", type=Path, required=True)
    p = sub.add_parser("preview"); p.add_argument("--cutlist", type=Path, required=True)
    q = sub.add_parser("fast"); q.add_argument("--cutlist", type=Path, required=True); q.add_argument("--session")
    q.add_argument("--look", default="reel", help="reel | youtube (camera LUT + solver) | cinema | fivenode | cst5 (CST in/out + LGG + curves/HSL + look)"); q.add_argument("--horizontal", action="store_true", help="16:9 (YouTube)")
    q.add_argument("--profile", help="force the camera profile for every shot, e.g. GP-Log")
    q.add_argument("--letterbox", action="store_true", help="16:9 picture on a 9:16 canvas with black bars")
    for s in (f, e, b, p, q):
        s.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    {"firstcut": cmd_firstcut, "export": cmd_export, "build": cmd_build, "preview": cmd_preview,
     "fast": cmd_fast}[args.cmd](args, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
