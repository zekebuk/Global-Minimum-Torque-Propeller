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


AUDIO_EXT = {".mp3", ".wav", ".aif", ".aiff", ".flac", ".m4a"}


def import_sfx_library(media_pool, sfx_bin, sfx_root: Path) -> dict[str, int]:
    """Mirror <SFX>/<category>/*.mp3 into the 03_SFX bin as one sub-bin per category (whoosh, impact, riser, bed ...).
    Idempotent: a file already in its sub-bin (same name) is not imported again, so re-running a build adds only new
    sounds. Returns {category: files imported}."""
    imported: dict[str, int] = {}
    sfx_root = Path(sfx_root)
    if not sfx_root.is_dir():
        return imported
    for cat in sorted(p for p in sfx_root.iterdir() if p.is_dir()):
        files = sorted(f for f in cat.iterdir() if f.suffix.lower() in AUDIO_EXT and not f.name.startswith("."))
        if not files:
            continue
        sub = next((f for f in (sfx_bin.GetSubFolderList() or []) if f.GetName() == cat.name), None)
        sub = sub or media_pool.AddSubFolder(sfx_bin, cat.name)
        have = {c.GetName() for c in (sub.GetClipList() or [])}
        new = [str(f) for f in files if f.name not in have]
        if new:
            media_pool.SetCurrentFolder(sub)
            imported[cat.name] = len(media_pool.ImportMedia(new) or [])
    return imported
