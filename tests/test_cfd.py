"""CFD post-processing and BEMT calibration checks."""

from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

from prandtl_prop import config, geometry
from prandtl_prop.aero import AnalyticPolar, BladeStations, OperatingPoint, solve_rotor
from prandtl_prop.cfd import openfoam
from prandtl_prop.cfd.calibration import calibrate, read_corrections, write_corrections

OP = OperatingPoint(config.DESIGN_RADIUS_M, config.DESIGN_BLADE_COUNT, config.DESIGN_RPM)
POLAR = AnalyticPolar(cl_alpha_per_rad=5.8, alpha0_deg=-2.5, cd0=0.011, k=0.006)


def _stations() -> BladeStations:
    return BladeStations.from_rows(geometry.load_case_geometry("GMT"), OP.radius_m)


def test_calibration_recovers_known_station_factors() -> None:
    """Loads made by BEMT with known factors must calibrate back to those factors (identifiability)."""

    stations = _stations()
    x = stations.r_m / OP.radius_m
    true_lift, true_drag = 1.0 - 0.25 * x**4, 1.3 + 0.5 * x
    reference = solve_rotor(stations, OP, POLAR, collective_offset_deg=-1.0, corrections=(true_lift, true_drag)).station_rows()
    dT = np.array([row["dT_N"] for row in reference])
    dQ = np.array([row["dQ_Nm"] for row in reference])
    result = calibrate(stations, OP, POLAR, dT, dQ, collective_offset_deg=-1.0, degree=None)
    assert result.max_load_mismatch < 1.0e-6
    np.testing.assert_allclose(result.lift_factor, true_lift, atol=1.0e-5)
    np.testing.assert_allclose(result.drag_factor, true_drag, atol=1.0e-5)


def test_regularised_calibration_recovers_smooth_factors_and_matches_noisy_totals() -> None:
    """Cubic truth is recovered exactly by the default cubic fit; with noisy loads the totals still match exactly."""

    stations = _stations()
    x = stations.r_m / OP.radius_m
    true_lift, true_drag = 0.85 + 0.2 * x - 0.15 * x**3, 1.4 - 0.3 * x
    rows = solve_rotor(stations, OP, POLAR, corrections=(true_lift, true_drag)).station_rows()
    dT, dQ = np.array([r["dT_N"] for r in rows]), np.array([r["dQ_Nm"] for r in rows])
    exact = calibrate(stations, OP, POLAR, dT, dQ, collective_offset_deg=0.0)
    np.testing.assert_allclose(exact.lift_factor, true_lift, atol=1.0e-5)
    np.testing.assert_allclose(exact.drag_factor, true_drag, atol=1.0e-5)

    noise = np.random.default_rng(1).normal(1.0, 0.15, size=(2, len(dT)))
    noisy = calibrate(stations, OP, POLAR, dT * noise[0], dQ * noise[1], collective_offset_deg=0.0)
    assert abs(noisy.total_thrust_error_fraction) < 1.0e-8 and abs(noisy.total_torque_error_fraction) < 1.0e-8
    assert np.max(np.abs(np.diff(noisy.lift_factor, 2))) < 0.05  # smooth despite 15% station noise


def test_uncorrected_loads_calibrate_to_unity() -> None:
    stations = _stations()
    rows = solve_rotor(stations, OP, POLAR).station_rows()
    result = calibrate(
        stations, OP, POLAR, np.array([r["dT_N"] for r in rows]), np.array([r["dQ_Nm"] for r in rows]), collective_offset_deg=0.0
    )
    np.testing.assert_allclose(result.lift_factor, 1.0, atol=1.0e-9)
    np.testing.assert_allclose(result.drag_factor, 1.0, atol=1.0e-9)


def test_corrections_file_round_trips_with_interpolation(tmp_path) -> None:
    stations = _stations()
    x = stations.r_m / OP.radius_m
    rows = solve_rotor(stations, OP, POLAR).station_rows()
    result = calibrate(
        stations, OP, POLAR, 0.9 * np.array([r["dT_N"] for r in rows]), np.array([r["dQ_Nm"] for r in rows]), collective_offset_deg=0.0
    )
    path = write_corrections(tmp_path / "c.csv", stations, OP.radius_m, result, "test")
    lift, drag = read_corrections(path, x)
    np.testing.assert_allclose(lift, result.lift_factor, rtol=1.0e-9)
    lift_mid, _ = read_corrections(path, 0.5 * (x[:-1] + x[1:]))
    assert np.all((lift_mid >= np.minimum(lift[:-1], lift[1:]) - 1e-12) & (lift_mid <= np.maximum(lift[:-1], lift[1:]) + 1e-12))


