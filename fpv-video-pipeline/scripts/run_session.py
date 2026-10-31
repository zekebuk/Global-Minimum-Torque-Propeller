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
STEPS = ["ingest", "report", "highlights", "stage", "pause", "check", "beats", "build"]


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
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()

    sys.path.insert(0, str(HERE.parent))
    from fpvpipe import config
    cfg = config.load(args.config)
    raw = cfg["paths"]["library"] / args.session / "raw"
    stab = cfg["paths"]["stabilized"] / args.session
    common = ["--config", str(args.config)] if args.config else []
    py = [sys.executable]

    plan = {
        "ingest": py + [HERE / "ingest.py", args.card or "", "--session", args.session] + common,
        "report": py + [HERE / "camera_report.py", raw] + common,
        "highlights": py + [HERE / "find_highlights.py", raw] + common,
        "stage": py + [HERE / "stage_for_stabilization.py", args.session] + (["--all"] if args.all else []) + common,
        "pause": None,
        "check": py + [HERE / "check_stabilized.py", args.session] + common,
        "beats": py + [HERE / "beat_map.py", args.music] if args.music else None,
        "build": py + [HERE / "build_resolve_project.py", args.session] + (["--music", args.music] if args.music else []) + common,
    }
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
            print("skipped (no --music)")
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
