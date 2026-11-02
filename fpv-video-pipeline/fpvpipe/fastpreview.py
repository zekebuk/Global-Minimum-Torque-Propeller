"""Preview a cut list without Resolve: graded, cropped, on-beat, with music, in seconds per changed shot.

Per shot: solve the grade on the used range (cached), bake BAL + CONT + camera LUT into one .cube, then one ffmpeg
pass: fast seek -> trim -> 16-bit RGB -> lut3d -> crop/scale to the preview frame -> constant fps -> H.264 segment.
Segments are cached by (source, in/out, grade, size, fps), so after a note like "shot 3 shorter" only shot 3 renders.
Segments are joined with the concat demuxer (no re-encode) and the music is laid in at the cut list's music offset.
The colour result matches what auto_grade writes into Resolve (same solver, same LUT, same node order); reframing
for vertical is a centre crop here (keyframed reframes stay a Resolve finishing step).

look="fivenode" uses the scope-solved 5-node model in fivenode (per-shot overrides from the cut list's "grade",
plus two spatial tools a LUT cannot hold: "clarity" = local contrast on luma, "sky_grad" = graduated darkening of
the top of the frame in stops).
look="cinema" uses the scene-referred node tree in cinelook (GP-Log decoded from scratch, no camera LUT) with only
exposure solved per shot, plus light grain and a soft vignette. letterbox=True keeps the full 16:9 picture and
centres it on a 9:16 canvas with black bars (horizontal FPV motion, vertical delivery).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import camera, cinelook, colorsci as cs, cutlist as cl, fivenode, grade5, grading, media


@dataclass
class PreviewResult:
    video: Path
    rendered: int
    reused: int
    notes: list[str]


def _frame_geometry(vertical: bool, letterbox: bool = False) -> tuple[int, int, str]:
    if letterbox:  # 16:9 picture centred on a 1080x1920 canvas; the pad is applied after grain/vignette
        return 1080, 1920, "scale=1080:608:force_original_aspect_ratio=increase,crop=1080:608"
    if vertical:   # 9:16, centre crop (FPV sources are 4:3 / 8:7 / 16:9)
        return 540, 960, "scale=540:960:force_original_aspect_ratio=increase,crop=540:960"
    return 960, 540, "scale=960:540:force_original_aspect_ratio=increase,crop=960:540"


def _segment(src: str, in_s: float, dur: float, cube: Path | None, vf_geom: str, fps: float, out: Path,
             post: list[str] = (), bitrate: str = "4M", speed: float = 1.0):
    vf = ["format=rgb48le"]
    if speed != 1.0:
        vf.append(f"setpts=PTS/{speed:.4f}")
    if cube is not None:
        cpath = str(cube).replace("\\", "/").replace(":", "\\:")
        vf.append(f"lut3d=file='{cpath}':interp=trilinear")
    # tag the frames themselves: newer ffmpeg takes colour metadata from the frames over -color_* output options
    vf += [vf_geom, *post, f"fps={fps}", "format=yuv420p",
           "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"]
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{in_s:.3f}", "-t", f"{dur * speed:.3f}", "-i", src,
           "-vf", ",".join(vf), "-an", "-c:v", "libx264", "-preset", "veryfast", "-b:v", bitrate,
           "-maxrate", bitrate, "-bufsize", bitrate, "-g", str(int(round(fps))), "-color_primaries", "bt709", "-color_trc", "bt709",
           "-colorspace", "bt709", "-frames:v", str(int(round(dur * fps))), str(out)]
    subprocess.run(cmd, check=True)


def _cinema_cube(src: Path, s: cl.Shot, cube_dir: Path) -> tuple[Path, str, float]:
    """Nodes 1-7 of cinelook for one shot, exposure solved on the used range. Cached by source range + look."""
    lk = cinelook.Look()
    raw = f"{src.resolve()}|{src.stat().st_mtime_ns}|{s.in_s:.3f}|{s.dur:.3f}|{sorted(lk.to_dict().items())}|cine2"
    gkey = "cine_" + hashlib.sha1(raw.encode()).hexdigest()[:16]
    cube, meta = cube_dir / f"{gkey}.cube", cube_dir / f"{gkey}.json"
    if cube.exists() and meta.exists():
        return cube, gkey, json.loads(meta.read_text())["ev"]
    frames = grading.sample_frames(src, s.in_s, s.span, n=5, width=256)
    ev = cinelook.solve_exposure(grading.pool(frames, 30000), lk)
    cinelook.write_cube(cube, lk, cinelook.ShotNodes(ev=ev), title=gkey)
    meta.write_text(json.dumps({"ev": ev, "look": lk.to_dict()}))
    return cube, gkey, ev


def _fivenode_cube(src: Path, s: cl.Shot, profile: str, cfg: dict, cube_dir: Path) -> tuple[Path, str, list[str]]:
    """Solve the 5-node grade on the shot's used range and bake nodes 1-5 into one cube (plus one cube per node and
    the solved values next to it, for building the same tree in Resolve). Cached by source range + targets."""
    t = {k: v for k, v in s.grade.items() if k not in ("clarity", "sky_grad")}   # spatial tools are not node targets
    raw = (f"{src.resolve()}|{src.stat().st_mtime_ns}|{s.in_s:.3f}|{s.dur:.3f}|{profile}|{sorted(t.items())}|"
           f"{sorted(fivenode.TARGETS.items())}|five15")
    gkey = "five_" + hashlib.sha1(raw.encode()).hexdigest()[:16]
    cube, meta = cube_dir / f"{gkey}.cube", cube_dir / f"{gkey}.json"
    if cube.exists() and meta.exists():
        return cube, gkey, json.loads(meta.read_text())["notes"]
    if profile == "GP-Log2":
        cst = fivenode.CST(cs.load_cube(cfg["color"]["luts"]["GP-Log2"]))
    else:
        cst = fivenode.DRT(white_desat=fivenode.targets_for(t)["white_desat"])
    frames = grading.sample_frames(src, s.in_s, s.span, n=5, width=256)
    g = fivenode.solve(grading.pool(frames, 30000), cst, t)
    cs.write_cube(cube, lambda v: g.apply(v), n=65, title=gkey)
    fivenode.write_node_cubes(g, cube_dir / gkey, "node")
    meta.write_text(json.dumps(g.to_dict(), indent=1))
    return cube, gkey, g.notes


def _cst5_cube(src: Path, s: cl.Shot, cube_dir: Path) -> tuple[Path, str, list[str]]:
    """grade5 (the owner's 5-node layout) solved on the shot's used range; one cube for the chain plus one per node
    and the wheel values next to it, cached by source range + targets + code version."""
    t = {k: v for k, v in s.grade.items() if k not in ("clarity", "sky_grad")}
    raw = (f"{src.resolve()}|{src.stat().st_mtime_ns}|{s.in_s:.3f}|{s.dur:.3f}|{sorted(t.items())}|"
           f"{sorted(grade5.TARGETS.items())}|cst5.6")
    gkey = "cst5_" + hashlib.sha1(raw.encode()).hexdigest()[:16]
    cube, meta = cube_dir / f"{gkey}.cube", cube_dir / f"{gkey}.json"
    if cube.exists() and meta.exists():
        return cube, gkey, json.loads(meta.read_text())["notes"]
    frames = grading.sample_frames(src, s.in_s, s.span, n=5, width=256)
    g = grade5.solve(grading.pool(frames, 30000), t)
    cs.write_cube(cube, lambda v: g.apply(v), n=65, title=gkey)
    grade5.write_node_cubes(g, cube_dir / gkey, "node")
    meta.write_text(json.dumps(g.to_dict(), indent=1))
    return cube, gkey, g.notes


def _spatial(grade: dict, w: int, h: int) -> list[str]:
    """Spatial tools from the cut list (applied on the graded picture, before grain/vignette/bars)."""
    f = []
    if grade.get("clarity"):     # local contrast: luma + k * (luma - gaussian(luma, ~2% of width)), mid-tones only
        k, sig = float(grade["clarity"]), max(4.0, w * 0.02)
        wt = "clip(1-abs(A/255-0.45)/0.3,0,1)"
        f.append(f"format=yuv444p,split[cl_a][cl_b];[cl_b]gblur=sigma={sig:.1f}:planes=1[cl_c];"
                 f"[cl_a][cl_c]blend=c0_expr='clip(A+{k:.3f}*{wt}*(A-B),0,255)':c1_expr='A':c2_expr='A'")
    if grade.get("sky_grad"):    # graduated ND: top 35% of the frame, smooth falloff, N stops at the top edge
        k = 1 - 2 ** -float(grade["sky_grad"])
        e = f"(1-{k:.4f}*pow(max(0,1-Y/(0.35*H)),1.5))"      # commas are safe inside the quotes
        f.append(f"format=gbrp,geq=r='r(X,Y)*{e}':g='g(X,Y)*{e}':b='b(X,Y)*{e}'")
    return f


def build(c: cl.CutList, cfg: dict, out: Path, look: str = "reel", vertical: bool = True,
          profile_of=None, cache_dir: Path | None = None, letterbox: bool = False) -> PreviewResult:
    cache = cache_dir or out.parent / ".preview_cache"
    seg_dir, cube_dir, grade_dir = cache / "segments", cache / "cubes", cache / "grades"
    for d in (seg_dir, cube_dir, grade_dir):
        d.mkdir(parents=True, exist_ok=True)
    order = cfg["color"].get("nodes", {}).get("order", "scene")
    fps = float(c.fps)
    w, h, geom = _frame_geometry(vertical, letterbox)
    post = []
    if look in ("cinema", "fivenode", "cst5"):   # soft vignette + fine animated grain on the picture (before the bars)
        post += ["vignette=angle=PI/12", "noise=c0s=5:c0f=t+u"]
    if letterbox:
        post.append(f"pad={w}:{h}:0:(oh-ih)/2:black")
    bitrate = "12M" if w * h > 1e6 else "4M"
    notes, segs, rendered, reused = [], [], 0, 0
    prof_cache: dict[str, str] = {}

    for n, s in enumerate(c.shots, 1):
        src = Path(s.src)
        if src.as_posix() not in prof_cache:
            prof_cache[src.as_posix()] = profile_of(src) if profile_of else camera.identify(src, look=False).profile
        lut = grading.lut_for(prof_cache[src.as_posix()], cfg, None)
        cube, gkey = None, "ungraded"
        shot_post = []
        if look == "cst5" and prof_cache[src.as_posix()] == "GP-Log":
            cube, gkey, gnotes = _cst5_cube(src, s, cube_dir)
            notes += [f"shot {n}: {x}" for x in gnotes]
            shot_post = _spatial(s.grade, w, h if not letterbox else 608)
        elif look == "fivenode" and prof_cache[src.as_posix()] in ("GP-Log", "GP-Log2"):
            cube, gkey, gnotes = _fivenode_cube(src, s, prof_cache[src.as_posix()], cfg, cube_dir)
            notes += [f"shot {n}: {x}" for x in gnotes]
            shot_post = _spatial(s.grade, w, h if not letterbox else 608)
        elif look == "cinema" and prof_cache[src.as_posix()] == "GP-Log":
            cube, gkey, ev = _cinema_cube(src, s, cube_dir)
            notes.append(f"shot {n}: exposure {ev:+.2f} stops")
        elif look == "cinema":
            notes.append(f"shot {n}: cinema look is GP-Log only (profile {prof_cache[src.as_posix()]!r}), shown ungraded")
        elif lut:
            bal, cont, _, _ = grading.shot_grade(src, s.in_s, s.dur, lut, look, order, grade_dir)
            gkey = grading.grade_key(src, s.in_s, s.dur, lut, look, order)
            cube = cube_dir / f"{gkey}.cube"
            if not cube.exists():
                grading.bake_cube(lut, bal, cont, order, cube)
        else:
            notes.append(f"shot {n}: no LUT for profile {prof_cache[src.as_posix()]!r}, shown ungraded")
        key = hashlib.sha1(f"{src.resolve()}|{s.in_s:.4f}|{s.dur:.4f}|{s.speed:.3f}|{gkey}|{w}x{h}|{fps}|{post}|{shot_post}".encode()).hexdigest()[:16]
        seg = seg_dir / f"{key}.mp4"
        if seg.exists() and seg.stat().st_size > 0:
            reused += 1
        else:
            _segment(str(src), s.in_s, s.dur, cube, geom, fps, seg, shot_post + post, bitrate, s.speed)
            rendered += 1
        segs.append(seg)

    lst = cache / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segs))
    out.parent.mkdir(parents=True, exist_ok=True)
    total = c.duration()
    has_music = bool(c.music and Path(c.music).exists())
    if c.music and not has_music:
        notes.append(f"music not found: {c.music}")
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst)]
    vmap, fc = "0:v", []
    if c.titles:   # text is burned in with one extra encode pass; without titles the segments are just copied
        from . import titles as tt
        pic_h = 608 if letterbox else h
        pngs = [tt.render_png(t, w, pic_h, cache / f"title_{i}.png") for i, t in enumerate(c.titles)]
        t_in, t_fc, t_last = tt.overlay_args(c.titles, pngs, (h - pic_h) // 2 if letterbox else 0)
        cmd += t_in
        fc, vmap = ["-filter_complex", t_fc], f"[{t_last}]"
    music_idx = 1 + len(c.titles)
    if has_music:
        cmd += ["-ss", f"{c.music_in_s:.3f}", "-t", f"{total:.3f}", "-i", c.music]
    cmd += fc + ["-map", vmap]
    if has_music:
        cmd += ["-map", f"{music_idx}:a", "-c:a", "aac", "-b:a", "192k",
                "-af", f"afade=t=out:st={max(0.0, total - 0.4):.3f}:d=0.4"]
    if c.titles:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate,
                "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    (out.with_suffix(".json")).write_text(json.dumps({"shots": len(c.shots), "duration_s": round(total, 3),
                                                      "rendered": rendered, "reused": reused, "notes": notes}, indent=2))
    return PreviewResult(out, rendered, reused, notes)
