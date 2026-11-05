"""Streamlit entry point for the legacy aero-solver GUI."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY_SCRIPTS = ROOT / "archive" / "legacy_scripts"
for candidate in (LEGACY_SCRIPTS, ROOT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from aero_solver_ui import main


def running_under_streamlit() -> bool:
    """Return true when this file is being executed by Streamlit."""

    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx() is not None
    except Exception:
        return False


if __name__ == "__main__":
    if running_under_streamlit():
        main()
    else:
        from prandtl_prop.external import gui as gui_launchers

        raise SystemExit(gui_launchers.run_streamlit_aero_ui())
