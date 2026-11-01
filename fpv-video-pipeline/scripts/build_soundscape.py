#!/usr/bin/env python3
"""Music-free videos: mix an ambience + motor + whoosh + dive-wind track that follows the flight.

    # whole edit, straight from the Resolve timeline (clips on V1), scene per clip:
    python scripts/build_soundscape.py --from-resolve YT_final --session 2026-11-02_quarry --scene mountain_wind
        --scenes GX010042=forest DJI_20261102_0003_D=river --place
    # quick test on one clip:
    python scripts/build_soundscape.py --clip D:\\FPV\\Library\\...\\raw\\GX010042.MP4 --scene forest

Needs the ambient SFX set once: make_sfx_library.py --set ambient. Telemetry comes from the ORIGINAL GoPro files
(stabilized exports are mapped back by name via the session's raw folder). DJI/no-telemetry shots get a steady motor
level and no automatic whooshes. Writes <exports>/soundscape_<name>.wav (48 kHz stereo); --place puts it on A3 of the
timeline. Caveats: speed-ramped (retimed) shots use source-speed timing, so nudge those whooshes by hand; mix the
camera's own audio low (wind noise) and check loudness on Fairlight's meter (-14 LUFS for YouTube).
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, gpmf, media, soundscape  # noqa: E402


def load_sfx(root: Path, category: str, name: str, fallbacks=()) -> np.ndarray:
    for nm in (name, *fallbacks):
        hits = sorted((root / category).glob(f"{nm}_*.mp3")) if (root / category).is_dir() else []
        if hits:
            y, _ = media.decode_audio(hits[0], soundscape.SR)
            return y.astype(np.float32)
    return np.zeros(0, np.float32)


def imu(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Full-clip angular rate (deg/s) and |accel| (g) vs. source time; empty arrays when there is no GPMF."""
    try:
        info = media.video_summary(media.ffprobe(path))
        tr = gpmf.parse(media.extract_gpmf(path))
    except Exception:
        return (np.zeros(0),) * 4
    g, a = tr["GYRO"].array(), tr["ACCL"].array()
    tg, ta = gpmf.timeline(tr["GYRO"], info["duration_s"]), gpmf.timeline(tr["ACCL"], info["duration_s"])
    rate = np.degrees(np.linalg.norm(g, axis=1)) if len(g) else np.zeros(0)
    accg = np.linalg.norm(a, axis=1) / 9.80665 if len(a) else np.zeros(0)
    return tg, rate, ta, accg


def window(t, x, start, dur):
    m = (t >= start) & (t < start + dur)
    return t[m] - start, x[m]


def original_for(path: Path, raw: Path | None) -> Path:
    if raw is None or not raw.is_dir():
        return path
    key = path.stem.upper().split("_STABILI")[0]
    for p in raw.iterdir():
        if p.stem.upper() == key and p.suffix.lower() in (".mp4", ".mov"):
            return p
    return path


def segments_from_resolve(name: str, raw: Path | None, scene_of) -> tuple[list[soundscape.Segment], float, object]:
    from fpvpipe import resolve as rlib
    project = rlib.connect().GetProjectManager().GetCurrentProject()
    tl = next((project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)
               if project.GetTimelineByIndex(i).GetName() == name), None)
    if not tl:
        raise SystemExit(f"timeline {name!r} not found")
    project.SetCurrentTimeline(tl)
    fps = float(tl.GetSetting("timelineFrameRate") or 30)
    t0 = tl.GetStartFrame()
    segs, cache = [], {}
    for it in tl.GetItemListInTrack("video", 1) or []:
        mpi = it.GetMediaPoolItem()
        if not mpi:
            continue
        src = original_for(Path(mpi.GetClipProperty("File Path")), raw)
        if src not in cache:
            cache[src] = imu(src)
        tg, rate, ta, accg = cache[src]
        start, dur, src_in = (it.GetStart() - t0) / fps, it.GetDuration() / fps, it.GetLeftOffset() / fps
        t, r = window(tg, rate, src_in, dur)
        tA, aG = window(ta, accg, src_in, dur)
        segs.append(soundscape.Segment(start, dur, scene_of(src.stem), t, r, tA, aG))
    return segs, (tl.GetEndFrame() - t0) / fps, (project, tl)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-resolve", metavar="TIMELINE")
    src.add_argument("--clip", type=Path)
    ap.add_argument("--session", help="library session (to find original GoPro files for telemetry)")
    ap.add_argument("--scene", default="mountain_wind", help="default ambience bed (bed/<scene>_N.mp3)")
    ap.add_argument("--scenes", nargs="*", default=[], help="per-clip overrides: CLIPNAME=scene")
    ap.add_argument("--place", action="store_true", help="import and put the result on A3 of the timeline")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    sfx = cfg["paths"].get("sfx") or cfg["paths"]["music"].parent / "SFX"
    raw = cfg["paths"]["library"] / args.session / "raw" if args.session else None
    overrides = {k.upper(): v for k, v in (s.split("=", 1) for s in args.scenes)}

    def scene_of(stem: str) -> str:
        return overrides.get(stem.upper().split("_STABILI")[0], args.scene)

    handle = None
    if args.clip:
        tg, rate, ta, accg = imu(args.clip)
        dur = media.video_summary(media.ffprobe(args.clip))["duration_s"]
        segs, total, name = [soundscape.Segment(0.0, dur, scene_of(args.clip.stem), tg, rate, ta, accg)], dur, args.clip.stem
    else:
        segs, total, handle = segments_from_resolve(args.from_resolve, raw, scene_of)
        name = args.from_resolve
    if not segs:
        raise SystemExit("no shots found")

    beds = {sc: load_sfx(sfx, "bed", sc) for sc in {s.scene for s in segs}}
    missing = [sc for sc, b in beds.items() if not len(b)]
    if missing:
        print(f"! no bed for {missing}: run make_sfx_library.py --set ambient (or --only {' '.join(missing)})")
    motor = load_sfx(sfx, "drone", "motor_loop", ("prop_wash_loop",))
    whoosh = load_sfx(sfx, "whoosh", "whoosh_short")
    dive = load_sfx(sfx, "ambience", "dive_wind")
    if not len(motor):
        print("! no motor/prop loop found: motor layer silent")

    bus, stats = soundscape.mix(segs, {k: v for k, v in beds.items() if len(v)}, motor, whoosh, dive, total)
    out = cfg["paths"]["exports"] / f"soundscape_{name}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(bus, -1, 1) * 32767).astype("<i2")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(soundscape.SR)
        w.writeframes(np.repeat(pcm, 2).tobytes())
    no_imu = sum(1 for s in segs if len(s.t) < 4)
    print(f"✓ {out}  {total:.1f}s, {len(segs)} shots, scenes {stats['scenes']}, "
          f"{stats['whooshes']} whooshes, {stats['dives']} dive swells"
          + (f", {no_imu} shot(s) without telemetry (steady motor)" if no_imu else ""))

    if args.place and handle:
        project, tl = handle
        mp = project.GetMediaPool()
        items = mp.ImportMedia([str(out)]) or []
        ok = items and mp.AppendToTimeline([{"mediaPoolItem": items[0], "mediaType": 2, "trackIndex": 3,
                                              "recordFrame": tl.GetStartFrame()}])
        print("placed on A3" if ok else "could not place on A3 automatically: drag it from the Media Pool")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
