"""CalculiX solid-FEA chain against closed-form cantilever and centrifugal results."""

from __future__ import annotations

import shutil

import numpy as np
import pytest

from prandtl_prop.cad.airfoils import naca4
from prandtl_prop.cad.sections import build_sections
from prandtl_prop.structures.loads import LineLoads, section_plane_properties

pytest.importorskip("gmsh")
pytest.importorskip("cadquery")
pytestmark = pytest.mark.skipif(shutil.which("ccx") is None, reason="CalculiX ccx not installed")

E, NU, RHO = 70.0e9, 0.33, 2700.0
R0, R1, CHORD, COUNT = 0.05, 0.30, 0.04, 11


@pytest.fixture(scope="module")
def prismatic(tmp_path_factory):
    from prandtl_prop.cad import step
    from prandtl_prop.structures import calculix

    r = np.linspace(R0, R1, COUNT)
    sections = build_sections(r, np.full(COUNT, CHORD), np.zeros(COUNT), [naca4("NACA0012", 60)] * COUNT)
    folder = tmp_path_factory.mktemp("fea")
    step.blade_solid(sections).exportBrep(str(folder / "blade.brep"))
    mesh = calculix.mesh_solid(folder / "blade.brep", 0.003)
    weights = np.full(COUNT, r[1] - r[0])
    weights[[0, -1]] *= 0.5
    return folder, r, sections, mesh, weights


def test_node_ordering_matches_c3d10_edge_convention(prismatic) -> None:
    _, _, _, mesh, _ = prismatic
    nodes = mesh.nodes
    edges = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]
    for element in mesh.elements[:100]:
        for slot, (a, b) in enumerate(edges, start=4):
            midpoint = 0.5 * (nodes[element[a]] + nodes[element[b]])
            assert np.linalg.norm(nodes[element[slot]] - midpoint) < 0.25 * np.linalg.norm(nodes[element[a]] - nodes[element[b]])


def test_uniform_flap_load_matches_euler_bernoulli_and_conserves_load(prismatic) -> None:
    from prandtl_prop.structures import calculix

    folder, r, sections, mesh, weights = prismatic
    q, zero = 50.0, np.zeros(COUNT)
    forces = calculix.strip_nodal_forces(
        mesh, sections, LineLoads(r, zero, zero, np.full(COUNT, q), zero, zero, zero), weights, root_x_m=R0
    )
    deck = calculix.write_input_deck(
        folder / "flap.inp", mesh, E_Pa=E, nu=NU, density_kg_m3=RHO, omega_rad_s=0.0, nodal_forces=forces, root_x_m=R0, nlgeom=False
    )
    result = calculix.run_calculix(deck)
    span = R1 - R0
    tip = mesh.nodes[:, 0] > R1 - 1.0e-6
    expected = q * span**4 / (8.0 * E * section_plane_properties(sections[0]).I_ss_m4)
    assert result.root_reaction_N[2] == pytest.approx(-q * span, rel=1.0e-6)  # every applied newton reaches the support
    assert result.displacement[tip, 2].mean() == pytest.approx(expected, rel=3.0e-2)


def test_centrifugal_body_load_reaction_is_exact(prismatic) -> None:
    from prandtl_prop.structures import calculix

    folder, _, sections, mesh, _ = prismatic
    omega = 400.0
    deck = calculix.write_input_deck(
        folder / "spin.inp", mesh, E_Pa=E, nu=NU, density_kg_m3=RHO, omega_rad_s=omega, nodal_forces=None, root_x_m=R0, nlgeom=False
    )
    result = calculix.run_calculix(deck)
    area = section_plane_properties(sections[0]).area_m2
    assert -result.root_reaction_N[0] == pytest.approx(RHO * area * omega**2 * (R1**2 - R0**2) / 2.0, rel=1.0e-3)


def test_strip_distribution_reproduces_force_and_moment(prismatic) -> None:
    from prandtl_prop.structures import calculix

    _, r, sections, mesh, weights = prismatic
    rng = np.random.default_rng(0)
    loads = LineLoads(r, *(rng.normal(size=COUNT) for _ in range(6)))
    forces = calculix.strip_nodal_forces(mesh, sections, loads, weights, root_x_m=R0)
    assert not np.any(forces[mesh.nodes[:, 0] <= R0 + 1.0e-7])
    edges = np.concatenate([[-np.inf], 0.5 * (r[1:] + r[:-1]), [np.inf]])
    for index in range(COUNT):
        strip = (mesh.nodes[:, 0] >= edges[index]) & (mesh.nodes[:, 0] < edges[index + 1])
        arm = mesh.nodes[strip] - np.array([r[index], 0.0, 0.0])
        force = forces[strip].sum(axis=0)
        moment = np.cross(arm, forces[strip]).sum(axis=0)
        expected = np.array([getattr(loads, name)[index] for name in ("f_x", "f_y", "f_z", "m_x", "m_y", "m_z")]) * weights[index]
        np.testing.assert_allclose(np.concatenate([force, moment]), expected, atol=1.0e-9)
