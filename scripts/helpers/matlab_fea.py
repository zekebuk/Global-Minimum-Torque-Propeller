"""Headless MATLAB FEA execution and reporting helpers."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from scripts.helpers import csv_io, paths


@dataclass(frozen=True)
class MatlabFeaResult:
    """Result from a MATLAB FEA command attempt."""

    matlab_executable: str | None
    fea_directory: Path | None
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    report_path: Path


def find_matlab() -> str | None:
    """Find the MATLAB executable on PATH."""

    return shutil.which("matlab")


def find_fea_directory() -> Path | None:
    """Find the archived MATLAB FEA workflow directory."""

    candidates = [
        paths.LEGACY_FEA_DIR,
        paths.LEGACY_SCRIPTS_DIR / "fea" / "legacy_3d_beam",
    ]
    for candidate in candidates:
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
    report_path: Path = paths.OUTPUT_REPORTS_DIR / "matlab_fea_report.md",
) -> MatlabFeaResult:
    """Run or report the headless MATLAB FEA command."""

    matlab = matlab_executable or find_matlab()
    fea_dir = fea_directory or find_fea_directory()
    command: list[str] = []
    stdout = ""
    stderr = ""
    returncode: int | None = None
    if matlab and fea_dir:
        command = [matlab, "-batch", build_matlab_batch(fea_dir)]
    if execute and command:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout_s, check=False)
        stdout = completed.stdout
        stderr = completed.stderr
        returncode = completed.returncode
    write_matlab_fea_report(
        report_path=report_path,
        matlab_executable=matlab,
        fea_directory=fea_dir,
        command=command,
        execute=execute,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
    return MatlabFeaResult(matlab, fea_dir, command, returncode, stdout, stderr, report_path)


def write_matlab_fea_report(
    *,
    report_path: Path,
    matlab_executable: str | None,
    fea_directory: Path | None,
    command: list[str],
    execute: bool,
    returncode: int | None,
    stdout: str,
    stderr: str,
) -> Path:
    """Write a concise MATLAB FEA status report."""

    lines = [
        "# MATLAB FEA Report",
        "",
        "The MATLAB FEA workflow is preserved as legacy structural screening context.",
        "It is optional external-software work and is not certification-grade validation.",
        "",
        f"MATLAB executable: `{matlab_executable or 'not found'}`",
        f"FEA directory: `{paths.relative(fea_directory) if fea_directory else 'not found'}`",
        f"Execution requested: `{execute}`",
        f"Return code: `{returncode if returncode is not None else 'not run'}`",
        "",
        "## Command",
        "",
        "```text",
        subprocess.list2cmdline(command) if command else "No runnable MATLAB command could be constructed.",
        "```",
    ]
    if stdout.strip():
        lines.extend(["", "## Stdout", "```text", stdout.strip(), "```"])
    if stderr.strip():
        lines.extend(["", "## Stderr", "```text", stderr.strip(), "```"])
    return csv_io.write_text(report_path, "\n".join(lines) + "\n")
