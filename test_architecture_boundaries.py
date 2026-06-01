from __future__ import annotations

import ast
import csv
import hashlib
import json
from pathlib import Path

import pytest

import aero_solver


CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CODE_DIR.parent


def _imports_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def _write_optimizer_station_geometry(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "station": 1,
            "model_source": "optimizer_reduced_order",
            "generating_script": "Geometry_optimization.py",
            "mu": 0.25,
            "radius_m": 0.05,
            "dr_m": 0.01,
            "chord_m": 0.02,
            "twist_deg": 12.0,
            "alpha_deg": 4.0,
            "cl": 0.4,
            "cd": 0.02,
            "dthrust_n": 0.7,
            "dtorque_nm": 0.01,
            "airfoil": "A3",
            "hit_cl_limit": "False",
        },
        {
            "station": 2,
            "model_source": "optimizer_reduced_order",
            "generating_script": "Geometry_optimization.py",
            "mu": 0.75,
            "radius_m": 0.15,
            "dr_m": 0.01,
            "chord_m": 0.018,
            "twist_deg": 8.0,
            "alpha_deg": 3.0,
            "cl": 0.3,
            "cd": 0.018,
            "dthrust_n": 0.8,
            "dtorque_nm": 0.012,
            "airfoil": "A4",
            "hit_cl_limit": "False",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_geometry_optimizer_does_not_import_aero_solver() -> None:
    imports = _imports_for(CODE_DIR / "Geometry_optimization.py")

    assert "aero_solver" not in imports


def test_aero_layers_do_not_import_geometry_optimizer() -> None:
    assert "Geometry_optimization" not in _imports_for(CODE_DIR / "aero_solver.py")
    assert "Geometry_optimization" not in _imports_for(CODE_DIR / "aero_solver_ui.py")


def test_geometry_optimizer_has_no_aero_output_source_references() -> None:
    source = (CODE_DIR / "Geometry_optimization.py").read_text(encoding="utf-8")

    forbidden = [
        "results/aero",
        "results\\aero",
        "figures_paper/aero",
        "figures_paper\\aero",
        "results/aero_postprocess",
        "figures_paper/aero_postprocess",
        "bemt_xfoil",
    ]
    for token in forbidden:
        assert token not in source


def test_aero_solver_rejects_project_geometry_output_dir() -> None:
    with pytest.raises(ValueError, match="aero output path"):
        aero_solver.export_results(
            {
                "case_name": "bad_boundary",
                "summary": {"model_source": aero_solver.MODEL_SOURCE_IMPORTED_CSV},
                "geometry_source_csv": "",
                "operating_condition": {},
                "polar_source": "",
                "polar_is_placeholder": False,
                "warnings": [],
                "sanity_checks": {},
                "stations": [],
            },
            PROJECT_ROOT / "results" / "geometry_optimizer" / "bad_aero_export",
            "bad_boundary",
        )


def test_aero_run_does_not_overwrite_optimizer_geometry_csv(tmp_path: Path) -> None:
    geometry_csv = _write_optimizer_station_geometry(
        tmp_path / "results" / "geometry_optimizer" / "optimized_gmt" / "optimized_gmt_optimizer_geometry.csv"
    )
    before_hash = _sha256(geometry_csv)

    config = aero_solver.get_default_aero_config()
    config.update(
        {
            "case_name": "optimized_gmt",
            "case_type": aero_solver.GEOMETRY_CSV_CASE_TYPE,
            "geometry_csv_path": str(geometry_csv),
            "output_dir": str(tmp_path / "results" / "aero_postprocess" / "optimizer_station_data" / "optimized_gmt"),
            "figure_output_dir": str(
                tmp_path / "results" / "plots" / "aero_postprocess" / "optimizer_station_data" / "optimized_gmt"
            ),
            "diameter_m": 0.4,
            "radius_m": 0.2,
            "target_thrust_N": 1.5,
            "match_target": False,
            "prefer_imported_station_aero": True,
            "generate_plots": False,
        }
    )

    results = aero_solver.run_aero_from_config(config)

    assert _sha256(geometry_csv) == before_hash
    assert results["summary"]["geometry_source_csv"] == str(geometry_csv.resolve())
    assert results["summary"]["model_source"] == aero_solver.MODEL_SOURCE_IMPORTED_CSV
    assert results["summary"]["generating_script"] == "aero_solver.py"

    station_csv = (
        tmp_path
        / "results"
        / "aero_postprocess"
        / "optimizer_station_data"
        / "optimized_gmt"
        / "optimized_gmt_optimizer_station_data_stations.csv"
    )
    summary_json = (
        tmp_path / "results" / "aero_postprocess" / "optimizer_station_data" / "optimized_gmt" / "optimized_gmt_summary.json"
    )
    assert station_csv.exists()
    assert summary_json.exists()
    assert station_csv.resolve() != geometry_csv.resolve()

    with station_csv.open(newline="", encoding="utf-8") as handle:
        station_rows = list(csv.DictReader(handle))
    assert station_rows[0]["generating_script"] == "aero_solver.py"
    assert station_rows[0]["source_geometry_csv"] == str(geometry_csv.resolve())

    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    assert summary["station_source_csv"].endswith("optimized_gmt_optimizer_station_data_stations.csv")
