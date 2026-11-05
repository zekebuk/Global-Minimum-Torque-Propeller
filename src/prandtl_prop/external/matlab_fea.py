"""Headless MATLAB FEA execution and reporting for the archived beam/frame workflow."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from prandtl_prop import config


@dataclass(frozen=True)
class MatlabFeaResult:
    """Result from a MATLAB FEA command attempt. ``error`` explains why a requested run did not happen."""

    matlab_executable: str | None
    fea_directory: Path | None
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    report_path: Path
    error: str | None = None


def find_matlab() -> str | None:
    """Find the MATLAB executable on PATH."""

    return shutil.which("matlab")


def find_fea_directory() -> Path | None:
    """Find the archived MATLAB FEA workflow directory."""

    for candidate in (config.LEGACY_FEA_DIR, config.LEGACY_SCRIPTS_DIR / "fea" / "legacy_3d_beam"):
        if (candidate / "main_propeller_3d_beam_fea.m").exists():
            return candidate
    return None


def build_matlab_batch(fea_directory: Path) -> str:
    """Build the MATLAB batch expression for the archived FEA workflow."""

    matlab_path = fea_directory.as_posix().replace("'", "''")
    return f"cd('{matlab_path}'); main_propeller_3d_beam_fea;"


def run_matlab_fea(
    *,
    execute: bool = False,
    matlab_executable: str | None = None,
    fea_directory: Path | None = None,
    timeout_s: int = 1800,
    report_path: Path = config.OUTPUT_REPORTS_DIR / "matlab_fea_report.md",
) -> MatlabFeaResult:
    """Run or report the headless MATLAB FEA command."""

    matlab = matlab_executable or find_matlab()
    fea_dir = fea_directory or find_fea_directory()
    command = [matlab, "-batch", build_matlab_batch(fea_dir)] if matlab and fea_dir else []
    stdout = stderr = ""
    returncode: int | None = None
    error: str | None = None
    if execute:
        if not command:
            error = "MATLAB executable not found; pass --matlab." if not matlab else "Archived FEA directory not found; pass --fea-dir."
        else:
            try:
                completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout_s, check=False)
                stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
                stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
                error = f"MATLAB timed out after {timeout_s} s"
    lines = [
        "# MATLAB FEA Report",
        "",
        "The MATLAB FEA workflow is preserved as legacy structural screening context.",
        "It is optional external-software work and is not certification-grade validation.",
        "",
        f"MATLAB executable: `{matlab or 'not found'}`",
        f"FEA directory: `{config.relative(fea_dir) if fea_dir else 'not found'}`",
        f"Execution requested: `{execute}`",
        f"Return code: `{returncode if returncode is not None else 'not run'}`",
    ]
    if error:
        lines.append(f"Error: {error}")
    lines.extend(
        [
            "",
            "## Command",
            "",
            "```text",
            subprocess.list2cmdline(command) if command else "No runnable MATLAB command could be constructed.",
            "```",
        ]
    )
    if stdout.strip():
        lines.extend(["", "## Stdout", "```text", stdout.strip(), "```"])
    if stderr.strip():
        lines.extend(["", "## Stderr", "```text", stderr.strip(), "```"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return MatlabFeaResult(matlab, fea_dir, command, returncode, stdout, stderr, report_path, error)
