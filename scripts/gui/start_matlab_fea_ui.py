"""Start the interactive MATLAB FEA viewer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from prandtl_prop.external import gui as gui_launchers


def parse_args() -> argparse.Namespace:
    """Parse MATLAB FEA UI launcher options."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matlab", default=None, help="Path or command name for the MATLAB executable.")
    parser.add_argument("--results-csv", type=Path, default=None, help="Optional station-level FEA result CSV to open.")
    parser.add_argument("--viewer", choices=("native", "me504"), default="native", help="MATLAB viewer to open.")
    parser.add_argument("--plot-plane", default="3D isometric ribbon", help="Initial plot plane/view passed to the viewer.")
    parser.add_argument("--wait", action="store_true", help="Wait for MATLAB to exit instead of returning after launch.")
    parser.add_argument("--print-command", action="store_true", help="Print the command without starting MATLAB.")
    return parser.parse_args()


def main() -> int:
    """Start MATLAB or print the launch command."""

    args = parse_args()
    command = gui_launchers.matlab_fea_ui_command(
        matlab_executable=args.matlab,
        results_csv=args.results_csv,
        viewer=args.viewer,
        plot_plane=args.plot_plane,
    )
    if args.print_command:
        print(gui_launchers.command_text(command))
        return 0
    print("Starting MATLAB FEA UI.")
    return gui_launchers.start_matlab_fea_ui(
        matlab_executable=args.matlab,
        results_csv=args.results_csv,
        viewer=args.viewer,
        plot_plane=args.plot_plane,
        wait=args.wait,
    )


if __name__ == "__main__":
    raise SystemExit(main())
