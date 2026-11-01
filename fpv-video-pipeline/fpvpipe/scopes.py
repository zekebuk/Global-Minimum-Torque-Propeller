"""Waveform, RGB parade and vectorscope images (numpy), for before/after review of an automatic grade."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .colorsci import chroma, luma

H = 256


def _density(hist: np.ndarray) -> np.ndarray:
    h = np.log1p(hist)
    return h / h.max() if h.max() > 0 else h


def _graticule(img: np.ndarray, levels=(0.0, 0.1, 0.5, 0.9, 1.0)):
    for lv in levels:
        y = int((1 - lv) * (H - 1))
        img[y, ::4] = np.maximum(img[y, ::4], 90)


def waveform(frame: np.ndarray, width: int = 384) -> np.ndarray:
    """Luma waveform: x = image column, y = luma 0 (bottom) .. 1 (top)."""
    h_, w_ = frame.shape[:2]
    y = luma(frame)
    cols = np.repeat(np.arange(w_)[None, :], h_, 0) * width // w_
    rows = (np.clip(y, 0, 1) * (H - 1)).astype(int)
    hist = np.zeros((H, width))
    np.add.at(hist, (H - 1 - rows.ravel(), cols.ravel()), 1)
    g = (_density(hist) * 255).astype(np.uint8)
    img = np.stack([g * 0.6, g, g * 0.6], -1).astype(np.uint8)
    _graticule(img)
    return img


def parade(frame: np.ndarray, width: int = 384) -> np.ndarray:
    h_, w_ = frame.shape[:2]
    sub = width // 3
    panels = []
    for ch, tint in ((0, (1, 0.25, 0.25)), (1, (0.25, 1, 0.25)), (2, (0.35, 0.45, 1))):
        cols = np.repeat(np.arange(w_)[None, :], h_, 0) * sub // w_
        rows = (np.clip(frame[..., ch], 0, 1) * (H - 1)).astype(int)
        hist = np.zeros((H, sub))
        np.add.at(hist, (H - 1 - rows.ravel(), cols.ravel()), 1)
        d = _density(hist)
        p = (np.stack([d * tint[0], d * tint[1], d * tint[2]], -1) * 255).astype(np.uint8)
        _graticule(p)
        panels.append(p)
    return np.concatenate(panels, 1)


def vectorscope(frame: np.ndarray, size: int = H, neutral: tuple[float, float] | None = None) -> np.ndarray:
    """Cb right, Cr up, centre = neutral. Ring = 0.2 chroma; magenta dot = mean chroma of the detected neutrals."""
    cb, cr = chroma(frame.reshape(-1, 3))
    to_x = lambda v: int(np.clip((v + 0.5) * (size - 1), 0, size - 1))
    to_y = lambda v: int(np.clip((0.5 - v) * (size - 1), 0, size - 1))
    x = np.clip((cb + 0.5) * (size - 1), 0, size - 1).astype(int)
    y = np.clip((0.5 - cr) * (size - 1), 0, size - 1).astype(int)
    hist = np.zeros((size, size))
    np.add.at(hist, (y, x), 1)
    g = (_density(hist) * 255).astype(np.uint8)
    img = np.stack([g, g, g], -1)
    yy, xx = np.mgrid[:size, :size]
    r = np.hypot(xx - size / 2, yy - size / 2)
    img[np.abs(r - 0.2 * size) < 0.6] = 80
    img[size // 2, :] = np.maximum(img[size // 2, :], 50)
    img[:, size // 2] = np.maximum(img[:, size // 2], 50)
    if neutral is not None:
        cx, cy = to_x(neutral[0]), to_y(neutral[1])
        img[max(0, cy - 2):cy + 3, max(0, cx - 2):cx + 3] = [255, 0, 255]
    return img


def panel(frame: np.ndarray, label: str = "", neutral: tuple[float, float] | None = None) -> np.ndarray:
    """Thumbnail | waveform | parade | vectorscope, one row."""
    h_, w_ = frame.shape[:2]
    th = (np.clip(frame, 0, 1) * 255).astype(np.uint8)
    ys = (np.arange(H) * h_ // H).clip(0, h_ - 1)
    tw = int(w_ * H / h_)
    xs = (np.arange(tw) * w_ // tw).clip(0, w_ - 1)
    thumb = th[ys][:, xs]
    return np.concatenate([thumb, waveform(frame), parade(frame), vectorscope(frame, neutral=neutral)], 1)


def save_png(img: np.ndarray, path: Path):
    h, w = img.shape[:2]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                    "-i", "-", str(path)], input=np.ascontiguousarray(img, np.uint8).tobytes(), check=True)
