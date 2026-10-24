"""Content-addressed run folders for pipeline stages.

Every stage run lives in ``runs/<design>/<stage>/<key>/`` where ``key`` hashes exactly what the
stage depends on: its code version, the design sections it reads, the bytes of its input files, and
the keys of its upstream stages. Re-running an unchanged stage is a cache hit; changing anything
upstream changes every downstream key, so results can never silently mix design iterations.

A run is built in a temporary sibling folder and renamed into place only after ``manifest.json``
is written, so an interrupted run never looks complete.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prandtl_prop import config
from prandtl_prop.design import canonical_json

KEY_LENGTH = 16


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_key(
    *,
    stage: str,
    version: str,
    design_inputs: dict[str, Any],
    input_files: Iterable[Path] = (),
    upstream: dict[str, str] | None = None,
) -> str:
    """Hash everything a stage depends on."""

    payload = {
        "stage": stage,
        "version": version,
        "design_inputs": design_inputs,
        "input_files": {_display(path): file_digest(path) for path in sorted(set(map(Path, input_files)))},
        "upstream": dict(sorted((upstream or {}).items())),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:KEY_LENGTH]


@dataclass
class StageRun:
    """A stage run folder. Use ``path()`` for outputs, then ``finish()``."""

    design_name: str
    stage: str
    key: str
    directory: Path
    final_directory: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    cached: bool = False

    def path(self, name: str) -> Path:
        return self.directory / name

    def output(self, name: str) -> Path:
        """Path of a finished output (valid after ``finish`` or for cached runs)."""

        return self.final_directory / name

    @property
    def metrics(self) -> dict[str, Any]:
        return self.manifest.get("metrics", {})

    def write_json(self, name: str, payload: Any) -> Path:
        target = self.path(name)
        target.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        return target

    def finish(self, *, metrics: dict[str, Any], warnings: list[str] | None = None) -> StageRun:
        self.manifest.update(
            {
                "status": "complete",
                "finished_utc": _now(),
                "metrics": metrics,
                "warnings": list(warnings or []),
                "outputs": sorted(path.name for path in self.directory.iterdir()),
            }
        )
        self.write_json("manifest.json", self.manifest)
        if self.final_directory.exists():
            shutil.rmtree(self.final_directory)
        os.replace(self.directory, self.final_directory)
        self.directory = self.final_directory
        (self.final_directory.parent / "latest.txt").write_text(self.key + "\n", encoding="utf-8")
        return self


class RunStore:
    """Creates and finds stage runs under a root folder (default ``runs/``)."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else config.RUNS_DIR

    def stage_dir(self, design_name: str, stage: str) -> Path:
        return self.root / design_name / stage

    def find(self, design_name: str, stage: str, key: str) -> StageRun | None:
        directory = self.stage_dir(design_name, stage) / key
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "complete":
            return None
        return StageRun(design_name, stage, key, directory, directory, manifest, cached=True)

    def start(self, design_name: str, stage: str, key: str, *, inputs: dict[str, Any]) -> StageRun:
        final_directory = self.stage_dir(design_name, stage) / key
        work_directory = final_directory.with_name(f"{key}.tmp-{os.getpid()}")
        if work_directory.exists():
            shutil.rmtree(work_directory)
        work_directory.mkdir(parents=True)
        manifest = {
            "design": design_name,
            "stage": stage,
            "key": key,
            "status": "running",
            "started_utc": _now(),
            "git_commit": _git_commit(),
            "inputs": inputs,
        }
        return StageRun(design_name, stage, key, work_directory, final_directory, manifest)

    def latest(self, design_name: str, stage: str) -> StageRun | None:
        pointer = self.stage_dir(design_name, stage) / "latest.txt"
        if not pointer.is_file():
            return None
        return self.find(design_name, stage, pointer.read_text(encoding="utf-8").strip())


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(config.PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _git_commit() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=config.PROJECT_ROOT, capture_output=True, text=True, check=False, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None
