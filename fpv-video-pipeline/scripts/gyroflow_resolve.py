#!/usr/bin/env python3
"""Step 3b (in Resolve) - Stabilize an existing timeline with the Gyroflow OpenFX plugin (Resolve Studio must be open).

    python scripts/gyroflow_resolve.py                                   # current project + timeline, reel preset
    python scripts/gyroflow_resolve.py --project MOAB_reel --timeline MOAB_reel_v2 --preset youtube
    python scripts/gyroflow_resolve.py --timeline MOAB_reel_v2 --copy    # work on a duplicate, keep the original
    python scripts/gyroflow_resolve.py --remove                          # take Gyroflow back off

Every clip on V1 whose source still has gyro data gets a Fusion comp MediaIn -> Gyroflow -> MediaOut, pointed at the
source file, at the source size and the clip's speed, with the [gyroflow.<preset>] settings from config.toml.
Clips without gyro (stabilized exports, re-encodes) and clips that already have a Fusion comp are left alone.
Re-running updates the settings in place. Install the plugin first (docs/PIPELINE.md 3b); doctor.py checks it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, gyroflow, resolve as rlib  # noqa: E402

LABELS = {"added": "stabilized", "updated": "settings updated", "no gyro": "skipped, no gyro data (already stabilized?)",
          "has comp": "skipped, has its own Fusion comp", "no source": "skipped, no source file", "failed": "FAILED"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="default: the open project")
    ap.add_argument("--timeline", help="default: the current timeline")
    ap.add_argument("--preset", default="reel", help="[gyroflow.<preset>] in config.toml (reel / youtube)")
    ap.add_argument("--track", type=int, default=1)
    ap.add_argument("--copy", action="store_true", help="duplicate the timeline as <name>_gyro and work on the copy")
    ap.add_argument("--remove", action="store_true", help="delete the Gyroflow comps this script added")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    if not gyroflow.plugin_installed():
        raise SystemExit("Gyroflow OpenFX plugin not installed - see docs/PIPELINE.md section 3b")

    resolve = rlib.connect()
    pm = resolve.GetProjectManager()
    if args.project:
        pm.GotoRootFolder()
        p = pm.LoadProject(args.project)
    else:
        p = pm.GetCurrentProject()
    if not p:
        raise SystemExit(f"could not open project {args.project or '(none open)'}")
    if args.timeline:
        tl = next((p.GetTimelineByIndex(i) for i in range(1, p.GetTimelineCount() + 1)
                   if p.GetTimelineByIndex(i).GetName() == args.timeline), None)
    else:
        tl = p.GetCurrentTimeline()
    if not tl:
        raise SystemExit(f"no timeline {args.timeline!r} in {p.GetName()}")
    if args.copy:
        p.SetCurrentTimeline(tl)
        tl = tl.DuplicateTimeline(f"{tl.GetName()}_gyro") or raise_(f"could not duplicate {tl.GetName()}")
    p.SetCurrentTimeline(tl)

    if args.remove:
        n = sum(gyroflow.remove(it) for it in tl.GetItemListInTrack("video", args.track) or [])
        pm.SaveProject()
        print(f"{tl.GetName()}: Gyroflow removed from {n} clip(s)")
        return 0
    settings = gyroflow.preset(cfg, args.preset)
    res = gyroflow.stabilize_timeline(tl, settings, args.track)
    pm.SaveProject()
    print(f"{p.GetName()} / {tl.GetName()}  preset {args.preset}: " + ", ".join(f"{k} {v:g}" for k, v in settings.items()))
    for status, names in res.items():
        print(f"  {LABELS.get(status, status)}: {len(names)}  ({', '.join(dict.fromkeys(names))})")
    print("Check one stabilized clip on the Fusion page: the Gyroflow tool's Status should read OK and Loaded lens "
          "profile should name your camera.")
    return 1 if "failed" in res else 0


def raise_(msg: str):
    raise SystemExit(msg)


if __name__ == "__main__":
    raise SystemExit(main())
