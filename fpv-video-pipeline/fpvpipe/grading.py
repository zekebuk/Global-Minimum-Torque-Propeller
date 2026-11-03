"""Shared grading helpers: frame sampling, pixel pooling, LUT choice per camera profile, cached per-shot grades
and baking a solved grade (BAL + CONT + camera LUT) into a single .cube for ffmpeg previews."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

from . import colorsci as cs, media


def sample_frames(path: Path, start: float, dur: float, n: int = 7, width: int = 384,
                  crop: str | None = None) -> list[np.ndarray]:
    """Decode n frames as 16-bit RGB (full range, like Resolve's 'auto' data levels for video files).
    crop: an ffmpeg crop expression for the delivered framing (e.g. "crop=iw:iw*9/16"), so a grade is solved on
    exactly the picture the viewer sees - the 8:7 sensor frame carries sky and ground a 16:9 cut never shows."""
    info = media.video_summary(media.ffprobe(path))
    if crop:
        import re
        m = re.search(r"crop=iw:iw\*(\d+)/(\d+)", crop)
        aspect = int(m.group(2)) / int(m.group(1)) if m else info["width"] / info["height"]
        h = int(round(width / aspect / 2) * 2)
    else:
        h = int(round(info["height"] * width / max(info["width"], 1) / 2) * 2)
    frames = []
    for k in range(n):
        t = start + dur * (k + 0.5) / n
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                              "-vf", (crop + "," if crop else "") + f"scale={width}:{h}", "-pix_fmt", "rgb48le",
                              "-f", "rawvideo", "-"],
                             capture_output=True).stdout
        a = np.frombuffer(raw, "<u2").astype(np.float64) / 65535.0
        if a.size == width * h * 3:
            frames.append(a.reshape(h, width, 3))
    return frames


def pool(frames: list[np.ndarray], k: int = 60000, seed: int = 0) -> np.ndarray:
    px = np.concatenate([f.reshape(-1, 3) for f in frames])
    if len(px) > k:
        px = px[np.random.default_rng(seed).choice(len(px), k, replace=False)]
    return px


def lut_for(profile: str, cfg: dict, override: Path | None) -> Path | None:
    if override:
        return override
    p = cfg["color"]["luts"].get(profile) or cfg["color"]["conversion_lut"]
    return p if p and p.exists() else None


def grade_key(src: Path, start: float, dur: float, lut: Path, look: str, order: str) -> str:
    st = Path(lut).stat()
    raw = f"{Path(src).resolve()}|{start:.3f}|{dur:.3f}|{Path(lut).resolve()}|{st.st_mtime_ns}|{look}|{order}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def shot_grade(src: Path, start: float, dur: float, lut_path: Path, look: str, order: str,
               cache_dir: Path) -> tuple[cs.CDL, cs.CDL, dict, bool]:
    """Solve (or load from cache) the grade for one shot's used range. Returns (bal, cont, after_stats, cached)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = grade_key(src, start, dur, lut_path, look, order)
    f = cache_dir / f"{key}.json"
    if f.exists():
        d = json.loads(f.read_text())
        mk = lambda c: cs.CDL(np.array(c["slope"]), np.array(c["offset"]), np.array(c["power"]), c["sat"])
        return mk(d["bal"]), mk(d["cont"]), d["after"], True
    frames = sample_frames(src, start, dur, n=5, width=256)
    if not frames:
        raise RuntimeError(f"could not decode {src}")
    g = cs.solve(pool(frames, 30000), cs.load_cube(lut_path), look, order=order)
    f.write_text(json.dumps({"bal": g.bal.to_dict(), "cont": g.cont.to_dict(), "after": g.after, "notes": g.notes}))
    return g.bal, g.cont, g.after, False


def bake_cube(lut_path: Path, bal: cs.CDL, cont: cs.CDL, order: str, out: Path, n: int = 33) -> Path:
    """The whole primary chain as one 3D LUT: exactly what the preview needs, applied by ffmpeg's lut3d."""
    lut = cs.load_cube(lut_path)
    cs.write_cube(out, lambda rgb: cs.render(rgb, lut, bal, cont, order), n=n, title=out.stem)
    return out
