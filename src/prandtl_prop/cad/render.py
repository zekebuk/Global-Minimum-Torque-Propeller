"""Shaded renders of blade/rotor STL files with matplotlib (no extra dependencies)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import numpy as np


def read_stl(path: Path) -> np.ndarray:
    """Triangles as an (n, 3, 3) array from an ASCII or binary STL."""

    data = Path(path).read_bytes()
    if data[:5].lower() == b"solid" and b"facet" in data[:2048]:
        values = np.array(re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data), dtype=float)
        return values.reshape(-1, 3, 3)
    count = int.from_bytes(data[80:84], "little")
    records = np.frombuffer(data[84 : 84 + 50 * count], dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    return records["v"].astype(float)


def _shade(triangles: np.ndarray, light: np.ndarray, base_rgb: Sequence[float], view: np.ndarray | None = None) -> np.ndarray:
    """Two-sided Lambert diffuse plus a Blinn-Phong highlight, so surface curvature reads in a still image."""

    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1.0e-300)
    light = light / np.linalg.norm(light)
    diffuse = np.abs(normals @ light)
    base = np.asarray(base_rgb, dtype=float)
    colour = (0.25 + 0.75 * diffuse)[:, None] * (base if base.ndim == 2 else base[None, :])
    if view is not None:
        half = light + view / np.linalg.norm(view)
        half /= np.linalg.norm(half)
        colour += 0.35 * np.abs(normals @ half)[:, None] ** 40
    return np.clip(colour, 0.0, 1.0)


def _camera(elevation_deg: float, azimuth_deg: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Orthographic camera: (right, up, toward-camera) unit vectors for a view from the given direction."""

    e, a = np.radians(elevation_deg), np.radians(azimuth_deg)
    toward = np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])
    right = np.cross(np.array([0.0, 0.0, 1.0]), toward)
    right = right / np.linalg.norm(right) if np.linalg.norm(right) > 1.0e-9 else np.array([1.0, 0.0, 0.0])
    return right, np.cross(toward, right), toward


def subdivide(triangles: np.ndarray, max_edge: float) -> np.ndarray:
    """Bisect each triangle's longest edge until every edge is below ``max_edge``.

    The painter's algorithm sorts whole triangles by centroid depth, which misorders a large flat
    face (two triangles for a mold block side) against small ones; small triangles sort correctly.
    """

    done = []
    while len(triangles):
        lengths = np.linalg.norm(triangles - np.roll(triangles, -1, axis=1), axis=2)  # edge i runs vertex i -> i+1
        small = lengths.max(axis=1) <= max_edge
        done.append(triangles[small])
        big, longest = triangles[~small], lengths[~small].argmax(axis=1)
        rows = np.arange(len(big))
        a, b, c = (big[rows, (longest + k) % 3] for k in range(3))  # longest edge is a -> b
        middle = 0.5 * (a + b)
        triangles = np.concatenate([np.stack((a, middle, c), axis=1), np.stack((middle, b, c), axis=1)])  # bisect it
    return np.concatenate(done)


def draw_shaded(
    axes, triangles: np.ndarray, elevation_deg: float, azimuth_deg: float, base_rgb: Sequence[float] | np.ndarray, light: Sequence[float]
) -> None:
    """Painter's-algorithm orthographic render of triangles onto a 2D matplotlib axes.

    ``base_rgb`` is one colour, or one per triangle as an (n, 3) array.
    """

    from matplotlib.collections import PolyCollection

    right, up, toward = _camera(elevation_deg, azimuth_deg)
    projected = np.stack([triangles @ right, triangles @ up], axis=-1)
    order = np.argsort((triangles @ toward).mean(axis=1))  # far to near
    colours = _shade(triangles, np.asarray(light, dtype=float), base_rgb, toward)
    axes.add_collection(PolyCollection(projected[order], facecolors=colours[order], edgecolors=colours[order], linewidths=0.25))
    axes.autoscale_view()
    axes.set_aspect("equal")
    axes.set_axis_off()


