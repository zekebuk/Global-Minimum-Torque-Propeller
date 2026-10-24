"""Geometry, section-property, load-mapping, and beam-screen checks against closed-form results."""

from __future__ import annotations

import math

import numpy as np
import pytest

from prandtl_prop.cad.airfoils import cosine_stations, load_airfoil, naca4, read_airfoil_file
from prandtl_prop.cad.sections import build_sections, polygon_properties
from prandtl_prop.structures.beam import solve_beam
from prandtl_prop.structures.loads import LineLoads, map_station_loads, section_plane_properties

E, NU, RHO = 70.0e9, 0.33, 2700.0


def _prismatic(count: int = 201, beta_deg: float = 0.0, code: str = "NACA0012", chord: float = 0.04):
    r = np.linspace(0.05, 0.30, count)
    return r, build_sections(r, np.full(count, chord), np.full(count, beta_deg), [naca4(code, 80)] * count, 0.25)


def _uniform_loads(r: np.ndarray, f_z: float = 0.0, f_y: float = 0.0) -> LineLoads:
    zero = np.zeros_like(r)
    return LineLoads(r, zero, np.full_like(r, f_y), np.full_like(r, f_z), zero, zero, zero)


def test_polygon_properties_of_rectangle() -> None:
    props = polygon_properties(np.array([[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 1.0]]))
    assert (props.area_m2, props.centroid_s_m, props.centroid_t_m) == pytest.approx((2.0, 1.0, 0.5))
    assert (props.I_ss_m4, props.I_tt_m4, props.I_st_m4) == pytest.approx((2.0 / 12.0, 8.0 / 12.0, 0.0), abs=1.0e-12)


def test_naca0012_section_area_matches_known_value() -> None:
    """Polygon area converges to the integral of the closed-TE NACA 0012 thickness law."""

    x = np.linspace(0.0, 1.0, 200001)
    half = 0.6 * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1036 * x**4)
    props = polygon_properties(naca4("NACA0012", 200).loop())
    assert props.area_m2 == pytest.approx(2.0 * np.trapezoid(half, x), rel=1.0e-4)
    assert props.centroid_t_m == pytest.approx(0.0, abs=1.0e-12)


def test_selig_and_yz_files_load_identically(tmp_path) -> None:
    reference = naca4("NACA4412", 100)
    loop = reference.loop()
    (tmp_path / "MH115.dat").write_text("MH 115\n" + "\n".join(f"{x:.8f} {y:.8f}" for x, y in loop), encoding="utf-8")
    (tmp_path / "S4310_YZ.txt").write_text("\n".join(f"{i} {y:.8f} {x:.8f}" for i, (x, y) in enumerate(loop)), encoding="utf-8")
    dat = load_airfoil("mh115", tmp_path, 60)
    yz = load_airfoil("S4310", tmp_path, 60)
    np.testing.assert_allclose(dat.upper, yz.upper, atol=1.0e-7)
    np.testing.assert_allclose(dat.upper, np.interp(cosine_stations(60), reference.x, reference.upper), atol=2.0e-4)
    assert read_airfoil_file(tmp_path / "MH115.dat", "MH115").max_thickness == pytest.approx(0.12, rel=1.0e-2)


def test_missing_airfoil_requires_an_explicit_substitute(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="substitutes"):
        load_airfoil("A3", tmp_path)
    substitute = load_airfoil("A3", tmp_path, substitutes={"A3": "NACA4410"})
    assert substitute.source.startswith("SUBSTITUTE for A3")


def test_section_frame_places_leading_edge_forward_and_up() -> None:
    (section,) = build_sections([0.2], [0.04], [10.0], [naca4("NACA0012", 60)], 0.25)
    points = section.points_3d()
    leading_edge = points[59]  # loop order is TE upper -> LE -> TE lower
    trailing_edge = points[0]
    assert leading_edge[1] > 0.0 and leading_edge[2] > trailing_edge[2]
    assert math.degrees(math.atan2(leading_edge[2] - trailing_edge[2], leading_edge[1] - trailing_edge[1])) == pytest.approx(10.0, abs=0.2)
    assert np.allclose(points[:, 0], 0.2)


@pytest.mark.parametrize("beta_deg", [0.0, 35.0, 90.0])
def test_cantilever_uniform_load_matches_closed_form(beta_deg: float) -> None:
    """Uniform flap load q: w_tip = q L^4 / (8 E I), root moment q L^2 / 2, in any section orientation."""

    r, sections = _prismatic(beta_deg=beta_deg, code="NACA0012")
    q = 50.0
    result = solve_beam(sections, _uniform_loads(r, f_z=q), E_Pa=E, nu=NU, density_kg_m3=RHO, allowable_stress_Pa=1.0e9)
    span = r[-1] - r[0]
    props = section_plane_properties(sections[0])
    assert result.metrics["root_flap_moment_Nm"] == pytest.approx(-q * span**2 / 2.0, rel=1.0e-9)
    if beta_deg in (0.0, 90.0):  # principal axes coincide with y, z: deflection stays in z
        assert result.metrics["tip_deflection_z_m"] == pytest.approx(q * span**4 / (8.0 * E * props.I_ss_m4), rel=1.0e-4)
        assert result.metrics["tip_deflection_y_m"] == pytest.approx(0.0, abs=1.0e-12)
    else:  # unsymmetric bending couples flap load into in-plane deflection
        assert abs(result.metrics["tip_deflection_y_m"]) > 0.05 * abs(result.metrics["tip_deflection_z_m"])
    thickness_half = 0.06 * 0.04
    if beta_deg == 0.0:
        assert max(result.rows[0]["sigma_axial_max_Pa"], -result.rows[0]["sigma_axial_min_Pa"]) == pytest.approx(
            q * span**2 / 2.0 * thickness_half / props.I_ss_m4, rel=2.0e-2
        )


