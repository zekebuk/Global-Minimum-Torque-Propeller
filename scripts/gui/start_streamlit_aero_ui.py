"""Start the Streamlit aero-solver GUI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.helpers import gui_launchers


def parse_args() -> argparse.Namespace:
    """Parse Streamlit launcher options."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", dest="python_executable", default=None, help="Python executable used to run Streamlit.")
    parser.add_argument("--port", type=int, default=None, help="Optional Streamlit server port.")
    parser.add_argument("--print-command", action="store_true", help="Print the command without starting Streamlit.")
    return parser.parse_args()


def main() -> int:
    """Start Streamlit or print the launch command."""

    args = parse_args()
    command = gui_launchers.streamlit_command(python_executable=args.python_executable, port=args.port)
    if args.print_command:
        print(gui_launchers.command_text(command))
        return 0
    print("Starting Streamlit aero UI. Use Ctrl+C in this terminal to stop it.")
    return gui_launchers.run_streamlit_aero_ui(python_executable=args.python_executable, port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())
