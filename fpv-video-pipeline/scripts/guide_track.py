#!/usr/bin/env python3
"""Cut to a song you only have inside Edits/Instagram, without a copy of it: a click "guide track" with its timing.

The final music is added in Edits, so the edit only needs the song's timing: tempo, where beats fall, where the
drop is. This builds a local metronome WAV with that timing (accented bar starts, a loud hit on the drop) plus
the same .beats.json that beat_map.py writes, so build_resolve_project.py puts beat/bar/DROP markers on the reel.
The guide sits on A2 like a song would; render.py --no-music mutes it, so it never ships.

1) Get the timing (any one):
     python scripts/guide_track.py tap        # play the song (phone/Edits), press Enter on every beat ~16x,
                                              # then once more exactly on the drop; prints BPM, phase, drop
     or read BPM off the song's page / Edits, and note the drop time while previewing it
2) Make the guide for the part of the song you'll use:
     python scripts/guide_track.py make --bpm 128 --first-beat 0.42 --drop 52.5 --from 44 --to 76 --name trend_x
   -> <Music>/trend_x.guide.wav (+ trend_x.guide.beats.json, trend_x.guide.json = song time at file start)
3) run_session.py ... --music <Music>/trend_x.guide.wav   then cut, render --no-music, and
   reel_handoff.py --from-resolve REEL_final   prints the Edits start time in SONG time (offset included).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config  # noqa: E402

SR = 48000


def fit_taps(taps: list[float], drop: float | None = None) -> dict:
    """Least squares tap_k = phase + k*period averages out human tap jitter (~20-40 ms per tap)."""
    t = np.asarray(taps, dtype=float)
    n = np.arange(len(t))
    period, phase = np.polyfit(n, t, 1)
    if not 0.25 <= period <= 1.5:  # 40-240 BPM
        raise ValueError(f"taps imply {60 / period:.0f} BPM: tap once per beat (or half-time/double-time)")
    out = {"bpm": round(60 / period, 2), "phase_s": round(float(phase), 3),
           "jitter_ms": round(float(np.std(t - (phase + n * period)) * 1000), 1)}
    if drop is not None:
        k = round((drop - phase) / period)
        out.update(drop_beats=k, drop_s=round(float(phase + k * period), 3))
    return out


def tap() -> int:
    print("Play the song. Press Enter on each beat (16+ taps). Type d then Enter exactly on the drop. q to finish.")
    taps, drop, t0 = [], None, None
    while True:
        s = input()
        now = time.perf_counter()
        t0 = t0 or now
        if s.strip().lower() == "q":
            break
        if s.strip().lower() == "d":
            drop = now - t0
            print("  drop noted")
            continue
        taps.append(now - t0)
    if len(taps) < 8:
        print("need at least 8 beat taps")
        return 1
    try:
        r = fit_taps(taps, drop)
    except ValueError as e:
        print(e)
        return 1
    print(f"tempo {r['bpm']:.1f} BPM (tap jitter {r['jitter_ms']:.0f} ms; under ~40 ms is good)")
    if "drop_s" in r:
        print(f"drop {r['drop_beats']} beats after your first tap ({r['drop_beats'] / 4:.2f} bars) = {r['drop_s']:.2f}s")
    print("Times are relative to your first tap. Note the song time of that first tap in Edits/your player, add it to "
          "the numbers above, and pass them to `make` (--first-beat, --drop).")
    return 0


def click(freq: float, dur: float, amp: float) -> np.ndarray:
    tt = np.arange(int(SR * dur)) / SR
    return amp * np.sin(2 * np.pi * freq * tt) * np.exp(-tt * 40)


def make(args, cfg) -> int:
    period = 60.0 / args.bpm
    start, end = args.from_s, args.to_s
    if end <= start:
        raise SystemExit("--to must be after --from")
    first = args.first_beat
    k0 = int(np.ceil((start - first) / period))
    beats_song = [first + k * period for k in range(k0, 100000) if first + k * period < end]
    y = np.zeros(int(SR * (end - start)), dtype=np.float32)
    rel = []
    for b in beats_song:
        k = round((b - first) / period)
        bar = k % args.beats_per_bar == 0
        c = click(1500 if bar else 1000, 0.05, 0.6 if bar else 0.35)
        i = int((b - start) * SR)
        y[i:i + len(c)] += c[: len(y) - i]
        rel.append(round(b - start, 4))
    drops = []
    if args.drop is not None and start <= args.drop < end:
        tt = np.arange(int(SR * 0.6)) / SR
        boom = 0.9 * np.sin(2 * np.pi * 55 * tt) * np.exp(-tt * 6)
        i = int((args.drop - start) * SR)
        y[i:i + len(boom)] += boom[: len(y) - i]
        drops = [round(args.drop - start, 4)]
    y = np.clip(y, -1, 1)

    out = cfg["paths"]["music"] / f"{args.name}.guide.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((y * 32767).astype("<i2").tobytes())
    downs = [t for t, b in zip(rel, beats_song) if round((b - first) / period) % args.beats_per_bar == 0]
    Path(str(out)[:-4] + ".beats.json").write_text(json.dumps(
        {"tempo_bpm": args.bpm, "beats_s": rel, "downbeats_s": downs, "drops_s": drops}, indent=2))
    out.with_suffix(".json").write_text(json.dumps(
        {"song_time_at_file_start": start, "bpm": args.bpm, "first_beat": first, "drop": args.drop,
         "note": "timing-only guide; add the real song in Edits"}, indent=2))
    print(f"✓ {out}  ({end - start:.1f}s of song time {start:.1f}-{end:.1f}, {len(rel)} beats"
          f"{', drop at file ' + str(drops[0]) + 's' if drops else ''})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tap")
    m = sub.add_parser("make")
    m.add_argument("--bpm", type=float, required=True)
    m.add_argument("--first-beat", type=float, default=0.0, help="song time (s) of any downbeat (bar start)")
    m.add_argument("--drop", type=float, help="song time (s) of the drop")
    m.add_argument("--from", dest="from_s", type=float, default=0.0, help="song time where the guide starts")
    m.add_argument("--to", dest="to_s", type=float, required=True, help="song time where the guide ends")
    m.add_argument("--beats-per-bar", type=int, default=4)
    m.add_argument("--name", required=True)
    m.add_argument("--config", type=Path)
    args = ap.parse_args()
    if args.cmd == "tap":
        return tap()
    return make(args, config.load(args.config))


if __name__ == "__main__":
    raise SystemExit(main())