def test_centrifugal_root_tension_matches_closed_form() -> None:
    r, sections = _prismatic()
    rows = [{"r_m": value, "qz_N_per_m": 0.0, "qy_tangential_N_per_m": 0.0, "Vrel_m_s": 1.0, "Cm": 0.0} for value in r]
    omega = 400.0
    loads = map_station_loads(rows, sections, blade_count=2, omega_rad_s=omega, rho_air_kg_m3=1.225, material_density_kg_m3=RHO)
    result = solve_beam(sections, loads, E_Pa=E, nu=NU, density_kg_m3=RHO, allowable_stress_Pa=1.0e9)
    area = section_plane_properties(sections[0]).area_m2
    expected = RHO * area * omega**2 * (r[-1] ** 2 - r[0] ** 2) / 2.0
    assert result.metrics["root_centrifugal_force_N"] == pytest.approx(expected, rel=1.0e-5)
    assert result.metrics["blade_mass_kg"] == pytest.approx(RHO * area * (r[-1] - r[0]), rel=1.0e-9)


def test_aero_loads_split_per_blade_and_conserve_thrust() -> None:
    r, sections = _prismatic(count=11)
    rows = [{"r_m": value, "qz_N_per_m": 100.0, "qy_tangential_N_per_m": 8.0, "Vrel_m_s": 100.0, "Cm": ""} for value in r]
    loads = map_station_loads(rows, sections, blade_count=2, omega_rad_s=0.0, rho_air_kg_m3=1.225, material_density_kg_m3=RHO)
    assert np.allclose(loads.f_z, 50.0) and np.allclose(loads.f_y, -4.0)
    with pytest.raises(ValueError, match="does not match"):
        map_station_loads(rows[::-1], sections, blade_count=2, omega_rad_s=0.0, rho_air_kg_m3=1.225, material_density_kg_m3=RHO)


def test_loft_volume_matches_section_quadrature() -> None:
    step = pytest.importorskip("prandtl_prop.cad.step")
    pytest.importorskip("cadquery")
    r = np.linspace(0.06, 0.28, 12)
    chord = 0.055 - 0.1 * (r - 0.06)
    beta = 25.0 - 50.0 * (r - 0.06)
    sections = build_sections(r, chord, beta, [naca4("NACA4412", 60)] * len(r))
    report = step.report(step.blade_solid(sections))
    areas = np.array([section.properties().area_m2 for section in sections])
    assert report.is_valid
    assert report.volume_m3 == pytest.approx(np.trapezoid(areas, r), rel=5.0e-3)
    rotor = step.report(step.rotor_solid(step.blade_solid(sections), 3))
    assert rotor.volume_m3 == pytest.approx(3.0 * report.volume_m3, rel=1.0e-6)


def test_rotating_beam_modes_match_theory_and_calculix_benchmarks() -> None:
    """Prismatic cantilever: f1, f2, lag exact to beam theory; Southwell K_flap ~1.49 and spin softening K_flap - K_lag ~1
    (CalculiX gave 1.49 / 0.50 on the same section)."""

    from prandtl_prop.structures.modal_beam import excitation_margin, rotating_modes

    r, sections = _prismatic(count=21)
    props = section_plane_properties(sections[0])
    span = r[-1] - r[0]
    f1 = 1.8751**2 / (2.0 * np.pi) * np.sqrt(E * props.I_ss_m4 / (RHO * props.area_m2 * span**4))
    still = rotating_modes(sections, E_Pa=E, density_kg_m3=RHO, omega_rad_s=0.0, count=3)
    spinning = rotating_modes(sections, E_Pa=E, density_kg_m3=RHO, omega_rad_s=4000.0 * np.pi / 30.0, count=3)
    assert [m.kind for m in still] == ["flap", "flap", "lag"]
    assert still[0].frequency_hz == pytest.approx(f1, rel=1.0e-3)
    assert still[1].frequency_hz == pytest.approx(f1 * (4.6941 / 1.8751) ** 2, rel=2.0e-3)
    n = 4000.0 / 60.0
    k_flap = (spinning[0].frequency_hz ** 2 - still[0].frequency_hz ** 2) / n**2
    k_lag = (spinning[2].frequency_hz ** 2 - still[2].frequency_hz ** 2) / n**2
    assert k_flap == pytest.approx(1.49, abs=0.03)
    assert k_flap - k_lag == pytest.approx(1.0, abs=0.05)
    assert 0.0 < excitation_margin([spinning], [4000.0]) < 1.0


def test_outline_arrays_match_section_arrays() -> None:
    from prandtl_prop.structures.modal_beam import concat_arrays, outline_arrays, section_arrays

    r, sections = _prismatic(count=5, beta_deg=12.0, code="NACA4412")
    from_sections = section_arrays(sections)
    from_outlines = outline_arrays([section.points_3d() for section in sections])
    for key, values in from_sections.items():
        np.testing.assert_allclose(from_outlines[key], values, rtol=1e-12, atol=0.0)
    assert len(concat_arrays(from_sections, from_outlines)["r"]) == 10
