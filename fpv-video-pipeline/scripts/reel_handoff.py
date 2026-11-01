#!/usr/bin/env python3
"""Hand a reel to Instagram's Edits app: muted-music video + a cue card saying where to start the song.

    # You cut the reel in Resolve to a local copy of the song on A2, then rendered with render.py --no-music:
    python scripts/reel_handoff.py D:\\FPV\\Exports\\FPV_quarry\\REEL_final_nomusic.mp4 --from-resolve REEL_final

    # Or without Resolve: say when the hardest move hits in the reel, and use the song's detected drop:
    python scripts/reel_handoff.py REEL_final_nomusic.mp4 --song D:\\FPV\\Music\\track.mp3 --reel-drop 7.5

Edits starts the chosen song at your chosen point when the reel starts (0:00), so the only number needed is
"song time at reel 0:00". From Resolve it is the song clip's source in-point minus any gap before it on the
timeline; from beats it is song_drop - reel_drop. The cue card also gives the nearest whole/half second (if
the app's scrubber can't land exactly) and how many frames off that is, so you know if it matters (>2 frames shows).
Writes <video>.edits.txt and copies both files into [reel].handoff (a Google Drive / OneDrive synced folder),
or a local "Edits inbox" folder when you use Edits on the desktop (--target desktop, the default).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, media  # noqa: E402


def fmt(t: float) -> str:
    sign = "-" if t < 0 else ""
    t = abs(t)
    return f"{sign}{int(t // 60)}:{t % 60:04.1f}"


def cue_from_timeline(song_in_s: float, song_starts_at_reel_s: float) -> float:
    """Song time that plays at reel 0:00."""
    return song_in_s - song_starts_at_reel_s


def cue_from_drops(song_drops: list[float], reel_drop_s: float) -> float | None:
    """Line the song's first drop that leaves enough intro (>= reel_drop) up with the reel's hardest move."""
    for d in song_drops:
        if d - reel_drop_s >= 0:
            return d - reel_drop_s
    return None


def rounding_report(cue: float, fps: float) -> list[str]:
    out = []
    for step, label in ((0.5, "half second"), (1.0, "whole second")):
        r = round(cue / step) * step
        frames = abs(r - cue) * fps
        verdict = "fine" if frames <= 2 else "visible: cuts will feel late/early"
        out.append(f"nearest {label}: {fmt(r)} ({frames:.1f} frames off, {verdict})")
    return out


def guide_offset(audio_path: Path) -> float:
    """A guide track (guide_track.py) starts partway into the song; its .json says where. Real songs: 0."""
    meta = audio_path.with_suffix(".json")
    if meta.exists():
        try:
            return float(json.loads(meta.read_text()).get("song_time_at_file_start", 0.0))
        except (ValueError, AttributeError):
            pass
    return 0.0


def from_resolve(name: str, track: int) -> tuple[float, str, float]:
    from fpvpipe import resolve as rlib
    project = rlib.connect().GetProjectManager().GetCurrentProject()
    tl = next((project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)
               if project.GetTimelineByIndex(i).GetName() == name), None)
    if not tl:
        raise SystemExit(f"timeline {name!r} not found")
    fps = float(tl.GetSetting("timelineFrameRate") or 30)
    items = tl.GetItemListInTrack("audio", track) or []
    if not items:
        raise SystemExit(f"no clip on A{track} of {name}: put the song there (or use --song/--reel-drop)")
    song = min(items, key=lambda i: i.GetStart())
    starts_at = (song.GetStart() - tl.GetStartFrame()) / fps
    song_in = song.GetLeftOffset() / fps
    drop_markers = [f / fps for f, m in tl.GetMarkers().items() if m.get("name") == "DROP"]
    return cue_from_timeline(song_in, starts_at), song.GetName(), (min(drop_markers) if drop_markers else None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path, help="the reel rendered with render.py --no-music")
    ap.add_argument("--from-resolve", metavar="TIMELINE")
    ap.add_argument("--song", type=Path, help="local copy of the song (with .beats.json from beat_map.py)")
    ap.add_argument("--reel-drop", type=float, help="reel time (s) of the hardest move the drop should hit")
    ap.add_argument("--song-drop", type=float, help="override: song time (s) of the drop")
    ap.add_argument("--title", default="", help="song title / artist as it appears in Edits")
    ap.add_argument("--handoff", type=Path, help="override [reel].handoff folder")
    ap.add_argument("--target", choices=["desktop", "phone"], help="Edits on this computer or on the phone "
                    "(default: [reel].edits_target, else desktop)")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    info = media.video_summary(media.ffprobe(args.video))
    fps = info["fps"] or 30.0
    reel_drop = args.reel_drop

    if args.from_resolve:
        cue, song_name, marker_drop = from_resolve(args.from_resolve, int(cfg["reel"].get("music_track", 2)))
        cue += guide_offset(cfg["paths"]["music"] / song_name)
        reel_drop = reel_drop if reel_drop is not None else marker_drop
        title = args.title or Path(song_name).stem.replace(".guide", "")
    elif args.song and reel_drop is not None:
        if args.song_drop is not None:
            drops = [args.song_drop]
        else:
            bj = args.song.with_suffix(".beats.json")
            if not bj.exists():
                raise SystemExit(f"run beat_map.py on {args.song.name} first (or pass --song-drop)")
            drops = json.loads(bj.read_text())["drops_s"]
        cue = cue_from_drops(drops, reel_drop)
        if cue is None:
            raise SystemExit(f"no drop in the song late enough for a {reel_drop}s hook; pass --song-drop")
        if args.song_drop is None:
            cue += guide_offset(args.song)  # beats.json times are file-relative; Edits needs song time
        title = args.title or args.song.stem.replace(".guide", "")
    else:
        raise SystemExit("use --from-resolve TIMELINE, or --song + --reel-drop")

    lines = [f"REEL: {args.video.name}  ({info['duration_s']:.1f}s, {info['width']}x{info['height']} {fps}p)",
             f"SONG: {title}", "", f"START THE SONG AT  {fmt(cue)}"]
    if cue < 0:
        lines.append(f"! the song starts {fmt(-cue)} after the reel begins: Edits plays music from 0:00, so move the "
                     "song to the start of the timeline in Resolve, or add silence/SFX for the gap")
    lines += ["  " + r for r in rounding_report(cue, fps)]
    if reel_drop is not None:
        lines.append(f"Drop hits at reel {fmt(reel_drop)} = song {fmt(cue + reel_drop)}: check it lands on the hardest move.")
    target = args.target or cfg["reel"].get("edits_target", "desktop")
    first = ("Edits (desktop) > New project > import this file" if target == "desktop"
             else "Drive app > save video to device > Edits > New > import video")
    lines += ["", f"Steps: {first} > Audio > add song >",
              "       set start to the time above > keep the reel's own audio (SFX) at ~30-50% > Export / Share to Instagram",
              "", "CAPTION DRAFT", "<hook line> | <location>",
              " ".join("#" + h.lstrip("#") for h in cfg.get("youtube_meta", {}).get("hashtags", []))]
    card = args.video.with_suffix(".edits.txt")
    card.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))

    dest = args.handoff or (Path(cfg["reel"]["handoff"]).expanduser() if cfg["reel"].get("handoff") else None)
    if dest:
        dest.mkdir(parents=True, exist_ok=True)
        for f in (args.video, card):
            shutil.copy2(f, dest / f.name)
        print(f"\ncopied to {dest}")
    elif target == "phone":
        print("\nset [reel].handoff in config.toml to a Google Drive/OneDrive synced folder to skip the manual copy")
    else:
        print(f"\nimport {args.video} into Edits desktop (or set [reel].handoff to an 'Edits inbox' folder)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
