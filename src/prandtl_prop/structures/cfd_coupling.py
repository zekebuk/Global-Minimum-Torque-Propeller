"""One-way CFD -> FEA coupling: live wall pressure and shear from OpenFOAM onto the CalculiX blade.

Method (first principles):

- The FE blade surface is the set of C3D10 faces used by one element only. Face corner and mid-side
  nodes follow CalculiX's C3D10 face numbering; the outward normal points away from the element's
  fourth corner.
- Each FE face takes the traction of the nearest CFD wall face on the same blade (blade 1, x > 0):
  pressure p (Pa, gauge to the free stream) acting along the inward normal, plus the wall shear
  traction tau = F_viscous / A. The FE face force is F = (-p n_out + tau) A_FE.
- Consistent nodal loads: for a constant traction on a flat 6-node triangle, the corner nodes carry
  zero and each mid-side node one third of the face force (the integral of the quadratic shape
  functions).
- Equilibrium check: the mapped total force and the torque about the rotor axis are compared with the
  CFD integral over the same blade's wall faces. A near-uniform gauge offset integrates to zero on
  the closed FE surface, so it cannot add a net force.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

# C3D10 faces (0-based, CalculiX node order): corners, then the mid-side nodes between them.
_C3D10_FACES = (
    ((0, 1, 2), (4, 5, 6), 3),
    ((0, 3, 1), (7, 8, 4), 2),
    ((1, 3, 2), (8, 9, 5), 0),
    ((2, 3, 0), (9, 7, 6), 1),
)


def surface_faces(elements: np.ndarray, nodes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Boundary faces of a C3D10 mesh: (corner node triples oriented outward, mid-side node triples)."""

    corners, mids, opposite = [], [], []
    for face_corners, face_mids, other in _C3D10_FACES:
        corners.append(elements[:, face_corners])
        mids.append(elements[:, face_mids])
        opposite.append(elements[:, other])
    corners, mids, opposite = np.vstack(corners), np.vstack(mids), np.concatenate(opposite)
    key = np.sort(corners, axis=1)
    _, inverse, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    boundary = counts[inverse.reshape(-1)] == 1
    corners, mids, opposite = corners[boundary], mids[boundary], opposite[boundary]
    p0, p1, p2 = (nodes[corners[:, k]] for k in range(3))
    normal = np.cross(p1 - p0, p2 - p0)
    inward = ((nodes[opposite] - p0) * normal).sum(axis=1) > 0.0
    corners[inward] = corners[inward][:, [0, 2, 1]]
    mids[inward] = mids[inward][:, [2, 1, 0]]  # keep each mid node between the same two corners
    return corners, mids


def map_wall_loads(
    nodes: np.ndarray,
    elements: np.ndarray,
    cfd_centres: np.ndarray,
    cfd_area_vectors: np.ndarray,
    cfd_pressure_force: np.ndarray,
    cfd_viscous_force: np.ndarray,
    *,
    blade_side: float = 1.0,
) -> tuple[np.ndarray, dict[str, float]]:
    """Nodal forces (n_nodes, 3) on the FE blade from the CFD wall loads of the blade at sign(x) = blade_side."""

    from scipy.spatial import cKDTree

    pick = np.sign(cfd_centres[:, 0]) == blade_side
    centres, areas = cfd_centres[pick], cfd_area_vectors[pick]
    magnitude = np.linalg.norm(areas, axis=1)
    into_blade = areas / magnitude[:, None]
    pressure = (cfd_pressure_force[pick] * into_blade).sum(axis=1) / magnitude  # Pa, + pushes into the blade
    shear = cfd_viscous_force[pick] / magnitude[:, None]
    corners, mids = surface_faces(elements, nodes)
    p0, p1, p2 = (nodes[corners[:, k]] for k in range(3))
    area_vector = 0.5 * np.cross(p1 - p0, p2 - p0)  # outward, |.| = face area
    face_area = np.linalg.norm(area_vector, axis=1)
    outward = area_vector / face_area[:, None]
    centroid = (p0 + p1 + p2) / 3.0
    distance, nearest = cKDTree(centres).query(centroid)
    force = (-pressure[nearest, None] * outward + shear[nearest]) * face_area[:, None]
    nodal = np.zeros_like(nodes)
    for k in range(3):
        np.add.at(nodal, mids[:, k], force / 3.0)
    cfd_force = (cfd_pressure_force[pick] + cfd_viscous_force[pick]).sum(axis=0)
    cfd_torque = float(
        (
            centres[:, 0] * (cfd_pressure_force[pick] + cfd_viscous_force[pick])[:, 1]
            - centres[:, 1] * (cfd_pressure_force[pick] + cfd_viscous_force[pick])[:, 0]
        ).sum()
    )
    mapped_torque = float((nodes[:, 0] * nodal[:, 1] - nodes[:, 1] * nodal[:, 0]).sum())
    total = nodal.sum(axis=0)
    diagnostics = {
        "cfd_blade_force_z_N": float(cfd_force[2]),
        "mapped_force_z_N": float(total[2]),
        "mapped_over_cfd_force_z": float(total[2] / cfd_force[2]) if cfd_force[2] else float("nan"),
        "cfd_blade_torque_z_Nm": cfd_torque,
        "mapped_torque_z_Nm": mapped_torque,
        "mapped_over_cfd_torque_z": mapped_torque / cfd_torque if cfd_torque else float("nan"),
        "fe_surface_faces": int(len(corners)),
        "cfd_blade_faces": int(pick.sum()),
        "max_mapping_distance_m": float(distance.max()),
        "median_mapping_distance_m": float(np.median(distance)),
    }
    return nodal, diagnostics


