"""Flow plots from an OpenFOAM MRF case, read directly from the written fields (no function objects).

- Meridional slice (plane y = 0, through the rotor axis and blade 1): induced axial velocity
  w = -U_z - V (positive = added downwash beyond the free stream) and pressure coefficient
  p / (0.5 (Omega R)^2), sampled on a regular grid from the nearest cell (a cell-by-cell cut).
- Blade surface: each wall face coloured by the local pressure coefficient
  Cp = p / (0.5 W^2), W^2 = (Omega r)^2 + V^2 (induced velocity left out), seen from the suction
  (+z) and pressure (-z) sides.
- Chordwise sections: wall faces of blade 1 in a thin strip at each r/R, placed in the section's
  chord frame with the design section's pitch, chord, and pitch axis.

All pressures in OpenFOAM's incompressible solver are kinematic (p / rho).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from prandtl_prop.cfd.openfoam import PolyMesh, face_geometry


def all_cell_centres(mesh: PolyMesh) -> np.ndarray:
    """Cell centres as the mean of their face centres (vectorised accumulation)."""

    face_centres = np.array([mesh.points[face].mean(axis=0) for face in mesh.faces])
    cells = int(max(mesh.owner.max(), mesh.neighbour.max())) + 1
    total = np.zeros((cells, 3))
    count = np.zeros(cells)
    internal = len(mesh.neighbour)
    for cell_ids, centres in ((mesh.owner, face_centres), (mesh.neighbour, face_centres[:internal])):
        for k in range(3):
            total[:, k] += np.bincount(cell_ids, weights=centres[:, k], minlength=cells)
        count += np.bincount(cell_ids, minlength=cells)
    return total / np.maximum(count, 1)[:, None]


def meridional_slice(
    centres: np.ndarray,
    U: np.ndarray,
    p: np.ndarray,
    *,
    radius_m: float,
    omega_rad_s: float,
    v_inf_m_s: float,
    window: tuple[float, float, float, float],
    resolution: int = 420,
):
    """Cutting plane y = 0 sampled on a regular (x, z) grid from the nearest cell (cell-by-cell, like a cut).

    Returns (x grid, z grid, induced w, Cp); points farther from any cell centre than the local cell
    size (inside the blade) are masked.
    """

    from scipy.spatial import cKDTree

    tree = cKDTree(centres)
    size = tree.query(centres, k=2)[0][:, 1]
    x0, x1, z0, z1 = window
    nx = resolution
    nz = int(resolution * (z1 - z0) / (x1 - x0))
    X, Z = np.meshgrid(np.linspace(x0, x1, nx), np.linspace(z0, z1, nz))
    query = np.column_stack([X.ravel(), np.zeros(X.size), Z.ravel()])
    distance, neighbours = tree.query(query, k=8)
    index = neighbours[:, 0]
    # Inside the blade the nearest cells are all small (surface refinement) yet far away; at a
    # refinement-level jump one of the neighbours is large, so the test does not fire there.
    inside_solid = distance[:, 0] > 1.5 * size[neighbours].max(axis=1)
    w = (-U[index, 2] - v_inf_m_s).reshape(X.shape)
    cp = (p[index] / (0.5 * (omega_rad_s * radius_m) ** 2)).reshape(X.shape)
    mask = inside_solid.reshape(X.shape)
    return X, Z, np.ma.array(w, mask=mask), np.ma.array(cp, mask=mask)


def plot_meridional(slice_data, *, radius_m: float, title: str, output: Path, blade_outline: np.ndarray | None = None) -> Path:
    import matplotlib.pyplot as plt
    from matplotlib.colors import TwoSlopeNorm

    from prandtl_prop.plot_style import diverging_cmap, style

    style()
    X, Z, w, cp = slice_data
    figure, axes = plt.subplots(1, 2, figsize=(13, 6.6))
    for ax, values, label, name in (
        (axes[0], w, "induced axial velocity w (m/s, + = downwash)", "Induced axial velocity"),
        (axes[1], cp, "p / (0.5 (Omega R)^2)", "Pressure"),
    ):
        limit = float(np.nanpercentile(np.abs(values.compressed()), 99.0)) or 1.0
        norm = TwoSlopeNorm(vmin=-limit, vcenter=0.0, vmax=limit)
        field = ax.pcolormesh(X / radius_m, Z / radius_m, values, cmap=diverging_cmap(), norm=norm, shading="nearest", rasterized=True)
        if blade_outline is not None:
            ax.plot(blade_outline[:, 0] / radius_m, blade_outline[:, 1] / radius_m, color="#0b0b0b", linewidth=0.8)
        ax.axhline(0.0, color="#898781", linewidth=0.6, linestyle=":")
        ax.set_aspect("equal")
        ax.set_xlabel("x / R (blade 1 to the right)")
        ax.set_ylabel("z / R (thrust up; wake below)")
        ax.set_title(name)
        ax.grid(False)
        figure.colorbar(field, ax=ax, shrink=0.82, label=label, extend="both")
    figure.suptitle(title, fontsize=12)
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150)
    plt.close(figure)
    return output


def blade_surface(mesh: PolyMesh, p_wall: np.ndarray, *, omega_rad_s: float, v_inf_m_s: float, patch: str = "blade"):
    """Triangles (fan per wall face) and their local Cp."""

    faces = mesh.patch_faces(patch)
    triangles, values = [], []
    for row, face_id in enumerate(faces):
        vertices = mesh.points[mesh.faces[face_id]]
        centre = vertices.mean(axis=0)
        r = math.hypot(centre[0], centre[1])
        cp = p_wall[row] / (0.5 * ((omega_rad_s * r) ** 2 + v_inf_m_s**2))
        for k in range(1, len(vertices) - 1):
            triangles.append((vertices[0], vertices[k], vertices[k + 1]))
            values.append(cp)
    return np.array(triangles), np.array(values)


def plot_blade_cp(triangles: np.ndarray, cp: np.ndarray, *, title: str, output: Path, limits: tuple[float, float] = (-1.5, 1.0)) -> Path:
    import matplotlib.pyplot as plt
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import TwoSlopeNorm

    from prandtl_prop.cad.render import draw_data
    from prandtl_prop.plot_style import diverging_cmap, style

    style()
    norm = TwoSlopeNorm(vmin=limits[0], vcenter=0.0, vmax=limits[1])
    cmap = diverging_cmap()
    # A long two-blade rotor reads best in stacked rows; a near-round rotor (3+ blades) side by side.
    extent = np.ptp(triangles[..., 0]) / max(np.ptp(triangles[..., 1]), 1e-12)
    if extent > 3.0:
        figure, axes = plt.subplots(2, 1, figsize=(13, 6.2))
    else:
        figure, axes = plt.subplots(1, 2, figsize=(13, 6.0))
    scaled = triangles * 1000.0
    for ax, (name, elevation, azimuth) in zip(
        axes, (("Suction side (from +z)", 89.0, -90.0), ("Pressure side (from -z)", -89.0, -90.0)), strict=True
    ):
        draw_data(ax, scaled, cp, cmap, norm, elevation, azimuth)
        ax.set_title(name)
    figure.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=axes, shrink=0.9, fraction=0.03, label="Cp = p / (0.5 ((Omega r)^2 + V^2))")
    figure.suptitle(title, fontsize=12)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(figure)
    return output


def section_cp(
    mesh: PolyMesh,
    p_wall: np.ndarray,
    sections,
    stations_r_R: Sequence[float],
    *,
    radius_m: float,
    omega_rad_s: float,
    v_inf_m_s: float,
    patch: str = "blade",
) -> dict[float, dict[str, np.ndarray]]:
    """Chordwise Cp on blade 1 (x > 0) at each r/R: x/c, Cp, and side (+1 suction, -1 pressure)."""

    faces = list(mesh.patch_faces(patch))
    centres, areas = face_geometry(mesh, faces)
    r = np.hypot(centres[:, 0], centres[:, 1])
    radii = np.array([s.r_m for s in sections])
    out: dict[float, dict[str, np.ndarray]] = {}
    for r_R in stations_r_R:
        r_s = r_R * radius_m
        beta = math.radians(float(np.interp(r_s, radii, [s.beta_deg for s in sections])))
        chord = float(np.interp(r_s, radii, [s.chord_m for s in sections]))
        axis_fraction = float(sections[0].pitch_axis_chord_fraction)
        strip = (np.abs(r - r_s) < 0.008 * radius_m) & (centres[:, 0] > 0.0)
        y, z = centres[strip, 1], centres[strip, 2]
        s_coord = -(y * math.cos(beta) + z * math.sin(beta))
        t_hat = np.array([-math.sin(beta), math.cos(beta)])  # (y, z) of the section's thickness direction
        outward = -areas[strip][:, 1:] / np.linalg.norm(areas[strip], axis=1)[:, None]
        side = np.sign(outward @ t_hat)
        x_c = (s_coord + axis_fraction * chord) / chord
        cp = p_wall[strip] / (0.5 * ((omega_rad_s * r_s) ** 2 + v_inf_m_s**2))
        order = np.argsort(x_c)
        out[float(r_R)] = {"x_c": x_c[order], "cp": cp[order], "side": side[order]}
    return out


def plot_section_cp(data_by_point: dict[str, dict[float, dict[str, np.ndarray]]], labels: dict[str, str], output: Path, title: str) -> Path:
    """-Cp vs x/c at each station, one colour per mission point (ordinal), suction solid, pressure dashed."""

    import matplotlib.pyplot as plt

    from prandtl_prop.plot_style import ordinal, style

    style()
    names = list(data_by_point)
    stations = sorted(next(iter(data_by_point.values())))
    figure, axes = plt.subplots(1, len(stations), figsize=(4.2 * len(stations), 4.4), sharey=True)
    colors = ordinal(len(names))
    for ax, r_R in zip(np.atleast_1d(axes), stations, strict=True):
        for name, color in zip(names, colors, strict=True):
            d = data_by_point[name][r_R]
            for side, style_ in ((1.0, "-"), (-1.0, "--")):
                pick = d["side"] == side
                ax.plot(
                    d["x_c"][pick], -d["cp"][pick], linestyle=style_, color=color, linewidth=1.4, label=labels[name] if side > 0 else None
                )
        ax.set_title(f"r/R = {r_R:.2f}")
        ax.set_xlabel("x / c")
        ax.set_xlim(-0.02, 1.02)
    np.atleast_1d(axes)[0].set_ylabel("-Cp (solid: suction side, dashed: pressure side)")
    np.atleast_1d(axes)[-1].legend(loc="upper right")
    figure.suptitle(title, fontsize=12)
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150)
    plt.close(figure)
    return output
