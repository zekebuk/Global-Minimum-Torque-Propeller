"""Location titles ("MOAB") as a textured overlay: tracked capitals in a sand colour, a worn texture in the letters,
a soft shadow, faded in and out over the picture. Rendered once as a PNG with alpha, then overlaid by ffmpeg.

cutlist.json:
  "titles": [{"text": "MOAB", "start": 0.4, "dur": 2.8, "font": "stencil", "size": 0.13, "tracking": 0.9,
              "color": "#e3c69b", "opacity": 0.95, "y": 0.5}]
size and y are fractions of the picture height (the 16:9 picture when letterboxed); tracking is extra space between
letters as a fraction of the font size.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = {
    "stencil": "C:/Windows/Fonts/STENCIL.TTF",      # weathered, broken letterforms
    "felix": "C:/Windows/Fonts/FELIXTI.TTF",        # classic titling capitals
    "constantia": "C:/Windows/Fonts/constan.ttf",
    "dune": "E:/ASSETS/Fonts/Dune_Rise.ttf",          # Dune Rise (Fontswan, SIL OFL): thin geometric titling capitals
}


def render_png(t: dict, w: int, h: int, out: Path, seed: int = 7) -> Path:
    """Title text centred on a w x h transparent canvas (the picture area), saved as RGBA PNG."""
    if t.get("font") == "geo":
        return render_png_geo(t, w, h, out, seed)
    font = ImageFont.truetype(FONTS.get(t.get("font", "stencil"), t.get("font", FONTS["stencil"])),
                              max(8, int(h * t.get("size", 0.13))))
    text = t["text"].upper()
    track = font.size * t.get("tracking", 0.9)
    widths = [font.getlength(ch) for ch in text]
    total = sum(widths) + track * (len(text) - 1)
    asc, desc = font.getmetrics()
    x = (w - total) / 2
    y = h * t.get("y", 0.5) - (asc + desc) / 2

    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    for ch, cw in zip(text, widths):
        d.text((x, y), ch, font=font, fill=255)
        x += cw + track

    # worn texture: speckled noise eats into the letters a little (like dry-brushed paint)
    rng = np.random.default_rng(seed)
    grain = rng.random((h, w))
    grain = np.asarray(Image.fromarray((grain * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2)),
                       np.float32) / 255
    wear_amt = t.get("wear", 0.3)                          # 0 = clean; thin faces want little
    wear = np.clip((grain - 0.25) / 0.25, 0, 1) * wear_amt + (1 - wear_amt)
    a = np.asarray(mask, np.float32) / 255 * wear * t.get("opacity", 0.95)

    col = np.array(Image.new("RGB", (1, 1), t.get("color", "#e3c69b")).getpixel((0, 0)), np.float32)
    shade = 0.9 + 0.1 * grain[..., None]                    # slight tonal variation inside the letters
    rgb = np.clip(col[None, None, :] * shade, 0, 255)
    # soft shadow under the letters so they read on bright rock ("shadow" = its strength)
    sh = np.asarray(mask.filter(ImageFilter.GaussianBlur(h * 0.012)), np.float32) / 255 * float(t.get("shadow", 0.35))
    alpha = np.clip(a + sh * (1 - a), 0, 1)
    rgb = (rgb * a[..., None] + 0 * sh[..., None] * (1 - a[..., None])) / np.maximum(alpha[..., None], 1e-4)
    img = np.dstack([np.clip(rgb, 0, 255), alpha * 255]).astype(np.uint8)
    Image.fromarray(img, "RGBA").save(out)
    return out


def overlay_args(titles: list[dict], pngs: list[Path], pic_y: int, fade_out: tuple[float, float] | None = None,
                 base: str = "0:v", first_input: int = 1) -> tuple[list[str], str, str]:
    """ffmpeg inputs + filter_complex chain overlaying each title PNG (faded) at the picture's top edge.
    base = the label the picture arrives on; first_input = the index the first PNG input will get."""
    inputs, chain, last = [], [], base
    for i, (t, p) in enumerate(zip(titles, pngs), 1):
        idx = first_input + i - 1
        if isinstance(p, tuple):                              # (pattern, frames, fps): a tracked PNG sequence, fades baked in
            pattern, n, fps = p
            inputs += ["-framerate", f"{fps}", "-start_number", "0", "-i", pattern]
            chain.append(f"[{idx}:v]format=rgba,setpts=PTS-STARTPTS+{t['start']:.3f}/TB[t{i}]")
            chain.append(f"[{last}][t{i}]overlay=0:{pic_y}:eof_action=pass[o{i}]")
            last = f"o{i}"
            continue
        inputs += ["-loop", "1", "-t", f"{t['start'] + t['dur'] + 0.1:.3f}", "-i", str(p)]
        fade = min(0.7, t["dur"] / 3)
        chain.append(f"[{idx}:v]format=rgba,fade=t=in:st={t['start']:.3f}:d={fade:.3f}:alpha=1,"
                     f"fade=t=out:st={t['start'] + t['dur'] - fade:.3f}:d={fade:.3f}:alpha=1[t{i}]")
        chain.append(f"[{last}][t{i}]overlay=0:{pic_y}:eof_action=pass[o{i}]")
        last = f"o{i}"
    if fade_out:                                          # (start, duration): fade the picture to black at the end
        chain.append(f"[{last}]fade=t=out:st={fade_out[0]:.3f}:d={fade_out[1]:.3f}[fo]")
        last = "fo"
    return inputs, ";".join(chain), last


# ---------------------------------------------------------------- "geo": thin geometric capitals drawn as strokes
# A wordmark in the spirit of the Dune title face: hairline strokes, round arches, no crossbar on the A. Letters are
# built from lines and arcs in a 1x1 box (y down); anything not listed falls back to the TrueType path.

def _geo_glyphs():
    return {
        "M": [("line", [(0.0, 1.0), (0.0, 0.5)]), ("arc", [(0.0, 0.0), (0.5, 1.0)], 180, 360),
              ("line", [(0.5, 0.5), (0.5, 1.0)]), ("arc", [(0.5, 0.0), (1.0, 1.0)], 180, 360),
              ("line", [(1.0, 0.5), (1.0, 1.0)])],
        "O": [("arc", [(0.0, 0.0), (1.0, 1.0)], 0, 360)],
        "A": [("line", [(0.0, 1.0), (0.5, 0.0), (1.0, 1.0)])],
        "B": [("line", [(0.0, 0.0), (0.0, 1.0)]), ("line", [(0.0, 0.0), (0.5, 0.0)]),
              ("arc", [(0.0, 0.0), (1.0, 0.5)], 270, 450), ("line", [(0.0, 0.5), (0.5, 0.5)]),
              ("arc", [(0.0, 0.5), (1.0, 1.0)], 270, 450), ("line", [(0.0, 1.0), (0.5, 1.0)])],
        "U": [("line", [(0.0, 0.0), (0.0, 0.5)]), ("arc", [(0.0, 0.0), (1.0, 1.0)], 0, 180), ("line", [(1.0, 0.0), (1.0, 0.5)])],
        "N": [("line", [(0.0, 1.0), (0.0, 0.5)]), ("arc", [(0.0, 0.0), (1.0, 1.0)], 180, 360), ("line", [(1.0, 0.5), (1.0, 1.0)])],
        "E": [("line", [(1.0, 0.0), (0.5, 0.0)]), ("arc", [(0.0, 0.0), (1.0, 1.0)], 90, 270), ("line", [(0.5, 1.0), (1.0, 1.0)]),
              ("line", [(0.15, 0.5), (1.0, 0.5)])],
        "S": [("arc", [(0.0, 0.0), (1.0, 0.5)], 90, 360), ("arc", [(0.0, 0.5), (1.0, 1.0)], 270, 540)],
        "T": [("line", [(0.0, 0.0), (1.0, 0.0)]), ("line", [(0.5, 0.0), (0.5, 1.0)])],
        "I": [("line", [(0.5, 0.0), (0.5, 1.0)])],
        "L": [("line", [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)])],
        "H": [("line", [(0.0, 0.0), (0.0, 1.0)]), ("line", [(1.0, 0.0), (1.0, 1.0)]), ("line", [(0.0, 0.5), (1.0, 0.5)])],
        "C": [("arc", [(0.0, 0.0), (1.0, 1.0)], 40, 320)],
        "R": [("line", [(0.0, 0.0), (0.0, 1.0)]), ("line", [(0.0, 0.0), (0.55, 0.0)]), ("arc", [(0.1, 0.0), (1.0, 0.55)], 270, 450),
              ("line", [(0.0, 0.55), (0.55, 0.55)]), ("line", [(0.55, 0.55), (1.0, 1.0)])],
        "D": [("line", [(0.0, 0.0), (0.0, 1.0)]), ("line", [(0.0, 0.0), (0.5, 0.0)]), ("arc", [(0.0, 0.0), (1.0, 1.0)], 270, 450),
              ("line", [(0.0, 1.0), (0.5, 1.0)])],
        "V": [("line", [(0.0, 0.0), (0.5, 1.0), (1.0, 0.0)])],
        "Y": [("line", [(0.0, 0.0), (0.5, 0.5), (1.0, 0.0)]), ("line", [(0.5, 0.5), (0.5, 1.0)])],
        "K": [("line", [(0.0, 0.0), (0.0, 1.0)]), ("line", [(1.0, 0.0), (0.0, 0.6)]), ("line", [(0.3, 0.42), (1.0, 1.0)])],
        "G": [("arc", [(0.0, 0.0), (1.0, 1.0)], 40, 360), ("line", [(1.0, 0.5), (1.0, 1.0)]), ("line", [(0.6, 0.5), (1.0, 0.5)])],
        "P": [("line", [(0.0, 0.0), (0.0, 1.0)]), ("line", [(0.0, 0.0), (0.55, 0.0)]), ("arc", [(0.1, 0.0), (1.0, 0.55)], 270, 450),
              ("line", [(0.0, 0.55), (0.55, 0.55)])],
        "W": [("line", [(0.0, 0.0), (0.0, 0.5)]), ("arc", [(0.0, 0.0), (0.5, 1.0)], 0, 180), ("line", [(0.5, 0.0), (0.5, 0.5)]),
              ("arc", [(0.5, 0.0), (1.0, 1.0)], 0, 180), ("line", [(1.0, 0.0), (1.0, 0.5)])],
        "F": [("line", [(1.0, 0.0), (0.5, 0.0)]), ("arc", [(0.0, 0.0), (1.0, 1.0)], 90, 270), ("line", [(0.15, 0.5), (1.0, 0.5)])],
        "X": [("line", [(0.0, 0.0), (1.0, 1.0)]), ("line", [(1.0, 0.0), (0.0, 1.0)])],
        "Z": [("line", [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)])],
        "J": [("line", [(0.7, 0.0), (0.7, 0.5)]), ("arc", [(0.0, 0.0), (1.0, 1.0)], 0, 180)],
        "Q": [("arc", [(0.0, 0.0), (1.0, 1.0)], 0, 360), ("line", [(0.6, 0.6), (1.05, 1.05)])],
    }


def render_geo_mask(text: str, h_px: int, stroke_frac: float = 0.032, tracking: float = 0.55, aspect: float = 0.78,
                    ss: int = 4):
    """Mask (L) of the wordmark: letters h_px tall, aspect = letter width / height, drawn at ss x and downsampled."""
    text = text.upper()
    g = _geo_glyphs()
    H = h_px * ss
    W = int(H * aspect)
    sw = max(2, int(H * stroke_frac))
    gap = int(H * tracking)
    total = len(text) * W + (len(text) - 1) * gap
    img = Image.new("L", (total + 2 * sw, H + 2 * sw), 0)
    d = ImageDraw.Draw(img)
    x0 = sw
    for ch in text:
        for kind, *rest in g.get(ch, []):
            if kind == "line":
                pts = [(x0 + px * W, sw + py * H) for px, py in rest[0]]
                d.line(pts, fill=255, width=sw, joint="curve")
                for p in pts:                                   # round caps
                    d.ellipse([p[0] - sw / 2, p[1] - sw / 2, p[0] + sw / 2, p[1] + sw / 2], fill=255)
            else:
                (ax, ay), (bx, by) = rest[0]
                d.arc([x0 + ax * W, sw + ay * H, x0 + bx * W, sw + by * H], rest[1], rest[2], fill=255, width=sw)
        x0 += W + gap
    return img.resize((img.width // ss, img.height // ss), Image.LANCZOS)


def render_png_geo(t: dict, w: int, h: int, out: Path, seed: int = 7) -> Path:
    """Geometric wordmark centred on a w x h transparent canvas, same colour / light wear / shadow as render_png."""
    mark = render_geo_mask(t["text"], max(8, int(h * t.get("size", 0.22))), tracking=t.get("tracking", 0.55))
    mask = Image.new("L", (w, h), 0)
    mask.paste(mark, ((w - mark.width) // 2, int(h * t.get("y", 0.5) - mark.height / 2)))
    rng = np.random.default_rng(seed)
    grain = np.asarray(Image.fromarray((rng.random((h, w)) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2)),
                       np.float32) / 255
    wear = np.clip((grain - 0.25) / 0.25, 0, 1) * 0.15 + 0.85                 # lighter wear: the strokes are thin
    a = np.asarray(mask, np.float32) / 255 * wear * t.get("opacity", 0.95)
    col = np.array(Image.new("RGB", (1, 1), t.get("color", "#e3c69b")).getpixel((0, 0)), np.float32)
    rgb = np.clip(col[None, None, :] * (0.92 + 0.08 * grain[..., None]), 0, 255)
    sh = np.asarray(mask.filter(ImageFilter.GaussianBlur(h * 0.012)), np.float32) / 255 * 0.35
    alpha = np.clip(a + sh * (1 - a), 0, 1)
    rgb = (rgb * a[..., None]) / np.maximum(alpha[..., None], 1e-4)
    Image.fromarray(np.dstack([np.clip(rgb, 0, 255), alpha * 255]).astype(np.uint8), "RGBA").save(out)
    return out


# ---------------------------------------------------------------- tracked titles: text that sits in the landscape
# A title with "track": true follows the picture under it. The ground's motion is measured with sub-pixel phase
# correlation in a window around the title's anchor (translation + expansion as the camera flies in), the title is
# laid back onto the ground plane with a keystone ("tilt") and moved / scaled with that motion frame by frame, and
# the frames go out as a PNG sequence that the preview overlays and Resolve imports as an image-sequence clip.
# (The reference look - camera-tracked 3D text, as in Resolve's Fusion Camera Tracker -> Text3D - needs a solved
# camera; a 2D track of the ground is what can be done from a script, and holds up for slow forward reveals.)

def _shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Sub-pixel translation from a to b by phase correlation (parabolic peak fit)."""
    a = a - a.mean()
    b = b - b.mean()
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    fa, fb = np.fft.fft2(a * win), np.fft.fft2(b * win)
    r = fb * np.conj(fa)
    c = np.fft.ifft2(r / (np.abs(r) + 1e-6)).real
    h, w = c.shape
    dy, dx = divmod(int(np.argmax(c)), w)

    def sub(cm, c0, cp):
        d = cm - 2 * c0 + cp
        return 0.0 if d == 0 else 0.5 * (cm - cp) / d
    sx = sub(c[dy, (dx - 1) % w], c[dy, dx], c[dy, (dx + 1) % w])
    sy = sub(c[(dy - 1) % h, dx], c[dy, dx], c[(dy + 1) % h, dx])
    return (dx if dx <= w // 2 else dx - w) + sx, (dy if dy <= h // 2 else dy - h) + sy


def track(src: Path, in_s: float, dur: float, fps: float, anchor=(0.5, 0.6), window=(0.6, 0.4),
          stride: int = 3, crop: str | None = None) -> np.ndarray:
    """Per-frame (x, y, scale) of the ground under the anchor, as fractions of the picture width / height and a
    scale factor, cumulative from the first frame. One row per timeline frame (round(dur * fps) + 1)."""
    import subprocess
    W, H = 480, 420
    vf = (f"{crop}," if crop else "") + f"fps={fps},scale={W}:{H},format=gray"
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{in_s:.3f}", "-t", f"{dur + 0.2:.3f}", "-i", str(src),
                          "-vf", vf, "-f", "rawvideo", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)
    x0, x1 = int(W * (anchor[0] - window[0] / 2)), int(W * (anchor[0] + window[0] / 2))
    y0, y1 = int(H * (anchor[1] - window[1] / 2)), int(H * (anchor[1] + window[1] / 2))
    hw, hh = (x1 - x0) // 2, (y1 - y0) // 2
    keys, cx, cy, sc = [(0, 0.0, 0.0, 1.0)], 0.0, 0.0, 1.0
    for k in range(0, len(fr) - stride, stride):
        a, b = fr[k][y0:y1, x0:x1], fr[k + stride][y0:y1, x0:x1]
        dx, dy = _shift(a, b)
        div = ((_shift(a[:, hw:], b[:, hw:])[0] - _shift(a[:, :hw], b[:, :hw])[0]) / hw
               + (_shift(a[hh:], b[hh:])[1] - _shift(a[:hh], b[:hh])[1]) / hh) / 2
        cx, cy, sc = cx + dx / W, cy + dy / H, sc * (1 + float(np.clip(div, -0.05, 0.05)))
        keys.append((k + stride, cx, cy, sc))
    n = int(round(dur * fps)) + 1
    kf = np.array(keys, dtype=float)
    out = np.column_stack([np.interp(np.arange(n), kf[:, 0], kf[:, j]) for j in (1, 2, 3)])
    k5 = np.ones(5) / 5                                    # a touch of smoothing against correlation jitter
    for j in range(3):
        pad = np.pad(out[:, j], 2, mode="edge")
        out[:, j] = np.convolve(pad, k5, mode="valid")
    return out