def read_frd_nodal(path: Path) -> dict[str, np.ndarray]:
    """Last DISP and STRESS nodal blocks of a CalculiX .frd (ASCII): {name: (n_nodes_in_file, components)} by node id order."""

    blocks: dict[str, dict[int, list[float]]] = {}
    name = None
    current: dict[int, list[float]] = {}
    with Path(path).open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith(" -4"):
                name = line.split()[1]
                current = {}
            elif line.startswith(" -1") and name is not None:
                node = int(line[3:13])
                values = [float(v) for v in re.findall(r"-?\d\.\d+E[+-]\d+", line[13:])]
                current[node] = values
            elif line.startswith(" -3") and name is not None:
                blocks[name] = current
                name = None
    out = {}
    for key, data in blocks.items():
        ids = np.array(sorted(data))
        out[key] = np.array([data[i] for i in ids])
        out[key + "_ids"] = ids
    return out


def von_mises(stress: np.ndarray) -> np.ndarray:
    """From CalculiX stress columns SXX, SYY, SZZ, SXY, SYZ, SZX."""

    sxx, syy, szz, sxy, syz, szx = (stress[:, k] for k in range(6))
    return np.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2) + 3.0 * (sxy**2 + syz**2 + szx**2))


def plot_fe_results(
    nodes: np.ndarray,
    elements: np.ndarray,
    displacement: np.ndarray,
    stress_vm: np.ndarray,
    *,
    title: str,
    output: Path,
    scale: float | None = None,
) -> Path:
    """Two rows: von Mises on the deformed blade (suction-side and leading-edge views), and deflection."""

    import matplotlib.pyplot as plt
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import Normalize

    from prandtl_prop.cad.render import draw_data
    from prandtl_prop.plot_style import sequential_cmap, style

    style()
    corners, _ = surface_faces(elements, nodes)
    magnitude = np.linalg.norm(displacement, axis=1)
    if scale is None:
        span = float(np.ptp(nodes[:, 0]))
        scale = 0.08 * span / max(float(magnitude.max()), 1e-12)  # tip moves ~8% of span on screen
    deformed = (nodes + scale * displacement) * 1000.0
    triangles = deformed[corners]
    vm_face = stress_vm[corners].mean(axis=1) / 1e6
    d_face = magnitude[corners].mean(axis=1) * 1000.0
    figure, axes = plt.subplots(2, 2, figsize=(15, 8.5))
    views = (("From above (suction side)", 89.0, -90.0), ("From the leading edge, 20 deg up", 20.0, 90.0))
    for row, (values, cmap, label) in enumerate(
        ((vm_face, sequential_cmap("orange"), "von Mises (MPa)"), (d_face, sequential_cmap("blue"), "deflection (mm)"))
    ):
        top = float(np.percentile(values, 99.5))
        norm = Normalize(vmin=0.0, vmax=top)
        for col, (name, elevation, azimuth) in enumerate(views):
            draw_data(axes[row, col], triangles, values, cmap, norm, elevation, azimuth)
            axes[row, col].set_title(f"{name}: {label}")
        figure.colorbar(
            ScalarMappable(norm=norm, cmap=cmap), ax=axes[row, :], shrink=0.8, label=label + " (scale capped at 99.5th percentile)"
        )
    figure.suptitle(f"{title} (deformation x{scale:.0f})", fontsize=12)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(figure)
    return output
