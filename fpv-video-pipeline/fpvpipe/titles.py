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
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

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


def occlusion_mask(frame: np.ndarray, text: np.ndarray, size: tuple[int, int], spires_only: bool = False) -> np.ndarray:
    """Where near rock stands in front of the word. frame: the shot's graded RGB (uint8, any size, same aspect);
    text: boolean mask of the word at full size. Near sunlit rock is saturated and not too bright, the hazy distance
    and sky are not, so a saturation key finds the rock; per column, the first rock row from the top is the skyline.
    In the columns where that skyline rises above the bottom of the word, the rock below it is in front of the
    letters: those pixels (feathered) are returned as 0..1 at full size. spires_only keeps just the columns that stand
    well above their neighbours, so a spire cuts through a letter while a broad wall leaves it readable."""
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
    win = max(2, int(0.1 * fw))
    for cx in range(int(cols.min() * sx), min(fw - 1, int(cols.max() * sx)) + 1):
        top = first[cx]
        if spires_only and np.median(first[max(0, cx - win):cx + win + 1]) - top < 0.05 * fh:
            continue
        if top < bottom * sy:                                               # the rock rises into the word here
            out[int(top):, cx] = rk[int(top):, cx]
    m = Image.fromarray((out * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5)).resize((w, h), Image.BILINEAR)
    return np.asarray(m, np.float32) / 255 * text


# ---------------------------------------------------------------- fly-through: the word as an object in the scene
# "fly": true renders the title in real 3D instead of warping a flat image: each letter is a quad in camera space
# with an extruded back, projected through a wide lens (the Python equivalent of Fusion's Text3D + Camera3D + Merge3D).
# It rises from behind the towers letter by letter, drifts with the terrain and swings slowly, then the camera pushes
# through it: the letters accelerate past the lens with motion blur and leave the frame on the cut.
# "seated": true is the calm version of the same renderer: the word lies back on the ground plane, follows the terrain
# exactly, fades in and out in place and casts a soft shadow away from the sun. No push, no swing, nothing moves by itself.

def fly_scale(tt: float, dur: float, t_out: float = 1.3, s_end: float = 6.0) -> float:
    """The word's screen scale over time (1 = its hold size): a slow creep, then an accelerating push past the lens."""
    t_fly = dur - t_out
    if tt < t_fly:
        return 0.92 + 0.13 * max(0.0, tt) / t_fly
    u = min(1.0, (tt - t_fly) / t_out)
    return 1.05 * (s_end / 1.05) ** (u ** 2.2)


def emerge(tt: float, i: int, stagger: float = 0.12, length: float = 0.9) -> float:
    """Letter i's arrival, 0..1 with an ease-out: letters rise one after another."""
    p = min(1.0, max(0.0, (tt - i * stagger) / length))
    return 1 - (1 - p) ** 3


