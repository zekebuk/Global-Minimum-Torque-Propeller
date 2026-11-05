"""CFD -> FEA load transfer: surface extraction, consistent nodal loads, equilibrium, FRD reading."""

from __future__ import annotations

import numpy as np
import pytest

from prandtl_prop.structures.cfd_coupling import map_wall_loads, read_frd_nodal, surface_faces, von_mises


def _box_mesh(tmp_path):
    pytest.importorskip("cadquery")
    pytest.importorskip("gmsh")
    import cadquery as cq

    from prandtl_prop.structures.calculix import mesh_solid

    box = cq.Solid.makeBox(0.04, 0.02, 0.01, cq.Vector(0.01, -0.01, -0.005))
    path = tmp_path / "box.brep"
    box.exportBrep(str(path))
    return mesh_solid(path, 0.004)


def _cfd_faces_on(mesh):
    """Synthetic 'CFD' wall faces: the FE surface itself, area vectors pointing into the solid."""

    corners, _ = surface_faces(mesh.elements, mesh.nodes)
    p0, p1, p2 = (mesh.nodes[corners[:, k]] for k in range(3))
    outward = 0.5 * np.cross(p1 - p0, p2 - p0)
    return (p0 + p1 + p2) / 3.0, -outward


def test_surface_faces_are_outward(tmp_path) -> None:
    mesh = _box_mesh(tmp_path)
    corners, mids = surface_faces(mesh.elements, mesh.nodes)
    p0, p1, p2 = (mesh.nodes[corners[:, k]] for k in range(3))
    area = 0.5 * np.cross(p1 - p0, p2 - p0)
    centroid = (p0 + p1 + p2) / 3.0
    assert np.linalg.norm(area.sum(axis=0)) < 1e-12  # closed surface
    assert (centroid * area).sum() == pytest.approx(3.0 * 0.04 * 0.02 * 0.01, rel=1e-6)  # divergence theorem, outward
    # Each mid node sits between the corners it is listed with.
    midpoint = 0.5 * (mesh.nodes[corners[:, 0]] + mesh.nodes[corners[:, 1]])
    assert np.allclose(mesh.nodes[mids[:, 0]], midpoint, atol=1e-6)


def test_uniform_pressure_has_no_net_force_and_bottom_pressure_lifts(tmp_path) -> None:
    mesh = _box_mesh(tmp_path)
    centres, into = _cfd_faces_on(mesh)
    unit = into / np.linalg.norm(into, axis=1)[:, None]
    area = np.linalg.norm(into, axis=1)
    p = 1000.0
    uniform, diag = map_wall_loads(mesh.nodes, mesh.elements, centres, into, p * unit * area[:, None], np.zeros_like(into))
    assert np.abs(uniform.sum(axis=0)).max() < 1e-9 * p * area.sum()
    bottom = centres[:, 2] < -0.005 + 1e-9
    force = np.where(bottom[:, None], p * unit * area[:, None], 0.0)
    lifted, diag = map_wall_loads(mesh.nodes, mesh.elements, centres, into, force, np.zeros_like(into))
    assert lifted[:, 2].sum() == pytest.approx(p * 0.04 * 0.02, rel=1e-9)  # pushes up on the bottom face
    assert diag["mapped_over_cfd_force_z"] == pytest.approx(1.0, rel=1e-9)
    corner_nodes = np.unique(surface_faces(mesh.elements, mesh.nodes)[0])
    assert np.abs(lifted[corner_nodes]).max() == 0.0  # consistent loads: all on mid-side nodes


def test_frd_reader_takes_the_last_increment(tmp_path) -> None:
    text = "\n".join(
        [
            "    1C",
            " -4  DISP        4    1",
            " -1         1 1.00000E-03 0.00000E+00 0.00000E+00",
            " -3",
            " -4  DISP        4    1",
            " -1         1 2.00000E-03 0.00000E+00-1.00000E-04",
            " -3",
            " -4  STRESS      6    1",
            " -1         1 1.00000E+06 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00",
            " -3",
        ]
    )
    path = tmp_path / "x.frd"
    path.write_text(text)
    data = read_frd_nodal(path)
    assert data["DISP"][0].tolist() == pytest.approx([2e-3, 0.0, -1e-4])
    assert von_mises(data["STRESS"])[0] == pytest.approx(1e6)
