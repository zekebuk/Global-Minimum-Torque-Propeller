"""Run or report the optional headless XROTOR comparison workflow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.helpers import paths, xrotor_headless


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the optional XROTOR workflow."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Actually run XROTOR. Without this flag, only a report is written.")
    parser.add_argument("--xrotor-exe", type=Path, default=None, help="Path to xrotor executable.")
    parser.add_argument("--command-file", type=Path, default=None, help="Text command file to feed to XROTOR stdin.")
    parser.add_argument("--timeout-s", type=int, default=120, help="Execution timeout in seconds.")
    return parser.parse_args()


def main() -> int:
    """Write the XROTOR status report, optionally executing XROTOR."""

    args = parse_args()
    result = xrotor_headless.run_headless_xrotor(
        executable=args.xrotor_exe,
        command_file=args.command_file,
        timeout_s=args.timeout_s,
        execute=args.execute,
    )
    print("Headless XROTOR workflow complete")
    print(f"  executable: {paths.relative(result.executable) if result.executable else 'not found'}")
    print(f"  command file: {paths.relative(result.command_file) if result.command_file else 'not provided'}")
    print(f"  return code: {result.returncode if result.returncode is not None else 'not run'}")
    print(f"  {paths.relative(result.report_path)}")
    return 0 if result.returncode in (None, 0) else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
