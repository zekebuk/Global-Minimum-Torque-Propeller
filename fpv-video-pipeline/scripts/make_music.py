#!/usr/bin/env python3
"""Generate an instrumental track sized to the edit, then beat-map it, ready for the Resolve build.

    python scripts/make_music.py reel --bpm 128 --seconds 24 --mood "dark aggressive phonk"
    python scripts/make_music.py youtube --bpm 100 --seconds 240 --mood "uplifting cinematic, warm synths"

The prompt asks for the structure the edit needs: reels get a short intro, a build, and a hard drop placed at
--drop seconds (default: ~30% in, after the hook), YouTube tracks get evolving sections without a big drop.
After generation it runs the beat detector and prints requested vs. detected tempo and where the drop actually
landed, so you know whether to regenerate (--seed changes the take). Writes <Music>/<name>.mp3, .beats.json and
.license.json (prompt, model, date: keep it as your record for platform disputes). Needs ELEVENLABS_API_KEY and a
paid plan for commercial use; check ElevenLabs' current music terms for your plan.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import beats, config, elevenlabs, media  # noqa: E402


def build_prompt(kind: str, bpm: float, seconds: float, mood: str, drop_s: float) -> str:
    if kind == "reel":
        return (f"Instrumental track, {mood}, exactly {bpm:.0f} BPM in 4/4, strong steady kick on every beat. "
                f"Length {seconds:.0f} seconds. Structure: punchy 1-bar intro, tension build, then a hard bass drop "
                f"at about {drop_s:.0f} seconds, high energy until the end, ends cleanly on a downbeat so it loops. "
                "Mixed loud and clear for phone speakers. No vocals.")
    return (f"Instrumental track, {mood}, {bpm:.0f} BPM in 4/4. Length {seconds:.0f} seconds. Evolving sections "
            "every 30-45 seconds with gentle builds and releases, no abrupt drops, cinematic and immersive, "
            "suitable under long flowing drone footage. Soft fade-out ending. No vocals.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["reel", "youtube"])
    ap.add_argument("--bpm", type=float, required=True)
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--mood", default="epic energetic electronic, cinematic")
    ap.add_argument("--drop", type=float, help="reel: second the drop should land (default 30%% of length)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--name", help="output file name (default: <kind>_<bpm>bpm_<date>_<seed>)")
    ap.add_argument("--dry-run", action="store_true", help="print the request, generate nothing")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    model = cfg.get("elevenlabs", {}).get("music_model", "music_v1")

    drop = args.drop if args.drop is not None else round(args.seconds * 0.3, 1)
    prompt = build_prompt(args.kind, args.bpm, args.seconds, args.mood, drop)
    body = elevenlabs.music_body(prompt, args.seconds, model, True, args.seed)
    name = args.name or f"{args.kind}_{args.bpm:.0f}bpm_{date.today().isoformat()}_{args.seed if args.seed is not None else 'x'}"
    out = cfg["paths"]["music"] / f"{name}.mp3"
    if args.dry_run:
        print(json.dumps(body, indent=2))
        print(f"-> {out}")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        audio = elevenlabs.music(prompt, args.seconds, model, True, args.seed)
    except elevenlabs.ElevenLabsError as e:
        print(f"✗ {e}")
        return 1
    out.write_bytes(audio)
    out.with_suffix(".license.json").write_text(json.dumps({
        "source": "ElevenLabs Music", "model": model, "generated": date.today().isoformat(),
        "request": body, "note": "Commercial use per your ElevenLabs plan terms at generation date."}, indent=2))

    y, sr = media.decode_audio(out)
    bm = beats.analyse(y, sr)
    out.with_suffix(".beats.json").write_text(json.dumps(asdict(bm), indent=2))
    dur = len(y) / sr
    print(f"✓ {out.name}: {dur:.1f}s (asked {args.seconds:.0f}s), tempo {bm.tempo_bpm:.1f} BPM (asked {args.bpm:.0f})")
    if abs(bm.tempo_bpm - args.bpm) > 2 and abs(bm.tempo_bpm - 2 * args.bpm) > 3 and abs(bm.tempo_bpm * 2 - args.bpm) > 3:
        print("  ! tempo drifted from the request: beat markers follow the detected tempo, or regenerate with --seed")
    if args.kind == "reel":
        near = min(bm.drops_s, key=lambda d: abs(d - drop), default=None)
        if near is None:
            print(f"  ! no clear drop detected (asked at {drop:.0f}s): try another --seed")
        else:
            print(f"  drop detected at {near:.1f}s (asked {drop:.0f}s)")
    print(f"Next: run_session.py ... --music {out}   (or build_resolve_project.py <session> --music {out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
