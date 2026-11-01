#!/usr/bin/env python3
"""Generate an FPV sound-design library with ElevenLabs, once, then reuse it in every edit.

    set ELEVENLABS_API_KEY=...            (PowerShell: $env:ELEVENLABS_API_KEY="...")
    python scripts/make_sfx_library.py --dry-run                 # see what would be generated
    python scripts/make_sfx_library.py --bpm 128 --variants 3    # risers sized to 1 / 2 bars at 128 BPM

Writes <SFX>/<category>/<name>_<n>.mp3 and <SFX>/library.json (prompt, model, date per file: your record of what
was generated under which plan). Existing files are skipped, so re-running only fills gaps.
Why these sounds: reels sell speed with sound. Whoosh = motion across frame/ramp, sub-hit = the drop, riser = tension
into the drop, motor/prop = realism layer under music. Import the folder into Resolve's Media Pool once (03_SFX).
Commercial use needs a paid ElevenLabs plan: check your plan's terms before publishing.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, elevenlabs  # noqa: E402

# name, category, prompt, duration (s) or ("bars", n) for tempo-locked sounds, loop
LIBRARY = [
    ("whoosh_short", "whoosh", "fast close air whoosh pass-by, clean, no music, cinematic", 0.8, False),
    ("whoosh_long", "whoosh", "long deep cinematic whoosh sweeping left to right, airy, no music", 1.8, False),
    ("whoosh_reverse", "whoosh", "reversed whoosh swelling into a cut, tight ending", 1.2, False),
    ("sub_hit", "impact", "deep cinematic sub bass hit with short tail, boom, trailer impact", 2.0, False),
    ("impact_metal", "impact", "punchy metallic impact hit with short reverb, cinematic", 1.5, False),
    ("riser_1bar", "riser", "tension riser building up, synthetic, ends abruptly, cinematic", ("bars", 1), False),
    ("riser_2bar", "riser", "long tension riser with rising pitch, ends abruptly on the downbeat", ("bars", 2), False),
    ("tape_stop", "transition", "tape stop slowdown effect, pitch dropping", 0.9, False),
    ("glitch_stutter", "transition", "short digital glitch stutter transition", 0.6, False),
    ("fpv_motor_rev", "drone", "FPV racing drone motors revving hard, high pitched brushless motor whine", 2.5, False),
    ("fpv_flyby", "drone", "FPV racing drone flying past close and fast, doppler, prop wash", 2.5, False),
    ("prop_wash_loop", "drone", "steady FPV drone propeller buzz, seamless loop, no doppler", 6.0, True),
    ("wind_rush_loop", "ambience", "high speed wind rush, seamless loop, no music", 8.0, True),
    ("dive_wind", "ambience", "wind rushing louder during a steep fast dive, building intensity", 3.0, False),
]

# Ambient set (--set ambient): seamless 30 s location beds + drone layers for music-free videos (build_soundscape.py).
AMBIENT = [
    ("forest", "bed", "calm forest ambience, light breeze in leaves, distant birds, no music, seamless loop", 30.0, True),
    ("mountain_wind", "bed", "high mountain ridge ambience, steady cold wind, open space, no music, seamless loop", 30.0, True),
    ("beach", "bed", "ocean waves breaking on a beach, gentle wind, no music, seamless loop", 30.0, True),
    ("river", "bed", "fast river and waterfall rushing water ambience, no music, seamless loop", 30.0, True),
    ("city", "bed", "distant city ambience from above, soft traffic hum, no voices close up, seamless loop", 30.0, True),
    ("snow", "bed", "quiet snowy mountain ambience, soft muffled wind, no music, seamless loop", 30.0, True),
    ("desert", "bed", "dry desert ambience, warm light wind, insects far away, no music, seamless loop", 30.0, True),
    ("night", "bed", "night ambience, crickets, calm air, no music, seamless loop", 30.0, True),
    ("quarry", "bed", "open quarry ambience, wind over rock walls, distant machinery hum, seamless loop", 30.0, True),
    ("motor_loop", "drone", "FPV drone brushless motors at steady mid throttle, close, seamless loop, no doppler", 10.0, True),
    ("whoosh_short", "whoosh", "fast close air whoosh pass-by, clean, no music, cinematic", 0.8, False),
    ("whoosh_long", "whoosh", "long deep cinematic whoosh sweeping left to right, airy, no music", 1.8, False),
    ("dive_wind", "ambience", "wind rushing louder during a steep fast dive, building intensity", 3.0, False),
]


def duration(spec, bpm: float) -> float:
    if isinstance(spec, tuple):
        return spec[1] * 4 * 60.0 / bpm
    return float(spec)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bpm", type=float, default=128.0, help="tempo the risers should fit (bars of 4/4)")
    ap.add_argument("--variants", type=int, default=2, help="takes per sound (pick the best in the edit)")
    ap.add_argument("--only", nargs="*", help="limit to these names or categories")
    ap.add_argument("--set", choices=["reel", "ambient", "all"], default="reel",
                    help="reel = music-video sound design (default), ambient = location beds + drone layers")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    el = cfg.get("elevenlabs", {})
    root = cfg["paths"].get("sfx") or (cfg["paths"]["music"].parent / "SFX")
    index_path = root / "library.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}

    todo = []
    chosen = {"reel": LIBRARY, "ambient": AMBIENT, "all": LIBRARY + [a for a in AMBIENT if a[0] not in {x[0] for x in LIBRARY}]}[args.set]
    for name, cat, prompt, spec, loop in chosen:
        if args.only and name not in args.only and cat not in args.only:
            continue
        sec = duration(spec, args.bpm)
        for n in range(1, args.variants + 1):
            out = root / cat / f"{name}{'_' + str(round(args.bpm)) + 'bpm' if isinstance(spec, tuple) else ''}_{n}.mp3"
            if not out.exists():
                todo.append((out, prompt, sec, loop))
    total_s = sum(t[2] for t in todo)
    print(f"{len(todo)} sounds to generate ({total_s:.0f} s of audio) into {root}")
    if args.dry_run:
        for out, prompt, sec, loop in todo:
            print(f"  {out.relative_to(root)}  {sec:.1f}s{' loop' if loop else ''}  '{prompt}'")
        return 0

    for out, prompt, sec, loop in todo:
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            audio = elevenlabs.sound_effect(prompt, sec, influence=el.get("sfx_prompt_influence", 0.4), loop=loop,
                                            model_id=el.get("sfx_model", "eleven_text_to_sound_v2"))
        except elevenlabs.ElevenLabsError as e:
            print(f"✗ {out.name}: {e}")
            if "401" in str(e) or "ELEVENLABS_API_KEY" in str(e):
                return 1
            continue
        out.write_bytes(audio)
        index[str(out.relative_to(root)).replace("\\", "/")] = {
            "prompt": prompt, "seconds": round(sec, 2), "loop": loop, "model": el.get("sfx_model", "eleven_text_to_sound_v2"),
            "generated": date.today().isoformat(), "source": "ElevenLabs sound effects"}
        index_path.write_text(json.dumps(index, indent=2))
        print(f"✓ {out.relative_to(root)} ({len(audio) / 1024:.0f} KB)")
    print(f"Library index: {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