def render_flythrough(t: dict, w: int, h: int, path: np.ndarray, out_dir: Path, fps: float,
                      frames: np.ndarray | None = None) -> tuple[str, int]:
    """PNG sequence of the fly-through (see above). path = titles.track; frames = the shot's graded picture, so the
    towers hide the letters while the word is still behind them. Returns (ffmpeg pattern, frame count)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dur, n = float(t["dur"]), len(path)
    seated = bool(t.get("seated"))
    t_out = float(t.get("fly_out", 1.3))
    t_fly = dur if seated else dur - t_out
    k_fly = min(n - 1, int(t_fly * fps))
    follow = float(t.get("follow", 0.8)) if seated else 0.6   # how much of the ground's expansion the word takes
    grow = (1.0 if seated else 1.05) * float(path[:k_fly + 1, 2].max()) ** follow
    text = t["text"].upper()
    fpath = FONTS.get(t.get("font", "stencil"), t.get("font", FONTS["stencil"]))
    size = max(8, int(h * t.get("size", 0.17)))
    track_em = t.get("tracking", 0.9)
    font = ImageFont.truetype(fpath, size)
    word = sum(font.getlength(ch) for ch in text) + size * track_em * (len(text) - 1)
    if word * grow > 0.86 * w:                             # the whole word stays in frame through the hold
        size = int(size * 0.86 * w / (word * grow))
        font = ImageFont.truetype(fpath, size)
    ss = 1.6                                               # letters drawn larger than needed: the push magnifies them
    stroke = int(round(float(t.get("weight", 0.012)) * size * ss))   # thin display faces need body to read as objects
    pad = 8 + stroke
    big = ImageFont.truetype(fpath, int(size * ss))
    widths = [font.getlength(ch) for ch in text]
    gap = size * track_em
    pen = -(sum(widths) + gap * (len(text) - 1)) / 2
    asc, desc = font.getmetrics()
    letters = []
    for i, (ch, cw) in enumerate(zip(text, widths)):
        a2, d2 = big.getmetrics()
        im = Image.new("L", (int(big.getlength(ch)) + 2 * pad, a2 + d2 + 2 * pad), 0)
        ImageDraw.Draw(im).text((pad, pad), ch, font=big, fill=255, stroke_width=stroke, stroke_fill=255)
        letters.append({"im": im, "small": im.reduce(2), "x": pen - pad / ss, "y": -(asc + desc) / 2 - pad / ss,
                        "w": im.width / ss, "h": im.height / ss, "z": (-0.05, 0.04, -0.03, 0.05)[i % 4] * size})
        pen += cw + gap
    f = 0.6 * w                                            # a wide lens, like the footage
    pitch = np.radians(float(t.get("pitch", 55.0 if seated else 10.0)))   # top edge leans away
    extrude = float(t.get("extrude", 0.05 if seated else 0.2)) * size
    ground_haze = float(t.get("haze", 0.12)) if seated else 0.0           # the air between the lens and the word
    col = np.array(Image.new("RGB", (1, 1), t.get("color", "#fbf3e3")).getpixel((0, 0)), np.float32) * 0.94
    side_near, side_far = np.array([176, 138, 98], np.float32), np.array([90, 60, 40], np.float32)
    haze_col = np.array([205, 196, 190], np.float32)
    ax, ay = t.get("anchor", (0.5, 0.3))
    foe = np.array([0.5 * w, 0.42 * h])                    # where the camera is heading during the push
    strength = float(t.get("occlude_strength", 0.6)) if t.get("occlude") and frames is not None else 0.0
    xs = np.arange(w, dtype=np.float32)

    def scene(tt: float, layers: int, canvas: np.ndarray) -> float:
        kf = min(n - 1.0, max(0.0, tt * fps))
        k0 = int(kf)
        k1 = min(n - 1, k0 + 1)
        dx, dy, sc = path[k0] + (path[k1] - path[k0]) * (kf - k0)
        sf = 1.0 if seated else fly_scale(tt, dur, t_out)
        s_all = sf * sc ** follow
        centre = np.array([ax * w + dx * w, ay * h + dy * h])
        if sf > 1.05:                                      # the push: everything streams out from the heading
            centre = foe + (centre - foe) * (sf / 1.05)
        yaw = np.radians(float(t.get("yaw", 0.0)) if seated
                         else -9.0 + 16.0 * min(1.0, tt / max(t_fly, 1e-3)))  # a slow swing: the word turns as we pass
        cy_, sy_, cp, sp = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch)
        for i, L in sorted(enumerate(letters), key=lambda a: -a[1]["z"]):
            e = emerge(tt, 0, 0.0, float(t.get("fade_in", 1.2))) if seated else emerge(tt, i)
            if e <= 0:
                continue
            scale = s_all if seated else s_all * (0.4 + 0.6 * e)
            z0 = f / scale
            lift = 0.0 if seated else 0.07 * h * (1 - e)
            haze = ground_haze if seated else min(1.0, (1 - e) * 1.2)
            src = L["small"] if scale < 0.8 else L["im"]
            sw, sh_ = src.size
            for j in range(layers - 1, -1, -1):
                ez = extrude * j / max(1, layers - 1)
                quad = []
                for qx, qy in ((0, 0), (L["w"], 0), (L["w"], L["h"]), (0, L["h"])):
                    X, Y, Z = L["x"] + qx, L["y"] + qy, L["z"] + ez
                    Y2, Z2 = Y * cp + Z * sp, -Y * sp + Z * cp      # pitch
                    X3, Z3 = X * cy_ + Z2 * sy_, -X * sy_ + Z2 * cy_  # yaw
                    zc = z0 + Z3
                    if zc < 0.12 * f:                       # behind the near plane: this letter has passed the lens
                        quad = None
                        break
                    quad.append((centre[0] + f * X3 / zc, centre[1] + lift + f * Y2 / zc))
                if quad is None:
                    continue
                qa = np.array(quad)
                x0, y0 = max(0, int(np.floor(qa[:, 0].min()))), max(0, int(np.floor(qa[:, 1].min())))
                x1, y1 = min(w, int(np.ceil(qa[:, 0].max())) + 1), min(h, int(np.ceil(qa[:, 1].max())) + 1)
                if x1 <= x0 or y1 <= y0:
                    continue
                coef = _perspective([(0, 0), (sw, 0), (sw, sh_), (0, sh_)], [(u - x0, v - y0) for u, v in quad])
                m = np.asarray(src.transform((x1 - x0, y1 - y0), Image.PERSPECTIVE, coef, Image.BICUBIC), np.float32) / 255
                c = col if j == 0 else side_near + (side_far - side_near) * (j / max(1, layers - 1))
                c = c + (haze_col - c) * haze
                a = (m * e)[..., None]
                canvas[y0:y1, x0:x1, :3] = c * a + canvas[y0:y1, x0:x1, :3] * (1 - a)
                canvas[y0:y1, x0:x1, 3:] = a + canvas[y0:y1, x0:x1, 3:] * (1 - a)
        return sf

    word_half = (sum(widths) + gap * (len(text) - 1)) / 2
    for k in range(n):
        tt = k / fps
        shutter = 0.5 / fps
        ds = 0.0 if seated else abs(fly_scale(tt + shutter, dur, t_out) - fly_scale(tt, dur, t_out)) * word_half
        subs = int(min(10, ds / 5 + 1))                    # motion blur only where things actually move fast
        layers = 10 if subs == 1 else 5
        acc = np.zeros((h, w, 4), np.float32)
        sf = 1.0
        for q in range(subs):
            canvas = np.zeros((h, w, 4), np.float32)
            sf = scene(tt + shutter * q / subs, layers, canvas)
            acc += canvas
        acc /= subs
        rgb, a = acc[..., :3], acc[..., 3]
        if not seated and 1.0 < tt < t_fly:                # a band of low sun passes across the word during the hold
            xc = -0.2 * w + 1.4 * w * (tt - 1.0) / max(t_fly - 1.0, 1e-3)
            rgb = rgb * (1 + 0.2 * np.exp(-(((xs - xc) / (0.14 * w)) ** 2)))[None, :, None]
            rgb = np.minimum(rgb, a[..., None] * 255)
        k_occ = strength * min(1.0, max(0.0, (1.3 - sf) / 0.25))      # nearer than the towers: nothing hides it
        if k_occ > 0 and a.max() > 0:
            hide = 1 - k_occ * occlusion_mask(frames[min(k, len(frames) - 1)], a > 0.03, (w, h), spires_only=True)
            rgb, a = rgb * hide[..., None], a * hide
        fade = min(1.0, (dur - tt) / (float(t.get("fade_out", 1.0)) if seated else 0.12 * t_out))  # gone on the cut
        rgb, a = rgb * fade, a * fade
        sh_k = float(t.get("shadow", 0.5)) * min(1.0, max(0.0, (1.8 - sf) / 0.5))
        if sh_k > 0 and a.max() > 0:
            mask = Image.fromarray((a * 255).astype(np.uint8))
            if seated:                                     # cast on the ground, away from the low sun on the right
                mask = ImageChops.offset(mask, -int(0.014 * w), int(0.012 * h))
            blur = np.asarray(mask.filter(ImageFilter.GaussianBlur(h * 0.01)), np.float32) / 255 * sh_k
            a = a + blur * (1 - a)
        rgb_s = rgb / np.maximum(a[..., None], 1e-4)
        out = np.dstack([np.clip(rgb_s, 0, 255), np.clip(a, 0, 1) * 255]).astype(np.uint8)
        Image.fromarray(out, "RGBA").save(out_dir / f"f_{k:04d}.png", compress_level=1)
    return str(out_dir / "f_%04d.png"), n


# ---------------------------------------------------------------- a flat word with one summit in front of it
def word_mask(t: dict, w: int, h: int) -> np.ndarray:
    """The word as a 0..1 mask, supersampled. "width" = fraction of the picture width, "baseline" = fraction of its
    height; an O in "slash": [letter indices] gets a long thin stroke through it, the way a poster sets the Nordic O."""
    S, text = 3, t["text"].upper()
    path = FONTS.get(t.get("font"), t.get("font"))
    size = 200
    for _ in range(6):
        size = max(8, int(size * w * t.get("width", 0.7) * S / sum(ImageFont.truetype(path, size).getlength(c) for c in text)))
    font = ImageFont.truetype(path, size)
    im = Image.new("L", (w * S, h * S), 0)
    d = ImageDraw.Draw(im)
    x, base = (w * S - sum(font.getlength(c) for c in text)) / 2, h * S * t.get("baseline", 0.6)
    cap = font.getbbox("N")[3] - font.getbbox("N")[1]
    for i, c in enumerate(text):
        adv = font.getlength(c)
        d.text((x, base), c, font=font, fill=255, anchor="ls")
        if i in t.get("slash", []):
            cx, cy = x + adv / 2, base - cap / 2
            d.line([(cx - adv * .42, cy + cap * .72), (cx + adv * .42, cy - cap * .72)], fill=255, width=max(2, int(size * .008)))
        x += adv
    return np.asarray(im.resize((w, h), Image.LANCZOS), float) / 255


def summit_mask(frame: np.ndarray, band=(0.45, 0.70), xr=(0.36, 0.60)) -> np.ndarray:
    """Warm or dark rock against a cool sky, only inside the box around the summit, feathered. 0..1 at frame size."""
    h, w = frame.shape[:2]
    a = frame.astype(float) / 255
    y = a @ np.array([KR_, KG_, KB_])
    rock = (a[..., 0] > a[..., 2] + 0.02) | (y < 0.25)
    y0, y1, x0, x1 = int(h * band[0]), int(h * band[1]), int(w * xr[0]), int(w * xr[1])
    # solid land: in each column everything below the first run of rock, so shaded faces never let letters through
    hits = np.lib.stride_tricks.sliding_window_view(rock[y0:y1, x0:x1], 8, axis=0).all(axis=-1)
    box = np.zeros((h, w), bool)
    for cx in np.flatnonzero(hits.any(axis=0)):
        box[y0 + hits[:, cx].argmax():, x0 + cx] = True
    m = Image.fromarray((box * 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.GaussianBlur(1.2))
    return np.asarray(m, float) / 255


KR_, KG_, KB_ = 0.2126, 0.7152, 0.0722


def render_behind(t: dict, frames: np.ndarray, out_dir: Path, fps: float) -> tuple[str, int]:
    """The flat word as a PNG sequence with the summit cut out frame by frame and the fades baked in. frames = the
    graded shot under the title, at picture size. Same sequence for the preview and Resolve's V2 track."""
    out_dir.mkdir(parents=True, exist_ok=True)
    n, h, w = len(frames), *frames.shape[1:3]
    word = word_mask(t, w, h)
    rgb = np.array(t.get("color_rgb", (247, 245, 237)), np.uint8)
    fade = t.get("fade", 0.6)
    for k in range(n):
        a = min(1.0, k / fps / fade, (n - 1 - k) / fps / fade) * t.get("opacity", 0.93)
        alpha = word * (1 - summit_mask(frames[k], t.get("band", (0.45, 0.70)), t.get("xr", (0.36, 0.60)))) * max(0.0, a)
        im = np.dstack([np.broadcast_to(rgb, (h, w, 3)), (alpha * 255).astype(np.uint8)])
        Image.fromarray(im, "RGBA").save(out_dir / f"f_{k:04d}.png", compress_level=1)
    return str(out_dir / "f_%04d.png"), n
