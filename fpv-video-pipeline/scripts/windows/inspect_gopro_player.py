#!/usr/bin/env python3
"""Windows only: dump GoPro Player's UI-automation tree so its batch export can be driven by script.

    pip install pywinauto
    (open GoPro Player, load a clip, open the HyperSmooth Pro panel and the Export dialog)
    python scripts\\windows\\inspect_gopro_player.py

Writes gopro_player_ui.txt: every window, button, slider, and field the app exposes to Windows UI Automation,
with names and automation IDs. Send that file back: it shows whether the app's controls are scriptable
(stable names/IDs) and which clicks a full "open folder -> apply preset -> batch export" automation needs.
Nothing is clicked or changed; this only reads.
"""
import sys
from pathlib import Path

try:
    from pywinauto import Desktop
except ImportError:
    raise SystemExit("pip install pywinauto  (Windows only)")

wins = [w for w in Desktop(backend="uia").windows() if "gopro" in w.window_text().lower()]
if not wins:
    raise SystemExit("GoPro Player window not found - open it first")
out = Path(__file__).resolve().parent / "gopro_player_ui.txt"
with open(out, "w", encoding="utf-8") as f:
    for w in wins:
        f.write(f"=== {w.window_text()} ===\n")
        sys.stdout, saved = f, sys.stdout
        try:
            w.print_control_identifiers(depth=12)
        finally:
            sys.stdout = saved
print(f"Wrote {out}")
