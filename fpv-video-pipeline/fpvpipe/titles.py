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
    # soft shadow under the letters so they read on bright rock
    sh = np.asarray(mask.filter(ImageFilter.GaussianBlur(h * 0.012)), np.float32) / 255 * 0.35
    alpha = np.clip(a + sh * (1 - a), 0, 1)
    rgb = (rgb * a[..., None] + 0 * sh[..., None] * (1 - a[..., None])) / np.maximum(alpha[..., None], 1e-4)
    img = np.dstack([np.clip(rgb, 0, 255), alpha * 255]).astype(np.uint8)
    Image.fromarray(img, "RGBA").save(out)
    return out


def overlay_args(titles: list[dict], pngs: list[Path], pic_y: int) -> tuple[list[str], str]:
    """ffmpeg inputs + filter_complex chain overlaying each title PNG (faded) at the picture's top edge."""
    inputs, chain, last = [], [], "0:v"
    for i, (t, p) in enumerate(zip(titles, pngs), 1):
        inputs += ["-loop", "1", "-t", f"{t['start'] + t['dur'] + 0.1:.3f}", "-i", str(p)]
        fade = min(0.7, t["dur"] / 3)
        chain.append(f"[{i}:v]format=rgba,fade=t=in:st={t['start']:.3f}:d={fade:.3f}:alpha=1,"
                     f"fade=t=out:st={t['start'] + t['dur'] - fade:.3f}:d={fade:.3f}:alpha=1[t{i}]")
        chain.append(f"[{last}][t{i}]overlay=0:{pic_y}:eof_action=pass[o{i}]")
        last = f"o{i}"
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
