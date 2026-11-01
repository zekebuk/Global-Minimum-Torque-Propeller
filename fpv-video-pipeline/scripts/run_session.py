#!/usr/bin/env python3
"""One command per flying session: card -> Resolve project, stopping only where a human has to click.

    python scripts/run_session.py E:\\DCIM --session 2026-11-02_quarry --music D:\\FPV\\Music\\track.mp3
    python scripts/run_session.py --session 2026-11-02_quarry --from build        # re-run from a step

Steps (each one is an existing script; this only chains them and stops on the first failure):
  ingest     copy + verify the card, manifest with camera/profile per clip
  report     camera_report: shutter angle, ISO, ND used / ND for next time
  highlights rank flips, rolls, dives and smooth lines from gyro telemetry (GoPro)
  stage      put clips worth stabilizing in _queue           (--stabilizer hsp)
  pause      you run GoPro Player batch export / Gyroflow   (press Enter when done)
  check      verify the exports (10-bit, fps, length) and name them
  beats      beat map for --music
  build      Resolve project with timelines, markers and per-camera LUTs (Resolve Studio must be open)
--stabilizer none skips stage/pause/check (HyperSmooth was on in camera, or you'll stabilize later).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = ["ingest", "report", "highlights", "stage", "pause", "check", "beats", "build", "firstcut", "fast", "notify"]
# Overnight: no Resolve, no clicks. Stabilization comes later, on the approved shots only.
OVERNIGHT = ["ingest", "report", "highlights", "beats", "firstcut", "fast", "notify"]


def pick_music(cfg, common) -> Path:
    """Newest track that has a beat map in the Music folder; otherwise generate a 128 BPM guide click."""
    music = cfg["paths"]["music"]
    beat_maps = sorted(music.glob("*.beats.json"), key=lambda p: p.stat().st_mtime, reverse=True) if music.is_dir() else []
    for bj in beat_maps:
        base = bj.name[: -len(".beats.json")]
        for cand in music.glob(base + ".*"):
            if cand.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac", ".flac") and not cand.name.endswith(".beats.json"):
                print(f"music: {cand.name} (newest beat-mapped track)")
                return cand
    guide = music / "default_128.guide.wav"
    if not guide.exists():
        subprocess.run([sys.executable, str(HERE / "guide_track.py"), "make", "--bpm", "128", "--first-beat", "0",
                        "--drop", "7.5", "--to", "60", "--name", "default_128"] + [str(c) for c in common], check=True)
    print("music: no beat-mapped track found, using a 128 BPM guide click (swap it in Edits)")
    return guide


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("card", nargs="?", type=Path, help="card / folder to ingest (needed for the ingest step)")
    ap.add_argument("--session", required=True)
    ap.add_argument("--music", type=Path)
    ap.add_argument("--stabilizer", choices=["hsp", "gyroflow", "none"], default="hsp",
                    help="hsp = GoPro Player HyperSmooth Pro (default), gyroflow, none")
    ap.add_argument("--all", action="store_true", help="stabilize every clip, not just highlight clips")
    ap.add_argument("--from", dest="start", choices=STEPS, default="ingest")
    ap.add_argument("--to", dest="stop", choices=STEPS, default="build")
    ap.add_argument("--overnight", action="store_true",
                    help="unattended: ingest -> highlights -> first cut -> fast preview -> notify (no Resolve, no pauses)")
    ap.add_argument("--seconds", type=float, default=24.0, help="reel length for the first cut")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()

    sys.path.insert(0, str(HERE.parent))
    from fpvpipe import config
    cfg = config.load(args.config)
    raw = cfg["paths"]["library"] / args.session / "raw"
    review_dir = cfg["paths"].get("review") or cfg["paths"]["library"].parent / "Review"
    cut = review_dir / args.session / "reel.json"
    stab = cfg["paths"]["stabilized"] / args.session
    common = ["--config", str(args.config)] if args.config else []
    if args.overnight and not args.music:
        args.music = pick_music(cfg, common)
    py = [sys.executable]

    plan = {
        "ingest": py + [HERE / "ingest.py", args.card or "", "--session", args.session] + common,
        "report": py + [HERE / "camera_report.py", raw] + common,
        "highlights": py + [HERE / "find_highlights.py", raw] + common,
        "stage": py + [HERE / "stage_for_stabilization.py", args.session] + (["--all"] if args.all else []) + common,
        "pause": None,
        "check": py + [HERE / "check_stabilized.py", args.session] + common,
        # skip when a beat map exists (guide tracks carry an exact grid; real tracks only need mapping once)
        "beats": py + [HERE / "beat_map.py", args.music]
                 if args.music and not Path(args.music).with_suffix(".beats.json").exists() else None,
        "build": py + [HERE / "build_resolve_project.py", args.session] + (["--music", args.music] if args.music else []) + common,
        "firstcut": py + [HERE / "review.py", "firstcut", "--session", args.session, "--music", args.music or "",
                          "--out", cut, "--seconds", str(args.seconds)] + common,
        "fast": py + [HERE / "review.py", "fast", "--cutlist", cut, "--session", args.session] + common,
        "notify": py + [HERE / "notify.py", "--session", args.session, "--cutlist", cut] + common,
    }
    if args.overnight:
        steps = [x for x in OVERNIGHT if STEPS.index(x) >= STEPS.index(args.start)]
    else:
        steps = STEPS[STEPS.index(args.start):STEPS.index(args.stop) + 1]
    if args.stabilizer == "none":
        steps = [s for s in steps if s not in ("stage", "pause", "check")]
    elif args.stabilizer == "gyroflow":
        steps = [s for s in steps if s != "stage"]
    if "ingest" in steps and not args.card:
        raise SystemExit("ingest needs the card path (or use --from report)")

    for step in steps:
        print(f"\n━━ {step} " + "━" * (60 - len(step)))
        if step == "pause":
            where = f"{stab / '_queue'}" if args.stabilizer == "hsp" else f"{raw} (Gyroflow: output to {stab})"
            print(f"Stabilize now: {where}\n  export into {stab}\nPress Enter when the export has finished "
                  "(Ctrl+C to stop here and resume later with --from check).")
            input()
            continue
        cmd = plan[step]
        if cmd is None:
            print("skipped (no --music, or its beat map already exists)")
            continue
        t0 = time.time()
        rc = subprocess.run([str(c) for c in cmd]).returncode
        if rc != 0 and step != "check":
            print(f"\n✗ {step} failed (exit {rc}). Fix it, then resume with --from {step}")
            return rc
        if rc != 0:
            print("Some exports need attention (see above); continuing with the good ones.")
        print(f"✓ {step} ({time.time() - t0:.0f}s)")
    print("\nDone. Open Resolve: REEL_selects and YT_selects are waiting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
