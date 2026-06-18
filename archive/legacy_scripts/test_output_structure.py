from __future__ import annotations

import json
from pathlib import Path

import cleanup_outputs
import output_paths
from common_plot_style import save_matplotlib_figure


class DummyFigure:
    def savefig(self, path: Path, **_: object) -> None:
        Path(path).write_text("figure\n", encoding="utf-8")


def test_output_path_helper_returns_canonical_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(output_paths, "PROJECT_ROOT", tmp_path)

    assert output_paths.get_results_dir(
        "geometry_optimizer",
        model_source="optimizer_reduced_order",
        case_name="optimized_gmt_22",
    ) == tmp_path / "results" / "geometry_optimizer" / "optimized_gmt"
    assert output_paths.get_results_dir(
        "aero_postprocess",
        model_source="optimizer_station_data",
        case_name="optimized_mil_22",
    ) == tmp_path / "results" / "aero_postprocess" / "optimizer_station_data" / "optimized_mil"
    assert output_paths.get_figures_dir(
        "aero_postprocess",
        model_source="bemt_xfoil",
        case_name="optimized_gmt_22",
    ) == tmp_path / "results" / "plots" / "aero_postprocess" / "bemt_xfoil" / "optimized_gmt"
    assert output_paths.get_results_dir(
        "aero_postprocess",
        comparison_name="optimized_mil_vs_gmt",
    ) == tmp_path / "results" / "aero_postprocess" / "comparisons" / "optimized_mil_vs_gmt"
    assert output_paths.get_results_dir(
        "verification",
        model_source="theory_verification",
    ) == tmp_path / "results" / "verification" / "theory"
    assert output_paths.get_figures_dir(
        "fea",
        model_source="fea_abaqus",
    ) == tmp_path / "results" / "plots" / "fea" / "abaqus"


def test_metadata_sidecar_is_written(tmp_path):
    output = tmp_path / "result.csv"
    output.write_text("x\n", encoding="utf-8")

    sidecar = output_paths.write_metadata_sidecar(
        output,
        {
            "generating_script": "unit_test.py",
            "model_source": "optimizer_station_data",
            "case_name": "unit",
            "geometry_source_csv": "geometry.csv",
            "station_source_csv": "",
            "input_config_path": "",
            "twist_column_used": "twist_deg",
            "aero_source": "optimizer_station_data",
        },
    )

    assert sidecar == tmp_path / "result.csv.metadata.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["generating_script"] == "unit_test.py"
    assert payload["model_source"] == "optimizer_station_data"
    assert payload["figure_export_policy"] == "png_svg_only"
    assert "output_created_utc" in payload


def test_save_matplotlib_figure_writes_png_svg_only(tmp_path):
    save_matplotlib_figure(DummyFigure(), tmp_path, "plot")

    assert (tmp_path / "plot.png").exists()
    assert (tmp_path / "plot.svg").exists()
    assert not (tmp_path / "plot.pdf").exists()


def test_cleanup_outputs_dry_run_deletes_nothing(tmp_path, monkeypatch, capsys):
    generated = tmp_path / "results" / "geometry_optimizer"
    generated.mkdir(parents=True)
    (generated / "file.txt").write_text("generated\n", encoding="utf-8")
    monkeypatch.setattr(cleanup_outputs, "GENERATED_OUTPUT_DIRS", (generated,))

    cleanup_outputs.cleanup_outputs(confirm=False)

    assert generated.exists()
    assert "Would delete:" in capsys.readouterr().out


def test_cleanup_outputs_confirm_deletes_only_generated_targets(tmp_path, monkeypatch):
    generated = tmp_path / "results" / "aero_postprocess"
    manual = tmp_path / "configs"
    generated.mkdir(parents=True)
    manual.mkdir(parents=True)
    (generated / "file.txt").write_text("generated\n", encoding="utf-8")
    (manual / "config.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(cleanup_outputs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cleanup_outputs, "GENERATED_OUTPUT_DIRS", (generated,))

    cleanup_outputs.cleanup_outputs(confirm=True)

    assert not generated.exists()
    assert manual.exists()
