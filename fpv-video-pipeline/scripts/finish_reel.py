#!/usr/bin/env python3
"""One cut list to a finished reel, unattended: Resolve project, closed-loop grade, unifying layer, title, master, phone copy.

    python scripts/finish_reel.py reel.json --project SENJA_FJORD --aspect 4:3 --masters "G:/My Drive/FPV Reels"

Steps: build_reel_project.py (a new project and timeline), converge_grade.py (per clip, then --unify),
title_behind_resolve.py when the title has "behind": true, then render the master, set its loudness, read the READY FOR
EXPORT checklist, copy it to --masters and make a phone copy under 30 MiB next to the cut list. Stops at the first failure.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, cutlist as cl, deliver, resolve as rlib, titles as tt  # noqa: E402

HERE = Path(__file__).resolve().parent
SIZE = {"4:3": (1440, 1080), "9:16": (1080, 1920), "uhd": (3840, 2160)}


def run(*args):
    print(">>", " ".join(str(a) for a in args), flush=True)
    subprocess.run([sys.executable, *map(str, args)], check=True)


def phone_copy(master: Path, out: Path, limit_mib: float = 29.0) -> float:
    dur = deliver.probe(master)["duration"] if "duration" in deliver.probe(master) else None
    if dur is None:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(master)],
                           capture_output=True, text=True)
        dur = float(r.stdout.strip())
    kbps = int(limit_mib * 8 * 1024 / dur) - 200
    kbps = min(kbps, 8000)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(master), "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-b:v", f"{kbps}k",
                    "-maxrate", f"{int(kbps * 1.3)}k", "-bufsize", f"{kbps * 2}k", "-pix_fmt", "yuv420p", "-colorspace", "bt709",
                    "-color_primaries", "bt709", "-color_trc", "bt709", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                    str(out)], check=True)
    return out.stat().st_size / 2**20


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cutlist", type=Path)
    ap.add_argument("--project", required=True)
    ap.add_argument("--aspect", default="4:3", choices=sorted(SIZE))
    ap.add_argument("--unify", type=float, default=0.35)
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--masters", type=Path)
    ap.add_argument("--render-only", action="store_true", help="Resolve is on the project already: only render, check and copy")
    ap.add_argument("--config", type=Path)
    a = ap.parse_args()
    cfg, c = config.load(a.config), cl.CutList.load(a.cutlist)
    w, h = SIZE[a.aspect]
    cache = a.cutlist.parent / ".preview_cache"
    cache.mkdir(exist_ok=True)
    flat = [t for t in c.titles if not t.get("behind")]
    if flat:                                                  # the builder imports this PNG; no preview render needed
        tt.render_png(flat[0], w, h, cache / f"{c.name}_title_0.png")

    if not a.render_only:
        run(HERE / "build_reel_project.py", a.cutlist, "--name", a.project, "--aspect", a.aspect)
        run(HERE / "converge_grade.py", a.cutlist, "--project", a.project, "--aspect", a.aspect, "--iters", a.iters, "--unify", a.unify)
        if any(t.get("behind") for t in c.titles):
            run(HERE / "title_behind_resolve.py", a.cutlist, "--project", a.project, "--width", w, "--height", h)

    resolve = rlib.connect()
    p = resolve.GetProjectManager().GetCurrentProject()
    tl = p.GetCurrentTimeline()
    spec = {**cfg["reel"], "width": w, "height": h}
    if a.aspect == "uhd":            # YouTube: H.265, its own rate, no reel length limit
        spec = {**spec, **cfg["youtube"], "bitrate_kbps": 45000, "max_seconds": 900, "fps": c.fps}
    out = deliver.render(p, tl, spec, cfg["paths"]["exports"] / a.project, c.name, log=lambda *x, **k: None)
    lufs, gain = deliver.set_loudness(out)
    print(f"loudness {lufs:.1f} LUFS, gain {gain:+.1f} dB", flush=True)
    c2 = cl.CutList.load(a.cutlist)
    ok, text = deliver.verdict(out, spec, graded=len(c2.shots), shots=len(c2.shots), music=Path(c2.music).stem if c2.music else "")
    print(text, flush=True)
    if a.masters:
        a.masters.mkdir(parents=True, exist_ok=True)
        shutil.copy(out, a.masters / out.name)
    mib = phone_copy(out, a.cutlist.with_name(f"{c.name}_phone.mp4"))
    print(f"phone copy {mib:.1f} MiB", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