def _perspective(src_quad, dst_quad):
    """PIL PERSPECTIVE coefficients mapping output pixels (dst) back to input pixels (src)."""
    m = []
    for (x, y), (u, v) in zip(dst_quad, src_quad):
        m.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        m.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    a = np.array(m, dtype=float)
    b = np.array([c for p in src_quad for c in p], dtype=float)
    return np.linalg.solve(a, b)


def render_tracked(t: dict, w: int, h: int, path: np.ndarray, out_dir: Path, fps: float, seed: int = 7,
                   frames: np.ndarray | None = None) -> tuple[str, int]:
    """PNG sequence of the title riding the tracked ground: keystoned onto the ground plane (tilt = how much the top
    edge narrows, "lean" = how much it lies back), scaled with the scene's expansion (softened), moved with it, faded
    in and out in the alpha. With "occlude": true and the shot's graded frames (any size, same aspect), near rock
    that rises INTO the word - a spire tip - hides the letters behind it (see occlusion_mask), so the title sits
    among the towers instead of on top of the picture. Returns (ffmpeg pattern, frame count)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    base = render_png(t, w, h, out_dir / "_base.png", seed)
    img = Image.open(base).convert("RGBA")
    bb = img.split()[3].getbbox()
    grow = max(float(path[:, 2].max()), 1.0) ** 0.6
    if bb and (bb[2] - bb[0]) * grow > 0.9 * w:           # keep the word inside the frame at its largest
        t = {**t, "size": t.get("size", 0.13) * 0.9 * w / ((bb[2] - bb[0]) * grow)}
        base = render_png(t, w, h, out_dir / "_base.png", seed)
        img = Image.open(base).convert("RGBA")
    ax, ay = t.get("anchor", (0.5, 0.6))
    tilt = float(t.get("tilt", 0.35))
    lean = float(t.get("lean", 0.72))
    occlude = bool(t.get("occlude")) and frames is not None
    strength = float(t.get("occlude_strength", 0.9))
    n = len(path)
    fade = min(0.7, t["dur"] / 3)
    # the text was drawn centred at (w/2, h*y); move that centre onto the anchor
    cx0, cy0 = w / 2, h * t.get("y", 0.5)
    half_w, half_h = w / 2, h / 2
    for k in range(n):
        dx, dy, sc = path[k]
        s = sc ** 0.6                                     # most of the expansion, not all: text is "above" the ground
        px, py = ax * w + dx * w, ay * h + dy * h
        # destination quad of the (whole) canvas: scaled about the text centre, moved to (px, py), top edge narrowed
        def corner(ux, uy):
            X = (ux - cx0) * s
            Y = (uy - cy0) * s
            keystone = 1 - tilt * (0.5 - (Y / (2 * half_h * s) + 0.5))   # narrower above the centre line
            return px + X * keystone, py + Y * lean                        # foreshortened: lying back on the ground
        dst = [corner(0, 0), corner(w, 0), corner(w, h), corner(0, h)]
        coeffs = _perspective([(0, 0), (w, 0), (w, h), (0, h)], dst)
        frame = img.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
        tt = k / fps
        a = min(1.0, tt / fade, (t["dur"] - tt) / fade) if fade > 0 else 1.0
        if a < 1.0 or occlude:
            r, g, b, al = frame.split()
            al_np = np.asarray(al, np.float32) * max(0.0, min(1.0, a))
            if occlude:
                m = occlusion_mask(frames[min(k, len(frames) - 1)], al_np > 8, (w, h))
                al_np *= 1 - strength * m
            frame = Image.merge("RGBA", (r, g, b, Image.fromarray(al_np.astype(np.uint8))))
        frame.save(out_dir / f"f_{k:04d}.png")
    return str(out_dir / "f_%04d.png"), n


def occlusion_mask(frame: np.ndarray, text: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Where near rock stands in front of the word. frame: the shot's graded RGB (uint8, any size, same aspect);
    text: boolean mask of the word at full size. Near sunlit rock is saturated and not too bright, the hazy distance
    and sky are not, so a saturation key finds the rock; per column, the first rock row from the top is the skyline.
    In the columns where that skyline rises above the bottom of the word, the rock below it is in front of the
    letters: those pixels (feathered) are returned as 0..1 at full size."""
    w, h = size
    f = frame.astype(np.float32)
    mx, mn = f.max(axis=2), f.min(axis=2)
    sat = (mx - mn) / (mx + 1.0)
    luma = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
    rock = (sat > 0.30) & (luma < 0.78 * 255)
    # clean the key a little (box blur + threshold) so single pixels of haze or grain do not count
    rk = np.asarray(Image.fromarray((rock * 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(2)), np.float32) > 127
    fh, fw = rk.shape
    sy, sx = fh / h, fw / w
    cols = np.where(text.any(axis=0))[0]
    if len(cols) == 0:
        return np.zeros((h, w), np.float32)
    rows = np.where(text.any(axis=1))[0]
    bottom = rows.max()
    out = np.zeros((fh, fw), np.float32)
    first = np.where(rk.any(axis=0), rk.argmax(axis=0), fh)               # skyline row per frame column
    for cx in range(int(cols.min() * sx), int(cols.max() * sx) + 1):
        top = first[cx]
        if top < bottom * sy:                                               # the rock rises into the word here
            out[int(top):, cx] = rk[int(top):, cx]
    m = Image.fromarray((out * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5)).resize((w, h), Image.BILINEAR)
    return np.asarray(m, np.float32) / 255 * text
