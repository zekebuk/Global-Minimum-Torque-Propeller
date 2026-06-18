"""Headless XROTOR execution and reporting helpers."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from scripts.helpers import csv_io, paths


@dataclass(frozen=True)
class XrotorRunResult:
    """Result from a headless XROTOR attempt."""

    executable: Path | None
    command_file: Path | None
    returncode: int | None
    stdout: str
    stderr: str
    report_path: Path


def default_xrotor_executable() -> Path | None:
    """Return the first likely XROTOR executable path."""

    candidates = [
        paths.PROJECT_ROOT / "Xrotor7.55" / "Xrotor" / "xrotor.exe",
        paths.PROJECT_ROOT.parent / "Xrotor7.55" / "Xrotor" / "xrotor.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    discovered = shutil.which("xrotor")
    return Path(discovered) if discovered else None


def discover_xrotor_artifacts(root: Path = paths.OUTPUTS_DIR) -> list[Path]:
    """Return XROTOR-related artifacts under the repo outputs tree."""

    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*xrotor*") if path.is_file())


def run_headless_xrotor(
    *,
    executable: Path | None = None,
    command_file: Path | None = None,
    timeout_s: int = 120,
    execute: bool = False,
    report_path: Path = paths.OUTPUT_REPORTS_DIR / "xrotor_headless_report.md",
) -> XrotorRunResult:
    """Run XROTOR without a GUI when an executable and command file are available."""

    exe = executable or default_xrotor_executable()
    stdout = ""
    stderr = ""
    returncode: int | None = None

    if execute and exe and command_file and command_file.exists():
        command_text = command_file.read_text(encoding="utf-8")
        completed = subprocess.run(
            [str(exe)],
            input=command_text,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        returncode = completed.returncode

    write_xrotor_report(
        report_path=report_path,
        executable=exe,
        command_file=command_file,
        execute=execute,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
    return XrotorRunResult(exe, command_file, returncode, stdout, stderr, report_path)


def write_xrotor_report(
    *,
    report_path: Path = paths.OUTPUT_REPORTS_DIR / "xrotor_headless_report.md",
    executable: Path | None = None,
    command_file: Path | None = None,
    execute: bool = False,
    returncode: int | None = None,
    stdout: str = "",
    stderr: str = "",
) -> Path:
    """Write a concise XROTOR headless status report."""

    artifacts = discover_xrotor_artifacts()
    lines = [
        "# Headless XROTOR Report",
        "",
        "XROTOR is an optional external numerical comparison tool.",
        "This report is not experimental validation and does not provide acoustic SPL validation.",
        "",
        f"Executable: `{paths.relative(executable) if executable else 'not found'}`",
        f"Command file: `{paths.relative(command_file) if command_file else 'not provided'}`",
        f"Execution requested: `{execute}`",
        f"Return code: `{returncode if returncode is not None else 'not run'}`",
        "",
        f"Discovered XROTOR-related artifacts: {len(artifacts)}",
    ]
    if artifacts:
        lines.append("")
        lines.append("## Artifacts")
        lines.extend(f"- `{paths.relative(path)}`" for path in artifacts)
    if stdout.strip():
        lines.extend(["", "## Stdout", "```text", stdout.strip(), "```"])
    if stderr.strip():
        lines.extend(["", "## Stderr", "```text", stderr.strip(), "```"])
    return csv_io.write_text(report_path, "\n".join(lines) + "\n")

