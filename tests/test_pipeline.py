"""Design-file validation, content-addressed caching, and the end-to-end pipeline."""

from __future__ import annotations

import json

import pytest

from prandtl_prop import config
from prandtl_prop.cli import main as cli_main
from prandtl_prop.design import design_from_dict, load_design, read_override_file
from prandtl_prop.pipeline import run_design
from prandtl_prop.runs import RunStore, stage_key

SMOKE_OVERRIDES = read_override_file(config.DESIGNS_DIR / "smoke_overrides.txt")


def _smoke(name: str = "gmt", extra: list[str] | None = None):
    return load_design(config.DESIGNS_DIR / f"{name}.yaml", SMOKE_OVERRIDES + (extra or []))


def test_design_files_load_and_match_thesis_constants() -> None:
    for name in ("mil", "gmt"):
        design = load_design(config.DESIGNS_DIR / f"{name}.yaml")
        assert design.operating_point.radius_m == pytest.approx(config.DESIGN_RADIUS_M)
        assert design.structure.material.E_Pa == 70.0e9  # written as 70.0e9, which YAML 1.1 reads as a string


def test_design_rejects_unknown_keys_and_bad_values() -> None:
    base = {"name": "x", "geometry": {"stations_csv": "a.csv"}}
    with pytest.raises(ValueError, match="Unknown key"):
        design_from_dict({**base, "aero": {"tip_los": False}})
    with pytest.raises(ValueError, match="must be a number"):
        design_from_dict({**base, "operating_point": {"rpm": "fast"}})
    with pytest.raises(ValueError, match="integer"):
        design_from_dict({**base, "operating_point": {"blade_count": 2.5}})
    with pytest.raises(ValueError, match="name"):
        design_from_dict({**base, "name": "bad name"})


def test_stage_key_tracks_inputs(tmp_path) -> None:
    data = tmp_path / "input.csv"
    data.write_text("a\n1\n", encoding="utf-8")
    key = stage_key(stage="aero", version="1", design_inputs={"rpm": 4000}, input_files=[data])
    assert key == stage_key(stage="aero", version="1", design_inputs={"rpm": 4000}, input_files=[data])
    assert key != stage_key(stage="aero", version="1", design_inputs={"rpm": 4001}, input_files=[data])
    assert key != stage_key(stage="aero", version="2", design_inputs={"rpm": 4000}, input_files=[data])
    assert key != stage_key(stage="aero", version="1", design_inputs={"rpm": 4000}, input_files=[data], upstream={"x": "y"})
    data.write_text("a\n2\n", encoding="utf-8")
    assert key != stage_key(stage="aero", version="1", design_inputs={"rpm": 4000}, input_files=[data])


