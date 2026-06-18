"""Command builders for interactive GUI launchers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from scripts.helpers import matlab_fea, paths

STREAMLIT_ENTRY = paths.GUI_SCRIPTS_DIR / "streamlit_aero_ui.py"
MATLAB_FEA_UI_ENTRY = paths.GUI_SCRIPTS_DIR / "start_matlab_fea_ui.m"


def command_text(command: list[str]) -> str:
    """Return a shell-readable command string."""

    return subprocess.list2cmdline([str(part) for part in command])


def streamlit_command(*, python_executable: str | Path | None = None, port: int | None = None) -> list[str]:
    """Build the command that starts the Streamlit aero UI."""

    command = [str(python_executable or sys.executable), "-m", "streamlit", "run", str(STREAMLIT_ENTRY)]
    if port is not None:
        command.extend(["--server.port", str(port)])
    return command


def run_streamlit_aero_ui(*, python_executable: str | Path | None = None, port: int | None = None) -> int:
    """Start the Streamlit aero UI and wait until the Streamlit process exits."""

    command = streamlit_command(python_executable=python_executable, port=port)
    env = os.environ.copy()
    legacy_path = str(paths.LEGACY_SCRIPTS_DIR)
    env["PYTHONPATH"] = legacy_path + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(command, cwd=paths.PROJECT_ROOT, env=env, check=False)
    return int(completed.returncode)


def matlab_string(value: str | Path | None) -> str:
    """Format a Python value as a MATLAB string literal."""

    if value is None:
        return "''"
    text = str(value).replace("\\", "/")
    return "'" + text.replace("'", "''") + "'"


def build_matlab_fea_ui_expression(
    *,
    results_csv: Path | None = None,
    viewer: str = "native",
    plot_plane: str = "3D isometric ribbon",
) -> str:
    """Build the MATLAB expression that starts the FEA viewer."""

    results_arg = results_csv if results_csv is not None else ""
    return (
        f"addpath({matlab_string(paths.GUI_SCRIPTS_DIR)}); "
        f"start_matlab_fea_ui({matlab_string(results_arg)}, {matlab_string(viewer)}, {matlab_string(plot_plane)});"
    )


def matlab_fea_ui_command(
    *,
    matlab_executable: str | Path | None = None,
    results_csv: Path | None = None,
    viewer: str = "native",
    plot_plane: str = "3D isometric ribbon",
) -> list[str]:
    """Build the desktop MATLAB command that starts the FEA viewer."""

    matlab = str(matlab_executable or matlab_fea.find_matlab() or "matlab")
    expression = build_matlab_fea_ui_expression(results_csv=results_csv, viewer=viewer, plot_plane=plot_plane)
    return [matlab, "-r", expression]


def start_matlab_fea_ui(
    *,
    matlab_executable: str | Path | None = None,
    results_csv: Path | None = None,
    viewer: str = "native",
    plot_plane: str = "3D isometric ribbon",
    wait: bool = False,
) -> int:
    """Start the MATLAB FEA UI."""

    command = matlab_fea_ui_command(
        matlab_executable=matlab_executable,
        results_csv=results_csv,
        viewer=viewer,
        plot_plane=plot_plane,
    )
    if wait:
        completed = subprocess.run(command, cwd=paths.PROJECT_ROOT, check=False)
        return int(completed.returncode)
    subprocess.Popen(command, cwd=paths.PROJECT_ROOT)
    return 0
