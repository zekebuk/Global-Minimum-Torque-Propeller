#!/usr/bin/env python3
"""Slide in-points so motion peaks land on beats, cuts avoid rolls and travel carries across cuts (fpvpipe/cutfit.py).

    python scripts/fit_cuts.py reel.json --window 1.2 --lock 1,10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import cutfit, cutlist as cl  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cutlist", type=Path)
    ap.add_argument("--window", type=float, default=1.2, help="seconds a shot may slide either way")
    ap.add_argument("--lock", default="", help="1-based shots to leave where they are, comma separated")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    c = cl.CutList.load(a.cutlist)
    lock = {int(x) for x in a.lock.split(",") if x}
    rep = cutfit.fit(c, a.window, lock, cache=a.cutlist.parent / "motion_cache")
    print(" #  moved  peak-to-beat  punch  roll in/out  carry  note")
    for r, s in zip(rep, c.shots):
        print(f"{r['shot']:2d} {r['moved']:+6.2f}s {r['peak_off']:9.2f}s {r['punch']:7.2f} {r['roll_in']:6d}/{r['roll_out']:<4d} "
              f"{'' if r['carry'] is None else format(r['carry'], '+.2f'):>6}  {s.note}")
    if not a.dry_run:
        c.save(a.cutlist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
