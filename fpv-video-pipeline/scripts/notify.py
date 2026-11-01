#!/usr/bin/env python3
"""Last overnight step: put the preview where your phone sees it, optionally push a notification, and leave a
LATEST.json pointer so Claude can find "last night's preview" when you open the app in the morning.

    python scripts/notify.py --session 2026-11-03_quarry --cutlist D:/FPV/Review/2026-11-03_quarry/reel.json

Phone delivery options ([notify] in config.toml): copy into a Drive/OneDrive-synced folder (phone_folder), and/or a
push via ntfy (ntfy_topic). The push carries only text: session, shots, length, where the file is.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, cutlist as cl  # noqa: E402


def push_ntfy(server: str, topic: str, title: str, message: str) -> bool:
    req = urllib.request.Request(f"{server.rstrip('/')}/{topic}", data=message.encode(),
                                 headers={"Title": title, "Tags": "movie_camera"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True
    except Exception as e:
        print(f"push failed ({e}); preview is still in place")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True)
    ap.add_argument("--cutlist", type=Path, required=True)
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    nt = cfg.get("notify", {})
    c = cl.CutList.load(args.cutlist)
    base = args.cutlist.parent / f"{c.name}_v{c.version}_fast"
    files = [p for p in (base.with_suffix(".mp4"), base.parent / (base.name + "_shots.jpg"), base.with_suffix(".txt"))
             if p.exists()]
    if not files:
        print("no preview found: run review.py fast first")
        return 1
    delivered = []
    if nt.get("phone_folder"):
        dest = Path(nt["phone_folder"]).expanduser() / args.session
        dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dest / f.name)
            delivered.append(str(dest / f.name))
        print(f"copied {len(files)} file(s) to {dest}")
    fillers = sum(1 for s in c.shots if "filler" in s.note)
    msg = (f"{len(c.shots)} shots, {c.duration():.0f}s" + (f", {fillers} filler" if fillers else "") +
           ". Open Claude and say: review last night's preview.")
    if nt.get("ntfy_topic"):
        if push_ntfy(nt.get("ntfy_server", "https://ntfy.sh"), nt["ntfy_topic"], f"FPV {args.session}: v{c.version} ready", msg):
            print("push sent")
    review_root = args.cutlist.parent.parent
    (review_root / "LATEST.json").write_text(json.dumps({
        "session": args.session, "version": c.version, "time": time.strftime("%Y-%m-%d %H:%M"),
        "cutlist": str(args.cutlist), "files": [str(f) for f in files], "phone_copies": delivered, "summary": msg}, indent=2))
    print(f"{args.session}: {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
