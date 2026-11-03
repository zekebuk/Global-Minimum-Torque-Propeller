"""Preview a cut list without Resolve: graded, cropped, on-beat, with music, in seconds per changed shot.

Per shot: solve the grade on the used range (cached), bake BAL + CONT + camera LUT into one .cube, then one ffmpeg
pass: fast seek -> trim -> 16-bit RGB -> lut3d -> crop/scale to the preview frame -> constant fps -> H.264 segment.
Segments are cached by (source, in/out, grade, size, fps), so after a note like "shot 3 shorter" only shot 3 renders.
Segments are joined with the concat demuxer (no re-encode) and the music is laid in at the cut list's music offset.
The colour result matches what auto_grade writes into Resolve (same solver, same LUT, same node order); reframing
for vertical is each shot's own 9:16 window on the full sensor frame (Shot.frame: position, eased pan, zoom).

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

from . import camera, cinelook, colorsci as cs, cutlist as cl, fivenode, grade5, grading, media, ramp, transitions


@dataclass
class PreviewResult:
    video: Path
    rendered: int
    reused: int
    notes: list[str]


PIC_H = {"16:9": 608, "8:7": 944}   # picture height on the 1080x1920 canvas for each letterboxed aspect


def _frame_geometry(vertical: bool, letterbox: bool = False, aspect: str = "16:9") -> tuple[int, int, str]:
    if letterbox:  # the full picture centred on a 1080x1920 canvas; the pad is applied after grain/vignette
        ph = PIC_H[aspect]
        return 1080, 1920, f"scale=1080:{ph}:force_original_aspect_ratio=increase,crop=1080:{ph}"
    if vertical:   # 9:16, centre crop (FPV sources are 4:3 / 8:7 / 16:9)
        return 540, 960, "scale=540:960:force_original_aspect_ratio=increase,crop=540:960"
    return 960, 540, "scale=960:540:force_original_aspect_ratio=increase,crop=960:540"


def reframe(s: cl.Shot, animate: bool = True) -> str:
    """The shot's 9:16 window on the full sensor frame as an ffmpeg crop. frame = {"x": centre as a fraction of the
    source width, "x_end": centre at the end of the shot (eased pan), "zoom": 1.0 = full sensor height,
    "rot": 180 for a camera mounted upside down (x is measured on the righted picture)}.
    animate=False gives the static window at the middle of the pan: the framing the grade is solved on, and the one
    build_reel_project.py sets as Pan/Zoom in Resolve."""
    f = s.frame or {}
    z = max(1.0, float(f.get("zoom", 1.0)))
    x0 = float(f.get("x", 0.5))
    x1 = float(f.get("x_end", x0))
    if animate and abs(x1 - x0) > 1e-4:
        p = f"clip(t/{max(s.dur, 1e-3):.4f}\\,0\\,1)"
        xc = f"({x0:.4f}+{x1 - x0:.4f}*{p}*{p}*(3-2*{p}))"     # smoothstep ease in/out
    else:
        xc = f"{(x0 + x1) / 2:.4f}"
    flip = "hflip,vflip," if int(f.get("rot", 0)) % 360 == 180 else ""     # camera mounted upside down
    return f"{flip}crop=ih*9/16/{z:.4f}:ih/{z:.4f}:clip({xc}*iw-ow/2\\,0\\,iw-ow):(ih-oh)/2"


def _segment(src: str, in_s: float, dur: float, cube: Path | None, vf_geom: str, fps: float, out: Path,
             post: list[str] = (), bitrate: str = "4M", speed: float = 1.0, ramp_pts: list = (),
             span: float | None = None, src_fps: float = 0.0):
    """One shot as a graded segment. speed = constant retime; ramp_pts = a speed ramp over source time
    (fpvpipe/ramp.py), in which case `span` is the source range used and src_fps decides whether the slow parts
    need motion interpolation (a 59.94 source slowed to 0.5x on a 29.97 timeline still has a real frame per frame)."""
    span = span if span is not None else dur * speed
    vf = ["format=rgb48le"]
    rate = f"fps={fps}"
    if ramp_pts:
        vf.append(f"setpts='{ramp.setpts_expr(ramp_pts, in_s, in_s + span)}'")
        if ramp.min_speed(ramp_pts, in_s, in_s + span) * (src_fps or fps) < fps * 0.98:
            # slower than the source's frame supply: optical-flow interpolation instead of repeated frames
            rate = f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
    elif speed != 1.0:
        vf.append(f"setpts=PTS/{speed:.4f}")
    if cube is not None:
        cpath = str(cube).replace("\\", "/").replace(":", "\\:")
        vf.append(f"lut3d=file='{cpath}':interp=trilinear")
    # tag the frames themselves: newer ffmpeg takes colour metadata from the frames over -color_* output options
    vf += [vf_geom, *post, "format=yuv420p", rate,
           "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"]
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{in_s:.3f}", "-t", f"{span:.3f}", "-i", src,
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
    t = {k: v for k, v in s.grade.items() if k not in ("clarity", "sky_grad", "unify")}   # not node targets
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


def cst5_key(src: Path, s: cl.Shot, crop: str | None = None) -> str:
    """Cache key of a shot's cst5 grade. build_reel_project.py looks the cubes up by this too, so keep it the one
    place the key is made (a copy there once dropped "unify" and loaded stale grades)."""
    t = {k: v for k, v in s.grade.items() if k not in ("clarity", "sky_grad")}
    raw = (f"{src.resolve()}|{src.stat().st_mtime_ns}|{s.in_s:.3f}|{s.dur:.3f}|{sorted(t.items())}|"
           f"{sorted(grade5.TARGETS.items())}|{sorted(grade5.UNIFY.items())}|{crop}|cst5.18")   # .18: lift_stops/lift_at in the meta (node DCTLs)
    return "cst5_" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def _cst5_cube(src: Path, s: cl.Shot, cube_dir: Path, crop: str | None = None) -> tuple[Path, str, list[str]]:
    """grade5 (the owner's 5-node layout) solved on the shot's used range; one cube for the chain plus one per node
    and the wheel values next to it, cached by source range + targets + code version."""
    t = {k: v for k, v in s.grade.items() if k not in ("clarity", "sky_grad")}
    gkey = cst5_key(src, s, crop)
    cube, meta = cube_dir / f"{gkey}.cube", cube_dir / f"{gkey}.json"
    if cube.exists() and meta.exists():
        return cube, gkey, json.loads(meta.read_text())["notes"]
    frames = grading.sample_frames(src, s.in_s, s.span, n=5, width=256, crop=crop)
    px = grading.pool(frames, 30000)
    g = grade5.solve(px, t)
    u = s.grade.get("unify", True)                       # the reel-wide unifying node (False to exempt a shot)
    if u is not False:
        un = grade5.solve_unify(g.apply(px), u if isinstance(u, dict) else None)
        chain = lambda v: un.apply(g.apply(v))
        g.notes.append(f"UNIFY: lift {un.lift:+.3f} gain x{un.gain:.2f} gamma {1 / un.power:.2f} "
                       f"rock {un.rock_rot:+.0f}deg x{un.rock_scale:.2f} chroma x{un.chroma_scale:.2f}")
    else:
        un, chain = None, g.apply
    cs.write_cube(cube, chain, n=65, title=gkey)
    grade5.write_node_cubes(g, cube_dir / gkey, "node")
    if un is not None:
        cs.write_cube(cube_dir / gkey / "node_unify.cube", un.apply, n=33, title=f"{gkey} unify")
    d = g.to_dict()
    d["unify"] = un.to_dict() if un else None
    meta.write_text(json.dumps(d, indent=1))
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
          profile_of=None, cache_dir: Path | None = None, letterbox: bool = False,
          aspect: str = "16:9", hires: bool = False) -> PreviewResult:
    cache = cache_dir or out.parent / ".preview_cache"
    seg_dir, cube_dir, grade_dir = cache / "segments", cache / "cubes", cache / "grades"
    for d in (seg_dir, cube_dir, grade_dir):
        d.mkdir(parents=True, exist_ok=True)
    order = cfg["color"].get("nodes", {}).get("order", "scene")
    fps = float(c.fps)
    w, h, geom = _frame_geometry(vertical, letterbox, aspect)
    pic_h = PIC_H[aspect] if letterbox else h
    # the framing the grade is solved on = the framing delivered (the letterboxed picture, or the 9:16 centre crop)
    if letterbox or not vertical:
        pic_crop = "crop=iw:iw*9/16" if aspect == "16:9" else None     # 8:7 = the whole sensor frame
    else:
        pic_crop = None                     # vertical: each shot's own 9:16 window on the sensor (reframe)
    reframed = vertical and not letterbox
    if reframed and hires:
        w, h = 1080, 1920
        pic_h = h
    post = []
    if look in ("cinema", "fivenode", "cst5"):   # soft vignette + fine animated grain on the picture (before the bars)
        post += ["vignette=angle=PI/12", "noise=c0s=5:c0f=t+u"]
    if letterbox:
        post.append(f"pad={w}:{h}:0:(oh-ih)/2:black")
    bitrate = "12M" if w * h > 1e6 else "4M"
    notes, segs, seg_len, rendered, reused = [], [], [], 0, 0
    prof_cache: dict[str, str] = {}
    fps_cache: dict[str, float] = {}             # source frame rates, only probed for ramped shots

    for n, s in enumerate(c.shots, 1):
        src = Path(s.src)
        if src.as_posix() not in prof_cache:
            prof_cache[src.as_posix()] = profile_of(src) if profile_of else camera.identify(src, look=False).profile
        lut = grading.lut_for(prof_cache[src.as_posix()], cfg, None)
        cube, gkey = None, "ungraded"
        shot_post = []
        shot_crop = reframe(s, animate=False) if reframed else pic_crop
        shot_geom = f"{reframe(s)},scale={w}:{h}:flags=lanczos" if reframed else geom
        prof = prof_cache[src.as_posix()]
        if look == "cst5" and prof == "GP-Log2" and not s.grade.get("cst_lut"):
            notes.append(f"shot {n}: GP-Log2 needs grade.cst_lut (the GP-Log2 -> Rec.709 .cube) for cst5, shown ungraded")
        elif look == "cst5" and (prof in ("GP-Log", "GP-Log2") or s.grade.get("cst_lut")):   # any source with its own CST cube
            cube, gkey, gnotes = _cst5_cube(src, s, cube_dir, shot_crop)
            notes += [f"shot {n}: {x}" for x in gnotes]
            shot_post = _spatial(s.grade, w, pic_h)
        elif look == "cst5":                     # display-referred source (phone B-roll): shown as shot
            notes.append(f"shot {n}: profile {prof!r} is not log, shown as shot")
        elif look == "fivenode" and prof_cache[src.as_posix()] in ("GP-Log", "GP-Log2"):
            cube, gkey, gnotes = _fivenode_cube(src, s, prof_cache[src.as_posix()], cfg, cube_dir)
            notes += [f"shot {n}: {x}" for x in gnotes]
            shot_post = _spatial(s.grade, w, pic_h)
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
        # transition handles: half of each soft transition on either side of the cut, rendered into the segment
        # from source beyond in/out at the speed the shot holds there (xfade overlaps them, so the beat grid holds)
        eh = c.shots[n - 1].trans_dur / 2 if n > 1 else 0.0
        et = c.shots[n].trans_dur / 2 if n < len(c.shots) else 0.0
        v0 = float(ramp.speed_at(s.ramp, s.in_s)) if s.ramp else s.speed
        v1 = float(ramp.speed_at(s.ramp, s.out_s)) if s.ramp else s.speed
        in_x = max(0.0, s.in_s - eh * v0)
        eh = (s.in_s - in_x) / v0
        dur_x, span_x = s.dur + eh + et, s.span + (s.in_s - in_x) + et * v1
        key = hashlib.sha1(f"{src.resolve()}|{s.in_s:.4f}|{s.dur:.4f}|{s.speed:.3f}|{s.ramp}|{eh:.3f}|{et:.3f}|{gkey}|{w}x{h}|{fps}|{post}|{shot_post}|{shot_geom}".encode()).hexdigest()[:16]
        seg = seg_dir / f"{key}.mp4"
        if seg.exists() and seg.stat().st_size > 0:
            reused += 1
        else:
            if s.ramp and src.as_posix() not in fps_cache:
                fps_cache[src.as_posix()] = media.video_summary(media.ffprobe(src))["fps"]
            _segment(str(src), in_x, dur_x, cube, shot_geom, fps, seg, shot_post + post, bitrate, s.speed,
                     s.ramp, span_x, fps_cache.get(src.as_posix(), 0.0))
            rendered += 1
        segs.append(seg)
        seg_len.append(round(dur_x * fps) / fps)

    out.parent.mkdir(parents=True, exist_ok=True)
    total = c.duration()
    has_music = bool(c.music and Path(c.music).exists())
    fade = float(getattr(c, "fade_out_s", 0.0) or 0.0)         # picture + music fade at the end (seconds)
    if c.music and not has_music:
        notes.append(f"music not found: {c.music}")
    soft = any(s.trans_dur > 0 for s in c.shots[1:])
    chain: list[str] = []
    if soft:
        # every segment is an input: soft cuts overlap through xfade (their handles are in the segments), hard
        # cuts are concatenated, so every cut stays on its beat; one encode pass
        cmd = ["ffmpeg", "-v", "error", "-y"]
        for p_ in segs:
            cmd += ["-i", str(p_)]
        # one common timebase: concat emits 1/1000000 while the segments carry 1/11988, and xfade refuses mixed ones
        chain += [f"[{k}:v]settb=AVTB[s{k}]" for k in range(len(segs))]
        n_in, last, acc = len(segs), "s0", seg_len[0]
        band = ((h - pic_h) / 2, (h + pic_h) / 2) if letterbox else None        # picture rows (px), for effects
        for k in range(1, len(segs)):
            d = c.shots[k].trans_dur
            if d > 0:
                chain.append(f"[{last}][s{k}]xfade={transitions.xfade_spec(c.shots[k].trans_in, band)}:"
                             f"duration={d:.3f}:offset={acc - d:.3f}[x{k}]")
                acc += seg_len[k] - d
            else:
                chain.append(f"[{last}][s{k}]concat=n=2:v=1:a=0[x{k}]")
                acc += seg_len[k]
            last = f"x{k}"
    else:          # hard cuts only: the concat demuxer, no re-encode unless titles or the fade need one
        lst = cache / "concat.txt"
        lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segs))
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst)]
        n_in, last = 1, "0:v"
    if c.titles:
        from . import titles as tt
        pngs = [tt.render_png(t, w, pic_h, cache / f"title_{i}.png") for i, t in enumerate(c.titles)]
        t_in, t_fc, last = tt.overlay_args(c.titles, pngs, (h - pic_h) // 2 if letterbox else 0,
                                           (max(0.0, total - fade), fade) if fade else None, base=last, first_input=n_in)
        cmd += t_in
        chain.append(t_fc)
    elif fade:
        chain.append(f"[{last}]fade=t=out:st={max(0.0, total - fade):.3f}:d={fade:.3f}[fo]")
        last = "fo"
    music_idx = n_in + len(c.titles)
    if has_music:
        cmd += ["-ss", f"{c.music_in_s:.3f}", "-t", f"{total:.3f}", "-i", c.music]
    if chain:
        cmd += ["-filter_complex", ";".join(chain), "-map", f"[{last}]"]
    else:
        cmd += ["-map", last]
    if has_music:
        afade = max(fade, 0.4)
        cmd += ["-map", f"{music_idx}:a", "-c:a", "aac", "-b:a", "192k",
                "-af", f"afade=t=out:st={max(0.0, total - afade):.3f}:d={afade:.3f}"]
    if chain:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate,
                "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffmpeg failed assembling the preview:\n{r.stderr.strip()[-2000:]}")
    (out.with_suffix(".json")).write_text(json.dumps({"shots": len(c.shots), "duration_s": round(total, 3),
                                                      "rendered": rendered, "reused": reused, "notes": notes}, indent=2))
    return PreviewResult(out, rendered, reused, notes)
