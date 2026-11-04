#!/usr/bin/env python3
"""Rebuild a "behind" title from the frames Resolve actually outputs, so the summit cut-out matches the stabilized,
graded shot and not the preview. The title in the cut list must have "behind": true; Resolve must be open on the
project with the timeline current.

    python scripts/title_behind_resolve.py reel.json --project SENJA_reel_4x3

Renders the title's range from V1 with the title track off, cuts the mask on those frames (titles.render_behind), and
puts the new PNG sequence on V2 in place of the old one.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_reel_project as b  # noqa: E402
from fpvpipe import config, cutlist as cl, deliver, resolve as rlib, titles as tt  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cutlist", type=Path)
    ap.add_argument("--project", required=True)
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--config", type=Path)
    a = ap.parse_args()
    cfg, c = config.load(a.config), cl.CutList.load(a.cutlist)
    t = next((x for x in c.titles if x.get("behind")), None)
    if t is None:
        raise SystemExit('no title with "behind": true in the cut list')
    resolve = rlib.connect()
    pm = resolve.GetProjectManager()
    p = pm.GetCurrentProject()
    if p.GetName() != a.project:
        raise SystemExit(f"Resolve is on {p.GetName()!r}, not {a.project!r}")
    tl = p.GetCurrentTimeline()
    start = tl.GetStartFrame()
    first, n = start + int(round(t["start"] * c.fps)), int(round(t["dur"] * c.fps))
    work = a.cutlist.parent / f"title_resolve_{int(time.time())}"     # a new folder: Resolve holds the old sequence open
    work.mkdir(parents=True)

    spec = {**cfg["reel"], "width": a.width, "height": a.height}
    p.SetCurrentTimeline(tl)
    p.SetCurrentRenderFormatAndCodec("mp4", spec["codec"])
    p.SetRenderSettings({**deliver.settings(spec, work, "plate"), "SelectAllFrames": False, "MarkIn": first, "MarkOut": first + n - 1})
    old = tl.GetItemListInTrack("video", 2) or []     # off the timeline first: a plate must not depend on the old title
    if old:
        tl.DeleteClips(old)
    try:
        job = p.AddRenderJob()
        if not job:
            raise SystemExit("AddRenderJob failed")
        p.StartRendering([job])
        while p.IsRenderingInProgress():
            time.sleep(1)
        st = p.GetRenderJobStatus(job)
        p.DeleteRenderJob(job)
        if st.get("JobStatus") != "Complete":
            raise SystemExit(f"plate render: {st}")
    finally:
        p.SetRenderSettings({"SelectAllFrames": True})

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(work / "plate.mp4"), "-vf", "format=rgb24", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, a.height, a.width, 3)
    print(f"plate: {len(frames)} frames (wanted {n})")
    seq = work / "seq"
    tt.render_behind(t, frames, seq, c.fps)

    mp = p.GetMediaPool()
    root = mp.GetRootFolder()
    folder = next((f for f in root.GetSubFolderList() or [] if f.GetName() == "03_Graphics"), root)
    clip = b.import_sequence(mp, folder, seq, c)
    if clip is None:
        raise SystemExit("could not import the title sequence")
    mp.SetCurrentFolder(root)
    done = mp.AppendToTimeline([{"mediaPoolItem": clip, "mediaType": 1, "trackIndex": 2, "startFrame": 0,
                                 "endFrame": len(frames) - 1, "recordFrame": first}])
    pm.SaveProject()
    print("title on V2:", bool(done), "from", first, "frames", len(frames))
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main())