def render(
    stl_path: Path,
    output: Path,
    *,
    sections: Sequence[np.ndarray],
    radii_m: Sequence[float],
    title: str = "",
    scale: float = 1000.0,
    unit: str = "mm",
    base_rgb: Sequence[float] = (0.40, 0.47, 0.56),
) -> Path:
    """Two shaded orthographic views at true proportions, a planform, and a down-span section stack (twist)."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import cm

    triangles = read_stl(stl_path) * scale
    figure = plt.figure(figsize=(14, 9.5), facecolor="white")
    grid = figure.add_gridspec(2, 2, height_ratios=(1.0, 0.9), hspace=0.12, wspace=0.08)

    views = (
        (grid[0, 0], "3/4 view from the leading edge, above (thrust side)", 32.0, 60.0),
        (grid[0, 1], "3/4 view from outboard of the tip: twist and taper", 22.0, 20.0),
    )
    for slot, name, elevation, azimuth in views:
        axes = figure.add_subplot(slot)
        draw_shaded(axes, triangles, elevation, azimuth, base_rgb, light=(0.3, 0.6, 1.0))
        axes.set_title(name, fontsize=10)

    plan = figure.add_subplot(grid[1, 0])
    leading = np.array([s[np.argmax(s[:, 1])] for s in sections]) * scale
    trailing = np.array([0.5 * (s[0] + s[-1]) for s in sections]) * scale
    outline = np.vstack([leading[:, :2], trailing[::-1, :2]])
    plan.fill(outline[:, 0], outline[:, 1], color=base_rgb, alpha=0.85)
    plan.plot(leading[:, 0], leading[:, 1], color="#1f3b57", linewidth=1.2, label="leading edge")
    plan.plot(trailing[:, 0], trailing[:, 1], color="#c4661f", linewidth=1.2, label="trailing edge")
    plan.axhline(0.0, color="0.4", linewidth=0.7, linestyle="--", label="pitch axis (x)")
    plan.set_aspect("equal")
    plan.set_xlabel(f"radius x [{unit}]")
    plan.set_ylabel(f"y [{unit}] (motion +y)")
    plan.set_title("Planform, seen from the thrust side (+z)", fontsize=10)
    plan.legend(fontsize=8, loc="lower right")
    plan.grid(color="0.9")

    stack = figure.add_subplot(grid[1, 1])
    colours = cm.viridis(np.linspace(0.0, 1.0, len(sections)))
    for points_m, colour in zip(sections, colours, strict=True):
        loop = np.vstack([points_m, points_m[:1]]) * scale
        stack.plot(loop[:, 1], loop[:, 2], color=colour, linewidth=1.1)
    stack.set_aspect("equal")
    stack.set_xlabel(f"y [{unit}]")
    stack.set_ylabel(f"z thrust [{unit}]")
    stack.set_title("Sections looking inboard from the tip: twist", fontsize=10)
    stack.grid(color="0.9")
    scalar = cm.ScalarMappable(cmap="viridis", norm=plt.Normalize(min(radii_m) * scale, max(radii_m) * scale))
    figure.colorbar(scalar, ax=stack, fraction=0.035, pad=0.02, label=f"radius [{unit}]")

    if title:
        figure.suptitle(title, fontsize=12, y=0.98)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(figure)
    return output


def render_comparison(
    variants: dict[str, Sequence],
    output: Path,
    *,
    shaded_stl: Path | None = None,
    shaded_label: str = "",
    max_thickness_m: float | None = None,
    title: str = "",
) -> Path:
    """Compare blade variants: spanwise max thickness, root and tip sections, trailing-edge detail, and a shaded view.

    ``variants`` maps a label to a list of ``BladeSection`` (same stations for every variant).
    """

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colours = ["#5b6770", "#c4661f", "#2a7ab0", "#3c9a5f"]
    figure = plt.figure(figsize=(14, 9.5), facecolor="white")
    grid = figure.add_gridspec(2, 3, hspace=0.32, wspace=0.28)

    span = figure.add_subplot(grid[0, 0])
    for (label, sections), colour in zip(variants.items(), colours, strict=False):
        r = [s.r_m * 1000 for s in sections]
        t = [s.airfoil.max_thickness * s.chord_m * 1000 for s in sections]
        span.plot(r, t, color=colour, linewidth=1.8, label=label)
    if max_thickness_m is not None:
        span.axhline(max_thickness_m * 1000, color="0.3", linestyle="--", linewidth=0.9, label="molding max thickness")
    span.set_xlabel("radius [mm]")
    span.set_ylabel("max section thickness [mm]")
    span.set_title("Spanwise thickness", fontsize=10)
    span.legend(fontsize=8)
    span.grid(color="0.9")

    for slot, index, name in ((grid[0, 1], 0, "Root section"), (grid[0, 2], -1, "Tip section")):
        axes = figure.add_subplot(slot)
        for (label, sections), colour in zip(variants.items(), colours, strict=False):
            outline = sections[index].local_outline() * 1000
            loop = np.vstack([outline, outline[:1]])
            axes.plot(loop[:, 0], loop[:, 1], color=colour, linewidth=1.3, label=label)
        axes.set_aspect("equal")
        axes.set_xlabel("chordwise from pitch axis [mm]")
        axes.set_ylabel("thickness direction [mm]")
        axes.set_title(f"{name} (r = {list(variants.values())[0][index].r_m * 1000:.0f} mm)", fontsize=10)
        axes.grid(color="0.9")

    detail = figure.add_subplot(grid[1, 0])
    for (label, sections), colour in zip(variants.items(), colours, strict=False):
        outline = sections[-1].local_outline() * 1000
        loop = np.vstack([outline, outline[:1]])
        detail.plot(loop[:, 0], loop[:, 1], color=colour, linewidth=1.5, label=label)
    te = list(variants.values())[0][-1].local_outline()[0] * 1000
    detail.set_xlim(te[0] - 4.0, te[0] + 0.6)
    detail.set_ylim(te[1] - 1.5, te[1] + 1.5)
    detail.set_aspect("equal")
    detail.set_title("Tip trailing edge: blunted for molding", fontsize=10)
    detail.set_xlabel("chordwise [mm]")
    detail.grid(color="0.9")

    if shaded_stl is not None:
        shaded = figure.add_subplot(grid[1, 1:])
        draw_shaded(shaded, read_stl(shaded_stl) * 1000, 30.0, 58.0, (0.22, 0.24, 0.27), light=(0.3, 0.6, 1.0))
        shaded.set_title(shaded_label, fontsize=10)

    if title:
        figure.suptitle(title, fontsize=12, y=0.98)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(figure)
    return output


def render_mold(
    parts: Sequence[tuple[Path, Sequence[float], Sequence[float]]], output: Path, *, title: str = "", scale: float = 1000.0
) -> Path:
    """Exploded view of mold inserts and part. ``parts``: (STL in metres, colour, offset in metres)."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def scene(items):
        triangles, colours = [], []
        for path, colour, offset in items:
            t = read_stl(path) * scale + np.asarray(offset) * scale
            t = subdivide(t, 6.0)
            triangles.append(t)
            colours.append(np.tile(np.asarray(colour, dtype=float), (len(t), 1)))
        return np.concatenate(triangles), np.concatenate(colours)

    figure = plt.figure(figsize=(14, 7.5), facecolor="white")
    triangles, colours = scene(parts)
    for index, (name, elevation, azimuth) in enumerate(
        (("Exploded, from above the leading edge", 28.0, 62.0), ("Exploded, from the root end", 12.0, 160.0))
    ):
        axes = figure.add_subplot(1, 2, index + 1)
        draw_shaded(axes, triangles, elevation, azimuth, colours, light=(0.3, 0.6, 1.0))
        axes.set_title(name, fontsize=10)
    if title:
        figure.suptitle(title, fontsize=12)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(figure)
    return output
