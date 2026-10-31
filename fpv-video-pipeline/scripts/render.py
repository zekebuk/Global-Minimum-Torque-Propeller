#!/usr/bin/env python3
"""Step 6 - Render deliverables from the open project (Resolve Studio must be open).

    python scripts/render.py reel    --timeline REEL_final
    python scripts/render.py youtube --timeline YT_final

Settings come from [reel] / [youtube] in config.toml. Output: <exports>/<project>/<timeline>.mp4
Reel: 1080x1920 H.264 ~25 Mbps 29.97p, AAC 320k - Instagram re-encodes, so give it a clean master.
YouTube: 3840x2160 H.265 ~80 Mbps - a 4K upload is what earns the higher-bitrate VP9/AV1 stream.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, resolve as rlib  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", choices=["reel", "youtube"])
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    spec = cfg[args.target]

    resolve = rlib.connect()
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise SystemExit("No project open in Resolve")
    tl = next((project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)
               if project.GetTimelineByIndex(i).GetName() == args.timeline), None)
    if not tl:
        raise SystemExit(f"Timeline {args.timeline!r} not found in {project.GetName()}")
    project.SetCurrentTimeline(tl)

    out_dir = cfg["paths"]["exports"] / project.GetName()
    out_dir.mkdir(parents=True, exist_ok=True)
    if not project.SetCurrentRenderFormatAndCodec("mp4", spec["codec"]):
        raise SystemExit(f"Resolve rejected mp4/{spec['codec']} - check project.GetRenderCodecs('mp4')")
    settings = {
        "SelectAllFrames": True,
        "TargetDir": str(out_dir),
        "CustomName": args.timeline,
        "FormatWidth": spec["width"],
        "FormatHeight": spec["height"],
        "FrameRate": str(spec["fps"]),
        "VideoQuality": spec["bitrate_kbps"],
        "ExportVideo": True,
        "ExportAudio": True,
        "AudioCodec": "aac",
        "AudioSampleRate": 48000,
    }
    if not project.SetRenderSettings(settings):
        print("warning: some render settings were rejected; check the Deliver page before trusting the file")
    job = project.AddRenderJob()
    if not job:
        raise SystemExit("AddRenderJob failed (is the timeline empty or the output path unwritable?)")
    project.StartRendering([job])
    start = time.time()
    while project.IsRenderingInProgress():
        st = project.GetRenderJobStatus(job)
        print(f"\r{st.get('JobStatus')} {st.get('CompletionPercentage', 0)}%", end="", flush=True)
        time.sleep(2)
    st = project.GetRenderJobStatus(job)
    print(f"\n{st.get('JobStatus')} in {time.time() - start:.0f}s -> {out_dir / (args.timeline + '.mp4')}")
    if st.get("JobStatus") != "Complete":
        print(st.get("Error", ""), file=sys.stderr)
        return 1
    if args.target == "reel":
        tl_seconds = (tl.GetEndFrame() - tl.GetStartFrame()) / float(spec["fps"])
        if tl_seconds > spec["max_seconds"]:
            print(f"warning: reel is {tl_seconds:.0f}s, over the {spec['max_seconds']}s target")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
