"""Connect to a running DaVinci Resolve Studio through Blackmagic's scripting API.

This is the same API the MCP server uses, so if this connects, MCP will too (and vice-versa).
Requirements: Resolve Studio running, Preferences > General > External scripting using = Local,
and a 64-bit Python the Resolve build accepts (3.10-3.12 is the safe range; see docs/MCP_SETUP.md).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_DEFAULTS = {
    "darwin": (
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
        "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
    ),
    "win32": (
        os.path.expandvars(r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"),
        r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll",
    ),
    "linux": ("/opt/resolve/Developer/Scripting", "/opt/resolve/libs/Fusion/fusionscript.so"),
}


def connect():
    api, lib = _DEFAULTS.get(sys.platform, _DEFAULTS["linux"])
    api = os.environ.setdefault("RESOLVE_SCRIPT_API", api)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", lib)
    modules = str(Path(api) / "Modules")
    if modules not in sys.path:
        sys.path.append(modules)
    try:
        import DaVinciResolveScript as dvr  # type: ignore
    except ImportError as e:
        raise SystemExit(
            f"Could not import DaVinciResolveScript from {modules}.\n"
            "Is Resolve installed in the default location? Set RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB if not."
        ) from e
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise SystemExit(
            "Resolve did not answer. Check: (1) Resolve Studio is open, (2) Preferences > General > "
            "'External scripting using' = Local, then restart Resolve, (3) this Python is 64-bit and a version "
            "your Resolve build supports. Run scripts/doctor.py for a full check."
        )
    return resolve


def seconds_to_frames(seconds: float, fps: float) -> int:
    return int(round(seconds * fps))
