#!/usr/bin/env python3
"""Step 3a - Beat map for the music track: tempo, beats, downbeats, drops.

    python scripts/beat_map.py ~/FPV/Music/track.mp3

Writes track.beats.json next to the song. build_resolve_project.py places these as timeline markers on the
reel so you can snap cuts to them (blue = beat, purple = downbeat / bar start, red = drop).
Cutting rule of thumb for punchy reels: cut on downbeats, put the hardest move on the drop.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import beats, media  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("track", type=Path)
    args = ap.parse_args()
    y, sr = media.decode_audio(args.track)
    bm = beats.analyse(y, sr)
    out = args.track.with_suffix(".beats.json")
    out.write_text(json.dumps(asdict(bm), indent=2))
    bar = 4 * 60.0 / bm.tempo_bpm
    print(f"{args.track.name}: {bm.tempo_bpm:.1f} BPM, {len(bm.beats_s)} beats, bar = {bar:.2f}s")
    print(f"  drops at: {', '.join(f'{d:.1f}s' for d in bm.drops_s) or 'none detected'}")
    print(f"  shot-length menu: 1 beat {bar / 4:.2f}s | 2 beats {bar / 2:.2f}s | 1 bar {bar:.2f}s | 2 bars {2 * bar:.2f}s")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
