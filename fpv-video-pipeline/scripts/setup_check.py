#!/usr/bin/env python3
"""Setup-day check: point the pipeline at your SSD and confirm every piece works before the first real session.

    python scripts/setup_check.py --root D:\\FPV            # writes paths into config.toml, creates folders
    python scripts/setup_check.py                           # check only

Checks: Python version, numpy, ffmpeg/ffprobe (HEVC 10-bit decode), library folders writable, free space on the
SSD (and how many minutes of footage that holds), LUT files present, GoPro Player / Gyroflow / Topaz installed
(Windows), Resolve scripting reachable, and an offline smoke test of the parsers. Ends with a to-do list.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TODO: list[str] = []


def check(ok: bool, msg: str, todo: str = "", warn: bool = False) -> bool:
    print(f"[{'ok' if ok else ('warn' if warn else 'FAIL')}] {msg}")
    if not ok and todo:
        TODO.append(todo)
    return ok


def write_root(root: Path):
    cfg = ROOT / "config.toml"
    text = cfg.read_text()
    for key, sub in (("library", "Library"), ("stabilized", "Stabilized"), ("exports", "Exports"), ("music", "Music"), ("sfx", "SFX")):
        p = (root / sub).as_posix()
        text = re.sub(rf'^{key} = ".*"$', f'{key} = "{p}"', text, count=1, flags=re.MULTILINE)
    cfg.write_text(text)
    for sub in ("Library", "Stabilized", "Exports", "Music", "References", "SFX", "ResolveCache"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    print(f"config.toml paths -> {root}; folders created (ResolveCache is for Resolve's cache/proxies, see SETUP_DAY.md)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, help="SSD folder that will hold the whole FPV library")
    args = ap.parse_args()
    if args.root:
        write_root(args.root)

    from fpvpipe import config
    cfg = config.load()
    v = sys.version_info
    check((3, 10) <= (v.major, v.minor) <= (3, 12), f"Python {v.major}.{v.minor} ({sys.executable})",
          "use Python 3.11 for this venv (Resolve's scripting module is happiest on 3.10-3.12)", warn=True)
    try:
        import numpy  # noqa: F401
        check(True, "numpy")
    except ImportError:
        check(False, "numpy", "pip install -r requirements.txt")

    for tool in ("ffmpeg", "ffprobe"):
        check(shutil.which(tool) is not None, f"{tool} on PATH", "winget install Gyan.FFmpeg (then reopen the terminal)")
    if shutil.which("ffmpeg"):
        dec = subprocess.run(["ffmpeg", "-hide_banner", "-decoders"], capture_output=True, text=True).stdout
        check(" hevc " in dec, "ffmpeg can decode HEVC (GoPro / DJI)", "install a full ffmpeg build (Gyan.FFmpeg)")

    lib = cfg["paths"]["library"]
    try:
        lib.mkdir(parents=True, exist_ok=True)
        probe = lib / ".write_test"
        probe.write_text("x")
        probe.unlink()
        check(True, f"library writable: {lib}")
        free = shutil.disk_usage(lib).free / 1e9
        # GoPro 5.3K/60 HEVC ~120 Mbps ~ 0.9 GB/min; stabilized CineForm/ProRes ~6 GB/min.
        check(free > 200, f"{free:.0f} GB free on library drive (~{free / 0.9:.0f} min raw, or ~{free / 7:.0f} min raw + "
              "stabilized intermediates)", "free up space or point --root at a bigger SSD", warn=True)
    except OSError as e:
        check(False, f"library not writable: {lib} ({e})", "run with --root <SSD folder>")

    for prof, lut in cfg["color"]["luts"].items():
        check(bool(lut and lut.exists()), f"LUT for {prof}: {lut}", f"put the {prof} -> Rec.709 LUT at {lut}", warn=True)

    if sys.platform == "win32":
        apps = {"GoPro Player": ["GoPro"], "Gyroflow": ["Gyroflow"], "Topaz Video": ["Topaz"]}
        pf = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        try:
            store = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-AppxPackage | Select -Expand Name"],
                                   capture_output=True, text=True, timeout=30).stdout
        except Exception:
            store = ""
        for app, needles in apps.items():
            found = any(n.lower() in store.lower() for n in needles) or any(
                Path(p, d).exists() for p in pf if p for d in needles)
            check(found, f"{app} installed", f"install {app} (only needed if you use it)", warn=True)

    rc = subprocess.run([sys.executable, str(ROOT / "scripts" / "doctor.py")], capture_output=True, text=True)
    last = [l for l in rc.stdout.splitlines() if l.startswith("[")]
    check(rc.returncode == 0, "Resolve scripting reachable" + (f" ({last[-1]})" if last else ""),
          "open Resolve Studio, Preferences > System > General > External scripting = Local, restart; "
          "run scripts/doctor.py for details")

    t = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(ROOT / "tests")],
                       capture_output=True, text=True, cwd=ROOT)
    tail = (t.stdout.strip().splitlines() or ["pytest not installed"])[-1]
    check(t.returncode == 0, f"offline smoke tests: {tail}", "pip install pytest; send me the failing output")

    print("\nTo do:" if TODO else "\nAll set. First session: scripts/run_session.py <card> --session <name>")
    for i, t_ in enumerate(dict.fromkeys(TODO), 1):
        print(f"  {i}. {t_}")
    return 0 if not TODO else 1


if __name__ == "__main__":
    raise SystemExit(main())
