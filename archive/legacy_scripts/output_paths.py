from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CODE_DIR.parent if CODE_DIR.name.lower() == "code" else CODE_DIR
FIGURE_EXPORT_POLICY = "png_svg_only"
PLOTS_DIR_NAME = "plots"

SUPPORTED_STAGES = {"geometry_optimizer", "aero_postprocess", "verification", "fea"}
SUPPORTED_MODEL_SOURCES = {
    "optimizer_reduced_order",
    "optimizer_station_data",
    "bemt_xfoil",
    "xrotor_optional",
    "theory_verification",
    "fea_beam",
    "fea_abaqus",
}


def project_path(relative_path: str | Path) -> Path:
    return PROJECT_ROOT / Path(relative_path)


def resolve_project_output_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


def get_plots_root() -> Path:
    path = project_path("results") / PLOTS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def git_metadata() -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "git_commit_hash": "",
        "dirty_git_tree": None,
    }
    try:
        metadata["git_commit_hash"] = subprocess.check_output(
            ["git", "-C", str(CODE_DIR), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        status = subprocess.check_output(
            ["git", "-C", str(CODE_DIR), "status", "--porcelain"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        metadata["dirty_git_tree"] = bool(status.strip())
    except (OSError, subprocess.CalledProcessError):
        pass
    return metadata


def write_metadata_sidecar(output_path: str | Path, metadata: Mapping[str, Any]) -> Path:
    path = Path(output_path)
    sidecar = path.with_name(path.name + ".metadata.json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "output_path": str(path),
        "output_created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "figure_export_policy": FIGURE_EXPORT_POLICY,
        **git_metadata(),
        **dict(metadata),
    }
    sidecar.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return sidecar


def get_results_dir(
    stage: str,
    model_source: str | None = None,
    case_name: str | None = None,
    comparison_name: str | None = None,
) -> Path:
    return _get_output_dir(
        root=project_path("results"),
        stage=stage,
        model_source=model_source,
        case_name=case_name,
        comparison_name=comparison_name,
        figures=False,
    )


def get_figures_dir(
    stage: str,
    model_source: str | None = None,
    case_name: str | None = None,
    comparison_name: str | None = None,
) -> Path:
    return _get_output_dir(
        root=get_plots_root(),
        stage=stage,
        model_source=model_source,
        case_name=case_name,
        comparison_name=comparison_name,
        figures=True,
    )


def canonical_case_dir_name(case_name: str | None) -> str | None:
    if not case_name:
        return None
    clean = _clean_path_part(case_name)
    for base in ("optimized_mil", "optimized_gmt", "reference_mil", "reference_gmt"):
        if clean == base or clean.startswith(base + "_"):
            return base
    return clean


def safe_filename_stem(value: str | Path) -> str:
    return _clean_path_part(value) or "plot"


def _get_output_dir(
    *,
    root: Path,
    stage: str,
    model_source: str | None,
    case_name: str | None,
    comparison_name: str | None,
    figures: bool,
) -> Path:
    if stage not in SUPPORTED_STAGES:
        raise ValueError(f"unsupported output stage: {stage}")
    if model_source is not None and model_source not in SUPPORTED_MODEL_SOURCES:
        raise ValueError(f"unsupported model source: {model_source}")

    if stage == "geometry_optimizer":
        path = root / "geometry_optimizer"
        if case_name:
            path /= canonical_case_dir_name(case_name) or _clean_path_part(case_name)
    elif stage == "aero_postprocess":
        path = root / "aero_postprocess"
        if comparison_name:
            path = path / "comparisons" / _clean_path_part(comparison_name)
        elif model_source in {"optimizer_station_data", "bemt_xfoil"}:
            path = path / model_source
            if case_name:
                path /= canonical_case_dir_name(case_name) or _clean_path_part(case_name)
        elif model_source == "xrotor_optional":
            path /= "xrotor"
        else:
            raise ValueError("aero_postprocess requires a model_source or comparison_name")
    elif stage == "verification":
        path = root / "verification" / "theory"
    else:
        path = root / "fea"
        if comparison_name:
            path = path / "comparisons"
            if comparison_name:
                path /= _clean_path_part(comparison_name)
        elif model_source == "fea_abaqus":
            path /= "abaqus" if figures else "abaqus_exports"
        elif model_source == "fea_beam":
            path /= "beam_solver"
            if case_name:
                path /= canonical_case_dir_name(case_name) or _clean_path_part(case_name)
        else:
            path /= "beam_solver"
            if case_name:
                path /= canonical_case_dir_name(case_name) or _clean_path_part(case_name)

    path.mkdir(parents=True, exist_ok=True)
    return path


def _clean_path_part(value: str | Path) -> str:
    text = str(value).strip().replace("\\", "/").split("/")[-1]
    return "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in text)


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _assert_output_path_in_roots(path: str | Path, roots: tuple[Path, ...], owner: str) -> Path:
    candidate = Path(path).expanduser()
    resolved_path = _resolved(candidate if candidate.is_absolute() else PROJECT_ROOT / candidate)
    resolved_project = _resolved(PROJECT_ROOT)
    if not _is_relative_to(resolved_path, resolved_project):
        return Path(path)

    resolved_roots = tuple(_resolved(root) for root in roots)
    if any(_is_relative_to(resolved_path, root) for root in resolved_roots):
        return Path(path)

    allowed = ", ".join(str(root) for root in roots)
    raise ValueError(f"{owner} output path must stay under one of: {allowed}; got {resolved_path}")


def assert_geometry_output_path(path: str | Path) -> Path:
    return _assert_output_path_in_roots(
        path,
        (
            project_path("results/geometry_optimizer"),
            project_path("results/plots/geometry_optimizer"),
        ),
        "geometry",
    )


def assert_aero_output_path(path: str | Path) -> Path:
    return _assert_output_path_in_roots(
        path,
        (
            project_path("results/aero_postprocess"),
            project_path("results/plots/aero_postprocess"),
        ),
        "aero",
    )


def assert_verification_output_path(path: str | Path) -> Path:
    return _assert_output_path_in_roots(
        path,
        (
            project_path("results/verification"),
            project_path("results/plots/verification"),
        ),
        "verification",
    )


def assert_fea_output_path(path: str | Path) -> Path:
    return _assert_output_path_in_roots(
        path,
        (
            project_path("results/fea"),
            project_path("results/plots/fea"),
        ),
        "FEA",
    )
