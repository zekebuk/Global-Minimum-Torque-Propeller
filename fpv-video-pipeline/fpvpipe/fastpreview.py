"""Preview a cut list without Resolve: graded, cropped, on-beat, with music, in seconds per changed shot.

Per shot: solve the grade on the used range (cached), bake BAL + CONT + camera LUT into one .cube, then one ffmpeg
pass: fast seek -> trim -> 16-bit RGB -> lut3d -> crop/scale to the preview frame -> constant fps -> H.264 segment.
Segments are cached by (source, in/out, grade, size, fps), so after a note like "shot 3 shorter" only shot 3 renders.
Segments are joined with the concat demuxer (no re-encode) and the music is laid in at the cut list's music offset.
The colour result matches what auto_grade writes into Resolve (same solver, same LUT, same node order); reframing
for vertical is a centre crop here (keyframed reframes stay a Resolve finishing step).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import camera, cutlist as cl, grading, media


@dataclass
class PreviewResult:
    video: Path
    rendered: int
    reused: int
    notes: list[str]


def _frame_geometry(vertical: bool) -> tuple[int, int, str]:
    if vertical:   # 9:16, centre crop (FPV sources are 4:3 / 8:7 / 16:9)
        return 540, 960, "scale=540:960:force_original_aspect_ratio=increase,crop=540:960"
    return 960, 540, "scale=960:540:force_original_aspect_ratio=increase,crop=960:540"


def _segment(src: str, in_s: float, dur: float, cube: Path | None, vf_geom: str, fps: float, out: Path):
    vf = ["format=rgb48le"]
    if cube is not None:
        cpath = str(cube).replace("\\", "/").replace(":", "\\:")
        vf.append(f"lut3d=file='{cpath}':interp=trilinear")
    vf += [vf_geom, f"fps={fps}", "format=yuv420p"]
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{in_s:.3f}", "-t", f"{dur:.3f}", "-i", src,
           "-vf", ",".join(vf), "-an", "-c:v", "libx264", "-preset", "veryfast", "-b:v", "4M", "-maxrate", "6M",
           "-bufsize", "8M", "-g", str(int(round(fps))), "-color_primaries", "bt709", "-color_trc", "bt709",
           "-colorspace", "bt709", "-frames:v", str(int(round(dur * fps))), str(out)]
    subprocess.run(cmd, check=True)


def build(c: cl.CutList, cfg: dict, out: Path, look: str = "reel", vertical: bool = True,
          profile_of=None, cache_dir: Path | None = None) -> PreviewResult:
    cache = cache_dir or out.parent / ".preview_cache"
    seg_dir, cube_dir, grade_dir = cache / "segments", cache / "cubes", cache / "grades"
    for d in (seg_dir, cube_dir, grade_dir):
        d.mkdir(parents=True, exist_ok=True)
    order = cfg["color"].get("nodes", {}).get("order", "scene")
    fps = float(c.fps)
    w, h, geom = _frame_geometry(vertical)
    notes, segs, rendered, reused = [], [], 0, 0
    prof_cache: dict[str, str] = {}

    for n, s in enumerate(c.shots, 1):
        src = Path(s.src)
        if src.as_posix() not in prof_cache:
            prof_cache[src.as_posix()] = profile_of(src) if profile_of else camera.identify(src, look=False).profile
        lut = grading.lut_for(prof_cache[src.as_posix()], cfg, None)
        cube, gkey = None, "ungraded"
        if lut:
            bal, cont, _, _ = grading.shot_grade(src, s.in_s, s.dur, lut, look, order, grade_dir)
            gkey = grading.grade_key(src, s.in_s, s.dur, lut, look, order)
            cube = cube_dir / f"{gkey}.cube"
            if not cube.exists():
                grading.bake_cube(lut, bal, cont, order, cube)
        else:
            notes.append(f"shot {n}: no LUT for profile {prof_cache[src.as_posix()]!r}, shown ungraded")
        key = hashlib.sha1(f"{src.resolve()}|{s.in_s:.4f}|{s.dur:.4f}|{gkey}|{w}x{h}|{fps}".encode()).hexdigest()[:16]
        seg = seg_dir / f"{key}.mp4"
        if seg.exists() and seg.stat().st_size > 0:
            reused += 1
        else:
            _segment(str(src), s.in_s, s.dur, cube, geom, fps, seg)
            rendered += 1
        segs.append(seg)

    lst = cache / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segs))
    out.parent.mkdir(parents=True, exist_ok=True)
    total = c.duration()
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst)]
    if c.music and Path(c.music).exists():
        cmd += ["-ss", f"{c.music_in_s:.3f}", "-t", f"{total:.3f}", "-i", c.music,
                "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-af", "afade=t=out:st="
                f"{max(0.0, total - 0.4):.3f}:d=0.4"]
    elif c.music:
        notes.append(f"music not found: {c.music}")
    cmd += ["-c:v", "copy", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    (out.with_suffix(".json")).write_text(json.dumps({"shots": len(c.shots), "duration_s": round(total, 3),
                                                      "rendered": rendered, "reused": reused, "notes": notes}, indent=2))
    return PreviewResult(out, rendered, reused, notes)
