#!/usr/bin/env python3
"""Step 6 - Render deliverables from the open project (Resolve Studio must be open).

    python scripts/render.py reel    --timeline REEL_final
    python scripts/render.py youtube --timeline YT_final
    python scripts/render.py reel --timeline REEL_selects --save-preset "FPV Reel" --no-render   # one-time
    python scripts/render.py reel --timeline REEL_final --no-music   # for Edits / in-app music: SFX kept, song muted
    python scripts/render.py reel --check E:/EXPORTS/MOAB_reel/MOAB_reel_v10.mp4   # only the export checklist

Settings come from [reel] / [youtube] in config.toml. Output: <exports>/<project>/<timeline>.mp4
Reel: 1080x1920 H.264 High ~20 Mbps 29.97p, AAC 320k, 1-1-1 tags - Instagram re-encodes, so give it a clean master.
A reel render ends with the READY FOR EXPORT checklist (fpvpipe/deliver.py), read back from the file itself.
YouTube: 3840x2160 H.265 ~80 Mbps - a 4K upload is what earns the higher-bitrate VP9/AV1 stream.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, deliver, resolve as rlib  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", choices=["reel", "youtube"])
    ap.add_argument("--timeline")
    ap.add_argument("--config", type=Path)
    ap.add_argument("--save-preset", metavar="NAME", help="also save these settings as a Deliver-page render preset")
    ap.add_argument("--no-render", action="store_true", help="only set up (and save) the settings")
    ap.add_argument("--no-music", action="store_true",
                    help="mute the music track ([reel].music_track, default A2) for this render; adds _nomusic to the name")
    ap.add_argument("--check", type=Path, metavar="MP4", help="no Resolve: run the export checklist on this file")
    args = ap.parse_args()
    cfg = config.load(args.config)
    spec = cfg[args.target]
    if args.check:
        ok, text = deliver.verdict(args.check, spec, no_music=args.no_music or args.check.stem.endswith("_nomusic"))
        print(text)
        return 0 if ok else 1
    if not args.timeline:
        ap.error("--timeline is required (or --check <mp4>)")

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

    if args.save_preset or args.no_render:
        if not project.SetCurrentRenderFormatAndCodec("mp4", spec["codec"]):
            raise SystemExit(f"Resolve rejected mp4/{spec['codec']} - check project.GetRenderCodecs('mp4')")
        if not project.SetRenderSettings(deliver.settings(spec, out_dir, args.timeline)):
            print("warning: some render settings were rejected; check the Deliver page before trusting the file")
        if args.save_preset:
            ok = project.SaveAsNewRenderPreset(args.save_preset)
            print(f"render preset {args.save_preset!r} {'saved' if ok else 'NOT saved (name taken? delete it on the Deliver page)'}")
        if args.no_render:
            return 0
    try:
        out = deliver.render(project, tl, spec, out_dir, args.timeline, no_music=args.no_music,
                             music_track=int(cfg["reel"].get("music_track", 2)))
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"-> {out}")
    if args.target == "reel":
        ok, text = deliver.verdict(out, spec, no_music=args.no_music)
        print(text)
        return 0 if ok else 1
    check_color_tags(out)
    return 0


def check_color_tags(path: Path) -> bool:
    """Verify the file carries 1-1-1 (bt709 primaries, transfer, matrix): what YouTube/Instagram expect for SDR."""
    try:
        tags = deliver.probe(path)["tags"]
    except Exception as e:
        print(f"could not read color tags ({e})")
        return False
    ok = tags == ("bt709", "bt709", "bt709")
    print(f"color tags {'/'.join(str(t) for t in tags)}: " + ("OK (1-1-1)" if ok else
          "NOT 1-1-1 -> expect a gamma/contrast shift on YouTube. Set Deliver > Advanced > Gamma tag = Rec.709 "
          "(or timeline Rec.709-A) and re-render"))
    return ok


if __name__ == "__main__":
    raise SystemExit(main())
