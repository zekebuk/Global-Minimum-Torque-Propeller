#!/usr/bin/env python3
"""Step 6 - Render deliverables from the open project (Resolve Studio must be open).

    python scripts/render.py reel    --timeline REEL_final
    python scripts/render.py youtube --timeline YT_final
    python scripts/render.py reel --timeline REEL_selects --save-preset "FPV Reel" --no-render   # one-time
    python scripts/render.py reel --timeline REEL_final --no-music   # for Edits / in-app music: SFX kept, song muted

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
    ap.add_argument("--save-preset", metavar="NAME", help="also save these settings as a Deliver-page render preset")
    ap.add_argument("--no-render", action="store_true", help="only set up (and save) the settings")
    ap.add_argument("--no-music", action="store_true",
                    help="mute the music track ([reel].music_track, default A2) for this render; adds _nomusic to the name")
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
        "CustomName": args.timeline + ("_nomusic" if args.no_music else ""),
        "FormatWidth": spec["width"],
        "FormatHeight": spec["height"],
        "FrameRate": str(spec["fps"]),
        "VideoQuality": spec["bitrate_kbps"],
        "ExportVideo": True,
        "ExportAudio": True,
        "AudioCodec": "aac",
        "AudioSampleRate": 48000,
    }
    # 1-1-1 color tags (Rec.709 primaries / transfer / matrix). Resolve's default Gamma 2.4 tag is 1-2-1, which
    # YouTube/Instagram/QuickTime interpret differently -> washed-out uploads. Keys per Resolve's scripting docs.
    for key, cfg_key in (("ColorSpaceTag", "color_space_tag"), ("GammaTag", "gamma_tag")):
        if spec.get(cfg_key):
            settings[key] = spec[cfg_key]
    if not project.SetRenderSettings(settings):
        print("warning: some render settings were rejected; check the Deliver page before trusting the file")
    if args.save_preset:
        ok = project.SaveAsNewRenderPreset(args.save_preset)
        print(f"render preset {args.save_preset!r} {'saved' if ok else 'NOT saved (name taken? delete it on the Deliver page)'}")
    if args.no_render:
        return 0
    music_track = int(cfg["reel"].get("music_track", 2))
    if args.no_music:
        if not tl.SetTrackEnable("audio", music_track, False):
            raise SystemExit(f"could not mute audio track A{music_track}; mute it by hand and render without --no-music")
        print(f"muted A{music_track} (music) for this render")
    try:
        return _render(project, tl, args, spec, out_dir)
    finally:
        if args.no_music:
            tl.SetTrackEnable("audio", music_track, True)


def check_color_tags(path: Path) -> bool:
    """Verify the file carries 1-1-1 (bt709 primaries, transfer, matrix): what YouTube/Instagram expect for SDR."""
    import json
    import subprocess
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=color_primaries,color_transfer,color_space", "-of", "json", str(path)],
                             capture_output=True, text=True, check=True).stdout
        st = json.loads(out)["streams"][0]
    except Exception as e:
        print(f"could not read color tags ({e})")
        return False
    tags = (st.get("color_primaries"), st.get("color_transfer"), st.get("color_space"))
    ok = tags == ("bt709", "bt709", "bt709")
    print(f"color tags {'/'.join(str(t) for t in tags)}: " + ("OK (1-1-1)" if ok else
          "NOT 1-1-1 -> expect a gamma/contrast shift on YouTube. Set Deliver > Advanced > Gamma tag = Rec.709 "
          "(or timeline Rec.709-A) and re-render"))
    return ok


def _render(project, tl, args, spec, out_dir) -> int:
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
    name = args.timeline + ("_nomusic" if args.no_music else "")
    print(f"\n{st.get('JobStatus')} in {time.time() - start:.0f}s -> {out_dir / (name + '.mp4')}")
    if st.get("JobStatus") != "Complete":
        print(st.get("Error", ""), file=sys.stderr)
        return 1
    check_color_tags(out_dir / (name + ".mp4"))
    if args.target == "reel":
        tl_seconds = (tl.GetEndFrame() - tl.GetStartFrame()) / float(spec["fps"])
        if tl_seconds > spec["max_seconds"]:
            print(f"warning: reel is {tl_seconds:.0f}s, over the {spec['max_seconds']}s target")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
