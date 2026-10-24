"""Solid-element blade FEA with CalculiX: gmsh C3D10 mesh, CENTRIF body load, mapped aero loads.

Model, in SI units and the shared blade frame (+x span, +z thrust and rotation axis):

- Mesh: the CAD solid (``blade_m.brep``, metres) meshed with quadratic tetrahedra (C3D10). Linear
  tetrahedra lock in bending and are not used.
- Boundary condition: every node on the root face (x = r_root) is fixed.
- Centrifugal load: CalculiX ``*DLOAD CENTRIF`` with omega^2 about +z. This applies the exact body
  force, including the propeller moment.
- Aero load: each BEMT station's per-blade force and moment about the pitch axis, both times the
  station width, go to the surface nodes in that station's strip. The distribution is the
  minimum-norm one that reproduces the force and the moment exactly (6 equations). This is the
  idea behind an RBE3 element, so torsion is not biased toward the mid-chord.
- ``nlgeom=True`` includes geometric stiffening, so centrifugal tension resists flap bending.

Requires ``ccx`` on PATH (or ``ccx_executable``) and the ``gmsh`` Python module.

CalculiX runs single-threaded by default. Its multithreaded SPOOLES factorization was observed to
give run-to-run eigenvalue scatter of 0.4%, and occasionally spurious negative eigenvalues, on
identical input decks. Single-threaded results are bit-identical.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from prandtl_prop.cad.sections import BladeSection
from prandtl_prop.structures.loads import LineLoads

# gmsh 10-node tet edge order (01,12,20,30,32,31) -> CalculiX/Abaqus C3D10 (01,12,20,03,13,23).
GMSH_TO_CCX_TET10 = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]


@dataclass(frozen=True)
class TetMesh:
    nodes: np.ndarray  # (n, 3) metres
    elements: np.ndarray  # (m, 10) zero-based node indices, CalculiX order
    surface_nodes: np.ndarray  # zero-based indices of nodes on the boundary surface
    min_quality: float  # worst element signed inverse condition number (gmsh SICN; <= 0 is invalid)
    curved: bool = True  # False: straight-sided C3D10 (fallback when gmsh's high-order optimiser aborts)


@dataclass(frozen=True)
class CalculixResult:
    displacement: np.ndarray  # (n, 3)
    von_mises_ip: np.ndarray  # per integration point
    ip_element: np.ndarray  # element index of each integration point
    root_reaction_N: np.ndarray  # (3,)
    returncode: int
    log: str


def _mesh_solid(brep_path: Path, size_m: float, min_size_m: float | None, curved: bool) -> TetMesh:
    """Mesh a BREP solid with second-order tetrahedra (see ``mesh_solid``)."""

    import gmsh

    gmsh.initialize(interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.NumThreads", 1)
        gmsh.option.setNumber("Mesh.RandomSeed", 1)
        gmsh.model.add("blade")
        gmsh.model.occ.importShapes(str(brep_path))
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMax", size_m)
        gmsh.option.setNumber("Mesh.MeshSizeMin", min_size_m if min_size_m is not None else size_m / 4.0)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
        gmsh.option.setNumber("Mesh.ElementOrder", 2)
        gmsh.option.setNumber("Mesh.SecondOrderLinear", 0 if curved else 1)
        gmsh.option.setNumber("Mesh.HighOrderOptimize", 1 if curved else 0)
        gmsh.model.mesh.generate(3)
        tags, coords, _ = gmsh.model.mesh.getNodes()
        index_of = {int(tag): i for i, tag in enumerate(tags)}
        nodes = np.asarray(coords, dtype=float).reshape(-1, 3)
        element_tags, element_nodes = gmsh.model.mesh.getElementsByType(11)
        quality = gmsh.model.mesh.getElementQualities(element_tags, "minSICN") if len(element_tags) else [0.0]
        tets = np.vectorize(index_of.__getitem__)(np.asarray(element_nodes, dtype=np.int64).reshape(-1, 10))
        surface_tags: set[int] = set()
        for dim, tag in gmsh.model.getEntities(2):
            node_tags, _, _ = gmsh.model.mesh.getNodes(dim, tag, includeBoundary=True)
            surface_tags.update(int(value) for value in node_tags)
    finally:
        gmsh.finalize()
    if len(tets) == 0:
        raise RuntimeError(f"gmsh produced no tetrahedra for {brep_path}")
    surface = np.array(sorted(index_of[tag] for tag in surface_tags))
    return TetMesh(nodes, tets[:, GMSH_TO_CCX_TET10], surface, float(np.min(quality)), curved)


def mesh_solid(brep_path: Path, size_m: float, *, min_size_m: float | None = None) -> TetMesh:
    """Mesh a BREP solid with second-order tetrahedra, in a child process.

    Meshing is single-threaded on purpose: multithreaded 3D meshing is not reproducible, and the
    pipeline's cache assumes identical inputs give identical results. gmsh's high-order optimiser
    can abort the whole process (an uncaught C++ exception, "Failed to reach critical value ...
    ScaledJac") on thin trailing edges, so the mesh is made in a spawned child; if it dies, the mesh
    is remade with straight-sided C3D10 (mid-edge nodes on the chords, which cannot invert).
    """

    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    from concurrent.futures.process import BrokenProcessPool

    context = multiprocessing.get_context("spawn")
    try:
        with ProcessPoolExecutor(1, mp_context=context) as pool:
            return pool.submit(_mesh_solid, Path(brep_path), size_m, min_size_m, True).result()
    except BrokenProcessPool:
        with ProcessPoolExecutor(1, mp_context=context) as pool:
            return pool.submit(_mesh_solid, Path(brep_path), size_m, min_size_m, False).result()


def strip_nodal_forces(
    mesh: TetMesh,
    sections: Sequence[BladeSection],
    loads: LineLoads,
    weights_m: np.ndarray,
    *,
    root_x_m: float,
    root_tolerance_m: float = 1.0e-7,
    inner_edge_m: float | None = None,
) -> np.ndarray:
    """Nodal force array (n, 3) whose strip resultants equal each station's force and moment exactly.

    Root-face nodes are excluded: a load on a clamped node goes straight into the support and would
    silently drop out of the model.
    """

    radii = np.array([section.r_m for section in sections])
    first_edge = -np.inf if inner_edge_m is None else inner_edge_m  # no aero load inboard of the first BEMT strip
    edges = np.concatenate([[first_edge], 0.5 * (radii[1:] + radii[:-1]), [np.inf]])
    forces = np.zeros_like(mesh.nodes)
    free_surface = mesh.surface_nodes[mesh.nodes[mesh.surface_nodes, 0] > root_x_m + root_tolerance_m]
    surface_x = mesh.nodes[free_surface, 0]
    for index, r in enumerate(radii):
        in_strip = free_surface[(surface_x >= edges[index]) & (surface_x < edges[index + 1])]
        if len(in_strip) < 3:
            raise ValueError(f"Station {index} strip has {len(in_strip)} surface nodes; refine the mesh")
        weight = weights_m[index]
        target = np.array(
            [
                loads.f_x[index] * weight,
                loads.f_y[index] * weight,
                loads.f_z[index] * weight,
                loads.m_x[index] * weight,
                loads.m_y[index] * weight,
                loads.m_z[index] * weight,
            ]
        )
        arm = mesh.nodes[in_strip] - np.array([r, 0.0, 0.0])
        # Rows: sum of f (3 eqs), sum of arm x f (3 eqs). Unknowns: f per node, flattened (x, y, z).
        count = len(in_strip)
        A = np.zeros((6, 3 * count))
        for axis in range(3):
            A[axis, axis::3] = 1.0
        ax, ay, az = arm[:, 0], arm[:, 1], arm[:, 2]
        A[3, 1::3], A[3, 2::3] = -az, ay
        A[4, 0::3], A[4, 2::3] = az, -ax
        A[5, 0::3], A[5, 1::3] = -ay, ax
        solution = A.T @ np.linalg.lstsq(A @ A.T, target, rcond=None)[0]
        forces[in_strip] += solution.reshape(count, 3)
    return forces


def write_input_deck(
    path: Path,
    mesh: TetMesh,
    *,
    E_Pa: float,
    nu: float,
    density_kg_m3: float,
    omega_rad_s: float,
    nodal_forces: np.ndarray | None,
    root_x_m: float,
    nlgeom: bool = True,
    include_centrifugal: bool = True,
    root_tolerance_m: float = 1.0e-7,
    clamp_nodes: np.ndarray | None = None,
) -> Path:
    """Write a CalculiX input deck (one-based node and element numbers)."""

    root_nodes = clamp_nodes if clamp_nodes is not None else np.flatnonzero(mesh.nodes[:, 0] <= root_x_m + root_tolerance_m)
    if len(root_nodes) < 3:
        raise ValueError("No root nodes found to clamp")
    lines = ["*HEADING", "Blade FEA generated by prandtl_prop.structures.calculix (SI units)", "*NODE, NSET=NALL"]
    lines += [f"{i + 1}, {x:.10e}, {y:.10e}, {z:.10e}" for i, (x, y, z) in enumerate(mesh.nodes)]
    lines.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
    lines += [f"{e + 1}, " + ", ".join(str(n + 1) for n in element) for e, element in enumerate(mesh.elements)]
    lines.append("*NSET, NSET=NROOT")
    lines += _chunk(root_nodes + 1)
    lines += [
        "*MATERIAL, NAME=BLADE",
        "*ELASTIC",
        f"{E_Pa:.10e}, {nu:.6f}",
        "*DENSITY",
        f"{density_kg_m3:.10e}",
        "*SOLID SECTION, ELSET=EALL, MATERIAL=BLADE",
        "*BOUNDARY",
        "NROOT, 1, 3, 0.0",
        "*STEP, NLGEOM" if nlgeom else "*STEP",
        "*STATIC",
        "0.25, 1.0, 1e-5, 0.25" if nlgeom else "",
    ]
    if include_centrifugal and omega_rad_s > 0.0:
        lines += ["*DLOAD", f"EALL, CENTRIF, {omega_rad_s**2:.10e}, 0., 0., 0., 0., 0., 1."]
    if nodal_forces is not None:
        loaded = np.flatnonzero(np.any(nodal_forces != 0.0, axis=1))
        if len(loaded):
            lines.append("*CLOAD")
            for node in loaded:
                for dof in range(3):
                    if nodal_forces[node, dof] != 0.0:
                        lines.append(f"{node + 1}, {dof + 1}, {nodal_forces[node, dof]:.10e}")
    lines += [
        "*NODE PRINT, NSET=NALL",
        "U",
        "*NODE PRINT, NSET=NROOT, TOTALS=ONLY",
        "RF",
        "*EL PRINT, ELSET=EALL",
        "S",
        "*NODE FILE",
        "U",
        "*EL FILE",
        "S",
        "*END STEP",
    ]
    path.write_text("\n".join(line for line in lines if line != "") + "\n", encoding="utf-8")
    return path


@dataclass(frozen=True)
class Mode:
    number: int
    frequency_hz: float
    kind: str  # "flap", "lag", or "torsion" from the tip motion
    tip_flap_m: float
    tip_lag_m: float
    tip_twist_rad: float


def write_modal_deck(
    path: Path,
    mesh: TetMesh,
    *,
    E_Pa: float,
    nu: float,
    density_kg_m3: float,
    omega_rad_s: float,
    root_x_m: float,
    modes: int = 6,
    root_tolerance_m: float = 1.0e-7,
    clamp_nodes: np.ndarray | None = None,
) -> Path:
    """Prestressed modal analysis at one rotor speed.

    Step 1 (only when spinning) is a static NLGEOM step under CENTRIF. Step 2 is a *FREQUENCY
    perturbation about that state, so centrifugal stiffening is included, with CENTRIF repeated so
    spin softening is too. Coriolis coupling is neglected (that would need *COMPLEX FREQUENCY).
    """

    root_nodes = clamp_nodes if clamp_nodes is not None else np.flatnonzero(mesh.nodes[:, 0] <= root_x_m + root_tolerance_m)
    tip_nodes = np.flatnonzero(mesh.nodes[:, 0] >= mesh.nodes[:, 0].max() - 1.0e-7)
    lines = ["*HEADING", "Blade modal analysis generated by prandtl_prop.structures.calculix (SI units)", "*NODE, NSET=NALL"]
    lines += [f"{i + 1}, {x:.10e}, {y:.10e}, {z:.10e}" for i, (x, y, z) in enumerate(mesh.nodes)]
    lines.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
    lines += [f"{e + 1}, " + ", ".join(str(n + 1) for n in element) for e, element in enumerate(mesh.elements)]
    lines.append("*NSET, NSET=NROOT")
    lines += _chunk(root_nodes + 1)
    lines.append("*NSET, NSET=NTIP")
    lines += _chunk(tip_nodes + 1)
    lines += ["*MATERIAL, NAME=BLADE", "*ELASTIC", f"{E_Pa:.10e}, {nu:.6f}", "*DENSITY", f"{density_kg_m3:.10e}"]
    lines += ["*SOLID SECTION, ELSET=EALL, MATERIAL=BLADE", "*BOUNDARY", "NROOT, 1, 3, 0.0"]
    centrif = f"EALL, CENTRIF, {omega_rad_s**2:.10e}, 0., 0., 0., 0., 0., 1."
    if omega_rad_s > 0.0:
        lines += ["*STEP, NLGEOM", "*STATIC", "0.25, 1.0, 1e-5, 0.25", "*DLOAD", centrif, "*END STEP", "*STEP, PERTURBATION"]
    else:
        lines.append("*STEP")
    lines += ["*FREQUENCY", f"{modes}"]
    if omega_rad_s > 0.0:
        lines += ["*DLOAD", centrif]
    lines += ["*NODE PRINT, NSET=NTIP", "U", "*END STEP"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_modal(deck: Path, mesh: TetMesh, *, ccx_executable: str | None = None, threads: int = 1, timeout_s: int = 3600) -> list[Mode]:
    """Run a modal deck and classify each mode by its tip motion."""

    dat = _run_ccx(deck, ccx_executable=ccx_executable, threads=threads, timeout_s=timeout_s)
    text = dat.read_text(encoding="utf-8", errors="replace")
    table = text[text.rindex("E I G E N V A L U E   O U T P U T") :].splitlines()
    negative = [line for line in table[1:40] if len(line.split()) == 5 and line.split()[0].isdigit() and float(line.split()[1]) < 0.0]
    if negative:
        raise RuntimeError(
            f"CalculiX returned negative eigenvalues (indefinite stiffness or solver failure) in {dat}: {negative[0].strip()}"
        )
    return parse_modes(dat, mesh)


def _run_ccx(deck: Path, *, ccx_executable: str | None, threads: int, timeout_s: int) -> Path:
    ccx = ccx_executable or shutil.which("ccx")
    if ccx is None:
        raise FileNotFoundError("CalculiX 'ccx' not found on PATH; install CalculiX or set fea.ccx_executable")
    count = str(max(1, threads))
    env = dict(os.environ, OMP_NUM_THREADS=count, CCX_NPROC_EQUATION_SOLVER=count, CCX_NPROC_STIFFNESS=count)
    completed = subprocess.run(
        [ccx, "-i", deck.stem], cwd=deck.parent, capture_output=True, text=True, timeout=timeout_s, check=False, env=env
    )
    log = completed.stdout + completed.stderr
    (deck.parent / f"{deck.stem}.log").write_text(log, encoding="utf-8")
    dat = deck.with_suffix(".dat")
    if completed.returncode != 0 or not dat.is_file() or "*ERROR" in log:
        raise RuntimeError(f"CalculiX failed (exit {completed.returncode}); see {deck.parent / (deck.stem + '.log')}")
    return dat


def parse_modes(dat: Path, mesh: TetMesh) -> list[Mode]:
    """Frequencies from the eigenvalue table and tip-motion classification from the per-mode NTIP prints."""

    text = dat.read_text(encoding="utf-8", errors="replace")
    frequencies: list[float] = []
    table = text[text.rindex("E I G E N V A L U E   O U T P U T") :]
    for line in table.splitlines()[1:]:
        values = line.split()
        if len(values) == 5 and values[0].isdigit():
            frequencies.append(float(values[3]))
        elif frequencies and not line.strip():
            continue
        elif frequencies:
            break
    shapes: list[dict[int, np.ndarray]] = []
    current: dict[int, np.ndarray] | None = None
    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("displacements"):
            current = {}
            shapes.append(current)
            continue
        values = line.split()
        if current is not None and len(values) == 4 and values[0].isdigit():
            current[int(values[0]) - 1] = np.array([float(v) for v in values[1:]])
        elif current is not None and stripped and not stripped[0].isdigit() and not stripped.startswith("-"):
            current = None
    shapes = shapes[-len(frequencies) :] if frequencies else []
    modes: list[Mode] = []
    for index, (frequency, shape) in enumerate(zip(frequencies, shapes, strict=False), start=1):
        nodes = np.array(sorted(shape))
        u = np.array([shape[node] for node in nodes])
        y, z = mesh.nodes[nodes, 1], mesh.nodes[nodes, 2]
        y0, z0 = y.mean(), z.mean()
        flap, lag = float(u[:, 2].mean()), float(u[:, 1].mean())
        dy, dz = y - y0, z - z0
        twist = float(((dy * (u[:, 2] - flap) - dz * (u[:, 1] - lag)).sum()) / max(float((dy**2 + dz**2).sum()), 1.0e-30))
        half_chord = 0.5 * float(np.ptp(y))
        kind = max((("flap", abs(flap)), ("lag", abs(lag)), ("torsion", abs(twist) * half_chord)), key=lambda item: item[1])[0]
        modes.append(Mode(index, frequency, kind, flap, lag, twist))
    return modes


def run_calculix(deck: Path, *, ccx_executable: str | None = None, threads: int = 1, timeout_s: int = 3600) -> CalculixResult:
    """Run ``ccx`` on ``deck`` (``<job>.inp``) in its folder and parse the ``.dat`` results."""

    dat = _run_ccx(deck, ccx_executable=ccx_executable, threads=threads, timeout_s=timeout_s)
    return parse_dat(dat, 0, (deck.parent / f"{deck.stem}.log").read_text(encoding="utf-8"))


def parse_dat(path: Path, returncode: int = 0, log: str = "") -> CalculixResult:
    """Parse displacements, root reaction totals, and integration-point stresses from the last increment."""

    displacement: dict[int, list[float]] = {}
    stress_rows: list[list[float]] = []
    reaction = np.zeros(3)
    block = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered.startswith("displacements"):
            block, displacement = "u", {}
            continue
        if lowered.startswith("stresses"):
            block, stress_rows = "s", []
            continue
        if lowered.startswith("total force"):
            block = "rf"
            continue
        if re.match(r"^[a-z]", lowered):
            block = None
            continue
        values = text.split()
        if block == "u" and len(values) == 4:
            displacement[int(values[0])] = [float(value) for value in values[1:]]
        elif block == "s" and len(values) == 8:
            stress_rows.append([float(value) for value in values])
        elif block == "rf" and len(values) == 3:
            reaction = np.array([float(value) for value in values])
    if not displacement or not stress_rows:
        raise RuntimeError(f"No displacement or stress results found in {path}")
    count = max(displacement)
    u = np.zeros((count, 3))
    for node, values in displacement.items():
        u[node - 1] = values
    s = np.array(stress_rows)
    sxx, syy, szz, sxy, sxz, syz = (s[:, column] for column in range(2, 8))
    mises = np.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2) + 3.0 * (sxy**2 + sxz**2 + syz**2))
    return CalculixResult(u, mises, s[:, 0].astype(int) - 1, reaction, returncode, log)


def tab_clamp_nodes(
    mesh: TetMesh, clamp_outer_radius_m: float, tab_thickness_m: float, z_centre_m: float, tolerance_m: float = 2.0e-4
) -> np.ndarray:
    """Surface nodes on the tab's top and bottom faces inside the hub clamp, i.e. where the hub plates grip."""

    surface = mesh.surface_nodes
    x, z = mesh.nodes[surface, 0], mesh.nodes[surface, 2]
    on_face = np.abs(np.abs(z - z_centre_m) - 0.5 * tab_thickness_m) <= tolerance_m
    return surface[(x <= clamp_outer_radius_m + 1.0e-7) & on_face]


def summarize(
    mesh: TetMesh,
    result: CalculixResult,
    sections: Sequence[BladeSection],
    *,
    allowable_stress_Pa: float,
    clamp_zone_end_m: float | None = None,
) -> tuple[dict[str, float], list[dict[str, float]]]:
    """Headline metrics and a per-station stress table (element centroids binned to station strips)."""

    radii = np.array([section.r_m for section in sections])
    tip_nodes = mesh.nodes[:, 0] >= mesh.nodes[:, 0].max() - 0.25 * sections[-1].chord_m
    root_x = mesh.nodes[:, 0].min()
    centroids_x = mesh.nodes[mesh.elements[:, :4], 0].mean(axis=1)
    ip_x = centroids_x[result.ip_element]
    edges = np.concatenate([[-np.inf], 0.5 * (radii[1:] + radii[:-1]), [np.inf]])
    rows: list[dict[str, float]] = []
    for index, r in enumerate(radii):
        mask = (ip_x >= edges[index]) & (ip_x < edges[index + 1])
        rows.append({"r_m": float(r), "von_mises_max_Pa": float(result.von_mises_ip[mask].max()) if mask.any() else float("nan")})
    # Saint-Venant: clamp artefacts decay within about one chord (or one tab width past a tab clamp).
    clamp_zone = clamp_zone_end_m if clamp_zone_end_m is not None else root_x + sections[0].chord_m
    outside = ip_x > clamp_zone
    max_away = float(result.von_mises_ip[outside].max())
    metrics = {
        "node_count": int(len(mesh.nodes)),
        "mesh_min_quality_sicn": mesh.min_quality,
        "element_count": int(len(mesh.elements)),
        "tip_deflection_x_m": float(result.displacement[tip_nodes, 0].mean()),
        "tip_deflection_y_m": float(result.displacement[tip_nodes, 1].mean()),
        "tip_deflection_z_m": float(result.displacement[tip_nodes, 2].mean()),
        "max_von_mises_Pa": float(result.von_mises_ip.max()),
        "max_von_mises_outside_clamp_zone_Pa": max_away,
        "clamp_zone_end_r_m": float(clamp_zone),
        "margin_of_safety_outside_clamp_zone": allowable_stress_Pa / max_away - 1.0,
        "root_reaction_x_N": float(result.root_reaction_N[0]),
        "root_reaction_y_N": float(result.root_reaction_N[1]),
        "root_reaction_z_N": float(result.root_reaction_N[2]),
    }
    return metrics, rows


def _chunk(values: np.ndarray, per_line: int = 16) -> list[str]:
    return [", ".join(str(int(v)) for v in values[i : i + per_line]) for i in range(0, len(values), per_line)]