def test_pipeline_caches_and_invalidates_only_downstream_of_a_change(tmp_path) -> None:
    store = RunStore(tmp_path)
    first = run_design(_smoke(), ["beam"], store=store)
    assert list(first) == ["aero", "sections", "beam"] and not any(outcome.cached for outcome in first.values())
    assert first["aero"].run.metrics["thrust_N"] == pytest.approx(35.0, abs=1.0e-8)
    assert first["aero"].run.metrics["nonconverged_station_count"] == 0
    assert first["beam"].run.metrics["margin_of_safety"] > 0.0

    again = run_design(_smoke(), ["beam"], store=store)
    assert all(outcome.cached for outcome in again.values())

    stiffer = run_design(_smoke(extra=["structure.material.E_Pa=140.0e+9"]), ["beam"], store=store)
    assert stiffer["aero"].cached and stiffer["sections"].cached and not stiffer["beam"].cached
    assert stiffer["beam"].run.metrics["tip_deflection_z_m"] == pytest.approx(
        first["beam"].run.metrics["tip_deflection_z_m"] / 2.0, rel=1.0e-9
    )

    faster = run_design(_smoke(extra=["operating_point.rpm=4200"]), ["beam"], store=store)
    assert not any(outcome.cached for outcome in faster.values())

    manifest = json.loads((first["beam"].run.final_directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete" and manifest["inputs"]["upstream"]["aero"] == first["aero"].run.key
    assert store.latest("gmt", "beam").key == faster["beam"].run.key


def test_cad_stage_exports_valid_step(tmp_path) -> None:
    pytest.importorskip("cadquery")
    outcomes = run_design(_smoke(extra=["cad.export_stl=false", "cad.export_rotor=false"]), ["cad"], store=RunStore(tmp_path))
    cad = outcomes["cad"].run
    assert cad.metrics["blade_solid_valid"] is True
    assert cad.metrics["loft_vs_section_quadrature_volume_ratio"] == pytest.approx(1.0, abs=2.0e-3)
    step_text = cad.output("blade.step").read_text(encoding="utf-8", errors="replace")
    assert step_text.startswith("ISO-10303-21") and "SI_UNIT(.MILLI.,.METRE.)" in step_text


def test_step_is_true_size_in_millimetres_for_solidworks(tmp_path) -> None:
    """Regression: the STEP header says mm, so coordinates must be mm (they were metres, 1000x too small)."""

    pytest.importorskip("cadquery")
    from prandtl_prop.cad import step

    outcomes = run_design(_smoke(extra=["cad.export_stl=false", "cad.export_rotor=false"]), ["cad"], store=RunStore(tmp_path))
    cad = outcomes["cad"].run
    imported = step.import_step_m(cad.output("blade.step"))
    assert step.report(imported).volume_m3 == pytest.approx(cad.metrics["blade_volume_m3"], rel=1.0e-6)
    box = imported.BoundingBox()
    assert box.xmax == pytest.approx(config.DESIGN_RADIUS_M * 0.9997, rel=1.0e-4)
    curves = sorted((cad.final_directory / "solidworks_curves").glob("section_*.sldcrv"))
    assert len(curves) == 37
    first = [float(value) for value in curves[0].read_text(encoding="utf-8").splitlines()[0].split()]
    assert first[0] == pytest.approx(0.2 * config.DESIGN_RADIUS_M * 1000.0, rel=1.0e-6)  # mm


def test_missing_polars_fail_with_instructions(tmp_path, capsys) -> None:
    code = cli_main(
        [
            "run",
            str(config.DESIGNS_DIR / "gmt.yaml"),
            "--stages",
            "aero",
            "--runs-dir",
            str(tmp_path),
            "--set",
            f"aero.polar.directory={tmp_path}",
        ]
    )
    assert code == 1
    assert "Copy the XFOIL polars" in capsys.readouterr().err


def test_cli_compare_reports_both_designs(tmp_path, capsys) -> None:
    smoke = str(config.DESIGNS_DIR / "smoke_overrides.txt")
    designs = [str(config.DESIGNS_DIR / "mil.yaml"), str(config.DESIGNS_DIR / "gmt.yaml")]
    assert cli_main(["compare", *designs, "--stages", "aero", "--set-file", smoke, "--runs-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "aero.torque_Nm" in output and "delta vs mil" in output


def test_every_stage_is_fully_registered() -> None:
    from prandtl_prop import pipeline

    for registry in (pipeline.STAGE_UPSTREAM, pipeline.STAGE_FUNCTIONS, pipeline.STAGE_MODULES, pipeline.STAGE_DESIGN_INPUTS):
        assert set(registry) == set(pipeline.STAGES)
    for stage, upstream in pipeline.STAGE_UPSTREAM.items():
        assert all(pipeline.STAGES.index(name) < pipeline.STAGES.index(stage) for name in upstream)


def test_cad_stage_writes_a_render(tmp_path) -> None:
    pytest.importorskip("cadquery")
    outcomes = run_design(_smoke(extra=["cad.export_rotor=false"]), ["cad"], store=RunStore(tmp_path))
    image = outcomes["cad"].run.output("render.png")
    assert image.is_file() and image.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