def test_field_parser_reads_uniform_nonuniform_and_inline_lists(tmp_path) -> None:
    header = "FoamFile { version 2.0; format ascii; class volVectorField; object U; }\n"
    vectors = "\n".join(f"({i} {2 * i} {3 * i})" for i in range(12))
    (tmp_path / "U").write_text(
        header
        + f"internalField nonuniform List<vector> \n12\n(\n{vectors}\n)\n;\n"
        + "boundaryField\n{\n    wall\n    {\n        type fixedValue;\n        value nonuniform List<vector> 2((1 0 0) (0 1 0));\n    }\n"
        + "    outlet\n    {\n        type zeroGradient;\n    }\n}\n",
        encoding="utf-8",
    )
    field = openfoam.read_field(tmp_path / "U", 3)
    assert field.internal.shape == (12, 3) and field.internal[11].tolist() == [11, 22, 33]
    assert field.boundary["wall"].tolist() == [[1, 0, 0], [0, 1, 0]]
    assert field.boundary["outlet"] is None


@pytest.mark.skipif(
    shutil.which("blockMesh") is None and not __import__("pathlib").Path("/usr/share/openfoam/etc/bashrc").exists(),
    reason="OpenFOAM not installed",
)
def test_mesh_reader_and_momentum_balance_on_a_blockmesh_cube(tmp_path) -> None:
    """A closed cube's outward area vectors sum to zero, and uniform flow carries no net momentum."""

    case = tmp_path / "cube"
    (case / "system").mkdir(parents=True)
    (case / "system" / "controlDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
        "application blockMesh; startFrom startTime; startTime 0; stopAt endTime; endTime 1; deltaT 1; writeControl timeStep; writeInterval 1;\n",
        encoding="utf-8",
    )
    (case / "system" / "blockMeshDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object blockMeshDict; }\n"
        "convertToMeters 1; vertices ((0 0 0) (2 0 0) (2 1 0) (0 1 0) (0 0 3) (2 0 3) (2 1 3) (0 1 3));\n"
        "blocks (hex (0 1 2 3 4 5 6 7) (4 3 5) simpleGrading (1 1 1)); edges ();\n"
        "boundary ( farfield { type patch; faces ((0 3 2 1) (4 5 6 7) (0 1 5 4) (1 2 6 5) (2 3 7 6) (3 0 4 7)); } );\n",
        encoding="utf-8",
    )
    script = f"source {openfoam._default_bashrc()} >/dev/null 2>&1 || true; cd '{case}' && blockMesh"
    subprocess.run(["bash", "-c", script], check=True, capture_output=True)
    mesh = openfoam.read_mesh(case)
    centres, areas = openfoam.face_geometry(mesh, mesh.patch_faces("farfield"))
    assert np.abs(areas.sum(axis=0)).max() < 1.0e-12
    assert np.linalg.norm(areas, axis=1).sum() == pytest.approx(2 * (2 * 1 + 1 * 3 + 2 * 3))
    assert np.all(((centres - np.array([1.0, 0.5, 1.5])) * areas).sum(axis=1) > 0.0)  # outward
    time_dir = case / "1"
    time_dir.mkdir()
    count = mesh.patches["farfield"][1]
    cells = int(mesh.owner.max()) + 1
    for name, cls, internal, value in (
        ("U", "volVectorField", "uniform (0 0 -5)", f"nonuniform List<vector> {count}(" + " ".join(["(0 0 -5)"] * count) + ")"),
        ("p", "volScalarField", "uniform 0", f"nonuniform List<scalar> {count}(" + " ".join(["0"] * count) + ")"),
    ):
        (time_dir / name).write_text(
            f"FoamFile {{ version 2.0; format ascii; class {cls}; object {name}; }}\ninternalField {internal};\n"
            f"boundaryField\n{{\n    farfield\n    {{\n        type fixedValue;\n        value {value};\n    }}\n}}\n",
            encoding="utf-8",
        )
    balance = openfoam.farfield_thrust(case, time_dir, rho_kg_m3=1.2, mesh=mesh)
    assert cells == 60
    assert balance["net_volume_flux_m3_s"] == pytest.approx(0.0, abs=1.0e-9)
    assert balance["thrust_N"] == pytest.approx(0.0, abs=1.0e-9)
    assert balance["outflow_m3_s"] == pytest.approx(5.0 * 2.0)


def test_processor_grid_factorises() -> None:
    for count in (1, 2, 4, 6, 8, 12, 7):
        grid = openfoam._processor_grid(count)
        assert grid[0] * grid[1] * grid[2] == count
