#!/usr/bin/env python3
"""Why won't Resolve (or the Resolve MCP server) connect? Run this on the editing machine.

    python scripts/doctor.py

Checks, in the order failures usually happen:
  1. Python is 64-bit and in the version range Resolve's scripting module accepts
  2. the scripting module and fusionscript library exist where expected
  3. the module imports
  4. Resolve answers (needs Studio + External scripting = Local + Resolve open)
  5. edition/version, and whether a project is open
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import gyroflow, resolve as rlib  # noqa: E402


def check(ok: bool, msg: str, fix: str = "", level: str = "FAIL") -> bool:
    print(f"[{'ok' if ok else level}] {msg}")
    if not ok and fix:
        print(f"       fix: {fix}")
    return ok


def main() -> int:
    bits = struct.calcsize("P") * 8
    v = sys.version_info
    check(bits == 64, f"Python is {bits}-bit ({sys.executable})", "install 64-bit Python")
    check((3, 10) <= (v.major, v.minor) <= (3, 12), f"Python {v.major}.{v.minor}",
          "3.10-3.12 is the safe range; older Resolve builds import 'imp' (removed in 3.12) or fail on 3.13+. "
          "If 3.13+ fails, make a 3.11 venv for these scripts and the MCP server.", level="warn")

    api, lib = rlib._DEFAULTS.get(sys.platform, rlib._DEFAULTS["linux"])
    api = os.environ.get("RESOLVE_SCRIPT_API", api)
    lib = os.environ.get("RESOLVE_SCRIPT_LIB", lib)
    if not check(Path(api, "Modules", "DaVinciResolveScript.py").exists(), f"scripting module in {api}",
                 "reinstall Resolve, or set RESOLVE_SCRIPT_API to its Developer/Scripting folder"):
        return 1
    if not check(Path(lib).exists(), f"fusionscript library at {lib}", "set RESOLVE_SCRIPT_LIB"):
        return 1
    check(gyroflow.plugin_installed(), "Gyroflow OpenFX plugin installed (needed for [gyroflow] mode = \"ofx\")",
          "download Gyroflow-OpenFX from github.com/gyroflow/gyroflow-plugins/releases, copy Gyroflow.ofx.bundle to "
          f"{gyroflow.BUNDLE.get(sys.platform, gyroflow.BUNDLE['linux']).parent}, restart Resolve", level="warn")

    try:
        resolve = rlib.connect()
    except SystemExit as e:
        check(False, "Resolve answered", str(e))
        return 1
    check(True, "Resolve answered")
    product = resolve.GetProductName() if hasattr(resolve, "GetProductName") else "?"
    version = resolve.GetVersionString() if hasattr(resolve, "GetVersionString") else "?"
    check("Studio" in str(product), f"{product} {version}",
          "external scripting needs Studio; if you have Studio, make sure the Studio app (not free) is the one open")
    proj = resolve.GetProjectManager().GetCurrentProject()
    check(proj is not None, f"open project: {proj.GetName() if proj else None}")
    print("\nIf this passes but the MCP client still shows 'disconnected', the client is launching a different "
          "Python than the one you just ran. Put this exact interpreter path in the MCP config:\n  " + sys.executable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
