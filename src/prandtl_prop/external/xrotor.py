"""Headless XROTOR execution, artifact discovery, and reporting."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from prandtl_prop import config


@dataclass(frozen=True)
class XrotorRunResult:
    """Result from a headless XROTOR attempt. ``error`` explains why a requested run did not happen."""

    executable: Path | None
    command_file: Path | None
    returncode: int | None
    stdout: str
    stderr: str
    report_path: Path
    error: str | None = None


def default_xrotor_executable() -> Path | None:
    """Return the first likely XROTOR executable path."""

    candidates = [
        config.PROJECT_ROOT / "Xrotor7.55" / "Xrotor" / "xrotor.exe",
        config.PROJECT_ROOT.parent / "Xrotor7.55" / "Xrotor" / "xrotor.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    discovered = shutil.which("xrotor")
    return Path(discovered) if discovered else None


def discover_xrotor_artifacts(root: Path = config.OUTPUTS_DIR) -> list[Path]:
    """Return XROTOR-related artifacts under the outputs tree, excluding this workflow's own reports."""

    if not root.exists():
        return []
    reports = config.OUTPUT_REPORTS_DIR.resolve()
    return sorted(path for path in root.rglob("*xrotor*") if path.is_file() and reports not in path.resolve().parents)


def copy_xrotor_figure(output_dir: Path = config.OUTPUT_FIGURES_DIR) -> list[Path]:
    """Copy the final XROTOR/BEMT comparison figure from the external results tree when present."""

    output_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for suffix in ("png", "pdf"):
        source = config.FINAL_AERO_DIR / f"xrotor_vs_bemt_model_difference.{suffix}"
        if source.exists():
            destination = output_dir / f"fig_xrotor_bemt_comparison.{suffix}"
            shutil.copy2(source, destination)
            copied.append(destination)
    return copied


def run_headless_xrotor(
    *,
    executable: Path | None = None,
    command_file: Path | None = None,
    timeout_s: int = 120,
    execute: bool = False,
    report_path: Path = config.OUTPUT_REPORTS_DIR / "xrotor_headless_report.md",
) -> XrotorRunResult:
    """Run XROTOR without a GUI, feeding ``command_file`` to stdin from the command file's folder."""

    exe = executable or default_xrotor_executable()
    stdout = stderr = ""
    returncode: int | None = None
    error: str | None = None
    if execute:
        if exe is None:
            error = "XROTOR executable not found; pass --xrotor-exe."
        elif command_file is None:
            error = "No command file given; pass --command-file."
        elif not command_file.is_file():
            error = f"Command file does not exist: {command_file}"
        else:
            try:
                completed = subprocess.run(
                    [str(exe)],
                    input=command_file.read_text(encoding="utf-8"),
                    text=True,
                    capture_output=True,
                    timeout=timeout_s,
                    check=False,
                    cwd=command_file.parent,
                )
                stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
            except subprocess.TimeoutExpired as exc:
                stdout, stderr = _text(exc.stdout), _text(exc.stderr)
                error = f"XROTOR timed out after {timeout_s} s"
    write_xrotor_report(
        report_path=report_path,
        executable=exe,
        command_file=command_file,
        execute=execute,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        error=error,
    )
    return XrotorRunResult(exe, command_file, returncode, stdout, stderr, report_path, error)


def write_xrotor_report(
    *,
    report_path: Path = config.OUTPUT_REPORTS_DIR / "xrotor_headless_report.md",
    executable: Path | None = None,
    command_file: Path | None = None,
    execute: bool = False,
    returncode: int | None = None,
    stdout: str = "",
    stderr: str = "",
    error: str | None = None,
) -> Path:
    """Write a concise XROTOR headless status report."""

    artifacts = discover_xrotor_artifacts()
    lines = [
        "# Headless XROTOR Report",
        "",
        "XROTOR is an optional external numerical comparison tool.",
        "This report is not experimental validation and does not provide acoustic SPL validation.",
        "",
        f"Executable: `{config.relative(executable) if executable else 'not found'}`",
        f"Command file: `{config.relative(command_file) if command_file else 'not provided'}`",
        f"Execution requested: `{execute}`",
        f"Return code: `{returncode if returncode is not None else 'not run'}`",
    ]
    if error:
        lines.append(f"Error: {error}")
    lines.extend(["", f"Discovered XROTOR-related artifacts: {len(artifacts)}"])
    if artifacts:
        lines.extend(["", "## Artifacts"])
        lines.extend(f"- `{config.relative(path)}`" for path in artifacts)
    if stdout.strip():
        lines.extend(["", "## Stdout", "```text", stdout.strip(), "```"])
    if stderr.strip():
        lines.extend(["", "## Stderr", "```text", stderr.strip(), "```"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
